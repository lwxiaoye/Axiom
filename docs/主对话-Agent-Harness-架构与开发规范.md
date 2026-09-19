> AXIOM 迁移说明：保留上游较新的 v1.211 架构说明，已合并原文件中的两处文档冲突。2026-09-19：工作流编排、子智能体委派、对外 Agent API、智能体推荐与皮肤系统已整体删除，本文相应段落已去掉；§15 中只描述这些已删功能的条目一并移除，完整历史见 Git。历史测试、部署及服务状态不代表 AXIOM 当前环境。

# 主对话 Agent Harness 架构与开发规范

| 项目 | 内容 |
| --- | --- |
| 文档身份 | **主对话唯一执行事实源（SSOT），覆盖架构、开发、Code Review 与验收** |
| 版本 | v2.0 |
| 更新日期 | 2026-09-19 |
| 当前状态 | H1–H6 的源码入口与协议切换已完成，H2 扩展能力和 H7 仍有独立运行验收门禁。Standard/Plan 共用主模型工具循环；Research 经同一 Harness 入口进入专用研究编排，当前以临时团队取证、空工具面成稿和发表前核验为主路径。Run、Plan、Tool、Context、Event、Worker 与恢复保持共享；内置 presentation/campus_services/interview 通过策略注册接入。源码存在不等于服务已加载或发布门禁通过。 |
| 最新交互 | 普通主对话在 + 右侧提供绑定 Thread 的工作文件夹；连接器与旧顶栏工作区入口仍隐藏。Research 完成行使用持久耗时和 URL 去重后的 sourcesFound，成员说明展示实际网页/材料成果，导航只释放观察者。面试过程中只展示问答，动作收进单个菜单；整场结束并保存复盘后才展示白灰报告，分数、点评和导出复用服务端整场汇总。按凭据密钥作用域排队的 Job 使用 queued_scoped/leased_scoped，公开 Run 协议不变。具体契约见 §3、§5.3、§6.6.2、§9.4，历史验收按日期另读。 |
| 适用范围 | `/center/chat`、`/center/chat/ppt`、`/center/chat/campus`、`/center/chat/interview`、主对话及内置应用前端、AXIOM Agent（`agent-api` 主 Agent）、Run Worker、工具与沙箱、上下文、记忆、事件和计划 |
| 不适用范围 | 知识库管理页（`src/views/knowledge/`）、管理配置页 `/admin`、auth-api 的登录与用户体系 |

> 本文是主对话架构、开发、Code Review 和验收的唯一事实源。
> 与其他文档、代码注释或历史实现冲突时，以本文为目标；若产品决策发生变化，必须先更新本文，再修改代码。

---

## 0. 怎么使用本文

1. 开始任何主对话开发前完整阅读本文，不从历史文件或代码注释推导目标架构。
2. 开发任务必须标明本文的阶段和条目，例如 `H2 / ToolSpec`、`H4 / Plan CAS`。
3. 本文描述的是**目标和实施契约**；未勾选的项目不得被宣传为已经落地。
4. 产品原则变化时先修改本文，再修改代码和测试。
5. 阶段完成后在本文更新状态和证据，不创建新的“主对话开发计划”“Harness V2”或会话交接稿。
6. 历史调查的有效结论并入本文后删除；需要追溯时使用 Git 历史。

### 0.1 事实、目标与假设

- **当前已确认事实**：主对话已有持久 Run、后台 Worker、事件回放、RunState CAS、工具网关、沙箱会话、`bash`、文件工具、搜索/浏览器/知识库/连接器以及计划和研究入口等基础能力。
- **切换前已确认问题**：主循环和入口文件过大；DAG、旧计划卡、Runtime V2、退休工具名和兼容协议曾有残留；工具策略依赖名字集合；模式逻辑散落；前后端曾保存旧任务图投影。
- **当前已确认源码状态**：主对话生产代码入口统一到 Agent Harness；旧主对话 DAG、Runtime V2、`run_code`、任务卡和协议执行路径已切换。工具、Profile、计划、上下文、记忆、事件和完成验证均有正典接口与契约测试；这不等于确认任一运行服务已加载当前工作区。
- **目标状态**：本文定义的 AXIOM Agent Harness。
- **兼容边界**：不恢复已退休的主对话 V1/V2 协议；当前协议内已有的历史消息投影、活动 Run 游标、面试旧五分制只读换算及无团队研究记录仍有兼容路径，不得把“不兼容旧架构”解释成可删除这些恢复能力。

### 0.2 当前源码核对入口（2026-09-11）

本次核对基于本地工作区（含未提交改动），以 Git HEAD `b2ab8bab4` 为提交基线；不等于远端最新发布版本。以下只记录源码可核实的入口，测试通过数、运行服务、Provider 实测和上线状态必须另附各自证据。

| 核对项 | 当前源码入口与边界 |
| --- | --- |
| 主入口与循环 | `agent_harness/kernel.py::HarnessKernel.stream` → `orchestrator.py`；Standard/Plan 使用共享模型驱动，Research 由 `research/kernel.py` 编排 |
| Research | `agent_harness/research/team.py`、`scope.py`、`budget.py`、`quality.py`、`review.py`；团队取证后不再跑旧覆盖流程，报告恢复不重置证据或重开检索 |
| 终态 | `agent_harness/completion.py` 提供结构化判定，`chat/turn_finalizer.py` 消费等待/失败/部分交付并做终态 CAS；诊断性 `continue/verifying` 不回灌模型循环 |
| 队列与恢复 | `agent_harness/run_store.py` 与 `app/worker.py`；Job 状态和公开 Run 状态分开，密钥不兼容的 Worker 不领取作用域任务 |
| 本机服务 | `agent-api/run.py`、`app/core/local_worker.py`、`app/main.py`；统一环境和密钥、子 Worker 监护、就绪检查 |
| 内置应用 | `chat/builtin_assistants/registry.py` 与各模块策略；前端 `builtinAssistants/`、`BuiltinHarnessRunPage`，路由见 `src/router/routes/mainOut.ts` |
| 工作文件夹 | `app/routers/files.py`、`files/user_file_service.py`、`agent_harness/workspace_service.py`；前端 `WorkFolderMenu.vue` 和 `useCenterChat.ts` |
| 数据迁移 | MySQL 链头 `mysql_0024_drop_orchestration`（其前一版 `mysql_0023_drop_skins`），Runtime 链头 `runtime_0024_drop_eval_runs`；旧库升级规则见 [迁移说明](../agent-api/migrations/README.md) |
| 已删除（2026-09-19） | 子智能体委派（`call_subagent`）、智能体推荐（`recommend_agent`）、工作流编排与对外 Agent API、皮肤系统整体删除；`ChatRequest.subagent_id` 只为旧前端宽松保留并被忽略，`services/agents/` 只剩模型目录 `agent_service.py` |

表内后端缩写路径以 `agent-api/app/services/` 为基准，带 `app/` 的路径以 `agent-api/` 为基准。现存源码中的旧注释、未被调用的验证辅助分支及架构测试，只能按实际调用链判断，不能直接作为当前产品行为。

产品待确认项：`AGENTS.md` 的三项主导航、两个 Skill 入口约定，与当前桌面六项导航、手机/iPad 四项导航及 + 菜单 Skill 入口存在差异。本次如实记录现状并保留目标要求，不自行选择产品方案或修改 UI。

---

## 1. 一句话目标与不可回退原则

把主对话建设成一个 Web 端任务执行者。主对话里的主 Agent 产品名称是 **AXIOM Agent**（欢迎语与执行过程中的缺省名称）。Harness 运行时仍叫 AXIOM Agent Harness，不改包名、路由或协议。

> **模型负责理解、思考和选择下一步；Harness 负责计划、上下文、权限、执行、状态、记忆、证据和完成真相。**

不可回退原则：

1. 主对话只有一套 AXIOM Agent Harness，不再并存 Runtime V2、V3、legacy 或 DAG 执行内核。
2. Standard 与 Plan 共用同一套 Agent Loop。Research 是唯一允许的专用研究编排：入口仍为 `HarnessKernel.stream`，先 `seed_goal_contract`，再由 `research.kernel` 驱动临时团队取证与报告阶段，复用 `model_driver`、工具网关、Run Store 与 SSE。只有没有团队记录的兼容路径仍可进入 `collect_coverage`；新团队返回后不能再次串行检索。用户停止后继续应继承对应研究 Profile 与台账。禁止为 Plan/Standard 或内置助手另建执行内核。
3. Plan 是可实时修订的语义计划，不是调度 DAG。
4. 普通回合在模型不再调用工具并给出助手回复时结束；证据缺口不能回灌同一 Loop 或把执行锁在 `verifying`。结构化 HITL、可恢复依赖故障、已验证失败/部分交付与 Research 引用核验仍由共享终态链处理，具体见 §9.2。
5. 模型只能调用本轮 Tool Registry 实际提供的工具，提示词不能虚构能力。
6. 用户可以受控查看模型供应商返回的 `reasoning_content` 或官方 reasoning summary：思考是时间线上的一等执行步骤，与 bash 等动作同行。仅当本轮确实产生思考内容时才插入该步骤，发送后不预先占位。进行中标题为 shimmer「Thinking」，不显示秒数，默认收起；点击后完整展示灰色流式正文。结束后标题为「Thoughts for Ns」，仍可展开看全文。箭头紧跟标题右侧（收起向右、展开向下）。正文为 Cursor 同款：15px / 字重 400 / `#999` / 行高 1.55 的无衬线灰色段落，完整不透明。该内容不得写入聊天正文、参与完成判定，也不得冒充真实动作与证据。
7. `bash` 是模型可见的唯一通用执行器；退休工具不得通过提示词、Skill 或兼容层复活。
8. 计划、Run 状态、事件和产物都必须有唯一持久化事实源。
9. 架构替换完成后删除旧代码、旧表和旧前端投影，不保留回退开关。

---

## 2. 总体架构

```text
用户消息
  -> Run API 接受、鉴权、持久化
  -> Run Store 创建 Job 并发出 run.accepted
  -> Worker 获取租约
  -> Context Compiler 编译当前上下文
  -> Harness Kernel 调用模型
       -> 控制命令：更新计划 / 追问 / 提出记忆候选
       -> 工具调用：Tool Registry -> Dispatcher -> Gateway -> Executor
  -> ToolObservation 持久化
  -> Plan Controller 更新步骤和目标版本
  -> Event Projector 生成用户可见事实
  -> 本轮无工具调用的助手回复即完成；结构化 HITL 则等待用户
```

### 2.1 模块边界

目标源码包为 `agent-api/app/services/agent_harness/`：

| 模块 | 只负责 | 禁止负责 |
| --- | --- | --- |
| Kernel | 驱动单轮状态迁移，调用其他模块 | 具体工具逻辑、模式硬编码、数据库细节、UI 文案 |
| Context Compiler | 从事实源编译模型上下文 | 判断业务完成、写记忆、直接执行工具 |
| Run Store | RunState、CAS、Job lease、事件与恢复 | 模型提示词和工具业务逻辑 |
| Profile Registry | Standard/Plan/Research 的能力、约束与观测字段声明 | 复制 Agent Loop、决定任务终态 |
| Policy Engine | 权限、审批、危险动作、幂等、资源锁、取消和外部副作用边界 | 依赖工具名称猜语义、按预算结束任务 |
| Tool Registry | 按 Profile、阶段和权限构建本轮工具表 | 执行工具 |
| Dispatcher/Gateway | 校验、幂等、资源锁、执行和回执 | 修改计划或编写最终回答 |
| Plan Controller | 目标版本、计划版本、确认和步骤状态 | 调度 DAG、直接执行任务 |
| Memory Controller | 检索、候选治理、写入和删除 | 保存临时运行状态 |
| Completion Verifier | 汇总结构化用户等待、系统恢复、验证终态与证据诊断，交收尾层处理 | 将诊断性缺口回灌同一 Loop、把 phase 打成 verifying 锁工具 |
| Event Projector | 将已提交事实投影为前端事件 | 保存另一份 Run 或 Plan 状态 |

### 2.2 依赖方向

```text
API / Worker
    -> Kernel
        -> Context / Plan / Policy / Completion
        -> Tool Registry -> Dispatcher -> Gateway -> Executors
        -> Run Store / Event Catalog
```

- Executors 不得反向引用 Kernel、聊天 UI、Plan Controller 或模型提示词。
- Kernel 不得导入具体工具实现。
- Profile 只能组合能力和策略，不能实现第二套循环。唯一例外：Research 经 `research.kernel` 进入临时团队取证与报告编排，旧无团队记录才走兼容覆盖检索；不得推广到 Standard/Plan。
- 前端只能消费协议快照与事件，不能推导后端终态。

---

## 3. 单一 Harness Loop

Standard/Plan 的共享模型回合按以下步骤执行；Research 的专用取证、报告核验和预算边界见 §5.3，内置应用的领域提交约束见 §6.6：

1. 从 Run Store 读取最新 `state_version`、`goal_revision`、`plan_version` 和事件游标。
2. 根据 Profile、Run 阶段、用户权限和风险边界构建本轮 Tool Registry。
3. Context Compiler 组装最新目标、计划、对话、记忆、工具回执、恢复/终止事实和工作区范围；预算只作为观测指标。
4. 调用模型，接收控制命令、工具调用或完成声明。
5. 校验调用携带的状态版本；过期调用返回结构化 stale observation，不执行副作用。
6. Dispatcher 按资源锁分组：安全只读调用可以并行，写和外部副作用经过独占屏障。
7. Gateway 执行权限、审批、幂等、超时、取消和重试。
8. 将 ToolObservation、产物和真实进度先持久化，再发事件。
9. Plan Controller 更新步骤；Context Compiler 在下一轮读取新状态。
10. 模型本轮不再调用工具并给出助手回复时进入共享收尾层；普通回合不因诊断性证据缺口再次驱动模型。结构化 pending input 进入 `waiting_user`，可恢复依赖故障与研究报告核验按 §9.2 处理。

主 Agent 的供应商传输按模型能力选择，不再按品牌二分：模型目录明确声明支持 Responses 时使用 Responses，明确声明不支持时使用 Chat Completions；能力未知时先发 Responses 探测请求。DeepSeek 全系仍视为产品级 Responses 能力模型，新版网关/渠道可用时不得主动降级；仅当网关返回结构化 `HTTP 500 + error.code=convert_request_failed + error.message` 含 `not implemented`，且当前语义请求尚无任何供应商事件、公开正文/reasoning、工具副作用、Responses opaque cursor 或已确认传输时，才将原语义请求重建为 Chat Completions 流。Responses 成功能力事实可按用户 Key 隔离缓存；运行时协议拒绝只写当前 Run 的 `model_transport=chat_completions` 锁，不把单个 New API 渠道的负面观测写成跨 Run 的模型能力缓存。禁止已产生 Responses cursor 或工具事实后跨协议重放。Responses 只有 `response.completed` 是成功终态，`incomplete` / `failed` / 提前 EOF 不得执行半截工具调用；`reasoning.encrypted_content` 等 opaque item 原样回放给下一模型轮但不进入用户正文。

所有主对话可达的 SDK client 必须显式禁用内建重试（例如 `max_retries=0`），Harness 是唯一重试所有者。模型流发生网络中断、读写/连接超时、HTTP 408/425 或普通 5xx 时，当前显式 Harness 重连策略可对尚未发生公开输出、Provider event 或工具副作用的同协议请求最多重连 5 次；每个真实网络尝试都必须单独记账。同协议重试冻结语义 payload，`semantic_payload_hash` 必须不变；Responses→Chat 或 reasoning 兼容修复改变了传输/控制参数，必须建立带 lineage 和 `fallback_reason` 的新逻辑调用，不得冒充普通重试。上述精确 `convert_request_failed/not implemented` 结构化 500 仍先于普通 5xx 判定；结构化、确定性的网关策略拒绝（当前包括 `error.code=sensitive_words_detected`）也必须先于普通 5xx 判定，不重连、不跨协议降级，并在当前 Run 只发布一次安全且可操作的停止说明。鉴权、额度/429、普通 4xx、上下文、用户取消和供应商明确 `response.incomplete` / `response.failed` 不重连。只有 `response.completed` 后才执行 Responses 工具调用；已有 Provider event、公开输出、opaque cursor 或副作用的尝试不得以「零消耗」重放。瞬时 `model.connection` 事件只表示实际 Harness 重试，不进入模型历史。

DeepSeek 的公开 preamble 是保留的可选体验层，但每轮只允许一个 Responses physical attempt；请求必须使用 `reasoning: {"effort":"none"}` 且不得携带 `thinking`。结果 incomplete、failed、不是完整句或超时时返回空 preamble 并继续主 Run，不得追加修补请求；该 attempt 的全部 usage 必须归到 `public_preamble` 与 originating Root Run。

仅上传图片或文件、未附文字的有效输入同样可以生成公开首句，不得因文字为空跳过后长时间沉默。首句仍早于共享附件预处理，只接收用户原话和附件元数据，不传原图或额外识图；必须明确告知首句模型尚未读取附件内容，不得据文件名猜测画面、文字、主体或已完成的读取结果。主对话、演示文稿助手与校园百事通共用此边界，不新增手机专用首句或视觉循环。

`update_plan` 与真实工具执行不得形成循环依赖。它只负责初次创建计划，或在步骤结构、顺序、文案、验收标准和执行方向真正变化时提交整表修订。非控制工具的成功/失败回执必须由 Plan Controller 绑定当前步骤、提交证据并推进卡片；该投影失败只记录可观测异常，不得拒绝下一工具或终答。模型未额外回写状态型 `update_plan` 时，卡片仍必须依据真实回执实时同步。

Kernel 允许的出口只有 `continue`、`wait_user`、`wait_system`、`complete`、`partial`、`fail` 和 `cancel`。驱动层的 `continue` 表示还有工具/模型工作，完成核验器中同名的诊断结果不意味着重新采样。`wait_user` 只来自结构化 HITL。Standard/Plan 的轮次、token、墙钟、上下文压缩、部署重启和单次超时不得单独选择终态；Research 取证预算单独适用 §5.3，不把报告阶段超时解释为核验拒绝。

HTTP 进程只持久化命令与受理事实；模型循环永远在 worker 中从 Kernel 进入，不恢复进程内 `/chat` 双路径。流畅度在 worker 唤醒、受理并行和事件合批上优化，不靠第二套 Loop。

v1.199 / H4、H5：新任务的 pending_input 由服务端写入不可逆的凭据密钥标识，Worker 在领取租约前过滤不兼容密钥的任务，避免跨环境领取后反复解密失败；使用相同共享密钥的 Worker 仍可负载分担。无标识的旧任务保留原领取规则，不改写历史密文。单机密钥文件先完整写入再原子发布，禁止并发首次启动互相覆盖；run.py 在启动 API/Worker 前初始化密钥。前端继续消费真实恢复/阶段事件。2026-09-11 按用户要求，运行头统一恢复为「本轮处理中」，不再因 created/waiting_system 切换顶部标题；详细进度与后台恢复逻辑保留，不伪造模型思考或重置计时。共享队列的旧版 Worker 仍可能领取不兼容任务，须协调加载新版；源码测试不能替代该部署门禁。

v1.199 验证：后端密钥初始化、队列领取、恢复控制与本地 Worker 监护 **34 passed**，前端 SSE 恢复、运行头与续订回归 **29 passed**；定向 ESLint、Python 编译及补丁检查通过。密钥并发测试使用三个独立进程；队列选择使用隔离 SQLite，不冒充线上 PostgreSQL 并发验收。本次仅存盘，尚未重启运行服务或验证新真实 Run。

v1.204 / H4：为防止未加载密钥检查的旧 Worker 抢领新任务，带凭据密钥标识的 Job 使用 `queued_scoped` / `leased_scoped` 排队和持有租约，仍由同一 Run Store、Job 表及 Worker 执行。新 Worker 同时识别原状态与作用域状态；兼容密钥、租约所有权、过期接管、退避、恢复唤醒和 HITL 继续生效。领取已存在的带标识旧 Job 时升级其调度状态；无标识旧 Job 保持原行为。公开 Run 状态、前端文案、事件与凭据不变，无数据库结构迁移。需同时加载本机 API 和 Worker，并用真实新 Run 验证；回滚旧 Worker 前应先排空作用域 Job，不能直接退回只识别旧状态的消费者。

### 3.1 并发与取消

- 只读调用只有在 `parallel_safe=true` 且资源锁不冲突时并行。
- 写文件、外部副作用、长期记忆写入和相同资源上的操作串行。
- 用户调整目标后立即递增 `goal_revision`。已启动的工具跑完；**尚未启动**的调用若 `goal_revision` 已落后，返回 stale observation，不得执行副作用。
- 可取消的长工具收到取消信号；不可安全强停的工具运行到最近安全点。
- 旧结果仍记录为事实，但不得自动推进新版计划。
- Steer 必须携带 `expected_run_id`（当前活跃 Run）；不匹配则 409，不得改 GoalContract。活 executing 的 steer 只写入 `agent_run_inputs`，禁止再 `enqueue_job`。

### 3.2 长跑与停止条件

Standard/Plan 任务没有固定轮次、累计 token 或总墙钟截止；这些字段仅用于观测、告警和性能优化，不得驱动 `forced_final`、`tool_choice=none`、`failed` 或 `partial`。Research 是明确例外：取证按 §5.3 的冻结范围和预算推进，随后收起工具成稿；报告仍不受旧 180/90 秒业务硬截止控制。单次模型调用、单个工具和外部重试必须有超时与退避，超时结果作为结构化 observation 回给模型或触发运行段恢复。

共享生命周期主要出口如下；等待态不是终态，完成核验的实际收尾规则见 §9.2：

1. 本轮模型不再调用工具并给出助手回复：`completed`（对齐 Codex `run_turn`）。
2. 用户明确取消：`cancelled`。
3. 结构化 HITL（计划确认、`ask_user_choice`、授权）缺少用户输入：`waiting_user`。
4. 工具执行中外部依赖暂时不可用、且尚未给出终答：`waiting_system`，保存现场并自动重试。
5. 用户可见的诚实失败只来自取消以外的明确控制面，不再用过程工具失败否决已经停手的终答。

模型停手后不得把证据缺口回灌同一 `drive_model` Loop，不得把 phase 打成 `verifying` 从而锁工具。`waiting_user` 仍由 finalize 与 HITL 收口。

自动恢复分四级，不得混为一谈：

1. **连接恢复**：SSE `after=` 回放；Worker 继续跑，不重建循环。
2. **工作区恢复**：新沙箱 Pull 会话工作区 / 产物检查点。
3. **循环游标恢复**：`loop_checkpoint.messages` 是 `drive_model` 的续接点，同时保存经 64,000 字符总预算约束的 `world_state`。检查点可剥除内嵌 base64 字节，但必须保留文本、可重取 URL 与显式媒体占位；超过软字符预算只记 `soft_limit_exceeded`，不得自行删除最旧消息。HITL `orchestration.messages` 必须复用同一 sanitizer，不能另造一份只剩文本的历史。只有成功 compact turn 才能将其写为 `history_kind=replacement`。Worker 崩溃后同一 Run 从该检查点继续，**不得**把受理时的原始用户句再当本轮 user。没有检查点（第一轮模型尚未返回）才允许按原句重采样。
4. **副作用**：崩溃时正在执行的工具记 aborted observation，禁止重放；不承诺外部调用 exactly-once。

HITL 挂起与崩溃恢复共用同一检查点结构。`pending_input` 只提供 token/模型/skill 等受理事实，不是当前目标句。当前目标以 GoalContract 与已注入的 steer 为准。恢复段不增加用户可见终态。

已上传并具有持久 `file_id` 的图片，`pending_input.attachments` 只保留引用、哈希、缩略图和附件元数据，不重复保存 `image_url` 的内嵌原图；否则事件游标和状态更新会反复读写整张图。没有持久引用的旧式内嵌图片必须完整保留。Worker 在同一共享图片预处理入口按最终模型选择：原生视觉模型经用户归属校验读取持久原图并仅在调用内存中还原 image URL，纯文本模型继续交给已配置视觉服务代读。此优化不能改变图片清晰度、引入额外视觉调用或吞掉失权/过期失败；持久文件不可读时必须展示附件失败并约束模型不得假装看过。

对齐 Claude Code 三条不等式：

1. **防无限循环 ≠ 任务结束。** 同形空转、同错重试和重复 observation 只记录指标并扩大模型的策略空间；只有用户取消、等待边界或验证失败且无可行替代时才结束。不得因轮次硬顶直接收尾。
2. **预算观测 ≠ 工具缩减。** 不再按预算触发 execution mode、强制 `tool_choice=none` 或删除模型可见工具。工具面只由 ToolSpec、Profile、授权和真实资源范围决定。
3. **上下文爆炸 ≠ Run 结束。** 超窗走 Codex 式 compact turn（`conversation_compact`）：先发 `context.compaction started`，用当前活历史加上压缩提示生成摘要，再把活历史替换为系统提示 + 最近用户句 + `SUMMARY_PREFIX` 摘要；压缩请求自身超窗则丢掉最旧非 system 项重试。线程发送前的 `ensure_compacted` 共用同一提示词与 `replacement_history` 检查点。压缩必须保留目标、用户约束、计划要点、未满足验收条件和工作区线索，然后继续同一 Run。不得因压缩单独 `completed`/`failed`/`partial`。

已有对应能力：对话压缩、Skill 自有工作区 Commit、用户「继续」、Worker 重拉同一 Run 再 Pull。保险丝只准记录事实和恢复空间，不准拦交付。

compaction 在 Provider 请求前按目标模型窗口与 token 估算分段，预留摘要输出和协议空间；不能通过删除未摘要历史修复 overflow。每个源分段只有一个 logical call / physical attempt，保留 originating Root Run 和分段编号；长材料的调用量随实际源分段增长。上一段完整摘要与下一段原文合并，所有分段完成才替换上下文。任一分段失败、取消或终态不完整都保留原始历史与覆盖游标。相同 `history_hash + model + compaction_policy_hash` 的失败在同一 Run 内去重；`live / preflight / background` 分类记账。

### 3.3 Provider 调用总账与 Root 归集

每个真实 Provider 网络请求在发出前建立 physical attempt，并在完成、失败、中断或取消时结束同一 handle。一个 attempt 不得代表多个 HTTP 请求，审计写入失败也不得把消耗记为 0；审计故障 fail-open 保持用户输出，同时发不含正文的 `audit_write_failed`。

总账必须覆盖 `main_loop / plain_answer / public_preamble / research_commentary / compaction_live / compaction_preflight / compaction_background / router / title / memory_extract / memory_summary / paid_search / browser_digest / subagent_model / workflow_node / acceptance / parent_summary / tool_internal`（`subagent_model` / `workflow_node` 只为历史审计行保留枚举，当前代码不再产生）。逻辑调用保存 `logical_call_id / root_run_id / run_id / thread_id / parent_logical_call_id / parent_tool_call_id / call_scope_id / purpose / model / transport`；物理尝试保存独立 `request_id / attempt_index / execution_segment / run_request_sequence / outcome / latency / semantic_payload_hash / wire_payload_hash`。`run_request_sequence` 必须由数据库原子分配，跨恢复与后台调用严格递增；主前缀比较只在相同 `call_scope_id + model + transport` 内选前驱。

usage 必须先绑定到真正返回它的 attempt，再做 Root 汇总。`completed / incomplete / failed` 终态只要携带可信 usage 就先入账；无终态 usage 的断流将 token 留为 `NULL` 并记 `unknown_provider_charge=true`，不得伪造 0。归一化后分别保存 input、output、reasoning、cache read/miss/write；Provider 返回的 `usage_metadata.amount` 和单位按原始字符串保存，不转 float，不由 token 反推「精确金额」。请求审计可保存 reasoning 控制字段，但不得保存模型正文、reasoning 正文、encrypted reasoning 或工具原文。

Root Run 的总用量等于自身、全部嵌套调用（研究团队成员、验收、parent summary） 以及 SSE 终态后 title/memory 的用量 delta 之和；`active → idle → completed`、重试和恢复不得重复归集，终态后调用标记 `post_terminal=true`。对外观测必须把三个指标分开：

1. **Provider cache usage**：供应商原始 cache read/miss/write token；
2. **本地 exact-prefix/LCP**：相邻主循环请求的 item 和规范化字符最长公共前缀，只证明结构稳定性；
3. **Root 总 Provider 用量/原始 amount**：覆盖主循环、辅助调用、后代和 post-terminal 消耗。

`uncached_equivalent = max(input-cache_read, 0) + output` 只是控制面近似指标，不是 Provider 精确费用。首次上线默认 `MODEL_USAGE_ENFORCEMENT_MODE=observe`：只入账与告警，不把预算计数注入 prompt，不自动以 Root token 硬中止，不向现有 SSE 暴露 cache token 或 amount。

