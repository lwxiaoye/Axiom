# 文档索引

2026-09-19 按当前源码整理。当天已整体删除皮肤系统、工作流编排 / 工作台、子智能体委派、对外 Agent API 与智能体推荐（`mysql_0023_drop_skins`、`mysql_0024_drop_orchestration`、`runtime_0024_drop_eval_runs`），文档随之只保留仍存在的东西；带日期的历史报告只证明记录当时的状态，不能替代源码或运行验收。

## 先读

- [`架构概览.md`](架构概览.md)：三层组成（Vue 前端 / agent-api / auth-api）、模块与代码入口、数据所有权、安全边界、已删除清单。
- [`功能清单.md`](功能清单.md)：登录、主对话、三个预置智能体、知识库、Skill 广场、我的文件、管理配置的能力入口，写到文件级。
- [`主对话-Agent-Harness-架构与开发规范.md`](主对话-Agent-Harness-架构与开发规范.md)：主对话唯一执行事实源（SSOT）。涉及 `/center/chat`、三个内置助手页、Run、Plan、Research、工具、上下文、记忆、事件、沙箱或完成验证时先读它。

## 部署与开发

- [`生产部署手册.md`](生产部署手册.md)：单机 docker compose（`docker-compose.local.yml` + `docker-compose.server.yml`）、`deploy/redeploy.sh`、开发叠加 `docker-compose.dev.yml` + `deploy/dev-server.sh`、管理配置页、已知限制。
- [`本地热更新开发工作流.md`](本地热更新开发工作流.md)：本机 Vite `:3200` + `python -u ./run.py` 的日常开发方式。
- [`模型连接配置.md`](模型连接配置.md)：平台对话模型名册与个人覆盖的形状、接口与密钥存储。
- [`AXIOM-本地复刻与配置.md`](AXIOM-本地复刻与配置.md)：环境文件、必要外部服务与上线前检查。
- [`处理连接学校VPN的方法.md`](处理连接学校VPN的方法.md)：本机代理 / VPN 排障。
- [`../agent-api/README.md`](../agent-api/README.md)：agent-api 接口、鉴权、数据面与本机启动。
- [`../agent-api/migrations/README.md`](../agent-api/migrations/README.md)：双目标 Alembic、链头与启动门禁。
- [`../auth-api/README.md`](../auth-api/README.md)：本地认证服务的配置、错误行为与测试。

## 复刻与校园路线

- [`AXIOM-项目拆解与复用路线.md`](AXIOM-项目拆解与复用路线.md)：现有代码能复用什么、校园场景还缺什么。

## 历史报告（只读）

以下记录 2026-09-17 时的状态，其中工作流 / 子智能体 / 皮肤相关内容已于 2026-09-19 移除，正文不再更新：

- [`AXIOM-全项目检查报告-2026-09-17.md`](AXIOM-全项目检查报告-2026-09-17.md)
- [`AXIOM-脱敏与验收.md`](AXIOM-脱敏与验收.md)
- [`AXIOM-AI修复指导书.md`](AXIOM-AI修复指导书.md)

## 维护规则

- 主对话的契约只在 Harness SSOT 维护，不另建第二份计划或交接稿。
- 新文档必须说明用途、状态及与现有事实源的关系；只写代码里确实存在的东西，写到文件 / 目录名级别。
- 一次性调研、截图和审查报告不成为长期事实源；有效结论并入正文后删除原稿，追溯用 Git 历史。
- 删除或重命名文档前先确认没有代码、测试、README 或其他文档引用它。
- 不提交 `.DS_Store`、临时日志、截图或仅供单次会话交接的记录。
