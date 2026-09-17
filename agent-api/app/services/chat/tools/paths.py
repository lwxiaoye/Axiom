# -*- coding: utf-8 -*-
"""路径寻址的文件工具（2026-07-27，批 2）——`read_file` / `write_file` / `edit_file` / `glob`。

## 为什么要它

旧的三件套按 `file_id`（UUID）寻址：模型得先 `list_files` 拿 id、再把 id 传进工具，而 `execute_in_sandbox`
里又是另一套路径寻址（`/workspace/inputs/<名>`）。**同一批文件在模型脑子里有两个地址空间**，
prompt 里一大段规则都在给这个割裂打补丁。

统一文件系统落地后 `/workspace/files` 就是「我的文件」，这组工具直接在**同一个地址空间**里读写，
与 `bash` 看到的是同一批文件、同一个路径。

## 与 bash 的分工（都能做的事为什么还要单开工具）

`bash` 能 `cat`/`echo >`，但三件事它做不好，而这三件正是主对话最高频的：
- **读**：二进制格式（pdf/docx/xlsx/图片）`cat` 出来是乱码，这里按后缀走 `document_parse`；
- **精确改**：`sed -i` 的转义地狱 + 静默不匹配。`edit_file` 做**唯一匹配校验**，命中 0 次或
  多次都明确报错，不会悄悄改错地方（借鉴 pi 的 edit.ts）；
- **可控截断**：`cat` 一个 5MB 的 json 直接冲爆上下文；这里有行区间和字节上限。

## 落库语义

写完直接落「我的文件」（`overwrite_file` 保 file_id / `save_file` 新建），不经过沙箱——
这组工具**不起容器**，比 `bash` 快一个数量级，也不占并发闸。
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from typing import List, Optional

from app.services.files import deliverable

from .base import INTENT_PROP, MainTool, ToolSoftError, ToolValue, _file_action, _file_origin, _line_diff_counts, text_tool_body

logger = logging.getLogger(__name__)

# 单次读取回执的字节上限（超出按行截断并如实标注）
_MAX_READ_BYTES = 120_000
# download_url 的绝对硬顶：配置被调大时 agent-api 进程也不该被一个 URL 吃穿内存。
# **真正生效的是下面 _download_limit() 算出来的值**——见那里的注释。
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
# 需要走解析器而不是当文本读的后缀（**读**的判据）
_PARSED_EXTS = (
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
)
# 不能按文本精确替换的后缀（**改**的判据）。与 _PARSED_EXTS 分开是因为两者不是一回事：
# .csv 走解析器读（表格化更好读）但**完全可以**按文本改，混在一张表里会让 edit_file 回一句
# 「data.csv 是二进制/富格式，不能按文本精确替换」——事实错误，还把模型赶去起容器。
_UNEDITABLE_EXTS = tuple(e for e in _PARSED_EXTS if e != ".csv")
_WORKSPACE_PREFIX = "/workspace/files/"


def _download_limit() -> int:
    """下载上限必须跟**落库上限**走，不能比它大。

    原先写死 64MB，而「我的文件」单文件上限是 `USER_FILES_MAX_SIZE_MB`（默认 15MB）：
    一个 40MB 的归档会老老实实下完 40MB，再在 `save_file` 里撞 413 —— 白等、白占内存，
    而工具描述还在教模型「先 download_url 取仓库归档」。对齐之后超限在**读第一个字节之前**
    就退，并且退的是一句能照着做的话（见 `_download`）。
    每次调用重新读配置：settings 可被运行时改/被测试 monkeypatch。
    """
    from app.core.config import settings

    mb = int(getattr(settings, "USER_FILES_MAX_SIZE_MB", 15) or 15)
    return max(1, min(_MAX_DOWNLOAD_BYTES, mb * 1024 * 1024))


def _name_from_url(url: str) -> str:
    """从 URL 末段推文件名，**先砍查询串和 fragment**。

    直接取 `rsplit("/")` 的结果会把 `?sig=AKIA…&token=secret` 整段带进文件名——签名和
    令牌就这样落进「我的文件」列表、工具回执和数据库，而且扩展名被毁（`.zip` 认不出来，
    后续引导模型 unzip 的那句话也就失效了）。
    """
    path = re.split(r"[?#]", str(url or ""), maxsplit=1)[0]
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return tail or "download.bin"


def normalize_path(raw: str) -> str:
    """把模型给的路径收敛成「我的文件」里的扁平文件名。

    模型会用三种写法，都要接住（否则它每次都得猜）：
      `/workspace/files/a.txt`、`files/a.txt`、`a.txt`
    子目录压成 `_`：「我的文件」是平铺 + 文件夹模型，没有任意深度路径。
    """
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        raise ToolSoftError("请给出文件路径。")
    # 前缀剥离必须**要求分隔符**：不要求的话 `/workspace/filesa.txt` 会被剥成 `a.txt`
    # ——指向一个完全不同的文件，静默读/写错对象；`/workspace/files-backup/x.md` 更是被
    # 剥成 `-backup_x.md` 这种垃圾名（2026-07-27 审计）。
    if value.startswith(_WORKSPACE_PREFIX):          # "/workspace/files/"
        value = value[len(_WORKSPACE_PREFIX):]
    elif value.rstrip("/") == "/workspace/files":
        raise ToolSoftError("请给出具体的文件名，而不是目录 /workspace/files。")
    elif value.startswith("files/"):
        value = value[len("files/"):]
    else:
        # `/workspace/` 下**非 files** 的路径必须报错，不能压平（2026-07-27 真机事故）。
        #
        # 原先 `/workspace/tmp/build.py` 会被 `"_".join` 压成 `workspace_tmp_build.py` 并
        # 存进用户「我的文件」——而沙箱里那个路径依然是空的。模型不是乱来：/workspace/tmp
        # 正是平台自己在工具描述、persist_notice() 和系统提示词里让它写的地方，它只是选了
        # write_file 而不是 bash。压平等于把一个**类别错误**静默转成脏数据，正是这轮的母题。
        # skills/ 与 outputs/ 同理（旧 read_file 曾有 skills 的兼容提示，迁移时丢了）。
        stripped = value.lstrip("/")
        if stripped.startswith("workspace/"):
            seg = stripped.split("/", 2)
            sub = seg[1] if len(seg) > 1 else ""
            raise ToolSoftError(
                f"{raw!r} 是**沙箱内**的路径（/workspace/{sub}/…），而本工具读写的是用户"
                "「我的文件」——两者只有 /workspace/files/ 这一处是同一批文件。\n"
                f"要在沙箱里读写 /workspace/{sub}/ 请用 **bash**"
                "（例如 `cat`、`python3 - <<'EOF' … EOF` 写文件）；"
                "要交付给用户就写到 /workspace/files/ 下、或直接用本工具的相对文件名。"
            )
    parts = [p for p in value.split("/") if p and p not in (".", "..")]
    if not parts:
        raise ToolSoftError(f"路径 {raw!r} 解析后为空，无法定位文件。")
    return "_".join(parts)


def _is_parsed(name: str) -> bool:
    return name.lower().endswith(_PARSED_EXTS)


async def _find_row(
    user_id: str,
    name: str,
    *,
    thread_id: str = "",
    selected_file_ids: Optional[set[str]] = None,
    revision_id: str = "",
    scope_to_thread: bool = False,
    workspace_folder_id: str = "",
) -> Optional[dict]:
    from app.services.files import user_file_service
    from app.services.chat.tools.workspace_sync import scoped_user_file_rows

    # `"__all__"` 必需：默认 folder_id=None 只回顶层（视图过滤 `not r.folder_id`），
    # 用户一旦把文件归进文件夹，这里就找不到了。随后按会话/选中件做可见性过滤，
    # 必须与 workspace_sync.load() 同口径，否则 bash 里明明有这个文件、
    # read_file 却说不存在；同名时更会各自选中不同的行（一边解析 A、一边回写 B）。
    listing = await user_file_service.list_files(user_id, "__all__")
    rows = scoped_user_file_rows(
        listing.get("files") or [],
        thread_id=thread_id,
        selected_file_ids=selected_file_ids,
        revision_id=revision_id,
        scope_to_thread=scope_to_thread,
        workspace_folder_id=workspace_folder_id,
    )
    for row in rows:
        if str(row.get("filename") or "") == name:
            return row
    return None


def build_path_tools(
    *,
    user_id: str,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    tool_meta_sink: Optional[dict] = None,
    newapi_key: str = "",
    # search_web 的图片目录（[图N] → 真实直链）。模型只看得到编号+标题，URL 只在这里，
    # 所以 download_url 必须能按编号解析——否则 PPT 配图无路可走。
    image_sink: Optional[List[dict]] = None,
    # 原位修改授权（Harness）：非空时本轮只允许改这一个文件，且写入走 sha256 乐观锁。
    # 这两条语义原先只在遗留 file_id 版工具里，移植过来才能让它们退休。
    revision_target: Optional[dict] = None,
    user_message: str = "",
    persist_downloads: bool = True,
    execution_profile: Optional[dict] = None,
    attachments: Optional[List] = None,
    scope_to_thread: bool = False,
    workspace_folder_id: str = "",
) -> List[MainTool]:
    from app.services.files import user_file_service

    revision_id = str((revision_target or {}).get("file_id") or "").strip()
    revision_name = str((revision_target or {}).get("filename") or "").strip()
    selected_file_ids = {
        str(
            (att.get("file_id") if isinstance(att, dict) else getattr(att, "file_id", ""))
            or ""
        ).strip()
        for att in (attachments or [])
    }
    selected_file_ids.discard("")
    folder_guards: dict[str, str] = {}

    async def _find_visible_row(name: str) -> Optional[dict]:
        row = await _find_row(
            user_id,
            name,
            thread_id=str(thread_id or ""),
            selected_file_ids=selected_file_ids,
            revision_id=revision_id,
            scope_to_thread=scope_to_thread,
            workspace_folder_id=workspace_folder_id,
        )
        if row and workspace_folder_id and row.get("folderId") == workspace_folder_id:
            folder_guards[str(row["id"])] = workspace_folder_id
        return row
    # 可变快照：同一轮里连续改同一个文件时，每次写成功后更新它，下一次才不会被自己的
    # 上一次写撞成冲突（遗留实现同款做法）。
    revision_guard = {"sha256": str((revision_target or {}).get("sha256") or "").strip().lower()}
    read_stamps: dict[str, str] = {}
    # ：同轮 URL 去重 + 同名已存在复用，避免断点续做/交付后把同一张图重下十几次。
    _url_to_name: dict = {}
    _visible_scope_label = (
        "选中的工作文件夹、本对话内部工作文件和本轮明确选中的文件"
        if scope_to_thread else "用户「我的文件」"
    )

    def _require_target(fid: str, name: str) -> None:
        """本轮只授权原位修改某个文件时，拦住「改别的文件」和「另建替代品」。"""
        if revision_id and fid != revision_id:
            raise ToolSoftError(
                f"本轮只授权原位修改《{revision_name or revision_id}》，"
                f"不能改写其它文件（{name}）或另建替代品。"
            )

    async def _atomic_write(fid: str, name: str, data: bytes, summary: str) -> dict:
        """按读取时的 sha256 写入，避免共享工作文件夹的并发修改相互覆盖。

        **返回落库行**：前端文件卡靠这个 dict 渲染（名称/大小/下载/我的文件入口），任务模式
        的 artifact_refs 也从它来。丢掉返回值 = 文件存了但界面上看不到卡片、且交付审查判
        「未产出任何产物」。
        """
        try:
            row = await user_file_service.update_file_content(
                user_id, fid, data.decode("utf-8", errors="replace"),
                thread_id=thread_id, run_id=run_id, change_summary=summary,
                expected_sha256=revision_guard["sha256"] if fid == revision_id else read_stamps.get(fid),
                **({"expected_folder_id": folder_guards[fid]} if fid in folder_guards else {}),
            )
        except user_file_service.UserFileError as e:
            raise ToolSoftError(f"写入失败：{e}") from None
        if fid == revision_id:
            revision_guard["sha256"] = hashlib.sha256(data).hexdigest()
        read_stamps[fid] = hashlib.sha256(data).hexdigest()
        return dict(row or {}) or {"id": fid, "filename": name, "size": len(data)}

    def _emit_file_meta(tool: str, row: dict, action: dict) -> None:
        """`files` 必须带上，且 `origin` 挂在**行内**而不是 meta 顶层。

        前端 `executionTimeline.ts` 只在 `ev.meta.files?.length` 时才合入产物卡，读的血缘
        也是 `meta.files[].origin`；任务模式 `runners.py` 的 artifact_refs 同样只认
        `meta["files"]`，空了就命中「任务声明了必须交付物，但未产出任何产物」。

        过程脚本（.py 等非交付后缀）仍落库供下一步 bash 使用，但**不**进 files 元数据——
        否则产物卡/契约矩阵会把 gen_*.py 和 Word 并列成「交付物」。
        """
        if tool_meta_sink is None:
            return
        row = dict(row or {})
        fname = str(row.get("filename") or row.get("name") or "")
        src = row.get("source") or "generated"
        # 行上若已算 deliverable 就信它；否则按文件名判。上传件永远可见。
        is_deliv = row.get("deliverable")
        if is_deliv is None:
            is_deliv = deliverable.is_deliverable(fname, src)
        if not is_deliv and src in ("generated", "", None):
            # 仍写 action 便于时间线显示「已写入 build.py」，但不带 files 卡
            tool_meta_sink[tool] = {"action": action, "process_file": fname}
            return
        row["origin"] = _file_origin(tool, run_id)
        tool_meta_sink[tool] = {"files": [row], "action": action}

    async def _read(args: dict) -> ToolValue:
        name = normalize_path(args.get("path"))
        head_note = ""
        row = await _find_visible_row(name)
        if row is None:
            raise ToolSoftError(
                f"文件 {name} 不存在。先用 glob 看看文件区里有什么（别猜文件名）。"
            )
        _r, data = await user_file_service.read_bytes(user_id, str(row["id"]))
        read_stamps[str(row["id"])] = hashlib.sha256(data).hexdigest()

        if _is_parsed(name):
            # 二进制/富格式：cat 出来是乱码，走平台既有解析器（PDF→文字、图片→OCR）
            # 走平台既有的上传解析管线（PDF→文字、图片→OCR、docx/pptx 内嵌图片也识别）。
            # 必须透传本请求的 New API key，否则回退平台网关时无凭证（旧 read_file 同款）。
            from app.services.files import document_parse_service
            try:
                parsed = await document_parse_service.parse_upload(
                    name, data, newapi_key=newapi_key)
                text = str(parsed.get("text") or "")
                if not text.strip():
                    raise ToolSoftError(f"{name} 未能解析出文本内容。")
            except ToolSoftError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("解析文件失败 name=%s", name, exc_info=True)
                raise ToolSoftError(
                    f"{name} 是需要解析的格式，但这次解析失败了。"
                    "可以用 bash 在沙箱里用 python 处理它（文件在 /workspace/files/）。"
                ) from None
        else:
            # NUL 嗅探：非白名单二进制（download_url 抓回来的 tar.gz 是主打用法）当文本硬解码
            # 会把 5000+ 字符的替换符乱码灌进上下文，而模型看不出这是二进制。
            if b"\x00" in data[:4096]:
                raise ToolSoftError(
                    f"{name} 看起来是二进制文件（含 NUL 字节），当文本读只会得到乱码。"
                    f"用 bash 在沙箱里处理它（{_WORKSPACE_PREFIX}{name}）。"
                )
            # 编码兜底：UTF-8 → GBK（中文环境常见）→ 最后 replace，并**如实告知**降级
            enc_note = ""
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = data.decode("gbk")
                    enc_note = "（注意：该文件不是 UTF-8，按 GBK 解出；edit_file 改不了它，需要改请用 bash）"
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                    enc_note = "（注意：该文件编码无法识别，以下内容含替换字符，不要据此精确编辑）"
            if enc_note:
                head_note = enc_note

        offset = max(0, int(args.get("offset") or 0))
        raw_limit = args.get("limit")
        # 类型守卫：limit="abc" 原先直接抛 ValueError（硬失败而非软错误）
        try:
            limit = max(1, int(raw_limit)) if raw_limit not in (None, "", 0) else None
        except (TypeError, ValueError):
            limit = None
        lines = text.splitlines()
        total = len(lines)
        if offset >= total and total:
            # 原先静默回一段空正文 + "本次第 100~99 行"，模型很可能判成"文件是空的"
            raise ToolSoftError(
                f"{name} 共 {total} 行，offset={offset} 已越过文件末尾。"
                f"请给 0~{total - 1} 之间的 offset。")
        if limit:
            lines = lines[offset: offset + limit]
        elif offset:
            lines = lines[offset:]
        body = "\n".join(lines)
        truncated = False
        if len(body.encode("utf-8", errors="ignore")) > _MAX_READ_BYTES:
            body = body.encode("utf-8", errors="ignore")[:_MAX_READ_BYTES].decode("utf-8", "ignore")
            truncated = True

        if tool_meta_sink is not None:
            tool_meta_sink["read_file"] = {"action": _file_action("read", name, file_id=str(row["id"]))}
        head = f"{_WORKSPACE_PREFIX}{name}（共 {total} 行"
        head += f"，本次第 {offset + 1}~{offset + len(lines)} 行" if (offset or limit) else ""
        head += "，内容已按上限截断" if truncated else ""
        model_text = head + "）" + (f"\n{head_note}" if head_note else "") + "\n\n" + body
        # P1.6：读路径也回结构化 observation（UI 摘要与模型全文分离）
        return ToolValue(
            model_content=model_text,
            ui={
                "summary": f"读了 {name}",
                "detail": head + "）",
                "action": "read",
                "path": name,
                "file_id": str(row.get("id") or ""),
                "lines": total,
            },
        )

    async def _write(args: dict) -> ToolValue:
        name = normalize_path(args.get("path"))
        content = args.get("content")
        if content is None:
            raise ToolSoftError("请给出要写入的内容（content）。缺内容时不写空文件覆盖原文件。")
        if name.lower().endswith(_UNEDITABLE_EXTS):
            # 产物质检的前置闸（2026-07-28）：write_file 只会写 UTF-8 文本，写出来的
            # .docx/.pptx/.xlsx/.pdf/图片**必然是打不开的坏文件**（不是 zip/二进制容器）。
            # bash 产物有 review_file 硬校验兜底，这条通道则在写之前就拦掉——
            # 省一轮「写坏→质检 fail→返工」。
            raise ToolSoftError(
                f"{name} 是富格式/二进制文件，write_file 只能写 UTF-8 文本，直接写出来的"
                "必然是打不开的坏文件。要生成它请用 bash 在沙箱里跑对应库"
                f"（python-docx / python-pptx / openpyxl 等，写到 {_WORKSPACE_PREFIX} 下即自动保存），"
                "或者改存 .md/.txt/.csv 等文本格式。")
        data = str(content).encode("utf-8")
        row = await _find_visible_row(name)
        if row is not None:
            _require_target(str(row["id"]), name)
            _r, old = await user_file_service.read_bytes(user_id, str(row["id"]))
            read_stamps.setdefault(str(row["id"]), hashlib.sha256(old).hexdigest())
            added, removed = _line_diff_counts(old.decode("utf-8", "replace"), str(content))
            saved = await _atomic_write(str(row["id"]), name, data, "write_file 整体覆写")
            verb, fid = "已覆写", str(row["id"])
        else:
            if revision_id:
                raise ToolSoftError(
                    f"本轮只授权原位修改《{revision_name or revision_id}》，不能新建 {name}。")
            saved = await user_file_service.save_file(
                user_id, name, data, source="generated", thread_id=thread_id, run_id=run_id)
            added, removed = len(str(content).splitlines()), 0
            verb, fid = "已新建", str(saved.get("id") or "")
        _emit_file_meta(
            "write_file", saved,
            _file_action("write", name, file_id=fid, added=added, removed=removed))
        # ⚠️ 必须回报沙箱内的绝对路径。只说「已保存到我的文件」的话，模型下一步用 bash 跑它
        # 会在 /workspace 下找不到（真机实测：write_file 存了 generate_ppt.py，
        # bash 执行 python3 generate_ppt.py 直接「脚本找不到」，整条任务失败）。
        is_user_deliverable = deliverable.is_deliverable(name, "generated")
        if is_user_deliverable:
            model_text = (
                f"{verb} {name}（+{added} -{removed} 行），已保存到「我的文件」。"
                f"\n沙箱内路径：{_WORKSPACE_PREFIX}{name}"
                f"（要用 bash 运行它就写全路径，或先 cd {_WORKSPACE_PREFIX.rstrip('/')}）"
            )
        else:
            # 过程脚本/中间数据：落库供下一步 bash 使用，但不是交付物。
            model_text = (
                f"{verb} {name}（+{added} -{removed} 行），这是过程文件，不会作为交付物展示给用户。"
                f"\n沙箱内路径：{_WORKSPACE_PREFIX}{name}"
                f"（继续用 bash 运行它生成 .docx/.pptx/.pdf 等真正交付文件）。"
            )
        arts = []
        if is_user_deliverable:
            arts = [{
                "filename": name,
                "file_id": fid,
                "bytes": len(data),
                "path": f"{_WORKSPACE_PREFIX}{name}",
            }]
        return ToolValue(
            model_content=model_text,
            ui={"summary": f"{verb} {name}", "detail": model_text[:500], "action": "write"},
            artifacts=arts,
        )

    async def _edit(args: dict) -> ToolValue:
        name = normalize_path(args.get("path"))
        old_string = str(args.get("old_string") or "")
        if "new_string" not in args or args.get("new_string") is None:
            # 漏传不能当成"删除"：静默删掉一段内容是最难发现的破坏。要删就显式传空串。
            raise ToolSoftError(
                "缺少 new_string。要**删除**这段内容请显式传 new_string=\"\"；"
                "漏传参数不会被当成删除。")
        new_string = str(args.get("new_string") or "")
        if not old_string:
            raise ToolSoftError("old_string 不能为空——要整体覆写请用 write_file。")
        if old_string == new_string:
            raise ToolSoftError("old_string 与 new_string 相同，这次编辑没有任何效果。")
        row = await _find_visible_row(name)
        if row is None:
            raise ToolSoftError(
                f"文件 {name} 不存在或不在本轮授权范围内。"
                "本轮只授权原位修改已选目标；请依据当前可见工作区重新定位文件。"
            )
        _require_target(str(row["id"]), name)
        if name.lower().endswith(_UNEDITABLE_EXTS):
            raise ToolSoftError(
                f"{name} 是二进制/富格式，不能按文本精确替换。"
                "用 bash 在沙箱里用 python-docx / openpyxl 这类库改（文件在 /workspace/files/）。"
            )
        _r, data = await user_file_service.read_bytes(user_id, str(row["id"]))
        read_stamps.setdefault(str(row["id"]), hashlib.sha256(data).hexdigest())
        # 严格解码，**不用 errors="replace"**：那样非 UTF-8 文本（中文用户的 GBK txt/csv 是
        # 常态）会被替换字符污染，写回时一次调用就把整份文件的中文毁掉，而模型读到的也是
        # 乱码却毫无提示。旧实现就是严格解码 + 明确拒绝，迁过来时丢了。
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolSoftError(
                f"{name} 不是 UTF-8 纯文本（可能是 GBK 等其它编码），无法安全地按文本精确替换"
                "——强行改会把整份文件的中文变成乱码。请用 bash 在沙箱里处理"
                f"（{_WORKSPACE_PREFIX}{name}，可以先探测编码再转码）。"
            ) from None

        # 唯一匹配校验（借鉴 pi 的 edit.ts）：0 次和多次都必须明确报错。
        # `sed -i` 的静默不匹配是最坏的形态——模型以为改了，其实什么都没发生。
        hits = text.count(old_string)
        if hits == 0 and "\r\n" in text and "\r\n" not in old_string:
            # read_file 把 CRLF 归一成 LF 展示，模型照着它给的 old_string 自然是 LF，
            # 而这里在原文上匹配 → 永远 0 次命中，模型只会照着同样的内容反复重试到轮次耗尽。
            # 把 old/new 转成文件自己的换行风格再匹配，**保留文件原有的 CRLF**（不擅自改
            # 全文换行符：那是一次用户没要求的全文改动）。
            crlf_old = old_string.replace("\n", "\r\n")
            if text.count(crlf_old) >= 1:
                old_string, new_string = crlf_old, new_string.replace("\n", "\r\n")
                hits = text.count(old_string)
        if hits == 0:
            raise ToolSoftError(
                f"在 {name} 里找不到 old_string（一字不差地匹配才算）。"
                "先 read_file 看清原文再改，注意空格、缩进和换行。"
            )
        if hits > 1:
            raise ToolSoftError(
                f"old_string 在 {name} 里出现了 {hits} 次，无法确定改哪一处。"
                "请把 old_string 加长到唯一（多带上下文几行）。"
            )
        updated = text.replace(old_string, new_string, 1)
        added, removed = _line_diff_counts(text, updated)
        saved = await _atomic_write(
            str(row["id"]), name, updated.encode("utf-8"), "edit_file 精确替换")
        _emit_file_meta(
            "edit_file", saved,
            _file_action("edit", name, file_id=str(row["id"]), added=added, removed=removed))
        model_text = (
            f"已修改 {name}（+{added} -{removed} 行），已保存到「我的文件」。"
            f"\n沙箱内路径：{_WORKSPACE_PREFIX}{name}"
        )
        return ToolValue(
            model_content=model_text,
            ui={"summary": f"已修改 {name}", "detail": model_text[:500], "action": "edit"},
            artifacts=[{
                "filename": name,
                "file_id": str(row["id"]),
                "bytes": len(updated.encode("utf-8")),
                "path": f"{_WORKSPACE_PREFIX}{name}",
            }],
        )

    async def _glob(args: dict) -> ToolValue:
        pattern = str(args.get("pattern") or "*").strip() or "*"
        # 先取全库元数据，再按当前会话/明确选中件过滤；不能用文件夹视图代替权限范围。
        listing = await user_file_service.list_files(user_id, "__all__")
        from app.services.chat.tools.workspace_sync import scoped_user_file_rows
        rows = scoped_user_file_rows(
            listing.get("files") or [],
            thread_id=str(thread_id or ""),
            selected_file_ids=selected_file_ids,
            revision_id=revision_id,
            scope_to_thread=scope_to_thread,
            workspace_folder_id=workspace_folder_id,
        )
        matched = [r for r in rows if fnmatch.fnmatch(str(r.get("filename") or ""), pattern)]
        if tool_meta_sink is not None:
            tool_meta_sink["glob"] = {
                "action": {"operation": "glob", "target": pattern}, "count": len(matched)}
        if not matched:
            hint = "文件区是空的。" if not rows else f"文件区有 {len(rows)} 个文件，但没有匹配 {pattern} 的。"
            model_text = f"没有匹配 {pattern} 的文件。{hint}"
            return ToolValue(
                model_content=model_text,
                ui={
                    "summary": f"没有匹配 {pattern}",
                    "detail": model_text,
                    "action": "glob",
                    "pattern": pattern,
                    "count": 0,
                },
            )
        lines = [
            f"- {r.get('filename')}  {int(r.get('size') or 0)} 字节  {str(r.get('createdAt') or '')[:19]}"
            for r in sorted(matched, key=lambda x: str(x.get("createdAt") or ""), reverse=True)[:100]
        ]
        more = f"\n（共 {len(matched)} 个，只列出最近 100 个）" if len(matched) > 100 else ""
        model_text = f"匹配 {pattern} 的文件（沙箱里在 /workspace/files/ 下）：\n" + "\n".join(lines) + more
        # P1.6：列表结果结构化，便于前端时间线用「找到 N 个文件」产品文案
        return ToolValue(
            model_content=model_text,
            ui={
                "summary": f"找到 {len(matched)} 个文件",
                "detail": model_text[:500],
                "action": "glob",
                "pattern": pattern,
                "count": len(matched),
            },
        )

    async def _download(args: dict) -> str:
        """服务端下载 URL 字节落进「我的文件」。沙箱可按部署网络策略联网，本工具仍是只搬字节的稳定入口。

        安全约束（这条最容易做错）：本工具跑在 **agent-api 进程**里，而 agent-api 有网、
        有 docker.sock、有数据库凭证 —— 是整个系统最敏感的位置。所以：
        · **只搬字节，绝不执行、绝不解包**：解压一律交给沙箱里的 bash（解压炸弹在 agent-api
          侧炸就是打中要害）；
        · 落地路径强制收敛到文件区内（normalize_path 已压平并拒 ..）；
        · 大小上限 + **PinnedPublicTransport**：SSRF 必须逐跳校验并钉住 IP。只在发请求前
          调一次 `_url_allowed` 是**不够的**——`follow_redirects=True` 时 302 到
          169.254.169.254 / 127.0.0.1 / 内网 Java 不会再过校验，且预检与建连之间存在
          DNS rebinding 窗口。全仓其它出网点（image_fetch / mcp_client / tool_invoker /
          工作流 HTTP 节点）都走这个 transport，这里曾是唯一的例外。
        """
        import httpx

        from app.services.gateway.mcp_client import PinnedPublicTransport

        from .image_fetch import _IMG_EXTS, _resolve_pinned_target, _sniff_ext, _url_allowed

        raw_url = str(args.get("url") or "").strip()
        if not raw_url:
            raise ToolSoftError("请给出要下载的地址（url）。")
        referer = ""
        from_image_ref = False
        # "图3" / "[图3]"：search_web 图片目录的编号引用。模型看不到真实直链（只在
        # image_sink 里），所以这一步必须服务端解析——顺带杜绝编造 URL。
        if "://" not in raw_url:
            ref_match = re.search(r"图\s*(\d+)", raw_url)
            if ref_match:
                idx = int(ref_match.group(1)) - 1
                sink = image_sink or []
                if not (0 <= idx < len(sink)):
                    raise ToolSoftError(
                        f"图片引用 {raw_url} 无效（本轮 search_web 图片目录共 {len(sink)} 张）。"
                        "先用 search_web 搜图拿到 [图N] 目录，再按编号引用。")
                entry = sink[idx]
                raw_url = str(entry.get("url") or "")
                # referer=图片所在页面：CDN 防盗链场景下与浏览器行为一致
                referer = str(entry.get("source") or "")
                from_image_ref = True
            else:
                raw_url = "https://" + raw_url
        reason = await _url_allowed(raw_url)
        if reason:
            raise ToolSoftError(
                f"这个地址不允许下载（{reason}）。内网、本机和云元数据地址一律拒绝。")
        name = normalize_path(args.get("path") or _name_from_url(raw_url))

        # 同轮 URL 去重：同一地址已下过则直接复用首次落库文件。
        prev_name = _url_to_name.get(raw_url)
        if persist_downloads and prev_name:
            row_prev = await _find_visible_row(prev_name)
            if row_prev is not None:
                size_prev = int(row_prev.get("size") or row_prev.get("byteSize") or 0)
                _emit_file_meta("download_url", row_prev, _file_action("download", prev_name))
                return (
                    f"已复用本轮刚下载的 {prev_name}（{size_prev} 字节），未再次请求网络。"
                    f"沙箱路径：{_WORKSPACE_PREFIX}{prev_name}。"
                    "不要对同一地址重复 download_url。"
                )

        # 同名已存在则复用：断点续做时已有素材禁止重下覆盖拖时长。
        # 故意换内容时请换文件名；损坏需换图时改用另一 [图N]。
        existing = await _find_visible_row(name) if persist_downloads else None
        if existing is not None:
            size_ex = int(existing.get("size") or existing.get("byteSize") or 0)
            if size_ex > 0:
                _url_to_name[raw_url] = name
                _emit_file_meta("download_url", existing, _file_action("download", name))
                if tool_meta_sink is not None:
                    tool_meta_sink["download_url"]["urls"] = [raw_url]
                    tool_meta_sink["download_url"]["size"] = size_ex
                    tool_meta_sink["download_url"]["reused"] = True
                return (
                    f"已复用「我的文件」中已有的 {name}（{size_ex} 字节），**未重新下载**。"
                    f"沙箱路径：{_WORKSPACE_PREFIX}{name}。"
                    "断点续做/配图请直接用此文件；若该文件损坏，请换文件名或另一个 [图N]。"
                )

        limit = _download_limit()
        limit_mb = max(1, limit // 1024 // 1024)

        def _too_big(actual: Optional[int] = None) -> ToolSoftError:
            size_note = f"（约 {actual // 1024 // 1024}MB）" if actual else ""
            return ToolSoftError(
                f"这个文件{size_note}超过单文件上限 {limit_mb}MB，**没有下载也没有保存**"
                "——「我的文件」存不下比这更大的文件。换一个更小的分发："
                "只要其中一个文件就取它的 raw 直链，要仓库代码就找子目录归档或浅克隆的打包；"
                "确实需要整份大文件，请把地址给用户让他自己下。"
            )

        headers = {"Referer": referer} if referer else None
        try:
            async with httpx.AsyncClient(
                timeout=60, follow_redirects=True, max_redirects=5,
                transport=PinnedPublicTransport(resolver=_resolve_pinned_target),
            ) as client:
                async with client.stream("GET", raw_url, headers=headers) as resp:
                    if resp.status_code >= 400:
                        raise ToolSoftError(
                            f"下载失败：HTTP {resp.status_code}。确认地址是公开可访问的直链。")
                    # 重定向链的终点也要复检：transport 逐跳钉 IP，这里再核一次最终 host，
                    # 与 image_fetch 同口径（双保险，别省）。
                    final_reason = await _url_allowed(str(resp.url))
                    if final_reason:
                        raise ToolSoftError(
                            f"这个地址跳转到了不允许下载的位置（{final_reason}）。")
                    # Content-Length 早退：服务端已经说了多大就别下了。流式那道闸只能在
                    # 传够字节之后才发现——对一个 500MB 的归档意味着白等几分钟、白占内存。
                    # 头可能缺失或撒谎，所以下面那道闸照留（两道不是重复，是先后）。
                    try:
                        declared = int(resp.headers.get("content-length") or 0)
                    except (TypeError, ValueError):
                        declared = 0
                    if declared > limit:
                        raise _too_big(declared)
                    chunks = bytearray()
                    async for chunk in resp.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > limit:
                            raise _too_big()
                    data = bytes(chunks)
        except ToolSoftError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("download_url 失败 url=%s", raw_url[:200], exc_info=True)
            raise ToolSoftError(
                f"下载失败（{type(exc).__name__}）。确认地址可公开访问、且是直链而非网页。"
            ) from None
        if not data:
            raise ToolSoftError("下载到 0 字节，没有保存。")

        # 魔数校验（2026-07-27 补回）：旧 execute_in_sandbox.fetch_urls 一直有这一道（image_fetch 的
        # `_sniff_ext`），退休时没跟过来。系统提示词是**命令**模型用 download_url("图N") 给 PPT
        # 配图的，而防盗链页/403 页/需要登录的 HTML 都会带着 200 和一个 .jpg 结尾的 URL 回来
        # ——不校验就是把一段 HTML 存成 cover.jpg 再嵌进 PPT，用户看到的是一个裂图。
        # 只对**声称是图片**的下载做（[图N] 引用，或落地名就是图片后缀）：download_url 是通用
        # 搬字节工具，对 tar.gz/字体/数据集嗅探图片魔数毫无意义。
        # .svg 不在 _IMG_EXTS 里（它是 XML 文本，没有魔数），天然不会被误杀。
        if (from_image_ref or name.lower().endswith(_IMG_EXTS)) and _sniff_ext(data) is None:
            raise ToolSoftError(
                f"下载回来的不是图片字节（{name}，{len(data)} 字节，头部不匹配任何图片格式），"
                "**没有保存**——最常见的是防盗链页、错误页或需要登录的 HTML。"
                "换一张：从 search_web 的图片目录里另选一个 [图N]，不要给同一个地址重试。"
            )

        if not persist_downloads:
            from app.services.sandbox.sandbox_executor import execute_in_sandbox

            result = await execute_in_sandbox(
                "true",
                language="bash",
                input_files={name: data},
                run_id=run_id,
                collect_workspace=False,
            )
            if not result.ok:
                raise ToolSoftError("下载已完成，但写入临时沙箱失败。")
            _url_to_name[raw_url] = name
            return (
                f"已下载 {name}（{len(data)} 字节）到临时沙箱："
                f"/workspace/inputs/{name}。未写入用户文件区。"
            )

        row = await _find_visible_row(name)
        # 落库失败必须归成**软错误**：不包的话 UserFileError(413/配额满) 会一路冒到循环层
        # 变成一句通用「工具执行失败」，模型看不出是大小还是配额，只会原样重试同一个地址。
        # 与同文件 `_atomic_write` 的口径一致（那里一直是包的，这条路径漏了）。
        try:
            if row is not None:
                _require_target(str(row["id"]), name)
                saved = await user_file_service.overwrite_file(
                    user_id, str(row["id"]), data, filename=name,
                    thread_id=thread_id, run_id=run_id, change_summary="download_url 覆盖",
                    # 与 _atomic_write 同口径：原位修改场景下覆盖也要走乐观锁，否则「下载覆盖」
                    # 成了绕过锁的后门。覆盖成功后同步刷新 guard，不然紧接着的 edit_file 会吃到
                    # 一个由它自己造成的 409。
                    expected_sha256=(revision_guard["sha256"]
                                     if str(row["id"]) == revision_id else None),
                )
                if str(row["id"]) == revision_id:
                    revision_guard["sha256"] = hashlib.sha256(data).hexdigest()
                verb = "已覆盖"
            else:
                if revision_id:
                    raise ToolSoftError(
                        f"本轮只授权原位修改《{revision_name or revision_id}》，不能下载新文件。")
                # source=material（2026-07-27）：下载回来的是**材料**，不是本轮的产出。
                # 真机事故：用户只说「扫描一下这个 GitHub 仓库」，模型逐个取回 16 个源文件，
                # 文件区一次多出 main.tsx / package.json / README.md / requirements.txt……
                # 后两个是文档格式，只按后缀判拦不住，必须靠来源这条轴。字节照常落库
                # （沙箱可按策略联网，模型也可在沙箱内读取），只是不进用户的交付清单。
                saved = await user_file_service.save_file(
                    user_id, name, data, source=deliverable.MATERIAL_SOURCE,
                    thread_id=thread_id, run_id=run_id)
                verb = "已保存"
        except user_file_service.UserFileError as e:
            raise ToolSoftError(
                f"字节下载到了，但存不进「我的文件」：{e}。"
                f"（单文件上限 {limit_mb}MB，另有文件数和总配额上限。）"
                "换一个更小的文件，或请用户先清理文件区——重试同一个地址不会有不同结果。"
            ) from None
        _url_to_name[raw_url] = name
        _emit_file_meta("download_url", saved, _file_action("download", name))
        if tool_meta_sink is not None:
            tool_meta_sink["download_url"]["urls"] = [raw_url]
            tool_meta_sink["download_url"]["size"] = len(data)
        return (
            f"{verb} {name}（{len(data)} 字节），沙箱内路径：{_WORKSPACE_PREFIX}{name}。\n"
            "下载回来的文件按**材料**存放：你随时读得到，但它不会出现在用户的文件清单里"
            "（用户要的是你的产出，不是你取来的原料）。"
            f"如果这份文件本身就是用户要的交付物，用 bash 复制一份成正式产物："
            f"`cp {_WORKSPACE_PREFIX}{name} {_WORKSPACE_PREFIX}<给用户的文件名>`。\n"
            "注意：只下载了字节，**没有解包也没有执行**。用 bash 解压，"
            "且**解到 /workspace/tmp 而不是 files/**（否则上百个中间文件会塞进用户文件区）：\n"
            f"`mkdir -p /workspace/tmp && tar xf /workspace/files/{name} -C /workspace/tmp`"
            f"（zip 用 `unzip /workspace/files/{name} -d /workspace/tmp`），"
            "然后在 /workspace/tmp 里 grep/find 遍历。"
        )

    # 这段进每个文件工具的描述。第二句是真机踩出来的：模型 write_file 存了脚本，
    # 下一步 bash 跑 `python3 generate_ppt.py` 却「找不到」——因为 bash 的 cwd 是
    # /workspace，文件在 /workspace/files/ 下。不把这条写死，模型每次都要猜一遍。
    common = (
        "路径写法三种都接受：`a.txt`、`files/a.txt`、`/workspace/files/a.txt`——就是 bash 里看到的同一批文件。\n"
        "**在 bash 里访问这些文件必须用 `/workspace/files/<名>`**（bash 的工作目录是 /workspace，"
        "不是 files/）。例如刚 write_file 存了 gen.py，要跑它就写 "
        "`python3 /workspace/files/gen.py`，或先 `cd /workspace/files && python3 gen.py`。"
    )
    if workspace_folder_id:
        common = (
            "当前工作文件夹已同步到 /workspace/files，bash 默认在这里执行。"
            "read_file/edit_file/write_file 接受相对文件名或 /workspace/files/<名>。"
            "临时脚本和下载参考素材请留在 /workspace/tmp；最终交付文件写到 /workspace/files。"
        )

    tools = [
        MainTool(
            name="glob",
            description=(
                f"按通配符列出{_visible_scope_label}（如 `*.xlsx`、`报告*`、`*` 列当前可见范围）。"
                + ("不会列出用户未选中的其它会话文件。" if scope_to_thread else "")
                + "**要动某个文件前先用它确认真实文件名，不要猜。**返回名字、大小、时间。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "通配符，如 *.docx；缺省 * 表示全部"},
                    "intent": dict(INTENT_PROP),
                },
                "required": [],
            },
            execute=_glob,
            public_action="核对可用文件",
            output_model=ToolValue,
            readonly=True,
            parallel_safe=True,
            semantic_tags=("workspace_read",),
            allowed_execution_profiles=("interactive",),
        ),
        MainTool(
            name="read_file",
            description=(
                "按路径读文件内容。" + common + "\n"
                "pdf/docx/xlsx/pptx/图片会自动解析成文字（图片走 OCR），不会给你乱码。"
                "大文件可用 offset/limit 按行分段读，避免一次拉爆上下文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径或文件名"},
                    "offset": {"type": "integer", "description": "从第几行开始（0 起，可选）"},
                    "limit": {"type": "integer", "description": "最多读多少行（可选）"},
                    "intent": dict(INTENT_PROP),
                },
                "required": ["path"],
            },
            execute=_read,
            public_action="读取目标文件",
            output_model=ToolValue,
            readonly=True,
            parallel_safe=True,
            semantic_tags=("workspace_read",),
            allowed_execution_profiles=("interactive",),
        ),
        MainTool(
            name="download_url",
            description=(
                "把一个公开可访问的 URL 下载到用户「我的文件」（沙箱也可按部署网络策略联网；本工具适合只搬字节）。"
                "**只搬字节，不解包、不执行。**\n"
                "典型用法——要看一个代码仓库的全部代码时**不要逐页抓网页**（那要几十次调用）："
                "先 download_url 取它的归档（如 GitHub 的 codeload tar.gz 直链），"
                "再用 bash `tar xf` / `unzip` 解开、`grep -rn` 遍历。快两个数量级。\n"
                "也用于把素材嵌进**文件产物**（PPT/文档配图、字体、数据集、技能包需要的文件）。**不要**用它给对话附图：用户只要在聊天里看图时，search_web 后正文写 [图N] 即可，再 download 只会把文件区堆满冗余副本。"
                "\n**复用优先**：若「我的文件」已有同名文件，本工具直接复用、不重新下载；同一 URL 本轮只下一次。断点续做必须沿用已有素材。"
                "只接受公开直链：需要登录的、内网地址一律失败。\n"
                f"**单文件上限 {_download_limit() // 1024 // 1024}MB**（就是「我的文件」的单文件上限，"
                "超过会在开始下载前直接失败）——大仓库请改取子目录归档或单文件 raw 直链。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "公开可访问的直链地址"},
                    "path": {"type": "string", "description": "保存成什么文件名（可选，默认取 URL 末段）"},
                    "intent": dict(INTENT_PROP),
                },
                "required": ["url"],
            },
            execute=text_tool_body(_download),
            public_action="下载外部文件",
            output_model=ToolValue,
            readonly=False,
            parallel_safe=False,
            effect_scope="user_files" if persist_downloads else "scratch",
            idempotent=False,
            resource_locks=("user-files",) if persist_downloads else ("sandbox",),
            semantic_tags=("download", "-revision_mutation"),
            allowed_execution_profiles=("interactive",),
        ),
        MainTool(
            name="write_file",
            description=(
                "按路径写文本文件：不存在就新建，已存在就**整体覆写**（进版本历史，可回滚）。"
                + common + "\n"
                "写完立即保存到用户「我的文件」，不需要额外的保存步骤。"
                "只改文件里某一处时用 edit_file，不要整体覆写（覆写会丢掉你没读到的内容）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径或文件名"},
                    "content": {"type": "string", "description": "完整文件内容"},
                    "intent": dict(INTENT_PROP),
                },
                "required": ["path", "content"],
            },
            execute=_write,
            public_action="写入交付文件",
            output_model=ToolValue,
            readonly=False,
            parallel_safe=False,
            capability="artifact.export",
            effect_scope="user_files",
            idempotent=False,
            resource_locks=("user-files",),
            semantic_tags=("file_write",),
            allowed_execution_profiles=("interactive",),
        ),
        MainTool(
            name="edit_file",
            description=(
                "精确替换文件里的一段文本。" + common + "\n"
                "**old_string 必须在文件里唯一出现**：找不到会报错，出现多次也会报错并要求你加长上下文——"
                "不会悄悄改错地方。改之前先 read_file 看清原文（空格、缩进、换行都要一字不差）。"
                "二进制/富格式（docx/xlsx/pptx/pdf）不能用它，改那些用 bash 配合 python 库。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径或文件名"},
                    "old_string": {"type": "string", "description": "要被替换的原文，必须唯一"},
                    "new_string": {"type": "string", "description": "替换成的新文本（可为空=删除）"},
                    "intent": dict(INTENT_PROP),
                },
                "required": ["path", "old_string", "new_string"],
            },
            execute=_edit,
            public_action="原位修改文件",
            output_model=ToolValue,
            readonly=False,
            parallel_safe=False,
            effect_scope="user_files",
            idempotent=False,
            resource_locks=("user-files",),
            semantic_tags=("file_write", "revision_targeted"),
            allowed_execution_profiles=("interactive",),
        ),
        ]
    # 遗留 file_id 版 edit_file/update_file/create_file 的两条语义（revision_target 授权 +
    # sha256 乐观锁）已在上面移植完毕。interactive 轮全部注册；PPT 工程轮由 shell.py
    # 提供同名工具（写沙箱工程，不是「我的文件」），此处必须让位，否则 Harness 会因重名炸掉。
    from app.services.skills.ppt_agentic_adapter import is_agentic_ppt_profile
    if is_agentic_ppt_profile(execution_profile):
        tools = [tool for tool in tools if tool.name not in {
            "glob", "read_file", "write_file", "edit_file",
        }]
    return tools
