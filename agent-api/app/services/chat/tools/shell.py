# -*- coding: utf-8 -*-
"""沙箱 shell 工具（2026-07-27）——`bash`。

为什么要它：主对话此前只能跑 Python（`execute_in_sandbox`），于是所有本该是 shell 管线的东西都被硬塞
进自包含 Python 脚本里——自研 ppt-html skill 长成那个样子就是这么来的。有了 bash，GitHub 上
现成的 skill（shell 脚本 + CLI + Makefile）可以按它自己的 README 直接跑。

复用 `sandbox_executor.execute_in_sandbox(language="bash")`：并发闸、会话复用（同一 Run 内共用容器，文件跨
调用保留）、mkdir、写入口、超时、输出截断、错误指纹止损全都是现成的，一行都没抄。

借鉴 pi（reference/pi，`packages/coding-agent/src/core/tools/bash.ts`）两条：
- **timeout 由模型按秒传、服务端封顶**，而不是写死一个配置值——现成 skill 跑构建动辄几分钟；
- **输出同时卡字节和行数**：`grep -r` 这类输出是「行多但每行短」，只卡字节挡不住冲爆上下文。

统一文件系统落地后（同日批 2/3），上面那条 v1 边界已经取消：`/workspace/files` 是本会话
工作区、用户明确选中件和修订目标的镜像；bash 在里面的改动执行后自动落库、进版本历史，产物有效性门禁改为对
files/ 的写入事件驱动。**但生成器脚本（.py/.sh）不落库**——它们是过程不是交付物，
见 workspace_sync._is_intermediate。
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import io
import json
import logging
import re
import shlex
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Awaitable, Callable, List, Optional

from app.core.config import settings
from app.services.sandbox import sandbox_executor
from app.services.skills import ppt_project_audit_runtime, pptd_layout_lint_runtime
from app.services.skills.ppt_agentic_adapter import STAGING_ROOT, is_agentic_ppt_profile
from app.services.skills.ppt_project_progress import ppt_project_progress
from app.services.skills.ppt_policy import (
    ppt_photo_requirement,
    requested_ppt_image_count,
)

from .base import (INTENT_PROP, MainTool, ToolSoftError, ToolValue, _file_origin,
                   current_tool_call_id, remaining_tool_budget_s,
                   _with_validity_gate)
from .workspace import _is_unavailable, _unavailable_note, _unmounted_note
from .workspace_sync import build_sync

logger = logging.getLogger(__name__)


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_PYYAML_RUNTIME_FILENAME = ".platform-pyyaml.zip"


@functools.lru_cache(maxsize=1)
def _pyyaml_runtime_zip() -> bytes:
    """Bundle trusted pure-Python YAML modules for drifted sandbox images."""
    import yaml

    package_root = Path(yaml.__file__).resolve().parent
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(package_root.glob("*.py")):
            archive.writestr(f"yaml/{source.name}", source.read_bytes())
    return stream.getvalue()


def _expected_images_from_attachments(attachments) -> list:
    """从本轮对话附件抽出用户选中的图片文件名（files/ 扁平名）。"""
    out = []
    seen = set()
    for item in attachments or []:
        if isinstance(item, dict):
            kind = str(item.get("kind") or "").lower()
            name = str(item.get("filename") or item.get("name") or "").strip()
            mime = str(item.get("mime") or item.get("mime_type") or "").lower()
        else:
            kind = str(getattr(item, "kind", "") or "").lower()
            name = str(getattr(item, "filename", None) or getattr(item, "name", "") or "").strip()
            mime = str(getattr(item, "mime", "") or "").lower()
        if not name:
            continue
        base = name.replace(chr(92), "/").rsplit("/", 1)[-1]
        if not base or base in {".", ".."}:
            continue
        ext = ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""
        is_image = kind == "image" or mime.startswith("image/") or ext in _IMAGE_EXTS
        if not is_image or base in seen:
            continue
        seen.add(base)
        out.append(base)
    return out


def _max_timeout_s() -> int:
    """服务端封顶：模型可以要更长，但不能无限长（沙箱容器本身还有自毁时限）。

    上限**不能超过** `TOOL_CALL_TIMEOUT_SECONDS`：主循环在那个点无条件 cancel 整个工具调用，
    而 cancel 走的是异常路径 —— `sync.persist()` 根本不会执行，已经产出的文件一个都不落库。
    原先描述里写着 600s、循环 300s 就砍，长构建必然半途而废且产物凭空消失。留 10s 余量给
    读回与落库。
    """
    loop_cap = int(getattr(settings, "TOOL_CALL_TIMEOUT_SECONDS", 0) or 0)
    if loop_cap <= 0:
        return _HARD_MAX_TIMEOUT_S
    return max(30, min(_HARD_MAX_TIMEOUT_S, loop_cap - 10))


_HARD_MAX_TIMEOUT_S = 600
# 命令终止后留给「读回 files/ 变更 + 产物质检 + 落库」的余量（秒）。压在这条线内，
# 主循环的 `task.cancel()` 就永远不会打断落库——那条路径一个产物都存不下来。
_COLLECT_RESERVE_S = 25.0
_DEFAULT_TIMEOUT_S = 120
# 回执里保留的输出行数上限（借鉴 pi：字节之外必须卡行数）
_MAX_OUTPUT_LINES = 300
_SKILLS_DIR_HINT = "workspace/skills"
_PPT_PROJECT_TEXT_EXTS = (
    ".page", ".pptd", ".md", ".txt", ".json", ".yaml", ".yml", ".css", ".csv",
)
_PPT_PROJECT_IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
)
_PPT_WRITE_MAX = 400_000
_PPT_PROJECT_DENIED_HEADS = {"skills", "files", "inputs", "outputs", "opt"}
_JOB_COMMAND_RE = re.compile(r"^job(?:\s+(status|logs|cancel|wait))?(?:\s+(\S+))?\s*$", re.I)


def _parse_job_command(command: str) -> tuple[Optional[str], str]:
    match = _JOB_COMMAND_RE.match(str(command or "").strip())
    if not match:
        return None, ""
    return (match.group(1) or "status").lower(), str(match.group(2) or "").strip()


_JOB_LOG_CAP = 4000


def _coerce_log_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return "".join(_coerce_log_text(item) for item in value)
    return str(value)


def _job_logs_payload(logs) -> tuple[str, object]:
    """OpenSandbox CommandLogs is `content` + `cursor`, not stdout/stderr."""
    cursor = getattr(logs, "cursor", None)
    if hasattr(logs, "content"):
        text = _coerce_log_text(getattr(logs, "content", None))
    else:
        text = _coerce_log_text(getattr(logs, "stdout", None))
        stderr = _coerce_log_text(getattr(logs, "stderr", None))
        if stderr:
            text = f"{text}\n[stderr]\n{stderr}" if text else stderr
    return text, cursor


def _tail_log(text: str, cap: int = _JOB_LOG_CAP) -> str:
    raw = str(text or "")
    if len(raw) <= cap:
        return raw
    return "…（此前日志已省略，只保留末尾）\n" + raw[-cap:]


def ppt_project_relpath(raw: str) -> str:
    """Resolve a model path onto the PPT working copy. Raises ValueError if outside."""
    text = str(raw or "").replace("\\", "/").strip()
    if not text:
        raise ValueError("请给出工程内路径，例如 pages/01.page 或 DESIGN.md。")
    prefixes = (
        STAGING_ROOT.rstrip("/") + "/",
        STAGING_ROOT,
        "/workspace/tmp/ppt-project/",
        "tmp/ppt-project/",
        "ppt-project/",
    )
    for prefix in prefixes:
        if text == prefix.rstrip("/") or text.startswith(prefix):
            text = text[len(prefix):].lstrip("/")
            break
    if text.startswith("/"):
        raise ValueError(
            "只能读写当前 PPT 工程（/workspace/tmp/ppt-project），"
            "不要读写技能目录或「我的文件」。"
        )
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in text.split("/")) or not parts:
        raise ValueError("路径非法。")
    head = parts[0].lower()
    if head in _PPT_PROJECT_DENIED_HEADS:
        raise ValueError(
            "技能目录和「我的文件」不是 PPT 工程。"
            "请读写 /workspace/tmp/ppt-project 下的 pages/*.page、DESIGN.md、media/。"
        )
    return "/".join(parts)


def ppt_project_is_text(rel: str) -> bool:
    lower = str(rel or "").lower()
    return any(lower.endswith(ext) for ext in _PPT_PROJECT_TEXT_EXTS)


def ppt_project_is_image(rel: str) -> bool:
    lower = str(rel or "").lower()
    return any(lower.endswith(ext) for ext in _PPT_PROJECT_IMAGE_EXTS)


# 作为源文件独立维护并在沙箱内执行；视觉模型仍负责艺术方向。
_PPTD_LAYOUT_LINT = Path(pptd_layout_lint_runtime.__file__).read_text(encoding="utf-8")
_PPT_PROJECT_AUDIT = Path(ppt_project_audit_runtime.__file__).read_text(encoding="utf-8")


def _requested_ppt_image_count(text: str) -> int:
    """Compatibility wrapper for focused tests and older internal callers."""
    return requested_ppt_image_count(text)


def _blocked_by_missing_skill(command: str, blocked: list, mounted: list) -> bool:
    """这条命令是不是**正要去用一个没挂上的技能**——是的话就该硬失败，不只是提醒。

    技能没挂上时回执里已经说了一句，但模型有可能读漏；而它若正要执行技能目录里的脚本，
    命令必然以一个语义无关的 "No such file or directory" 收场，模型会当成路径写错反复瞎试。
    这种情况直接明确失败，把真正的原因摆在它面前（ADR-043：含脚本技能不可降级）。

    但**不能一见 skills/ 就拦**：多选技能时 A 挂上了、B 没挂上，`ls /workspace/skills/A`
    是完全合法的。所以只在两种情形拦：
    ① 命令点名了某个没挂上的技能目录；② 命令泛泛地进 skills/ 而那里**一个技能都没有**。
    """
    text = str(command or "")
    for pkg in blocked or []:
        slug = str(pkg.get("slug") or "") if isinstance(pkg, dict) else ""
        if slug and f"skills/{slug}" in text:
            return True
    return _SKILLS_DIR_HINT in text and not mounted


def _cap_lines(text: str, limit: int = _MAX_OUTPUT_LINES) -> tuple[str, bool]:
    lines = str(text or "").splitlines()
    if len(lines) <= limit:
        return "\n".join(lines), False
    head = lines[: limit // 2]
    tail = lines[-(limit - len(head)):]
    return "\n".join(head + [f"…（中间 {len(lines) - limit} 行已省略）…"] + tail), True


def build_shell_tools(
    *,
    run_id: Optional[str] = None,
    tool_meta_sink: Optional[dict] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    newapi_key: str = "",
    # 技能包解析器：**技能文件挂进沙箱的唯一入口**，不接这一路 Skill 整体失效。
    # 它由 workspace.build_workspace_tools 的 exports 交过来，闭包了「@ 选中的包 +
    # use_skill 即时加载的包」，每次调用都取最新——所以同一轮里模型先 use_skill 再 bash
    # 也能看到刚加载的技能。
    resolve_skill_packages: Optional[Callable[[], Awaitable[list]]] = None,
    # 显式 ppt-studio 在受理时只冻结工具结构；真正读取成功前不得跑其脚本或发布。
    is_skill_loaded: Optional[Callable[[str], bool]] = None,
    # 与 use_skill 共用的「已提过的技能缺口」集合（由 workspace 的 exports 传入）：
    # 同一个包只念一遍"没挂上"，否则每次 bash 都重复一遍，真告警被自己的噪音稀释。
    skill_notice_seen: Optional[set] = None,
    # 用户意图：**只用于 PPT 产物存在性判定**（"要做 PPT 却一个 pptx 都没落地"），
    # 不用来判技能内部怎么实现——平台不检查技能管线（见 ppt_policy 模块头）。
    user_message: str = "",
    # 最近几条用户消息：PPT 语境要看最近几轮，不能只看当轮（澄清式对话会把「做个PPT」和
    # 「主题是…10页…」拆到两轮，干活那轮不含 PPT 字样）。与 turn_prepare 同一口径。
    recent_user_messages: Optional[List[str]] = None,
    # 进度通道：不接的话前端执行卡在整个命令执行期间是**空的**（「正在创建执行环境 /
    # 正在收集产物」这些阶段全靠它），而且**逐页产物直播也失效** —— PPT 逐页预览靠
    # sandbox_executor 从 stdout 解析 @@PPT_PAGE@@ 标记后经 progress_callback 发 artifact_page
    # 事件，没有回调就没有事件。
    tool_progress_queue: Optional["asyncio.Queue"] = None,
    # 原位修改授权（Harness）：非空时本轮只准回写这一个文件。bash 不能像 create_file 那样
    # 直接从工具清单里摘掉——PPT 修改就是靠它跑技能脚本——所以约束下沉到落库层（WorkspaceSync），
    # 与 paths.py `_require_target()` 同一口径：沙箱里随便写，但只有目标文件回得到「我的文件」。
    revision_target: Optional[dict] = None,
    # 本轮用户选中的附件（用于真图 PPT 嵌入质检）
    attachments: Optional[list] = None,
    expected_user_images: Optional[List[str]] = None,
    execution_profile: Optional[dict] = None,
    # Standard interactive Runs may let the model explicitly load the first-party PPT Skill.
    # Keep the candidate publish/fetch tools in the frozen schema, while their bodies consult the
    # shared live profile and reject calls until that authoritative Skill fact exists.
    dynamic_profile_enabled: bool = False,
    # search_web 图片目录。严格 PPT Profile 不允许 download_url 污染「我的文件」，
    # 因此由 fetch_ppt_asset 将选中的 [图N] 直接注入同一沙箱的 media/。
    image_sink: Optional[List[dict]] = None,
    persist_outputs: bool = True,
    scope_to_thread: bool = False,
    workspace_folder_id: str = "",
) -> List[MainTool]:
    _publish_state = {"fetched_assets": []}
    _skill_notice_seen: set = skill_notice_seen if skill_notice_seen is not None else set()
    _skill_notice_state = {"resolve_failed_reported": False}
    local_network = str(getattr(settings, "SKILL_SANDBOX_LOCAL_NETWORK", "none") or "none").strip().lower()
    sandbox_network_desc = (
        "• 沙箱当前可访问公网（Docker bridge）：可以用 Python urllib/pip/git 获取公开资源和安装依赖。"
        "curl/wget/npm 未预装；不要把外部代码直接管道给 shell，请先阅读再执行。\n"
        if local_network not in {"", "none", "disabled", "off"}
        else
        "• 沙箱当前禁止出网：不要 pip install / npm i / curl / git clone，外部字节请使用 download_url 注入。\n"
    )
    _expected_user_images = list(expected_user_images or []) or _expected_images_from_attachments(attachments)
    _selected_file_ids = {
        str(
            (att.get("file_id") if isinstance(att, dict) else getattr(att, "file_id", ""))
            or ""
        ).strip()
        for att in (attachments or [])
    }
    _selected_file_ids.discard("")
    _artifact_profile = is_agentic_ppt_profile(execution_profile)
    _explicit_ppt_skill_pending = bool(
        _artifact_profile
        and str((execution_profile or {}).get("resolution_reason") or "") == "explicit_ppt_skill"
    )

    def _artifact_profile_active() -> bool:
        return bool(_artifact_profile or is_agentic_ppt_profile(execution_profile))

    def _require_loaded_explicit_ppt_skill() -> None:
        if not _explicit_ppt_skill_pending:
            return
        if is_skill_loaded is not None and bool(is_skill_loaded("ppt-studio")):
            return
        raise ToolSoftError(
            "已选中的 ppt-studio 尚未读取；请先调用 use_skill(skill_id=\"ppt-studio\")，"
            "成功后再执行 PPT 工程、脚本、素材或发布操作。",
            code="skill_not_loaded",
            retryable=True,
        )

    _dynamic_artifact_candidate = bool(dynamic_profile_enabled and not _artifact_profile)
    context_brief = "\n".join(
        value for value in [*(recent_user_messages or []), user_message or ""] if value
    )
    profile_qa = (
        execution_profile.get("qa_contract") or {}
        if isinstance(execution_profile, dict) else {}
    )
    image_requirement = (
        profile_qa.get("image_requirement")
        if isinstance(profile_qa, dict) else None
    )
    if not isinstance(image_requirement, dict) or str(image_requirement.get("mode") or "") == "skill_default":
        image_requirement = ppt_photo_requirement(context_brief) or {
            "mode": "skill_default", "min_images": 0, "min_sources": 0, "brief": "",
        }
    photo_requirement_mode = str(image_requirement.get("mode") or "skill_default")
    try:
        profile_min_images = min(12, max(0, int(image_requirement.get("min_images") or 0)))
    except (TypeError, ValueError):
        profile_min_images = 0
    try:
        profile_min_sources = min(
            profile_min_images,
            max(0, int(image_requirement.get("min_sources") or 0)),
        )
    except (TypeError, ValueError):
        profile_min_sources = 0
    photo_requirement_brief = str(image_requirement.get("brief") or context_brief)[:400]
    if _artifact_profile_active() and local_network in {"", "none", "disabled", "off"}:
        sandbox_network_desc = (
            "• 沙箱当前禁止出网：不要 pip install / npm i / curl / git clone。PPT 图片素材先用 "
            "search_web 获取 [图N]，再用 fetch_ppt_asset 直接注入本工程 media/。\n"
        )

    async def _fetch_ppt_asset(args: dict) -> ToolValue:
        if not _artifact_profile_active():
            raise ToolSoftError("当前 Run 不是 artifact_coding PPT Profile，不能注入 PPT 素材。")
        _require_loaded_explicit_ppt_skill()
        raw_ref = str(args.get("url") or "").strip()
        if not raw_ref:
            raise ToolSoftError("请给出 search_web 图片目录中的 [图N] 或公开图片直链。")
        resolved_url = raw_ref
        referer = ""
        match = re.fullmatch(r"\[?\s*图\s*(\d+)\s*\]?", raw_ref)
        if match:
            index = int(match.group(1)) - 1
            sink = image_sink or []
            if not (0 <= index < len(sink)):
                raise ToolSoftError(
                    f"图片引用 {raw_ref} 无效（本轮图片目录共 {len(sink)} 张）。"
                    "先用 search_web 搜图，再按真实 [图N] 编号选择。"
                )
            entry = sink[index] if isinstance(sink[index], dict) else {}
            resolved_url = str(entry.get("url") or "").strip()
            referer = str(entry.get("source") or "").strip()
            source_title = str(entry.get("title") or "").strip()
            source_kind = "search_result"
        elif "://" not in resolved_url:
            raise ToolSoftError("url 必须是 [图N] 或完整的 http/https 图片直链。")
        else:
            source_title = ""
            source_kind = "public_url"

        from .image_fetch import fetch_image_urls, vet_images

        requested_name = str(args.get("filename") or "ppt-asset").strip()
        files, errors = await fetch_image_urls([{
            "url": resolved_url,
            "name": requested_name,
            "referer": referer,
        }])
        if not files:
            detail = "；".join(errors[:3]) or "没有取得有效图片字节"
            raise ToolSoftError(f"PPT 素材下载失败：{detail}")
        files, rejected = await vet_images(
            files,
            newapi_key or "",
            semantic_requirement=(
                photo_requirement_brief
                if photo_requirement_mode == "searched_photos" else ""
            ),
        )
        if not files:
            detail = "；".join(f"{name}：{reason}" for name, reason in rejected[:3])
            raise ToolSoftError(f"图片不适合作为 PPT 素材：{detail or '视觉质量检查未通过'}")

        filename, data = next(iter(files.items()))
        media_dir = f"{STAGING_ROOT}/media"
        provenance_path = (
            f"{STAGING_ROOT}/{ppt_project_audit_runtime.ASSET_PROVENANCE_FILENAME}"
        )
        provenance_input = ".ppt-asset-provenance-entry.json"
        provenance_entry = {
            "filename": filename,
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_kind": source_kind,
            "source_url": resolved_url[:2048],
            "source_page": referer[:2048],
            "source_title": source_title[:500],
        }
        merge_provenance = (
            "import json,pathlib,sys;"
            "target=pathlib.Path(sys.argv[1]);"
            "entry=json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'));"
            "payload=json.loads(target.read_text(encoding='utf-8')) if target.exists() "
            "else {'version':1,'assets':[]};"
            "assets=[item for item in payload.get('assets',[]) "
            "if isinstance(item,dict) and item.get('filename')!=entry['filename']];"
            "assets.append(entry);"
            "target.write_text(json.dumps({'version':1,'assets':assets},"
            "ensure_ascii=False,indent=2),encoding='utf-8')"
        )
        command = (
            f"set -eu && mkdir -p {shlex.quote(media_dir)} && "
            f"cp -- {shlex.quote('/workspace/inputs/' + filename)} "
            f"{shlex.quote(media_dir + '/' + filename)} && "
            f"test -s {shlex.quote(media_dir + '/' + filename)} && "
            f"python3 -c {shlex.quote(merge_provenance)} "
            f"{shlex.quote(provenance_path)} "
            f"{shlex.quote('/workspace/inputs/' + provenance_input)}"
        )
        res = await sandbox_executor.execute_in_sandbox(
            command,
            language="bash",
            input_files={
                filename: data,
                provenance_input: json.dumps(
                    provenance_entry, ensure_ascii=False,
                ).encode("utf-8"),
            },
            collect_workspace=False,
            collect_outputs=False,
            migrate_outputs=False,
            timeout_ms=30_000,
            session_key=str(run_id or ""),
        )
        if res.error:
            raise ToolSoftError(f"PPT 素材注入环境失败：{res.error}")
        if not res.ok:
            detail = (res.stderr or res.stdout or "未知错误").strip()[:800]
            raise ToolSoftError(f"PPT 素材未能写入工程 media/：{detail}")
        _publish_state["fetched_assets"] = [
            item for item in _publish_state["fetched_assets"]
            if item.get("filename") != filename
        ]
        _publish_state["fetched_assets"].append(provenance_entry)
        if tool_meta_sink is not None:
            tool_meta_sink["fetch_ppt_asset"] = {
                "action": {"operation": "fetch_ppt_asset", "target": filename},
                "execution_ok": True,
                "path": f"{media_dir}/{filename}",
                "bytes": len(data),
                "asset_receipt": dict(provenance_entry),
            }
        progress_receipts = await _capture_project()
        return ToolValue(
            status="succeeded",
            model_content=(
                f"已将图片素材写入 {media_dir}/{filename}（{len(data)} 字节）。"
                f"在 .page 中用 `elementType: image` 和 `src: media/{filename}` 明确引用；"
                "只下载未引用不算完成。"
            ),
            ui={
                "summary": "已准备 PPT 图片素材",
                "detail": filename,
                "action": "获取演示文稿素材",
            },
            receipts=progress_receipts,
        )

    def _make_sync():
        return build_sync(
            user_id,
            thread_id=str(thread_id or ""),
            run_id=str(run_id or ""),
            revision_target=revision_target,
            selected_file_ids=_selected_file_ids,
            scope_to_thread=scope_to_thread,
            workspace_folder_id=workspace_folder_id,
        )

    async def _persist_saved(sync, changes, persist_enabled: bool) -> list:
        saved = await sync.persist(changes) if persist_enabled else []
        if persist_enabled and (getattr(sync, "persist_failed", None) or []):
            names = "、".join(str(n) for n in list(sync.persist_failed)[:5])
            raise ToolSoftError(
                f"产物未能写入「我的文件」：{names}。"
                "这不是命令本身成功——用户侧没有拿到文件。请根据错误调整后重试，"
                "不要向用户宣称已保存。"
            )
        return saved

    def _publish_bash_meta(saved, command: str, res, truncated: bool = False) -> None:
        if tool_meta_sink is None:
            return
        from app.services.files import deliverable as _deliverable
        for row in saved or []:
            if isinstance(row, dict):
                row["origin"] = _file_origin("bash", run_id)
        visible = _deliverable.filter_rows(saved or [])
        review = getattr(res, "review", None) or {}
        tool_meta_sink["bash"] = {
            "files": visible,
            "action": {
                "operation": "bash",
                "target": (
                    f"沙箱命令（{len(visible)} 个产物）"
                    if visible else str(command or "")[:240]
                ),
            },
            "exit_code": getattr(res, "exit_code", None),
            "execution_ok": bool(getattr(res, "ok", False)),
            "review_status": review.get("status") if isinstance(review, dict) else None,
            "truncated": bool(getattr(res, "truncated", False) or truncated),
        }
        if len(visible) < len(saved or []):
            tool_meta_sink["bash"]["process_files"] = [
                str(r.get("filename") or r.get("name") or "")
                for r in (saved or [])
                if isinstance(r, dict)
                and str(r.get("filename") or r.get("name") or "")
                and r not in visible
            ]

    def _persist_notes(sync, persist_enabled: bool, res, saved) -> List[str]:
        parts: List[str] = []
        if persist_enabled:
            note = sync.notice() if hasattr(sync, "notice") else ""
            if note:
                parts.append(note)
            pn = sync.persist_notice() if hasattr(sync, "persist_notice") else ""
            if pn:
                parts.append(pn)
        if getattr(res, "workspace_deleted", None):
            gone = "、".join(res.workspace_deleted[:5])
            more = f" 等 {len(res.workspace_deleted)} 个" if len(res.workspace_deleted) > 5 else ""
            parts.append(
                f"（⚠️ {gone}{more} 在沙箱里被删除或改名了，但**用户「我的文件」里的原文件"
                "原封不动**——回写只同步新增和修改，删除/改名同步不回去。"
                "不要告诉用户文件已删除或已重命名；确实要删请让用户自己在「我的文件」里删。）"
            )
        if getattr(res, "workspace_oversized", None):
            big = "、".join(res.workspace_oversized[:5])
            parts.append(
                f"（{big} 超过单文件大小上限，**没有存进「我的文件」**。"
                "请把产物拆小，或只交付必要的那一份。）"
            )
        migrated = list(getattr(res, "outputs_migrated", None) or [])
        conflicts = list(getattr(res, "outputs_conflicts", None) or [])
        if migrated or conflicts:
            bits = []
            if migrated:
                bits.append(
                    f"{'、'.join(migrated[:8])} 是写在 /workspace/outputs/ 下的，"
                    "**那个目录不会交付给用户**，已自动移到 /workspace/files/ 并按上面的结果落库"
                )
            if conflicts:
                bits.append(
                    f"{'、'.join(conflicts[:8])} 同样写在 /workspace/outputs/ 下，"
                    "但 /workspace/files/ 里已有同名文件，**没有移动、也没有交付**"
                    "（不擅自覆盖用户已有文件）；要交付请自己换个名字复制过去"
                )
            parts.append("（" + "；".join(bits) + "。以后请直接写 /workspace/files/。）")
        if saved:
            names = [str(r.get("filename") or "") for r in saved if r.get("filename")]
            parts.append(f"（已保存到「我的文件」：{'、'.join(names[:10])}）")
        return parts

    async def _handle_job(action: str, job_id: str) -> ToolValue:
        from app.services.sandbox import session_pool as _pool
        from app.services.sandbox.base import SandboxNotSupported

        sess = _pool.peek(str(run_id or ""))
        if sess is None or getattr(sess, "sandbox", None) is None:
            raise ToolSoftError("当前没有可查询的沙箱作业（会话沙箱不在）。")
        job_id = job_id or str(getattr(sess, "last_job_id", "") or "")
        if not job_id:
            raise ToolSoftError("没有正在跟踪的后台作业。先跑一条长命令，或把 job_id 写在 `job status <id>` 里。")
        sandbox = sess.sandbox
        job_meta = dict((getattr(sess, "jobs", None) or {}).get(job_id) or {})
        try:
            if action == "cancel":
                await sandbox.interrupt(job_id)
                return ToolValue(
                    status="succeeded",
                    model_content=f"已请求取消后台作业 {job_id}。用 `job status {job_id}` 确认是否已停。",
                    ui={"summary": "已取消后台作业", "detail": job_id, "action": "执行命令并核对结果"},
                )
            if action == "logs":
                cursor = job_meta.get("log_cursor")
                logs = await sandbox.get_job_logs(job_id, cursor=cursor)
                text, new_cursor = _job_logs_payload(logs)
                if job_id in (getattr(sess, "jobs", None) or {}) and new_cursor is not None:
                    sess.jobs[job_id]["log_cursor"] = new_cursor
                shown = _tail_log(text) if text else "（暂无日志）"
                return ToolValue(
                    status="succeeded",
                    model_content=f"作业 {job_id} 日志：\n{shown}",
                    ui={"summary": "后台作业日志", "detail": job_id, "action": "执行命令并核对结果"},
                )
            status = await sandbox.get_job_status(job_id)
        except SandboxNotSupported as exc:
            raise ToolSoftError(f"当前沙箱不支持后台作业：{exc}") from exc
        running = bool(getattr(status, "running", False))
        exit_code = getattr(status, "exit_code", None)
        error = str(getattr(status, "error", "") or "")
        if running and action != "wait":
            return ToolValue(
                status="succeeded",
                model_content=(
                    f"作业 {job_id} 仍在运行。"
                    f"用 `job logs {job_id}` 看输出，结束后会再收集 /workspace/tmp 与 files/。"
                ),
                ui={"summary": "后台作业运行中", "detail": job_id, "action": "执行命令并核对结果"},
            )
        if running and action == "wait":
            remain = remaining_tool_budget_s()
            deadline = remain if remain > 0 else 20.0
            waited = 0.0
            while waited < min(deadline, 25.0):
                await asyncio.sleep(1.0)
                waited += 1.0
                status = await sandbox.get_job_status(job_id)
                running = bool(getattr(status, "running", False))
                exit_code = getattr(status, "exit_code", None)
                if not running:
                    break
            if running:
                return ToolValue(
                    status="succeeded",
                    model_content=f"作业 {job_id} 仍在运行（已等待 {int(waited)}s）。继续 `job wait {job_id}`。",
                    ui={"summary": "后台作业仍在运行", "detail": job_id, "action": "执行命令并核对结果"},
                )
        ending = f"exit_code={exit_code}"
        if error:
            ending += f" error={error}"
        if not job_meta:
            return ToolValue(
                status="succeeded" if exit_code in (0, None) else "failed",
                model_content=f"作业 {job_id} 已结束（{ending}）。",
                ui={"summary": "后台作业已结束", "detail": job_id, "action": "执行命令并核对结果"},
            )
        sync = _make_sync()
        if sync and job_meta.get("file_write_state") is not None:
            try:
                sync.restore_write_state(job_meta["file_write_state"])
            except ValueError as exc:
                raise ToolSoftError(str(exc)) from exc
        elif sync and workspace_folder_id:
            raise ToolSoftError("后台作业缺少启动时的文件版本基线，无法安全回写，请重新执行任务")
        persist_enabled = bool(
            persist_outputs
            and not _artifact_profile_active()
            and bool(job_meta.get("collect_workspace"))
        )
        from types import SimpleNamespace as _NS
        collect = _NS(
            ok=True, error=None, workspace_changes=[], workspace_deleted=None,
            workspace_oversized=None, outputs_migrated=None, outputs_conflicts=None,
            review=None, truncated=False, exit_code=exit_code,
        )
        saved: list = []
        progress_receipts: list = []
        if persist_enabled:
            remain = remaining_tool_budget_s()
            timeout_ms = 15_000
            if remain > 0:
                timeout_ms = int(max(8.0, min(remain - 2.0, 25.0)) * 1000)
            # 必须用作业启动前保存的 files 基线。再跑一次 execute 默认会重新打基线，
            # 把后台作业刚写出来的文件当成「本来就有」，收集结果变空。
            collect = await sandbox_executor.execute_in_sandbox(
                "true",
                language="bash",
                collect_workspace=True,
                collect_outputs=False,
                migrate_outputs=bool(job_meta.get("migrate_outputs")),
                timeout_ms=timeout_ms,
                session_key=str(run_id or ""),
                workspace_baseline=dict(job_meta.get("files_baseline") or {}),
            )
            if getattr(collect, "error", None):
                raise ToolSoftError(f"（执行环境未能完成本次命令：{collect.error}）")
            saved = await _persist_saved(sync, collect.workspace_changes, persist_enabled)
            _publish_bash_meta(saved, f"job {job_id}", collect)
            capture_ms = None
            remain = remaining_tool_budget_s()
            if remain > 0:
                capture_ms = int(max(0.0, min(8.0, remain - 2.0)) * 1000)
            if capture_ms is None or capture_ms >= 2000:
                progress_receipts = await _capture_project(
                    persist_allowed=persist_enabled,
                    timeout_ms=capture_ms,
                )
        _pool.release_job(sess, job_id)
        parts = [f"作业 {job_id} 已结束（{ending}）。"]
        parts.extend(_persist_notes(sync, persist_enabled, collect, saved))
        arts = []
        for row in saved or []:
            fn = str(row.get("filename") or "").strip()
            if not fn:
                continue
            arts.append({
                "filename": fn,
                "file_id": str(row.get("id") or ""),
                "bytes": int(row.get("size") or row.get("bytes") or 0) or None,
                "path": f"/workspace/files/{fn}",
            })
        return ToolValue(
            status="succeeded" if exit_code in (0, None) else "failed",
            model_content=" ".join(part for part in parts if part).strip(),
            ui={"summary": "后台作业已结束", "detail": job_id, "action": "执行命令并核对结果"},
            artifacts=arts,
            receipts=progress_receipts,
        )

    async def _bash(args: dict) -> ToolValue:
        _require_loaded_explicit_ppt_skill()
        command = str(args.get("command") or "").strip()
        if not command:
            raise ToolSoftError("请给出要执行的命令（command）。")

        raw_timeout = args.get("timeout")
        try:
            timeout_s = float(raw_timeout) if raw_timeout not in (None, "") else _DEFAULT_TIMEOUT_S
        except (TypeError, ValueError):
            timeout_s = _DEFAULT_TIMEOUT_S
        if timeout_s <= 0:
            timeout_s = _DEFAULT_TIMEOUT_S
        # 下限 1 秒：适配器把毫秒整除成秒（`timeout {n}s`），0<timeout<1 会算出
        # `timeout 0s` —— 而 coreutils 规定 duration=0 是**不限时**，等于把容器内超时
        # 彻底关掉，进程一路跑到容器自毁，回执却写着"执行超时（0s）"。
        timeout_s = max(1.0, timeout_s)
        max_timeout = _max_timeout_s()
        capped = timeout_s > max_timeout
        timeout_s = min(timeout_s, max_timeout)
        job_action, job_id_arg = _parse_job_command(command)
        if job_action:
            return await _handle_job(job_action, job_id_arg)
        if workspace_folder_id:
            command = "cd /workspace/files &&\n" + command

        # 会话文件系统：只把本对话文件、用户明确选中件和修订目标镜像进 /workspace/files；是否回写由
        # persist_outputs 单独控制，scratch 只加载镜像、不收集和持久化变化。
        #
        # 镜像**改成增量**（2026-07-28）：不在这里 await prepare()，而是把加载器交给内核，
        # 由它在拿到会话锁之后带着容器当前的镜像记账回调（见 sandbox_executor.workspace_loader）。
        # 原先每次调用都重读整个文件区 + 逐个 put_archive 进容器，实测约 0.3 秒/文件、
        # 200 文件上限外推 60s/次，而这段时间**不在** bash 自己的超时预算内、却在主循环
        # 300s 墙钟内——光准备就能把一整轮吃掉。
        sync = _make_sync()

        async def _report_progress(stage: str, label: str, detail: Optional[dict] = None) -> None:
            if tool_progress_queue is None:
                return
            await tool_progress_queue.put({
                # call_id：消费方靠它精确归属（并发只读工具共用一条队列，见 base.py）
                "call_id": current_tool_call_id(),
                "name": "bash", "stage": stage, "label": label, "detail": detail or {},
            })

        skill_pkgs: list = []
        skill_resolve_failed = False
        if resolve_skill_packages is not None:
            try:
                skill_pkgs = await resolve_skill_packages() or []
            except Exception:  # noqa: BLE001 取包失败不阻断执行
                # 但**必须留痕**：解析器自己已经会把失败折成 unavailable 占位记录，
                # 走到这里说明连解析器都炸了。静默当"没有技能"是这条链最后一层沉默。
                logger.warning("bash 取 skill 包失败，本次不挂载技能文件", exc_info=True)
                skill_resolve_failed = True
        unavailable_pkgs = [p for p in skill_pkgs if _is_unavailable(p)]
        mounted_pkgs = [p for p in skill_pkgs if not _is_unavailable(p)]
        blocked_scripts = [p for p in unavailable_pkgs if p.get("hasScripts")]
        if ((blocked_scripts or skill_resolve_failed)
                and _blocked_by_missing_skill(command, blocked_scripts, mounted_pkgs)):
            # 明确失败优于"命令跑了、什么都没发生"（ADR-043）
            names = "、".join(
                str(p.get("name") or p.get("skillId") or "?") for p in blocked_scripts[:3]
            ) or "本轮技能"
            raise ToolSoftError(
                f"（技能《{names}》**本轮没有挂进沙箱**（平台取包失败），"
                "/workspace/skills/ 下没有它的文件，这条命令不会执行。"
                "不要重试同一条命令、也不要假装脚本已运行；"
                "改用你自己的工具能做多少做多少，做不了就如实告诉用户该技能暂时不可用。）"
            )

        # 死线内收（2026-07-28）：主循环到 TOOL_CALL_TIMEOUT_SECONDS 就 `task.cancel()`，
        # 那是**异常路径**——下面的 `sync.persist()` 根本不会执行，沙箱里已经产出的 PPT/文档
        # 一个都不落库，模型只收到「已中止本次调用」。而让沙箱**自己**按 timeout 终止命令
        # （exit 124）时，本函数正常返回，产物照常落库、回执还能明确说是超时。所以把命令的
        # 超时压进「距离被 cancel 还剩多久」之内，留 _COLLECT_RESERVE_S 给读回与落库。
        # `_max_timeout_s()` 是静态上限，这里是**动态**的：镜像/技能包准备已经花掉的时间
        # 静态上限看不见。
        budget = remaining_tool_budget_s()
        deadline_capped = 0
        background = False
        background_timeout_ms = None
        if budget > 0:
            room = budget - _COLLECT_RESERVE_S
            if room < timeout_s:
                background = True
                background_timeout_ms = int(timeout_s * 1000)
                timeout_s = max(5.0, room)
                deadline_capped = int(timeout_s)
        # inspect / Plan 调查 / Research 仍可把用户文件镜像进沙箱做只读分析，
        # 但 scratch 中的任何变化都不得回写用户文件区。
        persist_enabled = bool(
            persist_outputs and sync is not None and not _artifact_profile_active()
        )
        res = await sandbox_executor.execute_in_sandbox(
            command,
            language="bash",
            skill_packages=mounted_pkgs or None,
            workspace_loader=(sync.load if sync else None),
            collect_workspace=persist_enabled,
            progress_callback=_report_progress,
            # outputs/ 不走旧的产物收集链路（基线 diff + 产物门禁 + 单独落库），而是**搬进
            # files/** 后顺着统一文件系统落库：所有 execute_in_sandbox 时代写成的技能、GitHub 上现成的
            # 技能都硬编码 /workspace/outputs，而 _mkdir_cmd 无条件建了这个目录 —— 往那里写
            # 永远成功、永远不交付、回执还一个字不提。见 sandbox_executor._migrate_outputs。
            collect_outputs=False,
            migrate_outputs=persist_enabled,
            timeout_ms=int(timeout_s * 1000),
            background=background,
            background_timeout_ms=background_timeout_ms,
            task_brief=user_message or "",
            newapi_key=newapi_key or "",
            # 沙箱复用作用域按 Run：同一 Run 内每次 bash 共用一个容器，
            # 前一次解开的包、装好的东西下一次还在（见 session_pool）。
            session_key=str(run_id or ""),
            expected_user_images=_expected_user_images or None,
        )
        if res.error:
            # 沙箱层错误（provider 不可用/写文件失败）不是用户命令的输出，要如实标失败
            raise ToolSoftError(f"（执行环境未能完成本次命令：{res.error}）")
        if getattr(res, "job_id", None) and res.exit_code is None:
            job_id = str(res.job_id)
            if sync is not None:
                from app.services.sandbox import session_pool as _pool
                sess = _pool.peek(str(run_id or ""))
                if sess is not None and job_id in (getattr(sess, "jobs", None) or {}):
                    sess.jobs[job_id]["file_write_state"] = sync.export_write_state()
            return ToolValue(
                status="succeeded",
                model_content=(
                    f"命令已转为后台作业 job_id={job_id}，没有在本轮工具超时前强行取消。"
                    f"用 bash command=`job status {job_id}` 查询，"
                    f"`job logs {job_id}` 读增量日志，`job cancel {job_id}` 取消。"
                    "作业结束前不要宣称产物已保存。"
                ),
                ui={"summary": "后台作业已启动", "detail": job_id, "action": "执行命令并核对结果"},
            )

        saved = await _persist_saved(sync, res.workspace_changes, persist_enabled)
        capture_ms = None
        remain = remaining_tool_budget_s()
        if remain > 0:
            capture_ms = int(max(0.0, min(8.0, remain - 2.0)) * 1000)
        progress_receipts: list = []
        if res.ok and (capture_ms is None or capture_ms >= 2000):
            progress_receipts = await _capture_project(
                persist_allowed=persist_enabled,
                timeout_ms=capture_ms,
            )
        stdout, out_capped = _cap_lines(res.stdout or "")
        stderr, err_capped = _cap_lines(res.stderr or "")

        _publish_bash_meta(saved, command, res, truncated=bool(out_capped or err_capped))

        parts: List[str] = []
        # 技能挂载缺口先说（在 stdout 之前）：模型读回执是从上往下读的，而"技能没挂上"
        # 决定了它接下来该不该继续按 SKILL.md 走——排在几百行 stdout 后面等于没说。
        if skill_resolve_failed and not _skill_notice_state["resolve_failed_reported"]:
            _skill_notice_state["resolve_failed_reported"] = True
            parts.append(
                "（⚠️ 本轮**技能包解析失败，/workspace/skills/ 是空的**——系统提示词里那句"
                "「脚本已挂在沙箱内」本轮不成立。不要执行任何技能脚本、也不要假装执行过。）"
            )
        gap_note = (_unavailable_note(unavailable_pkgs, _skill_notice_seen)
                    + _unmounted_note(mounted_pkgs, _skill_notice_seen)).strip()
        if gap_note:
            parts.append(gap_note)
        if persist_enabled:
            note = sync.notice()
            if note:
                parts.append(note)
        if persist_enabled:
            pn = sync.persist_notice()
            if pn:
                parts.append(pn)
        if getattr(res, "workspace_deleted", None):
            # 回写只认「执行后清单里有什么」，删掉的文件不在清单里 → 沙箱里的删除/改名
            # **永远同步不回用户文件区**。不说这句，模型 rm 完看到 exit 0 就会向用户宣布
            # "已删除"，而用户打开「我的文件」文件还在——平台替模型撒了谎。
            gone = "、".join(res.workspace_deleted[:5])
            more = f" 等 {len(res.workspace_deleted)} 个" if len(res.workspace_deleted) > 5 else ""
            parts.append(
                f"（⚠️ {gone}{more} 在沙箱里被删除或改名了，但**用户「我的文件」里的原文件"
                "原封不动**——回写只同步新增和修改，删除/改名同步不回去。"
                "不要告诉用户文件已删除或已重命名；确实要删请让用户自己在「我的文件」里删。）"
            )
        if getattr(res, "workspace_oversized", None):
            # 超过单文件上限的变更连读回都没做（读了也必然 413）。不说 = 模型以为存上了。
            big = "、".join(res.workspace_oversized[:5])
            parts.append(
                f"（{big} 超过单文件大小上限，**没有存进「我的文件」**。"
                "请把产物拆小，或只交付必要的那一份。）"
            )
        migrated = list(getattr(res, "outputs_migrated", None) or [])
        conflicts = list(getattr(res, "outputs_conflicts", None) or [])
        if migrated or conflicts:
            # 写 outputs/ 是 execute_in_sandbox 时代的习惯，现成技能里到处都是。搬过去只解决这一次，
            # 不说清楚它下次还写那儿——而 outputs/ 里的东西默认永远不交付。
            bits = []
            if migrated:
                bits.append(
                    f"{'、'.join(migrated[:8])} 是写在 /workspace/outputs/ 下的，"
                    "**那个目录不会交付给用户**，已自动移到 /workspace/files/ 并按上面的结果落库"
                )
            if conflicts:
                bits.append(
                    f"{'、'.join(conflicts[:8])} 同样写在 /workspace/outputs/ 下，"
                    "但 /workspace/files/ 里已有同名文件，**没有移动、也没有交付**"
                    "（不擅自覆盖用户已有文件）；要交付请自己换个名字复制过去"
                )
            parts.append("（" + "；".join(bits) + "。以后请直接写 /workspace/files/。）")
        if saved:
            names = [str(r.get("filename") or "") for r in saved if r.get("filename")]
            parts.append(f"（已保存到「我的文件」：{'、'.join(names[:10])}）")
        # 死线优先于静态上限：两条同时成立时，**真正卡住这次执行的是死线**，
        # 只报静态上限会让模型以为「调小一点就行」，而它下一次照样撞死线。
        if deadline_capped:
            # 说清是**本轮剩余时间**不够，而不是工具上限——否则模型下一次还照原样调大 timeout
            parts.append(
                f"（本轮剩余执行时间不足，本次超时按 {deadline_capped}s 执行。"
                "还需要更长时间的话，把工作拆成几条命令分次执行。）"
            )
        elif capped:
            parts.append(f"（请求的超时超过上限，已按 {max_timeout}s 执行）")
        if stdout.strip():
            parts.append(stdout)
        if stderr.strip():
            parts.append(f"[stderr]\n{stderr}")
        if bool(getattr(res, "timed_out", False)) or res.exit_code == 124 or getattr(res, "termination_reason", None) == "timeout":
            # 124 是 coreutils `timeout` 的专属退出码。不点名的话模型只看到"非零状态结束、
            # 请根据上面的输出修正"，而超时路径的输出往往已经被丢弃——它会把超时当语法错误
            # 反复瞎改同一条命令。
            parts.append(
                f"命令**执行超时**被终止（{int(timeout_s)}s 上限，exit_code={res.exit_code}）——这不是命令写错了。"
                f"要么把 timeout 调大（上限 {max_timeout}s），要么把工作拆成几条命令分次执行；"
                "长时间无输出的命令请让它定期 print 进度。"
            )
        elif getattr(res, "termination_reason", None) == "network_error":
            parts.append(
                "命令因**网络故障**失败（termination_reason=network_error）——"
                "这不是脚本语法错误，不要反复改同一条命令。"
            )
        elif getattr(res, "termination_reason", None) == "cancelled":
            parts.append("命令已被取消（termination_reason=cancelled），不要当成脚本写错。")
        elif res.exit_code == 153:
            # 128+SIGXFSZ：ulimit -f 单文件配额。裸报 153 模型完全无从下手
            parts.append(
                "命令因**单个文件写得太大**被内核终止（exit_code=153/SIGXFSZ）。"
                "检查是不是在往一个文件里无限追加，或产物本身超出了沙箱单文件上限。"
            )
        elif not res.ok:
            parts.append(
                f"命令以非零状态结束（exit_code={res.exit_code}）。"
                "请根据上面的输出修正后重试，不要在没看懂错误前重复同一条命令。"
            )
        elif not stdout.strip() and not stderr.strip():
            parts.append(f"命令执行成功，无输出（exit_code={res.exit_code}）。")
        if res.truncated or out_capped or err_capped:
            parts.append("（输出已按上限截断）")
        # 质检结论必须**用自然语言讲给模型**，不能只拼机读标记。只有标记的话，一个
        # 0 页 / 打不开的 PPT，模型收到的回执里一个字都不提它有问题——控制器把它推回来返工，
        # 它却不知道要修什么，只能原样再跑一遍。
        review = res.review or {}
        review_status = str(review.get("status") or "")
        if review_status in ("failed", "warning"):
            detail = str(review.get("summary") or "").strip()
            if not detail:
                items = [i for i in (review.get("items") or []) if isinstance(i, dict)]
                detail = "；".join(
                    f"{i.get('name') or '产物'}：{i.get('message') or i.get('status')}"
                    for i in items[:5]
                )
            lead = "产物可用性检查**未通过**" if review_status == "failed" else "产物可用性检查有提示"
            parts.append(f"（{lead}：{detail or '未给出细节'}）")

        # 只报告实际产物的客观检查结果。不要依据用户文字推导“PPT 必须存在”闸门：
        # 是否需要文件、用什么生成栈、何时交付分别由冻结 ToolSpec、显式/模型选择的 Skill
        # 和 Completion Verifier 决定；普通 bash 调用不能因为关键词而伪造失败。
        gate_status = review_status
        text = "\n".join(parts)
        # 产物有效性门禁（批 3）：sandbox_executor 对 files/ 里新产出的 pptx/docx/xlsx/pdf/html
        # 跑了硬校验，这里把状态拼成 [artifact_validity_gate=...] 标记。**控制器读这个标记**
        # 驱动 LoopState 的质量返工——不是给模型读的自然语言（那一半在上面），
        # 标记本身带进程级 nonce 防 stdout 伪造，见 base.py。
        gated = _with_validity_gate(text, gate_status)
        # P1.6：bash 也返回结构化 observation；model_content 仍带 validity gate 供控制器解析。
        arts = []
        for row in (saved or []):
            fn = str(row.get("filename") or "").strip()
            if not fn:
                continue
            arts.append({
                "filename": fn,
                "file_id": str(row.get("id") or ""),
                "bytes": int(row.get("size") or row.get("bytes") or 0) or None,
                "path": f"/workspace/files/{fn}",
            })
        status = "succeeded" if getattr(res, "ok", False) else "failed"
        if gate_status == "failed":
            status = "failed"
        public_detail_parts: List[str] = []
        if stdout.strip():
            public_detail_parts.append(stdout)
        if stderr.strip():
            public_detail_parts.append(f"[stderr]\n{stderr}")
        if not public_detail_parts:
            public_detail_parts.append(
                "命令执行成功，无输出。" if status == "succeeded" else f"命令未成功（exit_code={res.exit_code}）。"
            )
        if arts:
            public_detail_parts.append(
                "已保存：" + "、".join(str(item.get("filename") or "") for item in arts[:5])
            )
        public_detail = "\n".join(part for part in public_detail_parts if part).strip()[:500]
        return ToolValue(
            status=status,
            model_content=gated,
            ui={
                "summary": "已运行命令" if status == "succeeded" else "命令未成功",
                # 用户可见预览只展示本次命令事实；文件镜像/技能挂载警告仍保留在 model_content。
                "detail": public_detail,
                "action": "bash",
            },
            artifacts=arts,
            receipts=progress_receipts,
            error=(
                {"code": "bash_failed", "message": text[:1000], "retryable": True}
                if status == "failed" else None
            ),
        )

    async def _publish_ppt_artifact(args: dict) -> ToolValue:
        if not _artifact_profile_active():
            raise ToolSoftError("当前 Run 不是 artifact_coding PPT Profile，不能调用发布工具。")
        _require_loaded_explicit_ppt_skill()
        if not user_id:
            raise ToolSoftError("缺少用户文件区，无法发布 PPT 产物。")

        root = PurePosixPath(STAGING_ROOT)

        def _inside_staging(value: object, *, label: str) -> str:
            raw = str(value or "").strip()
            path = PurePosixPath(raw)
            if not raw or not path.is_absolute() or (path != root and root not in path.parents):
                raise ToolSoftError(f"{label} 必须位于 {STAGING_ROOT} 下。")
            if ".." in path.parts:
                raise ToolSoftError(f"{label} 不能包含 .. 路径。")
            return str(path)

        project_dir = _inside_staging(
            args.get("project_dir") or STAGING_ROOT, label="project_dir",
        )
        pptx_path = _inside_staging(args.get("pptx_path"), label="pptx_path")
        if not pptx_path.lower().endswith(".pptx"):
            raise ToolSoftError("pptx_path 必须指向 .pptx 文件。")
        filename = PurePosixPath(str(args.get("filename") or "").strip()).name
        if not filename or filename in {".", ".."} or not filename.lower().endswith(".pptx"):
            raise ToolSoftError("filename 必须是安全的 .pptx 文件名。")
        if filename != str(args.get("filename") or "").strip():
            raise ToolSoftError("filename 只能是文件名，不能包含目录。")
        final_pptx = f"/workspace/files/{filename}"
        explicit_photo_requirement = photo_requirement_mode == "searched_photos"
        images_opted_out = photo_requirement_mode == "none"
        require_images = bool(not images_opted_out and explicit_photo_requirement)
        require_photo_provenance = bool(not images_opted_out and explicit_photo_requirement)
        min_images = max(
            profile_min_images,
            _requested_ppt_image_count(context_brief),
        )
        min_photo_sources = profile_min_sources
        fetched_receipts = list(_publish_state["fetched_assets"])
        expected_assets_json = json.dumps(
            [item["filename"] for item in fetched_receipts], ensure_ascii=False,
        )
        trusted_photo_assets_json = json.dumps(
            fetched_receipts, ensure_ascii=False,
        )
        user_photo_assets_json = json.dumps(
            list(_expected_user_images), ensure_ascii=False,
        )
        trusted_python = (
            f"PYTHONPATH=/workspace/inputs/{_PYYAML_RUNTIME_FILENAME} python3 -P"
        )

        validate = (
            "import pathlib,sys,zipfile;"
            "pptx=pathlib.Path(sys.argv[1]);project=pathlib.Path(sys.argv[2]);"
            "z=zipfile.ZipFile(pptx);bad=z.testzip();"
            "slides=[n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')];"
            "pages=list((project/'pages').glob('*.page'));manifests=list(project.glob('*.pptd'));"
            "assert bad is None, f'PPTX ZIP contains corrupt member: {bad}';"
            "assert len(manifests)==1, f'expected exactly one .pptd, got {len(manifests)}';"
            "assert pages, 'PPTD project has no .page files';"
            "assert slides, 'PPTX has no slides';"
            "assert len(slides)==len(pages), f'page count mismatch: pptx={len(slides)} pptd={len(pages)}'"
        )
        command = " && ".join([
            "set -eu",
            f"test -d {shlex.quote(project_dir)}",
            f"test -f {shlex.quote(pptx_path)}",
            (f"{trusted_python} -c {shlex.quote(validate)} {shlex.quote(pptx_path)} "
             f"{shlex.quote(project_dir)}"),
            f"{trusted_python} -c {shlex.quote(_PPTD_LAYOUT_LINT)} {shlex.quote(project_dir)}",
            (
                f"{trusted_python} -c {shlex.quote(_PPT_PROJECT_AUDIT)} "
                f"{shlex.quote(project_dir)} "
                f"--pptx-path {shlex.quote(pptx_path)} "
                "--check-installed-fonts "
                + ("--require-images " if require_images else "")
                + ("--require-photo-provenance " if require_photo_provenance else "")
                + (f"--min-images {min_images} " if min_images else "")
                + (f"--min-photo-sources {min_photo_sources} " if min_photo_sources else "")
                + f"--expected-assets-json {shlex.quote(expected_assets_json)} "
                + f"--trusted-photo-assets-json {shlex.quote(trusted_photo_assets_json)} "
                + f"--user-photo-assets-json {shlex.quote(user_photo_assets_json)} "
                + "--user-assets-root /workspace/files"
            ),
            f"cp -- {shlex.quote(pptx_path)} {shlex.quote(final_pptx)}",
        ])
        sync = build_sync(
            user_id, thread_id=str(thread_id or ""), run_id=str(run_id or ""),
            revision_target=revision_target,
            selected_file_ids=_selected_file_ids,
            scope_to_thread=scope_to_thread,
            workspace_folder_id=workspace_folder_id,
        )
        if sync is None:
            raise ToolSoftError("用户文件同步服务不可用，无法发布 PPT 产物。")
        res = await sandbox_executor.execute_in_sandbox(
            command,
            language="bash",
            input_files={_PYYAML_RUNTIME_FILENAME: _pyyaml_runtime_zip()},
            workspace_loader=sync.load,
            collect_workspace=True,
            collect_outputs=False,
            migrate_outputs=False,
            timeout_ms=min(_max_timeout_s(), 300) * 1000,
            task_brief=user_message or "",
            newapi_key=newapi_key or "",
            session_key=str(run_id or ""),
            expected_user_images=_expected_user_images or None,
            run_visual_review=False,
        )
        if res.error:
            raise ToolSoftError(f"PPT 发布环境失败：{res.error}")
        if not res.ok:
            detail = (res.stderr or res.stdout or "未知错误").strip()[:1200]
            raise ToolSoftError(f"PPT 发布前结构校验失败：{detail}")
        # PPTD remains the internal editable/QA source.  User-visible delivery is
        # deliberately one file, so a stale or incidental workspace change can
        # never reintroduce a source archive into My Files.
        publish_changes = [
            item for item in (res.workspace_changes or [])
            if isinstance(item, dict)
            and PurePosixPath(str(item.get("path") or "")).name.lower() == filename.lower()
        ]
        saved = await sync.persist(publish_changes)
        if getattr(sync, "persist_failed", None):
            names = "、".join(str(name) for name in list(sync.persist_failed)[:5])
            raise ToolSoftError(f"PPT 已生成但发布到『我的文件』失败：{names}")
        pptx_rows = [
            row for row in saved
            if str(row.get("filename") or "").lower() == filename.lower()
        ]
        if not pptx_rows:
            raise ToolSoftError("发布回执中没有目标 PPTX，不能宣称已交付。")
        from app.services.files import deliverable as _deliverable
        for row in saved:
            row["origin"] = _file_origin("publish_ppt_artifact", run_id)
        visible = _deliverable.filter_rows(saved)
        if tool_meta_sink is not None:
            tool_meta_sink["publish_ppt_artifact"] = {
                "files": visible,
                "action": {"operation": "publish_ppt_artifact", "target": filename},
                "execution_ok": True,
                "review_status": "passed",
            }
        artifacts = [
            {
                "filename": str(row.get("filename") or ""),
                "file_id": str(row.get("id") or ""),
                "bytes": int(row.get("size") or row.get("bytes") or 0) or None,
                "path": f"/workspace/files/{row.get('filename')}",
            }
            for row in saved if row.get("filename")
        ]
        return ToolValue(
            status="succeeded",
            model_content=_with_validity_gate(
                f"PPT 已通过结构校验并发布：{filename}。",
                "passed",
            ),
            ui={"summary": "PPT 已发布", "detail": filename, "action": "发布演示文稿"},
            artifacts=artifacts,
        )

    async def _project_exec(
        script: str,
        *,
        input_files: Optional[dict] = None,
        timeout_ms: int = 20_000,
    ):
        result = await sandbox_executor.execute_in_sandbox(
            script,
            language="bash",
            input_files=input_files,
            collect_outputs=False,
            collect_workspace=False,
            migrate_outputs=False,
            timeout_ms=timeout_ms,
            session_key=str(run_id or ""),
        )
        if result.error:
            raise ToolSoftError(f"（工程读写环境失败：{result.error}）")
        return result

    async def _capture_project(
        persist_allowed: bool = True,
        timeout_ms: Optional[int] = None,
    ) -> list[dict]:
        if not _artifact_profile_active():
            # scratch / Research / Plan 调查不得把临时执行写进会话工作区。
            if not persist_allowed or not persist_outputs:
                return []
            if not (user_id and run_id and thread_id):
                return []
            try:
                from app.services.agent_harness.artifact_checkpoint import capture_work_staging
                await capture_work_staging(
                    run_id=str(run_id),
                    thread_id=str(thread_id or ""),
                    user_id=str(user_id),
                    timeout_ms=timeout_ms,
                )
            except Exception:  # noqa: BLE001
                logger.info("work staging capture skipped run=%s", run_id, exc_info=True)
            return []
        unavailable = [ppt_project_progress(None)]
        if not (user_id and run_id):
            return unavailable
        try:
            from app.services.agent_harness.artifact_checkpoint import capture_ppt_staging
            captured = await capture_ppt_staging(
                run_id=str(run_id),
                thread_id=str(thread_id or ""),
                user_id=str(user_id),
                force=True,
            )
            progress = (captured or {}).get("progress_receipt")
            if isinstance(progress, dict):
                return [progress]
        except Exception:  # noqa: BLE001
            logger.info("PPT staging capture after project file tool skipped run=%s", run_id, exc_info=True)
        return unavailable

    def _rel_or_raise(raw: str) -> str:
        try:
            return ppt_project_relpath(raw)
        except ValueError as exc:
            raise ToolSoftError(str(exc)) from None

    async def _read_project(args: dict) -> ToolValue:
        if not _artifact_profile_active():
            raise ToolSoftError("当前 Run 不是 PPT 工程，read_file 不能用于「我的文件」。")
        _require_loaded_explicit_ppt_skill()
        rel = _rel_or_raise(str(args.get("path") or ""))
        if ppt_project_is_image(rel):
            src = rel if rel.lower().startswith("media/") else f"media/{rel.rsplit('/', 1)[-1]}"
            return ToolValue(
                model_content=(
                    f"{rel} 是二进制图片，不要 dump 字节。"
                    f"在对应 .page 里用 `elementType: image` 和 `src: {src}` 引用。"
                ),
            )
        if not ppt_project_is_text(rel):
            raise ToolSoftError(
                f"{rel} 不是可编辑的工程文本（.page / .pptd / DESIGN.md）。"
            )
        abs_path = f"{STAGING_ROOT}/{rel}"
        try:
            offset = int(args.get("offset") or 0)
        except (TypeError, ValueError):
            offset = 0
        limit = args.get("limit")
        try:
            limit_n = None if limit in (None, "") else int(limit)
        except (TypeError, ValueError):
            limit_n = None
        script = (
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            f"p = Path({abs_path!r})\n"
            "if not p.is_file():\n"
            "    raise SystemExit('MISSING')\n"
            "text = p.read_text(encoding='utf-8')\n"
            "lines = text.splitlines()\n"
            f"offset = {max(0, offset)}\n"
            f"limit_n = {limit_n!r}\n"
            "if offset:\n"
            "    lines = lines[offset:]\n"
            "if limit_n is not None:\n"
            "    lines = lines[:max(0, int(limit_n))]\n"
            "print('\\n'.join(lines))\n"
            "PY\n"
        )
        res = await _project_exec(script)
        combined = f"{res.stderr or ''}\n{res.stdout or ''}"
        if "MISSING" in combined:
            raise ToolSoftError(
                f"工程里没有 {rel}。先 glob，或用 write_file 新建 pages/*.page。"
            )
        if not res.ok:
            raise ToolSoftError(f"读取 {rel} 失败：{combined.strip()[:800] or '未知错误'}")
        stdout = str(res.stdout or "")
        if len(stdout) > 80_000:
            stdout = stdout[:80_000] + "\n…(truncated)"
        return ToolValue(model_content=stdout or "(空文件)")

    async def _write_project(args: dict) -> ToolValue:
        if not _artifact_profile_active():
            raise ToolSoftError("当前 Run 不是 PPT 工程，write_file 不会写入「我的文件」。")
        _require_loaded_explicit_ppt_skill()
        rel = _rel_or_raise(str(args.get("path") or ""))
        if not ppt_project_is_text(rel):
            raise ToolSoftError(
                f"只能把文本写入工程文件（.page / .pptd / DESIGN.md），不能写 {rel}。"
                "图片用 fetch_ppt_asset 或用户放入工作区的照片。"
            )
        content = args.get("content")
        if content is None:
            raise ToolSoftError("请给出要写入的内容（content）。缺内容时不写空文件覆盖。")
        text = content if isinstance(content, str) else str(content)
        if len(text) > _PPT_WRITE_MAX:
            raise ToolSoftError(f"单次写入不能超过 {_PPT_WRITE_MAX} 字符。拆成多次 edit_file。")
        abs_path = f"{STAGING_ROOT}/{rel}"
        parent = str(PurePosixPath(abs_path).parent)
        script = (
            f"set -eu && mkdir -p {shlex.quote(parent)} && "
            f"cp -- {shlex.quote('/workspace/inputs/ppt-write.txt')} "
            f"{shlex.quote(abs_path)} && test -f {shlex.quote(abs_path)} && "
            f"echo WRITE_OK"
        )
        res = await _project_exec(
            script,
            input_files={"ppt-write.txt": text.encode("utf-8")},
        )
        if not res.ok or "WRITE_OK" not in str(res.stdout or ""):
            detail = (res.stderr or res.stdout or "未知错误").strip()[:800]
            raise ToolSoftError(f"未能写入 {rel}：{detail}")
        progress_receipts = await _capture_project()
        return ToolValue(
            model_content=(
                f"已写入工程 {rel}（{len(text.encode('utf-8'))} 字节）。"
                "这不是「我的文件」。导出仍用 bash 跑 run_export.py --force，"
                "交付仍调用 publish_ppt_artifact。"
            ),
            ui={"summary": f"已写入 {rel}", "action": "写入 PPT 工程"},
            receipts=progress_receipts,
        )

    async def _edit_project(args: dict) -> ToolValue:
        if not _artifact_profile_active():
            raise ToolSoftError("当前 Run 不是 PPT 工程，edit_file 不会改「我的文件」。")
        _require_loaded_explicit_ppt_skill()
        rel = _rel_or_raise(str(args.get("path") or ""))
        if not ppt_project_is_text(rel):
            raise ToolSoftError(f"只能编辑工程文本文件，不能编辑 {rel}。")
        old = args.get("old_string")
        new = args.get("new_string")
        if not isinstance(old, str) or old == "":
            raise ToolSoftError("old_string 不能为空——要整体覆写请用 write_file。")
        if not isinstance(new, str):
            raise ToolSoftError("请给出 new_string。")
        if old == new:
            raise ToolSoftError("old_string 与 new_string 相同，这次编辑没有任何效果。")
        abs_path = f"{STAGING_ROOT}/{rel}"
        payload = json.dumps(
            {"path": abs_path, "old": old, "new": new},
            ensure_ascii=False,
        )
        script = (
            "python3 - <<'PY'\n"
            "import json, pathlib, sys\n"
            "spec = json.loads(pathlib.Path('/workspace/inputs/ppt-edit.json').read_text(encoding='utf-8'))\n"
            "p = pathlib.Path(spec['path'])\n"
            "if not p.is_file():\n"
            "    raise SystemExit('MISSING')\n"
            "text = p.read_text(encoding='utf-8')\n"
            "old, new = spec['old'], spec['new']\n"
            "count = text.count(old)\n"
            "if count == 0:\n"
            "    raise SystemExit('MISS')\n"
            "if count > 1:\n"
            "    raise SystemExit('AMBIGUOUS:' + str(count))\n"
            "p.write_text(text.replace(old, new, 1), encoding='utf-8')\n"
            "print('EDIT_OK')\n"
            "PY\n"
        )
        res = await _project_exec(
            script,
            input_files={"ppt-edit.json": payload.encode("utf-8")},
        )
        blob = (str(res.stdout or "") + "\n" + str(res.stderr or "")).strip()
        if "MISSING" in blob:
            raise ToolSoftError(f"工程里没有 {rel}。先 glob 或 write_file 新建。")
        if "MISS" in blob:
            raise ToolSoftError(f"{rel} 中找不到 old_string，没有改动。先 read_file 再编辑。")
        if "AMBIGUOUS" in blob:
            raise ToolSoftError(f"{rel} 中 old_string 出现多次，请提供更长的唯一片段。")
        if not res.ok or "EDIT_OK" not in str(res.stdout or ""):
            raise ToolSoftError(f"未能编辑 {rel}：{blob[:800] or '未知错误'}")
        progress_receipts = await _capture_project()
        return ToolValue(
            model_content=f"已更新工程 {rel}。导出仍用 run_export.py --force。",
            ui={"summary": f"已编辑 {rel}", "action": "编辑 PPT 工程"},
            receipts=progress_receipts,
        )

    async def _glob_project(args: dict) -> ToolValue:
        if not _artifact_profile_active():
            raise ToolSoftError("当前 Run 不是 PPT 工程，glob 不能列「我的文件」。")
        _require_loaded_explicit_ppt_skill()
        pattern = str(args.get("pattern") or "*").strip() or "*"
        if ".." in pattern.replace("\\", "/").split("/"):
            raise ToolSoftError("glob 模式非法。")
        script = (
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            "import fnmatch\n"
            f"root = Path({STAGING_ROOT!r})\n"
            f"pattern = {pattern!r}\n"
            "if not root.exists():\n"
            "    print('EMPTY')\n"
            "    raise SystemExit(0)\n"
            "hits = []\n"
            "for path in sorted(root.rglob('*')):\n"
            "    if not path.is_file():\n"
            "        continue\n"
            "    rel = path.relative_to(root).as_posix()\n"
            "    name = path.name\n"
            "    if rel.startswith('.') or '/.' in f'/{rel}':\n"
            "        continue\n"
            "    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):\n"
            "        hits.append(rel)\n"
            "print('\\n'.join(hits[:80]) if hits else 'EMPTY')\n"
            "PY\n"
        )
        res = await _project_exec(script)
        stdout = str(res.stdout or "").strip()
        if not res.ok:
            raise ToolSoftError(f"无法列出工程文件：{(res.stderr or stdout)[:800]}")
        if stdout == "EMPTY" or not stdout:
            return ToolValue(
                model_content=(
                    f"{STAGING_ROOT} 目前是空的。用 write_file 写 DESIGN.md 和 pages/*.page；"
                    "用户放入工作区的照片应在 media/。"
                ),
            )
        return ToolValue(model_content="工程文件：\n" + stdout)

    # 文件区镜像是**唯一形态**（2026-07-29）：`build_sync` 只在没有 user_id 时返回 None，
    # 所以这里也只判 user_id。描述与实际落库行为必须严格同源——工具描述说错比缺失更坏
    # （模型会按它去绕路，把产物写到一个永远不会被回收的目录里还宣布已交付）。
    sync_on = bool(user_id and persist_outputs)
    files_desc = (
        "• 本对话工作区、用户在 + 中明确选中的文件和修订目标会镜像到 /workspace/files/ 供读取；本轮普通 bash 对该目录的"
        "新增和修改**不会直接落库**。请在 staging 完成 PPTD、导出和检查，最后调用 "
        "publish_ppt_artifact 一次性发布，禁止用 cp/mv 绕过发布门禁。\n"
        if sync_on and _artifact_profile else
        "• 本对话工作区与用户明确选中的文件已镜像到 **/workspace/files/**（不会自动暴露整个「我的文件」；注意 bash 的工作目录是 /workspace，"
        "所以访问要写 `/workspace/files/<名>` 或先 cd 进去——直接写文件名会找不到）；可直接读写；"
        "**写在这里就等于已保存**（本次改动/新建的文件执行后自动落库，进版本历史）。\n"
        "• ⚠️ 同步是**单向**的：新增和修改会回写，**删除和改名不会**——在 files/ 里 rm 或 mv 掉"
        "一个文件，用户「我的文件」里的原文件仍然在。所以不要用 bash 替用户删文件，"
        "也不要在删/改名后宣布已删除；要删请告诉用户自己在「我的文件」里删。\n"
        "• ⚠️ **解包/构建这类会摊出大量中间文件的操作，在 /workspace/tmp 下做**"
        "（`mkdir -p /workspace/tmp`），只把最终交付的文件复制回 /workspace/files/。"
        "直接在 files/ 里解归档会把上百个中间文件塞进用户的文件区。\n"
        if sync_on else
        "• 本轮只有临时 scratch 权限：用户文件可在 /workspace/files/ 中读取，"
        "但这里产生的新增、修改、删除和转换结果都不会同步到用户『我的文件』。"
        "可以运行检查、计算和临时整理命令；不要宣称已经创建、修改或交付用户文件。\n"
    )
    # 授权边界要**对模型可见**才叫目标驱动：落库层已经硬拦（见 workspace_sync），
    # 但不写进描述的话模型会一路做完才发现产物没落地，白烧一整轮。
    revision_name = str((revision_target or {}).get("filename") or "").strip()
    revision_desc = (
        f"• ⚠️ 本轮**只授权原位修改 /workspace/files/{revision_name or '目标文件'}**："
        "写别的文件、或另做一份替代品，即使在沙箱里生成成功也**不会存进用户的「我的文件」**。"
        "要改就改它本身；确有必要另做一份，先问用户。\n"
        if str((revision_target or {}).get("file_id") or "").strip() and sync_on else ""
    )
    artifact_desc = (
        f"• 本轮是 PPT 产物工程：完整 PPTD 项目必须放在 **{STAGING_ROOT}**；"
        "技能脚本目录以注入块「沙箱精确目录」为准，禁止 ls/find /workspace/skills。"
        "用 read_file / edit_file / write_file（或 bash）改 .pptd/.page；"
        "用户放入工作区的照片在 media/。"
        "导出用技能目录下 scripts/run_export.py --force；"
        "用户可见交付只能调用 publish_ppt_artifact。"
        "不要把半成品复制到 /workspace/files/，也不要调用固定 spec/create_deck 模板路径。\n"
        if _artifact_profile else ""
    )
    tools = [
        MainTool(
            name="bash",
            description=(
                "在安全沙箱里执行 bash 命令（独立容器、非 root，网络状态以下方说明为准，有超时和资源上限）。"
                "用于查看/查找/处理文件、解包、运行脚本和技能包里的命令行工具。\n"
                "• 工作目录固定 /workspace。**同一轮任务内多次调用共用同一个容器**，"
                "所以上一次创建的文件、解开的包都还在；但每次调用是**独立的 shell**——"
                "`cd`、`export` 不跨调用保留，需要连续操作就写成一条命令用 && 串起来。"
                "不要用 `&` 起后台进程：它不会被下一次调用接管，只会白占容器资源。"
                "超过本轮工具墙钟的长命令会转成平台后台作业；用 `job status` / `job logs` / `job cancel` / `job wait` 接管。\n"
                "• 已选中的 Skill 的文件在 /workspace/skills/<技能名>/ 下，可以直接 ls 查看、"
                "按它 SKILL.md 的说明执行其中的脚本。\n"
                f"{sandbox_network_desc}"
                "已预装：grep find sed awk tar **unzip zip make git jq rsync file** python3"
                "（含 python-pptx / python-docx / openpyxl / XlsxWriter / pypdf / Pillow / "
                "pandas / matplotlib / reportlab / lxml）。\n"
                + (
                    "• 版式文档转 PDF 用 **`to-pdf <文件>`**（已处理 LibreOffice 的 profile 锁，"
                    "别自己拼 soffice --headless）；默认输出到 /workspace/files/，即已保存。\n"
                    if sync_on else
                    "• 版式文档转 PDF 可用 **`to-pdf <文件>`**；输出只留在本轮临时 scratch，"
                    "不会保存到用户文件区。\n"
                )
                +
                # 可视化产物图标规范（2026-07-30 补回）：这段原本挂在 execute_in_sandbox 的描述上，
                # 它下线时**整段丢了**（全库零命中，靠测试的 xfail 才发现）。它不是可选风格
                # 建议——「Emoji 当图标」正是 PPT 审美否决清单上的一条，丢了它产物会退回
                # 用表情符号充当图标。路径口径已从旧的 /workspace/ 改成 files/。
                "• **可视化产物图标规范**：PPT、Word/PDF、表格看板、图表和海报，一律禁止把 Emoji、"
                "Unicode 符号（含彩色表情）或 Wingdings/图标字体字符直接写进文本框充当图标。"
                "PPT 用 Skill 管线里的 SVG 矢量图标、原生形状与线条；其它格式用原生矢量元素，"
                "或先在 /workspace/tmp 生成透明背景 PNG 再嵌入。同一份产物须统一线宽、配色、"
                "尺寸与视觉风格；无法可靠绘制时改用简洁文字标签，**不要退回 Emoji**。\n"
                + (
                    "• 中间文件（生成器脚本、解包出来的东西）写 **/workspace/tmp**"
                    "（含 /workspace/tmp/work），只把最终交付物放进 /workspace/files/。"
                    "会话续做会保存 /workspace/tmp 下除 ppt-project 以外的内容。"
                    "files/ 下的 .py/.sh 不会存进『我的文件』，其它文件会，堆一堆中间产物用户就找不到真东西了。\n"
                    if sync_on else ""
                )
                + files_desc + revision_desc + artifact_desc +
                "• 命令非零退出会返回 stderr，看懂错误再改，不要重复同一条命令。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 bash 命令。可用 && / | / 重定向；支持 bash 语法（数组、[[ ]]）。",
                    },
                    "timeout": {
                        "type": "number",
                        "description": (
                            f"超时秒数（可选，默认 {_DEFAULT_TIMEOUT_S}，上限 {_max_timeout_s()}）。"
                            "跑构建/转换这类慢命令时按需调大。"
                            "超过本轮工具墙钟时会转成 `job status` 可查询的后台作业。"
                        ),
                    },
                    "intent": dict(INTENT_PROP),
                },
                "required": ["command"],
            },
            execute=_bash,
            public_action=("执行内容处理并检查产物" if sync_on else "执行命令并核对结果"),
            output_model=ToolValue,
            # 有副作用（写文件、执行任意命令）：网关异常时不得自动重跑，也不能并发。
            readonly=False,
            parallel_safe=False,
            effect_scope="user_files" if sync_on else "scratch",
            idempotent=False,
            approval_policy="conditional",
            resource_locks=("user-files",) if sync_on else ("sandbox",),
            semantic_tags=(
                ("productive", "artifact_producer", "revision_targeted")
                if sync_on else ("investigate",)
            ),
        )
    ]
    if _artifact_profile:
        tools.extend([
            MainTool(
                name="glob",
                description=(
                    f"列出当前 PPT 工程（{STAGING_ROOT}）里的页面、素材和 DESIGN.md。"
                    "不要 ls 技能目录。pattern 如 pages/*.page 或 *。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "通配符，如 pages/*.page；缺省 * 表示工程内全部文件",
                        },
                        "intent": dict(INTENT_PROP),
                    },
                    "required": [],
                },
                execute=_glob_project,
                public_action="核对 PPT 工程文件",
                output_model=ToolValue,
                readonly=True,
                parallel_safe=True,
                semantic_tags=("workspace_read",),
                allowed_execution_profiles=("artifact_coding",),
                effect_scope="scratch",
                resource_locks=("sandbox",),
            ),
            MainTool(
                name="read_file",
                description=(
                    f"读 PPT 工程（{STAGING_ROOT}）里的文本文件，例如 pages/01.page、DESIGN.md。"
                    "这不是「我的文件」。图片只返回引用提示，不 dump 字节。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对工程根的路径，如 pages/01.page",
                        },
                        "offset": {"type": "integer", "description": "从第几行开始（0 起，可选）"},
                        "limit": {"type": "integer", "description": "最多读多少行（可选）"},
                        "intent": dict(INTENT_PROP),
                    },
                    "required": ["path"],
                },
                execute=_read_project,
                public_action="读取 PPT 工程文件",
                output_model=ToolValue,
                readonly=True,
                parallel_safe=True,
                semantic_tags=("workspace_read",),
                allowed_execution_profiles=("artifact_coding",),
                effect_scope="scratch",
                resource_locks=("sandbox",),
            ),
            MainTool(
                name="write_file",
                description=(
                    f"把文本写入 PPT 工程（{STAGING_ROOT}），不是「我的文件」。"
                    "新建或整文件覆盖 pages/*.page、DESIGN.md、deck.pptd。"
                    "改一处用 edit_file。导出和发布仍用 bash run_export.py / publish_ppt_artifact。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对工程根，如 pages/01.page 或 DESIGN.md",
                        },
                        "content": {"type": "string", "description": "文件全文"},
                        "intent": dict(INTENT_PROP),
                    },
                    "required": ["path", "content"],
                },
                execute=_write_project,
                public_action="写入 PPT 工程文件",
                output_model=ToolValue,
                readonly=False,
                parallel_safe=False,
                semantic_tags=("productive", "artifact_producer", "revision_targeted"),
                allowed_execution_profiles=("artifact_coding",),
                effect_scope="scratch",
                idempotent=False,
                resource_locks=("sandbox",),
            ),
            MainTool(
                name="edit_file",
                description=(
                    f"在 PPT 工程（{STAGING_ROOT}）里做精确字符串替换，不是「我的文件」。"
                    "适合改某一页 .page 的一处文案或几何。整文件覆盖用 write_file。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对工程根，如 pages/01.page",
                        },
                        "old_string": {"type": "string", "description": "要被替换的原文（必须唯一）"},
                        "new_string": {"type": "string", "description": "替换后的文本"},
                        "intent": dict(INTENT_PROP),
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                execute=_edit_project,
                public_action="编辑 PPT 工程文件",
                output_model=ToolValue,
                readonly=False,
                parallel_safe=False,
                semantic_tags=("productive", "artifact_producer", "revision_targeted"),
                allowed_execution_profiles=("artifact_coding",),
                effect_scope="scratch",
                idempotent=False,
                resource_locks=("sandbox",),
            ),
        ])
        tools.append(MainTool(
            name="fetch_ppt_asset",
            description=(
                "把 search_web 图片目录中的 [图N]（或公开图片直链）安全下载并直接注入当前"
                f"PPT 沙箱工程的 {STAGING_ROOT}/media/。它不会污染用户『我的文件』，"
                "也不会自动把图片放进页面；成功后必须在对应 .page 里用 `elementType: image` "
                "和 `src: media/<文件名>` 引用。人物、产品、地点、赛事/事件等具体题材，"
                "先批量搜图，再调用本工具取得选中的素材，最后按素材比例设计版式。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "search_web 返回的 [图N] 编号，或完整 http/https 图片直链",
                    },
                    "filename": {
                        "type": "string",
                        "description": "media/ 中的语义化文件名（可选；扩展名会按图片字节校正）",
                    },
                    "intent": dict(INTENT_PROP),
                },
                "required": ["url"],
            },
            execute=_fetch_ppt_asset,
            public_action="获取演示文稿图片素材",
            output_model=ToolValue,
            readonly=False,
            parallel_safe=False,
            effect_scope="scratch",
            idempotent=False,
            resource_locks=("sandbox",),
            semantic_tags=("download",),
            allowed_execution_profiles=("artifact_coding",),
        ))
        tools.append(MainTool(
            name="publish_ppt_artifact",
            description=(
                "发布 PPT 产物工程的唯一入口。它会在同一沙箱中校验 PPTX ZIP 结构、"
                "PPTD manifest、.page 页面数与 PPTX 幻灯片数。"
                "它还会检查显式字体角色；当用户要求真实照片或本轮搜过/取得配图时，"
                "只把经 fetch_ppt_asset 留下来源与字节校验记录、且已被页面实质引用的素材"
                "计为合格照片，Bash 自制或无来源 JPEG 不能冒充。"
                "ZIP/页数/布局 lint 通过后即把最终 PPTX 写入用户『我的文件』。"
                "不做审美评分或精品门槛。PPTD 工程仅用于沙箱内创作，"
                "不生成或交付源工程 ZIP。结构/可用性失败绝不发布半成品。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": f"完整 PPTD 工程目录，必须位于 {STAGING_ROOT} 下。",
                    },
                    "pptx_path": {
                        "type": "string",
                        "description": f"待发布 PPTX 绝对路径，必须位于 {STAGING_ROOT} 下。",
                    },
                    "filename": {
                        "type": "string",
                        "description": "用户最终看到的 .pptx 文件名，不得包含目录。",
                    },
                    "intent": dict(INTENT_PROP),
                },
                "required": ["pptx_path", "filename"],
            },
            execute=_publish_ppt_artifact,
            public_action="审查并发布演示文稿",
            output_model=ToolValue,
            readonly=False,
            parallel_safe=False,
            capability="artifact.export",
            effect_scope="user_files",
            idempotent=False,
            resource_locks=("user-files",),
            allowed_execution_profiles=("artifact_coding",),
        ))
    elif _dynamic_artifact_candidate:
        # The model may load the first-party Skill after the initial interactive snapshot has
        # frozen the schema.  Expose only the two PPT-specific closures here (generic read/write
        # tools remain the native interactive ones); bash performs Skill-defined authoring/export,
        # and publish performs the objective structural check before persistence.
        tools.extend([
            MainTool(
                name="fetch_ppt_asset",
                description=(
                    "在模型明确 use_skill 加载首方 PPT Skill 后，把 search_web 的 [图N] 或公开图片直链"
                    f"注入当前工程 {STAGING_ROOT}/media/。未加载首方 Skill 时本工具拒绝执行。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "[图N] 或完整 http/https 图片直链"},
                        "filename": {"type": "string", "description": "media/ 中的文件名（可选）"},
                        "intent": dict(INTENT_PROP),
                    },
                    "required": ["url"],
                },
                execute=_fetch_ppt_asset,
                public_action="获取演示文稿图片素材",
                output_model=ToolValue,
                readonly=False,
                parallel_safe=False,
                effect_scope="scratch",
                idempotent=False,
                resource_locks=("sandbox",),
                semantic_tags=("download",),
                allowed_execution_profiles=("interactive", "artifact_coding"),
            ),
            MainTool(
                name="publish_ppt_artifact",
                description=(
                    "在模型明确 use_skill 加载首方 PPT Skill 后发布 PPT。发布前检查 PPTX ZIP、"
                    "PPTD manifest、页面数和必要文件；检查失败不写入用户文件。未加载首方 Skill 时拒绝。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "project_dir": {
                            "type": "string",
                            "description": f"PPTD 工程目录，必须位于 {STAGING_ROOT} 下",
                        },
                        "pptx_path": {
                            "type": "string",
                            "description": f"待发布 PPTX，必须位于 {STAGING_ROOT} 下",
                        },
                        "filename": {
                            "type": "string",
                            "description": "用户最终看到的 .pptx 文件名，不得包含目录",
                        },
                        "intent": dict(INTENT_PROP),
                    },
                    "required": ["pptx_path", "filename"],
                },
                execute=_publish_ppt_artifact,
                public_action="审查并发布演示文稿",
                output_model=ToolValue,
                readonly=False,
                parallel_safe=False,
                capability="artifact.export",
                effect_scope="user_files",
                idempotent=False,
                resource_locks=("user-files",),
                semantic_tags=("productive", "artifact_producer", "revision_targeted"),
                allowed_execution_profiles=("interactive", "artifact_coding"),
            ),
        ])
    return tools
