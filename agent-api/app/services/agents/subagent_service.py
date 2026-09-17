"""SubagentAdapter：主智能体调用子智能体的统一缝（ADR-005/027/046）。

两条调用面共用本模块：
- `@` 进入会话模式：整轮转交（harness_orchestrator 派发分支）；
- `call_subagent` 工具：主模型在工具循环内委派自包含子任务（main_agent 闭包注入 runner）。

结果协议（§10.3 全集）：succeeded / failed / needs_input（携带 resume_id + interactive，
经 /chat/resume 续接）。异常与墙钟超时（SUBAGENT_TIMEOUT_SECONDS）一律收敛为 failed。
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select

from app.core.auth import UserContext
from app.core.config import settings
from app.core.database import async_session
from app.models import WorkflowApp, WorkflowDefinition
from app.services.agents.published_visibility import (
    load_approved_visibility_map,
    load_user_relation_ids,
    publish_visibility_allows_user,
    workflow_tenant_visible_clause,
    user_can_run_published_app,
)
logger = logging.getLogger(__name__)

# 可作为子智能体被主对话调用的应用类型（工具类应用不在此列）
RUNNABLE_TYPES = ("chatAgent", "workflow")


def _tenant_clause(tenant_id: str):
    """智能体/工作流候选的租户条件，受临时开关统一控制。"""
    return workflow_tenant_visible_clause(WorkflowApp.tenant_id, tenant_id)


async def list_callable_subagent_ids(user: UserContext) -> set:
    """当前用户**全部**可执行内部子智能体 ID（召回层权限权威全集，deny-by-default）。

    与旧 list_subagents 的差异（2026-07-22 语义发现升级）：
    - 不再有 update_time limit 200 / 前 50 截断——展示分页只属于 API 展示层；
    - 硬过滤：status=published + ai_app_type∈RUNNABLE_TYPES + published_json 非空 +
      published_version>0 + tenant 匹配；
    - 权限 deny-by-default：owner 放行；否则命中 approved 版本可见角色/部门；
      可见角色与部门皆空、或无 approved 版本 → 非 owner 拒绝；
    - 批量判定：先批量取回权威 approved 版本（一次查询），无权威版本的直接拒绝
      （与执行侧 user_can_run_published_app 同一前提，2026-07-28 对齐）；其上再用
      发布流程同步的 app_role/app_dept 一次 IN 查询放行，未命中部分回落版本可见性。
      全程不做逐应用 N+1。
    """
    async with async_session() as session:
        candidates = (
            await session.execute(
                select(
                    WorkflowApp.id,
                    WorkflowApp.owner_user_id,
                    WorkflowDefinition.published_version,
                )
                .join(WorkflowDefinition, WorkflowDefinition.app_id == WorkflowApp.id)
                .where(
                    WorkflowApp.status == "published",
                    WorkflowApp.ai_app_type.in_(RUNNABLE_TYPES),
                    _tenant_clause(user.tenant_id),
                    WorkflowDefinition.published_json.isnot(None),
                    WorkflowDefinition.published_json != "",
                    WorkflowDefinition.published_version > 0,
                )
            )
        ).all()
        if not candidates:
            return set()
        allowed = {str(app_id) for app_id, owner, _pv in candidates if owner == user.user_id}
        rest = [(str(app_id), int(pv or 0)) for app_id, owner, pv in candidates
                if str(app_id) not in allowed]
        if not rest:
            return allowed

        relation_role_ids, relation_dept_ids = await load_user_relation_ids(session, user)
        rest_ids = [app_id for app_id, _pv in rest]
        # 权威 approved 版本一次批量取回。它不只是「角色/部门可见性的来源」，更是
        # **执行侧的唯一准入依据**：user_can_run_published_app 只读版本上的 visible_*，
        # 版本缺失即无条件拒绝。召回必须用同一把尺子，见下方循环。
        visibility = await load_approved_visibility_map(
            session, rest_ids, {app_id: pv for app_id, pv in rest},
        )
        for app_id, _pv in rest:
            vis = visibility.get(app_id)
            if not vis:
                # 召回口径对齐执行口径（2026-07-28 修复）：published_version 指针指向的
                # 版本不再 approved（撤回 / 删除 / 指针漂移）时执行侧必拒。此前
                # app_role/app_dept 命中会**跳过**这个前提直接放行，于是候选清单里出现
                # 「模型看得见、选得中、一执行就报『子智能体不存在或无访问权限』」的幽灵：
                # deny 方向没有越权风险，但表现为委派中途莫名失败、而召回日志一切正常。
                # 不可执行的一律不进候选。
                continue
            # 只认版本可见性——与 user_can_run_published_app 逐字同一判据（2026-07-28 用户拍板）。
            #
            # 此前这里还有一条 app_role/app_dept 支路：表命中即放行，用来覆盖 WorkflowVersion
            # 上 visible_* 缺失的存量应用。但**执行侧从不读那两张表**，于是这条支路放进来的
            # 恰好是执行侧必拒的那一批——模型看得见、选得中，一执行就报「子智能体不存在或无
            # 访问权限」。deny 方向没有越权风险，代价是「委派中途莫名失败」这种最难排查的形态。
            #
            # 拍板口径：这类「可见但不可执行」的应用不该进委派候选，让它以**推荐卡**的形式
            # 出现给用户自己点（CLAUDE.md 铁律①「推荐仅兜底」）。推荐卡走的是另一条路
            # ——turn_context_builder._retrieve_agents → Qdrant 向量检索 + 角色/部门过滤，
            # 不经过本函数，所以收紧这里不会让它们从推荐里消失。
            role_ids, dept_ids = vis
            if publish_visibility_allows_user(
                role_ids, dept_ids, user,
                relation_role_ids=relation_role_ids,
                relation_dept_ids=relation_dept_ids,
            ):
                allowed.add(app_id)
        return allowed

# 主对话 call_subagent 委派轮回写用的专用运行会话标题：委派对话与「我的智能体」
# 运行页共用同一批 app_id 会话，用固定标题把「来自主对话的委派」聚成一条会话（Q2/2026-07-13）。
DELEGATION_SESSION_TITLE = "主对话委派"


async def list_subagents(user: UserContext, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出当前用户可调用的、已发布的子智能体（完整 ACL 全集，无 limit200/前50 截断）。

    2026-07-22 起基于 list_callable_subagent_ids 的权限全集工作；展示分页/条数限制
    只允许发生在 API 展示层（app/api/router.py），不得影响召回权限全集。
    """
    allowed = await list_callable_subagent_ids(user)
    if not allowed:
        return []
    async with async_session() as session:
        query = select(WorkflowApp).where(WorkflowApp.id.in_(list(allowed)))
        if keyword and keyword.strip():
            like = f"%{keyword.strip()}%"
            query = query.where(or_(WorkflowApp.name.like(like), WorkflowApp.description.like(like)))
        rows = (
            await session.execute(query.order_by(WorkflowApp.update_time.desc()))
        ).scalars().all()

    return [
        {
            "id": app.id,
            "name": app.name,
            "description": app.description or "",
            "icon": app.app_icon or "",
            "type": app.ai_app_type,
            "scope": "owned" if app.owner_user_id == user.user_id else "shared",
        }
        for app in rows
    ]


