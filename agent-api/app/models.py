from sqlalchemy import BigInteger, Boolean, Column, Float, Index, String, Text, DateTime, ForeignKey, Integer, SmallInteger, UniqueConstraint, func, or_
from sqlalchemy.dialects.mysql import LONGBLOB, MEDIUMTEXT

from app.core.database import Base


POLICY_REJECTED_MESSAGE_STATUS = "policy_rejected"
POLICY_REJECTED_PENDING_MESSAGE_STATUS = "policy_pending"
POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES = (
    POLICY_REJECTED_PENDING_MESSAGE_STATUS,
    POLICY_REJECTED_MESSAGE_STATUS,
)


class ChatThread(Base):
    __tablename__ = "ai_chat_threads"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    title = Column(String(255), default="")
    pinned = Column(SmallInteger, default=0, server_default="0")
    # WS5：独立 Agent 运行会话按 app 维度隔离；app_id 为空=主对话聚合会话
    app_id = Column(String(64), nullable=True, index=True)
    ai_app_type = Column(String(32), nullable=True)
    # 子智能体独立对话窗（ADR-046 增强）：parent_thread_id 非空=某主对话下的子线程，
    # subagent_id 为其绑定的子智能体；主历史列表只列 parent_thread_id 为空的顶层会话。
    parent_thread_id = Column(String(64), nullable=True, index=True)
    subagent_id = Column(String(64), nullable=True)
    # 会话来源标记（2026-07-14 三轮评审）：'delegation'=主对话 call_subagent 委派产生的运行会话。
    # 稳定标记，不随删除来源主对话（会清空 parent_thread_id）而丢失——悬浮窗据此判定"是否委派"，
    # 不再仅凭可空的 parent_thread_id，避免孤儿委派会话被误当普通独立对话自动选中。
    origin = Column(String(16), nullable=True)
    # v1.95：model 是该 Thread 下一次新 Run 的持久设置；last_run_model 是最近一次已受理
    # Run 的冻结模型，用于识别跨模型续聊。二者分开，避免旧队列项派发时反向覆盖下一轮选择。
    model = Column(String(255), nullable=True)
    last_run_model = Column(String(255), nullable=True)
    # Immutable after Thread creation; all Runs share this explicit file scope.
    workspace_folder_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    __tablename__ = "ai_chat_messages"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(64), ForeignKey("ai_chat_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    # 用户角色消息的真实发送方：human=用户直接发送，work_agent=主 Agent 委派。
    # 不能只依赖 ChatThread.origin：人类会在委派会话中继续追问，两者需要逐消息区分。
    sender_type = Column(String(24), nullable=True)
    feedback = Column(String(8), nullable=True)
    # 独立智能体运行页的一问一答使用同一回合标识，避免日志只能按相邻消息猜测配对。
    # 老数据保留 NULL，由读取侧走无歧义的兼容规则。
    turn_id = Column(String(64), nullable=True, index=True)
    # 用户消息附件元数据快照（JSON 数组：filename/kind/status/note/file_id + 图片压缩缩略图
    # preview_url，不含正文与原图字节）：附件卡与读取置信度在刷新/历史回放后仍可见（P0 附件
    # 生命周期）。assistant 行恒为 NULL。MEDIUMTEXT：缩略图 data URL 每张数十 KB、最多 10 张，
    # TEXT 的 64KB 上限存不下。
    attachments_json = Column(MEDIUMTEXT, nullable=True)
    # Run/Plan 的权威事实仍在 Runtime PG；这里只保存终态后的不可变展示投影。
    # 开发/生产可以共用 MySQL 会话、但使用不同 Runtime PG；若只现查当前
    # PG，用户从另一环境打开历史时会只剩正文，执行步骤和计划整体丢失。
    execution_trace_json = Column(MEDIUMTEXT, nullable=True)
    # 轮次归属与消息状态（P0 刷新丢失修复 + P1 版本化）：run_id 弱关联 Runtime PG 的
    # agent_runs（跨库无外键）。初始 user 行是 Run 的受理起点，assistant 行是输出锚点；
    # 两者都可以带同一 run_id。status 语义：completed=完整正文 / partial=有真实产出但
    # 未完整收尾 / cancelled=用户停止的部分正文 / interrupted=进程死亡对账回填 /
    # failed=失败轮占位 / policy_pending=policy_rejected 的跨库对账中间态 /
    # policy_rejected=网关策略拒绝轮（两者均历史可见，后续模型/摘要/记忆上下文
    # 排除）/ superseded=被「重新
    # 生成」取代的旧回答（历史接口仍返回供查看，LLM 上下文排除）/ archived=编辑重发截断
    # 的死分支（一切读取排除，仅留档）。NULL=列上线前旧数据（视同 completed）。
    run_id = Column(String(64), nullable=True, index=True)
    status = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


def live_chat_message_clause():
    """「存活消息」过滤子句（P1 版本化）：排除 superseded（重新生成的旧版）、
    archived（编辑重发死分支）以及 policy_pending / policy_rejected
    （网关策略拒绝轮）。⚠️ 必须显式
    放行 NULL——`status NOT IN (...)` 对 NULL 行结果是 NULL（假），旧数据会被整体误滤。
    LLM 上下文/压缩/记忆/最近历史等一切「当前对话」读取都用它；历史展示接口
    单独用 visible（保留 superseded 和 policy_rejected 供查看）。"""
    from app.models import ChatMessage  # 自引用防循环
    return or_(ChatMessage.status.is_(None),
               ChatMessage.status.notin_((
                   "superseded", "archived",
                   *POLICY_CONTEXT_EXCLUDED_MESSAGE_STATUSES,
               )))


def visible_chat_message_clause():
    """历史展示过滤（P1 版本化）：仅排除 archived 死分支；superseded 旧版本和
    policy_pending / policy_rejected 策略拒绝轮都随历史返回，
    由 status 标识其语义。"""
    from app.models import ChatMessage
    return or_(ChatMessage.status.is_(None), ChatMessage.status != "archived")


class NewApiUserKey(Base):
    """用户在 new-api 网关的专属 key 映射表。

    由 Java 侧在用户注册时写入，agent-api 只读。
    """

    __tablename__ = "new_api_user_key"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    token_id = Column(Integer, primary_key=True)
    user_id = Column(String(255), index=True)
    name = Column(String(255))
    create_time = Column(DateTime)
    api_key = Column(String(255))


class ChatModel(Base):
    """对话模型显示、排序和显式停用配置。"""
    __tablename__ = "ai_chat_model"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    enabled = Column(SmallInteger, nullable=False, default=1)
    is_default = Column(SmallInteger, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EmbeddingModel(Base):
    """Embedding 模型配置，供后端智能体检索使用。"""
    __tablename__ = "ai_embedding_model"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(255), unique=True, nullable=False)
    dimension = Column(Integer, nullable=False)
    enabled = Column(SmallInteger, nullable=False, default=1)
    is_default = Column(SmallInteger, nullable=False, default=0)
    api_key = Column(String(512), nullable=True)
    base_url = Column(String(512), nullable=True)
    is_active = Column(SmallInteger, nullable=False, default=0)
    test_status = Column(String(32), nullable=True)
    test_message = Column(String(512), nullable=True)
    last_test_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentIndexEvent(Base):
    """索引事件幂等表，防止重复处理。"""
    __tablename__ = "ai_agent_index_event"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    event_id = Column(String(64), primary_key=True)
    event_type = Column(String(32))
    agent_id = Column(String(64))
    source_version = Column(BigInteger)
    processed_at = Column(DateTime, server_default=func.now())


class WorkflowApp(Base):
    """工作台 AI 应用（智能体/工具），运行时归 agent-api，独立于 Java app_info。"""
    __tablename__ = "agent_workflow_app"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(32), default="0")
    # simple / chatAgent / workflow / workflowTool / httpToolSet（对齐 v1.9 §10.5.2）
    ai_app_type = Column(String(32), nullable=False, default="workflow")
    name = Column(String(128), nullable=False)
    description = Column(String(512), default="")
    app_category = Column(String(64), nullable=True)
    app_icon = Column(String(512), default="")
    config_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft")  # draft/published/unpublished
    owner_user_id = Column(String(64), index=True, nullable=False)
    owner_username = Column(String(128), default="")
    published_at = Column(DateTime, nullable=True)
    published_by = Column(String(64), nullable=True)
    # 公开调用是应用级配置；发布本身始终进入智能体广场。
    api_enabled = Column(Boolean, nullable=False, default=False)
    iframe_embed_enabled = Column(Boolean, nullable=False, default=False)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkflowDefinition(Base):
    """工作流定义：draft/published 双份 JSON（持久化信封含 fastgpt 画布模型）。"""
    __tablename__ = "agent_workflow_definition"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), index=True, unique=True, nullable=False)
    draft_json = Column(MEDIUMTEXT, nullable=True)
    published_json = Column(MEDIUMTEXT, nullable=True)
    published_version = Column(Integer, default=0)
    status = Column(String(32), default="draft")
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkflowVersion(Base):
    """工作流 / 对话 Agent 发布版本快照（不可变）+ 审批状态机（WS2/WS3）。

    每次「提交发布」冻结当前草稿为一条 pending_review 版本；审核通过即提升为线上
    （写回 definition.published_json + published_version 指针）。历史版本保留、可回滚。
    回滚 = 克隆目标历史版本为新 approved 版本并立即上线（版本号单调递增、留审计）。
    """

    __tablename__ = "agent_workflow_version"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), index=True, nullable=False)
    version_no = Column(Integer, nullable=False)            # 应用内单调递增
    ai_app_type = Column(String(32), default="workflow")   # 快照类型，便于审核台/管理台展示
    definition_json = Column(MEDIUMTEXT, nullable=True)     # 不可变画布快照
    config_json = Column(Text, nullable=True)              # 提交时应用级配置快照
    # pending_review / approved / rejected / cancelled / archived
    status = Column(String(24), nullable=False, default="pending_review", index=True)
    change_note = Column(String(1024), default="")
    visible_role_ids = Column(Text, nullable=True)          # JSON array，提交发布时选择的可见角色
    visible_dept_ids = Column(Text, nullable=True)          # JSON array，提交发布时选择的可见部门
    # 发布通道和嵌入源随审批版本冻结；旧版本由迁移回填 marketplace，写入侧在发布策略统一校验。
    publish_channels = Column(Text, nullable=True)          # JSON array: marketplace / api
    embed_origins_json = Column(Text, nullable=True)        # JSON array: exact HTTPS origins
    # 路由元数据快照（语义发现升级 §八/§九）：{routeDescription, triggerExamples,
    # negativeExamples, tags}。随版本冻结、不可变；仅 approved 成为线上后才同步进
    # CapabilityRegistry/Qdrant；回滚即恢复该版本的 routing_json 并重建路由索引。
    routing_json = Column(MEDIUMTEXT, nullable=True)
    submitted_by = Column(String(64), nullable=True, index=True)
    submitted_by_name = Column(String(128), default="")
    submitted_at = Column(DateTime, server_default=func.now())
    reviewed_by = Column(String(64), nullable=True)
    reviewed_by_name = Column(String(128), default="")
    reviewed_at = Column(DateTime, nullable=True)
    review_comment = Column(String(1024), default="")
    published_at = Column(DateTime, nullable=True)          # 成为线上的时间
    create_time = Column(DateTime, server_default=func.now())