---

## 4. Run、Plan 与事件契约

### 4.1 Run API

主对话只保留：

| 接口 | 作用 |
| --- | --- |
| `POST /chat/runs` | 创建 Run；请求使用 `agent_mode: standard | plan | research` |
| `POST /chat/runs/{run_id}/inputs` | 提交用户新消息、澄清答案或计划确认 |
| `GET /chat/runs/{run_id}` | 获取权威 Run/Plan 快照 |
| `GET /chat/runs/{run_id}/events?after={cursor}` | SSE 实时订阅和断线续传 |
| `POST /chat/runs/{run_id}/cancel` | 请求取消当前 Run |

- 协议头统一为 `X-Harness-Protocol-Version: 1`。
- 删除 `task_mode`、`research_mode`、旧 `execution_mode` 和 V1/V2 协商分支。
- 删除 V2 函数命名和“Agent Runtime V2”文案。

### 4.2 Run 状态

Run 至少持久化：

```text
run_id / thread_id / user_id / agent_mode / phase
state_version / goal_revision / plan_version / event_cursor
    active_tool_calls / pending_input / cancel_requested / terminal_reason
    execution_control: {
      segment_index / checkpoint_sequence / recovery_reason /
      last_progress_at / recovery_count
    }
    loop_checkpoint: { messages / goal_revision / plan_version / saved_at }
created_at / updated_at
```

RunPhase 枚举包含规划、用户等待、系统恢复、执行与终态，并非每轮必经的线性链：

```text
planning / plan_ready -> waiting_clarification / waiting_user / waiting_confirmation
用户输入或批准 -> executing
executing -> waiting_system -> 同一 Run 恢复
executing -> completed | partial | failed | cancelled
```

`verifying` 仍在类型及诊断判定中保留，但普通终答的缺口不能把运行锁进该阶段。数据库 `AgentRun.status`、RunState `phase`、`outcome` 与 Job 调度状态不是同一字段：已受理可为 `status=created`，部分交付可保存为 `status=completed, outcome=partial` 并投影 `run.partial`。Job 的 `queued_scoped/leased_scoped` 不进入公开 RunPhase。

所有状态写入使用 CAS；事件只能在状态事务提交后发布。

### 4.3 实时 Plan

`AgentPlan` 与 `AgentPlanStep` 是计划唯一事实源：

- Plan 包含 `run_id`、`goal_revision`、`plan_version`、目标摘要、状态和完整步骤快照。
- Step 包含稳定 ID、顺序、标题、状态、验收条件、证据引用和状态原因。
- Step 状态为 `pending | in_progress | completed | skipped | invalidated`；最多一个 `in_progress`。
- 数组顺序就是执行顺序：第 1 条是第一步，最后一条是收尾。线性计划里唯一 `in_progress` 必须落在最早未完成步；模型把后面的步骤标成进行中时，Harness 拉回光标。显式 `depends_on` 且已 ready 的并行步例外。这不是调度 DAG。
- `update_plan` 是 Harness 控制命令，不是普通工具。模型每次整表回传按新的执行顺序重写清单；被替换的旧步骤在 Store 里标 `invalidated`，任务协作 To-do 与模型上下文不把它们当成还没做的步骤。
- 更新必须携带 `expected_plan_version`，成功提交后才发 `plan.updated`。
- 采用完整快照事件，不采用容易乱序的局部 patch。
- 工具成功不等于任意制作阶段完成：素材、设计、源稿、导出、校验和发布必须匹配对应证据。PPT scratch 工具从实际工程快照生成结构化进度回执；设计文件和页面清单、全部清单页及图片引用、有效且不早于源稿的 PPTX 分别证明设计、源稿和导出，不能用下载图片或写入单页勾选导出。“导出并校验”还须复用既有布局检查的纯数据入口，不能仅凭 ZIP 存在判定通过；这只是快照上的进度投影，不新增工具或视觉模型调用。进度回执不含交付 `file_id`，发布仍以真实持久文件回执为准。检查失败只保留未完成状态，不阻塞工具、发布或正常终答，不推进未绑定的其他步骤。

运行中跟进行为对齐 Codex **排队模式**（桌面截图：回车进队列，点 Steer 才改向）：

- 回车默认 **queue**：话先停在输入框上方的排队卡，本轮结束后作为下一 Run 发出，并继承上一轮 GoalContract、计划、工作区与 Skill，不得当新任务。
- 点排队卡「调整方向」才 **steer**：注入当前 Run，不中断已启动工具。
- 多条排队卡按服务端 `position` 升序发送。左侧独立手柄是唯一拖拽命中区：按住后拖动项必须跟随指针，原位保留低对比占位，相邻行使用简短位移动画连续让位，松手再一次性提交完整 id 序列。持久化失败必须恢复拖动前顺序；正文和右侧操作区不得触发拖拽。队列暂停、Run 已停止或已结束只影响自动派发/「调整方向」，不得禁用排序；键盘聚焦手柄后可用上/下键等价重排。
- ⇧⌘Enter 对本条取反。菜单「关闭排队」后回车改为立刻注入。无 localStorage 时默认排队；已写成 `steer` 的视为个人偏好。
- 停键仍是取消，不恢复第三态 interrupt；停键不因草稿换成发送箭头。

Steer 受理：校验 `expected_run_id` → 递增 `goal_revision` → 补丁 GoalContract（文件任务不得塌成「对话答复」；短约束合并进目标句，换主题则改写目标句但继承交付类型）→ 写入 `agent_run_inputs`。活 executing 不得因此再入队 Job。Harness 在安全点把新目标交给模型；已启动工具跑完，未启动且目标已落后的调用返回 stale。不因插话强制 `update_plan`，不得借由 steer 关闭 bash/发布。`waiting_user` / `waiting_confirmation` 仍走确认卡 resume，不是 steer。前端静默切换到最新计划。

Plan Mode 第一版计划必须等待用户确认。执行中用户主动调整目标时，新消息本身视为授权；只有权限扩大、外部影响或破坏性动作才再次确认。

### 4.4 Event Catalog

事件名、Schema、持久化级别和用户可见性集中定义。首批正典事件：

```text
run.accepted / run.phase.changed
message.delta / message.completed
message.reasoning.delta（瞬时） / message.reasoning.completed（持久化思考正文上限 2000 字 + 秒数）
input.required
plan.updated / plan.confirmation.required
tool.started / tool.completed / tool.failed
message.commentary / approval.required / research.team
artifact.saved / progress.updated
run.completed / run.partial / run.failed / run.cancelled
```

每个事件必须有 `event_id`、`run_id`、`sequence`、`timestamp`、`schema_version` 和结构化 `data`。前端按 `run_id + sequence` 去重；发现游标断层时重新获取快照。

切回正在执行的会话时，历史端点必须用同一批持久事件生成完整的 `execution_trace + event_cursor`。初始用户行在受理时即绑定 `run_id`；对旧数据，若活动 Run 尚无任何持久消息锚点，历史响应在末尾追加一条不落库的临时助手轨迹投影。前端必须用该快照补齐或覆盖活动助手锚点，保留截止游标已发生的全部步骤与 `startedAt`，再从快照游标之后续订 SSE。不得从 `sequence=0` 可见重演，也不得只保留游标尾段而让旧步骤或计时消失。

页面导航、打开新对话窗口或可见订阅断开，只能释放前端观察者，不得调用 Run cancel API。活动 Run 的历史快照中可以合法存在 `running` 工具、Thought、研究团队成员或验证步骤；前端必须保留该状态并用后续事件收尾。只有 Run 已进入 `completed/failed/cancelled/partial` 终态后仍残留的 `running` 步骤，才能归一为历史中断记录。

---

## 5. 三种 Profile

### 5.1 Standard

- 普通问答可以直接回答；任务型请求由模型选择工具并执行。
- 根据用户目标动态启用只读、文件写入、沙箱和外部能力。
- 复杂任务可以维护实时计划，但不强制先展示和确认。

### 5.2 Plan

- 初始阶段只开放读取、搜索、检查和不持久化的临时沙箱。
- 关键条件缺失时集中追问，能从仓库或环境读出的事实不得反问用户。
- 计划形成后进入 `waiting_confirmation`，计划卡展示权威快照。即使用户可见报告已写出、模型未调用 `ask_user_choice`，平台也必须挂起确认卡；未写入 `approved_plan_version` 前不得把 `capability_scope` 升到可写，不得执行计划里的副作用。
- 用户确认后在同一个 Run 中切换到执行阶段，保留对话、文件、证据和计划版本。
- 禁止通过替换 system prompt 或新建另一个执行 Run 完成模式切换。
- 执行过程中计划随观察和用户新目标实时更新。
- 计划正文对齐 Codex `<proposed_plan>`：先写清环境调研与已确认事实，再给决策完备的实施步骤；必须覆盖接口/数据契约、测试与验收，以及必要的假设和默认值。传输标签只用于解析，不进入用户可见计划卡；工具调用在发生时绑定当前 `plan_step_id`，观察先持久化再投影，禁止并发完成时按“当前步骤”猜归属。

### 5.3 Research

本节按当前团队路径整理；旧的全员互审、48/24 次取证预算、报告 180/90 秒硬截止、超时材料盘点交付均已被后续实现替代，仅在 §15 留作历史。约 10 分钟是整体目标，不是报告模型请求的强制终止器。

v1.200 / H2、H5：按用户要求，研究团队按真实工作展示成果，不再对用户使用「部分完成」标签或重复的交接承诺。成员说明只描述已取得的网页来源、已提交或已保存的研究材料；网页按可核对的 URL 去重，结果明细缺失或被截断时不编造精确总数，搜索命中不等于已读正文或核验通过。旧记录在展示层按现有回执解释，不改写历史数据。内部 `partial`、恢复及证据门槛保持原语义；报告只说明影响结论的具体证据缺口，不把成员没提交总结等同于整项研究失败。无可用材料、真实错误与取消仍如实展示。此条覆盖此前 v1.188/v1.197 的 Research 用户可见「部分完成」文案，Research 运行头相应显示「本轮研究已结束」，普通对话终态文案不变。

v1.200 验证：前端团队解析、运行头、布局与一次性 Profile **36 passed**；后端团队、质量、范围交付、来源恢复与后台报告恢复 **61 passed**，定向 ESLint、Python 编译及补丁检查通过。已登录 Chrome 的 `:3200` 真实历史「我想了解deepseek v4.1 flash」显示「小溪：已找到 15 个网页；已保存研究材料」「小望：已找到 25 个网页；已保存研究材料」，桌面及 430 px 设备模拟的详情底部完整可读，报告与来源入口保留。设备模拟不代表真机验收。`tests/parity/` 不存在，未声称通过。Python 仅存盘，运行 API 的启动早于本次修改；历史展示通过不代表 Worker 成稿提示已加载，仍请跑服务的会话重启 `python -u ./run.py` 后验证新研究。

报告合成的阶段约束作为独立的 `turn_constraints` 世界状态字段优先保留，不能与长证据正文一起按前缀截断；同目标报告恢复也须补入当前合成约束。证据正文采用有界预算，来源编号保持台账顺序。空工具面的报告阶段拒绝文本协议和原生响应中的工具调用，并允许一次仅生成报告的纠正；纠正标记随同 Run 检查点保存，重复违约进入明确失败，不能重新开放检索或跨恢复无限重试。任何控制工具（包括 `update_plan`）均须先通过本轮工具表。Responses 续接保留原生 opaque items，并补齐本地恢复调用的结构化调用项；孤儿或重复工具输出不得发往供应商，也不得伪造未记录的调用。完成既有兼容修复后仍被供应商明确拒绝的无效请求（HTTP 400/404/405/415/422，排除上下文超长），以及鉴权、额度或访问拒绝（401/402/403）进入共享失败终态，不原样无限恢复；上下文、网络、读取超时、限流和普通 5xx 的恢复边界不变。阶段未完整交付但已保存材料或可用来源的成员内部保留 partial；用户可见说明按实际网页与材料成果呈现，不能据此声称已完整交付或核验。

研究网络链路继续复用既有搜索、正文抓取与重排管线。普通对话与 Deep Research 共用管理员配置的顺序路由：deepseek_first 为 DeepSeek 优先、自建或第三方服务备用，primary_fallback 为原搜索服务优先、已允许的 DeepSeek 备用；主路成功不固定并行调用备用。搜索供应商独立于回答模型，私有模型部署可以彻底关闭 DeepSeek 官方搜索；新部署不隐式启用外部模型服务，只有明确启用且用户凭据可用才发送请求。HTTP 错误、网络超时或空原生检索结果进入备用链路，Research 的合法空结果也允许按原查询补位。DeepSeek 使用发起人的专属 NewAPI Key，通过 `/responses` 的服务端 `web_search` 执行原生搜索；不得经未开启请求体透传的 `/messages` 转换链路把服务端工具降成客户端 `tool_use`。只接受已完成 `web_search_call` 的 `open_page` / `find_in_page` URL、结构化 sources 或 URL citation，不能从未执行搜索的模型正文猜来源。默认每个 Research Run 全队共享最多 3 次 DeepSeek 外层请求，包括失败调用；缺少共享作用域或额度耗尽时改用备用服务，不绕过额度再次调用 DeepSeek；DeepSeek Responses 忽略 `max_tool_calls`，因此调用范围由 Run 级共享查询预算、`tool_choice=auto`、非思考模式和输出上限共同控制。预算耗尽不终止已有研究。searchProvider 保留为图片检索和非 DeepSeek 服务的兼容选择；文字检索顺序由 providerMode/providerPool 决定。配置内网地址的服务直连，公网服务保留环境代理；SearXNG 不因空结果撤掉 `searxngEngines` 限制，按实例和引擎记录故障冷却，单请求最多探测一个到期引擎。普通模式中的合法空结果不算服务故障；Research 顺序检索在主路故障且备用空结果时保留故障语义。普通网页先经逐跳 IP 固定的轻量读取器，4 秒内未得到有效正文才进入既有 Firecrawl；解压后正文最多 2 MB，验证码/错误页不得覆盖搜索摘要。DeepSeek 返回的候选仍只是搜索摘要，只有经过正文读取成功的来源才标为 `scraped`。研究团队共享相同查询和 URL 的请求，成功缓存 10 分钟、失败 15 秒、最多 128 条；每个成员仍独立检查父 Run 权限并保存工具回执，实际网络调用只记一次供应商用量。单个等待者取消不影响其他成员，最后一个等待者取消或父 Run 结束时收回在途任务，缓存与搜索预算不跨 Run/用户或重启恢复。团队活动区分 `operation`、`errorCode`、`cacheHit` 和 `snippetOnly`；逐 Provider 参与和失败状态只保留在私有回执与审计中，不投影到用户界面。服务故障不投影成“无相关资料”，摘要不等价于已读全文。此变更不建立新的研究工作流，也不改变最终蓝框报告。

研究团队每条搜索活动的结果数量可展开查看该次检索的来源。结果卡随同一 Run 的团队快照保存并回放，只投影 URL、标题与短摘要（最多 30 条）；不发布原始供应商对象或全文。旧记录缺少明细时明确提示，不按关键词猜配其他搜索结果。结果列表及圆形关闭按钮已做组件验证，试验后端加载与新 Run 联验单独验收。

研究检索的有限阅读名额先按候选相关性顺序覆盖不同站点，再分配给同站点其他页面；不因单站点返回大量摘要而占满全部正文读取预算。最终候选截断须优先保留实际读到的正文，不能在重排后用未读摘要挤掉已取得的证据。此顺序只用于 Research，沿用原页数上限与逐跳公开 URL 校验，不增加隐式补读。

搜索配置回退（v1.193）：按用户确认撤回本轮未部署的共享限流网关及配套请求调整，部署继续由 SearXNG 直接提供 `8085`。恢复原配置优先级：非空 `WEB_SEARCH_SEARXNG_URL`、`WEB_SEARCH_SEARXNG_ENGINES`、`WEB_SEARCH_SEARXNG_IMAGE_ENGINES` 覆盖已保存的对应字段；环境值为空时使用后台保存值或缺省值。后台显示值与实际运行值因此可能不同，不能再宣称本地引擎与服务器一致。既有 Run 内请求去重、引擎冷却和有界混合搜索继续使用，不能据此宣称已有跨进程全局限流。回退不修改环境文件、配置数据库或运行中的服务，Python 是否加载需另行确认。

- 研究团队是本次研究的临时成员，不是广场里的智能体。三个蓝球成员分别承担资料研究、分析和交叉核验，主 Agent 统筹；各自独立上下文复用 `drive_model`，只开放研究只读工具。成员独立取证并交换带来源的公开发现，再由主 Agent 集中核对，最终仍由主 Agent 交付现有蓝框报告。公开事件是 `research.team`（allowlist 快照）与既有 `research.progress`；团队角色、公开交流、工具归属和成员检查点持久化于同一 Run；恢复不得覆盖主循环检查点、重复已交付成员或把未完成成员显示为成功。身份按本次团队成员区分，不复用广场应用 ID。前端仅新增头像状态与可展开成员详情，其他样式和报告查看/导出不改。成员失败后保留已有证据并在报告注明缺证；取消继续向上传播。
- 研究团队细化（v1.173）：新团队从名字池无重复抽取三个人名；名字随团队一起持久化，刷新、断点恢复和同用户同会话的停止后继续不得重抽。三个成员按冻结主题并行取证，主 Agent 负责计划、集中核对和成稿；各上下文共用既有 `drive_model`，不作为第四个取证成员；主报告仍由 Research 内核末尾统一合成。`share_research_update` 只在当前 Run 内交换明确公开的消息，检索回执带队友最新消息，不能伪造讨论或暴露私有思维链。`research.team.activity` 按保存顺序保留有界公开记录，角色列表与活动流分开；前端默认最近三条、可展开全过程，完成后收起到详情入口。球体轮廓与五官静止，水纹仅在球身遮罩内扩散；组队依次入场、活动模糊淡入/上移淡出，尊重 reduced-motion。取消必须传播并回收成员，未保存的快照不得当作公开成功。保存只 patch 团队字段，并为主 Run 同时写入事件造成的 CAS 冲突做有界退避。
- 单次取证与蓝框交付（v1.174）：团队已负责并行取证和讨论补证，返回后不得再进入旧 `collect_coverage`。报告整合时 `research_team_synthesis_only` 收起工具与能力发现入口，`drive_model` 同时禁止隐式回取工具；即使模型请求搜索也没有执行器。空工具集仍必须进入 `main_tool_turn` 的主模型分支，保留团队简报、证据注入、引用、Research 标记和现有蓝框报告交付，不得掉入普通回答 fallback。证据不足据实输出部分研究报告，不以新一轮搜索掩盖缺口。只有没有团队记录的兼容路径保留旧覆盖检索。
- 动态研究计划：新研究由主 Agent 通过受限 `drive_model` 上下文制定与用户问题对应的主题，不播固定四主题模板。取证前冻结 topic key、研究范围和成员归属，之后只允许在原主题内细化具体缺口，不得新增研究支线或按模型自报完成绕过证据门槛。计划与团队标记同次 scoped patch 持久化，检索回执投影到既有 `plan.updated`；停止后继续保留已保存范围和计划版本。协调异常只使用真实问题作为降级主题。协调上下文不含搜索工具，团队返回后不能重开取证。
- 研究材料：短公开进展与完整研究材料分别提交，材料按成员及阶段持久化，集中核对和最终成稿消费完整材料，SSE 只投影公开 allowlist。成员只读正文工具复用已有公开 URL/SSRF 校验与抓取服务；主 Agent 可向原成员指定具体补证，不能绕过真实正文覆盖门槛。已保存材料在超时或恢复时保留，进入 synthesizing 后关闭工具。来源保留至 8000 字符，成稿证据正文总预算 64000 字符优先分配给各主题的可读正文；引用保持台账顺序并包含后期来源（最多 80 项），同一来源可归属多个主题。最终报告按实际问题分析，不以固定字数或重复背景冒充深度。取证数量与时间使用下文当前预算，不再沿用旧两轮/48 次/30 分钟设置。
- 发表前核验：团队草稿先缓存在模型事件边界，由同一 `drive_model` 的独立只读、空工具上下文对照真实证据台账核对；只返回接受或带精确原文锚点的修订，不重写整篇报告，不追加检索。草稿、中间核验文本不进入用户消息，核验不覆盖根检查点，结果按草稿/证据哈希与 goal revision 持久化。引用编号、修改位置及目标版本机械校验通过后才投影正文；取消、目标改变、空正文或引用缺失/越界时不发布。模型对证据支持程度的判断仍须真实内容 QA，不能宣称机械证明事实正确。
- Research Profile 的模型 final 与主报告持久化必须保留 `[n]` 编号；普通对话的来源角标清理不得作用于研究报告，否则会导致核验误拒绝及历史记录丢引用。引用核验失败时保留私有诊断并阻止同目标的自动恢复重复成稿。
- 发表前核验必须独立传递用户原话，不能把草稿、证据或协调指令当成长期记忆操作授权。已持久化的引用核验拒绝通过类型化异常交给共享 Run 终态 CAS，进入 `failed` 并发布安全原因，不再作为临时故障循环恢复；取消已生效时不得覆盖。模型超时、网络与持久化故障继续走原恢复路径，不扩大为通用失败规则。

- 报告呈现：Research 已缓冲的终稿全文直接提交到共享消息投影，不再用逐字动画延迟卡片交付；权威 Run 终态优先于前端 loading。蓝框、全屏和下载沿用既有入口。正文使用连续文稿的标题、分节分析、必要对比表格与结论，来源链接由原来源面板提供。旧部分报告的原文堆叠与来源清单只在展示层收起，持久消息和台账不改写；新报告不能用网页摘录、材料统计或未经核验的草稿代替正式结论。
- 当前研究范围与取证预算（`scope.py` / `budget.py` / `quality.py` / `team.py`）：默认 2–3 个主题，超过 3 项须引用用户明确扩大范围的原话，最多 5 项；取证开始后不能新增主题。三成员各自取证，主 Agent 不重复全量搜索；每成员最多 4 次取证，最多一轮两项定向补证、每成员 1 次，全队最多 14 次搜索/读取。取证成员阶段最多 180 秒，提前 40 秒停止新检索并整理材料；计划 30 秒、集中核对 30 秒、补证 45 秒。根 Run 保存 600 秒目标时间并为报告预留 300 秒，恢复和改向不重置取证计时，新 Run 独立计时。提交计划、判断或材料成功即结束对应协调上下文；失败存储不得冒充提交。报告阶段使用下文共享传输与恢复规则，不保留 180/90 秒业务硬截止。报告应回答用户问题，在相关章节说明未核实细节；无法形成并核验正文时保存现场、报告真实错误，不生成材料盘点冒充交付。
- 报告后台连续性：导航离开只关闭页面订阅，后台 Run 继续运行。约 10 分钟仍通过冻结范围、限制取证和预留成稿时间实现；不得因草稿超过 180 秒或核验超过 90 秒取消仍在生成的模型请求。报告沿用共享模型传输的连接/读取超时及有限重连；传输与存储故障进入同一 Run 的 `waiting_system` 和 Worker 恢复，不能归类为引用核验拒绝。已收到完整草稿或核验响应时，在关闭模型生成器之前按目标版本与证据指纹持久化，恢复仅补未完成阶段，不重搜、不重写已经保存的报告。旧 `report_call_timeout` 标记及过期报告阶段时间不能封死仍可恢复的 Run；不批量复活历史终态任务。引用拒绝、用户取消、目标改向和正式消息提交边界保持不变。源码验证不代表服务已重载，也不代表真实模型在 10 分钟内完成。
- 恢复中的输入状态与报告存储（v1.186）：`waiting_system` 是后台自动恢复，前端仍保持运行态、灰色锁定研究开关和停止按钮，并继续按游标订阅；用户发送的后续要求按已有排队/改向语义处理，不能重复创建冲突 Run。只有 `waiting_user`/`waiting_confirmation` 才进入等待用户输入的 UI，不能按 `waiting_` 前缀混用。研究草稿包含的上下文投影须使用既有 `ProjectionLedgerState` 的 JSON 序列化与类型化恢复；禁止直接把运行时对象写入 JSON 列，也不能丢掉投影来规避异常。正式消息持久化后才提交投影的边界不变。
- 原生搜索与零证据恢复（v1.192，配置优先级以 v1.193 为准）：Responses 原生搜索包含服务端检索与生成，不能沿用普通搜索页的 12 秒读取超时；使用独立的 60 秒读取与 75 秒总网络期限，仍受团队取证预算和父 Run 取消约束。混合搜索两路均无结果且任一路故障时，保留故障语义。团队无可用证据时，最多对两个成员的原始查询各补取一次，共用既有权限、请求限额、台账与回执；检查点保存所选查询和阶段，恢复不重新展开研究。合法空结果的备用顺序以本节当前串行主备策略为准，仍受共享调用额度限制。仍无来源则在成稿前区分搜索依赖不可用与证据缺失，通过共享终态 CAS 保存安全原因；不生成无来源草稿再误报引用核验失败，不输出材料盘点，不无限恢复。此项替代旧文中零证据也产出 partial 的规则；已有证据的报告、传输与存储恢复继续原路径。历史投影须保留公开失败原因，旧记录只读补查原 Run；前端以「研究未完成」区别于用户停止，不推断切页导致失败。
- 自动建立研究计划；只有研究边界确实缺失时才追问。
- Web、知识库、连接器和已有用户文件在策略层物理只读。
- 允许在隔离 scratch 中计算和整理，不得同步到“我的文件”。
- 默认输出带来源引用的报告视图。
- 终态以对话 Markdown 渲染的蓝框报告为唯一默认交付；平台不自动生成、保存或追加 Word/HTML 产物卡。蓝框下载菜单可由用户主动复制或导出本地 MD/DOCX/PDF，不写入「我的文件」。
- Research 是一次性 Profile：本轮权威 `run.completed` 或交付型 `run.partial` 后只清理 composer 的下一轮选择，已完成消息仍保留 `agent_mode=research` 以渲染蓝框报告。`partial` 表示核心报告已交付但仍有非关键证据缺口，不是跨轮续跑态；`run.cancelled` / `run.failed` / `waiting_*` 才不自动退出。重新打开已完成或 partial 历史报告也不得反向点亮 composer。已入队 TurnContext 继续使用入队时冻结的 Profile，不在前一 Run 收尾时篡改。
- PPT、Excel 和复杂可编辑产物属于后续 artifact profile，不在 Research 内混合执行。
- 单次 `search_web` 最多并发读取 8 个结果页；这是批量上限，不是整轮来源上限或完成条件。ResearchPolicy 建立最低证据地板（主题独立来源、已读正文和跨站验证）；当前团队由集中核对在冻结范围内判断具体缺口与有限补证，不因计数不足重复铺开搜索，也不能按已搜索次数宣称完成。
- **无团队历史的覆盖检索兼容路径**：只有 Run 尚无团队记录时才可进入 `collect_coverage`，按原主题、查询预算及证据规则续接。新团队、已中断团队和已进入报告阶段的 Run 都不能在团队之后重跑旧覆盖流程。抓取正文、scraped 标记和编号引用共用同一有序台账；所有路径在成稿前都检查可用证据，零证据区分搜索服务不可用与证据缺失，不生成无来源报告或无限恢复。

---

## 6. 工具层

### 6.1 ToolSpec 与 ToolObservation

每个模型可见工具必须声明：

```text
name / input_schema / output_schema / capability
effect_scope: none | scratch | user_files | external | memory
idempotency / retry_policy / timeout / cancellable
resource_locks / parallel_safe / approval_policy / visible_to_user
allowed_profiles / allowed_phases / result_size_policy
```

Policy、并发、审批和重试只能读取 ToolSpec，不得维护工具名集合；未知工具默认拒绝。

所有工具统一返回：

```text
call_id / tool_name / status / summary / structured_data
evidence_refs / artifact_refs / retryable / error_code / result_handle
started_at / completed_at
```

大结果只把短摘要交给模型，完整内容保存为 result handle，按需读取。

### 6.2 工具分层

- **控制面**：更新计划、追问用户、加载 Skill、能力发现、读取大结果、提出记忆候选。
- **核心工具**：`glob`、`read_file`、`write_file`、`edit_file`、`bash`。
- **用户环境工具**：`get_current_time` 与 `get_user_location` 始终以稳定只读 Schema 注册，仅在任务确实需要时调用。`get_user_location` 只采信 ASGI 服务器经受信代理解析后的客户端地址，公网 IP 在活动 Run 中加密保存并于终态清除；调用时配置的定位供应商必然会接收该 IP，但工具只向模型返回国家/省州/城市/时区及准确性说明，不返回原始 IP、经纬度、邮编或精确地址。本地/内网地址、代理未正确转发或定位服务不可用时必须明确返回「不可定位」，禁止以 API/Worker/沙箱出口 IP 冒充用户位置。
- **检索工具**：`search_web`、页面抓取、状态化浏览器、知识库、只读连接器。
- **专业能力**：由 Skill/artifact profile 提供，不把专业规则塞进 `bash` 描述。

