"""Run a frozen API-published workflow without accepting caller credentials.

This service deliberately stays below the HTTP workflow router.  Its only
definition source is the currently approved ``WorkflowVersion`` and every
provider request is configured with the publishing owner's NewAPI key.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select

from app.core.database import async_session
from app.models import EmbeddingModel, WorkflowApp, WorkflowVersion
from app.services.agent_api.access_service import AgentApiPrincipal
from app.services.agent_api.external_session_service import ExternalSessionHandle
from app.services.agent_api.publish_policy import (
    ApiPublishCapabilityError,
    validate_api_workflow_capabilities,
)
from app.services.agents.agent_service import agent_service
from app.services.agents.app_info_publish_service import resolve_workflow_required_model_ids
from app.services.agents.published_visibility import load_published_visibility_version
from app.services.agent_harness.model_usage_audit import ExternalAttribution
from app.services.gateway.tool_invoker import load_published_definition
from app.services.platform.key_service import key_service
from app.services.workflows.workflow_engine import (
    EXECUTOR_METHODS,
    RunContext,
    WorkflowEngine,
    WorkflowExecutionError,
    _run_engine,
    parse_graph,
    resume_workflow,
)
from app.services.workflows.workflow_model_requirements import missing_required_models
from app.services.workflows.workflow_model_requirements import extract_referenced_app_ids


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_SOURCES = frozenset({"openai_api", "embed"})


class ApiExecutionError(RuntimeError):
    """A stable, non-provider error for the external Agent API boundary."""

    def __init__(self, code: str, message: str = ""):
        self.code = str(code or "api_execution_failed")
        super().__init__(message or self.code)


class ApiQuotaError(ApiExecutionError):
    def __init__(self, code: str = "insufficient_quota"):
        super().__init__(code)


@dataclass(frozen=True)
class ApiExecutionRequest:
    input_text: str
    histories: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    source: str = "openai_api"
    attribution: ExternalAttribution | None = None
    external_session: ExternalSessionHandle | None = None
    file_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ApiExecutionStreamEvent:
    type: str
    delta: str = ""
    result: dict[str, Any] | None = None
    error: ApiExecutionError | None = None


def _safe_session_id(value: str) -> str:
    session_id = str(value or "")
    if not _SAFE_SESSION_ID.fullmatch(session_id):
        raise ApiExecutionError("invalid_session")
    return session_id


def _validated_request(request: ApiExecutionRequest) -> ApiExecutionRequest:
    if str(request.source or "") not in _ALLOWED_SOURCES:
        raise ApiExecutionError("invalid_source")
    session_id = _safe_session_id(request.session_id)
    if not isinstance(request.histories, list) or any(not isinstance(item, dict) for item in request.histories):
        raise ApiExecutionError("invalid_history")
    if not isinstance(request.input_text, str):
        raise ApiExecutionError("invalid_input")
    if not isinstance(request.file_ids, list) or any(not isinstance(file_id, str) for file_id in request.file_ids):
        raise ApiExecutionError("invalid_file_ids")
    return ApiExecutionRequest(
        input_text=request.input_text,
        histories=[dict(item) for item in request.histories],
        session_id=session_id,
        source=request.source,
        attribution=request.attribution,
        external_session=request.external_session,
        file_ids=[str(file_id)[:64] for file_id in request.file_ids],
    )


async def load_active_api_version(app_id: str, owner_user_id: str) -> WorkflowVersion:
    """Resolve exactly the active approved version; no version is caller-selectable."""
    async with async_session() as session:
        app = await session.get(WorkflowApp, app_id)
        if (
            app is None
            or str(app.status or "") != "published"
            or str(app.owner_user_id or "") != str(owner_user_id or "")
        ):
            raise ApiExecutionError("api_release_inactive")
        version = await load_published_visibility_version(session, app_id)
        if version is None or not bool(getattr(app, "api_enabled", False)):
            raise ApiExecutionError("api_release_inactive")
        return version


async def _required_models_and_embeddings(workflow_json: str) -> tuple[list[str], list[str]]:
    async with async_session() as session:
        required = await resolve_workflow_required_model_ids(session, workflow_json)
        embeddings = (
            await session.execute(
                select(EmbeddingModel.model_id).where(
                    or_(EmbeddingModel.enabled == 1, EmbeddingModel.is_active == 1)
                )
            )
        ).scalars().all()
    return required, [str(item).strip() for item in embeddings if str(item or "").strip()]


async def _resolve_owner_model_configuration(provider_key: str, workflow_json: str) -> str:
    """Validate the frozen graph against the publisher's, never caller's, catalog."""
    try:
        required_models, embedding_models = await _required_models_and_embeddings(workflow_json)
        chat_models = await agent_service.get_models(
            user_key=provider_key, raise_on_lookup_failure=True,
        )
    except Exception as exc:
        raise ApiExecutionError("api_model_access_denied") from exc

    available: list[str] = list(embedding_models)
    for model in chat_models:
        model_id = str(getattr(model, "id", model) or "").strip()
        if model_id and model_id not in available:
            available.append(model_id)
    if missing_required_models(required_models, available):
        raise ApiExecutionError("api_model_access_denied")
    default = next((str(getattr(model, "id", "") or "") for model in chat_models if getattr(model, "is_default", False)), "")
    if not default and chat_models:
        default = str(getattr(chat_models[0], "id", "") or "")
    if not default and not required_models:
        raise ApiExecutionError("api_model_access_denied")
    return default


def _runtime_capabilities_are_safe(workflow_json: str) -> dict[str, Any]:
    try:
        validate_api_workflow_capabilities(workflow_json, external_context_ready=True)
        graph = parse_graph(workflow_json)
    except ApiPublishCapabilityError as exc:
        raise ApiExecutionError("api_runtime_capability_unsupported") from exc
    except WorkflowExecutionError as exc:
        raise ApiExecutionError("api_runtime_capability_unsupported") from exc
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            raise ApiExecutionError("api_runtime_capability_unsupported")
        node_type = str(node.get("flowNodeType") or node.get("nodeType") or "")
        if node_type not in EXECUTOR_METHODS:
            raise ApiExecutionError("api_runtime_capability_unsupported")
        if node_type in {"appModule", "pluginModule"} and not str(node.get("pluginId") or "").strip():
            raise ApiExecutionError("api_runtime_capability_unsupported")
    return graph


async def _validate_api_workflow_tree(
    root_version: WorkflowVersion,
    owner_user_id: str,
) -> None:
    """Validate every currently reachable sub-workflow before touching an owner key.

    ``appModule`` and ``pluginModule`` resolve their own currently approved
    definition at runtime.  Checking only the root snapshot would therefore
    permit a later child release to escape the API-channel/owner policy.
    """
    verified: set[str] = set()

    async with async_session() as session:
        async def _visit(
            version: WorkflowVersion,
            path: tuple[str, ...],
            runtime_definition: str | None = None,
        ) -> None:
            version_app_id = str(version.app_id or "")
            if not version_app_id or version_app_id in path:
                raise ApiExecutionError("api_runtime_capability_unsupported")
            if version_app_id in verified:
                return
            # Child nodes execute through tool_invoker.load_tool_definition,
            # whose safe source is definition.published_json.  Use that exact
            # source for validation so a missing published copy cannot fall
            # through to a child draft in the external API runtime.
            workflow_json = runtime_definition if runtime_definition is not None else str(version.definition_json or "")
            _runtime_capabilities_are_safe(workflow_json)
            next_path = (*path, version_app_id)
            for child_app_id in extract_referenced_app_ids(workflow_json):
                child_id = str(child_app_id or "")
                if not child_id or child_id in next_path:
                    raise ApiExecutionError("api_runtime_capability_unsupported")
                child_app = await session.get(WorkflowApp, child_id)
                if (
                    child_app is None
                    or str(child_app.status or "") != "published"
                    or str(child_app.owner_user_id or "") != str(owner_user_id or "")
                ):
                    raise ApiExecutionError("api_runtime_capability_unsupported")
                child_version = await load_published_visibility_version(session, child_id)
                if (
                    child_version is None
                    or str(child_version.app_id or "") != child_id
                ):
                    raise ApiExecutionError("api_runtime_capability_unsupported")
                child_definition = await load_published_definition(child_id)
                if not child_definition:
                    raise ApiExecutionError("api_runtime_capability_unsupported")
                await _visit(child_version, next_path, str(child_definition))
            verified.add(version_app_id)

        await _visit(root_version, ())


def _context_for_api_execution(
    principal: AgentApiPrincipal,
    request: ApiExecutionRequest,
    provider_key: str,
    default_model: str,
) -> RunContext:
    external_session = request.external_session
    if external_session is not None and (
        str(external_session.app_id) != str(principal.app_id)
        or str(external_session.api_key_id) != str(principal.key_id)
        or str(external_session.owner_user_id) != str(principal.owner_user_id)
        or str(external_session.session_id) != str(request.session_id)
    ):
        raise ApiExecutionError("external_session_scope_invalid")
    if request.file_ids and external_session is None:
        raise ApiExecutionError("external_session_required")
    run_id = f"api_run_{uuid.uuid4().hex}"
    return RunContext(
        input_text=request.input_text,
        variables={"histories": request.histories},
        # API and embed runtimes intentionally have no platform JWT.  Nodes
        # which need one must have been rejected by the capability policy.
        token="",
        # An external caller must never inherit the publisher's personal file
        # workspace. Provider access is supplied explicitly through llm_api_key.
        user_id="" if external_session is not None else principal.owner_user_id,
        app_id=principal.app_id,
        run_id=run_id,
        # This is an in-memory execution namespace only: it is never read from
        # or written to ChatThread/ChatMessage.
        thread_id=f"api:{principal.key_id}:{request.session_id}",
        audit_run_id=run_id,
        audit_root_run_id=run_id,
        audit_execution_segment=run_id,
        audit_purpose="workflow_node",
        external_attribution=request.attribution,
        external_execution=external_session is not None,
        external_session_id=(external_session.id if external_session else None),
        external_workspace_ref=(external_session.workspace_ref if external_session else None),
        external_file_ids=list(request.file_ids),
        api_runtime=True,
        llm_api_key=provider_key,
        default_model=default_model,
    )


async def _run_api_version(
    version: WorkflowVersion,
    ctx: RunContext,
) -> dict[str, Any]:
    """Execute directly through WorkflowEngine, retaining its result contract."""
    started = time.monotonic()
    engine: WorkflowEngine | None = None
    status = "success"
    error_message: str | None = None
    interactive: dict[str, Any] | None = None
    try:
        graph = parse_graph(version.definition_json)
        engine = WorkflowEngine(graph, ctx)
        interactive = await _run_engine(engine)
    except WorkflowExecutionError as exc:
        status, error_message = "failed", str(exc)
    except Exception as exc:  # preserve the public boundary; do not leak provider internals
        status, error_message = "failed", "api_execution_failed"

    output = "".join(text for _seq, text in sorted(ctx.output_parts, key=lambda pair: pair[0]) if text)
    if status == "success" and ctx.uncaught_errors:
        error_message = "；".join(ctx.uncaught_errors[:3])
        if not output:
            status = "failed"
    interaction = None
    if interactive and status == "success":
        if not ctx.external_execution or not ctx.external_session_id:
            raise ApiExecutionError("external_session_required")
        from app.services.agent_api.external_interaction_service import create_external_interaction

        interaction = await create_external_interaction(
            ctx.external_session_id, ctx.run_id, interactive, str(version.id or ""),
        )
        status = "waiting_external_input"
    return {
        "runId": ctx.run_id,
        "status": status,
        "output": output,
        "errorMessage": error_message,
        "durationMs": int((time.monotonic() - started) * 1000),
        "nodeRuns": [run.to_dict() for run in ctx.node_runs],
        "outputs": ctx.outputs,
        "files": ctx.generated_files,
        **({"interaction": interaction} if interaction else {}),
    }


async def _prepare_api_execution(
    principal: AgentApiPrincipal,
    request: ApiExecutionRequest,
    stream_output: Callable[[int, str], Awaitable[None]] | None = None,
) -> tuple[WorkflowVersion, RunContext]:
    request = _validated_request(request)
    version = await load_active_api_version(principal.app_id, principal.owner_user_id)
    await _validate_api_workflow_tree(version, principal.owner_user_id)
    provider_key = await key_service.get_user_key(principal.owner_user_id)
    if not provider_key:
        raise ApiQuotaError()
    default_model = await _resolve_owner_model_configuration(provider_key, str(version.definition_json or ""))
    ctx = _context_for_api_execution(principal, request, provider_key, default_model)
    ctx.stream_output = stream_output
    return version, ctx


async def execute_published_api_workflow(
    principal: AgentApiPrincipal,
    request: ApiExecutionRequest,
) -> dict[str, Any]:
    version, ctx = await _prepare_api_execution(principal, request)
    return await _run_api_version(version, ctx)


async def resume_published_api_workflow(
    principal: AgentApiPrincipal,
    request: ApiExecutionRequest,
    *,
    resume_id: str,
    value: Any,
) -> dict[str, Any]:
    """Continue the same LangGraph checkpoint under the owning isolated session."""
    version, ctx = await _prepare_api_execution(principal, request)
    if not ctx.external_execution or not ctx.external_session_id:
        raise ApiExecutionError("external_session_required")
    try:
        result = await resume_workflow(
            str(version.definition_json or ""), str(resume_id or ""), value,
            token="", user_id="", app_id=principal.app_id,
            llm_api_key=ctx.llm_api_key, default_model=ctx.default_model,
            thread_id=ctx.thread_id, audit_run_id=ctx.audit_run_id,
            audit_root_run_id=ctx.audit_root_run_id,
            audit_execution_segment=ctx.audit_execution_segment,
            external_execution=True, external_session_id=ctx.external_session_id,
            external_workspace_ref=ctx.external_workspace_ref,
            external_file_ids=ctx.external_file_ids,
        )
    except Exception as exc:
        raise ApiExecutionError("api_execution_failed") from exc
    interactive = result.get("interactive") if isinstance(result, dict) else None
    if result.get("status") == "waiting" and isinstance(interactive, dict):
        from app.services.agent_api.external_interaction_service import create_external_interaction

        interaction = await create_external_interaction(
            ctx.external_session_id, str(result.get("runId") or ""), interactive, str(version.id or ""),
        )
        return {**result, "status": "waiting_external_input", "interaction": interaction}
    return result


async def stream_published_api_workflow(
    principal: AgentApiPrincipal,
    request: ApiExecutionRequest,
) -> AsyncGenerator[ApiExecutionStreamEvent, None]:
    """Yield engine deltas followed by exactly one terminal result event."""
    queue: asyncio.Queue[ApiExecutionStreamEvent] = asyncio.Queue()

    async def _emit(_sequence: int, delta: str) -> None:
        if delta:
            await queue.put(ApiExecutionStreamEvent(type="delta", delta=delta))

    async def _runner() -> None:
        try:
            version, ctx = await _prepare_api_execution(principal, request, _emit)
            result = await _run_api_version(version, ctx)
            await queue.put(ApiExecutionStreamEvent(type="completed", result=result))
        except ApiExecutionError as exc:
            await queue.put(ApiExecutionStreamEvent(type="error", error=exc))
        except Exception:
            await queue.put(ApiExecutionStreamEvent(type="error", error=ApiExecutionError("api_execution_failed")))

    task = asyncio.create_task(_runner())
    try:
        while True:
            event = await queue.get()
            yield event
            if event.type in {"completed", "error"}:
                return
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
