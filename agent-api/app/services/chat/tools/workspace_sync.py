# -*- coding: utf-8 -*-
"""会话文件同步层——当前会话/明确选中件 ↔ 沙箱 `/workspace/files`。

## 为什么是「同步」而不是挂载

上一轮方案说「`data/user_files/<user_id>` 已经是 bind 卷，直接挂进容器」——那只对本地 docker
sibling 容器成立。默认 provider 是 **opensandbox（K8s 远程）**，与 agent-api 不共享文件系统，
挂不了。而所有东西进沙箱本来就都是拷字节（`FileWriteEntry` → `write_files`），技能包也是这么
进去的。所以这里沿用同一条通道：**local / opensandbox / e2b / sealos 四个 provider 都不用改
适配器**（`write_files`/`read_files` 是基类抽象方法，四个都真实实现过）。

## 与旧 `inputs/`+`outputs/` 的差别

旧模型：模型先 `list_files` 拿 `file_id` → 传进 `execute_in_sandbox` 的 `file_ids` → 文件出现在
`inputs/`（只读）→ 产物必须写到 `outputs/` 才算交付。三步、两个目录、一套复制约定，
而且**模型不传 file_ids 就什么都没有**。

新模型：运行时只把当前会话文件、明确选中件和修订目标镜像进 `files/`，
**写进去就是已保存**。模型只见路径，不获得整个「我的文件」的环境可见性。

## 上限与不静默截断

同步是真的搬字节（远程 provider 就是网络传输），必须有上限。超限时**不静默截断**——静默会被
模型读成「文件区就这些」，比明说更糟；这里返回未同步清单，由调用方拼进工具回执告诉模型。
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from typing import Iterable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def scoped_user_file_rows(
    rows: Iterable[dict],
    *,
    thread_id: str = "",
    selected_file_ids: Optional[Iterable[str]] = None,
    revision_id: str = "",
    scope_to_thread: bool = False,
    workspace_folder_id: str = "",
) -> list[dict]:
    """Return only files explicitly in the current conversation scope.

    The full ``My Files`` collection is a picker/library, not ambient model context.
    Runtime callers opt into scoping; direct legacy/unit callers keep the old view
    unless ``scope_to_thread`` is explicitly enabled.
    """
    items = [row for row in rows if isinstance(row, dict)]
    if not scope_to_thread:
        return items
    allowed_ids = {
        str(file_id or "").strip()
        for file_id in (selected_file_ids or [])
        if str(file_id or "").strip()
    }
    if str(revision_id or "").strip():
        allowed_ids.add(str(revision_id).strip())
    current_thread = str(thread_id or "").strip()
    folder_id = str(workspace_folder_id or "").strip()
    from app.services.files import deliverable
    visible = [
        row for row in items
        if str(row.get("id") or "").strip() in allowed_ids
        or (folder_id and str(row.get("folderId") or row.get("folder_id") or "") == folder_id)
        or (
            current_thread
            and str(row.get("threadId") or row.get("thread_id") or "").strip()
            == current_thread
            and (not folder_id or (
                not (row.get("folderId") or row.get("folder_id"))
                and (
                    row.get("source") in {"material", "workspace", "uploaded"}
                    or not deliverable.is_deliverable(str(row.get("filename") or ""), str(row.get("source") or "generated"))
                )
            ))
        )
    ]
    # Explicit selection wins over an ambient folder file with the same name.
    # Both path tools and the sandbox use this order before name deduplication.
    return sorted(visible, key=lambda row: (
        0 if revision_id and str(row.get("id") or "") == revision_id else
        1 if str(row.get("id") or "") in allowed_ids else
        2 if folder_id and row.get("folderId") == folder_id else 3
    ))


class WorkspaceSync:
    """一次工具调用的文件区同步：`prepare()` 收集要写进去的字节，`persist()` 把改动落库。"""

    def __init__(self, user_id: str, *, thread_id: str = "", run_id: str = "",
                 revision_target: Optional[dict] = None,
                 selected_file_ids: Optional[Iterable[str]] = None,
                 scope_to_thread: bool = False,
                 workspace_folder_id: str = ""):
        self.user_id = str(user_id or "")
        self.thread_id = str(thread_id or "")
        self.run_id = str(run_id or "")
        # 原位修改授权（Harness）：非空时本次只准回写这一个 file_id。
        # 这是 bash 唯一能被约束的地方 —— 工具集合那层收不掉它（PPT 修改就是靠 bash 跑技能
        # 脚本，摘掉等于把主用例打死），命令文本也拦不住（`cp`/`python3 -c open(...)` 有无数
        # 写法）。而沙箱里的字节要变成用户的文件**只有 persist() 这一条路**，闸设在这里就是
        # 物理边界，与 paths.py `_require_target()` 等价。
        self.revision_id = str((revision_target or {}).get("file_id") or "").strip()
        self.revision_name = str((revision_target or {}).get("filename") or "").strip()
        self.selected_file_ids = {
            str(file_id or "").strip()
            for file_id in (selected_file_ids or [])
            if str(file_id or "").strip()
        }
        self.scope_to_thread = bool(scope_to_thread)
        self.workspace_folder_id = str(workspace_folder_id or "")
        self.revision_blocked: list = []  # 越权写入被挡下的路径——必须报给模型，见 persist_notice()
        # 相对路径 → file_id，供 persist 时判断「覆盖已有」还是「新建」
        self._path_to_id: dict = {}
        self._file_folder_guards: dict[str, str] = {}
        # 相对路径 → 装载时刻的 sha256（乐观并发原料，2026-07-29 深扫补）。
        # load() 本来就算了这些摘要，但此前只当返回值交给内核记账、类里不留——于是
        # persist() 回写时不传 expected_sha256，而 user_file_service.overwrite_file
        # 的 CAS 闸门（409「目标文件在任务执行期间已被更新」）现成可用却接不上：
        # bash 执行期间用户在前端改了同一个文件，沙箱产物会静默盖掉它，用户无感知。
        self._path_to_stamp: dict = {}
        self.skipped: list = []      # 因上限未同步的文件名
        self.synced_count = 0
        self.reused_count = 0        # 命中容器镜像记账、本次未重传的文件数（性能观测用）
        self.too_many = 0            # 本次变更文件数超过 MAX_PERSIST 时记下真实数量
        self.empty_skipped: list = []  # 0 字节产物：不落库，如实告诉模型
        self.persist_failed: list = []  # 落库抛错的文件名——**必须报给模型**，见 persist()
        # 乐观并发冲突（CAS 409）的文件名，与 persist_failed 分开记：原因不同、处方也不同
        # （前者"我这边存不下"，后者"用户改过了，请重读合并"），混在一起模型会编造原因
        self.conflict_files: list = []
        self.shadowed: list = []     # 同名被遮挡、没能进沙箱的文件名
        self.intermediate_skipped: list = []  # 识别为中间产物、未落库（生成器脚本等）
        self.html_asset_failed: dict[str, list[str]] = {}  # HTML 本地图片未解析，禁止发布破图
        self.batch_pack: dict = {}  # 超限交付物打成 zip 后的清单

    # ---------- 进 ----------

    def export_write_state(self) -> dict:
        """Keep the pre-execution identity and CAS baseline across background polls."""
        return {
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "workspace_folder_id": self.workspace_folder_id,
            "revision_id": self.revision_id,
            "path_to_id": dict(self._path_to_id),
            "path_to_stamp": dict(self._path_to_stamp),
            "file_folder_guards": dict(self._file_folder_guards),
        }

    def restore_write_state(self, state: dict) -> None:
        for field in ("user_id", "thread_id", "workspace_folder_id", "revision_id"):
            if str(state.get(field) or "") != str(getattr(self, field) or ""):
                raise ValueError("后台作业的文件范围已改变，不能按新的范围回写")
        self._path_to_id = dict(state.get("path_to_id") or {})
        self._path_to_stamp = dict(state.get("path_to_stamp") or {})
        self._file_folder_guards = dict(state.get("file_folder_guards") or {})

    async def load(self, mirror: Optional[dict] = None) -> dict:
        """增量镜像加载器（`sandbox_executor.workspace_loader` 的实现）。

        `mirror` = 容器里 /workspace/files 当前的内容记账 `{相对路径: sha256}`，由沙箱会话
        持有（见 session_pool.SandboxSession.workspace_mirror）。命中的文件**这一次不再写进
        容器**——同一 Run 内每次 bash 都把整个文件区重搬一遍，实测约 0.3 秒/文件，200 个
        文件的上限外推 60s/次，而这段时间不在 bash 自己的超时预算内、却在主循环 300s 墙钟内。

        返回 `{"files": {rel: bytes}, "stamps": {rel: sha256}}`：
        - files  只含**真需要写**的（增量）；
        - stamps 覆盖**本次应当在容器里的全部文件**（含被跳过的），交由内核回写记账。

        判据是内容 sha256 而不是版本号/时间戳：文件区的行字典拿不到可靠的版本标记
        （只有 size 和 createdAt），内容寻址是唯一能证明「容器里那份和库里这份一模一样」的
        判据。跳过的前提永远是「字节完全相同」，所以不存在读到旧内容的可能。

        失败不抛——文件区同步不该让整个工具调用失败。
        """
        mirror = dict(mirror or {})
        # 内核在容器需要重建时会**再调一次**（带空记账，见 sandbox_executor 的降级分支）。
        # 累加器必须先清零，否则 notice() 会把同一批跳过的文件报两遍。
        self.skipped = []
        self.shadowed = []
        self.reused_count = 0
        max_files = int(getattr(settings, "SANDBOX_WORKSPACE_MAX_FILES", 200) or 200)
        max_bytes = int(getattr(settings, "SANDBOX_WORKSPACE_MAX_BYTES", 50 * 1024 * 1024) or 0)
        if not self.user_id or max_files <= 0:
            return {"files": {}, "stamps": {}}

        from app.services.files import user_file_service

        try:
            # `"__all__"` 不是可选项而是必需：默认 folder_id=None 时 list_files 的视图过滤是
            # `not r.folder_id`，**只回顶层**。用默认值会让所有归了类的文件静默缺席沙箱，
            # 而 notice() 只报"因上限跳过"的那批 —— 模型对此毫无感知，正是本模块开头
            # 「静默截断比明说更糟」要避免的情形。
            listing = await user_file_service.list_files(self.user_id, "__all__")
            # /chat/upload 的 source=workspace 会被全局清单隐藏，但它仍是用户在
            # 本轮明确选中的素材。只按 selected_file_ids 补回，不放开用户全局
            # 隐藏工作区；这也使附件上下文承诺的 /workspace/files/<name> 成为事实。
            if self.selected_file_ids:
                existing_ids = {
                    str(row.get("id") or "")
                    for row in (listing.get("files") or [])
                    if isinstance(row, dict)
                }
                missing_selected = self.selected_file_ids - existing_ids
                if missing_selected:
                    selected_rows = await user_file_service.get_files_by_ids(
                        self.user_id, list(missing_selected),
                    )
                    listing["files"] = [*(listing.get("files") or []), *selected_rows]
        except Exception:  # noqa: BLE001
            logger.warning("枚举用户文件区失败（本次不同步文件）", exc_info=True)
            if self.workspace_folder_id:
                raise
            return {"files": {}, "stamps": {}}

        rows = scoped_user_file_rows(
            listing.get("files") or [],
            thread_id=self.thread_id,
            selected_file_ids=self.selected_file_ids,
            revision_id=self.revision_id,
            scope_to_thread=self.scope_to_thread,
            workspace_folder_id=self.workspace_folder_id,
        )
        # 同名去重：/workspace/files 是平铺的，两条同名行只能进去一份。list_files 按
        # created_at DESC 返回，保留第一条=最新那条，**必须与 paths.py._find_row 的选择一致**
        # （它也取第一条匹配）——两边不一致的后果是 read_file 解析的是 A 行、bash 里看到并
        # 回写的是 B 行，用户打开自己那份文件发现没变，另一份被写进了不相干的内容。
        unique: dict = {}
        for row in rows:
            nm = str(row.get("filename") or row.get("name") or "").strip()
            if not nm or not str(row.get("id") or ""):
                continue
            if nm in unique:
                self.shadowed.append(nm)
                continue
            unique[nm] = row
        # 原位修订目标必须先进沙箱：它是用户本轮明确选中的文件，不是可舍弃的
        # 历史附件。只按“小文件优先”会在文件区较大时把 6–15MB 的 PPTX 挤出
        # SANDBOX_WORKSPACE_MAX_BYTES，后续 bash 连用户指定的原稿都找不到。目标之后仍
        # 按体积升序，保留原有的预算利用率。
        rows = sorted(
            unique.values(),
            key=lambda f: (
                0 if self.revision_id and str(f.get("id") or "") == self.revision_id else 1,
                int(f.get("size") or 0),
            ),
        )

        # 先按行上的 size 定额度、再去读字节：额度判定不需要真实字节，先读后判等于
        # 白读那些注定进不去的大文件。
        wanted: list = []      # [(name, file_id)] —— 本次应当出现在容器里的文件
        total = 0
        for row in rows:
            file_id = str(row.get("id") or "")
            name = str(row.get("filename") or row.get("name") or "").strip()
            if not file_id or not name:
                continue
            size = int(row.get("size") or 0)
            if len(wanted) >= max_files or (max_bytes and total + size > max_bytes):
                self.skipped.append(name)
                continue
            wanted.append((name, file_id))
            # `_path_to_id` 必须**对全部命中文件**都填上，不能只填这次真读了字节的那些：
            # persist() 靠它判断「覆盖已有」还是「新建」，漏一条就会给同一个文件反复
            # save_file 建新记录——版本历史永远是空的，还很快吃掉文件数上限。
            self._path_to_id[name] = file_id
            if self.workspace_folder_id and row.get("folderId") == self.workspace_folder_id:
                self._file_folder_guards[file_id] = self.workspace_folder_id
            total += size

        # 一次查库 + 并发读盘（read_bytes 是每个文件开一次 DB 会话，200 个文件就是 200 次
        # 串行往返；机器有负载时实测 40 个文件 47 秒）。增量命中的文件连读都不用读。
        blobs = await user_file_service.read_many_bytes(
            self.user_id, [fid for _n, fid in wanted])

        out: dict = {}
        stamps: dict = {}
        for name, file_id in wanted:
            data = blobs.get(file_id)
            if data is None:
                # 元数据在、字节不在（历史遗留/已过期）：跳过并记下，不让它毁掉整批
                logger.info("读取用户文件字节失败，跳过 file_id=%s", file_id)
                self.skipped.append(name)
                self._path_to_id.pop(name, None)
                continue
            digest = hashlib.sha256(data).hexdigest()
            stamps[name] = digest
            self._path_to_stamp[name] = digest  # 留给 persist() 做 CAS（见 __init__ 注释）
            if mirror.get(name) == digest:
                self.reused_count += 1
                continue  # 容器里那份逐字节相同，不用再搬一次
            out[name] = bytes(data)
        # 「同步了几个文件到 /workspace/files」说的是容器里有几个，不是这次写了几个——
        # 报成增量数会让模型以为文件区只剩这么点东西。
        self.synced_count = len(stamps)
        return {"files": out, "stamps": stamps}

    async def prepare(self) -> dict:
        """全量镜像（无增量记账）。保留给不走 workspace_loader 的调用方与既有测试。"""
        return (await self.load({})).get("files") or {}

    def persist_notice(self) -> str:
        """回写被拦下时给模型的一句实话 + 正确做法。空串=没被拦。

        多种情况可以同时发生（比如既有中间产物被滤、又有真产物落库失败），所以是**累加**
        而不是早退：早退会让排在后面的那条永远说不出口。
        """
        parts: list = []
        if self.revision_blocked:
            names = "、".join(self.revision_blocked[:5])
            more = f" 等 {len(self.revision_blocked)} 个" if len(self.revision_blocked) > 5 else ""
            target = self.revision_name or self.revision_id
            parts.append(
                f"（本轮只授权原位修改《{target}》，{names}{more} **没有存进「我的文件」**——"
                "在沙箱里写出来不等于交付给用户。要改的内容请直接写回 "
                f"/workspace/files/{target}；确实需要另做一份或改动别的文件，"
                "先把理由说给用户，由他说「重新做一份」再来。）"
            )
        if self.intermediate_skipped:
            names = "、".join(self.intermediate_skipped[:5])
            more = f" 等 {len(self.intermediate_skipped)} 个" if len(self.intermediate_skipped) > 5 else ""
            parts.append(
                f"（{names}{more} 看着是生成器脚本/中间文件，**没有存进「我的文件」**——"
                "用户要的是产物本身，文件区里多出几个 .py 只会让他找不到真东西。"
                "以后这类脚本请写到 **/workspace/tmp**，只把最终产物放进 /workspace/files/。"
                "如果它本身就是用户要的交付物，改用 write_file 保存。）"
            )
        if self.persist_failed:
            # 最坏的一种沉默：命令 exit 0、产物就在沙箱里、`saved` 却是空的，于是回执上
            # 一个字都不提落库 —— 而工具描述明写「写进 files/ 就等于已保存」。模型据此
            # 向用户宣布交付完成，文件只存在于会被 TTL 回收的容器里。必须明说。
            names = "、".join(self.persist_failed[:5])
            more = f" 等 {len(self.persist_failed)} 个" if len(self.persist_failed) > 5 else ""
            parts.append(
                f"（⚠️ {names}{more} **保存到「我的文件」失败了**——文件目前只在沙箱里，"
                "用户拿不到。常见原因：单文件超过大小上限、文件区配额或数量已满。"
                "请把情况如实告诉用户，不要宣布交付完成。）"
            )
        if self.conflict_files:
            # 乐观并发冲突（409）与上面那条**必须分开说**（2026-07-29 对抗审计）：
            # 原先 409 也落进 persist_failed，于是模型读到的原因是"单文件超上限/配额已满"
            # ——纯属编造，而真实原因是**用户在这次执行期间改过同一个文件**。说错原因的
            # 代价不止是话术难听：模型不会去重读合并，只会重试或向用户报一个假故障。
            names = "、".join(self.conflict_files[:5])
            more = f" 等 {len(self.conflict_files)} 个" if len(self.conflict_files) > 5 else ""
            parts.append(
                f"（⚠️ {names}{more} **没有写回**：用户在你执行期间自己改过这个文件，"
                "为避免覆盖掉他的改动，这次写入被拦下了。请先重新读取该文件的**当前内容**，"
                "把你的改动合并进去再写一次；如果你的改动与用户的改动冲突，"
                "把冲突点如实告诉用户让他决定，不要直接覆盖。）"
            )
        if self.empty_skipped and not self.too_many:
            names = "、".join(self.empty_skipped[:5])
            parts.append(
                f"（{names} 是 0 字节的空文件，**没有存进「我的文件」**——空产物交付给用户就是"
                "一个打不开的东西。请检查生成逻辑是不是没真正写入内容。）"
            )
        if self.too_many:
            parts.append(
                f"（本次在 files/ 下产生了 {self.too_many} 个文件变更，超过单次 {self.MAX_PERSIST} 个"
                "的上限，**都没有存进「我的文件」**。解包、构建这类会摊出大量中间文件的操作请在"
                "/workspace/tmp 下做，只把最终交付的文件复制到 /workspace/files/。）"
            )
        if self.batch_pack:
            packed = self.batch_pack
            parts.append(
                f"（批量交付 {packed.get('requested')} 个文件已打包为《{packed.get('filename')}》，"
                f"成功 {packed.get('packed')} 个"
                + (f"，跳过 {packed.get('skipped')} 个" if packed.get("skipped") else "")
                + "。）"
            )
        if self.html_asset_failed:
            details = []
            for name, refs in list(self.html_asset_failed.items())[:3]:
                details.append(f"{name}：{'、'.join(refs[:4])}")
            parts.append(
                "（⚠️ HTML **没有发布**：本地图片引用无法解析（"
                + "；".join(details)
                + "）。请先用 glob 确认 /workspace/files 中的真实图片名，"
                "修正 HTML 后再写入；不要宣布该 HTML 已交付。）"
            )
        return "".join(parts)

    def notice(self) -> str:
        """给模型看的一句实话：同步了几个、哪些没同步。空串=无需说明。"""
        if not self.skipped and not self.shadowed:
            return ""
        parts = [f"（注意：文件区只同步了 {self.synced_count} 个文件到 /workspace/files"]
        if self.skipped:
            head = "、".join(self.skipped[:8])
            more = f" 等 {len(self.skipped)} 个" if len(self.skipped) > 8 else ""
            parts.append(f"；因体积或数量上限**未同步**：{head}{more}")
        if self.shadowed:
            # 平铺目录容不下两个同名文件。不说的话模型会以为看到的就是用户指的那个。
            head = "、".join(list(dict.fromkeys(self.shadowed))[:5])
            parts.append(
                f"；另有同名文件只进来了最新的一份（{head}），"
                "旧版本不在沙箱里，需要时请先向用户确认要哪一份"
            )
        parts.append("。不要据此认为用户只有这些文件。）")
        return "".join(parts)

    # ---------- 出 ----------

    # 单次回写文件数上限。批量写入几乎从来不是交付意图——实测解一个仓库归档会在 files/ 下
    # 摊出上百个文件，全部落库把用户文件区彻底污染（click-8.1.7_.editorconfig 这种）。
    # 超限就整批不落，并明确告诉模型「把交付物单独挪进 files/」。
    MAX_PERSIST = 20
    _BATCH_DELIVERABLE_EXTS = (
        ".docx", ".xlsx", ".pptx", ".pdf", ".doc", ".xls", ".ppt",
        ".csv", ".txt", ".md",
    )

    # 生成器脚本不是交付物（2026-07-27 真机事故）。用户要一份 PPT，模型把 create_ppt.py
    # 写进 files/ 跑，结果「我的文件」里给用户的是三个 .py——他要的 pptx 反而混在里面。
    # MAX_PERSIST 那条闸拦不住：本次只有 3 个文件，离 20 很远。
    #
    # 为什么是黑名单而不是「只放行 pptx/docx/…」白名单：交付格式是开放集合（csv/json/svg/
    # zip/mp4 都可能是用户真要的东西），白名单会把没预料到的格式静默吞掉。而"中间产物"是
    # 个窄集合，枚举得完。
    #
    # 脚本本身就是交付物时（「写个 python 脚本给我」）走 write_file——那条路径不过这个闸，
    # 且下面会在回执里明确告诉模型这条出路，所以不是能力缺失。
    _INTERMEDIATE_EXTS = (
        ".py", ".pyc", ".pyo", ".pyd", ".sh", ".bash", ".zsh", ".bat", ".ps1",
        ".rb", ".pl", ".class", ".o", ".so",
    )
    _JUNK_NAMES = {".ds_store", "thumbs.db", ".gitignore", ".gitattributes"}
    _RENDER_JUNK_RE = re.compile(
        r"(?:^|/)(?:slide|page)[-_]?\d+\.(?:png|jpe?g)$",
        re.I,
    )
    _JUNK_DIR_PARTS = (
        "__pycache__", ".git", "node_modules", ".venv", "venv", ".pytest_cache",
        ".ipynb_checkpoints", ".mypy_cache", ".egg-info", "dist-info",
    )

    @classmethod
    def _is_intermediate(cls, rel: str) -> bool:
        low = rel.lower()
        parts = [p for p in low.split("/") if p]
        if any(any(j in p for j in cls._JUNK_DIR_PARTS) for p in parts[:-1]):
            return True
        base = parts[-1] if parts else low
        if base in cls._JUNK_NAMES:
            return True
        if cls._RENDER_JUNK_RE.search(low):
            return True
        return base.endswith(cls._INTERMEDIATE_EXTS)

    @classmethod
    def _is_batch_deliverable(cls, changes: list) -> bool:
        """Homogeneous top-level office/text files: pack instead of dropping."""
        if not changes:
            return False
        exts: set[str] = set()
        for item in changes:
            rel = str((item or {}).get("path") or "").replace("\\", "/").strip()
            if not rel or "/" in rel:
                return False
            lower = rel.lower()
            if not any(lower.endswith(ext) for ext in cls._BATCH_DELIVERABLE_EXTS):
                return False
            exts.add(lower.rsplit(".", 1)[-1])
        return len(exts) == 1

    @staticmethod
    def _pack_batch_zip(changes: list) -> tuple[str, bytes, int, int]:
        buf = io.BytesIO()
        packed = 0
        skipped = 0
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in changes:
                rel = str((item or {}).get("path") or "").replace("\\", "/").strip()
                data = (item or {}).get("data")
                if not rel or not isinstance(data, (bytes, bytearray)) or not data:
                    skipped += 1
                    continue
                archive.writestr(rel.rsplit("/", 1)[-1], bytes(data))
                packed += 1
        return "batch-deliverables.zip", buf.getvalue(), packed, skipped

    async def persist(self, changes: list) -> list:
        """把沙箱里改动过的文件落库。返回**落库结果 dict 列表**（save_file/overwrite_file 的
        返回值），不是文件名——前端文件卡靠这些 dict 渲染（名称/大小/下载/我的文件入口），
        只回名字的话文件会落库但**界面上看不到卡片**（迁移时差点漏掉这条）。

        同名已存在 → `overwrite_file`（保持 file_id 不变、进版本历史）；
        新路径 → `save_file`（source=generated，带产物血缘 run_id）。
        """
        if not changes or not self.user_id:
            return []
        # 先滤掉中间产物，再判 MAX_PERSIST：否则一堆 __pycache__ 就能把真交付物一起顶掉
        kept: list = []
        for item in changes:
            rel = str((item or {}).get("path") or "").strip()
            existing_id = self._path_to_id.get(rel) or self._path_to_id.get(rel.replace("/", "_"))
            if rel and self._is_intermediate(rel) and not existing_id:
                self.intermediate_skipped.append(rel)
                continue
            kept.append(item)
        if not kept:
            return []
        changes = kept
        if self.revision_id:
            from app.services.files import deliverable

            # 目标产物的**编辑器伴生文件**（`<名>.slides.json`）要跟着放行：它不是"另建替代品"，
            # 是同一份产物的可编辑源。拦掉它的后果是静默的——PPT 改完了、「我的文件」里那张卡
            # 的「编辑」按钮还在，点开却是改动前的旧内容（配对逻辑见 deliverable 模块注释）。
            stem = self.revision_name.rsplit(".", 1)[0] if "." in self.revision_name else ""
            # 认亲用 file_id 而不是文件名：名字对得上但不是同一行（同名新建）也是「另建替代品」。
            # rel 与扁平名两个键都查，口径与下面 save/overwrite 的分支完全一致。
            allowed: list = []
            for item in changes:
                rel = str((item or {}).get("path") or "").strip()
                name = rel.replace("/", "_")
                fid = self._path_to_id.get(rel) or self._path_to_id.get(name)
                if fid and fid == self.revision_id:
                    allowed.append(item)
                elif stem and deliverable.is_editor_companion(name) and name.startswith(stem + "."):
                    allowed.append(item)
                elif rel:
                    self.revision_blocked.append(rel)
            if not allowed:
                return []
            changes = allowed
        if len(changes) > self.MAX_PERSIST:
            if self._is_batch_deliverable(changes):
                filename, blob, packed, skipped = self._pack_batch_zip(changes)
                self.batch_pack = {
                    "filename": filename,
                    "requested": len(changes),
                    "packed": packed,
                    "skipped": skipped,
                }
                logger.info(
                    "files/ 本次交付 %d 个，超过 %d，已打包为 %s",
                    len(changes), self.MAX_PERSIST, filename,
                )
                changes = [{"path": filename, "data": blob}]
            else:
                # 不静默丢弃、也不硬落上百个文件：如实报数并给出正确做法
                self.too_many = len(changes)
                logger.info("files/ 本次变更 %d 个，超过 %d，整批不落库", len(changes), self.MAX_PERSIST)
                return []
        from app.services.files import user_file_service

        # 一次收齐本轮写入的素材，HTML 可以引用同一 bash 批次产生的图片。
        # 原有 /workspace/files 镜像中未改动的图片会在需要时按 file_id 另行读取。
        changed_assets: dict[str, bytes] = {}
        for item in changes:
            rel = str((item or {}).get("path") or "").strip()
            raw = (item or {}).get("data")
            if rel and isinstance(raw, (bytes, bytearray)) and raw:
                changed_assets[rel] = bytes(raw)

        mirrored_assets: dict[str, bytes] = {}
        mirrored_ids = list(dict.fromkeys(str(fid) for fid in self._path_to_id.values() if fid))
        if mirrored_ids and any(
            str((item or {}).get("path") or "").lower().endswith((".html", ".htm"))
            for item in changes
        ):
            blobs = await user_file_service.read_many_bytes(self.user_id, mirrored_ids)
            for path, fid in self._path_to_id.items():
                blob = blobs.get(str(fid))
                if blob:
                    mirrored_assets[path] = blob

        saved: list = []   # 元素是落库返回的 dict（含 id/filename/size/versionNo）
        for item in changes:
            rel = str((item or {}).get("path") or "").strip()
            data = (item or {}).get("data")
            if not rel or not isinstance(data, (bytes, bytearray)):
                continue
            if not data:
                # 空产物不落库（自 execute_in_sandbox 的 outputs/ 链路继承过来的行为）：0 字节文件
                # 落进「我的文件」就是给用户一个打不开的东西，比不落更糟。
                self.empty_skipped.append(rel)
                logger.info("空产物不落库 path=%s", rel)
                continue
            # 落库用扁平文件名：「我的文件」是平铺+文件夹模型，没有任意深度路径
            name = rel.replace("/", "_")
            # 认亲要用**落库名**兜一手：`_path_to_id` 的键是 DB 文件名（已扁平），而 rel 是
            # 沙箱里的相对路径。`files/out/report.md` 反复编辑时 `get(rel)` 必然 miss，
            # 于是每轮 save_file 建一条新记录——版本历史永远是空的，还很快吃掉文件数上限。
            file_id = self._path_to_id.get(rel) or self._path_to_id.get(name)
            try:
                if name.lower().endswith((".html", ".htm")):
                    from app.services.files.html_artifact_service import bundle_html_for_thread

                    bundled = await bundle_html_for_thread(
                        user_id=self.user_id,
                        thread_id=self.thread_id,
                        html=bytes(data),
                        html_path=rel,
                        extra_assets={**mirrored_assets, **changed_assets},
                    )
                    if bundled.missing:
                        self.html_asset_failed[name] = list(bundled.missing)
                        logger.info(
                            "HTML 本地图片无法解析，未发布 path=%s refs=%s",
                            rel, bundled.missing,
                        )
                        continue
                    data = bundled.data
                if file_id:
                    row = await user_file_service.overwrite_file(
                        self.user_id, file_id, bytes(data), filename=name,
                        thread_id=self.thread_id or None, run_id=self.run_id or None,
                        change_summary="沙箱内改动（bash）",
                        # 乐观并发（2026-07-29）：拿装载时刻的摘要做 CAS——用户在 bash 执行
                        # 期间改过同一个文件时抛 409，落进下面的 persist_failed 并由
                        # persist_notice() 如实报给模型，而不是静默盖掉用户的版本。
                        # 装载时被跳过/没读到字节的文件拿不到摘要，此时退回无条件覆盖
                        # （与改动前行为一致，不新增失败面）。
                        expected_sha256=(
                            self._path_to_stamp.get(name) or self._path_to_stamp.get(rel) or None
                        ),
                        **({"expected_folder_id": self._file_folder_guards[file_id]}
                           if file_id in self._file_folder_guards else {}),
                    )
                else:
                    row = await user_file_service.save_file(
                        self.user_id, name, bytes(data), source="generated",
                        thread_id=self.thread_id or None, run_id=self.run_id or None,
                    )
                saved.append(dict(row or {}) or {"filename": name})
            except Exception as exc:  # noqa: BLE001
                # 单个文件落库失败不毁整批。**但"回执里少一个名字"不等于报了**——回执是
                # 拼出来的，少一个名字模型根本看不出来。记进 persist_failed，由
                # persist_notice() 明说，否则就是「命令成功 + 产物丢了 + 模型宣布交付」。
                #
                # 409（CAS 冲突：用户在本次执行期间改过同一文件）单独记账：它和"存不下"
                # 是两回事，处方也相反（重读合并 vs 告知配额）。混在一起时模型照文案
                # 编造"配额已满"，还不会去合并（2026-07-29 对抗审计）。
                if getattr(exc, "status_code", None) == 409:
                    self.conflict_files.append(name)
                    logger.info("文件区改动与用户并发编辑冲突，未覆盖 path=%s", rel)
                else:
                    self.persist_failed.append(name)
                    logger.warning("文件区改动落库失败 path=%s", rel, exc_info=True)
        return saved


def build_sync(
    user_id: Optional[str], *, thread_id: str = "", run_id: str = "",
    revision_target: Optional[dict] = None,
    selected_file_ids: Optional[Iterable[str]] = None,
    scope_to_thread: bool = False,
    workspace_folder_id: str = "",
) -> Optional[WorkspaceSync]:
    """无 user_id 时返回 None（没有文件区可镜像，也没有落库归属）。

    2026-07-29：同步已是**唯一形态**，总开关 SANDBOX_WORKSPACE_SYNC 随旧 execute_in_sandbox 工具族一并
    下线，这里只剩 user_id 这一个前提。
    """
    if not user_id:
        return None
    return WorkspaceSync(user_id, thread_id=thread_id, run_id=run_id,
                         revision_target=revision_target,
                         selected_file_ids=selected_file_ids,
                         scope_to_thread=scope_to_thread,
                         workspace_folder_id=workspace_folder_id)