async def _resolve_accessible(user: UserContext, subagent_id: str) -> Optional[WorkflowApp]:
    """加载子智能体并校验可调用（按发布可见角色/部门）；不可访问返回 None。"""
    async with async_session() as session:
        app = await session.get(WorkflowApp, subagent_id)
        if not app:
            return None
        if await user_can_run_published_app(session, app, user):
            return app
        return None


def _provider_audit_identity(
    *,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
    parent_tool_call_id: str = "",
) -> tuple[str, str, str, str]:
    """Resolve child Provider ownership without inventing a synthetic root.

    ``call_subagent`` runs inside the main tool task and can inherit its exact call id.  Explicit
    ``@agent`` dispatch and HITL resume execute outside that context, so their caller passes the
    already-created main Run explicitly.
    """
    try:
        from app.services.chat.tools.base import current_tool_context

        context = current_tool_context()
        if context is not None:
            return (
                str(run_id or context.run_id or ""),
                str(thread_id or context.thread_id or ""),
                str(root_run_id or context.root_run_id or ""),
                str(parent_tool_call_id or context.call_id or ""),
            )
    except Exception:  # noqa: BLE001
        pass
    return run_id, thread_id, root_run_id, parent_tool_call_id


def _provider_audit_lineage(
    *,
    parent_logical_call_id: str = "",
    execution_segment: str = "",
) -> tuple[str, str]:
    try:
        from app.services.chat.tools.base import current_tool_context

        context = current_tool_context()
        if context is not None:
            return (
                parent_logical_call_id or str(context.parent_logical_call_id or ""),
                execution_segment or str(context.execution_segment or ""),
            )
    except Exception:  # noqa: BLE001
        pass
    return parent_logical_call_id, execution_segment


