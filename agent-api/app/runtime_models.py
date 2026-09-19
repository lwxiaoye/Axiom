"""Runtime 域表模型（§16.3–16.8 + §14）。独立 PG 库（RuntimeBase）。

状态枚举逐字对齐架构文档：AgentRun 用 §9.3；ToolCall 用 §11.3。子智能体结果协议（§10.3
succeeded/failed 等）是另一层，不进 Run 状态机。
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    JSON,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.runtime_db import RuntimeBase

_RUNTIME_JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

# §9.3 Run 生命周期（Phase 7 启用 routing/waiting_clarification；waiting_external 随异步业务启用）
RUN_STATUSES = (
    "created", "running", "routing", "waiting_user", "waiting_confirmation",
    "waiting_system", "waiting_clarification", "completed", "failed", "cancelled",
)
# §11.3 Tool Call 状态
TOOL_STATUSES = ("pending", "running", "succeeded", "failed", "needs_confirmation")


class AgentRun(RuntimeBase):
    """§16.3：一次任务运行。活动 Run 优先恢复（R0/ADR-002），一个 Thread 同时仅一个前台活动 Run。"""

    __tablename__ = "agent_runs"

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), index=True, nullable=False)
    user_id = Column(String(64), index=True, nullable=False)
    tenant_id = Column(String(32), default="0")
    status = Column(String(32), nullable=False, default="created")
    kind = Column(String(32), default="chat")          # chat / subagent / plan
    agent_mode = Column(String(16), nullable=False, default="standard")
    # 终态第二层（设计稿 §2）：success=required 全完成 / partial=核心完成但有非关键缺口。
    # status 只到 completed/failed/cancelled/waiting_user；「完成但有缺口」由 outcome 承载，
    # 不新增 completed_with_gaps 终态（避免牵动 DB/恢复/前端/协议/测试的爆炸半径）。
    outcome = Column(String(16), nullable=True)
    subagent_id = Column(String(64), nullable=True)
    model = Column(String(255), nullable=True)
    # 多 worker Run 租约（P1 2026-07-17）：owner_instance_id=创建/接管该 Run 的进程实例
    # （task_run_service.INSTANCE_ID，进程启动生成一次），heartbeat_at 由 owner 的心跳循环
    # 周期续约。僵尸判定分流：owner==本进程→本地任务表；外来/为空→仅租约过期
    # （is_run_lease_stale）才判僵尸。两列可空兼容 legacy 存量行（空心跳=租约过期可清）。
    owner_instance_id = Column(String(64), nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    # 可靠握手幂等键（N-02，2026-07-22）：客户端每次「发送」生成一次的请求 id。
    # (user_id, client_request_id) 部分唯一索引兜住并发重复 POST 的 TOCTOU；首帧丢失后
    # 客户端凭它查 GET /chat/requests/{id}/run 发现已建 Run，订阅续接而不是重发第二轮。
    client_request_id = Column(String(64), nullable=True)
    # 最终总结提交前关闭 Run 的输入入口。submit input 与 seal 使用同一事务咨询锁：
    # seal 前受理的输入必须先消费；seal 后到达的消息明确转 continuation，不能出现
    # “接口说已受理、旧总结却先完成”的竞态。
    input_intake_closed_at = Column(DateTime, nullable=True)
    goal = Column(Text, nullable=True)                 # 结构化目标摘要
    state = Column(JSON, nullable=True)                # Task Run State（待办字段/已确认字段）
    # Harness 控制面 CAS 版本。
    state_version = Column(Integer, nullable=False, default=0)
    parent_run_id = Column(String(64), index=True, nullable=True)
    root_run_id = Column(String(64), index=True, nullable=True)
    parent_tool_call_id = Column(String(64), nullable=True)
    delegation_depth = Column(Integer, nullable=False, default=0)
    accepted_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    resume_token = Column(String(64), nullable=True)   # 一次性恢复令牌（HITL）
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime, nullable=True)


# AgentPlan / AgentPlanStep 是 Harness 语义计划的持久化事实源。
# services/tasks/plan_service.py 在 update_plan 时事务更新，HITL 恢复/循环重建
# 可按 run_id 重建当前步骤；SSE task_plan 只是 UI 投影，不是第二事实源。
class AgentPlan(RuntimeBase):
    """Authoritative, versioned plan for one Harness Run."""

    __tablename__ = "agent_plans"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    status = Column(String(32), default="active")      # active / completed / abandoned
    summary = Column(Text, nullable=True)
    goal = Column(Text, nullable=True)
    goal_revision = Column(Integer, nullable=False, default=0)
    plan_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentPlanStep(RuntimeBase):
    """§16.4：计划步骤（由 plan_service 读写）。"""

    __tablename__ = "agent_plan_steps"

    id = Column(String(64), primary_key=True)
    plan_id = Column(String(64), index=True, nullable=False)
    step_key = Column(String(64), nullable=True)
    seq = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)
    status = Column(String(32), default="pending")     # pending / running / completed / failed / skipped
    required = Column(Integer, default=1)
    evidence = Column(JSON, nullable=True)             # 可信证据（核验后才勾选）
    acceptance_criteria = Column(JSON, nullable=True)
    depends_on = Column(JSON, nullable=True)
    requires = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    verified = Column(Integer, default=0)              # 0/1
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentStep(RuntimeBase):
    """§16.5：Run 的执行步骤记录（模型轮次 / 工具轮次 / 交互）。"""

    __tablename__ = "agent_steps"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    seq = Column(Integer, nullable=False, default=0)
    type = Column(String(32), nullable=False)          # message / tool / interrupt / system
    content = Column(Text, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgentToolCall(RuntimeBase):
    """§16.6：工具调用（含幂等键，副作用防重）。"""

    __tablename__ = "agent_tool_calls"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    step_id = Column(String(64), nullable=True)
    name = Column(String(128), nullable=False)
    args = Column(JSON, nullable=True)
    result = Column(Text, nullable=True)
    # Harness 结构化回执；result 仅保留事件摘要。
    observation = Column(JSON, nullable=True)
    raw_result_ref = Column(String(255), nullable=True)
    status = Column(String(32), default="pending")     # §11.3
    idempotency_key = Column(String(128), index=True, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRunEvent(RuntimeBase):
    """§16.7：Run 事件流（SSE v1 事件的持久化，供回放/审计/断线恢复）。"""

    __tablename__ = "agent_run_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), index=True, nullable=False)
    event_id = Column(String(64), nullable=False)
    sequence = Column(Integer, nullable=False, default=0)
    type = Column(String(64), nullable=False)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgentModelInputAudit(RuntimeBase):
    """Append-only snapshot of exactly what a main-chat model request can observe.

    This is intentionally separate from AgentRunEvent: it is an internal audit ledger, never an
    SSE source.  Persistence is fail-open at the service boundary.
    """

    __tablename__ = "agent_model_input_audits"
    __table_args__ = (
        UniqueConstraint("run_id", "request_id", name="uq_agent_model_input_audit_request"),
    )

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    thread_id = Column(String(64), index=True, nullable=True)
    request_id = Column(String(64), nullable=False)
    request_sequence = Column(Integer, nullable=False, default=0)
    model = Column(String(255), nullable=False, default="")
    source_manifest = Column(JSON, nullable=False, default=list)
    visible_payload = Column(JSON, nullable=False, default=dict)
    payload_hash = Column(String(64), nullable=False)
    shadow_hash = Column(String(64), nullable=False)
    match_status = Column(String(16), nullable=False, default="match")
    mismatch_detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgentModelCacheAudit(RuntimeBase):
    """Append-only provider cache usage linked to one exact model request attempt."""

    __tablename__ = "agent_model_cache_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "request_id"],
            ["agent_model_input_audits.run_id", "agent_model_input_audits.request_id"],
            ondelete="CASCADE",
            name="fk_agent_model_cache_audit_request",
        ),
        UniqueConstraint("run_id", "request_id", name="uq_agent_model_cache_audit_request"),
    )

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    thread_id = Column(String(64), index=True, nullable=True)
    request_id = Column(String(64), nullable=False)
    request_sequence = Column(Integer, nullable=False, default=0)
    model = Column(String(255), index=True, nullable=False, default="")
    transport = Column(String(32), nullable=False, default="")
    context_epoch = Column(Integer, nullable=False, default=0)
    epoch_reason = Column(String(64), nullable=False, default="initial")
    base_prompt_hash = Column(String(64), nullable=False, default="")
    tool_schema_hash = Column(String(64), nullable=False, default="")
    state_snapshot_hash = Column(String(64), nullable=False, default="")
    prefix_diagnostics = Column(JSON, nullable=False, default=dict)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    cache_read_tokens = Column(BigInteger, nullable=True)
    cache_miss_tokens = Column(BigInteger, nullable=True)
    cache_write_tokens = Column(BigInteger, nullable=True)
    cache_miss_source = Column(String(16), nullable=False, default="unknown")
    usage_schema = Column(String(32), nullable=False, default="unknown")
    created_at = Column(DateTime, server_default=func.now())


class AgentModelRunCounter(RuntimeBase):
    """Database-allocated physical Provider-attempt sequence for one Run."""

    __tablename__ = "agent_model_run_counters"

    run_id = Column(String(64), primary_key=True)
    last_sequence = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentModelLogicalCall(RuntimeBase):
    """One semantic model call that may own several physical Provider attempts."""

    __tablename__ = "agent_model_logical_calls"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('main_loop','plain_answer','public_preamble',"
            "'research_commentary','compaction_live','compaction_preflight',"
            "'compaction_background','router','title','memory_extract',"
            "'memory_summary','paid_search','browser_digest','subagent_model',"
            "'workflow_node','acceptance','parent_summary','tool_internal')",
            name="ck_agent_model_logical_call_purpose",
        ),
    )

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    root_run_id = Column(String(64), index=True, nullable=False)
    thread_id = Column(String(64), index=True, nullable=True)
    parent_logical_call_id = Column(String(64), nullable=True)
    parent_tool_call_id = Column(String(64), nullable=True)
    model = Column(String(255), index=True, nullable=False, default="")
    transport = Column(String(32), nullable=False, default="")
    endpoint_family = Column(String(64), nullable=False, default="")
    provider_key_fingerprint = Column(String(64), index=True, nullable=True)
    # 发布者自用 API / iframe 调用的跨库审计关联；普通平台调用保持 NULL。
    external_invocation_id = Column(String(64), index=True, nullable=True)
    external_key_id = Column(String(64), nullable=True)
    external_app_id = Column(String(64), index=True, nullable=True)
    external_owner_user_id = Column(String(64), nullable=True)
    external_session_id = Column(String(64), index=True, nullable=True)
    purpose = Column(String(64), index=True, nullable=False, default="main_loop")
    purpose_detail = Column(Text, nullable=True)
    fallback_reason = Column(String(128), nullable=True)
    scope_key = Column(String(255), nullable=False, default="")
    status = Column(String(32), index=True, nullable=False, default="started")
    context_epoch = Column(Integer, nullable=False, default=0)
    epoch_reason = Column(String(64), nullable=False, default="initial")
    base_prompt_hash = Column(String(64), nullable=False, default="")
    tool_schema_hash = Column(String(64), nullable=False, default="")
    state_snapshot_hash = Column(String(64), nullable=False, default="")
    attempt_count = Column(Integer, nullable=False, default=0)
    first_run_sequence = Column(BigInteger, nullable=True)
    last_run_sequence = Column(BigInteger, nullable=True)
    selected_attempt_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)


class AgentModelAttemptAudit(RuntimeBase):
    """One physical Provider request, including terminal usage and charge uncertainty."""

    __tablename__ = "agent_model_attempt_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["logical_call_id"],
            ["agent_model_logical_calls.id"],
            ondelete="CASCADE",
            name="fk_agent_model_attempt_logical_call",
        ),
        UniqueConstraint("run_id", "run_sequence", name="uq_agent_model_attempt_run_sequence"),
        UniqueConstraint(
            "logical_call_id", "attempt_index", name="uq_agent_model_attempt_call_index"
        ),
        UniqueConstraint("run_id", "request_id", name="uq_agent_model_attempt_request"),
        CheckConstraint(
            "purpose IN ('main_loop','plain_answer','public_preamble',"
            "'research_commentary','compaction_live','compaction_preflight',"
            "'compaction_background','router','title','memory_extract',"
            "'memory_summary','paid_search','browser_digest','subagent_model',"
            "'workflow_node','acceptance','parent_summary','tool_internal')",
            name="ck_agent_model_attempt_purpose",
        ),
    )

    id = Column(String(64), primary_key=True)
    logical_call_id = Column(String(64), index=True, nullable=False)
    retry_of_attempt_id = Column(String(64), nullable=True)
    prefix_predecessor_attempt_id = Column(String(64), nullable=True)
    run_id = Column(String(64), index=True, nullable=False)
    root_run_id = Column(String(64), index=True, nullable=False)
    thread_id = Column(String(64), index=True, nullable=True)
    request_id = Column(String(64), nullable=False)
    # The persisted contract uses the explicit ``run_request_sequence`` spelling.  Keep the
    # shorter Python key for compatibility with existing Harness code and report projections.
    run_sequence = Column(
        "run_request_sequence",
        BigInteger,
        key="run_sequence",
        nullable=False,
    )
    legacy_request_sequence = Column(Integer, nullable=True)
    attempt_index = Column(Integer, nullable=False)
    attempt_kind = Column(String(64), nullable=False, default="initial")
    execution_segment = Column(String(64), nullable=False, default="")
    model = Column(String(255), index=True, nullable=False, default="")
    transport = Column(String(32), nullable=False, default="")
    endpoint_family = Column(String(64), nullable=False, default="")
    provider_key_fingerprint = Column(String(64), index=True, nullable=True)
    external_invocation_id = Column(String(64), index=True, nullable=True)
    external_key_id = Column(String(64), nullable=True)
    external_app_id = Column(String(64), index=True, nullable=True)
    external_owner_user_id = Column(String(64), nullable=True)
    external_session_id = Column(String(64), index=True, nullable=True)
    purpose = Column(String(64), index=True, nullable=False, default="main_loop")
    purpose_detail = Column(Text, nullable=True)
    scope_key = Column(String(255), index=True, nullable=False, default="")
    context_epoch = Column(Integer, nullable=False, default=0)
    epoch_reason = Column(String(64), nullable=False, default="initial")
    base_prompt_hash = Column(String(64), nullable=False, default="")
    tool_schema_hash = Column(String(64), nullable=False, default="")
    state_snapshot_hash = Column(String(64), nullable=False, default="")
    source_manifest = Column(_RUNTIME_JSON_DOCUMENT, nullable=False, default=list)
    # Only structural fingerprints and public request controls are durable here.  Model/tool
    # bodies remain in their authoritative history stores and never enter the general cost ledger.
    logical_payload = Column(_RUNTIME_JSON_DOCUMENT, nullable=False, default=dict)
    wire_payload = Column(_RUNTIME_JSON_DOCUMENT, nullable=False, default=dict)
    logical_payload_hash = Column(String(64), nullable=False, default="")
    wire_payload_hash = Column(String(64), nullable=False, default="")
    shadow_hash = Column(String(64), nullable=False, default="")
    match_status = Column(String(16), nullable=False, default="match")
    mismatch_detail = Column(_RUNTIME_JSON_DOCUMENT, nullable=True)
    prefix_diagnostics = Column(_RUNTIME_JSON_DOCUMENT, nullable=False, default=dict)
    logical_item_count = Column(Integer, nullable=False, default=0)
    wire_item_count = Column(Integer, nullable=False, default=0)
    logical_canonical_chars = Column(BigInteger, nullable=False, default=0)
    wire_canonical_chars = Column(BigInteger, nullable=False, default=0)
    response_id = Column(String(128), nullable=True)
    previous_response_id = Column(String(128), nullable=True)
    provider_event_seen = Column(Boolean, nullable=False, default=False)
    partial_text_seen = Column(Boolean, nullable=False, default=False)
    terminal_seen = Column(Boolean, nullable=False, default=False)
    trusted_usage = Column(Boolean, nullable=False, default=False)
    terminal_status = Column(String(32), index=True, nullable=False, default="started")
    http_status = Column(Integer, nullable=True)
    error_code = Column(String(128), nullable=True)
    error_detail = Column(Text, nullable=True)
    input_tokens = Column(BigInteger, nullable=True)
    output_tokens = Column(BigInteger, nullable=True)
    reasoning_tokens = Column(BigInteger, nullable=True)
    cache_read_tokens = Column(BigInteger, nullable=True)
    cache_miss_tokens = Column(BigInteger, nullable=True)
    cache_write_tokens = Column(BigInteger, nullable=True)
    cache_miss_source = Column(String(16), nullable=False, default="unknown")
    usage_schema = Column(String(32), nullable=False, default="unknown")
    # Keep the exact Provider representation.  Decimal/float normalization can lose
    # trailing zeroes, exponent notation, or non-currency units used by a gateway.
    provider_amount_raw = Column(Text, nullable=True)
    provider_amount_unit = Column(String(32), nullable=True)
    unknown_provider_charge = Column(Boolean, nullable=False, default=False)
    billed_but_not_committed = Column(Boolean, nullable=False, default=False)
    committed = Column(Boolean, nullable=True)
    latency_ms = Column(BigInteger, nullable=True)
    legacy_backfilled = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)


# Transitional Python alias for internal code written while the migration was being
# prepared.  The durable table name and canonical ORM symbol are the ``*Audit`` forms.
AgentModelAttempt = AgentModelAttemptAudit


class AgentToolResultBlob(RuntimeBase):
    """Durable full tool output addressed by an opaque, ACL-checked handle."""

    __tablename__ = "agent_tool_result_blobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
            name="fk_agent_tool_result_blob_run",
        ),
        Index("ix_agent_tool_result_blobs_run_call", "run_id", "call_id"),
        Index("ix_agent_tool_result_blobs_thread_user", "thread_id", "user_id"),
        Index("ix_agent_tool_result_blobs_expires_at", "expires_at"),
        Index("ix_agent_tool_result_blobs_workflow_call", "workflow_execution_id", "call_id"),
        CheckConstraint(
            "(run_id IS NOT NULL AND workflow_execution_id IS NULL) OR "
            "(run_id IS NULL AND workflow_execution_id IS NOT NULL)",
            name="ck_tool_result_execution_owner",
        ),
    )

    handle = Column(String(64), primary_key=True)
    run_id = Column(String(64), nullable=True)
    workflow_execution_id = Column(String(64), nullable=True)
    thread_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False)
    tool_name = Column(String(128), nullable=False)
    call_id = Column(String(128), nullable=False)
    content_type = Column(String(64), nullable=False, default="text/plain")
    content_hash = Column(String(64), nullable=False)
    utf8_bytes = Column(BigInteger, nullable=False, default=0)
    chars = Column(BigInteger, nullable=False, default=0)
    estimated_tokens = Column(BigInteger, nullable=False, default=0)
    trust = Column(String(32), nullable=False, default="internal")
    source = Column(String(64), nullable=False, default="tool")
    applied_policy = Column(_RUNTIME_JSON_DOCUMENT, nullable=False, default=dict)
    content = Column(Text, nullable=False)
    full_available = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)


class AgentThreadContextLedger(RuntimeBase):
    """Shadow projection baseline shared across Runs of one thread/model/transport."""

    __tablename__ = "agent_thread_context_ledgers"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "model",
            "transport",
            name="uq_agent_thread_context_ledger_scope",
        ),
    )

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), index=True, nullable=False)
    model = Column(String(255), nullable=False)
    transport = Column(String(32), nullable=False)
    context_epoch = Column(Integer, nullable=False, default=0)
    epoch_reason = Column(String(64), nullable=False, default="initial")
    base_prompt_hash = Column(String(64), nullable=False, default="")
    tool_schema_hash = Column(String(64), nullable=False, default="")
    state_snapshot_hash = Column(String(64), nullable=False, default="")
    source_cursor = Column(BigInteger, nullable=False, default=0)
    source_history_hash = Column(String(64), nullable=False, default="")
    display_history_count = Column(BigInteger, nullable=False, default=0)
    display_history_hash = Column(String(64), nullable=False, default="")
    projected_items = Column(_RUNTIME_JSON_DOCUMENT, nullable=False, default=list)
    mode = Column(String(16), nullable=False, default="shadow")
    storage_revision = Column(BigInteger, nullable=False, default=0)
    last_projected_run_id = Column(String(64), nullable=True)
    last_canary_candidate_run_id = Column(String(64), nullable=True)
    last_canary_accounted_run_id = Column(String(64), nullable=True)
    last_canary_error_run_id = Column(String(64), nullable=True)
    shadow_eligible_pairs = Column(BigInteger, nullable=False, default=0)
    alignment_error_count = Column(BigInteger, nullable=False, default=0)
    expected_reset_count = Column(BigInteger, nullable=False, default=0)
    unexpected_reset_count = Column(BigInteger, nullable=False, default=0)
    canary_completed_runs = Column(BigInteger, nullable=False, default=0)
    canary_error_count = Column(BigInteger, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=2)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentThreadProjectionRolloutCohort(RuntimeBase):
    """Atomic clean-sample gates isolated by model and transport."""

    __tablename__ = "agent_thread_projection_rollout_cohorts"

    model = Column(String(255), primary_key=True)
    transport = Column(String(32), primary_key=True)
    shadow_clean_pairs = Column(BigInteger, nullable=False, default=0)
    alignment_error_count = Column(BigInteger, nullable=False, default=0)
    expected_reset_count = Column(BigInteger, nullable=False, default=0)
    unexpected_reset_count = Column(BigInteger, nullable=False, default=0)
    canary_clean_runs = Column(BigInteger, nullable=False, default=0)
    canary_error_count = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRunJob(RuntimeBase):
    """Harness 持久 Worker 队列。HTTP 只入队，Worker 用租约领取并从检查点继续。"""

    __tablename__ = "agent_run_jobs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_agent_run_jobs_run"),)

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    status = Column(String(16), nullable=False, default="queued")
    available_at = Column(DateTime, nullable=True)
    lease_owner = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    wake_reason = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRunContextSnapshot(RuntimeBase):
    """完整 EventLog 不删除；模型上下文只读取此处的压缩工作视图。"""

    __tablename__ = "agent_run_context_snapshots"
    __table_args__ = (UniqueConstraint("run_id", "version", name="uq_agent_run_context_snapshot"),)

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    covered_sequence = Column(Integer, nullable=False, default=0)
    summary = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now())


class AgentThreadAttachment(RuntimeBase):
    """§7.4 / 任务模式设计稿 §5（会话附件资产，2026-07-16 重新启用）：附件随消息**发送**后
    成为会话资产，此后每一轮主对话都恒可读（判定点是「发送」不是「上传」）。

    解析后的全文存本表（PG Text 无 MySQL 64KB 限制），每轮用当轮问题对**全部**会话附件重检索
    （session_file_service 内存计算 + embedding 按内容缓存），检索片段只进模型输入不进消息 content。
    绑定 message_id 供删除消息级联清理；sha256 是 thread 内去重键（同文件重复发送只存一份）。
    file_id 指向 MySQL「我的文件」中的同一份原始字节；上传附件不再是只读文本影子，
    模型可按 file_id 原位修改并生成版本。PG 只保存会话关联和解析文本，不复制原始字节。
    """

    __tablename__ = "agent_thread_attachments"

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), index=True, nullable=False)
    user_id = Column(String(64), index=True, nullable=False)
    message_id = Column(Integer, index=True, nullable=True)   # 绑定发送它的用户消息（删消息级联清理）
    kind = Column(String(16), default="")                     # text/pdf/docx/image
    filename = Column(String(255), default="")
    file_id = Column(String(64), index=True, nullable=True)
    sha256 = Column(String(64), index=True, nullable=False)   # thread 内容去重键
    text = Column(Text, nullable=False)                       # 解析文本 / 图片视觉描述
    char_len = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class AgentThreadQueue(RuntimeBase):
    """运行中消息队列（任务模式设计稿 §3）：主对话/任务运行时新消息默认进待发送队列，不立即
    打断；当前回复/任务结束后按 position 升序逐条派发（不并发触发多任务）。服务端持久化——
    刷新、切换会话仍保留；携带附件引用，派发时附件不丢。支持排序/编辑/删除/移回输入框。"""

    __tablename__ = "agent_thread_queue"

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), index=True, nullable=False)
    user_id = Column(String(64), index=True, nullable=False)
    content = Column(Text, nullable=False)
    attachments_json = Column(Text, nullable=True)   # 队列消息携带的附件引用（发送时不丢）
    # TurnContext 快照（审计项 1，2026-07-22）：入队时刻的 Skill/知识库/我的文件/联网/模型/
    # 任务模式选择。派发时以本快照为准，不读「当前全局选择」——排队期间用户改选资源不得
    # 漂移到已排队的消息上（查错库/用错 Skill/编辑错文件的根子）。
    context_json = Column(Text, nullable=True)
    position = Column(Integer, default=0)            # 队内顺序（可拖动调整）
    # 派发租约状态机（§3 + §10.6 端到端不丢）：queued→dispatching(带租约+lease_token)→
    # 服务端在 Run 成功持久化后原子绑定 dispatched_run_id 并删除；前端不再提前 confirm。
    # 租约超时回收时先看 dispatched_run_id：对应 Run 已存在＝消息已进活动轮，删除不重派；
    # Run 不存在＝派发中途失败，回收为 queued 重派。不丢、不重的证据链就在这两列上。
    status = Column(String(16), default="queued")    # queued / dispatching
    lease_at = Column(DateTime, nullable=True)       # 进入 dispatching 的时刻（租约起算）
    lease_token = Column(String(64), nullable=True)  # 本次派发的一次性租约凭证（防旧租约误确认）
    dispatched_run_id = Column(String(64), nullable=True)  # 派发进入的 Run（Run 落库前先绑定）
    created_at = Column(DateTime, server_default=func.now())


class AgentRunInput(RuntimeBase):
    """One durable, ordered user input submitted to an active Harness Run."""

    __tablename__ = "agent_run_inputs"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), index=True, nullable=False)
    thread_id = Column(String(64), index=True, nullable=False)
    user_id = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    attachments_json = Column(Text, nullable=True)
    input_sequence = Column(Integer, nullable=True)
    input_type = Column(String(24), nullable=True)
    # 主对话 MySQL 用户消息主键。跨库无法建 FK，但它让 ExecutionSegment 精确关联插话，
    # 不再依赖“相同正文 + 时间戳”猜测。
    source_message_id = Column(Integer, nullable=True)
    base_goal_revision = Column(Integer, nullable=True)     # 提交时的目标版本（CAS 基线）
    applied_goal_revision = Column(Integer, nullable=True)  # 安全点吸收后的目标版本
    status = Column(String(16), default="queued")           # queued/applying/applied/rejected/withdrawn
    # 认领时刻（置 applying 的时间）。崩溃留下的悬挂 applying 会永久阻塞该 Run 的后续引导
    # （同一 Run 最多一条 applying），靠它做超时回收——created_at 是提交时刻，不能替代。
    applying_at = Column(DateTime, nullable=True)
    # 消费安全点：turn=模型轮边界，terminal=终态收敛。
    applied_scope = Column(String(16), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgentThreadSummary(RuntimeBase):
    """§13.3 Compaction：会话摘要（Thread 级，覆盖到 covered_message_id 为止的旧消息）。

    摘要不覆盖原始记录（MySQL 消息表保持全量 Transcript）；Prompt 组装时用
    摘要块替代 covered_message_id 之前的消息。
    """

    __tablename__ = "agent_thread_summaries"

    thread_id = Column(String(64), primary_key=True)
    summary = Column(Text, nullable=False)
    covered_message_id = Column(Integer, nullable=False, default=0)  # MySQL ai_chat_messages.id 水位
    version = Column(Integer, default=1)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentMessageCitation(RuntimeBase):
    """§7.3 引用快照随消息持久化（消息主体在 MySQL，引用结构化数据落 Runtime PG，零 MySQL DDL）。"""

    __tablename__ = "agent_message_citations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(64), index=True, nullable=False)
    message_id = Column(Integer, index=True, nullable=False)   # MySQL ai_chat_messages.id
    sources = Column(JSON, nullable=False)                      # [{type, title, url/source, snippet}]
    created_at = Column(DateTime, server_default=func.now())


class AgentUserMemory(RuntimeBase):
    """§14.2：长期记忆（关系库为事实源，向量仅用于召回）。"""

    __tablename__ = "agent_user_memories"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(32), default="0")
    user_id = Column(String(64), index=True, nullable=False)
    type = Column(String(32), nullable=False)          # preference / fact / skills / interests / work_info / context
    content = Column(Text, nullable=False)
    structured_value = Column(JSON, nullable=True)
    source_thread_id = Column(String(64), nullable=True)
    source_message_ids = Column(JSON, nullable=True)
    confidence = Column(Integer, default=100)          # 0-100
    sensitivity = Column(String(16), default="normal")  # normal / sensitive
    status = Column(String(16), default="active")      # active / superseded / deleted
    valid_from = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentUserSetting(RuntimeBase):
    """§14 用户级记忆设置（新表，走 create_all 零迁移；刻意与 agent_user_memories 分表，
    避免给现有记忆表加列——create_all 不 ALTER 既有表）。memory_enabled 关闭时：召回注入
    与轮后抽取全停，已存记忆不动。"""

    __tablename__ = "agent_user_settings"

    user_id = Column(String(64), primary_key=True)
    memory_enabled = Column(Integer, default=1)  # 1 开 / 0 关（用 Int 避 PG bool 方言差异）
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentUserPersonalization(RuntimeBase):
    """用户个性化设置（复刻 ChatGPT Personalization，2026-07-10）：「关于你」（昵称/职业/
    详情）、自定义指令、记忆自动管理开关。单 JSON 列存储，字段演进免 ALTER；与
    agent_user_settings 分表同理——create_all 只建新表。"""

    __tablename__ = "agent_user_personalization"

    user_id = Column(String(64), primary_key=True)
    config_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentSkillDraft(RuntimeBase):
    """Successful-run trajectory drafts. Admin must publish; never auto-load as a Skill."""

    __tablename__ = "agent_skill_drafts"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), index=True, nullable=False)
    deliverable_kind = Column(String(64), default="")
    trajectory_fingerprint = Column(String(64), index=True, nullable=False)
    title = Column(String(200), nullable=False)
    markdown = Column(Text, nullable=False)
    success_count = Column(Integer, default=1)
    status = Column(String(16), default="collecting")  # collecting / suggested / published / rejected
    source_run_ids = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentThreadWorkspace(RuntimeBase):
    """One current tree pointer per chat thread. Source of Truth for session files.

    Bytes live in object storage under workspace/{user}/{thread}/; this row is not
    「我的文件」(AgentUserFile). create_all 建新表，不 ALTER 旧表。
    """

    __tablename__ = "agent_thread_workspace"

    thread_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    current_tree_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentWorkspaceObject(RuntimeBase):
    """Workspace asset or whole-tree snapshot. kind=asset|tree_snapshot."""

    __tablename__ = "agent_workspace_object"

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="asset")
    logical_path = Column(String(512), nullable=False, default="")
    storage_key = Column(String(512), nullable=False)
    mime = Column(String(128), default="")
    size_bytes = Column(BigInteger, nullable=False, default=0)
    sha256 = Column(String(64), default="")
    run_id = Column(String(64), nullable=True)
    parent_id = Column(String(64), nullable=True)
    added_count = Column(Integer, default=0)
    removed_count = Column(Integer, default=0)
    current_version = Column(Integer, nullable=False, default=1)
    manifest_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentWorkspaceObjectVersion(RuntimeBase):
    """Full-file snapshot for a workspace asset. Office/PDF are not diffed."""

    __tablename__ = "agent_workspace_object_version"
    __table_args__ = (
        UniqueConstraint("object_id", "version_no", name="uq_workspace_object_version_no"),
    )

    id = Column(String(64), primary_key=True)
    object_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    version_no = Column(Integer, nullable=False, default=1)
    storage_key = Column(String(512), nullable=False)
    size_bytes = Column(BigInteger, nullable=False, default=0)
    sha256 = Column(String(64), default="")
    created_at = Column(DateTime, server_default=func.now())
