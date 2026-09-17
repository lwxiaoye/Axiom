"""Persist PPTD staging across Runs without keeping the sandbox container.

Capture packs the live sandbox object (not a nested execute_in_sandbox, which
would miss a just-retired session or deadlock on the session lock). Restore
hydrates the new Run's container from the previous Run's tar. The blob is a
hidden user-file (source=staging_ckpt, VARCHAR(16)), never a deliverable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Iterable, Optional

from app.core.config import settings
from app.services.files import deliverable, user_file_service
from app.services.sandbox import session_pool
from app.services.sandbox import sandbox_executor
from app.services.skills.ppt_agentic_adapter import STAGING_ROOT, is_agentic_ppt_profile
from app.services.agent_harness.workspace_service import WORK_ROOT
from app.services.skills.ppt_project_progress import ppt_project_progress

logger = logging.getLogger(__name__)

CHECKPOINT_NAME = "ppt-project.tgz"
WORK_CHECKPOINT_NAME = "work-project.tgz"
_PACK_TIMEOUT_MS = 25_000
_RESTORE_TIMEOUT_MS = 25_000
_CAPTURE_DEBOUNCE_S = 20.0
_last_ok_capture: dict[str, float] = {}

RESTORED_STAGING_NOTE = (
    "PPTD 工程已从检查点恢复到 `/workspace/tmp/ppt-project`。"
    "这是断电或进程重启后的续做，请接着改现有工程，禁止整表重建。"
)
LIVE_SANDBOX_NOTE = (
    "本任务的沙箱会话仍在，`/workspace/tmp/ppt-project` 没有被清空。"
    "请接着改现有工程，禁止因为「新的一轮」就整表重建。"
)
STALE_STAGING_NOTE = (
    "当前沙箱是新的，`/workspace/tmp/ppt-project` 没有从上一轮恢复到文件。"
    "计划卡里标 completed 的步骤若目录里没有对应 pages/media，必须重做那些步骤，"
    "禁止把计划状态当成磁盘上的文件还在。"
)
WORKSPACE_PULLED_NOTE = (
    "会话工作区已灌入 `/workspace/tmp/ppt-project`。"
    "用户放入的照片在 `media/`；技能包只提供导出脚本和配方，不要把 `/workspace/skills/` 当工程。"
    "请用 read_file / edit_file / write_file 改 pages/*.page 和 DESIGN.md，不要整表重建。"
)
SESSION_WORKSPACE_PULLED_NOTE = (
    "会话工作区已灌入当前沙箱。请接着改现有文件，不要因为这是新的一轮就整份重写。"
)

_PACK_SCRIPT = f"""
set +e
if [ ! -d {STAGING_ROOT} ]; then
  echo PPT_STAGING_MISSING
  exit 0
fi
if [ -z "$(ls -A {STAGING_ROOT} 2>/dev/null)" ]; then
  echo PPT_STAGING_EMPTY
  exit 0
fi
mkdir -p /workspace/outputs
python3 - <<'PY'
import json, os
root = {STAGING_ROOT!r}
media = os.path.join(root, "media")
count = 0
if os.path.isdir(media):
    count = sum(1 for name in os.listdir(media) if os.path.isfile(os.path.join(media, name)))
print("PPT_STAGING_META", json.dumps({{"media_count": count}}))
PY
tar -C {STAGING_ROOT} -czf /workspace/outputs/{CHECKPOINT_NAME} .
status=$?
if [ $status -ne 0 ]; then
  echo PPT_STAGING_TAR_FAILED
  exit 0
fi
echo PPT_STAGING_PACKED
"""

_SLIM_PACK_SCRIPT = f"""
set +e
mkdir -p /workspace/outputs
tar -C {STAGING_ROOT} --exclude=media -czf /workspace/outputs/{CHECKPOINT_NAME} .
if [ $? -ne 0 ]; then
  echo PPT_STAGING_TAR_FAILED
  exit 0
fi
echo 'PPT_STAGING_META {{"media_count": 0}}'
echo PPT_STAGING_PACKED_SLIM
"""

_RESTORE_SCRIPT = f"""
set -eu
mkdir -p {STAGING_ROOT}
tar -xzf /workspace/inputs/{CHECKPOINT_NAME} -C {STAGING_ROOT}
python3 - <<'PY'
import json, os
root = {STAGING_ROOT!r}
media = os.path.join(root, "media")
count = 0
if os.path.isdir(media):
    count = sum(1 for name in os.listdir(media) if os.path.isfile(os.path.join(media, name)))