### 6.3 唯一通用执行器

- 模型只看到 `bash`，不再看到按语言拆分的旧执行工具。
- 内部代码执行模块改名为 `sandbox_executor.py`，入口改为 `execute_in_sandbox()`，结果类型改为 `SandboxExecutionResult`。
- 沙箱标签、日志、错误、测试夹具和注释全部改成 Harness/Bash 语义。
- Standard/Plan 执行阶段可通过 Artifact Publisher 将合格输出同步到“我的文件”。
- Plan 调查阶段和 Research 的 scratch 输出默认不持久化。
- 下载能力拆成“获取到临时工作区”和“明确保存到我的文件”，Research 只开放前者。
- 第一方 PPTD 运行时预检与实际导出器使用同一可用路径契约：接受 Skill 挂载目录或镜像自带 `/opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm`；两处都不可用才报告缺失。主对话与演示文稿助手继续共用同一执行器、配置和 Run 级隔离，不因专属页面另建沙箱体系。
- 第一方 PPTD 清单须显式写出与页面坐标一致的正数画布 `size: [width, height]`。缺失或非法时导出返回可修正的清单错误，不猜尺寸，也不静默用默认画布裁切内容；工程进度同样不能把缺少画布的清单当成完整源稿。
- 缺少文件交付回执时，终答纠偏不得把含“全部做好”等成功断言的模型原文重新拼回失败提示；中间文件不是已导出、已校验或已发布的证明。
- 第三方 Skill 不得并入第一方 PPTD 预检。通用运行时预检只读取包内声明（`skill.json.runtime` 或根目录 `requirements.txt`），结果缓存于本 Run 沙箱会话；`ready` 后禁止再探测或重装。未挂 Skill、未声明依赖的 `bash` 不预检，避免普通聊天被沙箱准备拖慢。
- 沙箱命令失败必须带稳定 `termination_reason`（`timeout` / `cancelled` / `network_error` / `waiting_stdin` / `resource` / `script`）。超时、取消和网络故障不得伪装成脚本语法错误。
- 联网是阶段权限，不是总开关：仅 `env_prep` 允许访问白名单软件源；处理师生材料时保持默认隔离。OpenSandbox 应用层未接到出网策略时，必须返回 `network_denied`，禁止让模型在断网沙箱里循环安装。
- 长命令作业仍在 `bash` 后面，不新增模型可见执行器。超过本轮工具墙钟时转为可查询作业（状态 / 增量日志 / 取消），不得让 Gateway cancel 打断落库。

### 6.4 Skill 兼容边界

- 第一方 Skill 必须迁移到当前工具词汇和统一工作区。
- 第三方 Skill 导入时检查退休工具名；不兼容时拒绝导入并给出迁移提示。
- 禁止在全局 prompt 中加入旧工具翻译兼容说明。
- composer Skill 保持一次性选择：正常发送后清空，重新生成沿用上一轮；编辑历史用户消息并重发时，则精确恢复被编辑消息自己的 Skill 快照。Skill ID 随用户消息引用元数据持久化；名称只用于旧历史的唯一命中兼容，同名歧义不得猜测。
- Skill 包完整性是一等事实：`complete | incomplete | unavailable`，并记录取包通道 `zip | bytes | text_json`。取包由 `services/skills/skill_package_bridge.py` 在进程内完成（内置包读磁盘、导入包解 zip），不得把损坏二进制挂进沙箱；树里声明了图片、字体、Office 模板但通道拿不到时必须 `incomplete` 并点名未挂载文件。
- 主对话不自动执行不可信 `entrypoint.sh`。依赖以包内声明为准；第一方 ppt-studio 仍可用仓库 overlay 补运行时文件。
- Skill 目录由 agent-api 自持（`services/skills/skill_catalog.py`，表 `agent_skill` / `agent_skill_version`）：内置 `ppt-studio` 为 `source=system`，用户直接编写或 zip 导入的技能只对属主可见，管理员分发后才成为全员可见的系统技能；`@Skill`、`use_skill` 与演示文稿助手都只读这一份目录。

### 6.6 平台内置 Harness 应用

- 演示文稿助手与校园百事通是代码内置页面，分别固定到 AXIOM Agent Harness 的 `presentation` 与 `campus_services` 预设；它们不是第二套 Agent、Profile 或执行循环。agent-api 启动时按 `pc_url` 为三个预设幂等补种 `app_info` 上架记录（`app/main.py::_seed_builtin_app_catalog`），已有记录不覆盖管理员后续的改名、换图标或停用。
- 上架记录 `app_type=external`，`pc_url` 分别为 `/center/chat/ppt`、`/center/chat/campus`、`/center/chat/interview`，打开方式为新窗口；`app_role` / `app_dept` 是 ACL。停用或删除记录后该助手即不可见、不可使用。同一固定路径同时存在多条已启用记录时必须 fail closed（503），不得随机选择一条 ACL。当前没有编辑这些记录的管理页面。
- 智能体广场的能力分类是 `src/views/peopleCenter/agentMarketCapabilities.ts` 里的 8 项正典定义：沟通交互、文档与知识、数据与表格、内容创作、规划与结构、开发与自动化、图像与多媒体、综合/其他。
- 智能体广场只消费 `GET /agent-api/chat/builtin-apps`（`services/chat/builtin_app_access.py`），返回当前用户可见的内置智能体，不注入静态卡片。卡片展示记录实际的应用名称与创建人姓名/头像；`app_icon` 与描述非空时优先使用管理员配置，为空时回退到对应智能体照片和默认描述，不得因回退而新建卡片。广场列表中校园百事通固定第一、演示文稿助手固定第二。主对话欢迎页的智能体卡片区（`RecommendGrid.vue`）从同一份可见列表按固定路径入选，并保持同一顺序。空角色+部门 ACL 表示所有已登录用户可用，否则角色或部门命中任一即授权；不再引入租户维度。前端隐藏不是权限边界，新 Run、继续输入、HITL 恢复、队列写入、历史读取与对话引用都必须从当前上架记录复核授权。
- `create_by` 历史上同时存在用户 ID 与登录名两种写法，广场展示层必须将它们归一到同一 `sys_user.realname/avatar`，不得将原始登录名误当不同的创建人。卡片不展示“外部”类型标签；这只是展示取舍，不得改写管理端的 `app_type=external` 与删除行为。
- 点击入口以新浏览器页面分别打开演示文稿助手 `/center/chat/ppt` 与校园百事通 `/center/chat/campus`，不得在 `/center/chat` 内替换当前主 Agent 页面或上下文。专属页面复用会话式外壳与交互层级，但内部仍调用 AXIOM Agent Harness 的同一受理、Run、Plan、Event、工具和恢复链路。
- 普通主对话、演示文稿助手、校园百事通的会话列表、搜索、分页、草稿和最近会话恢复必须完全分域：普通域只查询 `origin IS NULL`，两个内置应用分别只查询 `origin=presentation` 和 `origin=campus_services`。首次发送才创建对应 origin 的 Thread；已有 origin 标记的会话无需搬迁消息，直接归入对应专属页；不得按标题猜测旧会话，也不得把普通 Thread 原地改成内置应用 Thread。
- 专属页保留对话历史、记忆、任务协作以及各自策略允许的 composer 能力；空态复用主页面的欢迎层级但使用自身文案与形象。演示文稿助手继续隐藏普通主对话的 `@` 提示、欢迎页智能体卡片区、Skill 选择、计划模式和深度研究；校园百事通按已发布快照锁定知识库、模型和工具，同样不向用户开放计划模式与深度研究。校园百事通只开放主对话同源的图片选择、粘贴和拖放：图片必须先经 `/chat/upload` 获得持久 `file_id`，再沿同一 attachment 链进入 Harness。固定模型命中视觉能力时原图直接进入该模型；固定模型为纯文本时，Run 创建后由平台 `multimodal_model` 视觉配置读取持久原图，生成带“由视觉模型识别”来源标记的描述，再把描述交给固定文本模型继续推理。两条路径都必须归属同一个 Run 审计，视觉失败要如实降级；不得只传文件名或空文本冒充看过图片。文档、我的文件、知识库改选、对话引用和远程伪造图片 URL 均不得进入。演示文稿助手的 Run 受理强制 `agent_mode=standard` 且决策路由为 `agent`，客户端或续接不得把该专属页打进 Plan/Research Profile。
- 校园百事通继承主对话同一套 Agent Loop、公开首句、Thought、可见工具步骤、终答与恢复链，不另起执行内核。真实 Tool Registry 只允许 `search_knowledge` 和 `search_web`。模型应先收集已审核知识库和必要的官网证据，再整合回答；不得凭常识补学校专属事实。官网域名是发布必填项：查询在送入搜索后端前增加 `site:` 范围，文字结果、图片结果与抓取页在返回后继续按同一白名单过滤；空白名单必须返回零条官方网页/图片，不得退化成无约束搜索。已审核知识库片段中的原图，以及官网检索正文/配图中与问题相关的图片，可由模型判断是否在终答用 `[图N]` 展示；禁止输出非官方来源配图，禁止编造图片链接。不得为此新增下载、浏览器或沙箱工具。
- 主对话、演示文稿助手和校园百事通必须经过同一实际 `stream_chat` 图片预处理入口；Worker 应用预设时不得清空受理入口已校验的图片附件。能力判断使用本轮最终模型（校园为发布快照固定模型），视觉代读结果及其 `status/note` 一并进入后续附件元数据；识别失败必须同时触发附件提示和模型的如实降级约束。回归测试必须覆盖三个真实入口的原图直传、纯文本模型委托和视觉失败，不能只测试拆分附件或视觉识别辅助函数。`test_builtin_visual_entrypoint.py` 已覆盖这九个组合；这属于入口集成测试，不替代运行服务上的真实上传与模型调用验收。
- **2026-09-03 本机真实验收**：通过已登录的 `:3200` 页面图片选择器上传同一图片并发送，校园 Run `4772851d8fb64b2fb843edfa61610e18`、演示文稿 Run `c19f3c7285d5417da68f6504fe052b43` 均以纯文本 `deepseek-v4-flash` 运行，产生 `document_image_vision` 审计，委托 `deepseek-v4-flash-vision-exp` 分别约 10.4s / 8.6s 返回 HTTP 200。两轮的持久附件和同 Run 的 Provider 历史均含带来源标记的识别描述，最终回答已在页面显示，Run 为 completed。此证据证明本机两个入口的代读连通性，不代表全部图像细节准确、生产部署或所有移动浏览器验收；文稿助手对流苏左右位置有误识别，另按识别质量评估。
- 校园百事通终答以学生快速阅读为目标：先直接回答当前问题，再按需用时间/地点/材料的短清单、办理的编号步骤和必要提醒组织；多问题分开回答，不把所有召回片段堆进正文。简单问题不强套模板，重要条件、截止时间和风险不能为压缩篇幅而省略；缺少学院/校区等必要信息时简洁澄清，不替学生猜测。配图紧跟对应主题，独立成块，不与正文浮动混排；手机端完整显示图片比例并支持点开放大。此差异仅属于校园预设的表达和展示层，不改变共享 Harness、证据来源或检索步骤。
- 校园百事通的仅图片添加菜单在手机和平板上按内容撑开，不继承多级资源选择器的固定面板高度；保留标题、图片操作、关闭按钮、遮罩关闭、Esc 和安全区。主对话与演示文稿助手的完整资源菜单仍使用共享原有面板，不因校园展示调整缩小二级选择器空间。
- 主对话、演示文稿助手和校园百事通的输入框吉祥物统一只在无消息的欢迎态显示；用户发送首条消息后立即隐藏，加载已有会话时也不显示。校园百事通欢迎页的蓝色助手仍保留，不再沿用旧版对话态持续显示的小球特例。
- 主壳和内置应用独立页复用全局 Less 时，通过各自 `<style lang="less">` 中的 `@import` 引入；不要把同一文件同时作为多个非 scoped 的 `<style src>`。后者在 Vue 插件中共用外部文件的描述符，不同样式序号会造成加载/热更新顺序相关的编译失败。保持原有全局样式语义，不用追加 scoped 或关闭报错遮罩规避。
- 受理层拒绝携带 `skill_ids` / `selected_skills` 的 presentation 请求。Worker 每轮都从当前用户的权威、已启用 Skill 目录精确解析 `ppt-studio`，读取受信任的 `SKILL.md` 后才执行；禁止以其他 PPT 类 Skill 兜底。
- 真实 Tool Registry 必须移除 `use_skill`，恢复和 HITL 续接也重建同一边界。`ppt-studio` 未启用、失权、指令读取失败或超时时，必须向用户返回明确的暂不可用错误，禁止静默降级到普通 AXIOM Agent。

#### 6.6.1 定制助手模块边界（H2 / H5）

- 前端 `builtinAssistants/campusServices/`、`builtinAssistants/presentation/` 分别拥有自己的身份定义与 UI 策略；共用注册入口、`BuiltinHarnessRunPage`、`useCenterChat`、消息渲染和上传链路。新增助手须显式注册，不能靠显示名称识别或复制聊天状态机。
- 后端 `chat/builtin_assistants/campus_services/`、`chat/builtin_assistants/presentation/` 分别拥有身份、提示词、请求约束和工具策略；校园的发布配置、域名策略与运行快照同域归档。HTTP 路由和数据库模型仍是平台边界，不随目录整理改名或迁移数据。
- 身份 registry 只聚合身份与目录展示定义，不导入执行策略；独立的 runtime policy 注册入口组合各助手的工具边界与回合约束。首次运行和恢复续接消费同一策略接口，未知助手必须拒绝，普通主对话保持原有能力。注册新助手时同时维护受保护路由与请求枚举，并通过注册完整性测试；只新建空目录不会自动获得权限。
- 模块只声明或限制既有能力，不创建新 Agent Loop，不复制模型调用、视觉代读、SSE、Worker、取消或恢复逻辑。共享工具与图片服务修复仍同时服务三个入口。
- 本次只调整代码组织与策略接线，保留两个助手的公开路径、preset/origin、目录 ACL、历史分域、配置 hash/版本、图片输入输出和固定 Skill 契约；不引入数据库迁移或运行时新开关。迁移期间的旧导入桥接在本版本内清除，不保留两份业务实现。验收须在改前基线上重跑身份、ACL、工具、配置、图片、流式和恢复契约；源码与单测通过不等于本机 Python 进程已重载。

2026-09-03 分支验证记录（非运行验收）：

- `codex/builtin-assistant-modules` 从 `671aade5488368cc9c40ae27dd9ead7d735f84a3` 建立独立工作目录；未搬入功能会话尚未提交的修复。合并前须接入功能会话的最终提交，保留其同期文档增量，并对移动后的校园策略、配置服务及共用编排接线重新检查；不得用分支旧基线覆盖功能会话的新代码。
- 前端身份、校园/文稿契约、入口及 mascot 共 5 组测试 35 项通过；后端注册、请求/API 权限、发布快照、域名、工具边界、文稿 Skill 与官方配图等定向测试 120 项通过。搬迁的配置、快照、域名、知识库适配及校园/文稿策略共 63 个函数或类，与分支基线的 AST 一致；变化仅为所属目录、导入和文档说明。
- 扩展的 Worker、公开进度、文稿产物、附件视觉、回答投影、模型切换与续接回归 93 项通过；`test_bare_control_does_not_emit_recovered_skill_as_visible_work` 的旧源码字符串断言失败，原工作区同样可复现。旧 `test_campus_admin_router.py` 的两个测试因现有 Starlette/httpx 测试客户端不兼容在原工作区及本分支均失败；未修改共享依赖，另以 ASGITransport 验证 6 个非管理员接口均返回 403。
- 本分支未启动或替换运行服务，未合并、未部署；源代码测试不覆盖功能会话的大图 Run 实测。接入双方提交并重启本机 Python 后，仍须对三个入口做真实对话、视觉输入/图片输出和恢复续接联验，才可宣布合并验收完成。

2026-09-03 合并验证记录（更新上述分支阶段状态，非运行验收）：

- 已将 `codex/builtin-assistant-modules` 快进合并到原工作目录的 `wsr`，提交为 `f09f426b484ab1f24e67967ceef0ca8bfd6fc982`。功能会话先暂停编辑，仅对 6 个重叠文件建立可恢复备份再恢复其改动；未提交或暂存其他会话的功能代码，未推送或重启服务。
- 恢复时仅校园验收测试与本规范出现文本冲突：测试保留新策略导入路径和功能会话新增的全部校验；文档保留功能会话 v1.149–v1.158 历史与正文增量，再补入 v1.159 模块边界。校园策略的功能增量恢复到新目录，没有用分支旧实现覆盖图片、首句、流式或学生阅读布局修复。
- 合并前后 40 个非重叠的已修改或未跟踪源码文件哈希一致；校园策略 11 个函数/类、校园验收 9 个测试函数及公开首句生成函数与合并前功能会话的 AST 一致。原编排器功能补丁通过反向应用检查，无未解决冲突或暂存改动。
- 合并后后端组合回归 258 项通过，前端身份/校园/文稿/入口/mascot 5 组 36 项通过，定向 ESLint 与 `git diff --check` 通过。前端扩大回归为 175/177：`planReportCard.test.ts` 与 `MessageList.outputBoundary.test.ts` 各有一个断言仍要求流式函数位于 `useCenterChat.ts` 内，实际实现已由功能会话移到 `harnessProcessStreams.ts`；相关实现与测试文件哈希在本次合并前后均未变，行为测试通过，本次未为旧位置断言改回实现。
- 功能会话随后适配上述两项及 `harnessFrontendContract.test.ts` 的同类 reasoning 位置断言：检查共享 `harnessProcessStreams.ts`，并继续校验发送、续接、回放三路均完整传递 kind 和 reasoning 完成载荷，不恢复等待动画的旧实现。扩大前端回归为 15 组、157/160；剩余成员样式、Research 参数表达式、连接器条件的三个旧断言，在合并提交 `f09f426b4` 源码也不满足，未为本次适配修改相关业务。后端图片、纯附件首句、校园入口与公开研究进度 69 项复核通过；三个修改的前端测试文件 ESLint 与 `git diff --check` 通过。该收尾仅改测试及验收记录，不改变运行逻辑或宣称手机端已完成实测。
- Vite 对注册入口、两个助手模块及 `BuiltinHarnessRunPage.vue` 均返回成功编译的 JavaScript；实际浏览器停在登录页，未绕过认证。当前本机 Python 进程启动时间早于合并后的源码修改时间，运行服务尚未加载本次合并；须由跑服务的会话重启本机 Python 后，再做三个入口的真实对话、看图、输出与恢复验收，不能把源码回归或模块编译视为完整端到端通过。

#### 6.6.2 面试助手（H2 / H3 / H5，本地主流程已联验，扩展验收待补）

- 客户要求：以纯文字一问一答模拟学生求职面试；依据上传简历与目标岗位 JD 动态生成行为题、专业题和压力题，按回答深入追问。每次完整回答保存评分与证据，面试过程中只展示问答，整场结束后再显示综合百分制报告，说明整场做得好的地方和需要提升的地方；支持碎片化练习、暂停续练和结束复盘。
- 重复练习需要变化题目：开场在同一用户最近 30 场中匹配岗位名称或 JD 内容哈希，最多参考 5 场相关面试已经问出的题目，未公开候选题不算已问。参考限 18 题、9000 字，只投影题干、类型、考察点和追问标记，不读取或继承历史答案、分数。优先选择近期未用的开场角度，由模型结合真实材料换项目细节、场景条件或推理任务，语义相近但仅换措辞不视为新题；岗位核心能力可重复考察，不能为了变化编造经历、偏离 JD 或增加难度。平台拦截忽略标点/空白后的原题重出与本场重复题，语义差异仍需模型实测评估，不宣称完全消除近义题。出题策略在开场受理时冻结到本轮私有输入，恢复重试复用；只在开场工具上下文出现，不进入公开快照、评分输入或报告，不增加页面设置、数据库迁移或模型循环。
- 身份固定为 `assistant_preset=interview`、`thread.origin=interview`，受保护独立路由为 `/center/chat/interview`。上架记录与 ACL 同 §6.6（启动时幂等补种，停用即不可用）。新助手通过现有身份与运行策略 registry 显式接入 `standard`，继续同一 Run API、Worker、Harness、模型驱动、上传、SSE、取消和恢复链路。
- 前端复用 `BuiltinHarnessRunPage`、`ChatPage`、`useCenterChat`，新增面试设置、运行状态和整场复盘组件。设置包含简历文件引用、JD 文字或文件引用、目标岗位、求职阶段、题量和压力等级。解析失败或部分解析必须如实展示；不足的材料不能被宣称为完整个性化依据。学生确认的材料与配置在开场冻结版本。
- 面试页面按材料准备、聊天式逐题练习与复盘组织：简历区支持点击或拖放单个文档，JD 可粘贴或上传；题量可选 3/5/8 题或自定义 3–12 题，可选简历补充信息渐进展开。顶部岗位、进度、题号及操作整栏已移除；题目继续显示在真实聊天消息中。提示、跳过、暂停、结束归入输入框旁的单个操作菜单；暂停时保留继续入口，完成后只提供查看面试报告。下载内容与页面同为整场报告，不拼接逐题问答和维度分表，不包含完整简历/JD、账户标识或未公开题库；沿用共享消息和输入框，不增加运行内核或客户端评分。
- 面试首屏只突出简历、岗位、题量与开始操作，求职阶段和压力等级放入“更多设置”。报告仅在面试业务状态为 `completed` 且整场复盘已保存时显示；活动、暂停与未完成状态不显示报告或逐题反馈入口。报告容器采用白底、灰边、黑字和中性图标，页头保留名称、展开及下载。预览为一个整场百分制总分和一段 18px 短评，最多三行；展开正文为 17px，按整场表现组织“做得好的地方”和“需要提升的地方”，不提供题目或评分维度下拉框、不按题号排列。下一步练习和简短评分说明按需展开，报告与下载都直接读取整场 `review`，不把各题评价重新拼接，也不把后续已补清的内容恢复成不足。报告入场、弹窗和折叠使用短时过渡，兼容 prefers-reduced-motion；弹窗支持关闭、遮罩和 Esc。原始问答及评分记录保留，材料解析异常或部分解析确认保持。
- 新面试获得共享 Run 返回的 Thread 身份后，将该身份写入本页 URL，刷新按明确指定的场次续接；无场次参数的面试入口仍显示新练习准备页，不自动打开最近记录。暂停或结束后收起不可作答的输入框，隐藏操作不清理共享草稿；暂停时通过面板操作菜单继续，继续或重答进入活动态后恢复输入。
- 结束与重答领域动作使用独立控制消息，不消费共享回答草稿；请求被拒绝时也不以控制文案覆盖原文。页面按最新要求移除逐题反馈与重答选择器，后台原始评价及既有重答记录保留。简历截长前为学生补充说明预留空间，总材料长度仍受原上限约束，部分解析标记和冻结材料哈希继续对应实际可读文本；既有场次不重新解析或改写冻结材料。
- 学生可见的评价与复盘直接对学生说“你”，用一两句讲清回答中的具体做法和遗漏步骤，避免抽象能力标签、模板表扬及“状态收口”“证据边界”等报告腔。亮点和改进有事实才写，可以留空，不强凑对称栏目，不额外增加题目未问的要求；必要的专业术语和代码正常保留。总结先读整场已答历史，不仅概括最后一题；下一步只给一个能用文字完成的具体练习。过程说明不暴露内部动作、版本与提交字段；不要求录音、口述或开摄像头，不推断中断原因或编造暂停/继续顺序。
- 面试设置和报告内所有折叠项复用主 Agent 的 `PremiumChevron`：收起向右、展开向下，保留同一线条样式、旋转过渡与减少动态效果支持。原生 `details` 隐藏浏览器默认三角，展开状态仍以其 `open` 为准。
- 面试形象直接复用主 Agent 的球体路径、渐变色值及双眼几何，仅在下半球覆盖黑色西装、白衬衫与蓝领带；生成素材只提供服装，不能替换母版球体颜色或轮廓。静态入口与欢迎页共用同一形象，动态底图不含固定眼睛，双眼及交互继续由 `WorkAgentMascot` 渲染。形象是展示层配置，不改变运行、模型、权限或会话身份；问答开始后继续遵守现有隐藏规则。
- 一场面试归属于一个 Thread；每次回答与下一问仍使用正常 Harness Run。问出题目后 `run.completed` 只代表本轮输出结束，面试业务状态仍可为待回答。不得将整场题序塞入 Plan、长期记忆或长驻 `waiting_user` Run。
- 面试业务 MySQL 持久化会话、候选题库、当前题、答题记录、逐答评价与复盘；通过 `thread_id/user_id` 绑定现有业务会话，通过 `run_id/answer_message_id` 绑定真实回答。`question_id + expected_version` 校验提交目标，同一 Run 的提交必须幂等；记录提交后才发布展示。Runtime PG 与模型上下文只保存必要关联或有界投影，不能成为另一份题序/评分事实源。
- 模型通过统一 ToolSpec/Gateway 中的面试领域工具读取状态、提交结构化题目与评价；工具负责范围、归属、当前题、版本、证据引用和评分格式校验，不自行发模型请求。首轮题库须覆盖行为、专业、压力三类并绑定简历/JD 来源；每次只有一个待答问题，追问关联主问题。停止、恢复、跳题、重答与提前结束保持同一会话事实；重答追加记录并标记接受过反馈，不能覆盖首答。
- 新三维评价直接采用 0–100 整数分，保存 score_scale=100；区间说明用于区分具体表现，不先打五分再换算。旧评价缺少尺度标记时按五分制只读归一化，原记录保持不变，无数据库迁移。服务端先合并同一主问题与其追问的有效维度分，再按主问题等权汇总；专业匹配 50%、逻辑思维 30%、文字表达 20% 形成综合分，采用四舍五入。公共快照携带 score_scale=100、score_summary 与 performance，界面及下载使用同一服务端汇总，不从当前选中的单题推导整场分数。首答与提示/反馈后的辅助作答分别统计。未作答、本题未考察、证据不足不计零分，三个维度均有有效评分才生成综合分；覆盖范围始终显示实际已答题数。评价必须引用真实回答片段或消息。压力题只评价受到质疑时的回应与论证，允许降低强度或跳过；不得以辱骂、无关隐私、回复速度或身份属性推断心理抗压、人格或录用概率。
- 评分先核对回答已表达的语义，不能把已认可观点再列为缺失，不能把可选实现当唯一标准。专业维度须有本轮可分析的技术解释、方法或推理；只有主观感受、未测试或缺记录声明时，专业维度为证据不足，可在反馈指出未回答的方法，不能据此猜测能力分数。技术主张明确错误时仍可引用原文评低分；三维独立判断。
- 请求提示使用领域动作 `hint`，仍经同一 Harness 的读取、提交工具。提示回执不评分、不推进题目；成功保存提示后该题的后续回答标记为辅助作答。按钮与有限的独立文字快捷指令共用冻结题号/版本的载荷；仅完整匹配明确控制语句，回答或材料中提到“提示/跳过”等词不自动触发控制。旧标签页提交被拒后保留草稿、同步权威题目，并要求核对当前题后再发送，不将冲突写成临时网络故障。
- 简历、答案与评分仅在本人会话范围读取；应用配置权不自动授予学生记录读取权。面试策略只开放 `get_interview_session` 与 `commit_interview_turn`，首次和恢复使用同一边界。未公开的候选题和参考答案不进入面试公共快照；模型草稿、推理及领域工具参数不投影到学生消息或执行卡，最终通过同一 SSE 输出已提交的业务回执。简历与作答不自动抽取到主对话长期记忆；学生主动查看提示后的表现须注明辅助。
- 公共 Run 事件订阅必须在发送 SSE 响应头前完成 Run 归属和当前 Thread/应用权限检查；跨用户返回 404，已撤销应用权限返回 403，不能先返回 200 再在生成器中抛出权限异常。通过检查后仍使用共享订阅与原事件游标，不能创建助手私有事件流。
- 已使用 `project_answer` 投影已提交回执的领域策略跳过通用 preamble，避免把学生回答误作助手即将执行的任务；仍通过共享 Harness 发布结果。重答题号按主问题首次出题顺序计算，不能使用已答总题数替代。
- 面试读取工具按已冻结 `input.action` 返回本轮所需的 `action_contract`；暂停、继续、重答只返回操作身份、版本及 `commit_template`，不重复携带材料、评分或复盘。回答和跳题状态附带最多三条未答主问题摘要；模型用 `next_question_id` 引用已保存或本次提交的候选题，新追问仍提交完整题目。ID 取题继续校验范围、版本、已答状态与唯一下一问，不能改写原题。工具 Schema 在 Run 内保持稳定，评分证据标准、统一 Gateway、模型传输和终态规则不变。
- 验收须覆盖完整材料上传、三类个性化出题、连续追问、逐答证据评分、暂停/刷新/续练、跳题、重答、提前结束、重复提交与过期版本、跨用户/跨应用拒绝及主对话/校园/文稿回归。源码测试与登录 `:3200` 的真实模型多轮验收分别记录；代码会话仅存盘，由跑服务的会话重启 `run.py` 后再核对运行效果。
- 2026-09-03 源码验证：后端领域/接线/既有入口边界 154 项及迁移 24 项通过，前端注册/设置/会话同步 16 项通过；定向 ESLint、Python 编译、迁移离线 SQL 与补丁空白检查通过。真实组件的三档布局和 8 张截图使用合成数据；正式入口仍停在登录页，应用记录尚未配置。现有 MySQL 面试表已通过迁移接管的只读结构校验，但迁移版本仍为上一版，后端也早于最后修改。详见 `qa/interview-assistant/验收记录.md`；以上不代表真实模型、上传和恢复联验完成。
- 2026-09-04 凌晨运行记录（历史）：用户明确授权本会话自行重启后端。操作前旧 API 已为 503 且 Worker 不在运行；受控停止并启动最新 `run.py` 后，API 与 Worker 均因 MySQL/PG 连接 `errno 49` 失败退出，`:8000` 未恢复，原 Vite `:3200` 保持运行。当时数据库路由经无 IP 地址的 `utun4`，无法实时查询迁移版本与应用目录。
- 2026-09-04 09:50 运行更新（历史）：VPN 已获得地址，进一步定位自动路由脚本用 `ipconfig` 读不到 utun IPv4，改为 `ifconfig` 读取并经管理员授权安装，保留原定时修复机制。MySQL/PG 实际直连查询通过，MySQL 正式迁移至 `mysql_0012_interview_sessions` head，Runtime PG 为 `runtime_0019_prompt_history`。另一个服务会话在重启间隙拉起中转 API/Worker，占用端口；本会话标准 `run.py` 初始化通过但绑定失败后退出，当时保留健康中转进程。应用目录当时无面试入口，正式权限配置与登录后的模型联验未完成。
- 2026-09-04 已登录联验：管理员已登记正式入口，旧同名推荐兜底已移除，首页仅一个面试入口；标准 `run.py` 与 Worker 已直接连接学校数据库，10:46 最新重启后健康。合成材料实际上传、三类问题、追问、逐答反馈、运行中刷新、暂停续练、提示、降低强度、重答和跳题结束已完成；16 个 Run 最终完成，复盘刷新恢复。首答哈希在重答前后相同，过期答案未受理，后续公开事件无工具参数或通用首句。增量后端 63 项、前端会话 12 项与推荐 4 项通过。真实页面为 1280 和 423 宽度，不能替代未完成的正式 390/768 与真机键盘检查；评分对照样本和第二身份权限负向用例另列于 `qa/interview-assistant/验收记录.md`。
- 2026-09-04 增量：正确技术回答语义复验通过；证据不足样本发现专业分依据不足，规则已收紧并单列复验。通过真实受保护页面的同源 iframe 完成 390/768/1280 响应式检查，修复历史遮挡、长输入区底部空间与折叠侧栏残留；属于浏览器模拟，不代表真机。面试快照增加对共享 Run 完成标识的观察，覆盖 loading 已空闲的恢复路径，前端会话 13 项通过。简历板恢复且静态 Logo 与欢迎形象同步，球体母版和下移西装保留。第二身份权限联验与共享历史时长异常另列，不以已有单身份主流程代替。
- 2026-09-04 12:34 收尾：证据不足独立首答复验通过，专业维度为空且页面显示原因，未编造性能结果；验收矩阵为 17 项通过、1 项等待第二身份。共享 Runtime 创建时间的 UTC 回放修复后，原异常历史页已显示实际 4 分 26 秒；新增及既有历史投影回归 33 项通过。API/Worker 重载后健康，Vite 保持运行；第二身份权限负向用例仍待真实登录验证。

