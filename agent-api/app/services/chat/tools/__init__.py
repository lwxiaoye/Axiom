"""主对话工具集:按域组装(结构手术 Phase 1b:自 model_driver.build_tools 原样搬迁,行为零变化)。

目录即文档——base(契约与公共辅助)/plan(任务计划)/web(联网搜索)/
knowledge(知识检索)/workspace(文件+沙箱+技能)/memory(长期记忆)。
build_tools 保持原签名原顺序组装(工具顺序进 LLM payload,是行为的一部分);
循环内核(drive_model)与 call_subagent/ask_user 构建器仍在 main_agent。
"""
import asyncio
from types import SimpleNamespace
from typing import Awaitable, Callable, Iterable, List, Optional

from . import browser as _browser
from . import connectors as _connectors
from . import client_location as _client_location
from . import knowledge as _knowledge
from . import paths as _paths
from . import plan as _plan
from . import shell as _shell
from . import time_context as _time_context
from . import web as _web
from . import workspace as _workspace
from .base import (  # noqa: F401 —— 对外契约与跨模块辅助的正典出处
    CURRENT_TOOL_CALL_ID, CURRENT_TOOL_DEADLINE, CURRENT_TOOL_CONTEXT,
    MainTool, SubagentNeedsInput, ToolMetaSink, ToolExecutionResult, ToolSoftError,
    ToolFailure, ToolValue, ToolExecutionContext, assert_tool_contracts, text_tool_body,
    _MAX_IMAGES_PER_MESSAGE, _VALIDITY_GATE_RE,
    _file_action, _file_origin, _host_of, _line_diff_counts, _pop_validity_gate,
    _query_param, current_tool_call_id, pop_tool_meta, remaining_tool_budget_s,
    _with_validity_gate,
)
from .knowledge import (  # noqa: F401 —— 知识检索核心(harness_orchestrator 经 main_agent 使用)
    build_kb_pre_context, kb_search_query, resolve_kb_tenant, retrieve_knowledge,
    _run_knowledge_search,
)
from .plan import _normalize_plan_steps  # noqa: F401 —— 循环侧 resume 反推复用
from .tool_scope import resolve_tool_scope