class AgentApiAccessKey(Base):
    """发布者自用 Agent API 的长期访问 Key；明文不入库，仅保存校验哈希和密文。"""

    __tablename__ = "agent_api_access_key"
    __table_args__ = (
        UniqueConstraint("secret_hash", name="uq_agent_api_access_key_secret_hash"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), nullable=False, index=True)
    owner_user_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False, default="")
    key_prefix = Column(String(20), nullable=False)
    secret_hash = Column(String(128), nullable=False)
    # 仅供已登录的发布者在管理页重新复制；不得出现在调用/公开接口或日志中。
    secret_ciphertext = Column(Text, nullable=True)
    key_kind = Column(String(16), nullable=False, default="api")  # api / embed
    embed_origin = Column(String(255), nullable=True)              # exact origin for embed keys
    status = Column(String(16), nullable=False, default="active", index=True)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgentApiInvocation(Base):
    """一次 OpenAI 兼容 API 或 iframe 调用的 MySQL 投影与账单归属。"""

    __tablename__ = "agent_api_invocation"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), nullable=False, index=True)
    version_id = Column(String(64), nullable=False, index=True)
    api_key_id = Column(String(64), nullable=False, index=True)
    owner_user_id = Column(String(64), nullable=False, index=True)
    external_session_id = Column(String(64), nullable=True, index=True)
    source = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="running", index=True)
    request_user = Column(String(255), nullable=True)
    http_status = Column(Integer, nullable=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(String(512), nullable=True)
    started_at = Column(DateTime, server_default=func.now())
    first_byte_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    first_byte_latency_ms = Column(BigInteger, nullable=True)
    latency_ms = Column(BigInteger, nullable=True)
    input_tokens = Column(BigInteger, nullable=True)
    output_tokens = Column(BigInteger, nullable=True)
    reasoning_tokens = Column(BigInteger, nullable=True)
    usage_known = Column(Boolean, nullable=False, default=False)
    provider_amount_raw = Column(Text, nullable=True)
    provider_amount_unit = Column(String(32), nullable=True)


class ExternalAgentSession(Base):
    """外部 API / 静态嵌入调用的隔离运行上下文。"""

    __tablename__ = "agent_external_session"
    __table_args__ = (
        UniqueConstraint("app_id", "api_key_id", "session_id", name="uq_agent_external_session_scope"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), nullable=False, index=True)
    # Compatibility metadata for the pre-existing browser-session table. New
    # API sessions fill these harmless identifiers where an old schema still
    # marks them NOT NULL; they are not authorization inputs.
    published_version = Column(Integer, nullable=True)
    visitor_id = Column(String(64), nullable=True, index=True)
    origin = Column(String(512), nullable=True)
    api_key_id = Column(String(64), nullable=False, index=True)
    owner_user_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(128), nullable=False)
    # OpenSandbox / workspace provider returned reference. It never points at the publisher's workspace.
    workspace_ref = Column(String(255), nullable=False)
    status = Column(String(24), nullable=False, default="active", index=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ExternalAgentSessionFile(Base):
    """外部会话上传文件的归属与存储引用；文件只能在所属隔离会话中读取。"""

    __tablename__ = "agent_external_session_file"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    external_session_id = Column(String(64), nullable=False, index=True)
    storage_ref = Column(String(512), nullable=False)
    original_name = Column(String(255), nullable=False)
    mime_type = Column(String(255), nullable=True)
    size_bytes = Column(BigInteger, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="ready", index=True)
    created_at = Column(DateTime, server_default=func.now())


class ExternalAgentInteraction(Base):
    """外部运行中的表单、选择和确认等可恢复人工交互。"""

    __tablename__ = "agent_external_interaction"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    external_session_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(64), nullable=False, index=True)
    node_id = Column(String(128), nullable=True)
    kind = Column(String(32), nullable=False)
    schema_json = Column(MEDIUMTEXT, nullable=False)
    submitted_value_json = Column(MEDIUMTEXT, nullable=True)
    status = Column(String(24), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class WorkflowAcl(Base):
    """应用授权：按用户/角色/部门授予查看或编辑权限。"""
    __tablename__ = "agent_workflow_acl"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), index=True, nullable=False)
    subject_type = Column(String(16), nullable=False)  # USER / ROLE / DEPARTMENT
    subject_id = Column(String(64), nullable=False)
    permission = Column(String(16), nullable=False, default="VIEWER")  # VIEWER / EDITOR
    create_time = Column(DateTime, server_default=func.now())