async def run_subagent(
    *,
    user: UserContext,
    token: str,
    newapi_key: str,
    default_model: str,
    subagent_id: str,
    message: str,
    histories: Optional[List[Dict[str, str]]] = None,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
    audit_parent_tool_call_id: str = "",
    audit_parent_logical_call_id: str = "",
    audit_execution_segment: str = "",
) -> Dict[str, Any]:
    """派发一轮给子智能体（走 interrupt-capable 引擎，支持 HITL 挂起）。

    结果协议（§10.3 全集）：succeeded / failed / **needs_input**（工作流交互节点挂起）。
    needs_input 时携带 `resume_id`（checkpoint 恢复键）+ `interactive`（type/params），主对话
    据此置 Task Run 为 waiting 并向用户呈现表单/选项。异常一律收敛为 failed。
    """
    app = await _resolve_accessible(user, subagent_id)
    if not app:
        return {"status": "failed", "text": "子智能体不存在或无访问权限", "subagent_id": subagent_id, "subagent_name": ""}
    if app.status != "published":
        return {"status": "failed", "text": f"「{app.name}」尚未发布，无法调用", "subagent_id": subagent_id, "subagent_name": app.name}

    from app.services.gateway import tool_invoker
    from app.services.workflows.workflow_engine import execute_workflow

    published = await tool_invoker.load_published_definition(str(app.id))
    if not published:
        return {"status": "failed", "text": f"「{app.name}」没有已发布的工作流版本", "subagent_id": str(app.id), "subagent_name": app.name}

    variables: Dict[str, Any] = {"histories": histories} if histories else {}
    audit_run_id, audit_thread_id, audit_root_run_id, audit_parent_tool_call_id = (
        _provider_audit_identity(
            run_id=audit_run_id,
            thread_id=audit_thread_id,
            root_run_id=audit_root_run_id,
            parent_tool_call_id=audit_parent_tool_call_id,
        )
    )
    audit_parent_logical_call_id, audit_execution_segment = _provider_audit_lineage(
        parent_logical_call_id=audit_parent_logical_call_id,
        execution_segment=audit_execution_segment,
    )
    try:
        result = await _with_wall_clock(execute_workflow(
            published, message or "", variables,
            token=token or "", user_id=user.user_id, app_id=str(app.id),
            llm_api_key=newapi_key or "", default_model=default_model or "",
            thread_id=audit_thread_id,
            audit_run_id=audit_run_id,
            audit_root_run_id=audit_root_run_id,
            audit_parent_tool_call_id=audit_parent_tool_call_id,
            audit_parent_logical_call_id=audit_parent_logical_call_id,
            audit_execution_segment=audit_execution_segment,
            audit_purpose="subagent_model",
        ))
    except asyncio.TimeoutError:
        logger.warning("子智能体 %s 执行超时（>%ss）", subagent_id, settings.SUBAGENT_TIMEOUT_SECONDS)
        return {"status": "failed", "text": f"子智能体执行超时（超过 {settings.SUBAGENT_TIMEOUT_SECONDS}s），已中止",
                "subagent_id": str(app.id), "subagent_name": app.name}
    except Exception as exc:  # noqa: BLE001
        logger.warning("子智能体 %s 执行异常", subagent_id, exc_info=True)
        return {"status": "failed", "text": f"子智能体执行异常：{str(exc)[:200]}", "subagent_id": str(app.id), "subagent_name": app.name}
    return _map_workflow_result(result, app)