print("PPT_STAGING_META", json.dumps({{"media_count": count}}))
PY
"""


def _max_checkpoint_bytes() -> int:
    limit_mb = int(getattr(settings, "USER_FILES_MAX_SIZE_MB", 15) or 15)
    return max(1, min(80, limit_mb)) * 1024 * 1024


def checkpoint_store_decision(
    nbytes: int,
    limit: int,
    *,
    slim_bytes: Optional[int] = None,
) -> str:
    """full | slim | skip. Slim drops media/ so continue can re-fetch photos."""
    if nbytes <= limit:
        return "full"
    if slim_bytes is not None and slim_bytes <= limit:
        return "slim"
    return "skip"


def parse_media_count(stdout: str) -> int:
    for line in str(stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("PPT_STAGING_META "):
            continue
        try:
            payload = json.loads(line.split(" ", 1)[1])
            return max(0, int(payload.get("media_count") or 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0
    return 0


def _has_publish_receipt(state: dict[str, Any]) -> bool:
    extra = state.get("artifact_review") or state.get("publish_receipt") or {}
    if isinstance(extra, dict) and str(extra.get("status") or "").lower() in {
        "completed", "success", "succeeded", "ok",
    }:
        return True
    receipts = state.get("artifact_receipts") or []
    if isinstance(receipts, list):
        for item in receipts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("filename") or item.get("name") or "").lower()
            if name.endswith(".pptx") and item.get("file_id"):
                return True
    return False


async def _profile_and_state(run_id: str) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    from app.services.agent_harness import run_store

    packed = await run_store.get_run_state(run_id)
    state = dict((packed or {}).get("state") or {})
    profile = state.get("execution_profile")
    snapshot = profile.get("snapshot") if isinstance(profile, dict) else None
    if isinstance(snapshot, dict) and snapshot.get("id"):
        return snapshot, state
    if isinstance(profile, dict) and profile.get("id") == "artifact_coding":
        return profile, state
    return (snapshot if isinstance(snapshot, dict) else profile), state


async def run_owner(run_id: str) -> tuple[str, str]:
    """Return (user_id, thread_id) for a Run. Empty strings when unknown."""
    if not run_id:
        return "", ""
    try:
        from app.core.runtime_db import runtime_session
        from app.runtime_models import AgentRun

        factory = runtime_session()
        if factory is None:
            return "", ""
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return "", ""
            return str(run.user_id or ""), str(run.thread_id or "")
    except Exception:  # noqa: BLE001
        return "", ""


def first_checkpoint_run_id(rows: Iterable[Any]) -> str:
    """Newest-first rows of (run_id, state) → first run that stored a staging pointer."""
    for item in rows:
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        run_id, state = item[0], item[1]
        if not isinstance(state, dict):
            continue
        pointer = state.get("ppt_staging_checkpoint") or {}
        if isinstance(pointer, dict) and str(pointer.get("file_id") or "").strip():
            return str(run_id)
    return ""


async def latest_ppt_checkpoint_run_id(
    *,
    thread_id: str,
    user_id: str,
    exclude_run_id: str = "",
) -> str:
    """Newest prior Run in this thread that actually stored a staging tar."""
    if not thread_id or not user_id:
        return ""
    try:
        from sqlalchemy import select

        from app.core.runtime_db import runtime_session
        from app.runtime_models import AgentRun

        factory = runtime_session()
        if factory is None:
            return ""
        async with factory() as session:
            query = (
                select(AgentRun)
                .where(AgentRun.thread_id == str(thread_id))
                .where(AgentRun.user_id == str(user_id))
                .order_by(AgentRun.created_at.desc())
                .limit(20)
            )
            if exclude_run_id:
                query = query.where(AgentRun.id != str(exclude_run_id))
            rows = (await session.execute(query)).scalars().all()
        return first_checkpoint_run_id(
            [(str(row.id), dict(getattr(row, "state", None) or {})) for row in rows]
        )
    except Exception:  # noqa: BLE001
        logger.info("PPT staging latest-pointer lookup failed thread=%s", thread_id, exc_info=True)
        return ""


def attach_checkpoint_to_profile(profile: Any, meta: dict[str, Any]) -> Any:
    if not isinstance(profile, dict):
        return profile
    policy = dict(profile.get("workspace_policy") or {})
    policy["checkpoint_media_count"] = int(meta.get("media_count") or 0)
    policy["checkpoint_restored"] = bool(meta.get("restored"))
    next_profile = dict(profile)
    next_profile["workspace_policy"] = policy
    return next_profile


def _blob_from_outputs(result: Any) -> bytes:
    for item in getattr(result, "output_files", None) or []:
        if str(item.get("name") or "") != CHECKPOINT_NAME:
            continue
        content = item.get("content")
        if isinstance(content, (bytes, bytearray)):
            return bytes(content)
    return b""


async def _read_checkpoint_blob(sandbox: Any) -> bytes:
    try:
        results = await sandbox.read_files([f"/workspace/outputs/{CHECKPOINT_NAME}"])
    except Exception:  # noqa: BLE001
        return b""
    for item in results or []:
        data = getattr(item, "data", None)
        if data is None and isinstance(item, dict):
            data = item.get("data")
        ok = bool(getattr(item, "ok", True))
        if isinstance(item, dict) and item.get("error"):
            ok = False
        if ok and isinstance(data, (bytes, bytearray)) and data:
            return bytes(data)
    return b""


async def _pack_on_sandbox(sandbox: Any) -> tuple[bytes, str, str]:
    """Return (blob, stdout, kind) where kind is full|slim|empty|missing|no_blob."""
    from app.services.sandbox.base import ExecuteOptions

    opts = ExecuteOptions(timeout_ms=_PACK_TIMEOUT_MS, max_output_bytes=65536)
    result = await sandbox.execute(_PACK_SCRIPT, opts)
    stdout = str(getattr(result, "stdout", "") or "")
    if "PPT_STAGING_MISSING" in stdout:
        return b"", stdout, "missing"
    if "PPT_STAGING_EMPTY" in stdout:
        return b"", stdout, "empty"
    blob = await _read_checkpoint_blob(sandbox)
    if blob:
        limit = _max_checkpoint_bytes()
        if checkpoint_store_decision(len(blob), limit) == "full":
            return blob, stdout, "full"
    slim = await sandbox.execute(_SLIM_PACK_SCRIPT, opts)
    slim_out = str(getattr(slim, "stdout", "") or "")
    slim_blob = await _read_checkpoint_blob(sandbox)
    stdout = f"{stdout}\n{slim_out}".strip()
    if slim_blob and checkpoint_store_decision(
        len(blob) if blob else len(slim_blob),
        _max_checkpoint_bytes(),
        slim_bytes=len(slim_blob),
    ) == "slim":
        return slim_blob, stdout, "slim"
    if blob:
        return b"", stdout, "oversize"
    return b"", stdout, "no_blob"


async def _pack_from_session(session: Any) -> tuple[bytes, str, str]:
    lock = getattr(session, "lock", None)
    if lock is None:
        return await _pack_on_sandbox(session.sandbox)
    await lock.acquire()
    try:
        return await _pack_on_sandbox(session.sandbox)
    finally:
        lock.release()


_WORK_PACK_SCRIPT = f"""
set +e
if [ ! -d {WORK_ROOT} ]; then
  echo WORK_EMPTY
  exit 0