class WorkflowAdminAudit(Base):
    """工作台应用的关键操作审计（所有者、编辑者、审核员与管理员）。"""

    __tablename__ = "agent_workflow_admin_audit"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(32), nullable=False, default="0", index=True)
    app_id = Column(String(64), nullable=False, index=True)
    action = Column(String(32), nullable=False, index=True)
    actor_user_id = Column(String(64), nullable=False)
    actor_username = Column(String(128), default="")
    target_user_id = Column(String(64), nullable=True)
    reason = Column(String(512), default="")
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    create_time = Column(DateTime, server_default=func.now(), index=True)


class AuditEvent(Base):
    """Append-only user-facing audit ledger for cross-domain critical actions.

    Runtime model/tool records remain authoritative in PostgreSQL. This table stores
    business actions that originate in the Java-facing UI (knowledge access, exports,
    and successful sign-ins) so the monitor can present one traceable timeline without
    copying prompts, credentials, or tool arguments into the business database.
    """

    __tablename__ = "agent_audit_events"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(32), nullable=False, default="0", index=True)
    category = Column(String(32), nullable=False, index=True)
    action = Column(String(128), nullable=False)
    resource = Column(String(255), nullable=False, default="")
    status = Column(String(24), nullable=False, default="success", index=True)
    actor_user_id = Column(String(64), nullable=False, index=True)
    actor_username = Column(String(128), nullable=False, default="")
    ip = Column(String(64), nullable=False, default="")
    detail = Column(String(512), nullable=False, default="")
    source = Column(String(32), nullable=False, default="ui")
    create_time = Column(DateTime, server_default=func.now(), index=True)