async def run_subagent_stream(
    *,
    user: UserContext,
    token: str,
    newapi_key: str,
    default_model: str,
    subagent_id: str,
    message: str,
    histories: Optional[List[Dict[str, str]]] = None,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
    audit_parent_tool_call_id: str = "",
    audit_parent_logical_call_id: str = "",
    audit_execution_segment: str = "",
):
    """流式派发一轮给子智能体：逐节点吐工作流执行过程，供主对话「子智能体工作窗口」实时展示。

    yield 事件（dict）：
    - {"type":"node","label","status","node_id"}    工作流节点起止（label=节点名，status=success/failed/…）
    - {"type":"delta","text"}                        节点产出的输出文本（增量）
    - {"type":"reasoning","node_id","text"}          节点 LLM 的思考（推理模型 reasoningText，节点粒度）
    - {"type":"result", <succeeded/failed/needs_input 结果协议>}  最终结果（同 run_subagent）

    记忆隔离同 run_subagent（只透传 histories + 自包含 message）；langgraph 未启用或图不支持
    时回退为「跑完再一次性吐输出+结果」；异常/墙钟超时收敛为 failed result。
    """
    def _fail(text: str, name: str = ""):
        return {"type": "result", "status": "failed", "text": text,
                "subagent_id": subagent_id, "subagent_name": name}

    app = await _resolve_accessible(user, subagent_id)
    if not app:
        yield _fail("子智能体不存在或无访问权限"); return
    if app.status != "published":
        yield _fail(f"「{app.name}」尚未发布，无法调用", app.name); return

    from app.services.gateway import tool_invoker
    published = await tool_invoker.load_published_definition(str(app.id))
    if not published:
        yield _fail(f"「{app.name}」没有已发布的工作流版本", app.name); return

    # 首帧先交付已通过 ACL / 发布态校验的真实身份。call_subagent 工具路径已经在
    # tool_started 阶段发布 subagent.started，因此会忽略这张内部帧；显式 @ 整轮委派
    # 没有工具阶段，靠它立即建立同一张执行团队运行档，不能再等到最终 result 才知道名称。
    yield {
        "type": "started",
        "subagent_id": str(app.id),
        "subagent_name": app.name,
        "icon": getattr(app, "app_icon", "") or "",
    }

    from app.services.workflows.workflow_engine import (
        RunContext, WorkflowEngine, WorkflowExecutionError, parse_graph,
    )
    from app.services.workflow_runtime import CheckpointerRequiredError, UnsupportedGraphError
    from app.services.workflow_runtime.compiler import langgraph_enabled, stream_engine_with_langgraph

    audit_run_id, audit_thread_id, audit_root_run_id, audit_parent_tool_call_id = (
        _provider_audit_identity(
            run_id=audit_run_id,
            thread_id=audit_thread_id,
            root_run_id=audit_root_run_id,
            parent_tool_call_id=audit_parent_tool_call_id,
        )
    )
    audit_parent_logical_call_id, audit_execution_segment = _provider_audit_lineage(
        parent_logical_call_id=audit_parent_logical_call_id,
        execution_segment=audit_execution_segment,
    )
    ctx = RunContext(
        input_text=message or "",
        variables={"histories": histories} if histories else {},
        token=token or "", user_id=user.user_id, app_id=str(app.id),
        run_id=uuid.uuid4().hex,
        thread_id=audit_thread_id,
        audit_run_id=audit_run_id,
        audit_root_run_id=audit_root_run_id,
        audit_parent_tool_call_id=audit_parent_tool_call_id,
        audit_parent_logical_call_id=audit_parent_logical_call_id,
        audit_execution_segment=(audit_execution_segment or f"wf_{uuid.uuid4().hex}"),
        audit_purpose="subagent_model",
        llm_api_key=newapi_key or "", default_model=default_model or "",
    )
    status, error_message, interactive = "success", None, None
    engine: Optional[WorkflowEngine] = None
    emitted_parts = 0
    emitted_reasoning: set = set()

    def _drain_output():
        nonlocal emitted_parts
        evs = []
        while emitted_parts < len(ctx.output_parts):
            _seq, text = ctx.output_parts[emitted_parts]
            emitted_parts += 1
            if text:
                evs.append({"type": "delta", "text": text})
        return evs

    def _drain_reasoning():
        # 节点 LLM 的 reasoningText（节点粒度，节点完成时才写入 ctx.outputs）：新出现的才发
        evs = []
        for nid, outs in list(ctx.outputs.items()):
            if nid in emitted_reasoning or not isinstance(outs, dict):
                continue
            rt = outs.get("reasoningText")
            if rt:
                emitted_reasoning.add(nid)
                evs.append({"type": "reasoning", "node_id": nid, "text": str(rt)})
        return evs

    async def _produce():
        nonlocal interactive, status, error_message
        graph = parse_graph(published)
        eng = WorkflowEngine(graph, ctx)
        nonlocal engine
        engine = eng
        if langgraph_enabled():
            try:
                async for kind, data in stream_engine_with_langgraph(eng):
                    if kind == "node":
                        yield {"type": "node", "label": data.get("nodeLabel") or "",
                               "status": data.get("status") or "", "node_id": data.get("nodeId") or ""}
                        for e in _drain_reasoning():
                            yield e
                        for e in _drain_output():
                            yield e
                    elif kind == "interactive":
                        interactive = data
            except CheckpointerRequiredError as exc:
                status, error_message = "failed", str(exc)
            except UnsupportedGraphError:
                await eng.run()
                for e in _drain_reasoning():
                    yield e
                for e in _drain_output():
                    yield e
        else:
            await eng.run()
            for e in _drain_reasoning():
                yield e
            for e in _drain_output():
                yield e

    timeout = int(settings.SUBAGENT_TIMEOUT_SECONDS or 0)
    try:
        if timeout > 0:
            async with asyncio.timeout(timeout):
                async for ev in _produce():
                    yield ev
        else:
            async for ev in _produce():
                yield ev
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("子智能体 %s 流式执行超时（>%ss）", subagent_id, timeout)
        yield _fail(f"子智能体执行超时（超过 {timeout}s），已中止", app.name); return
    except WorkflowExecutionError as exc:
        status, error_message = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("子智能体 %s 流式执行异常", subagent_id, exc_info=True)
        yield _fail(f"子智能体执行异常：{str(exc)[:200]}", app.name); return

    output = "".join(text for _seq, text in sorted(ctx.output_parts, key=lambda p: p[0]) if text)
    partial_error = ""
    if status == "success" and ctx.uncaught_errors:
        error_message = "；".join(ctx.uncaught_errors[:3])
        if not output:
            status = "failed"
        else:
            # 「有输出的部分失败」：D-5 让未捕获的节点错误不终止全图，所以这里仍是 success。
            # 但错误文本此前到此为止就没人读了——主对话拿到半截结果 + succeeded，模型据此
            # 宣布委派完成，落库也记成成功，用户与监控都看不出这次委派炸了一半。
            partial_error = error_message
    base = {"subagent_id": str(app.id), "subagent_name": app.name}
    if interactive and status == "success":
        yield {"type": "result", "status": "needs_input", "text": output or "",
               "resume_id": (interactive or {}).get("resumeId"), "interactive": interactive,
               "files": list(ctx.generated_files), **base}
    elif status == "failed":
        yield {"type": "result", "status": "failed", "text": error_message or "子智能体执行失败", **base}
    else:
        yield {"type": "result", "status": "succeeded",
               "text": _with_partial_failure_note(output or "（工作流无文本输出）", partial_error),
               "files": list(ctx.generated_files),
               **({"partial_failure": partial_error[:500]} if partial_error else {}), **base}