### 6.7 移动端契约

> 皮肤系统（可移植皮肤包、`scope=main_chat/sub_agent`、运行时外观接口）已于 2026-09-19 整体删除（`mysql_0023_drop_skins`），原本节的皮肤契约作废；下面只保留仍然生效的移动端行为。

- **移动端行为优先于装饰**：输入框、发送/停止、历史入口、消息滚动和 HITL 操作必须始终可触达；装饰图层必须 `pointer-events:none`，不得制造横向滚动。窄屏可以缩放或隐藏非关键信息，但不得隐藏品牌身份、对话内容或运行状态。
- **点选历史即收抽屉**：手机与 iPad（`max-width: 1024px`）点选会话历史项后，主对话与系统内置 Harness 页都必须立即按 0.24s 曲线滑出侧栏并配合 0.18s 遮罩渐变收起，同时加载该会话；不得等消息拉取完成才关。置顶、重命名、删除不关抽屉。桌面主对话仍等加载成功后再关。
- 手机与 iPad 的主对话和系统内置 Harness 页共用同一紧凑对话顶栏与底部输入区；空对话的输入框不得卡在欢迎文案下方。

### 6.8 工具结果投影与恢复一致性

Runtime 的 `created_at` 由数据库返回无时区值时，历史轨迹和 SSE 回放按 UTC 解释；不能依赖服务进程所在时区，尤其不能让缺少 `run.started` 的恢复场次凭空增加 8 小时。事件保存的 `_event_timestamp` 毫秒值仍优先，MySQL 本地创建时间兜底保持单独处理；该展示修复不改变 Run 终态、租约或数据库历史。

`ResultSizePolicy` 允许每工具声明可选正数 `inline_token_limit`，未配置时继续使用 `inline_chars=8000` 的兼容行为。工具作者、Skill/插件和管理员同时配置 token 上限时取最严格的正数，审批策略仍独立计算，不得借大小策略改写权限。

`ToolResultProjector` 是模型可见工具结果的唯一投影器，接入点固定在工具 canonical 校验完成之后、`role=tool` / `function_call_output` 生成之前，并且在 checkpoint/history 持久化之前完成。禁止 Connector、Gateway 和 Provider adapter 各自做 8,000/16,000 字符硬切；Gateway 只保存合法 envelope、摘要和 result handle，不得截断 JSON。

每次工具调用开始时冻结 `AppliedToolResultPolicy`，历史项与工具回执必须一起持久化 applied char/token limit、raw/projected chars/tokens、是否截断、projected hash 和 result handle。恢复、HITL resume 或 Worker 重启直接复用已投影文本与当时策略，不得按新默认值重新截断旧 item。token-aware 投影为 JSON/envelope 和安全尾部保留 20% 序列化空间；提示注入边界、验收反馈、partial failure、artifact validity 与取回提示不得被正文截断吞掉。

只有原始结果先成功写入 durable Result Store，才能向模型声明「完整结果已保存」。存储失败、`persist_full_result=false` 或单条 UTF-8 正文超过 2 MiB 时必须返回 `full_available=false`，不生成可取回声明；生成文件继续遵循 artifact/file 的 durable contract，不复制进通用结果表。`ToolObservation.result_handle` 是取回事实源，`call_id` 只是兼容解析入口；`fetch_tool_result` 必须按 handle 分页，每个分页窗口重新包裹外部不可信数据边界，并受已冻结 applied policy 限制，不得递归产生新 result handle。

---

## 7. Context Compiler

Context 分为事实编译与持久投影两层，禁止把“每轮重读事实”实现成“每轮重排全部 Provider 消息”：

1. **事实编译层**：每个模型回合从 Run Store、Plan Store、工具回执和活沙箱重新计算当前 Profile、阶段、权限、目标、计划、完成事实与工作区清单。计划以 Plan Store 为准；工作区按活沙箱重读；工具原始大输出只保留有界投影或 handle。
2. **模型投影层**：同一 `context_epoch` 内保持不可变系统规则、稳定工具 Schema 和 append-only 消息账本。首次追加 `harness_context_state/full`，后续只有事实发生变化时才在尾部追加 `merge_patch`；无变化不得生成状态消息。状态事件按出现顺序应用，最新 full/patch 是权威事实，旧状态不得覆盖后续 patch。

系统上下文必须显式拆成：

- **`StableBasePrompt`**：只包含固定规则、权限、安全和 full/patch 解释契约。memory、selected skills、知识库、工作区、计划、日期和时区等事实不得拼进稳定 base。
- **`ThreadWorldState`**：携带上述动态事实，首次以 full 投影，变化时只在尾部追加 merge patch。时间事实只保留配置时区下的 `current_date` 和 timezone，日期跨日通过 patch 表达。

正常 epoch 的硬不变量：前一轮 Provider-visible `input/messages` 必须是后一轮的精确前缀。`round`、累计 input/output token、墙钟耗时、request sequence 和分钟时间等每轮计数不得进入模型上下文。精确时刻只能在任务确实需要时调用始终稳定注册的只读 `get_current_time`；当地天气、附近服务或「我在哪」等问题可按需调用同样稳定注册的只读 `get_user_location`。两者均只在工具调用后返回当时事实，不把分钟时间、原始 IP 或位置结果主动注入每个请求的稳定上下文。

状态 section 固定为 `runtime / goal / plan / completion_verification / recent_observations / workspace`，memory、connectors、selected skills、知识库、日期与时区作为类型化 `ThreadWorldState` 的有界动态事实投影。除字段上限外，`world_state` 紧凑 JSON 还必须共享 64,000 字符总上限，按确定性优先级保留日期/时区与本轮直接上下文，不得让多个合法大字段相乘成无界 prompt。`plan` 不投影 `updated_at` 等非语义字段；数组变化按整体替换处理；所有状态 JSON 使用确定性 key 顺序和紧凑序列化。

持久化 `ThreadContextProjectionLedger` 按 `thread_id + model + transport` 保存 context epoch、源消息游标/hash、base prompt/tool schema/state hash 与已投影 Provider items。另以 `display_history_count/hash` 绑定已落 MySQL 的公开 user/assistant transcript；用户消息还纳入 `attachments_json` 中 file id、sha256、文件名、类型、状态和引用身份，缩略图/base64 等展示字节不参与指纹。公开历史与 Provider 历史形状不同，后者还含 tool call/result、reasoning cursor 和 context events，但只有前者指纹未变时才允许恢复这些隐藏 items。只有源消息是上次历史的严格追加，且 model、transport、base prompt、tool schema 均未变时，新 Run 才能复用旧投影的精确前缀。edit、regenerate、history replacement 或 compaction 必须从权威公开历史重建；base prompt/tool schema/transport 变化必须显式开新 epoch，但公开指纹仍匹配时可保留 canonical user/assistant/tool 边界并替换新稳定 base。model 切换必须新建目标 model 的 row，并从公开历史安全重建；不得把某一模型的 opaque reasoning cursor 跨模型重放。Responses→Chat 时剥除 Chat 不可重放的 opaque item，保留公开正文、tool calls 与 tool results；Responses 无状态回放固定 `store=false`，message 的服务端 item id 必须移除，仅 replay 带 `encrypted_content` 的 reasoning cursor。工具主循环、plain/direct answer 与 HITL/Worker resume 必须调用同一 ledger/store，不得各自重建 user/assistant-only 历史。Provider 成功返回的 assistant/tool items 只在工具检查点或权威 assistant 行提交后进入 ledger；failed/incomplete/cancelled attempt 不得推进游标。Provider-visible 工具 Schema 按稳定名称排序并计算 hash；Capability Broker 合法扩展一次后即冻结。

三个观测口径必须独立：Provider usage 中 cache read/miss/write token 是供应商缓存事实；相邻主请求的 item/规范化字符 LCP 是结构诊断；Root Run 的全 purpose/后代/post-terminal usage 与原始 amount 才是总消耗口径。高 cache-read 不代表 Root 总费用低，exact-prefix 也不代表 Provider 必然命中；三者不得互相代替。usage 与精确 attempt audit 一对一关联，日志只记录标识、hash、长度和 token/amount 元数据，不记录用户正文。

DeepSeek 依然是无状态 Responses 边界：即使持久 ledger 开启，Provider 也必须收到完整逻辑输入，并显式保持 `stateful_responses_continuation=false`。DeepSeek payload 禁止发送 `previous_response_id`、`conversation`、`prompt_cache_key` 或为稳定前缀人工生成的 item ID；未来其他 Provider 的有状态续接必须由独立 capability 显式开启，不得仅因 `supports_responses=true` 自动启用。

`THREAD_PROJECTION_MODE=on` 表示请求的最终目标，不是绕过门禁的开关。Runtime 初始必须是 `shadow`：只计算跨 Run 候选投影、LCP 与 reset reason，不改变真实 Provider payload。连续至少 100 对跨 Run 无对齐错误后，才按 thread hash 开 10% canary；canary 再连续完成至少 100 个 Root Run 才自动进入 `on`。对齐/CAS/canary 错误会清零对应连续干净窗口；`alignment_error_count / expected_reset_count / unexpected_reset_count / canary_error_count` 是累计审计指标，不得以「历史上曾错一次」将 cohort 永久锁死。迁移和源码就绪不等于当前运行进程已加载；真实进程、样本数和 Provider 行为仍需部署后验收。

其它规则不变：事实、推测和风险分开表达；Context Compiler 不承载工具策略和完成规则；压缩不得丢弃尚未进入摘要的用户约束、计划、审批和证据；工作区不把素材二进制、图片像素或工程源文件塞进对话历史。空目录必须明说，计划 `completed` 不能当文件还在。

**压缩过程（对齐 Codex TUI / `compact.rs`）**：

1. 触发线仍是模型窗口 − 预留（封顶约 92%）。到达后在下一次采样前做 compact，不另起用户可见 Run。
2. compact 是真实模型回合：历史 + Codex `SUMMARIZATION_PROMPT`；成功后 `replace_compacted_history`。它必须优先读取当前 Run 冻结的 `model_transport`，与主采样使用同一 Responses/Chat 协议，不得另外硬编码 `/chat/completions`。
3. 前端必须先渲染过程（shimmer `Compacting context` + 耗时），再渲染完成态 `Context compacted`。禁止只有完成后的 chip、禁止无过程的闪断。
4. 失败降级继续本轮（可能仍超窗，下一轮再压），不得把压缩失败当成任务失败。
5. 单条长材料必须完整经过分段摘要，不得先按 8,000 字符切头却推进整条消息的覆盖范围。已覆盖用户原话的额外节选按 token 保留首尾；摘要本身不得再按字符截尾。摘要明确保留尾部约束、修订、未完成事项、来源与精确标识。摘要仍可能遗漏语义，原始 Transcript 始终保留。
6. 切小窗口后分段输入、输出及替换历史均按新模型估算。只有与读取时相同的摘要覆盖基线才能写回；禁止把旧摘要拼到另一 Worker 的新覆盖水位上。无可用压缩时保留未摘要原文，不以应急裁剪伪装上下文完整。

### 7.1 会话内模型切换（对齐 Codex Thread settings）

1. 模型选择是 Thread 的持久设置，表示“下一次新 Run 使用的模型”；本机缓存只作为新对话默认值，不能覆盖已打开 Thread 的设置。
2. Run 受理时把解析后的模型写入 `AgentRun.model` 并冻结。活动 Run 中修改 Thread 设置不得改变该 Run；HITL resume 仍沿用原 Run 模型。
3. 队列项在入队时冻结模型。排队期间切模型不改写旧队列项；队列派发也不得把旧快照反向覆盖 Thread 的下一轮设置。
4. `run.started`、活动 Run 查询与前端本地快照都携带 Run 模型。运行中选择器展示下一轮模型；二者不同时同时显示“本轮 A / 下一轮 B”，不得让用户误以为当前生成已换模型。
5. 新 Run 若与上一 Run 模型不同，Context Compiler 注入一次有界的模型切换说明，要求继续同一会话事实、目标、计划和工具契约；不得伪造不存在的供应商专属指令。
6. 切换到更小窗口时，下一 Run 在首次采样前按新模型窗口重新估算并触发 compact；`direct_answer` 也必须走该预检。正常路径不得静默丢弃未摘要历史。

### 7.2 主 Agent 统一流式节奏

1. SSE 维护网络侧累计全文，页面只消费共享节奏器提交的视觉全文；不得把网络分片大小直接暴露成跳字节奏。
2. 主对话与三个内置助手页的最终正文必须复用同一个节奏器和参数，禁止分别维护打字机实现。
3. 节奏器使用 `requestAnimationFrame` 按 60fps 视觉帧合并突发分片，并根据真实帧间隔和待显示积压自适应提速；普通流式正文的终态尾段在有界时间内平滑排空。Research 已核验终稿属于结构化报告，按 §5.3 在终态到达时直接展示完整文稿；面试领域报告同样消费持久化快照，不对报告再做逐字播放。
4. 后端终态全文始终是权威事实。缩短、改写前缀等语义纠正立即落屏；后台页签或 `prefers-reduced-motion` 下直接同步到权威全文；停止、断流恢复、插话切段不得丢字、重复或从头重播。
5. 自动滚动跟随视觉全文增长，而不是网络全文一次性增长；滚动目标与手势监听必须解析当前布局实际承载滚动的容器，不得硬编码主对话 `.workspace` 后漏掉校园/文稿等内置应用外壳。手机固定输入区的底部空间只预留一次，保留上滑阅读锁及回到底部恢复语义。“送回主任务”、落库和 TTS 仍使用权威终态全文，不能误用尚未排空的视觉子串。
6. 动画不得成为事件流的前置条件：已排队的 `requestAnimationFrame` 若不回调，必须通过有界计时兜底继续绘制；切入后台时，即使没有新 token，也要同步已收到的权威文本并释放等待。reasoning 累计全文与完成事实直接写入事件投影，展开的思考组件自行平滑显示，收起时不排队绘制；SSE 不得等待思考动画或额外绘制帧后才消费正文和终态。commentary 的可选视觉追赶同样不能阻塞后续事件，停止观察时仍须保全已收到的全文。正常正文流式速度不变，三个入口共用同一机制；不得为此伪造进度、模型内容或完成状态。

**2026-09-03 本机验收**：在已登录的 `:3200` 页面以 390×844 视口实际上传图片，Run `a419f482078f40d09e538f1b5af4cd49` 的首句在连续采样中于约 9s 出现；同会话追问 Run `742fe7eb379a47c3bd3d3a106b05091f` 约 3.3s 出现首句，文字增长时真实外壳的 `scrollTop` 同步变化，终答底部约 574px、输入框顶部约 728px，两个 Run 均 completed。1440×900 重新打开同会话后，历史正文正常显示，无新控制台错误或编译遮罩。独立故障页全程暂停动画帧回调，复用共享绘制器，首字约 122ms、开场/思考/终答全链约 2.55s 完成；这些是受控故障数据，不是模型响应速度承诺。

定向检查为 18 套 / 159 项，其中 156 项通过；`harnessFrontendContract.test.ts` 的 3 项旧断言（成员样式、Research Profile 的 `agent_mode` 写法、连接器条件）在 HEAD 源码也不满足，未为本次显示修复修改那些无关逻辑。定向 ESLint 与 `git diff --check` 通过。上述不等于真实 iPhone/Safari 验收，也不验收模型描图准确率；本次看图测试还观察到 public preamble 在视觉结果返回前猜测颜色、终答再纠正的独立内容质量问题，本轮未改该后端链路。

**v1.155 增量验收（2026-09-03）**：真实已登录的同一 `:3200` 页面，390×844 视口发送简短能力介绍问题，约 0.28s 的首个采样中吉祥物已隐藏，3.94s 首句出现、10.65s 正文出现、12.48s 完整正文与完成态可见；只有一份终答，思考全文保持折叠且可展开。对应 Run `ec740c94a3184e45b1af07ab11d24d82`，服务端首句、首段正文、终态分别在受理后约 2.67s / 9.27s / 10.24s；客户端计时从点击发送开始，不能直接当成服务端耗时。1280px 桌面同问题 Run `a8ed2cfff85e402281e8eee3f1cf8f49` 同样完成且无小球残留。前端 16 套 121 项回归通过，包括完全不提供绘制帧时立即消费长 reasoning 后的正文与终态；后端附件/恢复定向 49 项通过。v1.156 的大图引用优化尚待本机 Python 服务重启与真实上传计时，不能用单测替代加载证明，也不能将纯文字耗时外推到看图或真实 iPhone。

---

## 8. Memory Controller

长期记忆采用受控自动写入：

1. 模型只能提出 memory candidate，不能直接写数据库。
2. Harness 按稳定性、敏感性、用户意图、来源和重复性审核。
3. 只保存稳定偏好、长期项目事实和用户明确要求记住的内容。
4. 临时任务状态、敏感数据、未验证推测和工具错误不得自动写入。
5. 记忆保存来源、原因、时间、有效期和最后使用时间。
6. 写入、更新和删除经过 Gateway、幂等与审计。
7. 用户可以查看和删除长期记忆。
8. 记忆检索按用户隔离、相关性和 token 预算注入，不能把全量画像塞进每轮上下文。

v1.170 写入与召回约束：

- 并发预取逐项接收结果；目录、历史或个性化超时不能清空已完成的记忆召回。未完成任务取消并清理，目录保留原有短时缓存重试。
- 模型候选须提供可在本轮用户原话中逐字核对的 `source_quote`。自动提取只读用户输入及已应用的运行补充指令，助手回答和工具产物不能自证长期偏好；模型终态不完整不得提交候选。
- 新记忆保存来源 Thread、可验证的消息 ID、Run ID、原话；补充指令另带运行输入 ID，不冒用起始消息。无法读取消息记录时只用调用链独立提供的用户原话，不编造消息 ID。旧记忆不追填猜测来源。
- 自动替代只匹配明确的 `identity={subject,scope,attribute}`，相似文本不能证明矛盾；缺少身份的旧条目仅精确去重。PostgreSQL 按用户事务锁串行写入；已知来源消息较旧的延迟提取不得替代新来源。同一身份仍需模型正确识别，不把该机制视为语义等价证明。
- 敏感过滤检查具体凭据、联系方式、证件和个人敏感属性，同时检查来源元数据；普通健康、政治、财务教学主题不能仅因关键词被拒。召回也过滤遗留敏感条目，不批量删除历史记录；规则过滤不能替代所有敏感信息的人工判断。
- 召回候选覆盖现有 200 条容量；仅全局或未标范围的历史偏好常驻，课程/班级偏好经相关性或会话通道召回。向量不可用时按关键词相关性降级，不盲取最近事实。提示词最多约 2,400 个估算 token，按完整条目纳入，不截断单条约束。

---

## 9. 权限、证据与完成真相

### 9.1 权限与证据

- 权限由服务端身份、Profile、Run 阶段和 ToolSpec 共同决定。
- 只读必须从 Tool Registry 物理移除写能力，不能只靠 prompt。
- 外部写入、破坏性命令、权限扩大和不可逆操作必须确认。
- 审批请求只对当前待执行动作有效。模型收到 `needs_approval` 后若改走后续非控制工具批次，表示它已选择替代路径；Harness 必须在新批次执行前使旧 pending approval 失效并从待展示队列移除。只有最后仍未被替代的请求才能发 `approval.required`；已安全完成的 Run 不得因过期审批进入 `waiting_confirmation`。
- Guard 拒绝可以作为模型 observation，但不显示成用户可见工具失败。
- 文件交付以持久化成功、文件 ID、大小、哈希和格式检查为准。
- PPT 的生成栈由实际 Skill 决定：显式选择第一方 `ppt-studio` 时使用 PPTD；显式或模型通过 `use_skill` 选择第三方 Skill 时使用其自身栈，禁止静默替换为默认 Skill 或注入 PPTD 专属脚本。
  平台只保留 PPTX 可打开、页数/必要文件一致和明显结构错误等客观检查；模型自看页面可选，视觉评分或审美门禁不得阻塞发布。交付是否包含源工程由实际 Skill 与用户目标决定，不能把 ZIP 作为全局完成条件。
- 用户要求真实、比赛、赛事或现场照片时，PPT 发布必须以可追溯的搜索/公开来源、素材字节哈希和
  页面实质引用为证据；仅有 `.jpg/.png` 扩展名、Bash 自制插画或无来源图片不得满足照片要求。
- 搜索保留来源 URL、标题、抓取时间和引用对应关系。
- 外部业务操作以业务系统回执为准；命令退出码为零不等于产物交付成功。

### 9.2 收工与结构化终态

当前调用链为 `completion.py::verify_run_completion` → `chat/turn_finalizer.py` → Run 终态 CAS：

- 模型请求工具：执行并把回执送回，继续采样。
- 普通回合模型只给出助手回复、不再请求工具时结束；缺少计划或文件证据等诊断性 `resolution=continue` 不回灌 Loop，不把执行阶段锁成 `verifying`。
- 结构化 pending input/审批进入用户等待，不能用问号或「还是/请确认/要不要」等词猜测等待。
- 尚未解除的可重试依赖故障可以返回 `waiting_system`；收尾层抛出 `CompletionWaitingSystem`，由共享 Worker 保存现场、释放租约并安排恢复，不强制完成，也不只改状态而遗留租约。
- 有来源的失败或部分交付事实、Research 覆盖缺口与已通过报告核验，仍由共享判定处理；用户取消优先。普通工具失败不能直接等价为任务失败，部分交付也不能等价为所有目标已经完成。
- 终态 CAS 确认后才发 `run.completed/partial/failed/cancelled`；提交未确认时恢复同一 Run，不发布假终态。

`CompletionVerifier.verify` 中仍有 `continue/verifying` 诊断分支，这是源码现状；实际行为必须连同收尾调用方判断。“停手结束”不代表文件已生成、保存或交付。

### 9.3 未完成产物任务的寿命

任务不是 Run。用户说「继续」时创建新 Run，但仍是同一份未完成产物任务。沙箱容器仍按 Run 销毁（2026-07-22：不跨 Run 续租容器），中间工程靠检查点恢复，不靠长驻容器。

四条不变量必须同时成立，禁止再用续做禁搜、收工具、计划门锁或一次性沙箱互相否决：

1. **契约继承。** `needs_resume_checkpoint` 且上一份 GoalContract 的 `deliverable` 是文件（含「修改既有文件」）时，必须继承该契约，禁止把「继续」收成「对话答复」。仍走 `seed_goal_contract`，增加 `prior`，不绕过、不另写一套契约源。用户这句带了新约束（改配色、加页、换题）则按现句重算。
2. **工程检查点 / 工作区 Commit。** Workspace 是源文件仓库，Sandbox 是一次 Run 的工作副本，禁止共享目录。`artifact_coding` + PPTD 的 `/workspace/tmp/ppt-project` 经 File Service **Commit** 整树快照到会话工作区（MinIO 前缀 `workspace/{user}/{thread}/`，不进「我的文件」）。非 PPT 工程根是 `/workspace/tmp/work`（或 Skill 声明的相对根），**禁止**把通用树解到 `ppt-project`。同 Run 等待确认不得 `close_scope`；bash / `fetch_ppt_asset` 成功后增量 Commit。Worker 重拉同一 Run 或用户「继续」时 **Pull** 当前树进新沙箱——**不限 PPT Profile**；教案/文档/普通 `write_file` 续跑同样 Pull。Pull 失败必须告知模型：计划 `completed` 不能当文件还在。过渡期可读旧 `staging_ckpt`。PPT 完成验证仍只认最终 `.pptx` 发布回执。该 Run 已完成运行时准备时，会话池满应短等或重放安装配方，不得把已装依赖丢进一次性沙箱。
3. **可推进工具面。** Tool Registry 不得因预算、重复调用或关键词闸收成空集；工具可见性只由 ToolSpec、用户授权和真实资源范围决定。模型还在调用工具时不得提前收工；模型停手给出助手回复时本轮结束。交付任务的工作区检查点跨运行段恢复，任务不得要求用户再次说「继续」。
4. **Run 单飞。** 同一 `run_id` 同一时刻只允许一套 Loop。Job 租约过期后，旧 Worker 不得重入；新 Worker 只能在 CAS 接管后从 `execution_control` 检查点恢复，副作用按幂等键去重。暂时性恢复失败保持 `waiting_system` 并按上限退避；不得把租约接管或 `max_attempts` 单独解释为业务失败。

明确不做：PPT 第二内核（ppt-studio 仍是视觉与 PPTD 作者）；会话级长驻沙箱；把 PPTD 源 ZIP、中间 `.page`、搜来的配图或生成脚本送进「我的文件」。`LoopState.force_converge` 不随 resume 恢复。

### 9.4 三层文件域（Workspace 源仓库 ≠ 沙箱副本 ≠ 我的文件）

