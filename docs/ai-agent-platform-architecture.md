# AXIOM 校园智能体平台架构边界

| 项目 | 内容 |
| --- | --- |
| 文档身份 | 平台模块边界与数据所有权说明 |
| 版本 | v2.1 |
| 更新日期 | 2026-09-11（源码对齐，非部署验收） |
| 主对话事实源 | [`主对话-Agent-Harness-架构与开发规范.md`](主对话-Agent-Harness-架构与开发规范.md) |

> 本文不再定义主对话的循环、工具、计划、事件、上下文或记忆实现。所有主对话目标架构和开发顺序只以 Harness SSOT 为准。

## 1. 平台组成

```text
Vue 用户端 / 管理端
  ├─ /center/chat              主对话 Agent Harness
  ├─ /center/chat/ppt          演示文稿助手（presentation）
  ├─ /center/chat/campus       校园百事通（campus_services）
  ├─ /center/chat/interview    面试助手（interview）
  ├─ /center/agent             智能体广场
  ├─ /center/my-agent          我的智能体
  ├─ /center/skill             Skill 广场
  ├─ /center/knowledge         知识库入口
  ├─ /center/files             我的文件
  └─ /workflow                 工作台与确定性工作流

agent-api
  ├─ Agent Harness             主对话任务执行
  ├─ Workflow Engine           工作台发布流程执行
  ├─ Sandbox Adapters          隔离执行环境
  ├─ File / Artifact Services  用户文件与产物
  ├─ Runtime PostgreSQL        Run、事件、计划、记忆、工具结果与调用审计
  └─ Checkpoint PostgreSQL     独立 LangGraph checkpoint 连接池

Java / MySQL
  ├─ 用户、组织、角色和权限
  ├─ 智能体与应用目录
  ├─ 提示词型 Skill
  ├─ 知识库业务管理
  └─ 校园业务 API

Qdrant
  └─ 知识和可检索资源的向量索引
```

## 2. 模块事实源

| 模块 | 事实源 | 说明 |
| --- | --- | --- |
| 主对话 | `主对话-Agent-Harness-架构与开发规范.md` | 唯一 Agent Loop、Profile、工具、计划、上下文、记忆和事件 |
| 本地开发 | `本地热更新开发工作流.md` | Vite HMR、agent-api 重启和 Docker 边界 |
| 知识库 | 模块代码与定向测试 | 文档、检索、ACL 和管理流程 |
| 子智能体委派 | Harness SSOT 与定向测试 | 子智能体调用和结果协议；不得覆盖 Harness 主循环 |
| 子智能体对话窗 | Harness SSOT 与定向测试 | 独立 UI 和交互状态 |
| 工作台 | `src/views/workflow/` 与 `agent-api/app/routers/workflow.py` 当前契约 | 确定性工作流不等于主对话计划 |

## 3. 核心边界

### 3.1 主对话与工作台

- 主对话使用 Agent Harness：模型逐轮理解目标并选择能力。
- 三个内置助手经身份与运行策略注册表接入同一 Harness，按 `thread.origin` 隔离历史，并从当前应用目录复核 ACL；面试业务状态持久化在 MySQL，单轮 Run 完成不代表整场面试结束。
- 工作台可以使用图结构表达用户配置的确定性流程。
- 当前工作流执行器位于 `services/workflows/workflow_engine.py`，编译/状态定义位于 `services/workflow_runtime/`；Agent 与 toolCall 节点共用 `services/agents/agent_executor.py::run_function_call_loop`。该循环已无固定 30 轮/总墙钟终止，复用 Harness 传输、上下文压缩与隔离结果分页，仍由工作流执行，不具备主对话 Run/Job 的后台恢复契约。
- 工作台的图、节点和调度状态不得泄漏成主对话 Plan 或 Run 协议。
- 主对话委派工作台智能体时，只通过稳定的 Subagent/Capability 契约交互。

### 3.2 主对话与 Java 业务系统

- Java 继续拥有用户、组织、角色、知识库管理、应用目录和校园业务数据。
- 模型不能绕过工具权限直接写业务库。`agent-api` 自有的聊天、工作流、面试和文件服务会通过受控服务写共享 MySQL 中所属的业务表；“共库”不等于禁止 Python 持久化，也不授权任意修改 Java 所属表。
- 外部业务副作用必须通过服务端 Tool/Capability 完成，并保留权限校验、确认、幂等和业务回执。
- 浏览器传入的用户 ID、角色或部门不能替代可信服务端身份。

### 3.3 Skill 与 Capability

- Java 提示词型 Skill 和 Python 可执行技能包不共表、不混用 ID。
- Capability 描述“是否可以被 Agent 执行”；应用目录描述“是否可以被用户发现”，两者不能互相推断。
- 主对话本轮可见能力由 Harness Tool Registry 和 Profile 决定。
- Skill 说明不能绕过 ToolSpec、Policy、Sandbox 或 Approval。

### 3.4 文件、知识与记忆

- 用户文件是可下载、可版本化的持久内容。
- 会话附件是对话上下文资源，不自动进入长期知识库。
- 知识库是有 ACL 的检索数据源，不保存 Run 状态。
- 长期记忆保存稳定用户偏好和长期事实，不保存临时计划、工具回执或业务敏感数据。
- 沙箱工作区是执行环境；只有通过 Artifact/File Service 持久化成功的内容才算交付。

## 4. 数据所有权

| 数据 | 所有者 | 禁止做法 |
| --- | --- | --- |
| 用户、组织、角色 | Java/MySQL | agent-api 自行维护另一套身份真相 |
| 知识库业务数据 | Java/MySQL + Qdrant 索引 | 主对话绕过 ACL 直接检索 |
| 主对话 Run、Plan、Event | agent-api/PostgreSQL | 前端或聊天正文保存第二份状态 |
| 工作流草稿与发布版本 | agent-api 工作台域 | 复用主对话 Plan 表表达节点图 |
| 面试会话、题目、回答与评价 | agent-api 面试领域服务 / MySQL | 用 Plan、长期记忆或 Runtime JSON 另存一套题序和评分 |
| 工作流执行长结果 | Runtime PG 的工具结果表，按工作流执行身份隔离 | 为工作流伪造主对话 Run，或跨用户/执行读取句柄 |
| 用户文件与产物元数据 | File Service | 仅以沙箱路径或模型文案作为交付 |
| 长期用户记忆 | Memory Controller | 模型直接写库或跨用户召回 |

## 5. 跨模块协议

跨模块协议必须满足：

1. 使用项目自有 DTO，不暴露框架内部对象。
2. 结构化状态优先于自然语言推断。
3. 所有副作用带用户身份、Run/Call ID 和幂等键。
4. 事件在状态提交后发布，并可按序列回放。
5. 未知版本和未知事件安全失败，不能猜测执行。
6. 文件、业务操作和研究引用都有可验证回执。

## 6. 安全边界

- `/center` 保持登录保护，不能通过临时免登录路由绕过。
- 主业务接口使用平台认证；Agent API 同时校验可信身份与用户 token。
- 连接器、浏览器和沙箱遵守最小权限与网络边界。
- 外部写入、破坏性操作、权限扩大和不可逆动作需要明确确认。
- Prompt 和 Skill 不是安全边界；权限必须在 Registry、Policy、Gateway 和业务服务端再次校验。

## 7. 架构变更规则

- 主对话变更先更新 Harness SSOT。
- 平台所有权或跨模块契约变化先更新本文。
- 工作台、知识库等模块的局部实现由各自文档维护，不能反向改写主对话架构。
- 已被新事实源替代的设计稿、交接稿和评测快照应删除，历史通过 Git 追溯。
