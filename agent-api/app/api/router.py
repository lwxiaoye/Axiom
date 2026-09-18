import asyncio
import json
import hashlib
import logging
import re
import uuid

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from typing import Any, Dict, List, Optional

from app.core.auth import current_user, UserContext, is_admin
from app.core.config import settings
from app.core.hmac_auth import verify_internal_request
from app.schemas.schemas import (
    ChatRequest, ChatResponse, HarnessInputRequest, ModelItem, AgentItem,
    ThreadItem, MasterConfig, MasterConfigUpdate,
    AgentEvent, AgentBulkSyncRequest,
)
from app.api.sse_utils import SSE_HEADERS, with_sse_keepalive
from app.services.agent_harness.orchestrator import harness_orchestrator
from app.services.agents.agent_service import agent_service
from app.services.platform.config_service import ConfigService
from app.services.chat import builtin_app_access
from app.services.chat.builtin_assistants.registry import origin_for_preset
from app.routers.connectors import router as connectors_router
from app.routers.embedding_config import router as embedding_config_router
from app.routers.platform_config import router as platform_config_router
from app.routers.knowledge import router as knowledge_router
from app.routers.workflow import router as workflow_router
from app.routers.agent_skill import router as agent_skill_router
from app.routers.files import router as files_router
from app.routers.workspace import router as workspace_router
from app.routers.campus_assistant import router as campus_assistant_router
from app.routers.audit import router as audit_router
from app.routers.openai_compat import router as openai_compat_router
from app.routers.embed import router as embed_router
from app.routers.agent_api_management import router as agent_api_management_router
from app.routers.external_agent_runs import router as external_agent_runs_router
from app.api.interview import router as interview_router

api_router = APIRouter()
from app.routers.model_connection import router as model_connection_router
api_router.include_router(model_connection_router)
from app.routers.rerank_config import router as rerank_config_router
api_router.include_router(rerank_config_router)
api_router.include_router(embedding_config_router)
api_router.include_router(platform_config_router)
api_router.include_router(knowledge_router)
api_router.include_router(workflow_router)
api_router.include_router(agent_skill_router)
api_router.include_router(files_router)
api_router.include_router(workspace_router)
api_router.include_router(connectors_router)
api_router.include_router(campus_assistant_router)
api_router.include_router(audit_router)
api_router.include_router(openai_compat_router)
api_router.include_router(embed_router)
api_router.include_router(agent_api_management_router)
api_router.include_router(external_agent_runs_router)
api_router.include_router(interview_router)

config_service = ConfigService()
logger = logging.getLogger(__name__)


# Chat endpoints
@api_router.get("/chat/builtin-apps")
async def list_builtin_apps_route(user: UserContext = Depends(current_user)):
    """智能体广场里的内置智能体列表（校园百事通 / 面试助手 / 演示文稿助手）。"""
    from app.services.chat.builtin_app_access import list_builtin_apps

    return await list_builtin_apps(user)


@api_router.get("/chat/builtin-apps/{preset}")
async def get_builtin_app(preset: str, user: UserContext = Depends(current_user)):
    """Resolve one administrator-configured Harness page and enforce its current ACL."""
    return await builtin_app_access.require_builtin_app_access(user, preset)


@api_router.get("/chat/subagents")
async def list_subagents(
    keyword: Optional[str] = None,
    limit: int = 50,
    user: UserContext = Depends(current_user),
):
    """列出可 @提及调用的子智能体（已发布、当前用户可访问的工作台智能体）。

    limit 只是**展示层**截断（@ 面板一页量级）；权限全集/召回层用
    subagent_service.list_callable_subagent_ids，不受此限。
    """
    from app.services.agents import subagent_service
    rows = await subagent_service.list_subagents(user, keyword=keyword)
    import logging as _dbg_logging
    _dbg_logging.getLogger("app.api.router").info(
        "[DBG subagents] tenant=%r user=%s -> %d rows", user.tenant_id, user.user_id, len(rows)
    )
    return rows[: max(1, min(int(limit or 50), 200))]


