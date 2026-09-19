# AXIOM 仓库须知（给 AI 编码助手与新接手的工程师）

- 三层：Vue 3 前端（`src/`，主界面在 `src/views/peopleCenter/`）、FastAPI 智能体后端（`agent-api/`）、
  轻量认证服务（`auth-api/`，单管理员 + 学生自助注册）。产品现状与目录说明见 `docs/架构概览.md`，
  部署见 `docs/生产部署手册.md`，开发方式见 `docs/本地热更新开发工作流.md`。
- 智能体全部代码内置、预置在广场（校园百事通 / 演示文稿助手 / 面试助手）；没有工作流编排、
  子智能体、皮肤系统、独立 OCR，这些已于 2026-09-19 整体移除，不要再引入。
- 隐私边界是硬约束：管理员看不到用户的会话、文件、知识库、技能（不存在即 404，不回 403）。
- 前端用 pnpm，保留 `pnpm-lock.yaml`；单测 `pnpm test`（`jest.config.cjs` 分 project）；
  后端 `pytest`（`agent-api/tests/`，需要 `requirements-dev.txt`）。
- 不要提交真实 `.env`、密钥、证书、用户数据、构建产物；`deploy/local/.env` 是 Git 忽略的。
- 提交用 Conventional Commits，中文说明。
