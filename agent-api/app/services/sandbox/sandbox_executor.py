"""Internal sandbox execution kernel used by the model-visible ``bash`` tool.

把「一段代码 + 可选输入文件」在沙箱里跑完，取回 stdout/stderr/产物清单。与 provider 无关——
用 create_configured_sandbox() 按 SKILL_SANDBOX_PROVIDER 选 local(本地测试)/opensandbox(生产)。

约定（写进主对话系统提示）：
- 代码写入 /workspace/__main__.py 执行；输入文件在 /workspace/inputs/<name>；
- 交付物写 /workspace/files/（统一文件系统，写进去就是已保存）。
  /workspace/outputs 是 execute_in_sandbox 时代的产物目录，现成技能里仍到处硬编码它——`_mkdir_cmd`
  照旧创建它（不建的话那些技能直接报错），但 bash 这条路 `collect_outputs=False`，
  改由 `migrate_outputs` 把里面的文件搬进 files/ 再走落库，见 `_migrate_outputs`。

沙箱生命周期（2026-07-22 改）：默认按 **Run 复用**——同一作用域（主对话一个 run_id /
任务模式一个 graph 节点）内多次调用共用同一个容器，/workspace 里的中间产物保留，模型可以
分步做、只重跑失败的那一步。见 session_pool。复用带来的两个必须处理的后果就在本文件里：
- outputs/ 会残留上次调用的文件 → 执行前打基线快照，**只收本次新增/变更**的文件，
  否则上次的产物会被当成本次产物重收重审，交付判定也会假阳性；
- 同一会话内两次调用不能并行（会互相覆盖 __main__.py）→ 由 session.lock 串行化。
关掉 SANDBOX_SESSION_REUSE_ENABLED 即回到「每次全新沙箱、用完即弃」的旧行为。

本模块不注册模型工具，也不依赖对话、计划或 UI。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.core.config import settings

from . import session_pool
from .base import (
    ExecuteOptions,
    FileWriteEntry,
    SandboxError,
    SandboxNotSupported,
    SandboxUnavailable,
)
from .factory import create_configured_sandbox

logger = logging.getLogger(__name__)

_ENTRY = "/workspace/__main__.py"
_ENTRY_SH = "/workspace/__main__.sh"

# 语言 → (脚本落地路径, 执行命令)。bash 复用 execute_in_sandbox 的整套生命周期（并发闸/会话复用/
# mkdir/写文件/执行/产物收集/错误指纹止损），只换入口，不再抄一份。
#
# ⚠️ bash 必须显式调 bash，不能靠 execute 默认的外层 `sh -lc`：实测沙箱镜像里
# /bin/sh → /usr/bin/dash（bash 5.2 确实装了但不走它）。让 .sh 在 dash 下跑，
# 现成技能脚本里只要有 [[ ]]、数组、set -o pipefail 就报莫名其妙的语法错。
#
# python 侧 -u 必须保留：非 TTY 下 Python stdout 块缓冲，print 全憋到进程结束一次性吐出，
# 逐页直播会退化成「结尾齐发」（2026-07-20 实测）。shell 不存在这个问题，故 bash 不加。
_LANG_SPEC: dict = {
    "python": (_ENTRY, f"python -u {_ENTRY}"),
    "bash": (_ENTRY_SH, f"bash {_ENTRY_SH}"),
}
_OUTPUTS = "/workspace/outputs"
# 统一文件系统（2026-07-27）：用户「我的文件」在沙箱里的镜像。与 inputs/outputs 的区别是
# **双向**——会话开始同步进来，执行后扫变更同步回去落库，写进去就是已保存。
# 为什么是「同步」而不是 bind mount：默认 provider 是远程沙箱（opensandbox/K8s），与 agent-api
# 不共享文件系统，挂不了；所有东西进沙箱本来就都是拷字节（write_files），这里沿用同一条通道，
# 因此 local/opensandbox/e2b/sealos 四个 provider 全都不用改适配器。
_FILES = "/workspace/files"
# 会触发产物质检的后缀（批 3：文件写入事件驱动）。纯文本/代码不检——检了也只会误报。
_REVIEWABLE_EXTS = (".pptx", ".docx", ".xlsx", ".pdf", ".html", ".htm")
_INPUTS = "/workspace/inputs"
_SKILLS = "/workspace/skills"
# 中间产物区：**不**参与落库扫描。解包、构建、生成器脚本都该落在这里，只有最终交付物
# 进 files/。多处文案在引导模型用它，所以必须由 _mkdir_cmd 保证存在。
_TMP = "/workspace/tmp"

ProgressCallback = Callable[[str, str, Optional[dict]], Awaitable[None] | None]


async def _progress(
    callback: Optional[ProgressCallback], stage: str, label: str, detail: Optional[dict] = None
) -> None:
    """向上层报告可验证的沙箱阶段。

    这里故意不报虚构百分比：容器提供方只能告知当前正在哪个阶段，
    脚本内部的 PPT 页数/完成度在执行结束前并不可知。
    """
    if callback is None:
        return
    result = callback(stage, label, detail)
    if inspect.isawaitable(result):
        await result


# ---- 逐页产物直播（2026-07-20，对标 Manus 逐页预览）：生成脚本每写完一页在 stdout 打标记：
#   @@PPT_PAGE@@{"index":1,"total":7,"title":"封面"}\n<svg …>…</svg>\n@@PPT_PAGE_END@@
# sandbox_executor 流式解析后经 progress_callback(stage="artifact_page") 直播给前端逐页渲染。
PAGE_MARK = b"@@PPT_PAGE@@"
PAGE_MARK_END = b"@@PPT_PAGE_END@@"
_PAGE_SVG_MAX = 400_000     # 单页 SVG 字符上限，超限丢弃该页（不断流）
_PAGE_MAX_COUNT = 60        # 单次执行最多直播的页数
_PAGE_BUF_MAX = 2_000_000   # 解析缓冲上限：防脚本疯狂打印吃内存
_PAGE_BLOCK_RE = re.compile(r"@@PPT_PAGE@@.*?@@PPT_PAGE_END@@\s*", re.S)


class _PageStreamParser:
    """从流式 stdout 里解析逐页标记。单读线程顺序 feed，无并发；异常在 feed 内自吞。"""

    def __init__(self, emit):
        self._buf = bytearray()
        self._emit = emit
        self._count = 0

    def feed(self, chunk: bytes) -> None:
        if self._count >= _PAGE_MAX_COUNT:
            return
        self._buf.extend(chunk)
        while True:
            start = self._buf.find(PAGE_MARK)
            if start < 0:
                # 无起始标记：只留个尾巴防标记被块边界劈断，其余丢弃
                if len(self._buf) > len(PAGE_MARK):
                    del self._buf[: -len(PAGE_MARK)]
                return
            nl = self._buf.find(b"\n", start)
            if nl < 0:
                return
            end = self._buf.find(PAGE_MARK_END, nl)
            if end < 0:
                # 页体未到齐：清掉标记前的无关输出；页体本身超限则放弃这页重新同步
                if start > 0:
                    del self._buf[:start]
                if len(self._buf) > _PAGE_BUF_MAX:
                    del self._buf[: len(PAGE_MARK)]
                return
            meta_raw = bytes(self._buf[start + len(PAGE_MARK): nl])
            body = bytes(self._buf[nl + 1: end])
            del self._buf[: end + len(PAGE_MARK_END)]
            try:
                meta = json.loads(meta_raw.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                continue
            svg = body.decode("utf-8", errors="replace").strip()
            if not svg or len(svg) > _PAGE_SVG_MAX:
                continue
            self._count += 1
            try:
                self._emit(meta, svg)
            except Exception:  # noqa: BLE001
                logger.warning("逐页预览 emit 失败（忽略继续）", exc_info=True)


def _safe_rel(rel: str) -> str:
    """清洗技能包内相对路径，防穿越（复用蓝本约束：去 .. 与前导斜杠）。"""
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p and p != "." and p != ".."]
    return "/".join(parts)


def _build_entries(
    code: str,
    input_files: Optional[dict],
    skill_packages: Optional[list],
    skip_slugs: set,
    entry: str = _ENTRY,
    workspace_files: Optional[dict] = None,
) -> tuple[list, list]:
    """组装本次要写进容器的文件清单，返回 (entries, 本次新写入的技能 slug)。

    skip_slugs：复用会话里已经传过的技能包，跳过不重传（新建容器时传空集合即全量重传）。
    entry：脚本落地路径——python 走 __main__.py，bash 走 __main__.sh（见 _LANG_SPEC）。
    """
    # CRLF 归一：bash 对 \r 零容忍，一个 CRLF 脚本报的是
    # `line 7: syntax error: unexpected end of file`（而 if/fi 明明配对完整），
    # 错误信息与真实原因毫无关系，模型只能瞎改。模型贴 Windows 风格脚本时很容易踩。
    entries = [FileWriteEntry(path=entry, data=code.replace("\r\n", "\n").encode("utf-8"))]
    # 「我的文件」镜像：相对路径原样保留（支持子目录），只清洗穿越
    for rel, data in (workspace_files or {}).items():
        rel_safe = _safe_rel(str(rel))
        if not rel_safe:
            continue
        blob = data if isinstance(data, bytes) else str(data).encode("utf-8")
        entries.append(FileWriteEntry(path=f"{_FILES}/{rel_safe}", data=blob))
    for name, data in (input_files or {}).items():
        safe = str(name).replace("/", "_")
        blob = data if isinstance(data, bytes) else str(data).encode("utf-8")
        entries.append(FileWriteEntry(path=f"{_INPUTS}/{safe}", data=blob))
    # 技能包文件树挂进 /workspace/skills/<slug>/（供模型 import/执行其脚本）
    fresh_slugs: list = []
    for pkg in skill_packages or []:
        if not (pkg.get("files") or {}):
            # 取包失败的占位记录（skill_package_bridge 的 unavailable=True）也会走到这里。
            # 必须在算 slug 之前跳过：否则它的 slug 会进 fresh_slugs → session.written_skills，
            # 同一 Run 里后续真取到包的那次会被 skip_slugs 整包跳过，永远挂不上。
            continue
        slug = _safe_rel(str(pkg.get("slug") or pkg.get("skillId") or "skill")).replace("/", "_") or "skill"
        if slug in skip_slugs:
            continue
        fresh_slugs.append(slug)
        for rel, data in (pkg.get("files") or {}).items():
            rel_safe = _safe_rel(rel)
            if not rel_safe:
                continue
            blob = data if isinstance(data, bytes) else str(data).encode("utf-8")
            entries.append(FileWriteEntry(path=f"{_SKILLS}/{slug}/{rel_safe}", data=blob))
    return entries, fresh_slugs


async def _load_workspace(loader, mirror: dict) -> tuple[Optional[dict], Optional[dict]]:
    """调 workspace_loader 拿 (要写的文件, 执行前应有的完整镜像摘要)。

    失败**不抛**：文件区同步从来不该让整个工具调用失败（与 WorkspaceSync.prepare 同口径）。
    降级成「本次不镜像任何文件」而不是「按旧记账省掉写入」——后者会让容器里缺文件却无人知晓。
    """
    try:
        loaded = await loader(dict(mirror or {})) or {}
    except Exception:  # noqa: BLE001
        logger.warning("文件区增量镜像加载失败（本次不同步文件）", exc_info=True)
        return {}, {}
    return dict(loaded.get("files") or {}), dict(loaded.get("stamps") or {})


def _needs_pptd_preflight(skill_packages: Optional[list]) -> bool:
    """Whether the mounted package is the first-party PPTD Skill.

    Third-party presentation Skills own their runtime and are deliberately not
    forced through the PPTD/LibreOffice contract.  The first-party package is
    the only package for which this platform can state an exact preflight.
    """
    try:
        from app.services.skills.skill_package_bridge import is_first_party_ppt_studio
    except Exception:  # noqa: BLE001
        return False
    return any(
        is_first_party_ppt_studio(
            (pkg or {}).get("name") or (pkg or {}).get("skillId")
        )
        for pkg in (skill_packages or [])
        if isinstance(pkg, dict)
    )


async def _pptd_runtime_preflight(sandbox, skill_packages: Optional[list]) -> Optional[str]:  # noqa: ANN001
    """Run the first-party presentation capability check once before authoring."""
    if not _needs_pptd_preflight(skill_packages):
        return None
    wasm_paths = []
    for pkg in skill_packages or []:
        if not isinstance(pkg, dict) or not _needs_pptd_preflight([pkg]):
            continue
        slug = _safe_rel(str(pkg.get("slug") or pkg.get("skillId") or "skill")).replace("/", "_")
        if slug:
            wasm_paths.append(f"/workspace/skills/{slug}/scripts/local-export/pptd_wasm_bg.wasm")
    # Same fallback as run_export.py/export_pptx.py. The API host need not carry
    # a second copy when the configured sandbox image already provides WASM.
    wasm_paths.append("/opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm")
    script = (
        "import importlib.util,json,os,shutil;"
        "missing=[];"
        "missing += ['Node.js'] if not shutil.which('node') else [];"
        "missing += ['LibreOffice'] if not (shutil.which('libreoffice') or shutil.which('soffice')) else [];"
        "missing += ['Poppler'] if not shutil.which('pdftoppm') else [];"
        "missing += ['Python-Office-libraries'] if any(importlib.util.find_spec(x) is None "
        "for x in ('docx','openpyxl','pptx','pypdf')) else [];"
        f"missing += ['PPTD-WASM'] if not any(os.path.isfile(p) and os.path.getsize(p)>0 "
        f"for p in {wasm_paths!r}) else [];"
        "print(' '.join(missing))"
    )
    command = f"python3 -c {shlex.quote(script)}"
    result = await sandbox.execute(command, ExecuteOptions(timeout_ms=15_000))
    missing = (result.stdout or "").strip()
    if not result.ok and not missing:
        return "PPTD 运行环境自检失败，无法确认 Node.js、LibreOffice、Office 库和 PPTD WASM 是否可用"
    if missing:
        return (
            "PPTD 运行环境能力缺口：缺少 " + missing +
            "。请先由部署环境补齐组件并重建沙箱镜像；本轮不会反复探测或重试。"
        )
    return ""


async def _prune_stale_mirror(sandbox, prev_mirror: dict, stamps: dict) -> list:  # noqa: ANN001
    """把容器 files/ 里「上次由我们镜像进去、这次记账里已经没有」的文件删掉（2026-07-29）。

    没有这一步时同步是**单向**的：`_load_workspace` 只回「要写的增量」，`write_files` 只写
    这些路径，而复用会话的 files/ 从不清理（`session.workspace_mirror` 只在容器判定失效重建
    时才清空）。于是同一个 Run 里用户在「我的文件」删掉一个文件后：
      ① 模型下一次 bash 照样读到它，据此作答——一个已经不存在的文件；
      ② 更糟的是模型顺手改写它 → 变更被扫出来回写，而落库层查不到对应 DB 行，判成「新文件」
         → 被删掉的文件以一个新 file_id **复活**进用户文件区。

    **只删 prev_mirror ∩ ¬stamps，不删「容器里有而 stamps 里没有」的全部文件**，这条边界是
    可证明的而不是拍脑袋：镜像记账的不变量是「容器里那份与库里那份逐字节相同」（键就是
    sha256，见 session_pool.SandboxSession.workspace_mirror），所以删掉一个记账内的文件永远
    不会丢失任何独有字节——它要么还在库里，要么正是用户主动删掉的那个。
    反过来，容器 files/ 里还有大量**从来不在记账里**的文件：模型自己写的生成器脚本（被
    workspace_sync._is_intermediate 判为中间产物、有意不落库）、0 字节产物、超过
    USER_FILES_MAX_SIZE_MB 没读回的、读回/落库失败的。它们同样"不在 stamps 里"，但删掉就是
    在 Run 中途毁掉模型自己刚做出来的东西。宁可漏删不可错删：漏删的表现是一个陈旧只读副本，
    错删的表现是工作成果凭空消失。
    同理，被上一次执行**改动过**的文件已经从记账里被剔除（改动未必落得了库），因此不在
    prev_mirror 里、这里也不会删——那份容器内容可能是模型唯一的产出。

    调用方另有两条约束（写在这里免得被挪走）：
    - `stamps` 为空时**一律不调**。loader 的失败分支（枚举文件区抛错 / 读字节失败 / 关开关）
      与「用户真的一个文件都没有」返回的是同一个空 dict，分不开；拿一次数据库抖动当成
      「用户清空了文件区」去删容器，代价远大于多留一轮幽灵文件。
    - 必须排在 `files_baseline` 之前。基线在删除**之后**拍，删掉的文件才不会被算成
      「本次执行删了文件」→ 否则回执会向模型报一串它根本没碰过的 workspace_deleted。

    失败不抛：幽灵文件是错，但不值得让整个工具调用失败。返回真正发出删除的相对路径。
    """
    stale = sorted(set(prev_mirror or {}) - set(stamps or {}))
    # 路径拼法必须与 _build_entries 的写入分支**逐字一致**（同一个 _safe_rel + 同一个前缀）：
    # 只有这样「删的就是当初写进去的那一个」才是可证明的，而不是靠两边碰巧长得一样。
    paths = [f"{_FILES}/{r}" for r in (_safe_rel(rel) for rel in stale) if r]
    if not paths:
        return []
    removed: list = []
    # 分批：`rm` 的参数总长受 ARGV 上限约束（文件区上限 200 个 + 子目录路径够得着）。
    # `-f` 不报「文件不存在」（记账与容器可能已不同步，那不是错）；**绝不用 `-r`**——
    # 镜像项都是文件，加上 -r 只会让一次路径异常有机会掀掉整棵子目录。
    for start in range(0, len(paths), 60):
        batch = paths[start:start + 60]
        cmd = "rm -f -- " + " ".join(shlex.quote(p) for p in batch)
        try:
            res = await sandbox.execute(cmd, ExecuteOptions(timeout_ms=15000))
        except (SandboxError, SandboxUnavailable):
            logger.warning("清理容器内已删除的镜像文件失败（本次保留幽灵文件）", exc_info=True)
            break
        if not getattr(res, "ok", False):
            logger.warning("清理容器内已删除的镜像文件失败 rc=%s：%s",
                           getattr(res, "exit_code", "?"), (getattr(res, "stderr", "") or "")[:200])
            continue
        removed.extend(batch)
    if removed:
        # 这条 info 是「用户删了文件、容器也跟着删了」的唯一线索：模型回执里不提这件事
        # （它读不到的文件本来就不该出现在回执里），排障时只能靠日志对上时间点。
        logger.info("已清理容器内 %d 个不在文件区记账里的镜像文件：%s",
                    len(removed), [p[len(_FILES) + 1:] for p in removed[:5]])
    return [p[len(_FILES) + 1:] for p in removed]


def _mkdir_cmd(entries: list) -> str:
    """建目录：inputs/outputs/files/tmp + 所有待写文件的父目录（put_archive/docker cp 要求父目录
    已存在，嵌套的 skills/<slug>/scripts/ 必须先 mkdir，否则写入 404）。失败即沙箱异常。

    `_TMP` 必须在内：工具描述、`persist_notice()` 和系统提示词三处都在引导模型
    「中间文件/解包/构建放 /workspace/tmp」，而这个目录以前根本不存在——模型照做就吃一个
    `No such file or directory`。引导指向不存在的路径比不引导更坏。
    """
    import posixpath
    dirs = {_INPUTS, _OUTPUTS, _FILES, _TMP}
    for e in entries:
        parent = posixpath.dirname(e.path)
        if parent and parent != "/workspace":
            dirs.add(parent)
    return "mkdir -p " + " ".join(shlex.quote(d) for d in sorted(dirs))

# 并发闸（ADR-047 §7.4）：全局最多同时存在的 execute_in_sandbox 沙箱数，防狂发 execute_in_sandbox 打满宿主机。
# 懒创建（需在事件循环内）；排队超时后明确失败，不无限等。
_sem: Optional[asyncio.Semaphore] = None
_SEM_ACQUIRE_TIMEOUT = 60  # 秒：排队等不到名额即失败（明确 > 静默挂起）


_sem_loop: Optional[object] = None   # _sem 绑定的事件循环（换循环必须重建，见 _get_semaphore）
_sem_limit: int = 0                  # _sem 建立时用的上限（自己记账，不去读 Semaphore 私有属性）


def _get_semaphore() -> Optional[asyncio.Semaphore]:
    global _sem, _sem_loop, _sem_limit
    limit = int(getattr(settings, "SKILL_SANDBOX_MAX_CONCURRENT", 0) or 0)
    if limit <= 0:
        return None  # 0=不限
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    # 三种情况都要重建：
    # ① 首次；
    # ② 事件循环换了 —— uvicorn --reload 和测试各自建 loop，绑在**已关闭**循环上的
    #    Semaphore 其等待者永远唤不醒，表现就是"永久沙箱繁忙"；
    # ③ 并发上限被改了配置 —— 否则一直按旧容量跑。
    #    上限用模块级 _sem_limit 记账：Semaphore 没有公开的"初始值"属性，读私有 _value
    #    拿到的是**当前剩余**而不是容量，会误判。
    if _sem is None or loop is not _sem_loop or limit != _sem_limit:
        _sem = asyncio.Semaphore(limit)
        _sem_loop = loop
        _sem_limit = limit
    return _sem


async def _acquire_with_timeout(sem: asyncio.Semaphore, timeout: float) -> bool:
    """带超时地取一个名额；超时返回 False，**保证不泄漏名额**。

    背景（2026-07-27 实测校正，别被旧说法误导）：本文件原注释担心「名额累计泄漏满 20 即
    永久沙箱繁忙直到重启」，怀疑对象是 `await asyncio.wait_for(sem.acquire(), T)` 那个经典
    竞态（超时取消与「内部已拿到名额」同时发生 → 调用方收到 TimeoutError 而名额已被拿走）。
    **实测在 Python 3.11 上复现不出来**：老写法与本实现各跑 200 轮 x 4 等待者的贴线竞态，
    累计泄漏均为 0 —— CPython 3.8+ 的 wait_for 会在取消后回收内部 future 的完成结果。

    保留这个显式实现的理由不是「修了个 bug」，而是：①不依赖 wait_for 的微妙语义，换 Python
    版本不会悄悄变行为；②把「超时不得泄漏名额」这条不变量交给
    tests/test_sandbox_semaphore_leak.py 锁住，而不是靠对运行时的信任。

    做法：wait + cancel + 兜底 release —— cancel 之后仍 await 一次那个 task，若它其实已经
    成功（cancel 来晚了），就把名额立刻还回去。
    """
    task = asyncio.ensure_future(sem.acquire())
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        # **外部取消**（用户点停止 / 断连 / 切会话 → _drive_tool_call 的 task.cancel()）。
        # asyncio.wait 被取消时**不会**取消传进去的 task（只有 wait_for 会），那个 acquire()
        # 会继续排队、稍后拿到名额，而 execute_in_sandbox 的 try/finally 是在拿到名额之后才开始的
        # （acquired 还是 False）→ 名额永不归还。累计 20 次就永久"沙箱繁忙"直到重启。
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            sem.release()   # 取消来晚了，名额已到手，还回去
        raise
    if task in done:
        exc = task.exception()
        if exc is not None:  # 极少见：等待期间信号量本身出错，当作没拿到
            logger.warning("沙箱并发闸获取异常: %s", exc)
            return False
        return True

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return False  # 正常路径：确实没拿到
    except Exception:  # noqa: BLE001
        return False
    # cancel 来晚了——名额其实已经到手，必须还回去，否则就是那个泄漏
    sem.release()
    logger.info("沙箱并发闸：超时与获取竞态，已归还名额（未泄漏）")
    return False


# 对外稳定名字：沙箱名额闸只保留这一份实现（2026-07-28 的 bug 正来自两处各抄一份：
# 缺了外部取消（用户点停止）路径的兜底 release，名额累计泄漏满即永久「沙箱繁忙」）。
acquire_with_timeout = _acquire_with_timeout


@dataclass
class SandboxExecutionResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    # [{"name","size"}]；fetch_output_bytes=True 时附 "content": bytes（超上限的标 "skipped"）；
    # 审查开启时附 "review": {"status","summary"}
    output_files: list = field(default_factory=list)
    truncated: bool = False
    timed_out: bool = False
    termination_reason: Optional[str] = None
    # 统一文件系统：本次在 /workspace/files 下新建或改动过的文件 [{"path","data"}]，
    # 由调用方落库（sandbox_executor 不碰 DB）。未开 collect_workspace 时恒为空。
    workspace_changes: list = field(default_factory=list)
    # 因超过单文件大小上限而**没有读回**的 files/ 变更（相对路径）。不报给模型就是
    # 「命令成功 + 文件没进我的文件 + 回执一个字不提」——最坏的那种沉默。
    workspace_oversized: list = field(default_factory=list)
    # 执行前在 files/ 里、执行后不在了的相对路径（删除或改名）。**这些改动不会回写**
    # （回写只认执行后清单），所以要如实告诉模型，别让它向用户谎报"已删除/已改名"。
    workspace_deleted: list = field(default_factory=list)
    # 从 outputs/ 搬进 files/ 的文件名（migrate_outputs=True 时）。搬过来的会顺着 files/
    # 链路正常落库；名字本身仍要报给模型，让它改掉写 outputs/ 的习惯。
    outputs_migrated: list = field(default_factory=list)
    # outputs/ 里因 files/ 已有同名文件而**没有搬**的文件名——不覆盖用户既有文件，
    # 但必须报上去，否则又是一次"写了、没交付、回执不提"。
    outputs_conflicts: list = field(default_factory=list)
    error: Optional[str] = None  # 沙箱层错误（不可用/写文件失败），区别于用户代码 stderr
    review: Optional[dict] = None  # 产物审查总体结论 {"status": passed/warning/failed/unknown}
    reused: bool = False  # 本次复用了本任务已有沙箱（/workspace 保留着上次的文件）
    job_id: Optional[str] = None
    # outputs/ 里本次未改动、沿用上次调用的文件名（只作回执提示，不重复保存）
    unchanged_outputs: list = field(default_factory=list)

    def to_tool_text(self) -> str:
        """给模型看的紧凑回执（工具循环内可据此自修）。"""
        if self.error:
            return f"[沙箱执行失败] {self.error}"
        parts = [f"exit_code={self.exit_code}"]
        if self.job_id:
            parts.append(f"job_id={self.job_id}")
        if self.termination_reason:
            parts.append(f"termination_reason={self.termination_reason}")
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        if self.output_files:
            names = ", ".join(f["name"] for f in self.output_files)
            parts.append(f"产物文件（/workspace/outputs）: {names}")
        if self.unchanged_outputs:
            # 复用沙箱时 outputs/ 里还留着上次调用的文件：明确告诉模型它们在、但本次没改动，
            # 免得模型看到「产物文件」里没有它们就以为丢了而重新生成一遍。
            parts.append(
                "（/workspace/outputs 中本次未改动的既有文件：{}——仍在沙箱里，已交付过，不必重做）".format(
                    ", ".join(self.unchanged_outputs)
                )
            )
        reviewed = [f for f in self.output_files if f.get("review")]
        if reviewed:
            # 查错语义（2026-07-15 拍板）：回执只把「确实的错误」表述为需要修复；
            # unknown=检查缺席不诱导返工；审美/专业度建议降级为参考信息。
            _mark = {"passed": "✓ 通过", "warning": "△ 通过（有提醒）",
                     "failed": "✗ 未通过（存在需修正的错误）",
                     "unknown": "? 检查未完成（已正常保存，不需要返工）"}
            lines = []
            for f in reviewed:
                r = f["review"]
                detail = f"：{r['summary']}" if r.get("summary") else ""
                lines.append(f"- 《{f['name']}》{_mark.get(r['status'], r['status'])}{detail}")
                quality = r.get("quality") or {}
                if quality and r.get("status") == "failed":
                    # 只在确有错误时给出定向修复清单：error 级问题 + missing 要求
                    errors = [
                        i for i in quality.get("issues") or []
                        if i.get("severity") == "error"
                    ]
                    for item in errors[:5]:
                        fix = f"；修复：{item.get('fix')}" if item.get("fix") else ""
                        lines.append(f"  错误：{item.get('message') or '未描述'}{fix}")
                    missing = [
                        item for item in quality.get("requirement_checks") or []
                        if item.get("status") == "missing"
                    ]
                    for item in missing[:5]:
                        fix = f"；修复：{item.get('fix')}" if item.get("fix") else ""
                        lines.append(
                            f"  要求未实现：{item.get('requirement') or '未命名要求'}{fix}"
                        )
                elif quality:
                    # 非拦截性建议（审美/排版偏好）仅供下次生成参考，不要求返工
                    warns = [i.get("message") for i in quality.get("issues") or [] if i.get("message")]
                    if warns:
                        lines.append("  参考建议（不要求返工）：" + "；".join(str(x) for x in warns[:3]))
            parts.append("产物检查（结构校验 + 渲染查错）：\n" + "\n".join(lines))
            if (self.review or {}).get("status") == "failed":
                parts.append(
                    "有产物存在需修正的错误：请**只修复上面列出的具体错误**，其余内容保持原样，"
                    "用**相同文件名**通过 bash 重新生成（坏版本已存为草稿，修复通过后自动转正）。"
                    "修复完成前不要声称错误已解决。"
                )
        if self.truncated:
            parts.append("（输出已按上限截断）")
        return "\n".join(parts)


async def execute_in_sandbox(
    code: str,
    *,
    language: str = "python",
    input_files: Optional[dict] = None,
    timeout_ms: Optional[int] = None,
    collect_outputs: bool = True,
    fetch_output_bytes: bool = False,
    skill_packages: Optional[list] = None,
    # 统一文件系统：{相对路径: bytes} —— 会话开始时把用户文件区镜像进 /workspace/files。
    workspace_files: Optional[dict] = None,
    # 增量镜像（2026-07-28）：给出这个回调时**不要**再传 workspace_files —— 本内核会在拿到
    # 会话锁之后、用容器当前的镜像记账（{相对路径: sha256}）回调它，由它只回「真需要写进去
    # 的那几个文件」。为什么必须是回调而不是调用方先算好：记账挂在 session 上，而会话可能在
    # 调用方算完之后被丢弃（探活失败/池满驱逐）→ 调用方按旧记账省掉的写入，在新容器里就是
    # 凭空缺文件。回调在锁内执行，看到的记账一定是这个容器的。
    # 契约：`await workspace_loader(mirror) -> {"files": {rel: bytes}, "stamps": {rel: sha256}}`
    #   files  = 本次要写进容器的（增量）
    #   stamps = 本次执行前**整个 files/ 镜像应有的**内容摘要（含被跳过的），用于回写记账
    workspace_loader: Optional[Callable[[dict], Awaitable[dict]]] = None,
    # True=执行后扫 files/ 变更并读回字节（放进 result.workspace_changes 交调用方落库）
    collect_workspace: bool = False,
    # True=执行后把 outputs/ 里的文件搬进 files/ 再扫变更（见 _migrate_outputs 的说明）。
    # 只在 collect_workspace 同时开启时有意义——搬过去是为了让它们走 files/ 的落库链路。
    migrate_outputs: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    task_brief: str = "",
    newapi_key: str = "",
    session_key: str = "",
    # 用户本轮选中的图片文件名（files/ 下），供 pptx 真图嵌入质检
    expected_user_images: Optional[list] = None,
    # PPT publish already has deterministic ZIP/page/layout/font/image gates.
    # Callers may skip the additional remote visual-model review while keeping
    # the structural output review enabled.
    run_visual_review: bool = True,
    background: bool = False,
    background_timeout_ms: Optional[int] = None,
    workspace_baseline: Optional[dict] = None,
) -> SandboxExecutionResult:
    """在沙箱里执行一段代码。input_files: {name: bytes|str}。

    session_key：沙箱复用作用域（主对话传 run_id，任务模式由节点 worker 经 contextvar 设定）。
    同一 key 的多次调用共用一个容器直到该 Run 结束；空值或复用关闭时退回「用完即弃」。

    skill_packages: [{"slug": str, "files": {相对路径: bytes}}]——选中技能包的文件树，
    执行前挂到 /workspace/skills/<slug>/，模型可在 code 里读取/import/subprocess 执行其脚本
    （ADR-047 §6.6：Skill 广场脚本进主对话沙箱）。同一容器内 skills/ 与 inputs/outputs 路径隔离。
    """
    spec = _LANG_SPEC.get(language)
    if spec is None:
        return SandboxExecutionResult(
            ok=False,
            error=f"仅支持 {'/'.join(sorted(_LANG_SPEC))}，收到 {language!r}",
        )
    entry_path, exec_command = spec

    # 并发闸：满员时排队，超时即失败（防 DoS）
    sem = _get_semaphore()
    acquired = False
    if sem is not None:
        if sem.locked():
            await _progress(progress_callback, "queued", "正在等待可用的沙箱")
        acquired = await _acquire_with_timeout(sem, _SEM_ACQUIRE_TIMEOUT)
        if not acquired:
            return SandboxExecutionResult(ok=False, error="沙箱繁忙（并发已满），请稍后重试")

    # sem.acquire() 成功后的一切都进统一 try/finally：用户点停止（create 期 CancelledError）
    # 或本地无 docker 的 FileNotFoundError 从 create() 逃逸时，finally 必达——清可能已
    # `docker run -d` 起来的孤儿容器 + 归还并发名额。否则名额累计泄漏满 20 即"沙箱繁忙"直到重启。
    sandbox = None
    session = None
    session_lock = None  # 已持有的会话锁（同会话内串行，防两次调用互相覆盖 __main__.py）
    reused = False
    try:
        # contextvar 优先：任务模式的节点 worker 会把作用域收窄到「本节点」（同图并行节点
        # 不能共用一个容器，会互相覆盖 __main__.py 并共享 outputs/）；主对话不设，退回 run_id。
        scope = session_pool.current_scope() or str(session_key or "")
        session = await session_pool.acquire(scope)
        if session is not None:
            await session.lock.acquire()
            if session.closed:
                # 等锁期间会话被丢弃（探活失败/池满驱逐/TTL 到点）：别用这具尸体，
                # 尤其不能沿用它「技能包已传」的记账——降级为一次性沙箱重新来。
                session.lock.release()
                session_pool.release(session)
                session = None
            else:
                session_lock = session.lock
                sandbox = session.sandbox
                reused = session.started
        await _progress(
            progress_callback, "creating",
            "正在复用本任务的执行环境" if reused else "正在创建安全执行环境",
        )
        if sandbox is None:
            sandbox = create_configured_sandbox("harness-bash")
        try:
            await sandbox.create()  # 各 adapter 幂等：已就绪的复用容器直接返回
        except (SandboxUnavailable, SandboxError) as e:
            logger.warning("Harness bash 沙箱不可用: %s", e)
            if session is not None:
                # 复用会话建不起来：摘掉它，别让后续调用一直撞同一个坏会话
                session_lock.release()
                session_lock = None
                await session_pool.discard(session)  # 已在此销毁，finally 不要再 delete 一次
                session, sandbox = None, None
            return SandboxExecutionResult(ok=False, error=f"沙箱不可用：{e}")
        if session is not None:
            session.started = True
        await _progress(
            progress_callback,
            "preparing",
            "正在准备脚本与输入文件",
            {"input_count": len(input_files or {}), "skill_count": len(skill_packages or [])},
        )
        # 复用沙箱时同一技能包只写一次：技能包动辄几 MB，每次调用重传纯属白等。
        skipped_slugs = set(session.written_skills) if session is not None else set()
        # 用户文件区同款增量（2026-07-28）：把容器当前的镜像记账交给 loader，只取回真要写的。
        # 记账为空（一次性沙箱 / 会话刚建 / 上次执行后作废）时 loader 自然回全量，等价旧行为。
        workspace_stamps: Optional[dict] = None
        # 交给 loader 之前先留一份**上次的**记账：下面要靠它算出「上次镜像进去、这次记账里
        # 已经没有」的那批文件并从容器里删掉（见 _prune_stale_mirror）。
        # session.workspace_mirror 稍后会被就地清空，这里必须是拷贝而不是引用。
        prev_mirror: dict = dict(session.workspace_mirror) if session is not None else {}
        if workspace_loader is not None:
            workspace_files, workspace_stamps = await _load_workspace(
                workspace_loader, prev_mirror)
        entries, fresh_slugs = _build_entries(code, input_files, skill_packages, skipped_slugs, entry_path, workspace_files)
        mk = await sandbox.execute(_mkdir_cmd(entries), ExecuteOptions(timeout_ms=10000))
        if not mk.ok and session is not None:
            # 复用的容器已经不在了（被外部 docker rm / 到点自毁 / 池满驱逐）：这条每次调用都会
            # 执行的 mkdir 就是探活。丢掉坏会话、本次降级为一次性沙箱重来一遍，不把基础设施
            # 抖动甩给模型当代码错误看。技能包在新容器里没有，必须整份重传。
            logger.info("复用沙箱已失效（%s），本次降级为一次性沙箱", mk.stderr[:120])
            session_lock.release()
            session_lock = None
            await session_pool.discard(session)
            session = None
            reused = False
            # 换了一具全新的容器：技能包要整份重传，用户文件同理——必须拿空记账重跑一次
            # loader。沿用上面那份增量结果 = 新容器里凭空缺掉「上一个容器里已经有」的文件。
            # prev_mirror 一并清空：新容器里什么都没有，拿旧容器的记账去 rm 纯属白跑一趟。
            prev_mirror = {}
            if workspace_loader is not None:
                workspace_files, workspace_stamps = await _load_workspace(workspace_loader, {})
            entries, fresh_slugs = _build_entries(code, input_files, skill_packages, set(), entry_path, workspace_files)
            sandbox = create_configured_sandbox("harness-bash")
            try:
                await sandbox.create()
            except (SandboxUnavailable, SandboxError) as e:
                logger.warning("Harness bash 沙箱不可用: %s", e)
                return SandboxExecutionResult(ok=False, error=f"沙箱不可用：{e}")
            mk = await sandbox.execute(_mkdir_cmd(entries), ExecuteOptions(timeout_ms=10000))
        if not mk.ok:
            return SandboxExecutionResult(ok=False, error=f"初始化工作目录失败：{mk.stderr[:200]}")

        if session is not None:
            # 镜像记账**先清空再动手**：从下一行起容器里的 files/ 会被写入、再被命令任意改动，
            # 在拿到执行后清单之前我们证明不了任何一条还与「我的文件」一致。异常/取消（包括
            # write_files 自己失败）从中间逃逸时留下的是空记账 = 下次全量重镜像（退回本次改动
            # 前的行为），绝不会留下「以为同步过其实没有」的假记账——那会让模型读到自己上一次
            # 的改动却以为是用户文件的现状，或者干脆读到一个根本没写进去的文件。
            session.workspace_mirror = {}
        # 同步是双向的：写入增量之外，还要把「记账里已经没有」的旧镜像文件从容器里删掉，
        # 否则用户在「我的文件」删掉的文件会以幽灵形态留在 /workspace/files 里被模型继续读、
        # 甚至被改写后当成新文件复活。只清 files/ 镜像树——tmp/skills/outputs/inputs 不是镜像，
        # 里面的东西没有「用户那边已删除」这个概念。
        # `workspace_stamps` 为空即跳过（loader 失败与"用户真的没有文件"同一个空 dict，分不开），
        # 且必须排在下面 files_baseline 之前，两条理由都写在 _prune_stale_mirror 的文档里。
        if workspace_stamps:
            await _prune_stale_mirror(sandbox, prev_mirror, workspace_stamps)
        await sandbox.write_files(entries)
        if session is not None:
            session.written_skills.update(fresh_slugs)

        # First-party PPTD has a fixed capability contract. Check it before
        # running any authoring command, and cache the result for this Run's
        # reusable sandbox. A gap is returned as a clear tool observation so
        # the model can choose another permitted path or report the blocker;
        # it is never turned into a loop of runtime probes.
        if _needs_pptd_preflight(skill_packages):
            cache = dict(session.runtime_preflight or {}) if session is not None else {}
            preflight = cache.get("ppt-studio") if isinstance(cache.get("ppt-studio"), dict) else None
            if not preflight:
                gap = await _pptd_runtime_preflight(sandbox, skill_packages)
                preflight = {
                    "status": "blocked" if gap else "ready",
                    "message": gap or "PPTD runtime ready",
                }
                if session is not None:
                    cache["ppt-studio"] = dict(preflight)
                    session.runtime_preflight = cache
            if str((preflight or {}).get("status") or "") != "ready":
                return SandboxExecutionResult(
                    ok=False,
                    error=str((preflight or {}).get("message") or "PPTD 运行环境自检未通过"),
                )

        from app.services.skills.runtime_ready import ensure_generic_runtime

        generic_gap = await ensure_generic_runtime(sandbox, skill_packages, session)
        if getattr(sandbox, "network_leaked", False):
            if session is not None:
                if session_lock is not None:
                    session_lock.release()
                    session_lock = None
                await session_pool.discard(session)
                session = None
            return SandboxExecutionResult(
                ok=False,
                error=generic_gap or "env_prep 结束后未能收回沙箱出网策略，已销毁该沙箱",
                termination_reason="network_policy_restore_failed",
            )
        if generic_gap:
            return SandboxExecutionResult(ok=False, error=generic_gap)

        # 复用沙箱时 outputs/ 里还留着上次调用的产物：先打基线，执行后只认新增/变更的文件。
        # 少了这一步，上次的产物会被当成本次产物重收、重审、重存版本，交付判定也会假阳性。
        baseline = await _snapshot_outputs(sandbox) if (reused and collect_outputs) else {}
        # files/ 基线与 outputs/ 不同，**不看 reused**：我们刚把镜像写进去，必须在执行前
        # 立刻打基线，否则「原样同步进来的文件」会被当成用户本次改动，每轮都白落一个新版本。
        if workspace_baseline is not None:
            files_baseline = dict(workspace_baseline)
        elif collect_workspace:
            files_baseline = await _snapshot_workspace_files(sandbox)
        else:
            files_baseline = {}

        await _progress(progress_callback, "executing", "正在执行生成脚本")
        exec_opts = ExecuteOptions(timeout_ms=timeout_ms or settings.SKILL_SANDBOX_LOCAL_TIMEOUT_MS)
        # 一次性沙箱在本函数返回后就会销毁，不能交出一个随后无法查询的 job_id。
        if background and session is None:
            background = False
        if background:
            if collect_workspace and workspace_baseline is None and not files_baseline:
                files_baseline = await _snapshot_workspace_files(sandbox)
            bg_opts = ExecuteOptions(
                timeout_ms=background_timeout_ms or exec_opts.timeout_ms,
                working_directory=exec_opts.working_directory,
                env=exec_opts.env,
            )
            try:
                bg_res = await sandbox.execute_background(exec_command, bg_opts)
            except SandboxNotSupported:
                background = False
            else:
                job_id = str(getattr(bg_res, "job_id", "") or "")
                if not job_id:
                    background = False
                else:
                    session_pool.retain_job(session, job_id, {
                        "command": code[:500],
                        "files_baseline": dict(files_baseline or {}),
                        "collect_workspace": bool(collect_workspace),
                        "migrate_outputs": bool(migrate_outputs),
                        "log_cursor": None,
                    })
                    if getattr(bg_res, "termination_reason", None):
                        session_pool.release_job(session, job_id)
                        return SandboxExecutionResult(
                            ok=False,
                            stdout=getattr(bg_res, "stdout", "") or "",
                            stderr=getattr(bg_res, "stderr", "") or "",
                            exit_code=getattr(bg_res, "exit_code", 1),
                            job_id=job_id,
                            termination_reason=getattr(bg_res, "termination_reason", None),
                            reused=reused,
                        )
                    return SandboxExecutionResult(
                        ok=True,
                        stdout=getattr(bg_res, "stdout", "") or "",
                        stderr=getattr(bg_res, "stderr", "") or "",
                        exit_code=None,
                        job_id=job_id,
                        reused=reused,
                    )
        page_streamed = False
        if progress_callback is not None:
            # 逐页产物直播：流式解析 stdout 页标记 → artifact_page 进度事件（emit 在读线程，
            # 用 run_coroutine_threadsafe 回到事件循环调异步回调）
            loop = asyncio.get_running_loop()

            def _emit_page(meta: dict, svg: str) -> None:
                nonlocal page_streamed
                page_streamed = True
                try:
                    idx = int(meta.get("index") or 0)
                    total = int(meta.get("total") or 0)
                except (TypeError, ValueError):
                    return
                title = str(meta.get("title") or f"第 {idx} 页")[:80]
                # kind=html（html-ppt 技能）：页体是自包含 HTML，前端用 iframe 渲染；默认仍是 svg
                body_key = "html" if str(meta.get("kind") or "").lower() == "html" else "svg"
                asyncio.run_coroutine_threadsafe(
                    _progress(
                        progress_callback, "artifact_page", title,
                        {"index": idx, "total": total, "title": title, body_key: svg},
                    ),
                    loop,
                )

            page_parser = _PageStreamParser(_emit_page)
            # 实时输出行直播（2026-07-20 用户反馈「做一半像卡住」）：脚本 print 的最近一行
            # 节流后作为 tool.progress 标签流出，前端运行中的工具行/卡头标题随之实时刷新——
            # 长任务期间用户能看到脚本自己说在干什么。页标记块内的 SVG 行跳过不播。
            import time as _time

            line_tail = bytearray()
            live_state = {"last_emit": 0.0, "in_page": False}

            def _on_stdout(chunk: bytes) -> None:
                page_parser.feed(chunk)
                line_tail.extend(chunk)
                if len(line_tail) > 16384:
                    del line_tail[:-8192]
                if b"\n" not in chunk:
                    return
                lines = bytes(line_tail).split(b"\n")
                del line_tail[:]
                line_tail.extend(lines[-1])  # 未完行留作尾巴
                candidate = ""
                for raw in lines[:-1]:  # 顺序扫维护页块状态，取最后一条可播行
                    text_line = raw.decode("utf-8", errors="replace").strip()
                    if "@@PPT_PAGE_END@@" in text_line:
                        live_state["in_page"] = False
                        continue
                    if "@@PPT_PAGE@@" in text_line:
                        live_state["in_page"] = True
                        continue
                    if live_state["in_page"] or not text_line or text_line.startswith("<"):
                        continue
                    candidate = text_line
                if not candidate:
                    return
                now = _time.monotonic()
                if now - live_state["last_emit"] < 1.5:
                    return
                live_state["last_emit"] = now
                asyncio.run_coroutine_threadsafe(
                    _progress(progress_callback, "stdout", candidate[:120]), loop,
                )

            exec_opts.on_stdout = _on_stdout
        # -u 必须：非 TTY 下 Python stdout 块缓冲，print 全憋到进程结束一次性吐出——
        # 逐页直播/实时行会退化成「结尾齐发」（2026-07-20 实测踩坑，同 docker exec E2E 教训）
        res = await sandbox.execute(exec_command, exec_opts)
        if getattr(sandbox, "network_leaked", False) or getattr(res, "termination_reason", None) == "network_policy_restore_failed":
            if session is not None:
                if session_lock is not None:
                    session_lock.release()
                    session_lock = None
                await session_pool.discard(session)
                session = None
            return SandboxExecutionResult(
                ok=False,
                error=str(getattr(res, "stderr", None) or "env_prep 结束后未能收回沙箱出网策略，已销毁该沙箱"),
                termination_reason="network_policy_restore_failed",
            )
        if page_streamed and res.stdout:
            # 页体不回灌模型：几百 KB 的 SVG 会把工具回执撑爆（模型只需要知道页已产出）
            res.stdout = _PAGE_BLOCK_RE.sub("[页面已直播预览]\n", res.stdout)

        workspace_changes: list = []
        workspace_oversized: list = []
        workspace_deleted: list = []
        outputs_migrated: list = []
        outputs_conflicts: list = []
        review_overall: Optional[dict] = None
        if migrate_outputs and collect_workspace:
            # **必须在 _list_workspace_files 之前**：搬过来的文件要被算成本次 files/ 变更，
            # 才能顺着既有链路读回、质检、落库。
            outputs_migrated, outputs_conflicts = await _migrate_outputs(sandbox)
        if collect_workspace:
            listing = await _list_workspace_files(sandbox)
            after = listing or []
            changed = [
                f for f in after
                if files_baseline.get(f["path"]) != (f["size"], f["mtime"])
            ]
            # 回写是**单向的**：只看执行后清单里有什么，删掉的文件压根不在里面 →
            # 沙箱里的删除/改名永远同步不回用户文件区。这不是这里要修的（真删用户文件是
            # 另一个量级的权限），但**必须报上去**——否则模型 rm 完看到 exit 0，
            # 转头就向用户宣布"已删除"，而文件原封不动。上层拼进回执，见 shell.py。
            # listing is None（清单没问出来）时不报：那不是删除，是这次没看见。
            if listing is not None:
                after_paths = {f["path"] for f in after}
                workspace_deleted = sorted(p for p in files_baseline if p not in after_paths)
                # 增量镜像记账回填：只留「本次执行确实没动过、且现在还在容器里」的那些。
                # 被改动的一律作废（沙箱里的新内容未必落得了库——中间产物、越权写入、
                # 超限、落库失败都会让库里仍是旧字节，留着记账就等于让下次跳过重写）。
                if session is not None and workspace_stamps is not None:
                    session.workspace_mirror = {
                        rel: digest for rel, digest in workspace_stamps.items()
                        if rel in after_paths and rel not in {f["path"] for f in changed}
                    }
            # **读回之前**就按大小筛掉落不了库的文件：单文件超过 USER_FILES_MAX_SIZE_MB 时
            # save_file/overwrite_file 必然抛 413，把它读进 agent-api 进程纯属白付内存
            # （local 后端是 get_archive + b"".join + tar 解，峰值约 2× 文件字节，而 agent-api
            # 容器没有内存上限）。旧的 outputs/ 链路有 _fetch_output_bytes 在读之前过滤，
            # 迁到 files/ 时把这道闸丢了。
            max_file_bytes = int(getattr(settings, "USER_FILES_MAX_SIZE_MB", 15) or 15) * 1024 * 1024
            oversized = [f["path"] for f in changed if int(f.get("size") or 0) > max_file_bytes]
            changed_paths = [f["path"] for f in changed if f["path"] not in set(oversized)]
            if oversized:
                logger.info("files/ 变更中 %d 个超过单文件上限，不读回：%s",
                            len(oversized), oversized[:5])
            if changed_paths:
                await _progress(progress_callback, "collecting", "正在保存文件区的改动")
                try:
                    blobs = await sandbox.read_files([f"{_FILES}/{p}" for p in changed_paths])
                except (SandboxError, SandboxUnavailable):
                    logger.warning("读回 files/ 变更失败（本次改动不落库）", exc_info=True)
                    blobs = []
                for rel, blob in zip(changed_paths, blobs):
                    # ⚠️ 必须查 blob.ok：FileReadResult.data 默认是 b""，读回失败会被下游
                    # 当成「0 字节的空文件」→ 回执告诉模型"请检查生成逻辑是不是没真正写入内容"，
                    # 而文件在沙箱里内容完好，模型被引导去白重做一遍。
                    if getattr(blob, "ok", True) is False:
                        logger.warning("读回 files/%s 失败，跳过（不当成空文件）", rel)
                        continue
                    data = getattr(blob, "data", None)
                    if isinstance(data, (bytes, bytearray)):
                        workspace_changes.append({"path": rel, "data": bytes(data)})
                if oversized:
                    # 交给上层拼回执：不说的话模型以为存上了
                    workspace_oversized.extend(oversized)
                # 产物检查改成「文件写入事件驱动」（批 3）：命中办公/文档后缀就跑既有硬校验，
                # 结果以结构 review 状态回给调用方拼 [artifact_validity_gate=...] 标记。
                # ⚠️ 不能改成「让模型自己调 verify_artifact」——模型会忘，安全网当场失效；
                # 也不新写一套检查，直接复用 output_review（目录已参数化）。
                gated = [c for c in workspace_changes
                         if c["path"].lower().endswith(_REVIEWABLE_EXTS)]
                gated_entries = [
                    {"name": c["path"], "size": len(c["data"])} for c in gated
                ]
                # kill switch 要对新路径同样有效：outputs/ 分支一直带着这个开关，
                # files/ 分支漏了 → 关掉开关照跑质检，也照付那 20s 超时风险。
                if gated and settings.SANDBOX_OUTPUT_REVIEW_ENABLED:
                    await _progress(progress_callback, "reviewing", "正在检查产物可用性")
                    try:
                        from .output_review import review_outputs
                        review_overall = await review_outputs(
                            sandbox,
                            gated_entries,
                            directory=_FILES,
                            expected_user_images=expected_user_images,
                        )
                    except Exception:  # noqa: BLE001 —— 审查不可用不阻塞交付，如实标 unknown
                        logger.warning("files/ 产物质检失败", exc_info=True)
                        review_overall = {"status": "unknown"}
                    # 视觉模型审查不属于 Harness 的交付门禁：视觉自看可以由具体 Skill
                    # 自主选择，但平台只依据这里的客观结构检查决定 validity gate。
        outputs: list = []
        unchanged: list = []
        if collect_outputs:
            await _progress(progress_callback, "collecting", "正在收集生成的产物")
            outputs = await _list_outputs(sandbox)
            if baseline:
                outputs, unchanged = _split_by_baseline(outputs, baseline)
            # 第一层：同一沙箱内做硬校验（能否打开/是否为空/页数）。
            if outputs and settings.SANDBOX_OUTPUT_REVIEW_ENABLED:
                await _progress(
                    progress_callback, "reviewing", "正在审查生成的文件",
                    {"output_count": len(outputs)},
                )
                from .output_review import review_outputs
                review_overall = await review_outputs(sandbox, outputs, expected_user_images=expected_user_images)
                status = (review_overall or {}).get("status")
                await _progress(
                    progress_callback, "reviewed",
                    "已完成文件完整性检查",
                    {"review_complete": True},
                )
                # 视觉模型/审美评分不参与 Harness 交付判定。若具体 Skill 需要自看页面，
                # 应在自己的生成栈中完成；平台只保留上面的确定性结构校验。
            # 产物字节须在 finally 的 sandbox.delete() 之前读回（ADR-047 §6.6：落「我的文件」）
            if fetch_output_bytes and outputs:
                await _fetch_output_bytes(sandbox, outputs)

        await _progress(
            progress_callback,
            "collected",
            f"已收集 {len(outputs)} 个产物" if outputs else "脚本执行完成",
            {"output_count": len(outputs)},
        )

        return SandboxExecutionResult(
            ok=res.ok, stdout=res.stdout, stderr=res.stderr, exit_code=res.exit_code,
            output_files=outputs, truncated=res.truncated,
            timed_out=bool(getattr(res, "timed_out", False)),
            termination_reason=getattr(res, "termination_reason", None),
            workspace_changes=workspace_changes,
            workspace_oversized=workspace_oversized,
            workspace_deleted=workspace_deleted,
            outputs_migrated=outputs_migrated,
            outputs_conflicts=outputs_conflicts,
            review=review_overall, reused=reused,
            unchanged_outputs=[f["name"] for f in unchanged],
        )
    except SandboxError as e:
        return SandboxExecutionResult(ok=False, error=f"沙箱操作失败：{e}")
    finally:
        # 复用会话的容器留给本 Run 后续调用（由 session_pool 的显式关闭/TTL 巡检/容器自毁三层收），
        # 一次性沙箱仍是用完即弃：对未 create 的容器是 no-op；已起的孤儿在此清除。
        if session is not None:
            if session_lock is not None:
                session_lock.release()
            session_pool.release(session)
        elif sandbox is not None:
            await sandbox.delete()
        if acquired and sem is not None:
            sem.release()


async def _migrate_outputs(sandbox) -> tuple:  # noqa: ANN001
    """把 /workspace/outputs 下的文件**搬**进 /workspace/files，返回 (已搬, 同名未搬)。

    为什么需要：`_mkdir_cmd` 无条件创建 outputs/，所以往那里写永远成功；而 bash 这条路
    `collect_outputs=False`，那里的东西既不收集也不报告。execute_in_sandbox 时代写成的技能、GitHub 上
    现成的技能全都硬编码 `/workspace/outputs` —— 命令 exit 0、脚本打印"完成：outputs/x.pptx"、
    回执一个字不提落库、模型向用户宣布交付，文件随容器 TTL 蒸发。这是最纯粹的静默失败。

    为什么是"搬"不是"拷"：搬完 outputs/ 就空了，同一个 Run 里后续调用不会把同一个文件反复
    重收（不必再维护一份 outputs 基线），模型 `ls` 一眼也能看出该往哪写。文件仍然在，
    只是换到了 files/ —— 回执会点名说清楚。

    同名不覆盖：files/ 里已有同名文件时**不搬**，只记名字。模型写 outputs/ 是无意的，
    不该因此悄悄改写用户「我的文件」里的另一个文件（那是二次伤害）。
    """
    mover = (
        "import os,json,shutil;"
        f"o={_OUTPUTS!r};f={_FILES!r};"
        "moved=[];conflict=[]"
        "\nfor n in (sorted(os.listdir(o)) if os.path.isdir(o) else []):\n"
        "    p=os.path.join(o,n)\n"
        "    if not os.path.isfile(p):\n"
        "        continue\n"
        "    d=os.path.join(f,n)\n"
        "    if os.path.exists(d):\n"
        "        conflict.append(n); continue\n"
        "    try:\n"
        "        os.makedirs(f, exist_ok=True); shutil.move(p,d)\n"
        "    except OSError:\n"
        "        conflict.append(n); continue\n"
        "    moved.append(n)\n"
        "print(json.dumps({'moved':moved,'conflict':conflict}))"
    )
    try:
        res = await sandbox.execute(
            f"python -c {shlex.quote(mover)}", ExecuteOptions(timeout_ms=15000))
        data = json.loads((res.stdout or "").strip() or "{}")
    except (SandboxError, SandboxUnavailable, ValueError, TypeError):
        # 搬运失败不阻断交付（files/ 里的东西照常落库）；日志留痕即可
        logger.warning("outputs/ 迁移失败（本次不迁移）", exc_info=True)
        return [], []
    if not isinstance(data, dict):
        return [], []
    moved = [str(x) for x in (data.get("moved") or [])]
    conflict = [str(x) for x in (data.get("conflict") or [])]
    if moved or conflict:
        logger.info("outputs/ 迁移：已搬 %s，同名未搬 %s", moved[:5], conflict[:5])
    return moved, conflict


async def _list_outputs(sandbox) -> list:  # noqa: ANN001
    """列 /workspace/outputs 下的文件名 + 字节数 + mtime（不读回内容——下载由 G6 产出通道做）。

    用镜像必带的 python 列目录并输出 JSON，不赌 slim 镜像是否有 GNU `find -printf`。
    mtime 用 st_mtime_ns（整数，无浮点精度问题）：复用沙箱时靠它 + size 判断文件本次动没动过。
    """
    lister = (
        "import os,json;"
        f"d={_OUTPUTS!r};"
        "s=lambda n: os.stat(os.path.join(d,n));"
        "print(json.dumps([[n,(lambda st: [st.st_size, st.st_mtime_ns])(s(n))] "
        "for n in (sorted(os.listdir(d)) if os.path.isdir(d) else []) "
        "if os.path.isfile(os.path.join(d,n))]))"
    )
    res = await sandbox.execute(f"python -c {shlex.quote(lister)}", ExecuteOptions(timeout_ms=10000))
    try:
        arr = json.loads((res.stdout or "").strip() or "[]")
    except (ValueError, TypeError):
        return []
    return [{"name": n, "size": st[0], "mtime": st[1]} for n, st in arr]


async def _list_workspace_files(sandbox) -> Optional[list]:  # noqa: ANN001
    """递归列 /workspace/files 下的相对路径 + 字节数 + mtime_ns。**清单不可用时返回 None**。

    None 与 `[]` 必须分开：`[]` 是"目录真的空了"，None 是"这次没问出来"。合成一个值的话
    「清单挂了」会被读成「文件全被删了」——删除提示（见 collect_workspace 分支）会对着
    一次解析失败报一串根本没被删的文件名。

    与 _list_outputs 的差别：**递归**（用户文件区有子目录），返回的是相对 files/ 的路径。
    同样用镜像必带的 python 而不是 GNU find（slim 镜像未必有 -printf）。
    """
    lister = (
        "import os,json;"
        f"d={_FILES!r};"
        "out=[];"
        "\nfor root,_,fs in (os.walk(d) if os.path.isdir(d) else []):\n"
        "    for n in sorted(fs):\n"
        "        p=os.path.join(root,n)\n"
        # os.stat 跟随软链：files/ 下一个**悬空软链接**（解含 symlink 的 tar/zip 时很常见）
        # 就会让整个脚本 FileNotFoundError 退出、stdout 为空 → 下面解析失败返回 []
        # → 基线与执行后都是 []，本 Run 之后每一次 bash 的改动全部静默不落库。
        "        try:\n"
        "            st=os.stat(p)\n"
        "        except OSError:\n"
        "            continue\n"
        "        out.append([os.path.relpath(p,d), st.st_size, st.st_mtime_ns])\n"
        "print(json.dumps(out))"
    )
    res = await sandbox.execute(
        f"python -c {shlex.quote(lister)}",
        # max_output_bytes 必须显式给：默认 32KB（SKILL_SANDBOX_LOCAL_MAX_OUTPUT_BYTES）会把
        # JSON 清单从中间砍断 → json.loads 抛错 → return [] → 同样是"所有改动静默不落库"。
        # 实测约 744 个根目录文件就过线（带子目录路径更早），而文件区上限是 200 + 模型自己
        # 解包摊出来的中间文件，够得着。
        ExecuteOptions(timeout_ms=15000, max_output_bytes=4 * 1024 * 1024),
    )
    raw = (res.stdout or "").strip()
    try:
        arr = json.loads(raw or "[]")
    except (ValueError, TypeError):
        # 这条 warning 是唯一的线索：清单拿不到 → 变更 diff 失效 → 产物不落库，
        # 而调用方与模型都看不出异常。静默返回 [] 是之前一整类"文件凭空消失"的根因。
        logger.warning(
            "files/ 清单解析失败（本次变更检测失效，产物可能不落库）rc=%s 输出前 200 字：%r",
            res.exit_code, raw[:200])
        return None
    return [{"path": r, "size": sz, "mtime": mt} for r, sz, mt in arr]


async def _snapshot_workspace_files(sandbox) -> dict:  # noqa: ANN001
    """执行前基线：{相对路径: (size, mtime_ns)}。用于识别本次真正被改动/新建的文件。"""
    return {f["path"]: (f["size"], f["mtime"]) for f in (await _list_workspace_files(sandbox) or [])}


async def _snapshot_outputs(sandbox) -> dict:  # noqa: ANN001
    """执行前的 outputs/ 基线：{文件名: (size, mtime)}。复用沙箱时用于识别本次真正产出的文件。"""
    return {f["name"]: (f["size"], f.get("mtime")) for f in await _list_outputs(sandbox)}


def _split_by_baseline(outputs: list, baseline: dict) -> tuple[list, list]:
    """按基线拆成（本次新增/变更, 本次没动过的既有文件）。

    没有这一层，复用沙箱下上次调用的产物每次都会被重新收集、重新审查、重新存一个版本，
    连「本轮到底交付了没有」的判定都会假阳性。
    """
    fresh, stale = [], []
    for f in outputs:
        prev = baseline.get(f["name"])
        (fresh if prev is None or prev != (f["size"], f.get("mtime")) else stale).append(f)
    return fresh, stale


async def _fetch_output_bytes(sandbox, outputs: list) -> None:  # noqa: ANN001
    """把产物字节读回宿主（就地写进 outputs[i]["content"]）。

    单文件上限对齐「我的文件」的 USER_FILES_MAX_SIZE_MB；超限的不读、标 "skipped"，
    模型回执里仍可见名字与大小。读取失败标 "skipped"="read_error"，不让单个坏文件毁整批。
    """
    limit = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    eligible = []
    for o in outputs:
        if o["size"] > limit:
            o["skipped"] = "size_limit"
        else:
            eligible.append(o)
    if not eligible:
        return
    results = await sandbox.read_files([f"{_OUTPUTS}/{o['name']}" for o in eligible])
    by_path = {r.path: r for r in results}
    for o in eligible:
        r = by_path.get(f"{_OUTPUTS}/{o['name']}")
        if r is not None and r.ok:
            o["content"] = r.data
        else:
            o["skipped"] = "read_error"