@api_router.post("/chat/upload")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(""),
    user: UserContext = Depends(current_user),
):
    """会话文件上传：解析附件并落到隐藏工作区，不进「我的文件」。

    对话框里放下的图片 / PPT / 粘贴文本是本轮素材，不是交付物。「我的文件」只放
    Agent 发布的产物，以及用户在该页主动上传的个人文件。这里仍分配 file_id，
    供当轮解析和发送时 ingest 进会话工作区；清单（含显示全部）不露出。
    """
    from app.services.files.chat_upload_types import IMAGE_EXTENSIONS, chat_upload_reject_reason, file_extension
    filename = file.filename or "file"
    format_error = chat_upload_reject_reason(filename, file.content_type or "")
    if format_error:
        raise HTTPException(status_code=415, detail=format_error)
    max_bytes = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    too_large = f"文件超过 {settings.USER_FILES_MAX_SIZE_MB}MB 限制"
    # 防线对齐 /files/upload：原 `await file.read()` 在检查上限前把整个请求体读进内存，
    # 绕过前端限制的超大 body 可打 OOM。① Content-Length 粗筛（multipart 头略大于本体，
    # 放宽 1MB）；② 1MB 分块读、超限立即 413。注意峰值不是 max+1MB：join 瞬间 parts 与
    # 结果并存，约 2×max_bytes（+解析器开销）——从「无界」收敛到「有界约 30MB」。
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + (1 << 20):
        raise HTTPException(status_code=413, detail=too_large)
    parts: List[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1 << 20)  # 1MB/次
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail=too_large)
        parts.append(chunk)
    content = b"".join(parts)
    from app.services.files import document_parse_service
    from app.services.files import user_file_service
    from app.services.files.deliverable import WORKSPACE_SOURCE
    mime = str(file.content_type or "").split(";", 1)[0].strip().lower()
    is_image_upload = file_extension(filename) in IMAGE_EXTENSIONS or mime.startswith("image/")

    # Upload precedes AgentRun creation.  Provider OCR here cannot be truthfully attributed to
    # a root Run and may be paid even when the user never sends the message.  Keep this endpoint
    # storage + local text extraction only; the worker enriches text-model attachments after the
    # durable Run exists.  ``model`` remains in the public form contract for compatibility.
    try:
        saved = await user_file_service.save_file(
            user.user_id,
            filename,
            content,
            source=WORKSPACE_SOURCE,
            mime=file.content_type or "",
        )
    except user_file_service.UserFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    try:
        if is_image_upload:
            result = {
                "filename": filename,
                "kind": "image",
                "text": "",
                "chars": 0,
                "truncated": False,
                "status": "ok",
                "note": "原图已保存，将在回合执行阶段读取",
            }
        else:
            result = await asyncio.wait_for(
                document_parse_service.parse_upload(
                    filename,
                    content,
                    newapi_key="",
                    ocr_embedded_images=False,
                    ocr_visual=False,
                ),
                timeout=max(1, int(settings.CHAT_ATTACHMENT_PARSE_TIMEOUT_SECONDS)),
            )
    except asyncio.TimeoutError:
        logger.info("会话上传解析超时，保留文件供后续工具处理: %s", filename)
        result = {
            "filename": filename,
            "kind": "binary",
            "text": "",
            "chars": 0,
            "truncated": False,
            "status": "partial",
            "note": "附件已收下，但解析耗时较长；本轮可继续用工作区文件处理",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话上传解析失败，原始文件仍可通过 file_id 使用 %s: %s", filename, exc)
        result = {
            "filename": filename,
            "kind": "binary",
            "text": "",
            "chars": 0,
            "truncated": False,
            "status": "failed",
            "note": "附件已收下，但内容解析失败",
        }
    return {
        **result,
        "file_id": saved["id"],
        "sha256": hashlib.sha256(content).hexdigest(),
        "versionNo": saved.get("versionNo", 1),
    }


@api_router.post("/chat/runs", status_code=202)
async def create_chat_run(
    request: ChatRequest,
    http_request: Request,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header("", alias="X-Access-Token"),
    protocol_version: str = Header("1", alias="X-Harness-Protocol-Version"),
):
    """Durably accept a Harness Run; execution is owned by ``agent-worker``."""
    from app.services import sse_protocol
    protocol = sse_protocol.negotiate(protocol_version)
    if protocol != sse_protocol.HARNESS:
        raise HTTPException(status_code=406, detail="仅支持 Harness Protocol 1")
    requested_scope = (
        builtin_app_access.scope_for_preset(request.assistant_preset)
        if request.assistant_preset
        else None
    )
    if request.thread_id:
        await builtin_app_access.require_thread_access(
            user,
            request.thread_id,
            expected_scope=requested_scope,
        )
    elif request.assistant_preset:
        await builtin_app_access.require_builtin_app_access(user, request.assistant_preset)
    for reference_thread_id in request.thread_ids or []:
        await builtin_app_access.require_thread_access(user, reference_thread_id)
    attachments_payload = [a.model_dump() for a in request.attachments] if request.attachments else None
    from app.services.chat.tools.client_location import capture_client_network_context
    client_network_context = capture_client_network_context(http_request)
    started = await harness_orchestrator.accept_harness_run(
        user_id=user.user_id, message=request.message, thread_id=request.thread_id,
        workspace_folder_id=request.workspace_folder_id,
        model=request.model, skill_ids=request.skill_ids,
        selected_skills=[item.model_dump() for item in (request.selected_skills or [])],
        knowledge_ids=request.knowledge_ids,
        selected_knowledge=[item.model_dump() for item in (request.selected_knowledge or [])],
        user_context=user,
        regenerate=request.regenerate, subagent_id=request.subagent_id, token=x_access_token or "",
        web_search=request.web_search, agent_mode=request.agent_mode,
        assistant_preset=request.assistant_preset,
        interview_input=request.interview_input.model_dump(mode="json") if request.interview_input else None,
        attachments=attachments_payload, file_ids=request.file_ids, thread_ids=request.thread_ids,
        truncate_from_message_id=request.truncate_from_message_id,
        queue_item_id=request.queue_item_id, queue_lease_token=request.queue_lease_token,
        resume_source_run_id=request.resume_source_run_id,
        client_request_id=request.client_request_id,
        client_network_context=client_network_context,
        thread_origin=(
            origin_for_preset(request.assistant_preset)
            or ("side_chat" if request.side_chat and not request.thread_id else None)
        ),
        protocol=protocol,
        _builtin_access_checked=True,
    )
    # Knowledge selections are consumed asynchronously by the worker. Record the
    # accepted turn context here so the audit trail does not depend on later model
    # or retrieval success, and never stores the user's question or retrieved text.
    selected_knowledge = request.selected_knowledge or []
    knowledge_resources = [
        str(item.id or "").strip() for item in selected_knowledge if str(item.id or "").strip()
    ] or [str(item).strip() for item in (request.knowledge_ids or []) if str(item).strip()]
    if knowledge_resources:
        from app.services.audit.audit_service import record_client_event
        try:
            await record_client_event(
                user=user,
                category="knowledge_access",
                action="在对话中使用知识库",
                resource=",".join(knowledge_resources),
                detail=f"运行 {started['run_id']}",
                ip=str(http_request.client.host if http_request.client else ""),
            )
        except Exception:  # noqa: BLE001 - audit availability must not reject an accepted run
            logger.warning("知识库审计写入失败 run_id=%s", started["run_id"], exc_info=True)
    return JSONResponse(status_code=202, content={
        "run_id": started["run_id"], "thread_id": started["thread_id"],
        "model": started.get("model") or request.model,
        "agent_mode": request.agent_mode,
        "events_url": f"/chat/runs/{started['run_id']}/events",
    })


@api_router.get("/chat/runs/{run_id}/events")
async def subscribe_chat_run(
    run_id: str,
    after: int = 0,
    user: UserContext = Depends(current_user),
    protocol_version: str = Header("1", alias="X-Harness-Protocol-Version"),
):
    """订阅/恢复一个后台 Run 的事件流。用于切换会话、刷新或新窗口接回运行中任务。"""
    from app.services import sse_protocol
    protocol = sse_protocol.negotiate(protocol_version)
    if protocol != sse_protocol.HARNESS:
        raise HTTPException(status_code=406, detail="仅支持 Harness Protocol 1")
    from app.services.tasks import task_run_service
    run = await task_run_service.get_run(run_id, user.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    # Authorize before StreamingResponse sends 200; generator errors are too late
    # to return an HTTP refusal and must not bypass a revoked application ACL.
    await builtin_app_access.require_thread_access(user, run.get("thread_id"))
    return StreamingResponse(
        with_sse_keepalive(harness_orchestrator.subscribe_run(
            user_id=user.user_id,
            run_id=run_id,
            after_sequence=after,
            protocol=protocol,
        )),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@api_router.post("/chat/runs/{run_id}/cancel")
async def cancel_chat_run(
    run_id: str,
    user: UserContext = Depends(current_user),
):
    """停止一个后台 Run。status=pending 表示停止已发出但执行未收敛（P0 三批）：
    前端不得终态收尾，应保持订阅等真正的 run.cancelled 帧。"""
    result = await harness_orchestrator.cancel_chat_run(user.user_id, run_id)
    status = str((result or {}).get("status") or "cancelled")
    return {"success": status == "cancelled", "status": status}


@api_router.get("/chat/requests/{client_request_id}/run")
async def get_run_by_client_request(
    client_request_id: str,
    user: UserContext = Depends(current_user),
):
    """可靠握手（N-02）：按客户端幂等键反查已建 Run。首帧（run.started）丢失时客户端凭此
    发现「服务端其实已受理」，按 run_id 订阅续接而不是恢复草稿引导用户重发第二轮。
    404=该次发送确实没有建成 Run（重发是安全的）。"""
    from app.services.tasks import task_run_service
    try:
        run = await task_run_service.get_run_by_client_request(user.user_id, client_request_id)
    except task_run_service.RunLookupUnavailable as exc:
        raise HTTPException(status_code=503, detail="运行状态服务暂时不可用") from exc
    if not run:
        raise HTTPException(status_code=404, detail="该请求未创建运行任务")
    await builtin_app_access.require_thread_access(user, run.get("thread_id"))
    return run


@api_router.get("/chat/runs/{run_id}")
async def get_chat_run(
    run_id: str,
    user: UserContext = Depends(current_user),
    protocol_version: str = Header("1", alias="X-Harness-Protocol-Version"),
):
    from app.services import sse_protocol
    if sse_protocol.negotiate(protocol_version) != sse_protocol.HARNESS:
        raise HTTPException(status_code=406, detail="仅支持 Harness Protocol 1")
    from app.services.agent_harness import plan_store, run_store
    from app.services.tasks import task_run_service
    task_run = await task_run_service.get_run(run_id, user.user_id)
    if task_run is None:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    await builtin_app_access.require_thread_access(user, task_run.get("thread_id"))
    snapshot = await run_store.get_run_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Harness Run 不存在")
    plan = await plan_store.get_plan_snapshot(run_id)
    if plan and (
        plan.goal_revision != snapshot.goal_revision
        or plan.plan_version != snapshot.plan_version
    ):
        plan = None
    return {
        **snapshot.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json") if plan else None,
    }


@api_router.post("/chat/runs/{run_id}/inputs", status_code=202)
async def submit_chat_run_input(
    run_id: str,
    request: HarnessInputRequest,
    user: UserContext = Depends(current_user),
    x_access_token: str = Header("", alias="X-Access-Token"),
    protocol_version: str = Header("1", alias="X-Harness-Protocol-Version"),
):
    """Submit a user message, clarification answer or plan decision to one Run."""
    from app.services import sse_protocol
    if sse_protocol.negotiate(protocol_version) != sse_protocol.HARNESS:
        raise HTTPException(status_code=406, detail="仅支持 Harness Protocol 1")
    from app.services.tasks import run_input_service, task_run_service
    run = await task_run_service.get_run(run_id, user.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    thread = await builtin_app_access.require_thread_access(user, run.get("thread_id"))
    if thread.origin == "interview":
        raise HTTPException(status_code=409, detail="面试正在处理本轮输入，请等待完成后按当前题目作答")

    if request.kind == "message":
        content = request.content.strip()
        attachments = [item.model_dump() for item in (request.attachments or [])]
        if not content and not attachments:
            raise HTTPException(status_code=400, detail="输入内容不能为空")
        if len(content) > 20_000:
            raise HTTPException(status_code=413, detail="输入内容过长")
        if request.expected_run_id and str(request.expected_run_id) != str(run_id):
            raise HTTPException(status_code=409, detail="Run 已切换，请刷新后重试")
        run_status = str(run.get("status") or "")
        if run_status not in {"running", "routing"}:
            raise HTTPException(status_code=409, detail="Run 状态已变更，请刷新后重试")
        stable_id = request.client_input_id or uuid.uuid4().hex
        existing = await run_input_service.get_by_id(
            input_id=stable_id,
            run_id=run_id,
            user_id=user.user_id,
        )
        if existing is not None:
            return {
                "run_id": run_id,
                "accepted": True,
                "goal_revision": int((run.get("state") or {}).get("goal_revision") or 0),
                "input_id": stable_id,
                "message_id": existing.get("sourceMessageId"),
            }
        from app.services.agent_harness import plan_store, run_store
        revised = False
        goal_revision = 0
        for _ in range(3):
            current = await run_store.get_run_state(run_id)
            if current is None:
                break
            try:
                goal_revision = await plan_store.revise_goal(
                    run_id,
                    expected_state_version=int(current["version"]),
                    goal=content or str(run.get("state", {}).get("pending_input", {}).get("message") or ""),
                )
                revised = True
                break
            except plan_store.PlanConflict:
                continue
        if not revised:
            raise HTTPException(status_code=409, detail="Run 状态已变更，请刷新后重试")
        try:
            from app.services.agent_harness.goal_contract import (
                parse_goal_contract,
                patch_goal_contract_on_steer,
            )
            packed = await run_store.get_run_state(run_id)
            state = dict((packed or {}).get("state") or {})
            patched = patch_goal_contract_on_steer(
                content,
                parse_goal_contract(state.get("goal_contract")),
            )
            await run_store.patch_run_state(
                run_id,
                {"goal_contract": patched.to_state()},
            )
        except Exception:  # noqa: BLE001
            logger.warning("steer 补丁 GoalContract 失败 run=%s", run_id, exc_info=True)
        message_id = await run_input_service.persist_user_message(
            run_id=run_id,
            thread_id=str(run.get("thread_id") or ""),
            content=content,
            attachments=attachments,
        )
        item = await run_input_service.submit(
            run_id=run_id,
            thread_id=str(run.get("thread_id") or ""),
            user_id=user.user_id,
            content=content,
            attachments=attachments or None,
            input_id=stable_id,
            source_message_id=message_id,
        )
        if item is None:
            raise HTTPException(status_code=503, detail="Run 输入存储不可用")
        if bool(item.pop("_created", False)) and message_id is not None:
            await run_input_service.persist_received_event(
                run_id=run_id,
                input_id=str(item.get("id") or stable_id),
                message_id=message_id,
                content=content,
            )
        # Live executing Runs consume steer at the next drive_model safety point.
        # Re-queueing here races HITL waits when finish_job sees a leftover wake.
        return {
            "run_id": run_id,
            "accepted": True,
            "goal_revision": goal_revision,
            "input_id": stable_id,
            "message_id": message_id,
        }

    from app.services.agent_harness import run_store
    from app.services.tasks.plan_service import choice_approves_plan
    current_snapshot = await run_store.get_run_snapshot(run_id)
    expected_kind = (
        "plan_confirmation"
        if current_snapshot and current_snapshot.phase.value == "waiting_confirmation"
        else "clarification"
    )
    if request.kind != expected_kind:
        raise HTTPException(status_code=409, detail=f"当前 Run 等待 {expected_kind}")
    resume_value = request.value if request.value is not None else request.content
    current_state = await run_store.get_run_state(run_id)
    state = ((current_state or {}).get("state") or {})
    pending_rev = (
        state.get("pending_plan_revision")
        if isinstance(state.get("pending_plan_revision"), dict)
        else None
    )
    approves = choice_approves_plan(resume_value)
    if pending_rev:
        next_phase = "executing"
    elif expected_kind == "plan_confirmation" and not approves:
        next_phase = "planning"
    else:
        next_phase = "executing"
    state_patch = {
        "pending_input": None,
        "capability_scope": (
            "default" if next_phase == "executing" else "planning"
        ),
    }
    exit_plan_mode = bool(
        expected_kind == "plan_confirmation" and approves and next_phase == "executing"
    )
    if exit_plan_mode:
        state_patch["agent_mode"] = "standard"
        await task_run_service.set_agent_mode(run_id, "standard")
        from app.services.agent_harness.plan_execution import persist_plan_execution_unlock
        try:
            await persist_plan_execution_unlock(run_id)
        except Exception:  # noqa: BLE001
            logger.warning("计划执行解锁失败 run=%s", run_id, exc_info=True)
    if not await run_store.patch_run_state(
        run_id,
        state_patch,
        phase=next_phase,
    ):
        raise HTTPException(status_code=409, detail="Run 状态已变更，请刷新后重试")
    newapi_key, resolved_model = await harness_orchestrator.prepare_resume_chat(user.user_id, run_id)
    started = await harness_orchestrator.start_resume_chat_run(
        user_id=user.user_id, user_context=user, run_id=run_id,
        resume_value=resume_value, resume_id=request.resume_id,
        token=x_access_token or "", newapi_key=newapi_key,
        resolved_model=resolved_model, protocol=sse_protocol.HARNESS,
    )
    return JSONResponse(status_code=202, content={
        "run_id": started["run_id"],
        "events_url": f"/chat/runs/{started['run_id']}/events",
        "after": int(started.get("after_sequence") or 0),
    })


# ---- 运行中消息队列（任务模式设计稿 §3）：服务端持久化，前端在 Run 结束后逐条派发 ----

@api_router.get("/chat/threads/{thread_id}/queue")
async def get_thread_queue(
    thread_id: str,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    from app.services.tasks import thread_queue_service
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    return {"items": await thread_queue_service.list_queue(thread_id=thread_id, user_id=user.user_id)}


@api_router.post("/chat/threads/{thread_id}/queue")
async def enqueue_thread_message(
    thread_id: str,
    request: Request,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    from app.services.tasks import thread_queue_service
    thread = await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    if thread.origin == "interview":
        raise HTTPException(status_code=409, detail="面试按题目逐一作答，不支持消息排队")
    body = await request.json()
    content = str(body.get("content") or "").strip()
    attachments = body.get("attachments") or []
    # 允许「只挂附件不打字」入队：前端 composer 本来就允许这样发送，这里再按空正文 400
    # 会让运行中拖个文件进来必然报错。两者都空才是真的没内容。
    if not content and not attachments:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    raw_context = body.get("context") if isinstance(body.get("context"), dict) else None
    context = None
    if raw_context is not None:
        # 队列快照是后续执行输入，不接受任意深层 JSON。只保留协议字段并限制数量/长度，
        # 防止超大或投毒 payload 长期留在 Runtime 库中。
        def _pick_items(value, allowed, limit):
            out = []
            for item in value if isinstance(value, list) else []:
                if not isinstance(item, dict):
                    continue
                clean = {
                    key: str(item.get(key) or "")[:500]
                    for key in allowed if item.get(key) is not None
                }
                if clean.get("id"):
                    out.append(clean)
                if len(out) >= limit:
                    break
            return out

        context = {
            "skills": _pick_items(
                raw_context.get("skills"),
                ("id", "recordId", "skillId", "name", "description", "icon", "version", "author", "source"),
                10,
            ),
            # @ 委托目标与队列消息绑定；只保留执行需要的 id 和展示字段。
            "subagent": next(iter(_pick_items(
                [raw_context.get("subagent")],
                ("id", "name", "description", "icon", "type", "scope"),
                1,
            )), None),
            "knowledge": _pick_items(
                raw_context.get("knowledge"), ("id", "name", "permission"), 20),
            "files": _pick_items(raw_context.get("files"), ("id", "filename"), 20),
            # 「最近的对话」引用（2026-07-28）：只存 id + 标题，转录正文在派发轮按归属重新渲染，
            # 不快照正文——排队期间那个会话可能还在继续，派发时该带的是最新内容。
            "threads": _pick_items(raw_context.get("threads"), ("id", "title"), 5),
            "webSearch": bool(raw_context.get("webSearch")),
            "taskMode": bool(raw_context.get("taskMode")),
            "model": str(raw_context.get("model") or "")[:255],
        }
        if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise HTTPException(status_code=400, detail="队列上下文过大")
        for reference in context.get("threads") or []:
            await builtin_app_access.require_thread_access(user, reference.get("id"))
    try:
        item = await thread_queue_service.enqueue(
            thread_id=thread_id, user_id=user.user_id, content=content,
            attachments=attachments, context=context)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # 存储降级时 enqueue 返 None：照常 200 会让前端清空输入框、消息凭空消失（幽灵队列项）
    if item is None:
        raise HTTPException(status_code=503, detail="队列存储不可用，请稍后重试")
    return {"item": item}


@api_router.patch("/chat/threads/{thread_id}/queue")
async def reorder_thread_queue(
    thread_id: str,
    request: Request,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    from app.services.tasks import thread_queue_service
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    body = await request.json()
    ok = await thread_queue_service.reorder(
        thread_id=thread_id, user_id=user.user_id, ordered_ids=list(body.get("orderedIds") or []))
    # 失败也返 200 会让前端把「没生效」当成功（乐观更新不回滚，UI 与服务端就此不一致）
    if not ok:
        raise HTTPException(status_code=409, detail="排序未生效，请刷新后重试")
    return {"success": ok}


@api_router.put("/chat/queue/{item_id}")
async def update_queue_item(item_id: str, request: Request, user: UserContext = Depends(current_user)):
    from app.services.tasks import thread_queue_service
    item_thread_id = await thread_queue_service.get_item_thread_id(item_id=item_id, user_id=user.user_id)
    if item_thread_id:
        await builtin_app_access.require_thread_access(user, item_thread_id)
    body = await request.json()
    ok = await thread_queue_service.update_item(
        item_id=item_id, user_id=user.user_id, content=body.get("content"))
    if not ok:
        raise HTTPException(status_code=409, detail="该条已被派发或不存在，修改未生效")
    return {"success": ok}


@api_router.delete("/chat/queue/{item_id}")
async def delete_queue_item(item_id: str, user: UserContext = Depends(current_user)):
    from app.services.tasks import thread_queue_service
    item_thread_id = await thread_queue_service.get_item_thread_id(item_id=item_id, user_id=user.user_id)
    if item_thread_id:
        await builtin_app_access.require_thread_access(user, item_thread_id)
    ok = await thread_queue_service.delete_item(item_id=item_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=409, detail="该条已被派发或不存在，删除未生效")
    return {"success": ok}


@api_router.post("/chat/threads/{thread_id}/queue/pop")
async def pop_thread_queue(
    thread_id: str,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """Run 结束后前端派发下一条：认领队首（置 dispatching 租约，**不删除**）；同 thread 已有
    dispatching/活动 Run 时返回空——防"先删后发"丢消息与双标签并发派发（§3）。"""
    from app.services.tasks import thread_queue_service
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    return {"item": await thread_queue_service.pop_next(thread_id=thread_id, user_id=user.user_id)}


@api_router.post("/chat/queue/{item_id}/confirm")
async def confirm_thread_queue_item(
    item_id: str, request: Request, user: UserContext = Depends(current_user),
):
    """（兼容入口）删除 dispatching 项。§10.6 P0 加固：必须携带本次派发的 lease_token，
    且项仍处于 dispatching——旧租约/未认领项一律拒绝。主派发链路已改为服务端在
    Run 持久化后原子确认（/chat 携带 queue_item_id+lease_token），前端不再调本接口。"""
    from app.services.tasks import thread_queue_service
    item_thread_id = await thread_queue_service.get_item_thread_id(item_id=item_id, user_id=user.user_id)
    if item_thread_id:
        await builtin_app_access.require_thread_access(user, item_thread_id)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    ok = await thread_queue_service.confirm_dispatched(
        item_id=item_id, user_id=user.user_id,
        lease_token=str((body or {}).get("lease_token") or ""))
    return {"success": ok}


# ---- 立即引导（RunInstruction，§4）：运行中显式让智能体读新要求并调整（引导≠重来） ----

@api_router.get("/chat/threads", response_model=List[ThreadItem])
async def get_threads(
    search: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    scope: str = builtin_app_access.ORDINARY_THREAD_SCOPE,
    user: UserContext = Depends(current_user),
):
    """获取会话历史；传 search 时按标题/消息内容检索。置顶会话优先排序；传 limit 时分页。"""
    normalized_scope = builtin_app_access.normalize_thread_scope(scope)
    if normalized_scope != builtin_app_access.ORDINARY_THREAD_SCOPE:
        await builtin_app_access.require_builtin_app_access(user, normalized_scope)
    return await harness_orchestrator.get_threads(
        user.user_id,
        search=search,
        limit=limit,
        offset=offset,
        scope=normalized_scope,
    )


@api_router.post("/chat/threads/{thread_id}/pin")
async def pin_thread(
    thread_id: str,
    pinned: bool = Body(True, embed=True),
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """置顶 / 取消置顶会话"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    await harness_orchestrator.set_pinned(user.user_id, thread_id, pinned)
    return {"success": True, "pinned": pinned}


@api_router.post("/chat/threads/{thread_id}/rename")
async def rename_thread(
    thread_id: str,
    title: str = Body(..., embed=True),
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """重命名会话（用户手动改名）。"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    clean = await harness_orchestrator.rename_thread(user.user_id, thread_id, title)
    return {"success": True, "title": clean}


@api_router.get("/chat/threads/{thread_id}/model")
async def get_thread_model(
    thread_id: str,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """读取该会话下一次新 Run 的模型设置。"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    return await harness_orchestrator.get_thread_model_setting(user.user_id, thread_id)


@api_router.put("/chat/threads/{thread_id}/model")
async def update_thread_model(
    thread_id: str,
    model: str = Body(..., embed=True),
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """只更新后续 Run；活动 Run / HITL / 已入队项的冻结模型不变。"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    resolved = await harness_orchestrator.set_thread_model(user.user_id, thread_id, model)
    return {"success": True, "model": resolved}


@api_router.delete("/chat/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user)
):
    """删除会话"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    await harness_orchestrator.delete_thread(user.user_id, thread_id)
    return {"message": "删除成功"}


@api_router.get("/chat/threads/{thread_id}/messages")
async def get_thread_messages(
    thread_id: str,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user)
):
    """获取会话消息列表"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    return await harness_orchestrator.get_thread_messages(user.user_id, thread_id)


@api_router.get("/chat/threads/{thread_id}/active-run")
async def get_thread_active_run(
    thread_id: str,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """查询某会话当前是否存在运行中/等待中的 Run。"""
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    return await harness_orchestrator.get_thread_active_run(user.user_id, thread_id) or {}


@api_router.get("/chat/threads/{thread_id}/context-usage")
async def get_thread_context_usage(
    thread_id: str,
    model: Optional[str] = None,
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """估算某会话上下文占用（口径同流式 context.usage），供打开旧会话时恢复占用环。

    model 为该会话下一次发送将使用的模型 id：占用环分母须用它的真实窗口，不能恒用 32K 兜底。
    """
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    return await harness_orchestrator.get_thread_context_usage(user.user_id, thread_id, model)


@api_router.post("/chat/threads/{thread_id}/compact")
async def compact_thread_context(
    thread_id: str,
    model: Optional[str] = Body(None, embed=True),
    scope: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    """手动压缩上下文（composer「+」菜单）：把较早对话压进滚动摘要，释放上下文窗口。

    复用超窗兜底的 force_compact（无视阈值；对话不够长无可压段时返回 compacted=false）。
    返回压缩前后占用（口径同 context-usage），供前端更新占用环与提示释放量。
    """
    from app.services.memory import context_service
    await builtin_app_access.require_thread_access(user, thread_id, expected_scope=scope)
    newapi_key, resolved_model = await harness_orchestrator.prepare_chat(user.user_id, model)
    # 归属校验（不存在/他人会话 → 404），并留存压缩前占用（分母用本模型真实窗口，与占用环一致）
    before = await harness_orchestrator.get_thread_context_usage(user.user_id, thread_id, resolved_model)
    compacted = await context_service.force_compact(thread_id, resolved_model, newapi_key)
    usage = (
        await harness_orchestrator.get_thread_context_usage(user.user_id, thread_id, resolved_model)
        if compacted else before
    )
    return {"compacted": compacted, "before": before, "usage": usage}


@api_router.post("/chat/messages/{message_id}/feedback")
async def set_message_feedback(
    message_id: int,
    feedback: Optional[str] = Body(None, embed=True),
    user: UserContext = Depends(current_user),
):
    """对助手消息点赞/点踩（feedback: up | down | null）。喂 §20 评测。"""
    await builtin_app_access.require_message_access(user, message_id)
    await harness_orchestrator.set_message_feedback(user.user_id, message_id, feedback)
    return {"success": True, "feedback": feedback}


# Memory endpoints（§14 Phase 2：用户长期记忆可见可管理）
@api_router.get("/memories")
async def list_memories(
    type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user: UserContext = Depends(current_user),
):
    """列出本人 active 长期记忆（管理页用），可按内容搜索。Runtime 未配置时 enabled=false + 空表。"""
    from app.services.memory import memory_service
    from app.core.runtime_db import runtime_session
    enabled = runtime_session() is not None and await memory_service.is_enabled(user.user_id)
    items = await memory_service.list_memories(
        user.user_id, mem_type=type, limit=limit, offset=offset, query=q,
    )
    return {"enabled": enabled, "items": items}


@api_router.post("/memories")
async def add_memory(
    type: str = Body(..., embed=True),
    content: str = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """手动添加一条记忆，走完整治理管线（白名单/敏感拒写/去重/以新代旧/总开关硬门禁）。"""
    from app.services.memory import memory_service
    if not await memory_service.is_enabled(user.user_id):
        raise HTTPException(status_code=400, detail="记忆功能已关闭，请先在上方打开记忆开关")
    mid, is_new = await memory_service.store_memory(
        user_id=user.user_id, mem_type=type, content=content, return_new=True,
    )
    if not mid:
        raise HTTPException(status_code=400, detail="记忆未写入（类型不合法、内容为空或含敏感信息）")
    return {"success": True, "id": mid, "is_new": is_new}


@api_router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    type: str | None = Body(None, embed=True),
    content: str | None = Body(None, embed=True),
    user: UserContext = Depends(current_user),
):
    """编辑本人一条记忆（真 PUT 原地改）。取代前端旧的「先新增再删除」模拟——
    那会在编辑文本与原文语义相似时因去重命中旧条、随后删除同一条而导致整条记忆消失。"""
    from app.services.memory import memory_service
    if type is None and content is None:
        raise HTTPException(status_code=400, detail="没有要修改的内容")
    try:
        out = await memory_service.update_memory(
            user.user_id, memory_id, mem_type=type, content=content,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="记忆不存在或无权修改")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "memory": out}


@api_router.delete("/memories")
async def delete_all_memories(user: UserContext = Depends(current_user)):
    """删除本人全部记忆（标记删，保留审计）。复刻 ChatGPT「删除全部记忆」。"""
    from app.services.memory import memory_service
    count = await memory_service.delete_all_memories(user.user_id)
    return {"success": True, "deleted": count}


@api_router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: UserContext = Depends(current_user),
):
    """删除本人一条记忆（标记删，保留审计）。"""
    from app.services.memory import memory_service
    ok = await memory_service.delete_memory(user.user_id, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在或无权删除")
    return {"success": True}


@api_router.get("/memory-settings")
async def get_memory_settings(user: UserContext = Depends(current_user)):
    """获取本人记忆开关。"""
    from app.services.memory import memory_service
    from app.core.runtime_db import runtime_session
    available = runtime_session() is not None
    enabled = available and await memory_service.is_enabled(user.user_id)
    return {"available": available, "enabled": enabled}


@api_router.put("/memory-settings")
async def update_memory_settings(
    enabled: bool = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """设置本人记忆总开关（关=召回注入与轮后抽取全停，已存记忆不动）。
    未配置/落库失败明确报错——不许「看似保存成功实际没生效」。"""
    from app.services.memory import memory_service
    try:
        result = await memory_service.set_enabled(user.user_id, enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="保存开关失败，请稍后重试")
    return {"success": True, "enabled": result}


@api_router.post("/memories/summarize")
async def summarize_memories(user: UserContext = Depends(current_user)):
    """生成（或刷新）记忆摘要：全部记忆 → 模型分区总结，存入个性化配置并返回。"""
    from datetime import datetime, timezone
    from app.services.memory import memory_service, personalization_service
    newapi_key, resolved_model = await harness_orchestrator.prepare_chat(user.user_id, None)
    summary = await memory_service.generate_summary(user.user_id, model=resolved_model, api_key=newapi_key)
    if not summary:
        raise HTTPException(status_code=400, detail="没有可总结的记忆，或模型暂时不可用")
    updated_at = datetime.now(timezone.utc).isoformat()
    await personalization_service.save_personalization(
        user.user_id, {"memorySummary": summary, "memorySummaryAt": updated_at},
    )
    return {"summary": summary, "updatedAt": updated_at}


@api_router.post("/memories/nl-update")
async def nl_update_memories(
    text: str = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """自然语言「添加或更新」记忆（摘要页底部输入框）。返回入库内容列表。"""
    from app.services.memory import memory_service
    if not await memory_service.is_enabled(user.user_id):
        raise HTTPException(status_code=400, detail="记忆功能已关闭，请先打开记忆开关")
    newapi_key, resolved_model = await harness_orchestrator.prepare_chat(user.user_id, None)
    stored = await memory_service.update_from_text(
        user.user_id, text, model=resolved_model, api_key=newapi_key,
    )
    if not stored:
        raise HTTPException(status_code=400, detail="没有可记录的内容（可能为空或含敏感信息）")
    return {"success": True, "stored": stored}


@api_router.get("/personalization")
async def get_personalization(user: UserContext = Depends(current_user)):
    """获取本人个性化设置（关于你/自定义指令/记忆自动管理，复刻 ChatGPT Personalization）。"""
    from app.services.memory import personalization_service
    return await personalization_service.get_personalization(user.user_id)


@api_router.put("/personalization")
async def update_personalization(
    body: Dict[str, Any] = Body(...),
    user: UserContext = Depends(current_user),
):
    """保存本人个性化设置（仅覆盖提交的已知字段）。"""
    from app.services.memory import personalization_service
    return await personalization_service.save_personalization(user.user_id, body)


# Model endpoints
@api_router.get("/models", response_model=List[ModelItem])
async def get_models(user: UserContext = Depends(current_user)):
    """获取用户 Key 可用的非 Embedding 对话模型。"""
    from app.services.platform.key_service import key_service
    user_key = await key_service.get_user_key(user.user_id)
    if not user_key:
        raise HTTPException(status_code=403, detail="当前账号未分配模型 API Key，请联系管理员")
    return await agent_service.get_models(user_key=user_key)


# Agent endpoints
@api_router.get("/agents", response_model=List[AgentItem])
async def get_agents(
    recommend: bool = False,
    search: Optional[str] = None,
    user: UserContext = Depends(current_user)
):
    """获取已发布智能体列表"""
    return await agent_service.get_agents(
        recommend=recommend,
        search=search
    )


# Skill endpoints 已移除（ADR-001）：内存 SkillService 是无数据源的 demo 版，前端已切
# /agent-api/skill/*（Java /ai/skill/* 随 JeecgBoot 下线，目录由 services/skills/skill_catalog 自持）。


# Master config endpoints
@api_router.get("/master-config", response_model=MasterConfig)
async def get_master_config(user: UserContext = Depends(current_user)):
    """获取主智能体配置"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")
    return await config_service.get_master_config()


@api_router.get("/skill-drafts")
async def list_skill_drafts(
    user: UserContext = Depends(current_user),
):
    """Admin-only suggested Skill drafts. Never auto-published."""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")
    from app.services.agent_harness import skill_draft
    return {"items": await skill_draft.list_suggested_drafts()}


@api_router.post("/master-config")
async def save_master_config(
    config: MasterConfigUpdate,
    user: UserContext = Depends(current_user)
):
    """保存主智能体配置"""
    if not is_admin(user):
        raise HTTPException(403, "需要管理员权限")
    await config_service.save_master_config(config)
    return {"message": "保存成功"}


# Internal sync endpoints (HMAC protected)
@api_router.post("/internal/agents/events")
async def agent_event(
    request: Request,
):
    """单事件同步（Java 后端调用）"""
    await verify_internal_request(request)
    try:
        event = AgentEvent.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    from app.services.agents.agent_sync_service import process_event
    result = await process_event(event)
    return result


@api_router.post("/internal/agents/bulk-sync")
async def agent_bulk_sync(
    request: Request,
):
    """批量同步（首次迁移或人工修复，每批最多 200 条）"""
    await verify_internal_request(request)
    try:
        body = AgentBulkSyncRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if len(body.agents) > 200:
        raise HTTPException(status_code=400, detail="单批最多 200 条")
    from app.services.agents.agent_sync_service import process_bulk_sync
    result = await process_bulk_sync(body.agents)
    if result["failed"]:
        raise HTTPException(status_code=503, detail=result)
    return result


# Test endpoint
@api_router.post("/chat/test")
async def test_connection():
    """连通性测试"""
    return {"status": "ok", "message": "Agent API is running"}