async def _with_wall_clock(coro):
    """墙钟超时护栏（开发计划 Phase 3）：慢子智能体不再只能靠用户手动停。0=不限。"""
    timeout = int(settings.SUBAGENT_TIMEOUT_SECONDS or 0)
    if timeout <= 0:
        return await coro
    return await asyncio.wait_for(coro, timeout=timeout)


async def persist_delegation_turn(
    *, user_id: str, subagent_id: str, task: str, result_text: str,
    parent_thread_id: str = "",
    attachments: Optional[List[dict]] = None,
    generated_files: Optional[List[dict]] = None,
) -> None:
    """把主对话一次 call_subagent 委派（委派任务 + 子智能体结果）作为一轮写入该子智能体的
    app_id 运行会话，使其在「我的智能体」运行页与 @ 悬浮窗口都能翻到（Q2 决策 2026-07-13）。

    - 会话按主对话隔离（2026-07-14 拍板）：按 (user_id, app_id, parent_thread_id=主对话
      thread_id) 找/建专用会话——同一主对话反复委派同一子智能体累积一条；新主对话新开会话，
      标题「委派·<任务开头>」。parent_thread_id 缺省时退回旧的全局「主对话委派」会话。
    - 只在委派**终态**（succeeded/failed）调用；needs_input（HITL 未完）不落库。
    - 附带持久化，失败静默——绝不影响主对话委派链路本身。
    """
    task = (task or "").strip()
    result_text = (result_text or "").strip()
    if not task and not result_text:
        return
    try:
        import uuid as _uuid

        from sqlalchemy import func

        from app.models import ChatMessage, ChatThread
        from app.services.chat.turn_context_builder import _attachments_meta

        attachment_meta = _attachments_meta(attachments)
        generated_meta = [
            {"filename": str(item.get("filename") or ""), "kind": "generated", "status": "ok",
             "file_id": str(item.get("id") or item.get("file_id") or "")}
            for item in (generated_files or []) if isinstance(item, dict)
            and str(item.get("filename") or "").strip() and str(item.get("id") or item.get("file_id") or "").strip()
        ]

        async with async_session() as db:
            lookup = (
                select(ChatThread)
                .where(
                    ChatThread.user_id == user_id,
                    ChatThread.app_id == subagent_id,
                )
                .order_by(ChatThread.updated_at.desc())
            )
            if parent_thread_id:
                lookup = lookup.where(ChatThread.parent_thread_id == parent_thread_id)
            else:
                lookup = lookup.where(ChatThread.title == DELEGATION_SESSION_TITLE)
            existing = (await db.execute(lookup)).scalars().first()
            if existing is None:
                app = await db.get(WorkflowApp, subagent_id)
                title = f"委派·{task[:26]}" if (parent_thread_id and task) else DELEGATION_SESSION_TITLE
                thread = ChatThread(
                    id=f"run_{user_id}_{_uuid.uuid4().hex[:12]}",
                    user_id=user_id,
                    app_id=subagent_id,
                    ai_app_type=(app.ai_app_type if app else None),
                    title=title[:255],
                    parent_thread_id=parent_thread_id or None,
                    origin="delegation",  # 稳定来源标记（删来源主对话清 parent 后仍在，防误判为独立对话）
                )
                db.add(thread)
                await db.flush()
                sid = thread.id
            else:
                sid = existing.id
                existing.updated_at = func.now()
            db.add(ChatMessage(
                thread_id=sid,
                role="user",
                content=task or "（无委派说明）",
                sender_type="work_agent",
                attachments_json=(
                    json.dumps(attachment_meta, ensure_ascii=False) if attachment_meta else None
                ),
            ))
            db.add(ChatMessage(
                thread_id=sid,
                role="assistant",
                content=result_text or "（无输出）",
                attachments_json=json.dumps(generated_meta, ensure_ascii=False) if generated_meta else None,
                status="completed",
            ))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.info("委派轮持久化失败，跳过（不影响主流程）", exc_info=True)


