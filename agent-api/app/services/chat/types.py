"""主对话回合的统一内部对象。

仅是**内部契约**：API 字段、DB 语义和 Event Catalog 不由这里定义。
TurnContext 由 turn_prepare.prepare_turn 产出；TurnOutcome 聚合工具循环回合的执行结果，交 turn_finalizer
按终态 CAS 统一收尾。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class TurnContext:
    """回合准备结果(turn_prepare.prepare_turn 的产出,路由/预取/推荐三段)。"""

    effective_subagent_id: Optional[str] = None   # 显式 @ 或自动路由命中的子智能体
    route_info: Optional[dict] = None             # 自动路由命中信息(route.selected 事件用)
    clarify_options: list = field(default_factory=list)   # R5 消歧候选(非空即出选择卡)
    agents: Optional[list] = None                 # 向量检索到的推荐智能体候选(含分数)
    trusted_skills: list = field(default_factory=list)    # 已成功读取的可信技能全文（恢复段才可能非空）
    selected_skill_records: list = field(default_factory=list)  # ACL 目录元数据，尚未读取 SKILL.md
    effective_skill_ids: list = field(default_factory=list)  # 显式选择 + 服务端确定性自动选择
    memory_block: str = ""                        # 个性化 + 长期记忆召回合成的 prompt 段
    skill_catalog_block: str = ""                 # Skill 目录 prompt 段(模型自主 use_skill 用)
    recommend_agent_ids: list = field(default_factory=list)  # 内部推荐卡:平台智能体 id
    recommend_external: Optional[dict] = None     # 外部应用推荐(内部无相关时兜底)


@dataclass
class TurnOutcome:
    """工具循环回合的聚合结果(map_tool_loop_events 逐事件回填,turn_finalizer 收尾)。

    dict 形态与此前 stream_chat 内联的 `out = {...}` 聚合器逐键一致——生成器无法
    返回值,只能边消费事件边就地累积;`as_dict()` 供既有 dict 消费代码零改动过渡。
    """

    answer: str = ""
    trace: list = field(default_factory=list)
    usage_prompt_tokens: int = 0
    suspended: Optional[dict] = None              # HITL 挂起载荷(子智能体/消歧/任务卡)
    streamed_any: bool = False                    # 是否已流出过正文(降级/重发判定用)
    any_tool_succeeded: bool = False              # 本轮是否有工具成功(假故障 scrub 旁路)
    write_tool_succeeded: bool = False            # 写路径工具成功(bash/write_file/...)
    published_artifact_succeeded: bool = False    # 专用发布工具已审查并持久化成品
    completion_interrupted: bool = False           # 工具成功但最终总结通道中断，应收敛为 partial
    task_outcome: Optional[str] = None            # success / partial(落 Run.outcome)
    run_disposition: Optional[str] = None         # failed / cancelled(任务模式 2.0 处置)
    latest_task_plan: list = field(default_factory=list)  # 计划快照，终态强制收口用
    # 最近一次已公开的过程说明只供同一轮的去重判断使用，不进入最终回答或持久化状态。
    latest_public_commentary: str = ""
    force_converge: Optional[str] = None
    loop_steps: int = 0
    requires_citations: bool = False              # 深度研究终答：CompletionClaim 需要来源回执
    # Provider 历史只在权威 assistant 行落库后提交；bundle 只在当前进程内传递，不进入 API。
    projection_commit: Any = None

    # dict 兼容协议:map_tool_loop_events 及其消费代码此前按 out["key"] 读写聚合器,
    # 接线期零改动过渡;字段名即键名,写未知键=属性错误(比裸 dict 更早暴露拼写问题)。
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        if not hasattr(self, key):
            raise KeyError(f"TurnOutcome 无字段 {key!r}")
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class TurnEnv:
    """轮级执行环境(Phase 2c):骨架构造一次,三个回合体(委派/工具循环/直答)共享。

    只读输入 + 运行时句柄(session/channel/spawn_* 注入);可变槽仅两个——
    fallback_plain(工具循环让位直答的信号)与 plain(直答主体跨 session 边界
    交给收尾段的结果)。不进任何持久化,生命周期=一轮。
    """

    session: Any = None
    thread: Any = None
    channel: Any = None
    run_id: str = ""
    thread_id: str = ""
    user_id: str = ""
    user_context: Any = None
    token: str = ""
    newapi_key: str = ""
    resolved_model: str = ""
    message: str = ""
    model_input: str = ""
    # retired runtime Worker 在任何模型/工具执行前 CAS 冻结；下游只读，不得重新推断。
    execution_profile: Optional[dict] = None
    # 产物审查使用的精简任务说明；PPT 可附加独立视觉模型提取的参考图 DNA。
    quality_brief: str = ""
    model_input_content: Any = None
    attachments: Optional[list] = None
    text_atts: Optional[list] = None
    image_urls: list = field(default_factory=list)
    atts_meta: list = field(default_factory=list)
    web_search: bool = False
    # 计划模式这一轮（用户在 + 菜单点了「计划模式」，或话里明确要先出计划）：授权已压成
    # inspect，模型只读勘查后把计划报告作为本轮**回答**流出。它同时是「绝不静默降级」的
    # 判据——计划轮在第一个 token 之前失败必须如实失败，不能悄悄退回无工具的普通问答
    # （那样用户要的计划会变成一段没有勘查依据的空话，状态还写「已完成」）。
    # 前身是恒 False 的 is_plan_profile（graph 任务模式删除后留下的死字段），守卫因此长期空转。
    plan_mode: bool = False
    # Deep Research（用户在 + 菜单显式开启）：放宽 search_web 同轮次数与研究提示口径。
    research_profile: bool = False
    # 平台内置助手预设；不改变 Kernel，只供准备层和工具注册层收窄能力。
    assistant_preset: str = ""
    assistant_preset_snapshot: Optional[dict] = None
    # 自动路由进入任务模式的清晰、可逆编辑：展示计划但不再额外停下来等确认。
    task_auto_execute: bool = False
    turn_intent: str = "conversation"
    route: str = "agent"
    action_authority: str = "inspect"
    revision_mode: bool = False
    allow_create: bool = True
    revision_target: Optional[dict] = None
    # 修订歧义候选（多产物）：平台强制确认卡 + resume 解锁写目标用
    revision_file_candidates: list = field(default_factory=list)
    turn_guard_prompt: str = ""
    # Worker 在主模型调用前已经公开的模型首句。工具循环用它抑制第一轮重复 preamble，
    # 但不把这段文字写进最终回答或持久化模型状态。
    public_preamble: str = ""
    skill_ids: Optional[list] = None
    knowledge_ids: Optional[list] = None
    selected_knowledge: Optional[list] = None
    regenerate: bool = False
    is_first_turn: bool = False
    prep: Optional[TurnContext] = None
    summary: Optional[dict] = None
    summary_block: str = ""
    prompt_rows: list = field(default_factory=list)
    history_rows: list = field(default_factory=list)
    raw_est_tokens: int = 0
    ctx_window: int = 0
    spawn_bg: Optional[Callable] = None
    spawn_partial_persist: Optional[Callable] = None
    suspend_orchestration: Optional[Callable] = None
    fallback_plain: bool = False
    # 有意 pure_qa / direct_answer（非工具故障回退）。plain_turn 用不同护栏文案。
    intentional_pure_qa: bool = False
    # 工具循环已完成的知识库前置召回。故障退回 plain 时继续注入，避免选库语义消失。
    kb_pre_context: str = ""
    plain: dict = field(default_factory=dict)
    # Worker 崩溃续跑：已配对的 drive_model 游标。非空时不得把受理原句再当本轮 user。
    initial_messages: Optional[list] = None
    # 与恢复游标同一原子检查点的动态事实；重启后不用当前时间/新摘要篡改旧 Run。
    checkpoint_world_state: dict = field(default_factory=dict)
    # 用户停止后再说「继续」：新 Run 要继承上一份研究台账，而不是从空台账重搜。
    resume_source_run_id: str = ""
    # Research kernel 已读取并编号的来源。主循环用同一顺序预填 citation_sink，确保
    # 报告 [n]、实时来源面板、消息级引用持久化和历史回放始终指向同一 URL。
    research_citations: list = field(default_factory=list)
    # Team discussion is the evidence phase; synthesis must not restart research.
    research_team_synthesis_only: bool = False