Workspace 是 **Source of Truth**；Sandbox 是一次 Run 的 **Working Copy**。两者只通过 File Service 做 Pull（拷入）和 Commit（写回），禁止 bind-mount 同一目录。

「我的文件」保留 **Publish 后的最终产物** 与用户主动上传的个人文件；其中的个人文件夹可被选为主 Agent 的工作文件夹，复用其中的素材继续编辑。内部会话工作区弹层里的 `.pptx/.docx` 是未发布草稿/项目文件，不是产物卡。

| 域 | 谁用 | 放什么 | 寿命 |
| --- | --- | --- | --- |
| 沙箱 scratch | 仅本 Run 的模型 | 正在改的 `/workspace`（PPT 为 `/workspace/tmp/ppt-project`，其它工程为 `/workspace/tmp/work`） | Run 结束销毁 |
| 会话工作区 | 用户与模型；File Service | 用户上传、模型落地的素材、整树快照（MinIO `workspace/` 前缀） | 跟会话长期保存；删会话才清 |
| 我的文件 | 用户 + 产物卡；选择文件夹后供主 Agent 读写 | 已发布的 `.pptx/.docx/...`，以及用户主动上传的文件 | 现有配额；用户上传与工作文件夹交付文件长期保留，其余沿用 TTL |

三条闸：

1. **生成内容经发布进入「我的文件」，用户主动上传独立保留。** `publish_ppt_artifact` 结构校验（ZIP / 页数 / 布局 lint）通过后把成品写成 `source=generated`；视觉评分与精品分只作警告，不得拦交付。树快照、`.page`、生成过程脚本、工作区配图不得当交付物露出；用户主动上传的脚本仍属于用户文件并可编辑。
2. **内部工作区主键是会话。** 开工 Pull 该 `thread_id` 当前树；Commit 覆盖同一指针并留版本。用户从工作区抽屉或输入框放入的图片 / PPT / 粘贴文本都是本轮素材：`/chat/upload` 写 `source=workspace`（不进「我的文件」清单，含显示全部），发送时 ingest 进会话工作区（图片进 `media/`）。「我的文件」页或工作文件夹面板的主动上传仍是 `source=uploaded`。AXIOM Agent 的 `glob/read_file/bash` 只可见所选工作文件夹、本会话内部文件、本轮在 `+` 中明确选中的文件和明确 RevisionTarget，禁止枚举或镜像用户整个「我的文件」。Run 断掉只丢副本。
3. **主对话在输入框 + 右侧开放工作文件夹（v1.198，H2 / H3 / H5）。** 用户可新建或选择「我的文件」中的个人文件夹，直接上传素材、查看及下载文件。文件夹只收用户主动上传的素材与已生成的交付文件；搜索下载的参考图片、临时素材和工程检查点继续留在内部会话工作区，不自动归入所选文件夹。连接器管理入口仍隐藏。

工作文件夹绑定到 Thread，首次发送时由服务端校验归属并持久化；队列、续聊、重新生成和 Worker 恢复均读取同一绑定。运行时不能切换，已有对话改选文件夹会打开新对话，原对话及文件保持原归属。新对话再次选同一文件夹可继续使用其中已有文件，无需逐轮重新勾选；文件名相同的其他文件夹不可见。绑定文件夹被删除后明确报错，不静默扩大到全部「我的文件」。

`glob/read_file/edit_file/write_file` 与 `bash` 共用所选文件夹、当前会话内部材料及用户明确选中件的服务端范围。`bash` 在选择文件夹时以沙箱 `/workspace/files` 为默认工作目录；这是经 File Service 拷入的工作副本，不是浏览器用户电脑的磁盘目录。用户直接在文件夹页上传的文件同样可读可编辑，回写沿用现有文件 ID、版本历史与内容哈希、文件夹位置冲突检测。后台作业保存启动时的文件身份与版本基线，结束时复用，不重新读取较新的基线来覆盖并发修改。新生成的交付文件自动归入绑定文件夹并保留；编辑原文件保持原归属。Research 和 Plan 调查阶段继续只读，不因选择文件夹扩大写权限。内部工程仍按会话 Pull/Commit，不把全部「我的文件」或二进制内容塞进模型历史。

v1.198 验证状态（2026-09-10）：文件服务、工具、Harness、修订与后台 bash 定向回归 **254 passed**；前端工作文件夹与 Run 恢复 **14 passed**；定向 ESLint、Python 编译通过。`:3200` 真实页面已完成新建「工作文件夹验收-0910」、上传 135 B 示例文本与列表回显，此前版本桌面及 320/390 px 入口、选择与新建面板已检查。最新按用户提供的 Codex 参考改为浅灰圆角文件夹标识、无标题紧凑搜索列表及独立创建弹窗，支持创建时添加素材，选中与输入框聚焦均使用灰色；窄屏也保留文件夹名称。最新版本定向 ESLint 与 Vue/Less 编译已通过，前端恢复后停在登录页，待用户登录后验收最新样式与创建上传流程。后端已由跑服务会话重新加载，OpenAPI 已含 `workspace_folder_id`；真实 Thread `thread_2085197534705397761_6769bf344944` 自动读取夹内上传文本并原位编辑，文件 `66163f5c009b4cad8ebf47cadc4312a6` 保持原 ID 和 folder_id，版本从 1 到 2，落盘字节逐一比对符合预期，页面显示 v2 产物卡。内置浏览器下载事件仍未取得成功回执；文件下载到本机的最终验证尚未完成。仓库要求的 `tests/parity/` 当前不存在，已用上述定向回归代替；另一个既有 Skill 重发静态测试依赖已不存在的 `const turnSkills = reuseTurn` 源码标记，在本次改动前的 HEAD 也不能满足断言，本次未改该测试。

### 9.5 文件交付拦截清单（v1.81 核对）

「拦截文件交付」指平台**不让模型把文件交出去**（藏工具、`tool_choice=none`、拒发布、拒 `write_file`）。下列历史闸**不得复活**：

| 历史闸 | 生产状态 |
| --- | --- |
| 预算/`forced_final` 把 `tool_choice` 打成 `none` | 已删。循环里有工具时固定 `tool_choice=auto` |
| `execution_mode` 抽掉搜索/计划工具 | 函数残留兼容旧 import，**循环不调用** |
| 视觉评分 / 精品分拒绝 `publish_ppt_artifact` | 已删。结构校验（ZIP/页数/布局）仍要过；审美只警告 |
| 「照片/图片」关键词拒绝 `download_url` | 已删 |
| Completion Verifier 缺口回灌同一 Loop / `phase=verifying` 锁工具 | 已删。finalize 不把缺口打成 verifying |
| 计划绑定拦截 bash/发布 | `publish_ppt_artifact` 豁免；无 `update_plan` 时不拦 |
| 办公任务禁止 `write_file` 过程脚本 | **v1.81 删除**。过程脚本不是产物卡，但导出链路需要它 |
| 非 PPT「继续」不 Pull 工作区 | **v1.81 删除**。教案/文档续跑同样 Pull |

仍保留、**不是交付拦截**的边界：

1. `write_file` 拒绝把 `.docx/.pptx/.xlsx/.pdf/图片` 当 UTF-8 文本写（会得到打不开的坏文件；应走 bash + 对应库）。
2. 过程 `.py/.json` 照常落库，但不进产物卡 / 「我的文件」默认清单。
3. PPT 进「我的文件」仍只走 `publish_ppt_artifact` 结构校验。
4. v1.79：模型本轮不再调工具并给出助手回复即 `completed`。这不是藏工具；AXIOM Agent 若口头说完却没 `write_file`，平台不会替它写文件。

「继续」必须继承未完成产物任务：文件契约、研究报告契约、Research `agent_mode`、会话工作区。不得把「继续」收成新闲聊。

---

## 10. 用户可见进度与计划卡

受理与启动的边界（v1.189）：HTTP 202 只绑定持久化 Run/Thread，不等于 Worker 已开始执行。前端保留 `created` 与停止按钮，当前运行头统一为「本轮处理中」；等待启动/恢复细节由真实阶段事件表达，不用标题切换伪造模型活动；仅真实 `run.started` 或权威活动快照进入执行态。本地 `run.py` 须监护其拥有的 Worker，异常退出后按退避重新拉起，服务停止时不再拉起；本地受管 Worker 缺失时 `/health/ready` 返回 503，不能只凭数据库连通报告就绪。直接启动 API、外置 Worker 的部署不推断本机应存在子进程，也不由 API 另起 Worker。

历史会话可发现性（v1.184）：只要存在已保存、非归档且非空的用户消息，历史列表及搜索必须保留该会话。助手正在生成、空输出锚点、失败、取消或中断都不得成为隐藏整条会话的条件，也不得按助手正文中的错误关键词过滤历史。空线程和只剩归档用户消息的分支继续隐藏；用户归属、内置应用 origin、子线程隔离、搜索和分页仍在服务端统一生效。切换模块只释放当前观察连接，不取消后台 Run；返回主对话仍按既定交互展示欢迎页，通过历史明确打开会话，不恢复隐式跳转最近会话。Run 失败应在会话内展示真实原因，不通过移除历史入口处理。

敏感词拒绝轮隔离（v1.190）：适用范围内的主 Agent、内置应用与 Research 共用 New API 结构化敏感词终止边界。命中时先以 Runtime PG 终态 CAS 确认本轮失败，再将该 Run 的用户消息和终态锚点以 `policy_pending → policy_rejected` 的幂等中间态收敛：两个状态都由历史展示接口返回，也都必须从后续模型请求、会话压缩、记忆抽取、线程引用、任务快照与显式续接源中排除；若首轮自动标题仍等于被拒绝原文，同步清除该派生标题，避免它绕过 transcript 过滤重新注入引用对话。后续合法新问题必须使公开 transcript 指纹发生变化，旧 canonical Provider 隐藏历史不再符合恢复条件，只能从已隔离后的公开历史重建。普通网络错误、用户取消、研究引用拒绝及其他失败不得被扩大隔离。Runtime/MySQL 跨库无原子事务，当轮幂等写入外，启动与周期对账必须按权威敏感词终态收敛已有会话。不物理删除用户原话，不依赖前端清理历史，不以修改新提示词代替该隔离。源码测试不等于 Python 服务已重载，重启后仍须以「敏感词命中 → 合法问题正常回答」做真实网关 E2E。

真实动作、结果、检查和公开过程阐述来自已登记的权威事件，例如读取、搜索、运行、等待确认、验证和文件保存。模型供应商返回的 `reasoning_content` 或 reasoning summary 允许通过 `message.reasoning.delta/completed` 受控展示，但必须满足：

- 思考是时间线 `{kind:'thinking'}` 步骤，与 bash 等动作同行：英文标题右侧箭头（收起向右、展开向下）+ 可展开灰色正文（15px / `#999` / 行高 1.55，按空行分段）。
- 仅当收到 `message.reasoning.delta/completed` 时才插入该步骤，发送后不预先占位。
- 进行中标题为 shimmer「Thinking」，不显示秒数，默认收起；展开后完整流式展示灰色正文：与正文相同的 45ms/64 字合批 + 前端 rAF 追赶，不按空行切段跳字，不做高度手风琴。不做 180px 渐隐裁切。
- 用户向上滚动，或在生成中主动展开任一 Thinking/Thoughts，即表示正在阅读：消息列表必须立即锁定手动阅读态，不能因为仍处于贴底阈值内就在下一次 `scroll` 事件恢复；reasoning delta、后续工具步骤和新 Thought 的高度增长不得反复把视口拽回底部。只有用户主动向下回到底部、收起最后一个触发阅读态的 Thought、点击「回到最新消息」或发送新消息后才恢复跟随；单次收展过渡可以平滑改变高度，但不得连续跳动。
- 结束后标题为「Thoughts for Ns」，默认收起，仍可展开看全文。
- 公开正文开始后，思考步骤按到达顺序留在时间线；后续思考 burst 另起一步，不再把思考写进聊天正文。
- 历史回放可展开查看思考正文与秒数（`reasoning_summary` / `reasoning_seconds` 与 thinking 步骤），不回放原始逐 token 流。标题只用秒数，不用最后 64 字碎片当标题。
- 最终正文中的图片引用是消息展示的一部分：编号有序的 `citations` 必须随共享 MySQL 消息展示投影持久化，不得只留在单环境 Runtime PG 或浏览器内存中。历史读取优先当前 Runtime 引用，缺失时回放同消息保存的引用，不按图片加载成功与否重新编号。图片加载失败保留标题、真实来源与重试入口，不得整卡静默消失；旧历史没有可验证原始引用时如实展示不可用，不从正文或其它会话猜图片。
- 不写聊天正文，不作为工具已执行、产物已保存、质量已验证或任务已完成的证据。
- 不再另挂顶部隐约尾窗；思考只作为时间线执行步骤出现。

公开过程阐述使用 `message.commentary`，应像 Codex 一样根据刚发生的真实工作自然说明关键判断、依据或变化、下一步动作；每段只说当前真正有信息价值的部分，不强制套用固定三段式。表达应自然完整，避免连续使用“我……”开头，避免只复述工具标题、内部 Schema、prompt 修补、策略重试、Guard 假失败或无证据的完成声明。工具轮里同时产出的整篇答案草稿不是公开过程阐述；它可保留在 Provider 上下文供下一轮继续，但不得发送、落库或回放为 `message.commentary`，避免与终态权威正文重复。工具步骤本身仍只展示真实事件。

网页搜索步骤使用渐进披露：单独一次 `search_web` 直接显示「已搜索网页：代表结果页标题 · 域名」；同一执行阶段内连续两次及以上搜索默认归拢为「已完成网络搜索」，组头不显示搜索次数，用户可展开逐条查看相同格式的成员行。查询词只是工具入参，不在完成态成员行重复用户输入；进行中统一显示「正在搜索网页…」。失败成员只显示「网页搜索失败」，后端错误原因仍保留在事件数据中，不进入执行时间线。搜索命中数量继续保留在事件与来源面板，不在执行时间线追加 `+N`、「搜索到 N 个网页」或「N 次网页搜索」，也不得把重复命中数冒充已阅读网页数。组头箭头沿用其他执行步骤的灰色样式，默认隐藏，仅在鼠标悬停、键盘 `focus-visible` 或展开状态下显示；同时必须保留 `aria-expanded` 与 Enter/Space 键盘操作。所有执行步骤的渐进披露统一使用对称的进入与离开过渡，展开和收起都必须平滑，且在 `prefers-reduced-motion` 下关闭动画。公开过程说明、Thought、用户插话、计划步骤切换和其他工具动作会打断搜索归拢，不能为压缩高度篡改真实事件顺序。

研究报告卡上方的「研究完成情况」展示整轮持久计时和全队按 URL 去重的网页总数，例如「耗时 6分53秒 · 共搜索到 18 个网页」。完成行不向用户展示「（去重）」或其它统计口径说明。网页总数读取研究台账 `sourcesFound`，不统计工具调用次数、不累加各成员重复结果、不以最终报告引用数替代；计时复用整轮起止/持续时间，切页刷新不重新计时。旧记录缺少统计时明确显示未记录，不编造零值。

计划卡规则：

- 是 Run 级视图，不绑定某条聊天消息。
- 第一版只读展示，用户通过对话调整目标。
- 只渲染已提交的最新 `plan_version`，不做本地计划副本。
- 重复事件忽略，乱序事件按版本丢弃，版本断层重新加载快照。
- 计划变化静默刷新，步骤状态和真实进度实时更新。任务协作 To-do 按数组顺序展示现行步骤，0/N 不计已替换的 `invalidated` 残骸。
- 任务协作卡的状态只读 Plan Store 中已提交的 ToolObservation 投影；不依赖模型先说「计划已同步」，也不依赖状态型 `update_plan` 才允许继续执行。
- 计划白卡与研究报告白卡预览都只保留底部渐隐；电脑、手机和 iPad 都不画圆形下箭头。文档标题只出现一次：卡片顶栏和全屏纸面标题保留，正文不再重复同一标题。
- 规划阶段 `update_plan` 把步骤标成进行中，不等于用户已同意执行；正文仍收进计划卡。确认卡若没有带标签的 Markdown，用当前 `plan_steps` / `taskPlan` 合成预览（不含步骤验收）。
- 用户点「开始执行」后立即退出计划模式：前端关掉胶囊；Run 的 `agent_mode` 改为 standard；续接轮改写计划约束、放开写工具。补充或跳过仍留在计划模式。

---

## 11. 旧架构删除与数据切换

### 11.1 必须删除

- 主对话 `task_graph/` 的 repository、projector、types、router、brief、goal guard 和 progress narrator。
- `TASK_DAG_MODE`、`HARNESS_V2_MODE` 及所有 fallback。
- `runtime_v2_service` 命名和 V2 API 文案；其中已验证的 CAS、Job lease、事件回放迁入 Run Store。
- 旧需求、旧计划确认、旧计划就绪和 Task DAG 前后端事件。
- 前端任务图类型、reducer、历史回放和旧确认卡。
- 模型可见退休工具及其提示词、协议恢复、Skill 翻译和前端展示。
- 旧 `/chat` 流式路径和 legacy/v1/v2 主对话协议协商。

### 11.2 数据库

- 使用正式迁移删除 Task Graph、Node、Attempt、旧 Brief/Requirement 表和字段。
- 整理 Run、Plan、PlanStep、Event 的约束、索引和版本字段。
- 切换前停止旧 Worker，将非终态旧 Run 标记为因架构切换取消。
- 不迁移旧任务图和旧计划事件，不提供旧历史解释器。
- 不删除无关聊天消息、用户文件和知识库数据。

### 11.3 退休名称门禁

- 生产代码、前端、系统提示词、第一方 Skill、配置和用户文案中不得出现退休协议。
- 领域服务内部合法方法名不得被误删，但不能注册成模型旧工具。
- 自动化扫描只允许在退休名称门禁测试自身维护最小例外。

---

## 12. 架构防腐规则

违反任何一条不得合并：

1. 不得另建主对话 Agent Loop；§1 已明确允许的 Research 专用编排是唯一例外，仍复用共享模型、工具和运行基础设施。
2. Kernel 不得导入具体工具实现或依赖工具名称。
3. 模式差异只通过 Profile 和 Policy 表达，禁止散落模式判断。
4. 工具权限、并发、重试和审批只来自 ToolSpec。
5. 事件只来自 Event Catalog，前后端不得各自发明事件字符串。
6. Plan、Run 和工具回执各自只有一个持久化事实源。
7. 前端不得乐观修改计划或推导完成态。
8. Context Compiler 不得成为新的业务规则仓库。
9. 新能力通过模块接口接入，不能继续向 Kernel、入口服务和 system prompt 堆补丁。
10. 兼容层必须有明确删除版本；本次切换不保留兼容层。
11. 架构测试必须检查依赖方向、禁止导入、禁止名称和单 Loop。
12. 如果一个模块同时承担状态、策略、执行和展示中的两项以上，Code Review 必须要求拆分。
13. 一个真实 Provider 网络请求必须恰好对应一个 physical attempt；SDK 隐式重试必须为 0，usage 只能绑定实际返回它的 request ID。
14. Root usage 必须归集所有后代和 post-terminal 调用，只能按 delta 累计；Provider amount 按原始字符串保存，不得通过浮点或 token 公式伪造精确费用。
15. 同一 context epoch 的 Provider 输入只允许尾部追加；任何旧 item 改写必须开新显式 epoch。DeepSeek 必须保持完整无状态输入，不得借鉴其他 Provider 的有状态续接字段。
16. 工具结果只有在 durable store 写入成功后才可声称可取回；`AppliedToolResultPolicy` 是历史 item 的一部分，恢复时不得随新默认值漂移。

---

## 13. 实施阶段

### H0：契约与基线

- [x] 冻结 Run、Plan、Event、ToolSpec、ToolObservation Schema。
- [x] 建立架构依赖和退休名称门禁；H6 前采用只减不增债务上限。
- [x] 记录当前离线评测、结构债务和环境失败基线。真实模型延迟与 token 基线因账号无余额暂无可比数据，发布验收前必须补跑。

基线事实源：`agent-api/tests/baselines/harness_h0.json`。

退出条件：新契约测试通过，旧行为基线可重复。

### H1：Run Store 与 Harness Kernel

- [x] 建立 `agent_harness/` 包和唯一 Kernel。
- [x] 迁移 RunState CAS、Job lease、事件、取消和恢复。
- [x] 实现 Context Compiler、Profile Registry、Event Catalog。
- [x] 所有主对话工具由统一 Dispatcher 完成可见性与执行前授权，再经 Gateway/Executor 执行；不保留第二 Loop。

退出条件：普通问答、单工具、连续工具、取消和 Worker 恢复走同一 Kernel。

### H2：工具与沙箱

以下基础项为已完成的源码切换；后续未勾选扩展项已在 v1.171 记录实现，仍保留 Skill 取包、远程沙箱、真实文件交付等验收门禁。未勾选不等于没有代码，已有实现也不自动满足退出条件。

- [x] 引入 ToolSpec、ToolObservation 和资源锁。
- [x] Policy/Gateway 从 ToolSpec 读取权限、效果域、审批、幂等和并发策略。
- [x] 完成沙箱执行内核重命名，模型只见 `bash`。
- [x] 临时工作区结果与保存到用户文件的持久产物使用不同效果域和完成证据。
- [x] 第一方 Skill 统一使用 `bash` 与 `/workspace/files/`；旧 Skill 指令在进入模型前归一化，不注册退休工具。
- [ ] Skill 包完整性：优先 ZIP/字节通道；`text_json` 不得把损坏二进制当已挂载；`incomplete` 必须点名未挂载文件。
- [ ] 通用 Skill 运行时预检：只读包内声明，缓存于本 Run；`ready` 后不重探；无 Skill 的 bash 不预检。
- [ ] 沙箱失败分类：`timeout` / `cancelled` / `network_error` 不得伪装成脚本错误。
- [ ] 受控 `env_prep` 联网安装依赖（白名单软件源）；策略未落地时 `network_denied`。
- [ ] `bash` 长命令作业：可查状态、读增量日志、取消；超过工具墙钟不得打断落库。
- [ ] 非 PPT 工作区 Commit/Pull 使用 `/workspace/tmp/work`，禁止解到 `ppt-project`。
- [ ] 批量交付：中间产物仍拒；超过 20 个交付物时打包或分批，回执含请求/成功/失败清单。

退出条件：工具注册表、权限、并发、审批、幂等和产物同步契约全绿。通用 Skill 从「加载进来了」能走到「环境就绪、失败可定位、中断可续」；已有 PPT / 普通聊天 / 知识库路径不退化。

### H3：Context、Memory 与 Completion

- [x] 每轮从事实源重编译上下文。
- [x] 大工具结果通过 Result Store 外置并按需取回。
- [x] 记忆改为候选加治理写入。
- [x] Completion Verifier 与 turn_finalizer 共同处理结构化等待、恢复和终态；证据诊断不重新驱动模型 Loop。

退出条件：长会话、记忆隔离、假完成、持久化失败和证据不足用例全绿。

### H4：Plan、Steer 与三个 Profile

- [x] 实现 Plan CAS、完整快照事件和步骤证据。
- [x] Plan Mode 在同 Run 中完成调查、确认和执行。
- [x] 用户 steer 触发目标版本和安全点切换。
- [x] Research 物理只读、scratch 隔离和显式导出。

退出条件：计划卡在并发、重连、乱序、改向和恢复场景始终同步。

### H5：前端与协议切换

- [x] 前端切换 Harness Protocol 1。
- [x] 首次进入 `/center/chat` 或整页刷新时停留在欢迎/新对话，不自动打开上次或最近会话。点击左侧「主对话」同样进入新的空白窗口，但原 Thread/Run 继续后台执行；顶栏不额外增加「新对话」。「对话历史」每次打开都重拉服务端列表，刚才的会话必须无需整页刷新即可直接点回。
- [x] Run 级计划卡和事实进度只消费权威计划与 Event Catalog。
- [x] 删除任务图、旧要求卡、旧计划确认卡和旧 replay；原始推理改为 Event Catalog 管理的瞬时流，不恢复旧运行时或旧协议。
- [ ] 付费模型浏览器矩阵：gpt-5.5 Standard 基础链路已通过；Plan、Research、停止、刷新和断线重连仍待补跑。不能把单场景结果冒充完整矩阵，也不需要为此保留旧架构或兼容代码。2026-08-17 规划/稳定性升级把终局报告、僵尸巡检、GoalContract 与步骤验收接到了协议和任务协作面板，但上述五项真机矩阵仍未补跑。

退出条件：用户界面不再依赖任何旧事件或旧历史解释器。

### H6：硬删除与迁移

- [x] 删除旧 DAG、V2、旧 API、旧模式字段和 fallback。
- [x] H6 切换时迁移推进到 `runtime_0014_run_inputs`，删除或改名旧图表、模式列和输入命名；这是历史阶段证据，当前源码迁移头见 §0.2，实际运行库需单独核验。
- [x] 切换迁移取消非终态旧 Run；Worker 只从 Harness Kernel 进入。
- [x] 生产代码退休名称和双内核扫描归零；负向门禁测试仅把退休名作为禁止样本保存。

退出条件：生产路径只有 Agent Harness，旧历史不保证打开。

### H7：Provider 额度治理与持久上下文投影

H7 是 v1.122 的规范目标与分阶段上线门禁。本节未勾选项表示仍需源码、迁移或运行时验收；代码已合并、部署请求已排队或文档已更新，都不等于生产进程已加载。

Runtime PG 只能由 Alembic 从当前 revision 顺序升级。`create_all` 只能采纳全新空库，不得把已有业务表或旧 revision 直接 stamp 到新 head；否则会跳过 `runtime_0016_model_attempts` 的历史 input/cache audit 回填，形成“版本号已到、数据迁移未跑”的假绿。共享开发库的集成测试必须使用 `ddl=False` 只读验版，不得在测试过程中自动建表或 stamp revision。

- [ ] 完成全 purpose 的 logical call / physical attempt 总账、数据库单调 Run 序号、usage 归一化、Root 后代与 post-terminal delta 归集，并与同窗口 Provider 后台数据对账。
- [ ] 禁用所有主对话可达 SDK 隐式重试；验证 public preamble 只有一次 `reasoning.effort=none` 尝试，compaction 每源分段只有一个 physical attempt，完整分段链计入 Root 总账。
- [ ] 完成 `AppliedToolResultPolicy`、唯一 `ToolResultProjector`、durable Result Store 与跨 resume/restart 一致性；在未另行完成观测前，未配置 token limit 的工具保持 8,000 字符兼容行为。
- [x] 完成 `runtime_0019_prompt_history`、公开 transcript 指纹、工具/直答/恢复共用 canonical Provider 历史、显式 epoch 转换、无静默裁剪检查点与 reset 审计指标。
- [ ] 部署后以连续干净窗口累计至少 100 对无对齐错误的跨 Run 样本，再通过 10% canary 至少 100 个 Root Run，完成语义、LCP 和错误率门禁。
- [ ] 以 `MODEL_USAGE_ENFORCEMENT_MODE=observe` 连续观测至少 7 天且不少于 100 个完成 Root Run，再根据实测分布单独提出第二阶段预算与每工具 token limit；本阶段不自动写入生产硬阈值。

退出条件：迁移头、进程 `StartedAt`、`/health/ready`、全 purpose attempt 样本、Root 归集、DeepSeek 无状态全量 payload、工具结果跨恢复取回和 shadow/canary 门禁均有独立验收证据。

---

## 14. 测试矩阵与完成标准

### 14.1 自动化