fi
if [ -z "$(find {WORK_ROOT} -mindepth 1 -maxdepth 1 ! -name ppt-project ! -name 'ppt-project.tgz' 2>/dev/null)" ]; then
  echo WORK_EMPTY
  exit 0
fi
mkdir -p /workspace/outputs
tar -C {WORK_ROOT} --exclude=ppt-project --exclude=./ppt-project --exclude=ppt-project.tgz -czf /workspace/outputs/{WORK_CHECKPOINT_NAME} .
if [ $? -ne 0 ]; then
  echo WORK_TAR_FAILED
  exit 0
fi
echo WORK_PACKED
"""


async def capture_work_staging(
    *,
    run_id: str,
    thread_id: str,
    user_id: str,
    timeout_ms: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Pack non-PPT `/workspace/tmp` (excluding ppt-project) into the session tree."""
    if not run_id or not user_id or not thread_id:
        return None
    budget = int(timeout_ms) if timeout_ms is not None else _PACK_TIMEOUT_MS
    if budget < 1000:
        return None
    try:
        result = await sandbox_executor.execute_in_sandbox(
            _WORK_PACK_SCRIPT,
            language="bash",
            collect_outputs=True,
            fetch_output_bytes=True,
            collect_workspace=False,
            migrate_outputs=False,
            timeout_ms=min(budget, _PACK_TIMEOUT_MS),
            session_key=run_id,
        )
    except Exception:  # noqa: BLE001
        logger.info("work staging pack failed run=%s", run_id, exc_info=True)
        return None
    blob = b""
    for item in result.output_files or []:
        if str(item.get("name") or "") == WORK_CHECKPOINT_NAME and item.get("content"):
            blob = bytes(item.get("content") or b"")
            break
    if not blob or "WORK_PACKED" not in str(result.stdout or ""):
        return None
    from app.services.agent_harness import workspace_service
    return await workspace_service.commit_tree_snapshot(
        user_id=user_id,
        thread_id=thread_id,
        blob=blob,
        run_id=run_id,
    )


