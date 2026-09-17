"""Live session-workspace inventory for Context Compiler.

The model must see the current workspace each round: page names, material
names, and whether the staging dir is empty. Pixel bytes stay on disk; this
module only lists facts. Inspect peeks the live sandbox and never creates one.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from app.services.skills.ppt_agentic_adapter import STAGING_ROOT, is_agentic_ppt_profile

logger = logging.getLogger(__name__)

WORKSPACE_STATUS_MARK = "（会话工作区当前快照"
_LIST_TIMEOUT_MS = 8_000
_LOCK_WAIT_S = 1.5
_NAME_CAP = 40
_BLOCK_CHAR_CAP = 2500
_ROOT_KEEP = {
    "DESIGN.md",
    "spec.json",
    "deck.json",
    "README.md",
    "manifest.json",
}

_last_block: dict[str, str] = {}

_LIST_SCRIPT = f"""
python3 - <<'PY'
import json, os
root = {STAGING_ROOT!r}

def list_files(rel, suffixes=None):
    path = os.path.join(root, rel) if rel else root
    if not os.path.isdir(path):
        return []
    out = []
    for name in sorted(os.listdir(path)):
        fp = os.path.join(path, name)
        if not os.path.isfile(fp):
            continue
        if suffixes and not name.lower().endswith(suffixes):
            continue
        try:
            size = os.path.getsize(fp)
        except OSError:
            size = 0
        out.append({{"name": name, "bytes": int(size)}})
    return out