- 单元测试：状态迁移、CAS、Plan 更新、Profile 权限、ToolSpec、Policy、记忆治理、完成验证。
- 契约测试：API、SSE、Event Catalog、ToolObservation、前后端类型。
- 集成测试：Worker、Gateway、沙箱、用户文件、搜索、知识库、浏览器和连接器。
- 故障测试：重复消息、重复事件、乱序、模型/工具超时、取消、Worker 崩溃、租约过期、沙箱退出、上下文压缩和数据库冲突；验证同一 Run 自动恢复且副作用不重复。
- 安全测试：只读逃逸、危险 Bash、跨用户文件、外部副作用、敏感记忆和提示注入。
- 架构测试：单 Loop、依赖方向、禁止工具名集合、禁止退休名称、禁止旧协议。
- 目标生命周期测试：Standard/Plan 超过历史轮次/token/总墙钟阈值时，不因此产生预算型终态；诊断性 Verifier 缺口不回灌 Loop，系统依赖等待交 Worker 恢复。Research 的取证范围和预算另按 §5.3 验证，不能将其混作普通任务硬终止规则。
- Provider 调用账本测试：覆盖 main/plain/preamble/research/三类 compaction/router/title/memory/paid search/browser/acceptance/parent summary/tool internal（`subagent_model` / `workflow_node` 枚举只为历史行保留）；底层捕获的每个真实请求恰好一个 attempt start/terminal，SDK `max_retries=0`，同协议 retry 语义 hash 不变，兼容兜底是有 lineage 的新 logical call。
- Usage 与 Root 归集测试：`completed/incomplete/failed` 的可信 usage 都绑定正确 attempt；无 usage 断流保存 `NULL + unknown_provider_charge`；reasoning/cache/amount 归一化正确；研究团队成员调用、恢复与 post-terminal title/memory 只按 delta 归集一次。
- Context 投影测试：跨分钟 `StableBasePrompt` 逐字一致；`ThreadWorldState` 只携带正确日期/时区，`get_current_time` 按需返回 ISO-8601；memory/skills/workspace 变化只追加 patch；同 epoch 主输入精确前缀；edit/regenerate/compaction/model/transport/tool schema 变化产生正确 reset reason。DeepSeek 始终发完整输入且不含有状态禁止字段。
- 用户位置测试：只接受受信代理已解析的公网 client peer，忽略应用层伪造的 `X-Forwarded-For`；原始 IP 仅加密存入活动 Run 并在终态清除，不出现在模型结果、公开快照或新增的工具业务日志，已有网关/接入层 access log 仍按部署日志政策治理。公网 IPv4/IPv6 可返回有界的国家/省州/城市/时区；loopback、内网、保留地址、定位服务超时/限流/非法响应均 fail closed，不用服务器出口代替。
- 消耗收敛测试：public preamble 只有一次 `reasoning.effort=none` Responses attempt 且不含 `thinking`，半句/失败时不修补；compaction 每源分段一次调用，相同失败 hash 同 Run 去重，不以丢弃未读原文修复 overflow。
- 工具结果测试：每工具 applied token/char policy 真实影响 Responses/Chat payload；未配 token limit 时保持 8,000 字符兼容行为；durable store 成功才产生可取回 handle；超 2 MiB、不持久和写入失败不误报；分页均保留不可信边界，恢复后旧投影与 applied policy 逐项不变。

### 14.2 必测用户场景

1. 普通问答不滥用工具。
2. 读取并修改用户文件，落库后才宣布成功。
3. Bash 生成真实 Office/PDF 产物并完成格式或渲染检查。
4. Plan Mode 调查、追问、计划、确认、执行和验证。
5. 执行中用户改变目标，计划和 UI 实时一致。
6. Research 多来源检索、引用、只读约束、单一蓝框报告交付和用户主动导出；`run.completed` 或交付型 `run.partial` 后 composer 自动回到 Standard，停止/failed/waiting 时保留 Research，历史回放不重开开关。
7. 长对话压缩后保留用户目标、计划、证据和权限。
8. 浏览器刷新、SSE 重连和 Worker 重启后恢复同一 Run。
9. 工具成功但产物保存失败时诚实失败。
10. Guard 拒绝能引导模型纠正，但不制造用户可见假失败。
11. 一个 plain/direct Run、一个 10–20 轮 DeepSeek 工具 Run、一个 Research Run、一个强制 compaction 与一个受控 paid-search fallback，均能在 Root 总账中对齐 logical calls、physical attempts、usage、LCP 和 unknown charge。
12. 工具大结果跨新 `drive_model`、HITL resume 和 Worker restart 仍可按 handle 分页取回；原文未持久成功时不出现「完整已保存」。

### 14.3 性能与体验

- Run 接受后立即出现真实状态，不用模型虚构进度填空。
- 长任务持续产生真实阶段事件，不重复输出工具大结果。
- 重构前后记录首事件时间、模型轮数、输入 token、总耗时和失败率；不得出现无解释的明显回退。
- 取消信号、重复提交和恢复不能造成副作用重复执行。
- 性能报告同时展示 Provider cache usage、同 scope exact-prefix/LCP 和 Root 全 purpose 用量/原始 amount，不用任一指标代替另两个。
- `MODEL_USAGE_ENFORCEMENT_MODE=observe` 只做服务端观测与告警，不改变当前工具可见量、模型路由、HTTP/SSE 契约或任务终态。

### 14.4 最终完成定义

- 主对话生产代码只有 `agent_harness` 一套内核。
- Standard、Plan 共用主循环；Research 专用编排复用同一模型驱动、Run、Tool、Context、Event 和恢复基础设施。
- Plan 面板和持久化计划始终一致。
- 模型无法调用退休工具或绕过 Profile 权限。
- 用户可在活跃连接中看到 Cursor 式可展开 Thought 步骤；历史可展开回放截断后的思考正文和秒数，原始 token 流不进入正文或完成证据。
- 公开过程阐述准确说明判断、依据与下一步，工具步骤只来自真实事件。
- 终态由本轮是否还要调工具决定（Codex 停手即完成）；预算、重启、超时、上下文压缩和恢复次数不能单独产生终态。结构化 HITL 除外。
- Provider 每个 physical attempt 都能追溯到 logical call 与 Root Run，usage/amount 不丢失、不误绑、不重复归集；未知消耗保持未知，不写成 0。
- 同 epoch Provider 输入维持 append-only 精确前缀；StableBasePrompt 不含分钟时间与运行计数，DeepSeek 只接收完整无状态输入。
- 工具大结果的模型投影、durable handle 和 applied policy 在恢复后不漂移，存储失败不产生虚假取回承诺。
- DAG、Runtime V2、旧协议、旧表和旧前端投影全部删除。
- 定向测试、完整评测、浏览器验收和 `git diff --check` 全部通过。

### 14.5 历史基线证据（保留当时口径）

以下 H1–H6/v1.8 记录来自 2026-08 的阶段验收，其中思考展示、预算及完成规则已有后续版本覆盖。不能以本节测试数、服务启动时间或旧 UI 现象宣称 2026-09-11 工作区已完成验收；当前契约读正文，增量证据按 §15 的版本日期追溯。某条旧待验状态也不能覆盖后来版本已经记录的实测。

- 后端全量离线测试（H1-H6 切换时）：`2048 passed, 2 skipped`；跳过项为需要外部运行环境的用例。本次 v1.8 未重跑全量。
- 主对话前端测试（H1-H6 切换时）：`33 suites / 294 tests passed`。本次 v1.8 定向重跑 `harnessFrontendContract.test.ts`：20 passed。
- gpt-5.5 Standard 真机基础链路已通过：inspect 权限下执行 scratch `bash date`，reasoning 尾窗实时出现并在公开 commentary 前撤下，终态无残留；持久事件只保留 commentary、工具和终态，reasoning 事件计数为 0，且没有 artifact receipt。Run：`c8d74688096944f388d959e1ba1b72fb`。
- 后端相关离线测试（2026-08-17 规划/稳定性升级定向）：`tests/test_planning_stability_upgrade.py`、`tests/test_loop_state.py`、`tests/test_agent_harness_plan_controller.py` 及相邻 harness/修订用例合计 175 passed；`tests/test_refresh_loss_p0.py::test_public_commentary_is_projected_without_raw_reasoning_tokens` 1 failed（思考摘要截断，与本次升级无关的既有缺口）。`tests/parity/` 当前目录为空，无对拍用例可跑。
- 主对话改动文件定向 ESLint：通过。
- `agent-api` 与 `agent-worker` 已重启加载 v1.8 规划/稳定性代码；文件 mtime 早于进程 `StartedAt`；`/health/ready` 返回 `ready` 且 Runtime DB 为 `ok`。
- 执行稳定性：`run_terminal_report` 是可读取的结构化 RunState 事实；worker 巡检只把可恢复的非终态 Run 重新排队，不把租约超时、重启或恢复次数映射为失败/partial。`LoopState` 中保留的历史安全网字段只作兼容遥测，不能生成强制收敛文案或任务终态。
- 规划契约：`GoalContract` 随 RunState 持久化并由 Context Compiler / CompletionVerifier / 任务协作面板消费；`update_plan.acceptance` 写入步骤证据，调研类带验收标准的步骤至少两条成功回执才能标 completed；方向校正检查点每完成一步或每 4 轮注入一次（最多 4 次），不新增模型调用。
- 经验沉淀：failed/partial 终局只从结构化字段提炼 `[task_lesson]`（TTL 30 天，网页/工具正文不进记忆；毒指令「以后都这么做」被拒绝）；成功且 ≥6 轮的轨迹进入 `agent_skill_drafts`，满 3 次相似成功后仅出现在管理员 `GET /skill-drafts`，不自动上架。终局报告会带上权威计划步骤标题（`plan_titles`），避免 Skill 草稿步骤栏空白。
- Research 与 GoalContract 对齐：交付物恒为对话内「研究报告」，完成证据来自研究覆盖台账与带引用的正文，不要求 `write_file` 或 `report_file_ids`。
- 任务协作 To-do 与执行共用 Plan Store：模型 `update_plan` 只改结构/当前步；步骤按数组顺序从头到尾，线性计划的唯一 `in_progress` 是最早未完成步；非控制工具回执必须绑定唯一 `in_progress` 或唯一 ready 步；`completed` 仍由工具回执推进，不得空口打勾。交互态 Word/docx 的 bash 落盘记为 `artifact_build`+file_id，可勾该交付步；同 Run 上已有文件回执时，交付步也可据此完成（PPT 发布步仍要 `artifact_delivery`）。连续只改计划会熔断。`depends_on` 只计算 blocked/ready，不是调度 DAG。任务协作 0/N 不计已替换的 `invalidated` 残骸。
- Plan、Research、停止、刷新和断线重连的付费模型浏览器矩阵仍是发布验证缺口，不是继续保留旧架构或兼容分支的理由。

---

## 15. 变更记录