class AgentSkill(Base):
    """智能体技能（版本化技能包，v1.9 §10.5.7；蓝本 FastGPT agent_skills）。

    与 Java /ai/skill/*（提示词型 Skill 广场）是两套并存概念。
    """

    __tablename__ = "agent_skill"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    source = Column(String(16), nullable=False, default="personal")  # system / personal
    name = Column(String(128), nullable=False)
    description = Column(String(1024), default="")
    category_json = Column(String(512), default="[]")  # AgentSkillCategory[]
    creation_status = Column(String(16), nullable=False, default="ready")  # creating / ready / failed
    creation_error = Column(String(1024), nullable=True)
    current_version_id = Column(String(64), nullable=True)
    owner_user_id = Column(String(64), index=True, nullable=False)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentSkillVersion(Base):
    """技能版本：content 为技能包说明书（SKILL.md），import_source_json 记录 zip 导入来源。"""

    __tablename__ = "agent_skill_version"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    skill_id = Column(String(64), index=True, nullable=False)
    version_name = Column(String(64), default="v1")
    content = Column(MEDIUMTEXT, nullable=True)
    # 技能包完整文件树（zip 的 base64）；内容型技能为空，运行时由 content 物化 SKILL.md
    package_b64 = Column(MEDIUMTEXT, nullable=True)
    import_source_json = Column(String(512), nullable=True)
    create_time = Column(DateTime, server_default=func.now())


class CapabilityRegistry(Base):
    """Capability Registry（Phase 7，§12）：自动路由候选与能力元数据的 agent-api 自托管版。

    权威版是 Java/MySQL `app_info_capability_registry`（跨团队）；本表按发布态同步工作台智能体，
    路由候选按 source_system/execution_scope/runtime_type/enabled/health/published_version 强过滤。
    external_app 第三方条目由外部注册（默认无，R6 兜底就绪但内部 unmatched 前不启用）。
    """

    __tablename__ = "agent_capability_registry"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    app_id = Column(String(64), primary_key=True)
    tenant_id = Column(String(32), nullable=False, default="0", server_default="0")
    name = Column(String(128), nullable=False)
    description = Column(String(512), default="")
    ai_app_type = Column(String(32), default="workflow")
    source_system = Column(String(32), default="agent_workbench", index=True)  # agent_workbench / external_app
    execution_scope = Column(String(32), default="campus_internal")
    runtime_type = Column(String(32), default="python_workflow")
    published_version = Column(Integer, default=0)
    enabled = Column(SmallInteger, default=1)
    health = Column(String(16), default="healthy")  # healthy / degraded / down
    capabilities_json = Column(Text, default="{}")  # {tools:[], knowledge:[], skills:[], nodeTypes:[]}
    # 路由元数据（线上 approved 版本的 routing_json 副本，语义发现召回用）
    routing_json = Column(MEDIUMTEXT, nullable=True)
    # 最终路由文本（_route_text 产物）的 sha256：变更检测/避免无谓重建 embedding
    route_text_hash = Column(String(64), nullable=True)
    index_version = Column(Integer, nullable=False, default=1, server_default="1")
    owner_user_id = Column(String(64), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ToolGatewayCall(Base):
    """Tool Gateway（Phase 7，§11）：工具调用幂等去重 + 敏感工具审批的网关层记录。

    业务工具本体在子智能体/其它团队；本表只记我方网关的幂等键与审批状态，
    幂等键命中已完成调用即返回缓存结果（防重放重复副作用）。
    """

    __tablename__ = "agent_tool_gateway_call"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(String(64), index=True, nullable=False)
    tool_name = Column(String(128), nullable=False)
    args_hash = Column(String(64), default="")
    sensitive = Column(SmallInteger, default=0)
    # pending_approval / approved / rejected / running / completed / failed / unknown（§11.3 超时不确定态）
    # running=执行权已抢占（B6 并发去重：执行前落库，同键并发方读到即不重复执行）
    status = Column(String(24), nullable=False, default="completed", index=True)
    result_json = Column(MEDIUMTEXT, nullable=True)
    error = Column(String(512), nullable=True)
    approved_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AppInfoCapabilityRegistry(Base):
    """Capability Registry 权威表（§12.1）——与 Java 业务库同库 `ai_boot`，关联 `app_info`。

    设计上由 Java 发布/删除流程写入 + 发签名变更事件；Java 侧接管前，agent-api 暂代写入
    （仅 external_catalog 广场应用，从 `app_info` 同步）+ 消费签名事件（§12.2）。
    DDL 见 docs/sql/app_info_capability_registry.sql（已在 ai_boot 建表）。
    """

    __tablename__ = "app_info_capability_registry"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(36), primary_key=True)
    app_info_id = Column(String(36), nullable=False, index=True)
    tenant_id = Column(String(32), default="0")
    capability_code = Column(String(64), nullable=False)
    capability_name = Column(String(128))
    source_system = Column(String(32))            # agent_workbench / external_catalog
    source_app_id = Column(String(64))
    source_published_version = Column(Integer, default=0)
    route_description = Column(Text)
    trigger_examples = Column(Text)               # JSON 数组
    negative_examples = Column(Text)              # JSON 数组
    tags = Column(String(255))
    capability_type = Column(String(32))          # subagent / skill / knowledge
    execution_scope = Column(String(32))          # campus_internal / external_app
    provider = Column(String(64))
    data_sharing_policy = Column(String(32))
    launch_mode = Column(String(32))              # call_subagent / redirect / iframe
    runtime_type = Column(String(32))             # python_workflow / external_link
    endpoint = Column(String(500))
    remote_app_id = Column(String(128))
    secret_ref = Column(String(128))              # 仅引用，明文 Secret 不进本表
    protocol_version = Column(String(32))
    input_schema = Column(MEDIUMTEXT)
    output_schema = Column(MEDIUMTEXT)
    risk_level = Column(String(16))
    approval_policy = Column(String(32))
    enabled = Column(SmallInteger, default=1)
    version = Column(Integer, default=1)
    timeout_seconds = Column(Integer, default=30)
    health_status = Column(String(16), default="unknown")
    owner = Column(String(64))
    create_by = Column(String(50))
    create_time = Column(DateTime, server_default=func.now())
    update_by = Column(String(50))
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PlatformConfig(Base):
    """通用平台配置键值表：一行一个功能域的 JSON 配置。

    目前承载联网搜索（web_search，ADR-037）与 OCR（ocr，ADR-040）的管理端配置。
    密钥字段以明文存储于 config_json，仅管理员可读写；对前端读取时脱敏。
    """

    __tablename__ = "agent_platform_config"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    config_key = Column(String(64), primary_key=True)
    config_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentUserFile(Base):
    """「我的文件」用户文件工作区元数据（ADR-047 §6.6）。

    字节落盘 USER_FILES_DIR/{user_id}/，本表只存元数据。不是知识库（无索引/无共享），
    纯用户私有的对话工作文件。source=uploaded 默认永久；generated（沙箱产物）默认带
    TTL，expires_at 为空=永久，过期项由 user_file_service 惰性清理。
    """

    __tablename__ = "agent_user_file"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    mime = Column(String(128), default="")
    size_bytes = Column(BigInteger, nullable=False, default=0)
    source = Column(String(16), nullable=False, default="uploaded")  # uploaded / generated
    thread_id = Column(String(64), nullable=True)  # 来源会话（generated 产物回溯）
    storage_path = Column(String(512), nullable=False)  # 相对 USER_FILES_DIR 的路径
    expires_at = Column(DateTime, nullable=True)  # 空=永久
    folder_id = Column(String(64), nullable=True, index=True)  # 空=未分类；指向 agent_user_folder.id
    created_at = Column(DateTime, server_default=func.now())