async def capture_ppt_staging_if_due(
    *,
    run_id: str,
    thread_id: str,
    user_id: str,
) -> Optional[dict[str, Any]]:
    last = _last_ok_capture.get(run_id, 0.0)
    if last and (time.monotonic() - last) < _CAPTURE_DEBOUNCE_S:
        return None
    return await capture_ppt_staging(
        run_id=run_id, thread_id=thread_id, user_id=user_id, force=False,
    )


async def capture_ppt_staging(
    *,
    run_id: str,
    thread_id: str,
    user_id: str,
    session: Any = None,
    force: bool = True,
) -> Optional[dict[str, Any]]:
    """Pack STAGING_ROOT into a hidden file. Never raises into Run teardown."""
    if not run_id or not user_id:
        logger.info("PPT staging capture skip run=%s reason=missing_owner", run_id)
        return None
    if not force:
        last = _last_ok_capture.get(run_id, 0.0)
        if last and (time.monotonic() - last) < _CAPTURE_DEBOUNCE_S:
            return None
    try:
        profile, state = await _profile_and_state(run_id)
        if not is_agentic_ppt_profile(profile):
            return None
        if _has_publish_receipt(state):
            logger.info("PPT staging capture skip run=%s reason=published", run_id)
            return None
        sess = session if session is not None else session_pool.peek(run_id)
        if sess is None or getattr(sess, "sandbox", None) is None:
            logger.info("PPT staging capture skip run=%s reason=no_live_session", run_id)
            return None
        blob, stdout, kind = await _pack_from_session(sess)
        if kind in {"missing", "empty", "no_blob", "oversize"}:
            logger.info("PPT staging capture skip run=%s reason=%s", run_id, kind)
            return None
        media_count = parse_media_count(stdout)
        if kind == "slim":
            media_count = 0
        meta = None
        try:
            from app.services.agent_harness import workspace_service
            saved_ws = await workspace_service.commit_tree_snapshot(
                user_id=user_id,
                thread_id=thread_id or "",
                blob=blob,
                run_id=run_id,
                media_count=media_count,
                pack=kind,
            )
            if saved_ws:
                meta = {
                    "workspace_object_id": str(saved_ws.get("id") or saved_ws.get("workspace_object_id") or ""),
                    "file_id": "",
                    "sha256": str(saved_ws.get("sha256") or hashlib.sha256(blob).hexdigest()),
                    "bytes": len(blob),
                    "media_count": media_count,
                    "filename": f".ppt-project-{run_id}.tgz",
                    "pack": kind,
                    "source": "workspace",
                }
        except Exception:  # noqa: BLE001
            logger.warning("PPT workspace commit failed run=%s, falling back to staging_ckpt", run_id, exc_info=True)
            meta = None
        if meta is None:
            saved = await user_file_service.save_file(
                user_id,
                f".ppt-project-{run_id}.tgz",
                blob,
                source=deliverable.STAGING_CHECKPOINT_SOURCE,
                thread_id=thread_id or None,
                mime="application/gzip",
                run_id=run_id,
            )
            meta = {
                "file_id": str(saved.get("id") or ""),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
                "media_count": media_count,
                "filename": str(saved.get("filename") or ""),
                "pack": kind,
            }
        from app.services.agent_harness import run_store
        await run_store.patch_run_state(run_id, {"ppt_staging_checkpoint": meta})
        _last_ok_capture[run_id] = time.monotonic()
        logger.info(
            "PPT staging captured run=%s bytes=%s media=%s pack=%s source=%s",
            run_id, len(blob), media_count, kind, meta.get("source") or "staging_ckpt",
        )
        # Tool observations consume this separately from the persisted recovery
        # pointer. A scratch progress receipt must never inherit its file_id.
        return {**meta, "progress_receipt": ppt_project_progress(blob)}
    except Exception:  # noqa: BLE001
        logger.warning("PPT staging capture failed run=%s", run_id, exc_info=True)
        return None


