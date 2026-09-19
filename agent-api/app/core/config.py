import logging
from typing import Dict, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # API
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AXIOM Agent API"
    
    # Database
    DATABASE_URL: str = "mysql+aiomysql://user:CHANGE_ME@127.0.0.1:3306/ai_boot?charset=utf8mb4"
    
    # New API Gateway
    NEWAPI_BASE_URL: str = "http://127.0.0.1:9080/new-api/v1"
    
    # auth-api 内网地址：登录 / token 校验 / 用户信息 / 菜单权限的回源目标
    # （原 JeecgBoot Java 后端下线后由 auth-api 接管同一套 /sys/* 契约）。
    AUTH_API_BASE: str = "http://127.0.0.1:9090"
    # 旧名兼容：JAVA_INTERNAL_BASE 已改名为 AUTH_API_BASE。仅当新名未设置而环境 /.env 里仍有旧名时
    # 沿用旧值并告警；校验后旧属性始终镜像新值，尚未改完的旧读法在过渡期内不会拿到 None。
    # 过渡期结束后连同下方 validator 一起删除。
    JAVA_INTERNAL_BASE: Optional[str] = None

    @model_validator(mode="after")
    def _compat_java_internal_base(self) -> "Settings":
        """新名 AUTH_API_BASE 未显式设置时兜底读取旧名 JAVA_INTERNAL_BASE，避免集成瞬间线上鉴权断掉。"""
        legacy = (self.JAVA_INTERNAL_BASE or "").strip()
        if legacy and "AUTH_API_BASE" not in self.model_fields_set:
            logger.warning(
                "JAVA_INTERNAL_BASE 已改名为 AUTH_API_BASE，请更新环境变量；本次沿用旧值 %s", legacy,
            )
            self.AUTH_API_BASE = legacy
        self.JAVA_INTERNAL_BASE = self.AUTH_API_BASE
        return self

    # Default to revalidation so logout/password rotation revokes API access immediately.
    # Nonzero values explicitly accept a revocation delay of that many seconds.
    AUTH_TOKEN_CACHE_TTL_SECONDS: int = 0

    # 知识库 RAG 召回（主对话选中知识库时；检索本体在 Java /ai/knowledge/retrieval/test）
    # 前置强制检索：选了知识库即先检索并把片段注入 system prompt，不依赖模型主动调 search_knowledge，
    # 保证「选了就一定基于知识库内容回答」，弱模型也能用上；同时抬高 topK/降低阈值提升召回。
    KNOWLEDGE_PRE_RETRIEVE: bool = True
    KNOWLEDGE_TOP_K: int = 8          # 每次返回片段数，越大召回越高（占上下文也越多）
    KNOWLEDGE_THRESHOLD: float = 0.2  # 相似度阈值，越低召回越高

    # LangGraph Checkpointer（PG sidecar；空则禁用暂停恢复/回放能力）
    CHECKPOINT_DATABASE_URL: Optional[str] = None
    # Runtime 域表（Task Run/Plan/Step/Event/Memory，§16.10）PG 独立库；空则从 CHECKPOINT_DATABASE_URL
    # 派生同实例的独立 agent_runtime 库（起步复用实例、独立 database）。为空且无 checkpoint 时 Runtime 功能降级为不持久化。
    RUNTIME_DATABASE_URL: str = ""
    # Runtime PG fail-closed（P0 2026-07-17）：true 时未配置/初始化失败直接阻止启动、
    # 运行期不可达时 /health/ready 返回 503——生产必须 true（compose 已默认注入），
    # 否则任务模式退化成不可恢复的临时图、事件回放/R0 守卫/HITL 全部静默失效。
    # 默认 false 仅照顾裸机本地跑（无 PG 也能起）。
    RUNTIME_REQUIRED: bool = False
    # Qdrant 向量库（知识库集合）
    QDRANT_URL: str = "http://qdrant:6333"
    INITIAL_CHAT_MODELS: str = ""
    # 自动会话标题用的轻量模型 id（留空则复用当轮对话模型）。配一个便宜/快的模型可降低
    # 后台标题生成对用户模型配额的占用与成本；生成失败仍回退截断标题，不影响对话。
    TITLE_MODEL: str = ""
    DEFAULT_EMBEDDING_MODEL: str = "qwen3-embedding:0.6b-q8_0"
    DEFAULT_EMBEDDING_DIMENSION: int = 1024

    # HITL 挂起 Run 过期清理（原 Phase 0 checkpoint TTL，随 Phase 3 Run 表落地）
    RUN_WAITING_TTL_HOURS: int = 24
    RUN_SWEEP_INTERVAL_SECONDS: int = 3600   # 0=关闭 sweep

    # ---- Run 租约（P1 多 worker/多副本，2026-07-17）----
    # 心跳：lifespan 周期任务把本进程持有（owner_instance_id）的活动执行态 Run
    # （running/routing/created）续 heartbeat_at；0=关闭（仅调试——关闭后本进程的 Run
    # 在其他副本/下次启动对账眼里会因心跳停更而在 TTL 后被判僵尸）。
    RUN_HEARTBEAT_INTERVAL_SECONDS: int = 15
    # 租约 TTL：heartbeat_at 为空（legacy 存量行）或早于 now-TTL 即判租约过期（僵尸可收敛）。
    # 必须显著大于心跳间隔（默认 3 倍），否则一次心跳抖动就会被别的 worker 误杀。
    RUN_LEASE_TTL_SECONDS: int = 45

    # ---- Worker 并发（v2.40）：单 worker 进程内同时执行的 job 数。
    # 串行时一个 15 分钟 PPT 会堵住后续 pure_qa/天气，浏览器表现为「正在思考」挂死。
    # SKIP LOCKED 已支持多 worker；同进程多协程同样安全。1=旧行为。
    WORKER_MAX_CONCURRENT: int = 3

    # ---- 双库对账扫描窗口（P1，run_reconcile_service）----
    # 启动核销窗口：默认 168h=7 天，覆盖跨周末停机；对更旧历史做一次性深扫时临时调大重启。
    RECONCILE_STARTUP_WINDOW_HOURS: int = 168
    # 周期 sweep 核销窗口：与 RUN_SWEEP_INTERVAL_SECONDS（默认 1h）配套，2h 有一倍冗余。
    RECONCILE_SWEEP_WINDOW_HOURS: int = 2

    # ---- 启动期 DDL 门禁（P1 Alembic 版本化迁移配套，见 agent-api/migrations/README.md）----
    # True（默认，本地/开发友好）：保留启动期 create_all + _migrate_* + Runtime 裸 SQL 补列。
    # False（生产滚动发布）：启动不执行任何 DDL——schema 由部署前的 alembic upgrade head 管理；
    # 仅保留 ai_chat_messages 关键列断言与 Runtime 域库连通探测（缺列/不可达即拒绝启动）。
    MIGRATE_ON_STARTUP: bool = True

    # 主对话多模态图文直传（ADR-040 修订）：模型 id 命中以下任一关键字（小写子串）时，
    # 图片附件以 image_url 直接进模型的 HumanMessage，而非退化为 OCR 文本。逗号分隔，管理员可改。
    VISION_MODEL_KEYWORDS: str = (
        "vl,vision,gpt-4o,gpt-4.1,o4-mini,claude-3,claude-4,claude-opus,claude-sonnet,"
        "claude-haiku,gemini,internvl,llava,minicpm-v,glm-4v,glm-4.1v,glm-4.6v,step-1v,step-3,"
        "yi-vision,pixtral,llama-3.2-vision,molmo,qvq,doubao-vision,ernie-4.5-vl,kimi-vl,grok"
    )
    # 2026-09-19 用户拍板「OCR 不需要，我们用的是多模态模型」：管理员在「管理配置 → 对话模型」
    # 里配的那份连接（以及用户个人覆盖）所指的模型一律视为多模态——图片以 image 内容块直接进
    # 模型，不再经「视觉模型描述成文字 → 再喂文本模型」中转。关键字表跟不上模型命名
    # （grok / gpt-5 / qwen3 都不在表里），只靠它会让平台模型静默退回 OCR 中转链路。
    # 若将来平台模型换成纯文本模型，把这个开关置 False 即可恢复关键字判定。
    MODEL_CONNECTION_MULTIMODAL: bool = True

    # 上传附件解析后正文的字符上限：超出即截断并在附件卡标「部分读取·内容超长已截断」。
    # 20000 对一份学习/需求长文太小（正文尾部读不到）；现代模型上下文 128K+，放宽到 60000
    # 覆盖绝大多数文档；仍需截断的超大文件可用环境变量再调。图片/PDF 逐页 OCR 亦共用此上限。
    DOC_PARSE_MAX_TEXT_CHARS: int = 60000

    # ---- 工具循环轮次预算 ----
    # 一轮 = 一次 LLM 往返。产出型任务（逐页写 PPT→转换→审查→返工）每次质量返工都要
    # 消耗一轮重型 execute_in_sandbox；预算太小会在模型正干活时被强制收敛掐断、宣布「没有成品」。
    # 2026-07-21 用户反馈「工具循环次数不足」（PPT 逐页产出+编译+审查返工被 16 轮掐断）：
    # 大幅放宽到实际任务几乎碰不到的高度；保留数值只作「死循环熔断」，不作工作量限制。
    TOOL_LOOP_MAX_STEPS: int = 40
    # 质量门禁触发返工时的动态扩容：每次返工 +2 轮、累计不超过此值——返工是「确定在
    # 产出」的轮次，不该挤占普通推理预算；普通对话不受影响（不返工就不扩容）。
    TOOL_LOOP_QUALITY_EXTRA_STEPS: int = 20
    # 单轮输出上限（对齐 Claude：max_tokens 是模型看不见的强制上限，与模型看得见的
    # 预算提示分工不同）。0 = 不下发，用渠道/模型默认——这是默认值，因为各渠道模型的
    # 输出上限差异很大（4k~128k），统一下发一个偏大的值可能被渠道判非法请求。
    # 截断本身已由 finish_reason=length 分支兜住（不误执行残缺工具调用），此项只是给
    # 运维一个显式收紧/放宽的旋钮：明确知道渠道能吃下多少时再设。
    TOOL_LOOP_MAX_TOKENS: int = 0
    # 只读工具并发执行（对齐 Claude「同一轮的多个 tool_use 应并发执行」）：同批里
    # parallel_safe 的调用先并发跑，主循环仍按模型顺序逐个消费——事件顺序/状态记账/
    # messages 追加全不变，只是把多次网络等待叠在一起（多次 search_web 的场景最明显）。
    # 有副作用的工具（写文件/execute_in_sandbox/子智能体）不参与。出问题可置 false 立即回到全串行。
    TOOL_LOOP_PARALLEL_READS: bool = True
    # V3 contiguous parallel group concurrency.  Exclusive tools are hard barriers; a long
    # parallel group is consumed in bounded chunks of this size.
    TOOL_LOOP_MAX_PARALLEL: int = 4
    # PPT 参考图由独立视觉模型先提取视觉 DNA。原先外层 6s 硬切断时，
    # 真机视觉请求还未返回就恒定降级；单独留可运维旋钮，不与 Narrator 超时共用。
    PPT_STYLE_REFERENCE_TIMEOUT_SECONDS: float = 20.0
    # ---- 熔断的另外两个维度（2026-07-27）----
    # 此前熔断只有「轮次」一个维度，而一轮的成本方差是两个数量级（一轮 execute_in_sandbox 写 PPT
    # 可能烧两万 token，一轮 list_files 烧两百）——40 轮的花费与耗时都不可预测。
    # 补上 token 与墙钟：三者任一触顶即进入强制收敛轮（模型据已有结果作答，不是硬切）。
    # 数值按「实际任务几乎碰不到」取，与 TOOL_LOOP_MAX_STEPS 同一哲学：只作熔断，不作限额。
    TOOL_LOOP_TOKEN_BUDGET: int = 150_000        # 单轮对话累计输出 token 上限（0=不限）
    TOOL_LOOP_MAX_WALL_SECONDS: int = 900        # 整个工具循环的墙钟上限（0=不限）
    # 对齐 Codex 默认 stream_max_retries=5：仅网络/超时/可重试服务状态按
    # 200ms 起步指数退避；认证、额度、参数与模型明确失败不消耗重连额度。
    MODEL_STREAM_MAX_RETRIES: int = 5
    MODEL_STREAM_RETRY_BASE_SECONDS: float = 0.2
    MODEL_STREAM_RETRY_MAX_SECONDS: float = 3.2
    # Provider usage governance is observation-only in the first rollout.  It records every
    # physical attempt and emits control-plane alerts, but never injects counters into the model
    # prompt or terminates a Run based on an inferred cost.
    MODEL_USAGE_ENFORCEMENT_MODE: str = "observe"
    # Cross-Run context projection is persisted first as a shadow candidate.  DeepSeek remains a
    # stateless full-input transport even after canary/on; stateful Responses continuation is an
    # independent Provider capability and must never be inferred from supports_responses alone.
    # Requested target. Runtime clean-window gates still enforce shadow -> 10% canary -> on.
    THREAD_PROJECTION_MODE: str = "on"
    STATEFUL_RESPONSES_CONTINUATION: bool = False
    # 极旧渠道不能流式时的非流式兼容收尾。httpx read timeout 是单次
    # socket 读取上限；这里另加绝对墙钟。0=不设绝对上限（不推荐）。
    MODEL_FALLBACK_WALL_TIMEOUT_SECONDS: float = 120.0
    # 深度研究模式独立预算（P2 轻增强）：研究任务通常更长，与普通对话熔断解耦。
    # 0=回落 TOOL_LOOP_* 普通值。不引入独立子系统，只放宽主循环熔断档。
    RESEARCH_TOOL_LOOP_MAX_STEPS: int = 60
    RESEARCH_TOOL_LOOP_MAX_WALL_SECONDS: int = 1800
    RESEARCH_TOOL_LOOP_TOKEN_BUDGET: int = 250_000
    # Research 覆盖层预算与质量门槛。一次 search_web 在 research_depth 下会并发读取
    # 多个页面，因此这里限制的是「独立查询」而不是网页数。平台只要求每个主题至少
    # 有一条成功查询与多站点正文证据；是否继续补搜由主模型按具体证据缺口判断。
    # 上限仍只是熔断，不是模型必须花完的配额。
    RESEARCH_COVERAGE_MAX_SEARCHES: int = 24
    RESEARCH_MAX_QUERIES_PER_TOPIC: int = 6
    RESEARCH_MIN_SUCCESSFUL_QUERIES_PER_TOPIC: int = 1
    RESEARCH_CONSECUTIVE_FAILURE_LIMIT: int = 2
    # 合成阶段使用与最终引用快照同序的证据集。正文上限既要让模型真正读到页面内容，
    # 也要给系统提示、历史和最终报告留出上下文余量。
    RESEARCH_EVIDENCE_SOURCE_LIMIT: int = 80
    RESEARCH_EVIDENCE_SNIPPET_CHARS: int = 8_000
    TOOL_CALL_TIMEOUT_SECONDS: int = 300         # 单个工具执行的墙钟上限（0=不限）
    # 轮级引导（steer 插话）在每个工具轮边界的吸收模式（对齐 pi 的 QueueMode，2026-07-27）：
    # all=一个边界一次吸干（≤5 条，现状）；one_at_a_time=一个边界只吸一条，下一条留到
    # 下个边界——弱模型对同时注入的多条修正常只回应最后一条，逐条注入让每条都得到一次
    # 完整的「读到→反应」循环。封口逻辑保证最终回答前队列必然排空，两种模式都不丢条。
    RUN_INPUT_DRAIN_MODE: str = "all"

    # 上下文预算与压缩（§13，Phase 6 → 自动压缩「Codex 式」）
    # 阈值按「真实模型窗口 × 比例」派生（见 model_window），不再写死；下列为兜底/比例参数。
    CONTEXT_WINDOW_DEFAULT: int = 32000             # 模型窗口未命中时的保守默认
    # 按模型 id 子串覆盖真实窗口（token），优先于 model_window 内置表。运维按自家网关渠道的
    # 真实上限设置——渠道上限常小于模型原生窗口（见 plain_turn 超窗兜底注释）。JSON 环境变量，
    # 例：MODEL_WINDOW_OVERRIDES='{"qwen3.7-plus": 1000000, "qwen3.7-max": 262144}'
    # 注意：此值同时驱动自动压缩阈值（0.6×窗口触发）与历史预算（0.75×窗口），设得高于渠道真实
    # 上限会导致长会话直接超窗被拒而非提前压缩，务必填渠道实际能吃下的上限。
    MODEL_WINDOW_OVERRIDES: Dict[str, int] = {}
    CONTEXT_BUDGET_RATIO: float = 0.75             # 本轮「历史+摘要」占窗口的目标上限比例
    # 已废弃（2026-07-27 pi 对齐）：触发线改为「窗口 − 预留」并封顶 92%（见 model_window.
    # compact_trigger），不再按 0.6 比例——60% 触发把 1M 窗口模型压在 60 万就开始丢细节。
    # 字段保留仅为老部署 env 不炸；代码不再读取。
    CONTEXT_COMPACT_RATIO: float = 0.6
    # pi 对齐的压缩预留（reserveTokens，pi 默认 16384）：触发线 = 窗口 − 预留，预留覆盖
    # 本轮新增（用户消息+输出+系统块波动）。小窗口自适应取 10%（16K 会吃掉小模型半个窗）。
    CONTEXT_COMPACT_RESERVE_TOKENS: int = 16384
    # 触发线占窗口的封顶比例：估算+校准+账本仍有残差，最后 8% 留作安全垫（超窗另有
    # force_compact+重试兜底，但那是失败路径，不该常走）。
    CONTEXT_COMPACT_MAX_UTILIZATION: float = 0.92
    # 压缩段超过此估算体量时转后台执行（本轮直接放行——触发线自带预留，本轮必然装得下；
    # pi 在 agent_end 空闲期压缩同理）。20s 同步窗口大约压得动 3 万 token，再大必然超时白等。
    CONTEXT_COMPACT_SYNC_MAX_TOKENS: int = 30000
    # 后台压缩硬超时：大段（可达几十万 token）走摘要 LLM 是分钟级任务
    CONTEXT_COMPACT_BACKGROUND_TIMEOUT_SECONDS: int = 600
    CONTEXT_RESERVE_OUTPUT_TOKENS: int = 2000       # 预留给模型输出的 token（不计入历史预算）
    CONTEXT_HISTORY_TOKEN_BUDGET: int = 8000        # 兜底历史预算（未取到窗口时用；窗口派生优先）
    CONTEXT_COMPACT_THRESHOLD_TOKENS: int = 12000   # 兜底压缩阈值（未取到窗口时用）
    CONTEXT_COMPACT_RECENT_KEEP: int = 8            # 保留近期的最少条数下限（主口径已改按 token，见下）
    # pi 对齐（keepRecentTokens，pi 默认 20000）：压缩时保留近期的口径从「条数」改为「token」——
    # 8 条长消息可能 5 万 token（压不干净），8 条短消息可能 400 token（压过头丢近期语境）。
    # 小窗口自适应取触发线的 1/3。
    CONTEXT_COMPACT_KEEP_RECENT_TOKENS: int = 20000
    # 可压段最小体量（E2E 实证负收益保护，2026-07-16）：摘要本身约 600 字/500+ tokens，
    # 被压段比它还短时压缩是负收益（占用不降反升）——视为无可压段返回 False。
    # 超窗兜底/自动触发场景 to_compact 天然远超此值，不受影响。
    CONTEXT_COMPACT_MIN_SEGMENT_TOKENS: int = 1200
    # 应急硬上限：压缩不可用时才按此裁旧（有日志，非静默）。必须高于压缩触发线
    # （CONTEXT_COMPACT_MAX_UTILIZATION=0.92），否则正常增长区间会被应急裁剪抢跑。
    CONTEXT_HISTORY_HARD_CAP_RATIO: float = 0.96
    # 发送前同步压缩硬超时：超时即降级不压缩、本轮照常开跑。
    # 2026-08-08：20s 会把「首字」直接拖成空白；压缩可后台做，不该阻塞首帧。
    CONTEXT_COMPACT_TIMEOUT_SECONDS: int = 4

    # 发布审批（WS2，强制审批）：所有工作流/对话 Agent 发布须提交审核，审核员通过后上线。
    # 平台管理员角色白名单（逗号分隔的 auth-api 返回的 role_id）。命中即拥有跨用户管理权限。
    # role_id↔权限码映射是跨团队 seam；username==admin 或 role 含 "admin" 亦视为平台管理员（沿用 is_admin）。
    AGENT_ADMIN_ROLE_IDS: str = ""
    INTERNAL_SYNC_MAX_AGE_SECONDS: int = 300

    # 选中知识库后的前置召回不能无限挡住首帧；超时后模型会明确说明本轮未取得资料。
    # 2026-08-08：12s 对「选了库的普通问」过长；6s 足够大多数 Java 召回，超时走诚实降级文案。
    KNOWLEDGE_PRE_RETRIEVE_TIMEOUT_SECONDS: int = 6
    # 对话附件的深度解析（OCR/视觉识别）不能把上传或发消息卡死数分钟。
    CHAT_ATTACHMENT_PARSE_TIMEOUT_SECONDS: int = 20

    # ---- 首字延迟预算（2026-08-08 根治空白 10–20s）----
    # 回合准备（记忆/技能目录/智能体召回等）总墙钟上限：超时用已完成部分继续，不拖首帧。
    TURN_PREPARE_BUDGET_SECONDS: float = 2.5
    # 工具循环开跑前预检（联网开关/子智能体发现/连接器说明/KB 前置）总墙钟上限。
    MAIN_TOOL_PREFLIGHT_BUDGET_SECONDS: float = 3.0

    # 联网搜索抓取段 SSRF 预校验逃生阀（仅本地开发，生产必须 False）：开发机挂 fake-ip 模式
    # VPN 时公网域名会被解析成保留段（198.18.x.x），令客户端 DNS 预校验全数误拦→搜索 0 结果。
    # True 则跳过该预校验，靠抓取端（自托管 Firecrawl，服务器真实 DNS）自身 SSRF 防护兜底。
    WEB_SEARCH_SKIP_URL_SSRF_CHECK: bool = False

    # ---- 联网搜索初始值（env 播种，库覆盖）----
    # 语义：这些值只作为 agent_platform_config.web_search 的“缺省值”——后台「联网搜索配置」页
    # 一旦保存过，对应字段以库中值为准（多实例共库共享）。用途：全新部署/新库时不进后台也能
    # 直接把三段自托管服务接上（部署即用），与“运维配 env、管理员配后台”两种习惯兼容。
    WEB_SEARCH_ENABLED: bool = False
    WEB_SEARCH_SEARXNG_URL: str = ""          # 搜索段，如 http://<服务器IP>:8085
    # 正版图库三源（2026-07-22 PPT 配图质量升级）：图片段按 本地精选库→Pexels→Unsplash→
    # Pixabay→搜索引擎 的顺序供图；key 为空的源自动跳过
    WEB_SEARCH_PEXELS_KEY: str = ""
    # 图片段专用引擎（非空则覆盖由 searxngEngines 推导的 images 变体）。
    # 2026-07-22 实测：本实例仅 baidu/sogou/quark images 真实可用；360search images
    # 查无此引擎、bing/duckduckgo/google images 全部 0 结果
    WEB_SEARCH_SEARXNG_IMAGE_ENGINES: str = ""
    WEB_SEARCH_UNSPLASH_KEY: str = ""   # unsplash.com/developers 注册应用后的 Access Key
    WEB_SEARCH_PIXABAY_KEY: str = ""    # pixabay.com/api/docs 登录后页面直接显示 key
    # 逗号分隔的 SearXNG 引擎；境内服务器建议 baidu,sogou,360search,quark（境外引擎被墙 0 结果）
    WEB_SEARCH_SEARXNG_ENGINES: str = ""
    WEB_SEARCH_FIRECRAWL_URL: str = ""        # 抓取段，如 http://<服务器IP>:8086；非空即默认启用 firecrawl
    WEB_SEARCH_LOCAL_RERANKER_URL: str = ""   # 重排段（TEI /rerank），如 http://<服务器IP>:8087；非空即默认启用 local

    # 平台业务时区：ThreadWorldState 只投影日期/时区，精确时刻由
    # get_current_time 按需读取（容器默认 UTC，直接 now() 会差 8 小时）。
    AGENT_TIMEZONE: str = "Asia/Shanghai"

    # 用户粗略网络位置：只在模型按需调用 get_user_location 时查询。
    # 原始 IP 不进模型上下文；供应商响应仅保留国家/省州/城市/时区。
    USER_LOCATION_ENABLED: bool = True
    USER_LOCATION_PROVIDER_URL: str = (
        "https://ipwho.is/{ip}"
        "?fields=success,country,country_code,region,city,timezone.id&lang=zh-CN"
    )
    USER_LOCATION_TIMEOUT_SECONDS: float = 5.0
    USER_LOCATION_CACHE_TTL_SECONDS: int = 21600
    USER_LOCATION_CACHE_MAX_ENTRIES: int = 2048

    # Auth
    AUTH_HEADER_PREFIX: str = "X-"

    # 技能容器沙箱（对齐蓝本 sandbox-adapter 多 provider 架构）
    SKILL_SANDBOX_ENABLED: bool = True
    # provider 选择（蓝本三种，管理员选一个并配好凭证/基建）：e2b / sealosdevbox / opensandbox
    SKILL_SANDBOX_PROVIDER: str = "opensandbox"
    # 并发闸：全局最多同时**执行中**的沙箱调用数（满了排队，防高并发雪崩）；0=不限
    SKILL_SANDBOX_MAX_CONCURRENT: int = 20
    # ---- Run 级沙箱复用（2026-07-22 拍板）：一个 Run/graph 节点内多次 execute_in_sandbox 共用同一容器，
    # /workspace 中间产物保留到该 Run 结束，重试只补失败的一步而不是从零重跑整条管线。
    # 关掉即回到「每次 execute_in_sandbox 全新沙箱」的旧行为（提示词同步跟着切换，不会对模型撒谎）。
    SANDBOX_SESSION_REUSE_ENABLED: bool = True
    # 存活上限：独立于上面的执行并发闸——复用会话在整个 Run 期间持有容器，不能和执行闸共用名额。
    # 满员且全部在执行中时，先短等空闲会话，仍满才降级为一次性沙箱。
    SANDBOX_SESSION_MAX_LIVE: int = 24
    SANDBOX_SESSION_BUSY_WAIT_S: float = 8.0
    # 空闲多久回收（秒）：显式关闭漏掉时的第二层兜底
    SANDBOX_SESSION_IDLE_TTL_S: int = 900
    # 单个会话硬生命周期上限（秒）：再活跃也到点回收，防长任务把容器焊死
    SANDBOX_SESSION_MAX_LIFETIME_S: int = 3600
    # 回收巡检间隔（秒）
    SANDBOX_SESSION_REAP_INTERVAL_S: int = 60
    # bash 单文件写入上限（MB）；0=不设 ulimit -f。
    SKILL_SANDBOX_LOCAL_MAX_FILE_MB: int = 512
    # 统一用户文件镜像上限；超限必须进回执，禁止静默丢文件。
    SANDBOX_WORKSPACE_MAX_FILES: int = 200
    SANDBOX_WORKSPACE_MAX_BYTES: int = 50 * 1024 * 1024
    # 产物审查 v1（实施说明 Phase C §4.2）：execute_in_sandbox 产物在同一沙箱内做结构化硬校验后再交付
    SANDBOX_OUTPUT_REVIEW_ENABLED: bool = True
    # 独立渲染检查（2026-07-15 拍板定位=advisor 查错，不是质量 gate）：结构校验通过后，
    # 把文档渲染页 + 内容提纲交给独立多模态模型**查硬伤**（溢出/乱码/空白/缺内容）。
    # 不询问、不记录审美与专业度评价。
    SANDBOX_VISUAL_REVIEW_ENABLED: bool = True
    # 已失效（2026-07-15 拍板：检查服务异常/unknown 不得触发返工或拦截）——保留仅为兼容
    # 旧 .env，代码不再读取；勿据此恢复「审查不可用即不放行」。
    SANDBOX_VISUAL_REVIEW_REQUIRED: bool = True
    SANDBOX_VISUAL_REVIEW_MAX_FILES: int = 8
    SANDBOX_VISUAL_REVIEW_MAX_PAGES: int = 6
    # 渲染检查只查客观错误，不产生分数或审美门槛。
    # entrypoint 安装超时（秒）
    SKILL_SANDBOX_DEPLOY_TIMEOUT: int = 120
    # ---- e2b provider（商业云，需 pip install e2b-code-interpreter）----
    SKILL_SANDBOX_E2B_API_KEY: str = ""
    SKILL_SANDBOX_E2B_TEMPLATE: str = ""
    # ---- sealosdevbox provider（云开发环境）----
    SKILL_SANDBOX_SEALOS_BASE_URL: str = ""
    SKILL_SANDBOX_SEALOS_TOKEN: str = ""
    SKILL_SANDBOX_SEALOS_DEVBOX: str = ""
    # ---- opensandbox provider（阿里开源，需 K8s + pip install opensandbox）----
    SKILL_SANDBOX_OPENSANDBOX_DOMAIN: str = ""  # controller-server:8080
    SKILL_SANDBOX_OPENSANDBOX_API_KEY: str = ""
    SKILL_SANDBOX_OPENSANDBOX_POOL: str = ""
    SKILL_SANDBOX_OPENSANDBOX_IMAGE: str = ""
    # OpenSandbox server proxy keeps execd endpoints private to the sandbox network.
    SKILL_SANDBOX_OPENSANDBOX_USE_SERVER_PROXY: bool = True
    SKILL_SANDBOX_OPENSANDBOX_CPU: str = "1"
    SKILL_SANDBOX_OPENSANDBOX_MEMORY: str = "1Gi"
    SKILL_SANDBOX_OPENSANDBOX_READY_TIMEOUT_S: int = 90
    # ---- local provider（MVP/本地测试，docker CLI 起兄弟容器；ADR-047 §6.5，仅本地不进生产）----
    SKILL_SANDBOX_LOCAL_IMAGE: str = "agent-sandbox-py:local"
    SKILL_SANDBOX_LOCAL_NETWORK: str = "none"       # 默认断网（§7.1）；需外网时改白名单网络名
    # 1g：LibreOffice 文档转换（Word/PPT→PDF）内存偏吃，512m 易 OOM；仍是硬上限，防单任务拖垮宿主
    SKILL_SANDBOX_LOCAL_MEMORY: str = "1g"
    SKILL_SANDBOX_LOCAL_CPUS: str = "1.0"
    SKILL_SANDBOX_LOCAL_PIDS_LIMIT: int = 128       # 防 fork 炸弹
    SKILL_SANDBOX_LOCAL_USER: str = "sandbox"       # 非 root
    SKILL_SANDBOX_LOCAL_TIMEOUT_MS: int = 60000
    SKILL_SANDBOX_LOCAL_MAX_OUTPUT_BYTES: int = 32768
    # 后端：auto=有 docker-py 走 SDK(经 socket，容器内无 CLI 也可)否则用 docker CLI；cli/sdk 强制。
    # 容器内（agent-api）没有 docker CLI，须挂 /var/run/docker.sock + pip docker → 用 SDK。
    SKILL_SANDBOX_LOCAL_BACKEND: str = "auto"
    SKILL_SANDBOX_LOCAL_DOCKER_HOST: str = ""  # 空=docker.from_env（默认 unix:///var/run/docker.sock）

    # ---- 浏览器服务（agent-browser 独立容器，Playwright MCP）----
    # 空 = browser_fetch/browser_open/browser_act/browser_close 工具不注册（功能整体关闭）。
    # 例：http://127.0.0.1:8089/mcp
    BROWSER_SERVICE_URL: str = ""
    BROWSER_FETCH_TIMEOUT_S: float = 60.0
    BROWSER_FETCH_MAX_CONCURRENCY: int = 4
    BROWSER_FETCH_MAX_PER_USER: int = 1
    BROWSER_FETCH_MAX_PAGE_CHARS: int = 120000
    BROWSER_FETCH_CACHE_TTL_S: int = 900
    BROWSER_LIVE_MAX_SESSIONS: int = 4
    BROWSER_LIVE_IDLE_TTL_S: int = 180
    BROWSER_LIVE_MAX_LIFE_S: int = 900
    BROWSER_LIVE_SWEEP_INTERVAL_S: int = 60
    BROWSER_FETCH_MODEL: str = ""

    # 「我的文件」用户文件工作区（ADR-047 §6.6）：沙箱管算、这里管存
    # 相对路径=相对进程 cwd（容器内 /app → 经 ./:/app bind mount 落宿主 agent-api/，天然持久）
    USER_FILES_DIR: str = "data/user_files"
    USER_FILES_QUOTA_MB: int = 200          # 每用户字节配额（防变网盘）
    USER_FILES_MAX_COUNT: int = 200         # 每用户文件数上限
    USER_FILES_MAX_SIZE_MB: int = 15        # 单文件上限（与 /chat/upload 的 413 一致）
    USER_FILES_GENERATED_TTL_DAYS: int = 7  # 沙箱产物默认保留天数（0=永久）；「保留」转永久
    USER_FILES_MAX_FOLDERS: int = 50        # 每用户文件夹数软上限（防手滑建一堆）
    # 每文件版本快照数上限（Phase B；版本不计配额，此上限防反复编辑无限吃磁盘）；0=不限
    USER_FILES_MAX_VERSIONS_PER_FILE: int = 100
    # 用户文件字节存储后端：local（兼容现有 USER_FILES_DIR）/ minio（统一对象存储）。
    FILE_STORAGE_PROVIDER: str = "local"
    FILE_STORAGE_BUCKET: str = ""
    FILE_STORAGE_PREFIX: str = ""
    MINIO_ENDPOINT: str = ""
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_SECURE: bool = False

    # ---- 外部应用连接器（GitHub / Gmail / Outlook / Canva 等）----
    # 凭据加密主密钥。多实例部署应显式配置；留空时使用 CONNECTOR_KEY_FILE 本地密钥文件。
    CONNECTOR_SECRET_KEY: str = ""
    CONNECTOR_KEY_FILE: str = "data/.connector_key"
    # GitHub App OAuth + 安装选仓库配置；缺任一项时只保留已连接账号/令牌直连能力。
    CONNECTOR_GITHUB_CLIENT_ID: str = ""
    CONNECTOR_GITHUB_CLIENT_SECRET: str = ""
    CONNECTOR_GITHUB_APP_SLUG: str = ""
    CONNECTOR_OAUTH_REDIRECT_URI: str = ""
    # GitHub 远程 MCP 只读仓库工具集端点。
    CONNECTOR_GITHUB_MCP_URL: str = "https://api.githubcopilot.com/mcp/x/repos/readonly"
    # Google / Microsoft / Canva OAuth 凭据。留空时对应连接器显示未配置。
    CONNECTOR_GOOGLE_CLIENT_ID: str = ""
    CONNECTOR_GOOGLE_CLIENT_SECRET: str = ""
    CONNECTOR_MICROSOFT_CLIENT_ID: str = ""
    CONNECTOR_MICROSOFT_CLIENT_SECRET: str = ""
    CONNECTOR_MICROSOFT_TENANT: str = "common"
    CONNECTOR_CANVA_CLIENT_ID: str = ""
    CONNECTOR_CANVA_CLIENT_SECRET: str = ""
    CONNECTOR_CANVA_MCP_URL: str = ""
    CONNECTOR_TOOLS_TTL_SECONDS: int = 86400
    CONNECTOR_MAX_RESOURCES: int = 20

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