async def build_tools(
    *,
    token: str,
    knowledge_ids: Optional[List[str]],
    web_enabled: bool,
    citation_sink: Optional[List[dict]] = None,
    image_sink: Optional[List[dict]] = None,
    tenant_id: Optional[str] = None,
    tool_meta_sink: Optional[dict] = None,
    tool_progress_queue: Optional[asyncio.Queue] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    workspace_folder_id: str = "",
    skill_packages_provider: Optional[Callable[[], Awaitable[list]]] = None,
    loaded_skills: Optional[List[dict]] = None,
    selected_skills: Optional[List[dict]] = None,
    # 保留任务摘要/凭证参数以兼容既有 Skill 调用；Harness 只做客观结构校验，
    # 不调用独立视觉模型，也不以审美或视觉评分阻塞发布。
    user_message: Optional[str] = None,
    artifact_task_brief: Optional[str] = None,
    image_delivery_mode: Optional[str] = None,
    newapi_key: str = "",
    # 产物血缘（P1）：本轮 Run id——写文件时落 AgentUserFileVersion.source_run_id，
    # 并随 meta.files[].origin 下发前端（文件卡「由哪次任务/哪个工具/哪些技能生成」）
    run_id: Optional[str] = None,
    # Harness：工具集合本身即授权边界，不能只依赖 prompt 约束。
    action_authority: str = "mutate",
    turn_intent: str = "conversation",
    attachments: Optional[List] = None,
    revision_mode: bool = False,
    allow_create: bool = True,
    revision_target: Optional[dict] = None,
    # 最近几条用户消息（不含当轮）：PPT 语境判定要看最近几轮，不只看当轮。
    # turn_prepare 的技能预加载早就是这个口径，工具侧此前只看当轮——两处不一致，
    # 澄清式对话里「干活那轮不含 PPT 字样」会让工具侧的 PPT 判定整体失灵。
    recent_user_messages: Optional[List[str]] = None,
    # Deep Research：放宽 search_web 同轮次数（见 web.build_web_tools）
    research_profile: bool = False,
    execution_profile: Optional[dict] = None,
    allowed_domains: Optional[list] = None,
    # Campus (and similar presets): the assembled registry is the authorization
    # boundary. Extra tools must never be registered, not stripped after the fact.
    allowed_tool_names: Optional[Iterable[str]] = None,
    additional_tools: Optional[Callable] = None,
) -> List[MainTool]:
    """按会话上下文构建可用工具集。空列表表示无需走工具循环。

    citation_sink：调用方传入的引用收集器；工具执行时把结构化来源
    （§7.3 knowledge / web 两类）追加进去，供消息级引用下发与持久化。
    image_sink：搜索附带图片收集器（ChatGPT 式图文混排）；编号 [图N] 全消息全局递增，
    模型在正文用 [图N] 标记插图位置，前端替换成图片卡并随引用持久化。
    tool_meta_sink：工具执行的结构化元信息（如 search_web 的结果数/已读页面），
    随 tool.completed 事件透传前端思考时间线（「搜索到 N 个网页/浏览 N 个页面」）。
    """
    scope = resolve_tool_scope(
        message=user_message or "", attachments=attachments,
        revision_target=revision_target, turn_intent=turn_intent,
        action_authority=action_authority, revision_mode=revision_mode,
        recent_user_messages=recent_user_messages,
    )
    # Keep the accepted profile object shared with TurnEnv.  A verified model ``use_skill`` can
    # update the same Run's live closures and the subsequent driver/verifier call sees that fact;
    # the durable skill_state/run_store projection covers recovery segments.
    runtime_profile = execution_profile if isinstance(execution_profile, dict) else {}
    profile_reason = str(runtime_profile.get("resolution_reason") or "")
    allow_dynamic_profile = bool(
        str(runtime_profile.get("id") or "") == "interactive"
        and profile_reason != "explicit_skill_native_ppt"
    )
    # Image routing is a caller/profile fact.  Do not derive a hidden tool policy from
    # document keywords in ToolScope; the model receives the ordinary capability catalog.
    resolved_image_delivery_mode = image_delivery_mode or (
        "artifact_only"
        if str((execution_profile or {}).get("id") or "") == "artifact_coding"
        else "auto"
    )
    tools: List[MainTool] = []
    if additional_tools is not None:
        tools.extend(additional_tools(SimpleNamespace(
            user_id=str(user_id or ""), thread_id=str(thread_id or ""), run_id=str(run_id or ""),
        )))
    restrict_to = (
        {str(name) for name in allowed_tool_names if str(name)}
        if allowed_tool_names is not None else None
    )

    def _want(name: str) -> bool:
        return restrict_to is None or name in restrict_to

    if restrict_to is None:
        tools.extend(_plan.build_plan_tools())
        # Always register the read-only clock capability.  Exact time is fetched only on demand and
        # therefore no longer dirties the stable system prompt on every minute boundary.
        tools.extend(_time_context.build_time_tools())
        # The request peer is captured at accept time; the tool resolves it only when the model
        # needs local context and never places the raw address in provider-visible content.
        tools.extend(_client_location.build_location_tools(run_id=str(run_id or "")))
    if _want("search_web"):
        tools.extend(_web.build_web_tools(
            citation_sink=citation_sink, image_sink=image_sink, tool_meta_sink=tool_meta_sink,
            tool_progress_queue=tool_progress_queue, user_message=user_message,
            user_id=str(user_id or ""), run_id=str(run_id or ""),
            newapi_key=newapi_key or "",
            image_delivery_mode=resolved_image_delivery_mode,
            artifact_asset_tool=(
                "fetch_ppt_asset"
                if str((execution_profile or {}).get("id") or "") == "artifact_coding"
                and str((execution_profile or {}).get("artifact_kind") or "") == "presentation"
                else "download_url"
            ),
            research_profile=bool(research_profile),
            allowed_domains=allowed_domains,
        ))
    if restrict_to is None:
        # 浏览器抓取（2026-07-27）：紧跟 search_web —— 两者是同一类「获取外部信息」的能力，
        # 位置相邻有助于模型在「搜关键词」和「读指定链接」之间选对。
        # BROWSER_SERVICE_URL 未配置时返回空列表，工具不出现（功能整体关闭）。
        tools.extend(_browser.build_browser_tools(
            newapi_key=newapi_key, tool_meta_sink=tool_meta_sink,
            tool_progress_queue=tool_progress_queue,
            # 有状态浏览会话按「用户 + 会话(thread)」归属：不同用户/不同对话拿不到彼此的页面
            # （也就拿不到彼此的登录态），而**同一对话的下一轮拿得到**——run_id 每轮都换，
            # 按它归属的话「跨轮存活的浏览器会话」这句话是假的（2026-07-28 修）。
            # run_id 仍然传：thread_id 缺失时退回它，至少同回合内可用。
            # user_id 同时是抓取的每用户并发闸的键。
            run_id=str(run_id or ""), user_id=str(user_id or ""),
            thread_id=str(thread_id or "")))
        # 外部应用连接器（2026-07-28）：紧跟浏览器 —— 同属「去外部系统取信息」这一族，
        # 让模型在「搜网页 / 读链接 / 读已连接的代码库」之间挨着做选择。
        # 用户没连任何应用、或连了但没勾资源时返回空列表，工具不出现。
        tools.extend(await _connectors.build_connector_tools(user_id=user_id))
    if knowledge_ids and _want("search_knowledge"):
        tools.extend(_knowledge.build_knowledge_tools(
            token=token, knowledge_ids=knowledge_ids, citation_sink=citation_sink,
            image_sink=image_sink, tenant_id=tenant_id, tool_meta_sink=tool_meta_sink,
            telemetry_user_id=str(user_id or ""), turn_id=str(run_id or ""), source="CHAT"))
    if restrict_to is not None:
        return [tool for tool in tools if str(getattr(tool, "name", "") or "") in restrict_to]
    if user_id:
        # workspace 模块现在只剩 use_skill 一个工具（旧 file_id 族与 execute_in_sandbox 已整体删除，
        # 2026-07-29）。它同时通过 exports 把**技能包解析器**交给下面的 bash —— 那是技能
        # 文件能挂进沙箱的唯一通道，断了 Skill 直接失效（实测踩过）。
        # 签名随之瘦身：thread_id/run_id/tool_progress_queue/newapi_key/image_sink/
        # revision_target 六个参数只被删掉的那批工具使用，不再传。
        _skill_state: list[dict] = []
        if run_id:
            try:
                from app.services.chat.turn_context_builder import get_persisted_skill_state
                _skill_state = await get_persisted_skill_state(str(run_id))
            except Exception:  # noqa: BLE001
                # Skill state is an observation source, never a reason to expose a broader tool
                # set.  The current turn still revalidates the trusted IDs before injection.
                _skill_state = []
        # A recovered model-loaded first-party Skill is already an execution fact.  Rebuild the
        # same effective profile before creating the next segment's tools; explicit/native Skill
        # selection remains authoritative and never gets silently replaced.
        if allow_dynamic_profile and _skill_state:
            from app.services.chat.execution_profile import dynamic_profile_for_skill

            explicit_native_state = any(
                isinstance(_state, dict)
                and str(_state.get("selection_source") or "").strip().lower() == "explicit"
                and str(_state.get("status") or "").strip().lower() in {"authorized", "loaded"}
                and str(_state.get("execution_profile_id") or "interactive") == "interactive"
                for _state in _skill_state
            )
            if explicit_native_state:
                allow_dynamic_profile = False

            if allow_dynamic_profile:
                for _state in _skill_state:
                    if not isinstance(_state, dict):
                        continue
                    source = str(_state.get("selection_source") or "").strip().lower()
                    status = str(_state.get("status") or "").strip().lower()
                    if source not in {"model", "dynamic"} or status != "loaded":
                        continue
                    if str(_state.get("execution_profile_id") or "") != "artifact_coding":
                        continue
                    runtime_profile.update(dynamic_profile_for_skill(runtime_profile, _state))
                    allow_dynamic_profile = False
                    break
        _ws_exports: dict = {}
        tools.extend(_workspace.build_workspace_tools(
            user_id=user_id, token=token, run_id=run_id,
            tool_meta_sink=tool_meta_sink,
            skill_packages_provider=skill_packages_provider, loaded_skills=loaded_skills,
            selected_skills=selected_skills,
            skill_state=_skill_state,
            user_message=user_message,
            runtime_profile=runtime_profile,
            allow_dynamic_profile=allow_dynamic_profile,
            exports=_ws_exports))
        # 路径寻址文件工具（2026-07-27 批 2）：统一文件系统开启时**替换**旧的 file_id 版
        # read_file/edit_file/update_file/create_file/list_files —— 同名工具不能并存，而且
        # 两个地址空间并存正是这次改造要消掉的东西（模型脑子里不该有两套寻址）。
        #
        # 2026-07-29 用户拍板「跟以前 execute_in_sandbox 有关的部分都要去除，只保留现在的 bash」：
        # 路径寻址成为**唯一形态**，不再有开关、不再有 file_id 版的旧工具族可回退。
        # 旧族（read_file/list_files/edit_file/update_file/create_file 的 file_id 版 +
        # execute_in_sandbox）已从 workspace.py 整体删除，所以这里也不再需要"先注册再摘掉"那一步。
        # 承接关系逐条点名（删除前已核实，不是推测）：
        #   · execute_in_sandbox 的执行能力 → bash（同一个 sandbox_executor.execute_in_sandbox(language="bash") 内核）
        #   · execute_in_sandbox 的技能包挂载 → workspace 的 exports 交给 bash（下方 resolve_skill_packages）
        #   · execute_in_sandbox 的 fetch_urls（[图N] → 真实直链）→ paths 的 download_url 认 "图N" 写法
        #   · 旧 file_id 族的原位修改语义（revision_target 授权 + expected_sha256 乐观锁）
        #     → paths.py 的路径版，由 test_harness_v2 与 test_path_tools 双向守着
        #   · /workspace/outputs 的产物质检触发 → files/ 写入事件驱动 + bash 回执带
        #     [artifact_validity_gate=...]，LoopState 三张安全网不变
        # files/ 事件驱动分支只执行确定性的结构校验；视觉自看若需要，由具体 Skill 自主完成。
        tools.extend(_paths.build_path_tools(
            user_id=user_id, thread_id=thread_id, run_id=run_id,
            tool_meta_sink=tool_meta_sink, newapi_key=newapi_key,
            user_message=user_message or "",
            # PPT 配图链路（2026-07-27 起）：模型看到的图片目录只有编号+标题，真实 URL 只在
            # image_sink 里，所以 [图N] → 直链的解析必须在服务端做。这里不新增工具，只让
            # download_url 的 url 参数额外认 "图N" 这种写法。不接这一路，「新建 PPT 默认
            # 自动配图」整条链无路可走，而提示词还在命令模型配图 = 教它做做不到的事。
            image_sink=image_sink,
            # 原位修改授权随场景传下去（仅 revision_mode 且不许新建时）
            revision_target=revision_target if revision_mode and not allow_create else None,
            persist_downloads=action_authority == "mutate",
            workspace_folder_id=workspace_folder_id,
            execution_profile=execution_profile,
            attachments=attachments,
            scope_to_thread=True,
        ))
        # 沙箱 shell：唯一的执行器，与文件工具同一个复用容器。
        tools.extend(_shell.build_shell_tools(
            run_id=run_id, tool_meta_sink=tool_meta_sink,
            user_id=user_id, thread_id=thread_id,
            workspace_folder_id=workspace_folder_id,
            newapi_key=newapi_key or "",
            tool_progress_queue=tool_progress_queue,
            user_message=artifact_task_brief or user_message or "",
            # 与 paths/workspace 同一口径的原位修改授权。bash 唯独不能靠「从清单里摘掉」
            # 来约束（PPT 修改就是靠它跑技能脚本），所以约束下沉到落库层：沙箱里怎么写都行，
            # 但只有目标文件回得到「我的文件」。见 workspace_sync.WorkspaceSync.persist()。
            revision_target=revision_target if revision_mode and not allow_create else None,
            recent_user_messages=recent_user_messages,
            resolve_skill_packages=_ws_exports.get("resolve_skill_packages"),
            is_skill_loaded=_ws_exports.get("is_skill_loaded"),
            # 技能缺口回执与 use_skill 共享去重集合（同一个包只念一遍"没挂上"）
            skill_notice_seen=_ws_exports.get("skill_notice_seen"),
            # 真图 PPT 嵌入质检：本轮选中图片文件名 → bash → sandbox_executor → output_review
            attachments=attachments,
            execution_profile=runtime_profile,
            dynamic_profile_enabled=allow_dynamic_profile,
            image_sink=image_sink,
            # Research 始终只获得临时 scratch；即使普通意图判定给出 mutate，
            # 也不能让 bash 的 ToolSpec 变成 user_files 后再被 Profile 整体过滤掉。
            persist_outputs=action_authority == "mutate" and not research_profile,
            scope_to_thread=True,
        ))
        # Office 专用工具全部退休（2026-07-27 用户拍板「不需要给主对话太多余的工具，越简单越好」）。
        # 前提已成立：沙箱镜像里 python-docx / openpyxl / python-pptx / pypdf 全部可用（实测），
        # bash 一条命令就能做它们做的事，而且更灵活（build_docx 只能按固定结构生成）。
        # 每格式一个动词的设计本来就是 execute_in_sandbox 只能跑 Python 时代的产物 —— 那个限制已经没了。
        # 2026-07-27 二次清理：连 `chat/tools/office.py` 整模块一起删掉。上一版注释说
        # 「保留 _office 模块本身：工作流节点侧仍在用」——**核实为不实**：`build_office_tools`
        # 全仓库零调用者，`_office` 在本文件里只被 import、从不使用，workflow_engine /
        # workflow_runtime 也都没有引用它。留着只会让下一个读代码的人以为 Office 工具在
        # 某些配置下还会注册。（`app/services/artifacts/office_executor.py` 因此变成孤儿，
        # 但它是自成一体的能力模块，留待单独决策，本次不动。）
        # Tool visibility is governed by ToolSpec, authorization and real resource scope.
        # A PPT/document keyword must not silently remove a capability or select a Skill;
        # explicit Skill choice and model capability discovery handle that decision.
        from app.services.agent_harness.memory_tools import build_memory_tools
        tools.extend(build_memory_tools(user_id=user_id, thread_id=thread_id,
                                        user_message=user_message or "", run_id=run_id or ""))
    return tools