async def restore_ppt_staging(
    *,
    run_id: str,
    resume_source_run_id: str,
    user_id: str,
) -> Optional[dict[str, Any]]:
    """Hydrate STAGING_ROOT in the new Run sandbox from the previous Run's tar."""
    if not run_id or not resume_source_run_id or not user_id:
        return None
    try:
        _profile, source_state = await _profile_and_state(resume_source_run_id)
        pointer = source_state.get("ppt_staging_checkpoint") or {}
        file_id = str(pointer.get("file_id") or "").strip()
        if not file_id:
            logger.info(
                "PPT staging restore skip run=%s source=%s reason=no_pointer",
                run_id, resume_source_run_id,
            )
            return None
        _row, blob = await user_file_service.read_bytes(user_id, file_id)
        if not blob:
            return None
        result = await sandbox_executor.execute_in_sandbox(
            _RESTORE_SCRIPT,
            language="bash",
            input_files={CHECKPOINT_NAME: blob},
            collect_outputs=False,
            collect_workspace=False,
            migrate_outputs=False,
            timeout_ms=_RESTORE_TIMEOUT_MS,
            session_key=run_id,
        )
        if result.error or not result.ok:
            logger.info(
                "PPT staging restore failed run=%s source=%s err=%s",
                run_id, resume_source_run_id, result.error or result.stderr,
            )
            return None
        media_count = parse_media_count(str(result.stdout or ""))
        if media_count <= 0:
            try:
                media_count = int(pointer.get("media_count") or 0)
            except (TypeError, ValueError):
                media_count = 0
        meta = {
            "file_id": file_id,
            "restored": True,
            "restored_from": resume_source_run_id,
            "media_count": media_count,
            "bytes": len(blob),
        }
        from app.services.agent_harness import run_store
        await run_store.patch_run_state(run_id, {"ppt_staging_checkpoint": meta})
        logger.info(
            "PPT staging restored run=%s source=%s bytes=%s media=%s",
            run_id, resume_source_run_id, len(blob), media_count,
        )
        return meta
    except Exception:  # noqa: BLE001
        logger.warning(
            "PPT staging restore failed run=%s source=%s",
            run_id, resume_source_run_id, exc_info=True,
        )
        return None


def resolve_staging_restore_source(
    *,
    run_id: str,
    own_pointer: bool,
    resume_source_run_id: str = "",
    thread_latest_run_id: str = "",
    user_wants_resume: bool = False,
    has_live_session: bool = False,
) -> str:
    """Which Run's tar to hydrate. Empty means do not restore.

    Power-loss reclaim of the same Run uses this Run's own pointer even when the
    pending user message is the original task, not 「继续」.
    """
    if has_live_session:
        return ""
    if own_pointer:
        return str(run_id or "")
    if not user_wants_resume:
        return ""
    return str(resume_source_run_id or thread_latest_run_id or "").strip()


async def _pull_workspace(
    *,
    thread_id: str,
    run_id: str,
    user_id: str,
    include_tree: bool,
    project_root: str = "",
) -> Optional[dict[str, Any]]:
    if not thread_id:
        return None
    try:
        from app.services.agent_harness import workspace_service
        kwargs = {
            "thread_id": str(thread_id),
            "run_id": str(run_id),
            "user_id": str(user_id),
            "include_tree": include_tree,
        }
        if project_root:
            kwargs["project_root"] = project_root
        return await workspace_service.pull_into_run(**kwargs)
    except Exception:  # noqa: BLE001
        logger.warning("workspace pull failed run=%s", run_id, exc_info=True)
        return None


