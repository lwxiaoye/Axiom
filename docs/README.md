# 文档索引

本文是当前开发、部署与验收资料的入口，按 2026-09-11 工作区核对。正文说明当前契约；带日期的实施计划、截图和验证记录只证明记录时的范围，不能替代最新源码或运行验收。

## AXIOM 复刻资料

- [`AXIOM-项目拆解与复用路线.md`](AXIOM-项目拆解与复用路线.md)：现有校园问答能复用什么、比赛还缺什么。
- [`AXIOM-本地复刻与配置.md`](AXIOM-本地复刻与配置.md)：新仓库位置、环境文件和外部服务。
- [`AXIOM-脱敏与验收.md`](AXIOM-脱敏与验收.md)：品牌、凭据、证书与发布扫描结果。

## 当前事实源

- **[`主对话-Agent-Harness-架构与开发规范.md`](主对话-Agent-Harness-架构与开发规范.md)：主对话唯一执行事实源（SSOT）**。涉及 `/center/chat`、主 Agent、Run、Plan、工具、上下文、记忆、事件、沙箱或完成验证时必须先读本文。
- **[`本地热更新开发工作流.md`](本地热更新开发工作流.md)：日常开发效率 SSOT**。Vite `:3200` + `python -u ./run.py`；一个跑服务的会话统一看护；改代码会话不要自己重启后端。
- [`ai-agent-platform-architecture.md`](ai-agent-platform-architecture.md)：平台各模块的职责边界；主对话实现细节不在此重复定义。
- [`implementation-roadmap.md`](implementation-roadmap.md)：跨模块路线入口；主对话 H0–H7 的状态只在 Harness SSOT 维护。
- [`功能清单.md`](功能清单.md)：当前平台用户侧、工作台、业务流程、AI 资源与系统管理能力的归纳清单；不替代各模块的实现事实源或接口文档。

## 专项资料

- [`生产部署手册.md`](生产部署手册.md)：服务器部署、组件配置、迁移和上线验收；不用于日常热更新。
- [`../agent-api/README.md`](../agent-api/README.md)：API、鉴权、数据面与本机启动。
- [`../agent-api/migrations/README.md`](../agent-api/migrations/README.md)：双目标 Alembic、当前代码迁移头和启动门禁。
- [`workflow-node-help.md`](workflow-node-help.md)：工作流节点、变量、调试与发布前检查。
- [`可移植皮肤包与前端响应式规范.md`](可移植皮肤包与前端响应式规范.md)：两类皮肤包的字段、导入导出与三端边界。
- [`智能体广场能力分类与创建者接口契约.md`](智能体广场能力分类与创建者接口契约.md)：分类值、创建者展示及 Java 接口配合。
- [`处理连接学校VPN的方法.md`](处理连接学校VPN的方法.md)：本机 iNode / Clash 排障。先核对实际 Vite 模式和环境文件，不自行切换 Java 或数据库。
- `sql/`：需要人工执行或核对的菜单、能力注册 SQL。

## 带日期的设计与实施资料

`superpowers/specs/` 与 `superpowers/plans/` 中现存知识库权限/运营统计/原文路径、智能体发布/治理/监测和对话日志资料，是对应日期的设计及执行记录。计划里的未勾选项不等于功能尚未实现，也不能据文件存在宣布验收通过。当前实现分别回查 `src/views/knowledge/`、`src/views/peopleCenter/`、`src/views/workflow/`、`agent-api/app/routers/workflow.py` 与 `agent-api/app/services/workflows/`；Java 接口须在对应 Java 仓库另验。

仓库外或未入库的 `qa/`、`outputs/` 验证资料可能被历史版本说明引用；缺失时保留“证据待补”的结论，不补造测试结果。本轮文档对齐不删除历史资料，也不改写原验收记录。

## 维护规则

- 主对话不再创建第二份开发计划、版本化 Harness 计划或会话交接稿；直接维护唯一 SSOT 的版本和变更记录。
- 新文档必须说明用途、状态及其与现有事实源的关系。
- 新的临时调研、交接稿、一次性截图和审查报告不应成为长期事实源；已完成实施计划的有效契约先并入对应正文，再按仓库清理约定处理，追溯使用 Git 历史。
- 不提交 `.DS_Store`、临时日志、截图或仅供单次会话交接的记录。
- 删除前先确认没有代码、测试、README 或维护指南引用目标文件。
