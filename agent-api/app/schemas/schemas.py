from typing import Any, Literal, Optional, List
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.services.chat.builtin_assistants.interview.contracts import InterviewInput


class SelectedSkill(BaseModel):
    id: str
    name: str = ""
    description: Optional[str] = None
    version: Optional[str] = None
    author: Optional[str] = None
    source: Optional[str] = None


class SelectedKnowledge(BaseModel):
    id: str
    name: str = ""
    permission: Optional[str] = None


class ChatAttachment(BaseModel):
    filename: str = ""
    text: str = ""
    # 会话上传与「我的文件」共用同一份文件实体。上传接口立即返回精确 file_id，
    # 后续读取/修改都必须按它寻址，禁止再拿文件名去工作区猜测同名文件。
    file_id: str = ""
    # 原始文件字节哈希，用于附件去重与身份核对；不能用解析文本哈希代替。
    sha256: str = ""
    # 附件类型（text/pdf/docx/image，由 /chat/upload 解析产出）
    kind: str = ""
    # 图片附件的 data URL（多模态直传用；非图片为空，不落库）
    image_url: str = ""
    # 图片附件的压缩缩略图 data URL（前端 canvas 降采样产出）：随 attachments_json 落库，
    # 历史回放时图片卡据此显示——image_url（原图）仍然不落库
    preview_url: str = ""
    # 解析置信度（P0 附件生命周期，/chat/upload 产出）：ok/partial/failed + 简短原因。
    # 不声明会被 pydantic 静默丢弃——降级事件与附件元数据持久化都依赖它。
    status: str = ""
    note: str = ""

    @field_validator(
        "filename", "text", "file_id", "sha256", "kind",
        "image_url", "preview_url", "status", "note",
        mode="before",
    )
    @classmethod
    def normalize_nullable_strings(cls, value: Any) -> str:
        """兼容旧前端/历史事件里的 JSON null，附件元数据在服务端统一归一为空串。"""
        return "" if value is None else str(value)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    thread_id: Optional[str] = None
    model: Optional[str] = None
    workspace_folder_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    skill_ids: Optional[List[str]] = None
    selected_skills: Optional[List[SelectedSkill]] = None
    knowledge_ids: Optional[List[str]] = None
    selected_knowledge: Optional[List[SelectedKnowledge]] = None
    stream: bool = False
    # 右侧轻量旁路会话：只影响新建 Thread 的历史归属，不能由已有主会话改写来源。
    side_chat: bool = False
    regenerate: bool = False
    subagent_id: Optional[str] = None
    web_search: bool = False
    # 三种产品模式只选 Profile，不切换运行内核。
    agent_mode: Literal["standard", "plan", "research"] = "standard"
    # 平台内置助手预设；只冻结 Thread 身份和能力边界，仍执行同一 Harness Kernel。
    assistant_preset: Optional[Literal["presentation", "campus_services", "interview"]] = None
    interview_input: Optional[InterviewInput] = None
    attachments: Optional[List[ChatAttachment]] = None
    # 「我的文件」选中项（ADR-047 §6.6）：后端按归属校验解析为附件文本，注入本轮上下文
    file_ids: Optional[List[str]] = None
    # 「最近的对话」选中项（2026-07-28，composer + 菜单）：引用历史会话的对话记录。
    # 与 file_ids 同一条通道——后端按归属解析成转录附件，见 chat/thread_reference.py。
    thread_ids: Optional[List[str]] = None
    # 编辑重发（F2/ADR-042 线性覆盖）：删除本会话中 id >= 此值的历史消息后再走新一轮，
    # 保证后端与前端裁剪一致（否则刷新后旧消息「复活」形成分叉）
    truncate_from_message_id: Optional[int] = None
    # 队列派发（§10.6 P0 端到端不丢）：本请求是队列消息的派发轮时携带——服务端在建 Run 前
    # 原子校验租约并绑定 run_id，Run 持久化成功后确认删除；前端不再提前 confirm。
    queue_item_id: Optional[str] = None
    queue_lease_token: Optional[str] = None
    resume_source_run_id: Optional[str] = None
    # 可靠握手幂等键（N-02）：客户端每次发送生成一次。同键重复 POST 不再创建第二个 Run，
    # 而是直接订阅既有 Run 的事件流；首帧丢失后客户端也可凭它反查已建 Run。
    client_request_id: Optional[str] = Field(
        default=None,
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ChatResponse(BaseModel):
    response: str
    thread_id: str


class ResumeRequest(BaseModel):
    run_id: str
    resume_value: Any = None  # userSelect 为字符串，formInput 为对象
    resume_id: Optional[str] = None  # 一次性恢复令牌（挂起时下发；提供时必须与 Run 当前令牌匹配）
    stream: bool = True


class HarnessInputRequest(BaseModel):
    """The single input envelope for an existing Harness Run."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["message", "clarification", "plan_confirmation"]
    content: str = ""
    value: Any = None
    resume_id: Optional[str] = None
    client_input_id: Optional[str] = Field(
        default=None,
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    expected_run_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    attachments: Optional[List[ChatAttachment]] = None


class ModelItem(BaseModel):
    id: str
    name: str
    is_default: bool = False


class AgentItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    is_recommend: bool = False
    status: int = 0


class SkillItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    enabled: bool = False


class ThreadItem(BaseModel):
    id: str
    title: str
    model: Optional[str] = None
    assistant_preset: Optional[Literal["presentation", "campus_services", "interview"]] = None
    pinned: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    active_run: Optional[dict] = None


class MasterConfig(BaseModel):
    base_url: str
    api_key: str
    model: str
    system_prompt: str = ""
    welcome_message: str = ""


class MasterConfigUpdate(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None


class SkillToggle(BaseModel):
    skill_id: str
    enabled: bool = True


class AgentSyncAgent(BaseModel):
    id: str
    # 所属租户（app_info.tenant_id）。**有默认值以保持 /internal/agents/bulk-sync 向后兼容**：
    # 旧版 Java 增量同步不带该字段时落全局占位 '0'，语义 = 对所有租户可见（与
    # published_visibility.tenant_matches 一致）；Java 补齐后即按真实租户隔离。
    # 注意：'0' 是「全租户可见」而不是「没有租户」——填错会造成跨租户曝光。
    tenant_id: str = "0"
    owner_user_id: str = ""
    name: str
    description: str = ""
    icon: str = ""
    category: str = ""
    status: int = 1
    published: bool = True
    role_ids: List[str] = Field(default_factory=list)
    dept_ids: List[str] = Field(default_factory=list)
    # 显式公开标记（§4.4 风险3）：Java 同步时指定；缺省 None = 未提供，由 AGENT_ACL_STRICT 决定空 ACL 语义
    is_public: Optional[bool] = None
    source_version: int


class AgentEvent(BaseModel):
    event_id: str
    event_type: str  # upsert / delete / disable / unpublish
    source_version: int
    agent: Optional[AgentSyncAgent] = None


class AgentBulkSyncRequest(BaseModel):
    agents: List[AgentSyncAgent]