async def hydrate_ppt_staging(
    *,
    run_id: str,
    user_id: str,
    thread_id: str = "",
    resume_source_run_id: str = "",
    user_wants_resume: bool = False,
    execution_profile: Any = None,
) -> tuple[Optional[dict[str, Any]], str]:
    """Restore staging for PPT Runs. Returns (meta, model_note).

    Fresh Runs still Pull the session workspace so user-uploaded photos land in
    ``media/``. Resume / power-loss restores the staging tar first, then overlays
    assets without replacing the newer working copy with an older committed tree.
    """
    from app.services.chat.execution_profile import unwrap_profile_dict
    from app.services.agent_harness.workspace_service import WORK_ROOT
    if not run_id or not user_id:
        return None, ""
    if not is_agentic_ppt_profile(unwrap_profile_dict(execution_profile)):
        if session_pool.has_live_session(run_id):
            return None, ""
        pulled = await _pull_workspace(
            thread_id=str(thread_id or ""),
            run_id=str(run_id),
            user_id=str(user_id),
            include_tree=True,
            project_root=WORK_ROOT,
        )
        if pulled:
            return pulled, SESSION_WORKSPACE_PULLED_NOTE
        return None, ""
    if session_pool.has_live_session(run_id):
        return None, LIVE_SANDBOX_NOTE
    _profile, own_state = await _profile_and_state(run_id)
    pointer = own_state.get("ppt_staging_checkpoint") or {}
    own_pointer = isinstance(pointer, dict) and bool(
        str(pointer.get("file_id") or "").strip()
        or str(pointer.get("workspace_object_id") or "").strip()
    )
    thread_latest = ""
    if user_wants_resume and not own_pointer:
        thread_latest = await latest_ppt_checkpoint_run_id(
            thread_id=str(thread_id or ""),
            user_id=str(user_id or ""),
            exclude_run_id=str(run_id or ""),
        )
    source = resolve_staging_restore_source(
        run_id=run_id,
        own_pointer=own_pointer,
        resume_source_run_id=resume_source_run_id,
        thread_latest_run_id=thread_latest,
        user_wants_resume=user_wants_resume,
        has_live_session=False,
    )

    async def _remember_pull(pulled: dict[str, Any]) -> None:
        from app.services.agent_harness import run_store
        await run_store.patch_run_state(run_id, {"ppt_staging_checkpoint": pulled})

    if source:
        meta = await restore_ppt_staging(
            run_id=run_id, resume_source_run_id=source, user_id=user_id,
        )
        if not meta and user_wants_resume:
            alt = await latest_ppt_checkpoint_run_id(
                thread_id=str(thread_id or ""),
                user_id=str(user_id or ""),
                exclude_run_id=str(run_id or ""),
            )
            if alt and alt != source:
                meta = await restore_ppt_staging(
                    run_id=run_id, resume_source_run_id=alt, user_id=user_id,
                )
        if meta:
            await _pull_workspace(
                thread_id=str(thread_id or ""),
                run_id=str(run_id),
                user_id=str(user_id),
                include_tree=False,
            )
            return meta, RESTORED_STAGING_NOTE
        pulled = await _pull_workspace(
            thread_id=str(thread_id or ""),
            run_id=str(run_id),
            user_id=str(user_id),
            include_tree=True,
        )
        if pulled:
            await _remember_pull(pulled)
            return pulled, WORKSPACE_PULLED_NOTE
        if user_wants_resume or own_pointer:
            return None, STALE_STAGING_NOTE
        return None, ""

    pulled = await _pull_workspace(
        thread_id=str(thread_id or ""),
        run_id=str(run_id),
        user_id=str(user_id),
        include_tree=True,
    )
    if pulled:
        await _remember_pull(pulled)
        return pulled, WORKSPACE_PULLED_NOTE
    if user_wants_resume or own_pointer:
        return None, STALE_STAGING_NOTE
    return None, ""