def _with_partial_failure_note(text: str, error_message: str) -> str:
    """给「有输出的部分失败」的回灌正文补上失败标注（2026-07-28 修复）。

    背景：工作流引擎 D-5 语义是「未捕获的节点错误不终止全图」——三个节点炸了两个、
    只要还有任何文本输出，整体状态就仍是 success。此前 error_message 只在 `not output`
    时被用来翻成 failed，**有输出时它从此无人读取**：主对话收到半截结果 + succeeded，
    模型据此宣布委派完成，persist_delegation_turn 也按成功落库。

    修法不是把它翻成 failed（确有产出，硬判失败会误伤 HITL/落库/终态 CAS 各条路径），
    而是让**主模型看得见**：错误文本随正文回灌。

    ⚠️ 这段话是**用户也会读到**的：`@` 整轮委派模式（chat/subagent_turn.run_dispatch_turn）
    没有主模型在中间，这份 text 就是最终回答；委派会话历史与执行卡「结果报告」同样直接展示它。
    所以这里只陈述事实，不写任何对模型下指令的句子——给模型的行动要求由
    model_driver._partial_failure_feedback_block 单独拼进工具回执（用户看不到那条）。
    """
    err = (error_message or "").strip()
    if not err:
        return text
    return f"{text}\n\n【⚠️ 本次委派有节点执行失败，以上结果可能不完整。失败详情：{err[:500]}】"