下表是只追加的历史记录；含有当时的方案、测试和运行状态。被后续版本覆盖的规则不再生效，当前实现以正文及源码核对入口为准，不能将旧版本中的“当前”理解为今天。2026-09-19 起，只描述子智能体委派、智能体推荐、皮肤系统、工作流编排与对外 Agent API 的条目已随功能删除从本表移除（v1.85–v1.94、v1.105、v1.132–v1.134、v1.166、v1.209、v1.211），混合条目只去掉对应从句；完整历史见 Git。

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-08-16 | 冻结 AXIOM Agent Harness 目标架构；统一 Loop、Profile、Plan、Tool、Context、Memory、Event、Completion 和旧架构删除方案 |
| v1.1 | 2026-08-16 | 完成 H0 契约、架构债务门禁和可重复离线基线；记录真实模型基线因账号无余额暂无法取得 |
| v1.2 | 2026-08-16 | 完成 H1-H6 生产切换、旧实现硬删除、Harness Protocol 1 前端切换与数据库迁移；记录离线测试证据和付费模型真机验收缺口 |
| v1.3 | 2026-08-16 | 产品决策调整：允许当前活跃连接受控展示供应商原始 `reasoning_content`；新增瞬时 reasoning 事件契约，并明确公开 commentary 的判断、依据和下一步表达要求 |
| v1.4 | 2026-08-17 | GPT-5.5 纯问答路径接入 Responses API reasoning summary；与供应商 `reasoning_content` 共用瞬时事件和不持久化约束 |
| v1.5 | 2026-08-17 | PPTD 工程继续作为沙箱内创作与审查事实源；用户交付统一为单个最终 PPTX，移除 source ZIP 的生成、展示与完成依赖 |
| v1.6 | 2026-08-17 | 恢复思考过程隐约展示：活窗口 + 时间线 thinking 行；`message.reasoning.completed` 持久化紧凑摘要与秒数，delta 仍瞬时。确认单 worker 路径，不恢复进程内 `/chat`。流畅度：worker 由 PG LISTEN/NOTIFY 唤醒（1s 轮询兜底）；合批后的 `message.delta` 双写瞬时通道与事件表供重连 |
| v1.7 | 2026-08-17 | 思考过程改为 Cursor 式一等执行步骤：发送后立即出现「正在思考」，进行中默认展开流式正文，结束后「思考了 Ns」默认可展开收起；历史持久化上限改为 2000 字正文，不再用最后 64 字碎片当标题 |
| v1.8 | 2026-08-17 | 执行稳定性与规划升级：`run_terminal_report`、僵尸巡检、GoalContract、步骤验收、方向校正检查点、结构化失败教训与管理员可见 Skill 草稿；任务协作面板展示契约与验收。不引入 DAG / 第二套 Loop。付费模型浏览器矩阵仍待补跑 |
| v1.9 | 2026-08-17 | 思考步骤动画对齐 [aicss Thinking+Reasoning](https://www.aicss.dev/components/thinking-reasoning)：shimmer 标题、按句渐显、180px 视口渐隐上卷、结束后折叠。思考仍是时间线一等执行步骤，不是独立装饰块 |
| v1.10 | 2026-08-18 | Deep Research 升级为同一套 Agent Loop 上的阶段机（澄清→计划→分主题 `search_web`→交叉验证→报告合成）：证据门禁、连续 HTML 报告落「我的文件」。不新建第二内核，Research 分支继续 `seed_goal_contract` + `main_tool_turn`。`deep_read` 此后已移除，交叉验证按独立注册域计数。 |
| v1.11 | 2026-08-18 | 规划/稳定性收口：终局报告写入 `plan_titles`；Research 默认 GoalContract 为「研究报告」；平台编译报告计入完成证据；补 S6 工具成功观察契约单测。付费模型浏览器矩阵仍未补跑。 |
| v1.12 | 2026-08-18 | 思考步骤改为英文 aicss 口径：进行中 shimmer「Thinking…」加实时秒数，默认收起、点击展开看正文；结束后「Thought for Ns」仍可展开。样式对齐 [aicss Thinking+Reasoning](https://www.aicss.dev/components/thinking-reasoning)，不引入 360px 装饰卡。 |
| v1.13 | 2026-08-18 | 产品特批：仅 Deep Research 走第二内核 `research.kernel`（平台强制分主题 `search_web`，合成仍复用 `model_driver` 与同一套 SSE）。Standard/Plan 仍走 `main_tool_turn`。Research 分支继续 `seed_goal_contract`。报告改为 ChatGPT Deep Research 连续白纸文稿（对话内统计行 + 白卡预览 + 展览区全文），不再用封面/目录分页。 |
| v1.14 | 2026-08-18 | 计划与执行共用一份任务状态：任务协作 To-do 是 Plan Store 视图；`completed` 只由工具回执推进，模型不得空口打勾；后续里程碑可收掉更早的轻步骤。线性计划，不是 DAG。 |
| v1.15 | 2026-08-18 | 计划模式硬契约：批准绑定 `approved_plan_version`；确认卡升级为消息流内结构化审查卡（GoalContract + 步骤验收 + 改这一步）；执行中 `update_plan` 分类为 status / content / structure，仅结构性改动挂起二次确认；content 标记 `diverged`。 |
| v1.16 | 2026-08-18 | 滴水不漏身份绑定：`ToolObservation.plan_step_id` 指向 Plan 节点；无唯一光标或类型不匹配则拒绝执行副作用；按 `step_id` reduce，不再猜当前步或顺手勾掉更早步骤。`depends_on`/`requires` 为元数据。收尾未完成步 `invalidated`，不得空口 completed。不是 DAG 执行器。 |
| v1.17 | 2026-08-18 | 思考步骤改为 Cursor 式执行行：仅在收到 reasoning 时插入，不在发送时占位；左侧箭头收起向右、展开向下；展开后完整展示灰色流式正文，取消 180px 渐隐裁切。 |
| v1.18 | 2026-08-18 | 思考展开正文对齐 Cursor 截图字样：14px / 字重 400 / `#999` / 行高 1.55 无衬线灰色段落，按空行分段；箭头紧跟「Thought for Ns」右侧。 |
| v1.19 | 2026-08-18 | 思考流式与正文同路：`message.reasoning.delta` 走 45ms/64 字合批；前端 rAF 追赶、进行中单块 pre-wrap、不做折叠高度动画。 |
| v1.20 | 2026-08-18 | 模型反问用户（短文 + 文末问号 + 是否/还是等）不当失败终态：执行头「等待补充」，不展示「未完成」/错误条；Research 不 persist 假报告、CompletionVerifier 直接 COMPLETED。仍走 `seed_goal_contract`。 |
| v1.21 | 2026-08-18 | 思考进行中标题只显示 shimmer「Thinking」，不带实时秒数；结束后才显示「Thought for Ns」。 |
| v1.22 | 2026-08-18 | 工具轮内部独白（修正计划结构 / `requires:[investigation]` 等）不得作为终答黑字流式展示；等本轮 tool_calls 确定后走 commentary 过滤。思考折叠里仍可看 reasoning。 |
| v1.23 | 2026-08-18 | 深度研究对话总结与报告卡同一套 Markdown 规范：助手终答不得掉进用户纯文本气泡；剥「现在合成报告」过程句；正文 `[n]` 编译为 cite-chip。 |
| v1.24 | 2026-08-18 | 计划确认收成一张卡：完整计划报告 + 步骤验收 + 卡内补充输入 +「开始执行」。不再展示模型自造的编号选项（转成 Word / 就这样交付）和「改这一步」。短 intro 不得覆盖已生成的计划报告；工具轮后超过短窗口的正文当场流式画出，避免「正在根据检索结果推进」假死。 |
| v1.26 | 2026-08-18 | 计划模式流式正文写入「执行计划」卡：超过短窗口的计划 Markdown 不再当黑字气泡；打字机终态不得把计划倒回正文；确认前状态为「正在生成计划」。 |
| v1.27 | 2026-08-18 | PPT 续做死锁：`force_product_tool_choice` 不得抽走当前计划步 `requires` 的 investigate 工具；一次性沙箱里照片未注入时允许重新 `search_web`/`fetch_ppt_asset`；artifact_coding 常驻 `update_plan`；同一 Worker 不得把已 leased 给自己的过期 Job 重入成第二套 Loop。 |
| v1.28 | 2026-08-18 | Research 对话正文就是交付物：终答不得走 `scrub_false_file_delivery_claim`（「写研究报告」会把整篇 Markdown 折成「文件还没有成功写入我的文件」）。编译器从折平正文里恢复 `#`/`##` 标题。 |
| v1.29 | 2026-08-18 | 对齐 Codex Plan：用户确认一次后执行中 `update_plan`（含增删步骤）直接落库推进，任务协作可标 diverged；不再挂起「批准修订」。用户改方向走输入框，不二次确认结构。 |
| v1.30 | 2026-08-18 | Deep Research 终态保留执行过程 + 研究报告白卡；对话总结气泡（同一段 Markdown）不再并列展示。 |
| v1.31 | 2026-08-18 | 未完成产物任务连续性：任务≠Run；「继续」继承文件 GoalContract；PPTD 工程检查点跨 Run 恢复；Progress Policy 保证可推进工具面；Job/Run 单飞与诚实失败。禁止 PPT 第二内核，禁止会话级长驻容器。 |
| v1.32 | 2026-08-18 | 计划模式硬闸：做出的计划必须等用户点「开始执行」才动手。模型漏调 `ask_user_choice` 时平台强制挂起；确认卡只允许单选项「开始执行」；同意后才把 `capability_scope` 升到 default。执行中 `update_plan` 仍不二次确认。计划卡生成中先出骨架条，整份报告齐了再整卡入场，不把字打进卡片。 |
| v1.33 | 2026-08-18 | 计划卡视觉对齐研究报告白卡：蓝文档图标、16px 圆角轻阴影、标题栏操作、文稿预览底部渐隐；确认卡仍是同一张卡加「开始执行」。 |
| v1.34 | 2026-08-18 | Deep Research 覆盖检索必须先下发 `tool.started`（及抓取中的 `tool.progress`），不得把检索帧攒到 `search_web` 返回之后才 flush。开场不是模型 thinking：执行头在覆盖检索阶段为「正在检索」，时间线立即出现搜索/深读行。 |
| v1.35 | 2026-08-18 | Deep Research 交付对齐计划卡出场：生成中只出骨架条，报告齐了用 `plan-card-in` 整卡入场；同一段 Markdown 不先作为对话总结气泡输出。执行过程仍保留。反问补充不受影响。 |
| v1.36 | 2026-08-18 | 计划卡与深度研究报告白卡同一套出场：思考中不出灰条；整份计划齐了才整卡跳入（蓝图标、圆角白卡、底部渐隐与圆形下箭头）；点进去看全文。卡下「开始执行计划 / 进行补充」。过程独白不得当成计划卡。 |
| v1.37 | 2026-08-18 | 计划确认动作复用原 `plan-review` 补充框与「开始执行」，不另做「开始执行计划 / 进行补充」按钮。 |
| v1.38 | 2026-08-18 | 计划确认对齐 Codex：点「开始执行」自动发出用户气泡「好，请执行此计划。」并新开助手轮动手；卡内补充后仍停在计划轮，重新生成整份计划卡并再次确认（可执行、再补充或跳过）；「跳过」按当前计划继续。未同意不得动手。 |
| v1.39 | 2026-08-18 | 计划卡「跳过」只保留计划、不执行，随后继续聊天。用户在确认卡下输入「执行计划」「实施此计划」等同意语，同样升权并按计划干活。 |
| v1.40 | 2026-08-18 | 主对话 SSE 活订阅不得把 after=N 的空结果缓存在 API 进程：worker 落库无法作废这份缓存，会导致页面只靠 ping 保活、刷新才看到步骤和研究报告白卡。空闲钟只认 data 帧；研究报告文件一到即出白卡，不要求 loading 先落下。 |
| v1.41 | 2026-08-18 | 计划白卡下方不再重复展示 GoalContract 与步骤验收清单；确认区只留补充框、「跳过」和「开始执行」。任务协作面板的契约条与步骤「验收：」不变。 |
| v1.42 | 2026-08-18 | 计划白卡预览去掉圆形下箭头，研报卡该按钮仍保留。 |
| v1.43 | 2026-08-18 | 计划卡「开始执行」续接把 `resume_value` 传入 `_resume_orchestration`，避免未绑定变量导致 Run 立刻失败。 |
| v1.44 | 2026-08-18 | 未完成产物任务：同 Run 等待确认不得拆沙箱；PPTD 检查点打在活会话上并在 bash/素材后增量保存；续做从线程最新检查点恢复，恢复失败则明示计划 completed 不等于文件还在。 |
| v1.45 | 2026-08-18 | 断电重续：同一 Run 被 Worker 重拉时用本 Run 检查点灌回新沙箱，不要求用户消息是「继续」。 |
| v1.46 | 2026-08-18 | 用户点「开始执行」后立即退出计划模式：关掉前端胶囊，Run `agent_mode` 改为 standard，续接轮改写只读计划约束并放开写工具。补充或跳过仍留在计划模式。 |
| v1.47 | 2026-08-18 | 计划确认卡不得因规划阶段 `update_plan` 标成进行中而丢掉正文；无 Context/Phase 标签的 Markdown 仍进白卡，缺报告时用步骤标题/说明合成预览。 |
| v1.48 | 2026-08-18 | 堵住 `update_plan` 空转：bash 落盘的 Word/`artifact_build`+file_id 可勾交付步（PPT 发布仍要 `artifact_delivery`）；同 Run 上已有文件回执时允许把交付步标完成；降级必须写进工具回执；连续只改计划熔断；产物已落盘不再注入「先整表修订」。 |
| v1.49 | 2026-08-18 | PPT 一次做完：检查点 `source=staging_ckpt` 以适配 VARCHAR(16)；未发布 pptx 时 token/墙钟不得 `tool_choice=none`，禁止把「下一步写 pages」当终态。 |
| v1.50 | 2026-08-18 | 长跑停止条件对齐 Claude Code：防无限循环≠任务结束，预算不足≠失败，上下文爆炸≠Run 结束。文件未交付且预算见底进入 execution mode（禁搜/禁重规划，仍允许 bash/写盘/发布）。 |
| v1.51 | 2026-08-18 | 三层文件域：沙箱一次性；会话工作区保存未完成工程与素材（主键 thread_id，不进「我的文件」）；「我的文件」只留交付物与用户上传。发布是工作区→我的文件的唯一闸。 |
| v1.52 | 2026-08-18 | 工作区前端：主对话顶栏第四入口、本对话侧滑抽屉（对齐记忆抽屉）；不进主导航、不进 + 菜单、不进「我的文件」。只读摘要 +「在对话中继续」。 |
| v1.53 | 2026-08-18 | 工作区抽屉只展示已落到工程里的素材（配图、页面预览），不展示目录树/脚本/检查点源文件。 |
| v1.54 | 2026-08-18 | 会话工作区可保留并接纳素材：用户可放入照片；模型可检索/自制后写入同一抽屉；新 Run 灌回后接着做 PPT。Run 断掉不丢素材。成品仍只进「我的文件」。 |
| v1.55 | 2026-08-18 | 工作区状态每轮由 Context Compiler 从活沙箱动态加载（页面/素材清单），不把图片像素塞进对话历史。 |
| v1.56 | 2026-08-18 | 产物任务双轨：思考 token 不挤兑交付保险丝，PPT loop_policy 压过全局 150k/900s，未发布不得 tool_choice=none。Workspace 为源仓库、Sandbox 为工作副本，File Service Pull/Commit 复用 MinIO；顶栏工作区弹层展示用户可见文件。 |
| v1.57 | 2026-08-18 | 「开始执行」解锁必须落盘：HITL `tool_env` 改成 mutate、Run 列/JSON 的 `agent_mode` 与 `capability_scope` 改成 standard/default、调研游标让出写步骤；前端不得被历史 `run.started(agent_mode=plan)` 再次点亮胶囊。Composer 续接与按钮 HTTP 走同一解锁。类型不匹配/空口 completed 的回执不得再把模型赶去 `write_file`。 |
| v1.58 | 2026-08-18 | PPT 发布只拦结构硬错（ZIP 打不开、页数对不上、布局 lint 失败）。视觉审查、精品分、审查缺席或没有 summary 一律带警告写入「我的文件」，禁止因审查未通过卡住已做好的 PPT。 |
| v1.59 | 2026-08-18 | 用户插话或续说「直接交付 PPT」必须当成未完成文件任务的续做，禁止当成新任务从头做。有计划却没有 `update_plan` 时不得拦截 bash/发布；`publish_ppt_artifact` 不受步骤光标/investigate 约束。收尾执行模式不再用计划绑定逼模型去调已拿掉的 `update_plan`。 |
| v1.60 | 2026-08-18 | ppt-studio 导出必须是沙箱现有工具能逐步完成的闭环：注入目录下 `run_export.py --force` 出 PPTX，再 `publish_ppt_artifact` 进「我的文件」。禁止探测 Node/npm、禁止等 Chromium 审图、禁止为页数干等用户确认、禁止因视觉警告重做。 |
| v1.61 | 2026-08-18 | Java 取包丢 `.wasm`、远程 OpenSandbox 镜像也可能没有 `/opt` 引擎。平台在挂载 ppt-studio 时把 `pptd_wasm_bg.wasm` 写入技能目录；模型禁止全盘 `find` 引擎。 |
| v1.62 | 2026-08-18 | PPT 技能包只做工具箱；会话工作区是可编辑工程。新 Run 也会 Pull 工作区，裸图映射到 `media/`。`artifact_coding` 提供工程内 `read_file`/`edit_file`/`write_file`/`glob`（不写「我的文件」）。 |
| v1.63 | 2026-08-18 | 会话工作区对齐 Claude 文件夹：输入框附件和「我的文件」选中件在开跑前拷进工作区；有活沙箱只 overlay 素材。图片进 `media/`。抽屉上传与输入框上传同一份工程。 |
| v1.64 | 2026-08-19 | 运行中改向对齐 Codex：输入框上方任务条「调整方向」，停键不因草稿消失；steer 补丁 GoalContract，有计划时一次 `update_plan` 续做提示（不挡 bash/发布），禁止当成新任务从头做。 |
| v1.65 | 2026-08-19 | 运行中发送默认入队，任务结束后自动发出；「调整方向」在排队文案上才注入当前 Run。停键不因草稿消失。steer 补丁 GoalContract 与 `update_plan` 续做提示不变。 |
| v1.66 | 2026-08-19 | PPT 导出器把元素级 `style: "$title"` 提升到 `content.style`，并把只有 `color` 的背景补成 `{type: solid}`；挂载 ppt-studio 时覆盖仓库里的 local-export 脚本。 |
| v1.67 | 2026-08-19 | PPT 快速交付收敛为「写页 → 导出 → 发布」：`run_export.py` 在导出前把常见模型简写规范化为 PPTD v2，发布保留 ZIP/页数/布局/字体/图片来源硬门禁但不再同步调用独立视觉模型；模型页图自检可选且不得阻塞。方向校正仅在计划与目标零内容交集时提示改计划，禁止因标题未大量复述用户原话而反复整表修订。 |
| v1.68 | 2026-08-19 | 主 Agent 文件可见性收口到本会话工作区、`+` 明确选中件和 RevisionTarget，不再全量镜像「我的文件」。删除按「照片/图片」关键词硬拒绝 `download_url` 的语义闸；Artifact Profile 优先于单句关键词，安全/权限/破坏性与产物结构门禁继续保留。 |
| v1.69 | 2026-08-19 | 目标驱动生命周期：删除轮次/token/总墙钟终止与预算型 `forced_final`/`partial`；预算只作观测。模型、工具、Worker、沙箱和上下文故障保存 RunState/工作区/事件游标并自动恢复同一 Run；Completion Verifier 未通过时回灌结构化缺口 observation，只有真实等待、取消、不可绕过失败或证据允许的阶段成果才产生终态。 |
| v1.70 | 2026-08-20 | 收口目标驱动恢复：恢复 Job 的唤醒原因与退避原子交接；Completion Verifier 不再用反问关键词判完成；结构化终止/等待事实写入 RunState 并由 Context Compiler 提供；动态 `use_skill` 记录实际 Skill 版本/来源并可跨 Worker/沙箱恢复；恢复上下文继续只暴露会话工作区、明确选中文件和 RevisionTarget。历史预算、默认 PPTD、视觉评分和关键词闸描述仅保留为历史记录，不属于当前契约。 |
| v1.71 | 2026-08-20 | Completion Verifier 缺口在同一 `drive_model` Loop 内回灌结构化 observation 并继续当前目标；Worker 恢复只处理模型/工具/沙箱/租约故障。`waiting_user`/`waiting_system` 仍由 finalize 与恢复层收口。 |
| v1.72 | 2026-08-21 | 崩溃续跑消费 `loop_checkpoint`（同一 `drive_model` 游标，不把受理原句当新 turn）。Steer 对齐 Codex 桌面：运行中回车默认注入当前 Run（`expected_run_id` CAS，活租约不入队，未启动旧工具 stale）；⇧⌘Enter 入队。停键仍是取消。不抄 Codex「模型说完即完成」或 Plan 提示词写保护。 |
| v1.73 | 2026-08-21 | 插话改回排队模式：回车默认入队，点「调整方向」才注入。队列发出的下一 Run 带 `resume_source_run_id`，继承 GoalContract/计划/工作区。HITL 挂起写入同一 `loop_checkpoint`。压缩落 `replacement_history` 检查点。生产工具幂等键改走 Dispatcher 公式（含 `goal_revision`）。 |
| v1.75 | 2026-08-21 | Deep Research 覆盖检索只走 `search_web`，取消深读/抓页。停止后「继续」继承研究 Profile 与台账（Codex 续跑）：先公开一句接着做什么，再从断点检索/合成，不得当成新闲聊。研究报告结构卡由 Markdown 正文出场，停止后有报告骨架也要出卡。 |
| v1.77 | 2026-08-21 | 对齐 Codex `update_plan`：计划是逻辑有序清单，数组顺序即执行顺序；线性计划把唯一 in_progress 约束在最早未完成步（显式 depends_on 且 ready 的并行步除外）。任务协作与模型上下文不展示已替换的 invalidated 残骸，0/N 只计现行步骤。不是 DAG 调度器。 |
| v1.78 | 2026-08-21 | 主对话暂时隐藏连接器按钮和顶栏工作区入口，保留原组件便于可逆恢复；后端连接器授权绑定、Tool Registry 挂载、附件镜像、会话工作区 Pull/Commit 与 Context Compiler 注入不变。 |
| v1.79 | 2026-08-21 | 收工对齐 Codex 桌面源码 `run_turn`：模型不再调工具并给出助手回复即 `completed`。删除「缺口回灌同一 Loop / phase=verifying 锁工具」。Verifier 只识别结构化 HITL。 |
| v1.80 | 2026-08-21 | 思考展开正文由 16px 微调为 15px；标题保持 15px，`#999` 颜色、1.55 行高、动画、展开收起和 reasoning 事件契约不变。 |
| v1.81 | 2026-08-21 | 主 Agent 产品名称定为 AXIOM Agent。删除仍会拦住交付的活闸：办公任务禁止 `write_file` 过程脚本；非 PPT「继续」也 Pull 会话工作区；「继续」继承研究报告契约，且上一轮 Research 即使已 `completed` 也保持 `agent_mode=research`。生产循环不再使用 `tool_choice=none`、execution mode 收工具、视觉分拦发布、照片关键词拒 `download_url`、Verifier 回灌同一 Loop。富格式 `write_file` 仍拒绝 `.docx/.pptx` 伪文本（会写成打不开的坏文件，应走 bash）。v1.79 模型停手即 completed 仍在，不等于平台替模型调用 `write_file`。 |
| v1.82 | 2026-08-21 | 对话框放下的图片 / PPT / 粘贴文本进隐藏会话工作区（`/chat/upload` 的 `source=workspace`），不进「我的文件」。我的文件只留 Agent 发布产物，以及用户在该页主动上传的个人文件。 |
| v1.83 | 2026-08-21 | 上下文压缩对齐 Codex：`conversation_compact` 做 mid-turn 活历史替换（`SUMMARY_PREFIX` + 最近用户句，工具回执离窗）；超窗 compact 请求丢最旧非 system 再试。SSE `context.compaction` 过程帧驱动时间线：进行中 `• Compacting context (Ns)` 字符扫光（`codex-rs/tui/src/shimmer.rs`），完成后 `Context compacted`。发送前 `ensure_compacted` 触发时同步等待以便画出过程。 |
| v1.84 | 2026-08-24 | 主对话入口不再自动打开会话：`/center/chat` 落地、刷新、从其他板块返回都停在欢迎/新对话，不恢复 sessionStorage 里的上次 thread，也不打开最近一条。打开会话必须用户点对话历史，或文件页「来自对话」。后台回复条仍回到当前这次对话。 |
| v1.95 | 2026-08-25 | 公开过程叙述对齐 Codex 源码的 preamble / progress update 节奏：由模型根据真实上下文自然生成，相关动作成组，只在新发现、阶段变化或长耗时块前简短承上启下；琐碎读取和无新信息动作保持安静，执行行仍只投影真实工具事件。DeepSeek 首句辅助轮使用同一选定模型的非思考模式以降低首句等待，主任务思考与工具循环不变。 |
| v1.95 | 2026-08-25 | 会话内模型切换对齐 Codex Thread settings：Thread 持久化下一轮模型，Run/HITL/已排队项冻结受理时模型；运行中可修改下一轮并明确显示“本轮/下一轮”；新 Run 注入有界切换说明，按新窗口做发送前压缩预检，直答路径也不得绕过。 |
| v1.96 | 2026-08-25 | 主 Agent 正文统一使用 60fps、按时间与积压自适应的共享流式节奏器；网络全文与视觉全文分离，终态尾段平滑排空，后台页签、减弱动画、恢复和权威全文契约不变。 |
| v1.97 | 2026-08-25 | Codex 式 commentary 不是每工具一句的动作播报：首段用「目标 + 约束 + 下一组动作」给出小型计划，中途只在新发现、阶段切换、路线变化或长耗时块前更新，并把已确认结果、差异/影响与下一步至少连起两项；琐碎读取、工具探测和同动作重试保持安静。Skill 读取以权威目录 ID 为持久化事实；经 ACL/取包成功后才注册受控的第一方语义别名，保证不透明 `extract_*` 的 ppt-studio 在同一 Run 立即解锁 PPTD 工具，选中未读取时仍失败关闭。 |
| v1.98 | 2026-08-25 | `message.commentary` 仍是服务端落库的完整权威事件；实时页面对 preamble 与轮间 note 复用 60fps 节奏器渐进提交，不伪造 token，计划卡、系统占位和历史回放保持整体语义。复杂任务首段可按需写 1–3 句，有实质新信息的阶段更新通常用 2–3 句连起结果、差异/影响和下一步，不压成孤立动作标题。对话态列表底部只保留悬浮输入框占位与有上限的呼吸距离，不再用过大 padding 制造输出与输入框间的空白。 |
| v1.99 | 2026-08-25 | Codex 式可见顺序收口为单次主模型调用：撤销独立 preamble 模型请求，工具轮在确认 `tool_calls` 后按模型本轮真实输出发布 `commentary → reasoning → tool`。前端 SSE 消费增加可见背压，commentary 与 reasoning 各自渐进排空后才处理下一权威事件；真实 reasoning 进行中显示 `Thinking`，完成后才显示带 provider 或客户端实测耗时的 `Thoughts for Ns`，不得与叙述或工具行同帧跳出，也不得出现无耗时的裸 `Thoughts`。普通无工具回答仍保持 `reasoning → final`。 |
| v1.100 | 2026-08-25 | 整轮执行状态横线恢复：运行中按真实 running 动作在「正在思考」与「正在执行」之间切换并持续累计整轮时长，终态显示「已完成」与冻结时长。该行不代替下方每段 `Thinking` / `Thoughts for Ns`，也不生成任何伪执行事件。 |
| v1.101 | 2026-08-25 | Codex 原生 Responses 的 `Message(phase=commentary)` 可在同一响应内先于 reasoning/tool 作为独立 output item 流出；DeepSeek Chat Completions 无此 phase 且 reasoning-first。保留 DeepSeek 时，使用同模型、非思考、超时有界的首句兼容请求产出真实 preamble，主调用仍承担 reasoning、工具选择与最终回答。状态横线使用「状态 时长」无中点格式；终态可折叠总结外的全部过程，最终总结不进入折叠容器。 |
| v1.102 | 2026-08-25 | 主对话从 Chat Completions 切换为 Responses 协议：旧 chat message/tool cursor 在供应商边界转成 Responses input item，工具 schema 转成扁平 function tool；输出按 commentary/final message phase、reasoning summary 和 function call 分流。公开首句兼容请求也改走 `/responses`，不再另调 `/chat/completions`。请求持久化由 Harness 管理，供应商 `store=false`；历史、HITL 恢复和工具回执继续使用单一内部 cursor，不引入第二会话事实源。 |
| v1.103 | 2026-08-25 | NewAPI `v1.0.0-rc.10` 的 DeepSeek adaptor 对 Responses 仍为 `not implemented`；`deepseek-v4-flash` 渠道改用 OpenAI 兼容 adaptor 并显式指向 DeepSeek 官方 Base URL。渠道管理把 Chat / Responses 两个测试收进单一“测试”菜单，Responses 测试通过 `endpoint_type=openai-response` 验证真实协议路径。`deepseek-v4-pro` 与 `deepseek-v4-flash-vision-exp` 不视为 Responses 可用模型。 |
| v1.104 | 2026-08-25 | 产品决策覆盖 v1.103 的 DeepSeek 白名单：DeepSeek 全系强制 Responses，其他主 Agent 模型使用 Chat Completions；reasoning-first preamble 仅是 DeepSeek Responses 的首句兼容层。同步收紧 commit-before-publish，贯通工具 `call_id`，并为 commentary 增加可选证据与下一步字段。 |
| v1.106 | 2026-08-26 | 历史执行轨迹跨环境恢复：Run/Plan/Event 在活动期仍只认 Runtime PG；终态后把已提交事件编译为不可变的消息展示投影，随共享 MySQL 会话保存。本机与服务器使用不同 Runtime PG 时，历史接口优先读当前权威 Run，查不到才回放终态投影，保留真实蓝色执行框、Plan/Research 步骤与思考记录，不伪造进度。 |
| v1.107 | 2026-08-26 | Deep Research 改为当前会话的显式持续 Profile：用户开启后，每个 Run 终态只清理本轮账本，不自动关闭 Research 开关；后续第 2/N 轮仍以 `agent_mode=research` 建 Run，因而始终走同一份研究报告白卡与全屏查看器。用户再次点击、改选 Plan 或离开会话时才关闭。 |
| v1.108 | 2026-08-26 | Profile 在用户按下发送时冻结进 TurnContext，停止收尾、目录回源等异步窗口不得把已选 Plan 静默降成 Standard。Plan 回合若已同时产出完整计划报告并成功提交 `update_plan`，平台立即保留该报告并转 `waiting_confirmation`；执行步骤保持 pending 是等用户批准的正常事实，不得因 `plan_incomplete=true` 重复请求模型改计划或重写报告。 |
| v1.109 | 2026-08-26 | 报告查看样式严格跟随本轮显式 Profile：只有 `agent_mode=plan/research` 可使用蓝色计划/研究报告卡；Standard 即使多次 `search_web`、产出长 Markdown 报告，也保持普通终答样式。`research.progress` 只能从 Research Profile 发出；已落库的 `agent_mode=standard/plan` 必须覆盖旧版污染进度，保证历史刷新后也不误套蓝框。 |
| v1.110 | 2026-08-26 | 初始 Plan 的勘查阶段最多接受两次权威 `update_plan` 提交：首次可先立勘查计划，第二次同步核对后的计划；若同轮已给完整报告则立即确认挂起，否则下一轮关闭勘查工具面，只交付报告并转 `waiting_confirmation`。该转换仅结束“规划”阶段，不宣布任务完成，也不影响用户批准后的真正执行。 |
| v1.111 | 2026-08-26 | 完整 Plan 报告本身就是规划阶段的交付边界。若模型在同一响应里既给出完整报告，又夹带 `search_web` / read / `fetch_tool_result` / `ask_user_choice` 等后续调用，平台必须在发 `tool.started` 前拦住这些惯性动作，保留报告并直接转 `waiting_confirmation`。只含 `update_plan` 的批次先落权威步骤再挂起；用户确认之前不得继续勘查或重写报告。 |
| v1.112 | 2026-08-27 | 排队消息对齐 Codex 左侧手柄重排：拖动项跟随指针，原位保留低对比占位，相邻行以减弱动画友好的短位移平滑让位，松手持久化完整顺序，失败回滚；正文和右侧操作不命中拖拽。队列暂停、Run 已停止/已结束不锁排序，手柄仍支持指针与键盘上/下键等价排序。 |
| v1.113 | 2026-08-27 | 对齐桌面 Codex 的非阻塞 checklist：`update_plan` 只发计划更新，不拦截后续工具/终答；AXIOM 额外用现有 Plan Controller 将真实 ToolObservation 自动绑定、提交并投影到任务协作卡，同时保住卡片实时同步与执行流畅度。整轮组头收敛为「本轮处理中 / 本轮等待回应 / 本轮已完成」，搜索/生成/文件处理等阶段仅由下方真实进度事件表达。 |
| v1.114 | 2026-08-27 | Deep Research 交付边界收口为对话内蓝框报告：停止终态自动保存 Word/HTML 及 `artifact.saved` 重复产物卡；历史研究产物也不再出通用文件卡。复制、Markdown/Word/PDF 导出只从蓝框下载菜单主动触发。 |
| v1.115 | 2026-08-27 | 产品决策覆盖 v1.107：Deep Research 改为一次性 Profile，只在本轮权威 `run.completed` 后自动关闭 composer 开关；停止、partial、failed 和 `waiting_*` 保留 Research 以便继续/重试。已完成消息的 `agent_mode=research` 仍是蓝框报告事实源，历史回放不反向点亮开关，已入队 TurnContext 不被收尾改写。 |
| v1.116 | 2026-08-27 | 主 Agent 传输按真实模型分流：DeepSeek 全系 Responses，其他模型 Chat Completions；Responses 严格终态、实时 delta 与 opaque reasoning cursor 续传。Plan 内容对齐 Codex `<proposed_plan>` 并在调用时绑定步骤。Research 改为“平台最低证据地板 + 模型按具体缺口自适应续搜 + 总预算熔断”，保留抓取正文和统一引用；连续依赖失败同 Run 恢复，缺口耗尽诚实 partial。DeepSeek 可选首句限单次 2 秒，模型流式降级以 `model.connection` 可见收口。 |
| v1.117 | 2026-08-27 | 主 Agent 传输改为能力优先：模型目录明确声明 Responses 能力时直接路由，未声明时先安全探测 Responses，只在零输出/零副作用的明确协议拒绝后以 Chat Completions 流重建请求；DeepSeek 全系始终按 Responses 能力处理，同 Run 持久化协议锁。模型流对网络/超时/408/425/5xx 以约 200ms→3.2s 指数退避最多重连 5 次，界面原位更新 `1/5…5/5`；鉴权、额度/429、参数、上下文、取消和供应商明确终态不重试。Responses 中断尝试的工具项未经 `response.completed` 不执行，用尽重连后发布 `failed` 并停止当前 Loop，不转非流式继续消耗。 |
| v1.118 | 2026-08-27 | 保留「Responses 优先」产品路由，补齐 New API DeepSeek adaptor 的精确协议不兼容兜底：仅结构化 `HTTP 500 + convert_request_failed + not implemented` 在零供应商事件/零公开输出/零工具副作用/无 opaque cursor/未 committed 时立即重建 Chat Completions 流，普通 500 仍重连 5 次。兜底后当前 Run 持久化 `chat_completions` 锁，不写跨 Run 负能力缓存；model_driver 与 plain_turn 同口径。同时恢复 DeepSeek 公开 preamble 为共享 5 秒总预算、最多 2 次修补尝试。 |
| v1.119 | 2026-08-28 | 修正 Research 一次性 Profile 的终态语义：Runtime `status=completed, outcome=partial` 会投影为 `run.partial`，它表示核心报告已交付但存在非关键证据缺口，不是中断态。前端在 completed/partial 两种交付终态都关闭 composer Research，只有 cancelled/failed/waiting 保留；partial 消息仍按 `agent_mode=research` 渲染蓝框报告与证据局限。 |
| v1.120 | 2026-08-28 | 主对话增加阅读优先的滚动契约：用户向上滚动或在生成中展开任一 Thought 后立即暂停自动跟底，近底阈值不得反向抢回滚动权，持续 reasoning、工具步骤和新 Thought 不再抢视口；主动向下回到底部、收起最后一个阅读中的 Thought、显式回到最新消息或发送新消息后恢复。保持完整流式正文，不引入限高、截断或重新聚合。 |
| v1.121 | 2026-08-28 | 修复活动 Run 的历史锚点与 SSE 回放重复：模型在审批/补充边界前已落助手正文时，切走再返回保留并复用同 `run_id` 的最新助手消息；根据权威 `event_cursor` 只补回事件尾段以恢复审批/补充卡，不再可见地从第一步重播，也不再追加第二个「本轮处理中」。 |
| v1.122 | 2026-08-30 | 新增 Provider 额度治理与持久上下文投影的规范目标：每个真实网络请求单独记 physical attempt，Root 按 delta 归集后代与 post-terminal usage，Provider amount 保留原始字符串；区分 Provider cache、本地 LCP 和 Root 总消耗。Harness 成为唯一重试所有者，DeepSeek preamble 使用 `reasoning.effort=none` 且只尝试一次，compaction 最多 3 个 physical attempt。Context 拆为 `StableBasePrompt + ThreadWorldState`，精确时间改为按需 `get_current_time`，full/patch 账本持久化并以 shadow→canary 门禁上线；DeepSeek 仍发完整无状态输入且禁用有状态字段。工具结果冻结 `AppliedToolResultPolicy`，durable store 成功后才可声称可取回，恢复时不重投影。默认仅 `MODEL_USAGE_ENFORCEMENT_MODE=observe`、`THREAD_PROJECTION_MODE=shadow`；H7 尚需源码、迁移与运行时验收，本次文档更新不表示已部署。 |
| v1.123 | 2026-08-30 | 审批生命周期与模型当前动作对齐：`needs_approval` 后若模型改走后续非控制工具批次，旧 pending approval 自动标记为已被替代并从展示队列移除。安全替代路径已交付时不再发过期 `approval.required`、不把 Run 挂成等待确认；最后仍待执行的破坏性命令继续必须经用户确认。 |
| v1.124 | 2026-08-31 | 实现跨 Run canonical Provider 历史：以 MySQL 公开 transcript 指纹授权恢复 tool/reasoning/context items，工具、plain/direct answer 与 resume 共用投影帐本，终答只在权威 assistant 行落库后提交。base/tool schema/transport 切换显式开新 epoch 但保留已授权的工具边界；恢复检查点不再超字符后静默删旧消息，compact 跟随 Run 冻结传输。`ThreadWorldState` 增加 64,000 字符确定性总预算；`runtime_0019_prompt_history` 增加 transcript 指纹与 expected/unexpected reset 指标，连续干净窗口自动限制 `on` 目标为 shadow→10% canary→on。源码和迁移就绪不等于运行进程已加载或真实样本门禁已通过。 |
| v1.125 | 2026-08-31 | 修复执行中切页后步骤从头动画重播：活动 Run 尚无助手行时，不再丢弃附在持久用户输入上的 `execution_trace`；历史投影增加同批 `event_cursor`，前端一次性恢复到当前步骤后仅消费新事件。旧后端兼容路径从 Run State 取尾游标，游标暂不可用时 fail-closed，不再退回 `sequence=0` 可见全量重播。 |
| v1.126 | 2026-08-31 | 修复 Skill 编辑重发丢失：正常发送仍清空 composer 的一次性 Skill；用户编辑已发消息时按该消息的 Skill 快照重发，不读当前或最近其他轮次的选择。用户消息的 Skill 引用元数据新增稳定 ID，保证刷新后仍可恢复；旧历史仅在当前目录名称唯一命中时兼容恢复，服务端仍重验 ACL/启用状态。 |
| v1.127 | 2026-09-01 | 修复执行中切页后历史步骤与计时消失：初始 user 行与 Run 受理同步持久 `run_id`；旧 Run 无消息锚点时，历史响应追加唯一临时 assistant 轨迹投影。前端将完整快照合并到活动锚点，保留截止 `event_cursor` 的全部 Thought/工具/公开说明与 `startedAt`，新 SSE 事件只在其后追加。 |
| v1.128 | 2026-09-01 | 修复切换到新对话窗口后历史列表滞后：左侧「主对话」保持原有 `openNewChat()`/`resetChat()` 语义，原 Run 继续后台执行；顶栏不新增「新对话」。历史抽屉每次打开时重拉服务端会话列表，不刷新整页也能立即点回刚才的活动会话；整页刷新仍不自动打开最近会话。 |
| v1.129 | 2026-09-01 | 修复切页后活动工具步骤被误标失败：前端导航只 abort 本地 SSE 观察者，不发送 Run cancel；恢复活动 Run 快照时保留工具、Thought 与验证步骤的 `running` 状态，等后续 SSE 事件正常收口。仅终态轨迹中的 `running` 孤儿步骤保留「未跑完/中断」提示。 |
| v1.130 | 2026-09-01 | 新增主对话内置「演示文稿助手」presentation 预设：欢迎页第二张卡和智能体广场使用稳定内置入口；点击不创建空 Thread，首次发送才持久化 `origin=presentation`，历史据此恢复专属页。执行仍唯一进入 AXIOM Agent Harness，服务端每轮强制从权威目录加载 `ppt-studio`，并在真实 Tool Registry 中移除 Skill 选择；失败时明确报错，不降级普通主对话。 |
| v1.131 | 2026-09-01 | 演示文稿助手空态恢复主页面式欢迎层级，使用“你好，今天想制作什么演示文稿？”和专属说明；继续隐藏欢迎页智能体卡片区、`@` 与 Skill 入口。运行前将 `ppt-studio` 权威校验与可选记忆/个性化预取解耦，避免非 Skill 服务超时误报，并在计划/HITL 快照中保存 ACL 解析后的真实 Skill ID。 |
| v1.135 | 2026-09-02 | 主对话历史项改为两层信息结构：首层稳定展示智能体图标/名称与时间或运行态，次层独立展示会话标题，悬浮操作不再改变标题宽度；组名已表达今天/昨天/近期时，行内只显示钟点。校园百事通蓝色助手在欢迎态和对话输出后的固定输入框中都必须持续可见。 |
| v1.136 | 2026-09-02 | 演示文稿助手与校园百事通升级为不可删除、可启停并可按角色/部门授权的系统内置应用；统一进入智能体广场目录，点击分别新开 `/center/chat/ppt` 与 `/center/chat/campus`。普通主对话、presentation、campus_services 按 Thread origin 隔离历史、搜索、分页、草稿与恢复；旧会话按已有 origin 直接归类。专属页只复用会话外壳，执行继续唯一进入 AXIOM Agent Harness；权限由服务端覆盖 Run 受理、续接、队列、历史与引用，不能只靠前端隐藏。 |
| v1.137 | 2026-09-02 | 两个系统内置 Harness 应用改为全局唯一目录记录，不再按租户生成副本或限定可见范围；启停与角色/部门 ACL 继续作为全局应用权限事实源，空 ACL 表示所有已登录用户可用。 |
| v1.138 | 2026-09-02 | 演示文稿助手不对用户开放计划模式和深度研究。前端 `presentation_authoring` 隐藏入口、建议条和胶囊；发送、队列派发和 Run 受理强制 standard，禁止进入 Plan/Research Profile。ppt-studio 仍可调用 `update_plan` 作为制作待办，不得据此点亮用户侧计划模式。 |
| v1.139 | 2026-09-02 | 手机与 iPad 的主对话和系统内置 Harness 应用共用同一紧凑对话顶栏与底部输入区。空对话的输入框不得卡在欢迎文案下方；左侧会话历史保持固定抽屉布局，按主 Agent 的 0.24s 曲线滑入并配合 0.18s 遮罩渐变。 |
| v1.140 | 2026-09-02 | 纠正内置 Harness 应用的目录责任：代码只提供 `/center/chat/ppt` 与 `/center/chat/campus` 独立页面及固定预设；管理员手工新增 `external` 应用记录并维护名称、图标、启停与角色/部门 ACL。取消迁移播种、懒修复、租户副本和不可删除约束；记录可删除，重新新增同路径即恢复。广场只消费原应用目录，并展示实际创建人的姓名与头像。 |
| v1.141 | 2026-09-02 | 智能体广场与主对话欢迎页卡片区对已授权、已返回的固定路径应用恢复内置智能体照片：管理员未配置 `app_icon` 时回退默认照片，已配置时优先使用配置图标。图片回退只装饰现有记录，不补卡、不改写数据库。入选改按固定路径/预设识别，不依赖可编辑名称；卡片底部创建人头像和姓名仍来自实际创建人。 |
| v1.142 | 2026-09-02 | 管理员未填写应用描述时，固定路径记录回退对应内置默认描述；管理员配置值优先。智能体广场和主对话欢迎页卡片区统一将已授权、已返回的校园百事通与演示文稿助手置于前两位，内部顺序为校园百事通第一、演示文稿助手第二；其他应用相对顺序不变，仍不静态补卡或绕过 ACL。 |
| v1.143 | 2026-09-02 | 校园百事通继续走主对话同一工具循环，只保留 `search_knowledge` 与 `search_web`。先收集已审核知识库和学校官网依据再整合回答，检索步骤对用户可见。知识库原图与官网相关图片可由模型判断以 `[图N]` 输出，不得使用非官方配图或为此新增工具。 |
| v1.144 | 2026-09-02 | 修复广场卡片同一创建人显示不同名称：兼容 `create_by` 中的用户 ID 和登录名，归一到 `sys_user` 的真实姓名与头像。移除智能体广场卡片标题旁的“外部”视觉标签；应用在管理端仍保持 `external` 类型并可正常编辑、授权和删除。 |
| v1.145 | 2026-09-02 | 系统设置的应用新增、编辑与列表筛选统一复用智能体广场 8 项正典能力分类，不再读取可能残留旧值的 `app_category` 通用字典缓存。历史旧分类不批量迁移；管理员编辑保存时必须显式改选标准分类。 |
| v1.146 | 2026-09-02 | 收紧单一终答边界：工具轮中与 function call 同时产出的整篇答案草稿只保留在 Provider 上下文，不发布为 `message.commentary`，终态正文仍是唯一权威答案。`fetch_tool_result` 只接受已经进入当前 Provider 上下文的 durable handle；占位值或编造 handle 在可见工具步骤前私下回给模型纠正。前端对已落库的历史重复草稿以与终答的高重合度做兼容隐藏，不改写历史数据。 |
| v1.147 | 2026-09-02 | 校园百事通开放主对话同源的图片选择、粘贴、拖放、上传与多模态输入，只接受有持久 `file_id` 的常见栅格图片；文档、我的文件、知识库覆盖、对话引用及远程伪造图片继续拒绝。官网域名改为发布必填项，搜索请求先以 `site:` 收窄，结果与配图再按白名单过滤；空白名单失败关闭，不再把第三方网页或图片当官方来源。 |
| v1.148 | 2026-09-02 | 校园图片输入复用主对话视觉能力路由：校园固定模型支持视觉时直接注入原图；固定模型为纯文本时，在持久 Run 建立后调用平台配置的独立视觉模型读取原图，把带来源标记的完整视觉描述回灌给同一 Harness 与文本模型。视觉调用沿用 Run/thread 审计归属，不另建校园识图循环。 |
| v1.149 | 2026-09-03 | 手机与 iPad 点选左侧会话历史后，历史抽屉立即按主 Agent 的 0.24s 曲线滑出并配合 0.18s 遮罩渐变收起，同时加载选中会话；置顶、重命名、删除仍不关闭抽屉。桌面点选仍等加载成功后再关，失败可继续点其它会话。 |
| v1.150 | 2026-09-03 | 校园回答增加面向学生的简短结论、关键信息清单、编号办理步骤与必要提醒；不改写既有正文或凭空补事实。图片与引用随消息展示投影保存并在 Runtime 缺失时恢复，避免换设备打开历史只剩 `[图N]`。校园配图独立成块、移动端保持完整比例；加载失败保留来源和重试，不再静默隐藏整卡。共享 Less 改为各页面自有样式入口引入，消除主壳与独立应用页的外部样式描述符覆盖和 `scoped` 编译报错。 |
| v1.151 | 2026-09-03 | 手机与 iPad 点选会话历史后，主对话和系统内置应用页立即按 0.24s 曲线滑出侧栏并配合 0.18s 遮罩渐变收起，同时加载该会话；不再等消息拉取完成才关抽屉。置顶、重命名、删除仍不关。 |
| v1.152 | 2026-09-03 | 修复校园 Worker 应用预设时清空已验证图片附件的断点；主对话、演示文稿助手、校园百事通复用同一图像预处理入口，并将视觉识别后的状态同步到附件提示与回答约束。增加三入口九组合回归测试，验证原图直传、配置视觉模型委托及失败如实降级；实际服务加载与真实上传验收仍单独确认。 |
| v1.153 | 2026-09-03 | 校园百事通的手机/平板图片添加面板改为内容自适应高度，图片入口使用明确的图片图标和可触控操作行，消除单一入口下的大片留白；保持主对话、演示文稿助手的多级资源面板与共享上传链路不变。 |
| v1.154 | 2026-09-03 | 修复共享流式绘制器对动画帧的无限等待：帧暂停时用有界计时兜底，切后台立即同步权威文本；公开首句、思考正文、绘制屏障与最终回答沿用同一保障。自动跟随及手势监听改为查找真实滚动容器，覆盖没有 `.workspace` 的内置应用外壳；去掉手机输入区重复的底部占位。正常前台流式速度、阅读暂停、事件顺序、Run 完成事实及三个入口的共用 Harness 均不变。 |
| v1.155 | 2026-09-03 | H5 / §7.2：分离权威 reasoning 事件与组件视觉绘制，公开首句和思考的可选动画不得阻塞后续正文、工具或 Run 终态；不新增移动端专用链路。输入框吉祥物统一仅欢迎态显示，校园百事通首条消息发出后隐藏蓝色小球，与主对话和演示文稿助手一致。 |
| v1.156 | 2026-09-03 | H1 / RunStore：已上传图片在待执行输入中按持久 file_id 恢复，移除运行状态里重复的原图 base64，避免状态/事件读写随图像体积放大；原生视觉在共享预处理入口按归属读取原图，纯文本模型仍走既有视觉代读。兼容无 file_id 的旧请求，保留缩略图与如实失败，不建立校园或手机专用链路。 |
| v1.157 | 2026-09-03 | H5 / 公开首句：有效的纯附件消息不再因空文字而跳过 DeepSeek 可选 preamble；首句仅据用户文字和附件元数据说明即将做的事，明确未读取附件内容，禁止猜图或声称识图完成。三入口共用同一实现，保持单次非思考辅助请求与先首句后视觉的顺序，不改变视觉模型配置、推理质量或正文节奏。 |
| v1.158 | 2026-09-03 | 计划卡与研究报告卡去掉预览底部圆形下箭头，电脑、手机和 iPad 共用同一套白卡样式，只保留底部渐隐。全屏纸面与卡片顶栏已展示的文档标题从正文剥掉，连续重复的同一标题也只保留一次；复制和导出仍使用带标题的完整文稿。 |
| v1.159 | 2026-09-03 | H2 / H5 / §6.6.1：定制助手按目录归档身份、UI 和执行策略，通过共用注册入口接入唯一 Harness；保留公开路由、历史/ACL、校园发布快照、文稿固定 Skill 及三个入口共用的视觉、流式和恢复功能。与功能修复会话分支隔离实施，合并前仍须接入其提交并联验，不替代其后端重载与大图实测门禁。 |
| v1.160 | 2026-09-03 | H4 / Plan：修正共享 PPTD 预检对镜像自带 WASM 的误报；PPT 工程工具以真实快照回执区分素材、设计、源稿、导出与持久发布，禁止无关成功工具提前勾选制作/导出步骤；缺少有效画布尺寸时明确要求修正清单，不静默导出裁切稿；失败终答不再回填未经证明的成功摘要。保留唯一 Harness、Run 级沙箱、发布 fail-open 与历史数据，不改变运行终态规则。 |
| v1.161 | 2026-09-03 | H2 / H3 / H5：实现 interview 独立身份与唯一 Harness 接线，新增跨 Run 面试状态、材料题库、证据评分、暂停续练及超长内容分页；迁移支持先验证再接管开发预建表。后端 178 项、前端 16 项及组件布局检查通过；正式应用配置、迁移登记、后端重载与登录后的模型联验待完成。 |
| v1.162 | 2026-09-04 | H2 / H3 / H5：面试正式入口、MySQL 迁移和标准后端已就绪；修复重复推荐、通用首句、过期草稿重发与重答题号，补充同一 Harness 下的提示控制。真实上传至复盘刷新主流程通过，扩展评分、第二身份与部分设备验收单列。面试形象复用主 Agent 球体母版，增加黑西装并保留现有展示组件及动态交互。 |
| v1.163 | 2026-09-04 | H2 / H3 / H5：收紧面试语义与专业评分证据要求，正确回答复验通过，证据不足样本修正后另行复验；修复真实页面历史抽屉层级、长输入区遮挡、折叠侧栏及恢复完成后的面试快照同步。真实页面三档响应式通过，前端会话 13 项通过；恢复简历板并同步静态与动态形象。第二身份联验与共享历史时长异常单列。 |
| v1.164 | 2026-09-04 | H3 / H5：证据不足独立首答复验通过，面试验收矩阵 17 项通过、1 项等待第二身份。Runtime 无时区创建时间按 UTC 回放，修复缺少开场事件的恢复场次多出 8 小时，原真实历史页复验通过；三种时区与既有投影回归 33 项通过。不改业务记录、终态或租约。 |
| v1.165 | 2026-09-04 | H2 / H3 / H5：整理面试材料与题量设置，增加常驻题目/反馈入口、主问题记录导航及复盘下载；暂停/结束收起输入，继续/重答恢复。新场次自动保存 URL 并支持生成中刷新续接；评分与改进重点直接可见。文字复盘规则收紧后真实复验通过，首答保持不变。前端 46 项、后端 63 项及 7 个真实增量 Run 通过；响应式、实际下载与启动时迁移处理记录于 QA，第二身份与真机仍单列。 |
| v1.167 | 2026-09-04 | H5：面试页面删去重复介绍与提示，次要设置折叠；逐答反馈和复盘默认突出评分与一个行动重点，完整评价和证据保留在展开内容中。用户本机 Chrome 的 :3200 已核对真实历史记录、展开交互及 390 宽度设备模拟；定向 ESLint 与补丁检查通过。本次仅调整前端展示。 |
| v1.168 | 2026-09-04 | H2 / H3：面试操作规则随冻结动作按需提供，控制动作仅携带提交模板；已有题按 ID 原样选择，避免复制题目导致不可变校验失败后重复调用。反馈强调简洁重点，三维与原文证据校验保留。71 项领域与接线测试通过；真实时延基于 SSE epoch 与 Provider attempt audit 另记 QA，不以 SQL 混合时区时间差或单样本推断稳定提速比例。 |
| v1.169 | 2026-09-04 | H6：双账号联验发现事件订阅先发 200 后在生成器中拒绝归属，导致客户端等待。公共订阅入口前置 Run 归属与当前应用 ACL 检查，拒绝以 JSON HTTP 状态返回；合法订阅仍复用原共享事件流与恢复游标。 |
| v1.170 | 2026-09-05 | H3 / H7：长材料完整分段摘要、按目标窗口保留首尾约束，失败不推进覆盖，写回拒绝过期基线；预取超时保留已完成召回；记忆逐字来源核对、消息/补充指令溯源、按对象与范围替代、防迟到旧来源覆盖及敏感信息规则更新。复用已有 JSON/来源字段，无新增迁移；源码隔离回归与真实模型验收分开记录，Python 仅存盘，由跑服务的会话重启后验证运行效果。 |
| v1.171 | 2026-09-06 | H2 / §6.3 / §6.4 / §9.3 / §9.4：通用沙箱执行目标入 SSOT，并落地源码：Skill 包完整性（ZIP/字节优先，JSON 文本口不得损坏二进制）、声明式运行时预检（ready 后不重探）、OpenSandbox 失败分类、env_prep 白名单出网与 pip 安装尝试、后台作业适配器、非 PPT `/workspace/tmp/work` 捕获/灌入、同类批量交付打包。第一方 PPTD 路径保持金样；个人 Skill 导入与开放广场下载本轮不做。H2 未勾选项须等 `:3200` 真跑与 Java ZIP 通道后才能宣传已落地。 |
| v1.172 | 2026-09-07 | H2 / §5.3：Deep Research 增加本次 Run 临时研究团队（资料研究员 / 分析员 / 核验员）。成员独立上下文复用 `drive_model`，只开放研究只读 `search_web`，空 `run_id` 避免写主循环检查点；公开 SSE `research.team` 仅投影 allowlist。团队失败不得阻断覆盖检索与蓝框报告。前端仅新增头像状态与可展开成员详情，报告查看/导出和其他样式不改。试验分支 `wsr-research-team`，合并前不得占用 `wsr` 的 `:8000` / `:3200`。 |
| v1.173 | 2026-09-08 | H2 / §5.3：Grok Heavy 实机观察后细化研究团队：随机名字随本轮持久化、主 Agent 并行协调取证、显式公开消息在成员间传递、按时间保存检索与多轮发言；前端最近三条与全过程两态、独立右侧详情、球身内部波纹与入场/退场过渡。Python 3.11 队列取消改用 timeout 上下文避免吞取消，团队保存增加有界 CAS 退避。定向后端 86 项、前端 35 项通过；真实 Run 与视觉验收见 qa/research-team/verification.md，不能将组件示例视为真实研究结果。 |
| v1.174 | 2026-09-08 | H2 / §5.3：修正团队研究后串行旧覆盖检索的问题。团队返回后直接整合报告，收起全部工具；空工具面保留主模型/Research 交付分支，避免普通回答 fallback 丢失团队证据和蓝框。前端移除重复运行提示，研究开场白位于团队进度之前；右侧详情展开时主区及输入框收窄。研究成员计费审计复用合法 subagent_model 类别并以 research_team scope 区分。定向后端 95 项、前端 35 项通过；最终真实 Run 验收及外部检索限制见 qa/research-team/verification.md。 |
| v1.175 | 2026-09-08 | H2 / §5.3：研究计划由主 Agent 生成并基于取证事实修订，取代新团队的固定四项模板；实时检索证据驱动既有计划面板，保留 topic key、来源与恢复标记。定向后端 99 项通过；真实 SSE/WebSocket 选型 Run 从 6 项修订为 7 项，讨论后无新工具调用，最终保留蓝框报告。团队详情按钮改为侧栏图标；其余报告与导航视觉不改。 |
| v1.176 | 2026-09-08 | H2 / §5.3：修复研究材料被公开简报篇幅压缩、固定一轮互审过早成稿的问题。增加完整材料提交、正文读取、同团队质量协调与定向补证，保留恢复和取消边界；扩充证据正文且保持引用对应，同一来源支持多个主题。报告要求充分展开证据、机制、比较和适用边界；真实验收另见 qa/research-team/depth-verification.md。 |
| v1.177 | 2026-09-08 | H2 / §5.3：普通对话保持主搜索故障后才调用 DeepSeek；Deep Research 在同一 Run 内由主 Provider 与 DeepSeek 并行召回、交错合并并按规范 URL 去重。研究团队共用默认 3 次 DeepSeek 协同预算，额度耗尽后恢复普通故障兜底；搜索摘要与已读正文继续分级，供应商状态进入工具回执。未改变最终蓝框报告。源码回归已验证，服务重启后的真实 Provider Run 另行验收。 |
| v1.178 | 2026-09-08 | H2 / §5.3：报告核验独立传递用户原话，阻止研究材料误触发记忆写入声明校验；已持久化的引用拒绝进入共享失败终态，避免恢复后反复触发同一拒绝。保留终态 CAS、取消优先及临时故障恢复；源码测试与服务加载分别验证。 |
| v1.179 | 2026-09-08 | H2 / §5.3：研究按问题收窄范围，实时协作后集中核对，取消全员重复互审和固定二次改计划；补证限一轮两项，24 次取证请求。根 Run 持久化 10 分钟截止时间并预留 3 分钟成稿，恢复不重新计时；时限终态及真实交付分别验收，蓝框报告与引用门禁保留。 |
| v1.180 | 2026-09-08 | H2 / §5.3：DeepSeek 搜索改走发起人 NewAPI Key 的 Responses 原生 `web_search`，拒绝把 Messages 客户端 `tool_use` 误判为已搜索；只接收服务端搜索动作中的可引用 URL。逐 Provider 状态仅进入私有回执与审计，用户界面保持原样；Run 级三次协同预算与最终蓝框报告不变。 |
| v1.181 | 2026-09-08 | H2 / §5.3：修复成稿已完成但发表前核验固定 60 秒超时导致整轮失败。持久保存草稿，核验利用剩余预算并预留交付时间；超时以实际来源摘录与缺口交付部分蓝框报告，原模型草稿不冒充已核验结论。共享完成校验识别预算部分交付，不因旧搜索故障重启研究；取消、目标版本与落库失败边界保留。 |
| v1.182 | 2026-09-09 | H5 / §5.3 / §7.2：研究终稿绕过逐字播放，权威终态覆盖滞后的 loading，修复团队已收起而报告卡仍隐藏。报告内部恢复连续文稿、分节段落与对比表格，链接留在参考来源入口；预算部分交付以材料进度和缺口代替网页原文堆叠。前端故障回归、后端核验与呈现测试分别验证，已登录真实会话切页及 Python 服务重载另行验收。 |
| v1.183 | 2026-09-09 | H2 / H4 / §5.3：以冻结范围、成员主题归属、提交即交付和定点核验实现约 10 分钟的有效报告目标；提前结束取证并保留成稿时间，取消根计时到点输出材料盘点的路径。各阶段持久截止、证据与引用校验、取消、改向和单一 Harness 边界保留；运行服务重载及真实研究时延、报告质量单独验收。 |
| v1.184 | 2026-09-09 | H5 / §10：修复任务只有失败回答或空输出锚点时整条会话被历史列表过滤，导致切页、刷新及搜索均找不到的问题。列表以已保存用户消息为入口，保留真实失败与进行中会话；维持空线程过滤、用户和应用域隔离，以及通过历史明确打开的导航约定。 |
| v1.185 | 2026-09-09 | H2 / H4 / §5.3：修复报告阶段超时被误判为引用拒绝并终止后台 Run。移除报告的 180/90 秒业务硬截止，依赖共享模型传输和同 Run 恢复；完整草稿及核验响应先持久化再关闭连接，避免恢复时重复生成。研究范围和取证预算保留，真实重载、后台续跑与交付耗时另行验收。 |
| v1.186 | 2026-09-09 | H4 / H5 / §5.3：将后台恢复 `waiting_system` 与等待用户输入分开，恢复停止按钮、灰色研究开关与持续订阅，后续消息不再误发冲突 Run。研究草稿使用真实投影类型的 JSON 往返保存，修复模型完成后 TypeError 导致的重复生成；正式投影提交仍在权威消息落库之后。 |
| v1.187 | 2026-09-09 | H7 / §4.3：将 New API 结构化 `sensitive_words_detected` 从普通 5xx 瞬时故障中分离。命中策略的模型调用只记录一次真实 Provider 尝试，立即停止当前 Run 并发布安全说明；不重连、不降级 Chat Completions、不进入 Worker 恢复循环。普通 5xx 的有界重连与精确 Responses 不兼容降级边界保持不变。 |
| v1.188 | 2026-09-09 | H5 / §10：终态组头不再把所有结束状态统称为「本轮已完成」；敏感词策略拒绝与用户取消显示「本轮已停止」，普通失败显示「本轮未完成」，部分交付显示「本轮部分完成」。错误说明与模型回答分层展示；没有助手正文的系统失败不显示复制、点赞或点踩。该变更替代 v1.113 的三态组头文案约束，不改变 Run 终态或 New API 策略。 |
| v1.189 | 2026-09-09 | H4 / H5 / §10：修复本地 Worker 退出后 API 单独存活、任务已受理却长期空白的问题。启动入口监护原 Worker 并退避重启，就绪探针包含本地受管 Worker 存活；前端区分 202 受理与真实开始，未领取任务显示等待启动，仍保留原 Run、历史和停止能力。真实进程恢复与模型交付另行验收。 |
| v1.190 | 2026-09-09 | H2 / H4 / H7 / §10：敏感词拒绝轮改为「历史可见、后续上下文隔离」。共享 Harness 在终态 CAS 后幂等标记该 Run 的用户消息与锚点，所有 live transcript 消费者统一排除；公开历史与执行说明继续保留。启动/周期对账修复已存在的污染轮，canonical Provider 历史因 transcript 指纹不匹配而重建。普通失败、取消和研究引用拒绝不受影响；服务重启及真实敏感词→合法追问 E2E 仍是独立验收门。 |
| v1.191 | 2026-09-09 | H2 / §6.2 / §7：主对话新增按需的 `get_user_location` 粗略网络位置能力。Run API 只接受受信代理已解析的 client peer，公网 IP 加密留在活动 Run 中并在终态清除；模型只获得国家/省州/城市/时区与误差声明。本地/内网/保留地址和外部服务故障明确不可定位，不以服务器或沙箱出口冒充；源码回归、服务重启和真实代理链路 E2E 分开验收。 |
| v1.192 | 2026-09-09 | H2 / H4 / H5 / §5.3：修复本地环境变量覆盖已保存搜索引擎、原生搜索沿用 12 秒超时及零证据仍进入成稿的问题。原范围有限补取并保存真实原因，历史回放不再丢失失败信息或暗示切页取消。配置同题对照、真实搜索读取、源码回归与本机历史显示分别验证；服务加载和完整后台研究交付另行验收。 |
| v1.193 | 2026-09-09 | 按用户确认撤回未部署的共享搜索限流网关、配套请求调整及 v1.192 引擎配置对齐，恢复原部署入口与本地环境覆盖。保留 DeepSeek 原生搜索、独立超时和会话恢复修复；此次回退不修改环境文件、配置数据库或重启服务。 |
| v1.194 | 2026-09-09 | H2 / §5.3：搜索顺序改为可配置串行主备，当前已保存 DeepSeek 优先、SearXNG 备用，关闭旧并行混合路径；研究团队共享原生调用额度，失败计入，耗尽后不再绕过额度。私有部署可禁用官方搜索，新部署不自动启用；保留 v1.193 环境覆盖与网关回退。262 项研究/搜索回归及 3 项管理员探测通过，新源码真实链路 22.25 秒取得 4 个候选、3 篇正文，受控主路 429 后真实 SearXNG 1.31 秒取得 10 个候选。运行后端仍为旧代码，重启加载、管理页最终读回及完整研究交付另验；详见 qa/research-team/deepseek-primary-delivery-v194.md。 |
| v1.195 | 2026-09-09 | H5 / §10、H2 / §5.3：研究完成行改用整轮计时与全队去重网页总数，真实历史显示 6分53秒、18 个网页，替代旧 5 次调用文案。分离原生额度跳过与真实搜索失败，备用健康空结果不再被误报为 provider_error；验证码等故障仍保留。前端 44 项、后端 84 项通过，Chrome 原会话展示已验；Python 加载及新任务空结果另验。详见 qa/research-team/research-stats-v195.md。 |
| v1.196 | 2026-09-10 | H5 / §10：研究完成行去掉用户可见的「（去重）」标注，仍显示「耗时 … · 共搜索到 N 个网页」；计数继续用台账 `sourcesFound` 的 URL 去重总数，不改检索或统计口径。 |
| v1.197 | 2026-09-10 | H2 / H4 / H5、§5.3：修复长材料截断合成约束、文本恢复调用丢失原生配对、控制工具绕过工具表及无效 4xx 原样恢复的组合故障；鉴权/额度/访问拒绝同样明确终止，保留上下文与临时故障恢复。报告与核验保持空工具面，允许一次纠正；纠正游标必须保存，恢复/压缩不重置，核验游标不覆盖根 Run。JSON 文本协议共用结构检测，覆盖全部既有恢复工具及正常前缀，排除报告代码示例。未交付成员保留材料并显示 partial；DeepSeek 异常增加有界结构诊断，不改主备顺序或记录正文。后端 513 项、前端 96 项、定向 ESLint 与编译通过；旧串行播报基线失败在 HEAD 可复现，未改业务绕过。组件 HMR 已验；当前 :8000 仍是 04:04 启动的旧服务，加载及新完整研究 E2E 未通过验收。详见 qa/research-team/synthesis-recovery-v197.md。 |
| v1.198 | 2026-09-10 | H2 / H3 / H5、§9.4：按用户新要求开放 + 右侧工作文件夹，新建/选择复用「我的文件」，绑定 Thread 并作为 Agent 读写范围；只归档生成交付文件及用户主动上传的素材，内部下载素材与检查点保持隐藏。源代码、迁移、服务加载及真实读写验收分别记录。 |
| v1.199 | 2026-09-10 | H4 / H5：照片转 PDF Run 启动前记录 worker:ConnectorCryptoError，首次 Provider 调用晚于受理约 73 秒。修复本地密钥并发创建竞态，为新任务加入凭据密钥兼容性领取过滤，接通恢复阶段前端提示。历史故障发生于哪个进程仍待日志定位；共享 Runtime 存在多个客户端来源，相关旧 Worker 加载与真实新 Run 验收仍是独立门禁。 |
| v1.200 | 2026-09-11 | H2 / H5、§5.3：研究成员说明改为实际网页与材料成果，去掉「部分完成」及统一交接承诺；旧记录仅调整展示，内部终态及报告证据要求保留。源码回归、前端真实历史与 Python 加载分别验证。 |
| v1.201 | 2026-09-11 | H2 / H5、§6.6.2：面试结束与重答保留未提交草稿，完成页恢复逐题评分、证据及重答入口；长简历为学生补充说明预留空间，保持材料上限与冻结语义。前端 36 项通过；后端 152 项通过，文件修订和记忆回执两个既有契约用例在独立 HEAD 源码、相同环境下复现失败；仓库当前不存在 `tests/parity/`。Chrome 已验证完成历史的评分、证据展开与原回答定位；新合成场次完成上传、出题、作答、结束、刷新、重答，草稿完整保留，版本 4 回到原题，原回答与三维分数未变。首次重答缺少领域提交回执，原状态保持，再次操作成功；所有测试 Run 均已结束。已查看桌面和 390 宽度模拟，不能代替真机键盘验收。Python 仅存盘，长材料新逻辑须由跑服务会话重启 `run.py` 后再做真实开场验收。 |
| v1.202 | 2026-09-11 | H2 / H5、§6.6.2：面试新评价改为原生百分制，服务端按主问题合并全部有效回答与追问，再按专业 50%、逻辑 30%、表达 20% 生成综合分；首答/辅导后分离，空分不补零，旧五分制记录只读兼容。题号明确已答/当前/待进行，已答回看和当前问题定位可用；反馈收为整场分数与简短建议，依据按维度展开，增加题号、入场和折叠动效并兼容减少动态效果。定向前端 37 项、后端 154 项通过，ESLint、Python 编译与 diff 检查通过。Chrome 正式历史验证旧分数换算、反馈展开与维度切换、当前题定位、草稿保留和 390 px 布局；真实组件合成场景验证综合分、12 题横向导航、动画事件和复盘实际下载。合成组件不代表新模型评分；:8000 仍为 01:54:30 启动的 PID 24414，未加载本轮 Python，整场新汇总与新文案的真实模型联验待跑服务会话重启 run.py 后完成。临时 static 预览入口已清理。 |
| v1.203 | 2026-09-11 | H2 / H5、§6.6.2：按用户再次确认保留聊天式问答，移除顶部岗位/进度/题号整栏。反馈首层仅整场总分加一段 18px 点评，移除小字标签、维度条和按钮栏；正常 16px 的详细依据、记录选择、重答和下载迁入独立弹窗，控制动作收至输入框旁的菜单，暂停/完成时面板保留菜单。分数与点评短时过渡，弹窗兼容减少动态效果。前端相关 37 项及定向 ESLint 通过。Chrome 正式页面已验证顶栏移除、精简反馈、详情打开/Esc 退出与草稿保留；合成组件验证 82/100、18px 点评、390px 零横向溢出、动画事件和完成记录/复盘弹窗入口。本轮仅修改前端；新评分仍待服务会话重启此前保存的 Python 后做真实模型联验，不以合成分数代替实测。 |
| v1.204 | 2026-09-11 | H4：三个内置智能体实测均遭遇旧 Worker 抢领后的凭据解密恢复。带密钥标识的任务采用作用域 Job 调度状态，覆盖初始入队、恢复重排、过期租约和退避，保留兼容 Worker 接管及无标识旧任务；公开 Run 协议不变。新增混合版本队列回归，真实服务加载与三个页面验收记录见 qa/interview-assistant/runtime-recovery-0911.md。 |
| v1.205 | 2026-09-11 | H5、§6.6.2：按用户最新反馈，将面试的分数和点评收进与研究结果一致的报告框，使用白底灰边和中性图标。保留短点评预览，增加报告页头与展开入口，结束后可从页头下载复盘；展开复盘含服务端整场总分。浏览历史反馈不再改变主卡最新点评。报告和阅读区兼容窄屏与减少动态效果，实际模型问答和页面验收见 qa/interview-assistant/runtime-recovery-0911.md。 |
| v1.206 | 2026-09-11 | H2 / H5、§6.6.2：面试报告明确“做得好的地方”和“需要提升的地方”，整场已保存评价分组去重，不丢前面题目的表现，辅导后练习单列。正文取消能力小标题，题目、评分依据与下一步练习按需展开；所有折叠箭头复用主 Agent 的 PremiumChevron，收起向右、展开向下。生成政策改为直接、具体的自然短评，不强凑不足或模板赞美，不暴露内部提交规则；真实新场次文案、下载与页面验收见 qa/interview-assistant/runtime-recovery-0911.md。 |
| v1.207 | 2026-09-11 | 文档与当前工作区源码对齐：整理当前入口/状态与历史验收边界，移除正文中已被替代的 Research 预算、全员互审和报告硬截止，明确 Completion 诊断与实际收尾、Job 与公开 Run 状态、当前迁移头及本地 Worker 监护；补齐文档导航、功能清单、开发和部署说明。保留既有未提交功能与 §15 全部历史行，本轮仅修改文档；不代表重新完成模型 E2E 或生产发布。 |
| v1.208 | 2026-09-11 | H2 / H5、§6.6.2：按用户最终明确的范围，面试过程中只保留问答，整场结束后才显示一个白灰报告。删除逐题反馈组件、题目/维度选择器和菜单入口；报告与下载统一读取整场复盘，不再拼接各题评价或附逐答记录。每轮评价仍保存供整场评分和总结使用，公开正文只输出下一问或结束确认；回归及真实页面证据见 qa/interview-assistant/runtime-recovery-0911.md。 |
| v1.210 | 2026-09-11 | H2 / §6.6.2：面试增加有界的同用户近期已问题目参考、开场角度轮换与原题重复提交拦截；只使用已问的题干，不携带旧答案/分数。策略随开场受理冻结并供恢复复用，前端与评分规则保持原契约。隔离数据库验证相关性、用户/应用隔离、失败回滚、原题更正提交和历史容量边界；重载后的真实相同材料多场出题验收另记于 qa/interview-assistant/question-diversity-0911.md。 |
| v2.0 | 2026-09-19 | 随工作流编排、子智能体委派（`call_subagent`）、智能体推荐（`recommend_agent`）、对外 Agent API 与皮肤系统整体删除（`mysql_0023_drop_skins`、`mysql_0024_drop_orchestration`、`runtime_0024_drop_eval_runs`），删去 §6.5、原 §6.7 皮肤契约及各处委派/推荐从句，§0.2 核对入口与 §4.4 事件表按当前源码更新；主对话、三个内置助手、Research、计划模式、工具网关审批、上下文压缩与记忆机制不变。 |