pages = list_files("pages")
media = list_files("media")
root_files = [
    item for item in list_files("")
    if item["name"] in {sorted(_ROOT_KEEP)!r}
    or item["name"].endswith((".pptd", ".pptx", ".json", ".md"))
]
print("WORKSPACE_INVENTORY", json.dumps({{
    "exists": os.path.isdir(root),
    "pages": pages,
    "media": media,
    "root_files": root_files,
}}, ensure_ascii=False))
PY
"""


def parse_workspace_inventory(stdout: str) -> dict[str, Any]:
    for line in str(stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("WORKSPACE_INVENTORY "):
            continue
        try:
            payload = json.loads(line.split(" ", 1)[1])
        except (ValueError, json.JSONDecodeError):
            return {}
        if isinstance(payload, dict):
            return payload
    return {}


def _human_bytes(n: int) -> str:
    try:
        size = int(n or 0)
    except (TypeError, ValueError):
        size = 0
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size // 1024}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _name_line(items: list[Any], *, with_size: bool = False) -> tuple[int, str]:
    rows = [item for item in (items or []) if isinstance(item, dict) and item.get("name")]
    shown = rows[:_NAME_CAP]
    parts = []
    for item in shown:
        name = str(item.get("name") or "").strip()[:80]
        if not name:
            continue
        if with_size:
            parts.append(f"{name} ({_human_bytes(int(item.get('bytes') or 0))})")
        else:
            parts.append(name)
    extra = len(rows) - len(shown)
    body = ", ".join(parts) if parts else "无"
    if extra > 0:
        body += f"；另有 {extra} 个未列出"
    return len(rows), body


def format_workspace_status(inventory: Optional[dict[str, Any]] = None) -> str:
    data = dict(inventory or {})
    page_n, page_line = _name_line(list(data.get("pages") or []))
    media_n, media_line = _name_line(list(data.get("media") or []), with_size=True)
    root_n, root_line = _name_line(list(data.get("root_files") or []))
    exists = bool(data.get("exists"))
    empty = (not exists) or (page_n == 0 and media_n == 0 and root_n == 0)
    lines = [
        f"{WORKSPACE_STATUS_MARK}，每轮从磁盘重读，以这里为准，不要凭记忆或计划卡推断文件还在）",
        f"路径：{STAGING_ROOT}",
    ]
    if empty:
        lines.append("当前：空（目录不存在，或还没有页面/素材）")
        ckpt_n = data.get("checkpoint_media_count")
        try:
            ckpt_n = int(ckpt_n or 0)
        except (TypeError, ValueError):
            ckpt_n = 0
        if data.get("checkpoint_present") or ckpt_n > 0:
            extra = f"，检查点记录素材 {ckpt_n} 张" if ckpt_n > 0 else ""
            lines.append(
                f"会话工作区快照里可能还有上一轮工程{extra}。"
                "先确认是否已灌回沙箱；计划 completed 不能证明磁盘上还有文件。"
            )
        else:
            lines.append("请在此目录创建工程；不要把计划状态当成文件已在。")
            lines.append(
                "技能包只提供导出脚本和配方。用户从工作区抽屉或输入框放入的照片会出现在 media/。"
                "用 write_file / edit_file 写 pages/*.page，不要把技能目录当工程，"
                "也不要写进「我的文件」。"
            )
    else:
        lines.append(f"页面 {page_n}：{page_line}")
        lines.append(f"素材 {media_n}：{media_line}")
        if root_n:
            lines.append(f"工程文件：{root_line}")
        lines.append(
            "请用 read_file / edit_file / write_file 直接改这些已有文件继续；"
            "缺的素材再搜索或使用用户放入工作区的照片（media/）。"
            "技能目录不是工程。禁止因为新的一轮就整表重建。"
        )
    block = "\n".join(lines)
    if len(block) > _BLOCK_CHAR_CAP:
        return block[: _BLOCK_CHAR_CAP - 1] + "…"
    return block


def format_user_material_status(
    inventory: Optional[dict[str, Any]] = None,
    snippets: Optional[list[str]] = None,
) -> str:
    """Non-PPT runs: tell the model which uploaded lecture notes / files are in the thread."""
    data = dict(inventory or {})
    names: list[str] = []
    for key in ("root_files", "media", "pages"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
    snippet_rows = [str(item).strip() for item in (snippets or []) if str(item).strip()]
    if not names and not snippet_rows:
        return ""
    lines = [
        f"{WORKSPACE_STATUS_MARK}，用户已放入本会话的材料；主对话顶栏工作区入口可能隐藏，但仍按这里为准）",
        f"文件：{', '.join(names[:_NAME_CAP]) or '无文件名清单'}",
        "优先用本轮附件正文；PDF/讲义已解析的摘录如下。读不到的部分必须如实说明，不要编造讲义内容。",
    ]
    if snippet_rows:
        lines.append("文本摘录：")
        lines.extend(snippet_rows[:3])
    block = "\n".join(lines)
    if len(block) > _BLOCK_CHAR_CAP:
        return block[: _BLOCK_CHAR_CAP - 1] + "…"
    return block


def is_workspace_status_message(message: Any) -> bool:
    if not isinstance(message, dict) or message.get("role") != "system":
        return False
    content = message.get("content")
    return isinstance(content, str) and (
        WORKSPACE_STATUS_MARK in content[:80] or "【用户选择的工作文件夹】" in content[:80]
    )


def upsert_workspace_status(messages: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    """Replace the previous workspace snapshot in a mutable message list."""
    kept = [item for item in messages if not is_workspace_status_message(item)]
    if block:
        insert_at = 1 if kept and kept[0].get("role") == "system" else 0
        kept.insert(insert_at, {"role": "system", "content": block})
    messages[:] = kept
    return messages


def strip_workspace_status_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in messages if not is_workspace_status_message(item)]


async def inspect_live_inventory(run_id: str) -> Optional[dict[str, Any]]:
    """Peek the live sandbox. Never creates a session. None if unavailable."""
    if not run_id:
        return None
    try:
        from app.services.sandbox import session_pool
        from app.services.sandbox.base import ExecuteOptions

        session = session_pool.peek(run_id)
        sandbox = getattr(session, "sandbox", None) if session is not None else None
        if sandbox is None:
            return None
        lock = getattr(session, "lock", None)

        async def _run() -> Optional[dict[str, Any]]:
            result = await sandbox.execute(
                _LIST_SCRIPT,
                ExecuteOptions(timeout_ms=_LIST_TIMEOUT_MS, max_output_bytes=65536),
            )
            stdout = str(getattr(result, "stdout", "") or "")
            parsed = parse_workspace_inventory(stdout)
            if parsed:
                return parsed
            if "WORKSPACE_INVENTORY " in stdout:
                return {"exists": False, "pages": [], "media": [], "root_files": []}
            return None

        if lock is None:
            return await asyncio.wait_for(_run(), timeout=_LOCK_WAIT_S + 6)
        await asyncio.wait_for(lock.acquire(), timeout=_LOCK_WAIT_S)
        try:
            return await asyncio.wait_for(_run(), timeout=6)
        finally:
            lock.release()
    except Exception:  # noqa: BLE001
        logger.debug("workspace inventory inspect skipped run=%s", run_id, exc_info=True)
        return None


async def compile_workspace_status(
    *,
    run_id: str,
    execution_profile: Any = None,
    thread_id: str = "",
    user_id: str = "",
    user_query: str = "",
) -> str:
    """Return this round's workspace snapshot, or '' when this is not a PPT Run."""
    from app.services.chat.execution_profile import unwrap_profile_dict
    from app.services.files.work_folders import context_for_thread
    folder_status = await context_for_thread(user_id, thread_id)
    profile = unwrap_profile_dict(execution_profile)
    ppt = is_agentic_ppt_profile(profile)
    if not ppt:
        stored: dict[str, Any] = {}
        snippets: list[str] = []
        if thread_id and user_id:
            try:
                from app.services.agent_harness.workspace_service import (
                    inventory_for_compiler,
                    text_snippets,
                )
                stored = await inventory_for_compiler(thread_id, user_id)
                try:
                    snippets = await text_snippets(
                        thread_id,
                        user_id,
                        user_query,
                        run_id=run_id,
                    )
                except TypeError as exc:
                    # Backward-compatible adapter boundary: older injected
                    # snippet providers accepted exactly three arguments.
                    if "unexpected keyword argument 'run_id'" not in str(exc):
                        raise
                    snippets = await text_snippets(thread_id, user_id, user_query)
            except Exception:  # noqa: BLE001
                logger.debug("workspace compiler inventory skipped", exc_info=True)
        return "\n\n".join(filter(None, [folder_status, format_user_material_status(stored, snippets)]))
    live = await inspect_live_inventory(run_id)
    stored: dict[str, Any] = {}
    if thread_id and user_id:
        try:
            from app.services.agent_harness.workspace_service import inventory_for_compiler
            stored = await inventory_for_compiler(thread_id, user_id)
        except Exception:  # noqa: BLE001
            logger.debug("workspace compiler inventory skipped", exc_info=True)
            stored = {}
    live_empty = live is None or not (
        live.get("pages") or live.get("media") or live.get("root_files")
    )
    if live_empty and stored:
        live = stored
        live["exists"] = bool(stored.get("exists"))
    elif live is None:
        cached = _last_block.get(run_id) if run_id else ""
        if cached:
            return "\n\n".join(filter(None, [folder_status, cached]))
        live = stored or {}
    policy = {}
    if isinstance(profile, dict):
        policy = dict(profile.get("workspace_policy") or {})
    inventory = dict(live or {})
    if policy:
        inventory["checkpoint_media_count"] = policy.get("checkpoint_media_count")
        inventory.setdefault(
            "checkpoint_present",
            bool(policy.get("checkpoint_restored") or policy.get("checkpoint_media_count") or stored.get("exists")),
        )
    elif stored.get("exists"):
        inventory["checkpoint_present"] = True
    if live_empty and stored.get("exists"):
        inventory["checkpoint_present"] = True
        inventory.setdefault("checkpoint_media_count", len(stored.get("media") or []))
    block = format_workspace_status(inventory)
    if user_query and thread_id and user_id:
        try:
            from app.services.agent_harness.workspace_service import text_snippets
            try:
                snippets = await text_snippets(
                    thread_id,
                    user_id,
                    user_query,
                    run_id=run_id,
                )
            except TypeError as exc:
                # See the equivalent non-artifact branch above.  Audit-aware
                # implementations receive run_id; legacy adapters remain valid.
                if "unexpected keyword argument 'run_id'" not in str(exc):
                    raise
                snippets = await text_snippets(thread_id, user_id, user_query)
            if snippets:
                extra = "文本摘录：\n" + "\n---\n".join(snippets)
                block = f"{block}\n{extra}"[:_BLOCK_CHAR_CAP]
        except Exception:  # noqa: BLE001
            logger.debug("workspace text snippets skipped", exc_info=True)
    if run_id:
        _last_block[run_id] = block
    return "\n\n".join(filter(None, [folder_status, block]))