def _map_workflow_result(result: Dict[str, Any], app) -> Dict[str, Any]:
    """把工作流引擎结果映射到子智能体结果协议。"""
    base = {"subagent_id": str(app.id), "subagent_name": app.name}
    st = result.get("status")
    if st == "waiting":
        inter = result.get("interactive") or {}
        return {**base, "status": "needs_input", "text": result.get("output") or "",
                "resume_id": inter.get("resumeId"), "interactive": inter, "files": list(result.get("files") or [])}
    if st == "failed":
        return {**base, "status": "failed", "text": result.get("errorMessage") or "子智能体执行失败"}
    # success + errorMessage = 部分失败（execute_workflow 在有输出时保留 success 但填了
    # errorMessage）：此前只有 failed 分支读 errorMessage，这条信息整个丢掉，见上方注释。
    partial_error = str(result.get("errorMessage") or "").strip()
    return {**base, "status": "succeeded",
            "text": _with_partial_failure_note(
                result.get("output") or "（工作流无文本输出）", partial_error),
            "files": list(result.get("files") or []),
            **({"partial_failure": partial_error[:500]} if partial_error else {})}


async def resume_subagent(
    *,
    user: UserContext,
    token: str,
    newapi_key: str,
    default_model: str,
    subagent_id: str,
    resume_id: str,
    resume_value: Any,
    audit_run_id: str = "",
    audit_thread_id: str = "",
    audit_root_run_id: str = "",
    audit_parent_tool_call_id: str = "",
    audit_parent_logical_call_id: str = "",
    audit_execution_segment: str = "",
) -> Dict[str, Any]:
    """恢复一个挂起的子智能体（HITL）。返回同一套结果协议（可能再次 needs_input）。"""
    app = await _resolve_accessible(user, subagent_id)
    if not app:
        return {"status": "failed", "text": "子智能体不存在或无访问权限", "subagent_id": subagent_id, "subagent_name": ""}
    from app.services.gateway import tool_invoker
    from app.services.workflows.workflow_engine import resume_workflow

    published = await tool_invoker.load_published_definition(str(app.id))
    if not published:
        return {"status": "failed", "text": f"「{app.name}」没有已发布的工作流版本", "subagent_id": str(app.id), "subagent_name": app.name}
    audit_run_id, audit_thread_id, audit_root_run_id, audit_parent_tool_call_id = (
        _provider_audit_identity(
            run_id=audit_run_id,
            thread_id=audit_thread_id,
            root_run_id=audit_root_run_id,
            parent_tool_call_id=audit_parent_tool_call_id,
        )
    )
    audit_parent_logical_call_id, audit_execution_segment = _provider_audit_lineage(
        parent_logical_call_id=audit_parent_logical_call_id,
        execution_segment=audit_execution_segment,
    )
    try:
        result = await _with_wall_clock(resume_workflow(
            published, resume_id, resume_value,
            token=token or "", user_id=user.user_id, app_id=str(app.id),
            llm_api_key=newapi_key or "", default_model=default_model or "",
            thread_id=audit_thread_id,
            audit_run_id=audit_run_id,
            audit_root_run_id=audit_root_run_id,
            audit_parent_tool_call_id=audit_parent_tool_call_id,
            audit_parent_logical_call_id=audit_parent_logical_call_id,
            audit_execution_segment=audit_execution_segment,
            audit_purpose="subagent_model",
        ))
    except asyncio.TimeoutError:
        logger.warning("子智能体 %s 恢复超时（>%ss）", subagent_id, settings.SUBAGENT_TIMEOUT_SECONDS)
        return {"status": "failed", "text": f"子智能体恢复超时（超过 {settings.SUBAGENT_TIMEOUT_SECONDS}s），已中止",
                "subagent_id": str(app.id), "subagent_name": app.name}
    except Exception as exc:  # noqa: BLE001
        logger.warning("子智能体 %s 恢复异常", subagent_id, exc_info=True)
        return {"status": "failed", "text": f"子智能体恢复异常：{str(exc)[:200]}", "subagent_id": str(app.id), "subagent_name": app.name}
    return _map_workflow_result(result, app)