class AgentUserFolder(Base):
    """「我的文件」单层文件夹（ADR-047 §6.6）。纯逻辑分类，不落盘物理目录——文件物理路径
    仍是 {user_id}/{file_id}_{safe}，本表只存文件夹元数据，文件经 AgentUserFile.folder_id 归属。
    删除文件夹时文件 folder_id 置空回到「未分类」，不删文件。
    """

    __tablename__ = "agent_user_folder"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    name = Column(String(80), nullable=False)  # 前端限 ≤50，DB 留冗余
    created_at = Column(DateTime, server_default=func.now())


class AgentUserFileVersion(Base):
    """「我的文件」版本快照（实施说明 Phase B §3.2）。

    AgentUserFile 保持"当前指针"角色（字节仍在其 storage_path，全部既有读路径不变）；
    每个版本各存一份完整快照（Office/PDF 不做 diff，第一版整文件快照）。旧版本永不覆盖，
    恢复历史版本=以历史字节新建 restored 版本并写回当前指针。
    status=draft 为审查未通过的草稿版（§4.3.4）：可下载/可诊断，但不动当前指针。
    版本快照不计用户配额（内部冗余）；随文件删除/过期一并清理。
    """

    __tablename__ = "agent_user_file_version"
    __table_args__ = (
        # 并发防护（第二轮评审 P1）：版本号唯一约束 + 写路径行锁（FOR UPDATE）双保险，
        # 两个并发编辑不可能拿到同一个 version_no
        UniqueConstraint("file_id", "version_no", name="uq_user_file_version_no"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    file_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)  # 冗余归属，ACL 直查
    version_no = Column(Integer, nullable=False, default=1)
    storage_path = Column(String(512), nullable=False)  # 相对 USER_FILES_DIR 的快照路径
    filename = Column(String(255), nullable=False)
    mime = Column(String(128), default="")
    size_bytes = Column(BigInteger, nullable=False, default=0)
    source = Column(String(16), nullable=False, default="uploaded")  # uploaded/generated/restored
    status = Column(String(16), nullable=False, default="active")  # active / draft（审查未通过）
    source_thread_id = Column(String(64), nullable=True)
    source_run_id = Column(String(64), nullable=True)
    change_summary = Column(String(255), nullable=True)
    created_by = Column(String(16), nullable=False, default="user")  # user / agent
    created_at = Column(DateTime, server_default=func.now())


class ConnectorBinding(Base):
    """外部应用连接器绑定（一个用户 × 一个 provider 一行）。

    「连接器」是主对话接外部系统的统一入口：GitHub 这类有官方远程 MCP 的走通用 MCP 客户端
    （app/services/gateway/mcp_client.py），没有 MCP 的国内应用（QQ 邮箱等）由各自 provider
    适配器实现，对上层是同一张表、同一套开关与作用域语义。

    凭据**只以密文入库**（secret_cipher，Fernet）：明文令牌不进 config_json、不出接口层。
    scope_json 是用户显式选择的可访问资源（GitHub 即仓库白名单），既是给模型的上下文，
    更是调用时的**硬校验**——不在名单内的仓库直接拒绝，不依赖提示词约束。
    """

    __tablename__ = "agent_connector_binding"
    __table_args__ = (
        # 唯一键含 account_login：**一个 provider 可以连多个账户**（2026-07-29 用户拍板
        # ——「有时候有的人会有好几个账户」）。原先是 (user_id, provider)，第二次连
        # 同一家会撞唯一键，只能覆盖掉第一个账户。
        #
        # ⚠️ 用 account_login 而不是 id 做第三列：account_login 是**业务身份**
        # （邮箱地址 / GitHub 登录名），同一个账户重连时应当**更新**那一行而不是
        # 新增一行。拿 id 做键的话，用户每重连一次就多一个"账户"，而它们其实是同一个。
        #
        # 历史行的 account_login 可能是空串（早期没落这个字段），空串之间彼此相等，
        # 所以同一 provider 最多只有一条空 login 的行 —— 这正好保住了存量数据的唯一性。
        UniqueConstraint("user_id", "provider", "account_login",
                         name="uq_connector_user_provider_account"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(32), default="0")
    provider = Column(String(32), nullable=False, index=True)  # github / ...
    # 展示用账号信息（列表里显示「已连接 @someone」），不含任何凭据
    account_login = Column(String(128), default="")
    account_name = Column(String(128), default="")
    account_avatar = Column(String(512), default="")
    # 凭据密文（Fernet）。key_version 便于将来轮换密钥时区分批次。
    secret_cipher = Column(Text, nullable=False)
    key_version = Column(Integer, nullable=False, default=1)
    auth_kind = Column(String(16), nullable=False, default="oauth")  # oauth / token
    granted_scopes = Column(String(512), default="")
    # ---- 令牌刷新（2026-07-29，为 Gmail / Outlook / Canva 加）----
    # GitHub App 那条路我们**故意**没做过期（App 令牌不启用过期），到这三家躲不掉：
    # 微软 access token 约 1 小时，Google 更短，不刷新就是每小时断一次。
    # refresh token 同样是长期用户凭据，必须和 secret_cipher 一样只以密文入库。
    refresh_cipher = Column(Text, nullable=True)
    # access token 的到期时刻（UTC）。空 = 不过期或未知，此时按「用到失败再刷」处理。
    # ⚠️ 不要按各家文档里的"典型有效期"硬编码：微软官方两处文档自相矛盾
    # （一处说 web 应用 refresh token 无限期，一处说通常 90 天）。唯一可靠的
    # 实现是：到点就刷、刷失败就把绑定标成 error 让用户重新授权。
    token_expires_at = Column(DateTime, nullable=True)
    # GitHub App 安装 id。**授权分两段**：登录授权只证明「你是谁」，能读哪些仓库由这次安装
    # 决定（用户在 GitHub 上勾选，GitHub 自己强制）。为空 = 只完成了第一段，还读不到任何东西。
    installation_id = Column(String(64), nullable=True)
    # 账户级选中开关（2026-07-29 多账户）。与 provider 级的 enabled 是**两个维度**：
    #   enabled          = 这个连接器这一轮用不用（用户在列表行上的总开关）
    #   account_selected = 这个账户算不算在读取范围内（用户在账户列表里逐个勾）
    # 分开是因为「有两个邮箱、这轮只想看工作那个」和「这轮完全不想用邮箱」是不同诉求，
    # 合成一个开关会逼用户为了排除一个账户而关掉整个连接器。
    # 默认 True：新连上的账户立即可用——「连上了却读不到」这个死胡同我们已经踩过一次。
    account_selected = Column(SmallInteger, nullable=False, default=1)
    # 资源作用域：{"repos": ["owner/name", ...]}；空/缺省=尚未选择（不挂载工具）
    scope_json = Column(Text, nullable=True)
    # 连接时缓存的 MCP 工具清单 [{name, description, inputSchema}]，避免每轮握手
    tools_json = Column(MEDIUMTEXT, nullable=True)
    tools_synced_at = Column(DateTime, nullable=True)
    enabled = Column(SmallInteger, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="active")  # active / error / revoked
    last_error = Column(String(512), default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CampusAssistantConfig(Base):
    __tablename__ = "agent_campus_assistant_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_campus_assistant_tenant"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False)
    current_release_id = Column(String(64), nullable=True)
    draft_release_id = Column(String(64), nullable=True)
    revision = Column(Integer, nullable=False, default=0)
    enabled = Column(SmallInteger, nullable=False, default=1)
    created_by = Column(String(64), nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CampusAssistantRelease(Base):
    __tablename__ = "agent_campus_assistant_release"
    __table_args__ = (
        UniqueConstraint("config_id", "version_no", name="uq_campus_release_version"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    config_id = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False)
    version_no = Column(Integer, nullable=True)
    base_release_id = Column(String(64), nullable=True)
    rollback_from_release_id = Column(String(64), nullable=True)
    model_id = Column(String(255), nullable=False)
    # 主对话皮肤是校园百事通发布快照的一部分，但不进入 Agent Harness Run。
    # 这里固定引用一个不可变的 main_chat 皮肤版本；子智能体外观仍走下方独立表。
    main_chat_skin_id = Column(String(64), nullable=True, index=True)
    official_domains_json = Column(MEDIUMTEXT, nullable=False)
    policy_version = Column(String(32), nullable=False)
    change_note = Column(String(1024), nullable=True)
    config_hash = Column(String(64), nullable=True)
    created_by = Column(String(64), nullable=True)
    published_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    published_at = Column(DateTime, nullable=True)


class CampusAssistantReleaseKb(Base):
    __tablename__ = "agent_campus_assistant_release_kb"
    __table_args__ = (
        UniqueConstraint("release_id", "knowledge_id", name="uq_campus_release_kb"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    release_id = Column(String(64), nullable=False, index=True)
    knowledge_id = Column(String(64), nullable=False)
    knowledge_name_snapshot = Column(String(255), nullable=True)
    category = Column(String(64), nullable=True)
    department = Column(String(128), nullable=True)
    priority = Column(Integer, nullable=False, default=100)
    enabled = Column(SmallInteger, nullable=False, default=1)


class MainChatSkin(Base):
    """Tenant-installed, immutable portable skin version for `/center/chat`.

    The JSON is server-normalized declarative data.  It can never carry HTML/CSS/JavaScript or
    source paths.  A new package version creates a new row rather than mutating a published skin.
    """

    __tablename__ = "agent_main_chat_skin"
    __table_args__ = (
        UniqueConstraint("tenant_id", "skin_key", "version", name="uq_main_chat_skin_version"),
        UniqueConstraint("tenant_id", "content_hash", name="uq_main_chat_skin_content"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    skin_key = Column(String(64), nullable=False)
    version = Column(String(32), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    name = Column(String(128), nullable=False)
    description = Column(String(512), default="")
    renderer_key = Column(String(64), nullable=False)
    manifest_json = Column(MEDIUMTEXT, nullable=False)
    content_hash = Column(String(64), nullable=False)
    source_type = Column(String(24), nullable=False, default="imported")
    status = Column(String(16), nullable=False, default="active", index=True)
    installed_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MainChatSkinAsset(Base):
    """Raster bytes installed with one immutable main-chat skin version."""

    __tablename__ = "agent_main_chat_skin_asset"
    __table_args__ = (
        UniqueConstraint("skin_id", "asset_key", name="uq_main_chat_skin_asset_key"),
        UniqueConstraint("skin_id", "asset_path", name="uq_main_chat_skin_asset_path"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    skin_id = Column(String(64), nullable=False, index=True)
    asset_key = Column(String(64), nullable=False)
    asset_path = Column(String(255), nullable=False)
    mime_type = Column(String(64), nullable=False)
    sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False)
    content = Column(LONGBLOB, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class SubAgentSkin(Base):
    """Globally installed immutable portable skin for `/agent/run/:appId`."""

    __tablename__ = "agent_sub_agent_skin"
    __table_args__ = (
        UniqueConstraint("skin_key", "version", name="uq_sub_agent_skin_version"),
        UniqueConstraint("assignment_key", name="uq_sub_agent_skin_assignment"),
        UniqueConstraint("content_hash", name="uq_sub_agent_skin_content"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    skin_key = Column(String(64), nullable=False)
    version = Column(String(32), nullable=False)
    # Workflow presentation.preset stores this local, versioned lookup key.  Package identity remains
    # the portable skin_key + version pair and is never coupled to a database id.
    assignment_key = Column(String(128), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    name = Column(String(128), nullable=False)
    description = Column(String(512), default="")
    renderer_key = Column(String(64), nullable=False)
    manifest_json = Column(MEDIUMTEXT, nullable=False)
    content_hash = Column(String(64), nullable=False)
    source_type = Column(String(24), nullable=False, default="imported")
    status = Column(String(16), nullable=False, default="active", index=True)
    installed_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SubAgentSkinAsset(Base):
    """Raster bytes installed with one immutable sub-agent skin version."""

    __tablename__ = "agent_sub_agent_skin_asset"
    __table_args__ = (
        UniqueConstraint("skin_id", "asset_key", name="uq_sub_agent_skin_asset_key"),
        UniqueConstraint("skin_id", "asset_path", name="uq_sub_agent_skin_asset_path"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    skin_id = Column(String(64), nullable=False, index=True)
    asset_key = Column(String(64), nullable=False)
    asset_path = Column(String(255), nullable=False)
    mime_type = Column(String(64), nullable=False)
    sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False)
    content = Column(LONGBLOB, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgentPresentationPreset(Base):
    """运行页外观（“衣服”）目录。

    这里只登记全局稳定的渲染 key；真正的 Vue 组件和位图素材仍随前端代码发布，
    运行时必须同时命中后端目录和前端注册表，数据库不能注入任意 CSS/HTML。
    """

    __tablename__ = "agent_presentation_preset"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    preset_key = Column(String(64), primary_key=True)
    version_no = Column(Integer, nullable=False, default=1)
    name = Column(String(128), nullable=False)
    description = Column(String(512), default="")
    source_type = Column(String(24), nullable=False, default="builtin")
    renderer_key = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="active", index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentPresentationPresetAsset(Base):
    """外观素材清单；resource_ref 是内部引用，不直接返回给浏览器。"""

    __tablename__ = "agent_presentation_preset_asset"
    __table_args__ = (
        UniqueConstraint("preset_key", "asset_key", name="uq_presentation_preset_asset"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    preset_key = Column(String(64), nullable=False, index=True)
    asset_key = Column(String(64), nullable=False)
    asset_kind = Column(String(24), nullable=False, default="image")
    resource_ref = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentPresentationAssignment(Base):
    """智能体穿衣记录，草稿与线上分别保存，避免改草稿立即影响已发布页面。"""

    __tablename__ = "agent_presentation_assignment"
    __table_args__ = (
        UniqueConstraint("app_id", name="uq_presentation_assignment_app"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    draft_preset_key = Column(String(64), nullable=False, default="default")
    published_preset_key = Column(String(64), nullable=False, default="default")
    draft_updated_by = Column(String(64), nullable=True)
    published_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ─── 原 JeecgBoot(Java) 业务库表 ────────────────────────────────────────────
# 这些表原先由已下线的 Java 后端建立与维护，agent-api 只读不建。Java 移除后表
# 从未被创建，导致 builtin_app_access 查询 app_info 直接抛异常，内置智能体全部
# 返回 503「应用目录暂时不可用」——登录后落地校园百事通即白屏。
# 现由 Python 侧自行拥有 schema（create_all 负责建表），彻底断开对 Java 的依赖。
# 列集合取自实际查询语句，不臆造字段。

class AppInfo(Base):
    """智能体广场上架记录：名称/图标/分类/状态/归属，与 BUILTIN_APP_SPECS 按 route 关联。"""
    __tablename__ = "app_info"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_name = Column(String(128), nullable=False, default="")
    app_remark = Column(String(512), default="")
    # external=广场应用；custom=自建。见 builtin_app_access.CATALOG_APP_TYPES
    app_type = Column(String(32), nullable=False, default="external")
    app_icon = Column(String(512), default="")
    app_category = Column(String(64), default="")
    pc_url = Column(String(255), index=True, default="")
    h5_url = Column(String(255), index=True, default="")
    form_options = Column(Text, nullable=True)
    status = Column(String(8), nullable=False, default="1")  # "1" 启用
    order_num = Column(Integer, nullable=False, default=0)
    open_type = Column(String(32), default="route")
    del_flag = Column(Integer, nullable=False, default=0)
    create_by = Column(String(64), default="")
    create_time = Column(DateTime, server_default=func.now())


class AppRole(Base):
    """应用可见范围——角色维度。无行表示不限角色。"""
    __tablename__ = "app_role"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), nullable=False, index=True)
    role_id = Column(String(64), nullable=False, index=True)


class AppDept(Base):
    """应用可见范围——部门维度。无行表示不限部门。"""
    __tablename__ = "app_dept"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    app_id = Column(String(64), nullable=False, index=True)
    dept_id = Column(String(64), nullable=False, index=True)


class SysUser(Base):
    """用户档案：仅供展示创建者信息（_load_creator_profiles）。认证仍在 auth-api。"""
    __tablename__ = "sys_user"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    username = Column(String(128), index=True, nullable=False)
    realname = Column(String(128), default="")
    avatar = Column(String(512), default="")


class SysUserRole(Base):
    __tablename__ = "sys_user_role"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    role_id = Column(String(64), nullable=False, index=True)


class SysUserDepart(Base):
    __tablename__ = "sys_user_depart"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    dep_id = Column(String(64), nullable=False, index=True)


# ─── 知识库 ───────────────────────────────────────────────────────────────
# 原先整块归 JeecgBoot(Java)，Java 下线后 auth-api 只留了一个统一返回 503 的桩
# （「知识库业务服务尚未接入」），导致：知识库页面空转、校园百事通因「至少绑定一个
# 可用知识库」永远无法发布、RAG 检索完全不可用。现由 agent-api 自持——它本来就握着
# Qdrant 与 Embedding 配置，是唯一合理的归属方。

class KnowledgeBase(Base):
    """知识库。chunk_count 由入库流程维护，校园百事通发布校验会读它判断是否可用。"""
    __tablename__ = "agent_knowledge_base"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(32), nullable=False, default="0", index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), default="")
    owner_user_id = Column(String(64), nullable=False, index=True)
    owner_username = Column(String(128), default="")
    # 库内取值 ENABLED / DISABLED；对外序列化成 ACTIVE / DISABLED（前端契约）
    status = Column(String(16), nullable=False, default="ENABLED")
    chunk_count = Column(Integer, nullable=False, default=0)
    doc_count = Column(Integer, nullable=False, default=0)
    # 检索参数：知识库设置里可改，search_chunks 未显式传参时取这里的值
    top_k = Column(Integer, nullable=False, default=5)
    score_threshold = Column(Float, nullable=False, default=0.3)
    # 检索方式 VECTOR / KEYWORD / HYBRID 与混合检索两路权重（0–1）。工作流节点可按次覆盖，
    # 主对话与知识库页面的检索走这里的值。两路权重分开存而不是只存一个：
    # 服务端融合公式就是 semantic_weight*向量分 + keyword_weight*关键词分，存什么就用什么。
    retrieval_mode = Column(String(16), nullable=False, default="VECTOR")
    semantic_weight = Column(Float, nullable=False, default=0.5)
    keyword_weight = Column(Float, nullable=False, default=0.5)
    # 入库时所用的向量模型与维度：换模型后旧集合失效，据此判断是否需要重建
    embedding_model = Column(String(128), default="")
    embedding_dimension = Column(Integer, nullable=True)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeDocument(Base):
    """知识库中的一篇文档。切片本身在 Qdrant，这里只存元信息与处理状态。"""
    __tablename__ = "agent_knowledge_document"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    knowledge_id = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    content_type = Column(String(64), default="text/plain")
    size_bytes = Column(Integer, nullable=False, default=0)
    # 库内取值 PENDING / PROCESSING / COMPLETED / FAILED。对外（含校验里的
    # DOC_WARNING_STATUSES）用的是另一套更细的阶段枚举，在
    # knowledge_base_service._serialize_document 处翻译。
    status = Column(String(16), nullable=False, default="PENDING")
    chunk_count = Column(Integer, nullable=False, default=0)
    error_message = Column(String(512), default="")
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeChunk(Base):
    """知识库切片的正本。

    此前切片只存在 Qdrant 的 payload 里，MySQL 一无所知，于是两件事做不了：
    关键词/混合检索（要全文索引），以及页面上的「分段」管理（列表、编辑、停用、删除）。
    现在入库时向量进 Qdrant、正文进这张表，point_id 把两边连起来；改分段要两边同步改。

    content 建 ngram 全文索引（MySQL 自带的 ngram 解析器，token 长度 2）——默认解析器按
    空格分词，对中文等于没索引。
    """
    __tablename__ = "agent_knowledge_chunk"
    __table_args__ = (
        Index("ft_agent_knowledge_chunk_content", "content", mysql_prefix="FULLTEXT", mysql_with_parser="ngram"),
        {"mysql_charset": "utf8mb4"},
    )

    id = Column(String(64), primary_key=True)
    knowledge_id = Column(String(64), nullable=False, index=True)
    document_id = Column(String(64), nullable=False, index=True)
    # 在文档内的顺序，展示与「按原文顺序浏览」用
    chunk_index = Column(Integer, nullable=False, default=0)
    content = Column(MEDIUMTEXT, nullable=False)
    char_count = Column(Integer, nullable=False, default=0)
    # Qdrant 里对应点的 id；编辑要重嵌入并 upsert 同一个点，删除要连点一起删
    point_id = Column(String(64), nullable=False)
    # 停用：MySQL 置 0，Qdrant 点的 payload 写 enabled=false（检索按 must_not enabled==false 过滤，
    # 老点没有这个键也能正常命中），不删点，重新启用时不用重嵌入
    enabled = Column(SmallInteger, nullable=False, default=1)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeAcl(Base):
    """知识库授权。无行表示仅所有者可见；permission 取值对齐 RETRIEVAL_PERMISSIONS。"""
    __tablename__ = "agent_knowledge_acl"
    __table_args__ = {"mysql_charset": "utf8mb4"}

    id = Column(String(64), primary_key=True)
    knowledge_id = Column(String(64), nullable=False, index=True)
    subject_type = Column(String(16), nullable=False, default="user")  # user / role / dept
    subject_id = Column(String(64), nullable=False, index=True)
    # VIEWER / EDITOR / OWNER —— 对齐 config_service.RETRIEVAL_PERMISSIONS
    permission = Column(String(16), nullable=False, default="VIEWER")
    create_time = Column(DateTime, server_default=func.now())
