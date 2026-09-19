> 历史文档，记录 2026-09-17 时的状态；其中工作流/子智能体/皮肤相关内容已于 2026-09-19 移除，正文不再更新。

# AXIOM AI 修复指导书

与 [2026-09-17 检查报告](AXIOM-全项目检查报告-2026-09-17.md) 配合使用。目标是逐批把已识别问题修到可验收，不做无边界的“全部重构”。报告里的“待确认”项必须先复现和核对契约。

## 可直接交给 AI 的总指令

```text
你正在 D:\lwxiaoye\Axiom 修复 AXIOM。
先读 AGENTS.md、docs/AXIOM-项目拆解与复用路线.md、
docs/AXIOM-全项目检查报告-2026-09-17.md 和本指导书。
检查 git status，保留用户已有改动，不回退或覆盖它们。
先从指定批次开始，每次只处理具有共同根因的一组问题。

现有技术栈是 Vue 3/TypeScript/Vite + FastAPI；无需改成 Java。
auth-api 只是单管理员兼容服务，不能伪装成完整业务后端。
保留 Java API 契约、现有认证、权限/ACL、租户边界、LICENSE 和第三方声明。
校园助手只读；不增加真实校园事务操作，不声称十个 Agent 已协作。
不得读取或连接归档部署，不打包归档目录、真实 .env、证书或用户数据。
模型凭据留在服务端，不写入 VITE_*、日志、测试快照或 Git。
前端用 pnpm 并保留 pnpm-lock.yaml；不要通过 force 升级、关闭类型检查、
批量 any/ts-ignore、删除失败测试、绕过鉴权或 fake success 来达成“通过”。

每个问题先记录：编号、复现命令、预期与实际、源码根因、影响范围。
确认是产品缺陷还是过期断言、测试隔离或平台兼容问题。
做最小修改，并添加能捕获真实行为回归的测试；不要只测试实现文本。
跑对应聚焦测试、必要构建与 scripts/check_release.py。
输出修改文件、测试结果、仍未验证的环节和恢复方法，更新问题状态。
只有真实验证通过才能标记已解决；不要宣称不存在其他 bug。
涉及模型/知识业务且缺少凭据或资料时，先完成可实现的接口/校验/fixture，
明确列出用户需提供的模型地址、模型名、服务端 Key 和可用官方资料，不虚构它们。
不要重新创建数据库、删除卷或重启其他项目服务。
```

## 分批任务卡

### 批次 A：危险 Markdown 渲染（SEC-01）

从 `src/components/Markdown/src/MarkdownViewer.vue` 及 Skill 详情/广场调用处追踪内容来源。确认 Showdown 允许原始 HTML。对转换后的最终 HTML 使用经审查的 sanitizer 和明确白名单；复用项目内已有安全工具前，核对其配置和依赖版本是否适合这一入口。不要用正则去标签，不要先 sanitize Markdown 再把新增 HTML 绕过过滤。

验收：img/onerror、事件属性、危险 URL、SVG/MathML 等不能执行或逃逸；普通链接、表格、代码和必要图片正常。使用本地测试内容，不向真实用户内容写入攻击载荷。更新锁文件（若依赖变化），跑相关组件测试与生产构建。其他 v-html 入口可另列清单，不擅自扩大到全站视觉重构。

### 批次 B：依赖与权限测试（DEP-01/02、TEST-02）

读取 `output/audit/2026-09-17/pnpm-audit.json` 和 `python-dependency-audit.json`，对每条告警记录 advisory ID、依赖链、runtime/dev、可达性、修复版本。公告是当前审计工具的结果，开始修改时重新审计验证，不依赖猜测版本。

先恢复 Starlette/httpx 路由权限测试，再兼容升级 FastAPI/Starlette、multipart、MCP 等关键依赖。Python requirements 没有保证所有 API 组合兼容；pip check 通过不代表 TestClient 可用。前端编辑器跨主版本可能涉及配置和许可，逐项判断，不能无脑升最新。

验收：未登录拒绝、非管理员拒绝、管理员允许；登录/退出/旧 token 撤销；SSE 不缓冲；上传限制与解析；MCP 取消/错误；lockfile 可重复安装。重扫依赖并明确未修告警原因，开发工具告警和生产链路分开。不得关闭告警规则冒充消除漏洞。

### 批次 C：最小可演示业务（RUN-01/02、AUTH-01）

先读取现有模型表和配置契约，接入一个用户提供的可用模型；模型 API 可在本地或云端，不要求本地部署权重。验证容器内地址可达，设置服务端凭据和用户模型权限。

列出校园检索实际使用的知识业务接口，逐一实现/接入最小读协议，并保留 ACL。当前知识兼容接口 503 应在真正实现后解除；不能返回空结果或自动公开资料来掩盖缺项。导入允许使用的少量官方材料、建立向量集合、设置并发布校园配置。单管理员演示范围明确标注，多用户需求单独实现身份服务。

验收样例：有据可查问题带来源；无资料时明确不确定；跨用户私有资料拒绝；过期资料有可解释处理；上游断开和超时有清晰错误；取消后不继续产出；刷新后线程历史可恢复。需要真实模型/资料的验收与离线 mock 测试分别报告。

### 批次 D：恢复测试基础（TEST-01/03/04/05、BUILD-01）

先集中解决 LONGBLOB 与 SQLite fixture，恢复 62 个 setup 错误；需要 MySQL 语义的测试建立专用可丢弃测试库，不连接当前用户数据。补齐 pepper、DB、tenant_id、rollback 等 fixture。已失败的源码文本断言先看设计契约再更新，关键交互应改为行为测试。迁移测试应验证新增迁移完整性，不能回退到旧最大版本。

类型诊断按活跃路径和公共类型分组：登录/请求层/聊天 → 组件 Form/Table → 后台管理 → 未使用构建插件。列出每批前后诊断数量。不要用全局跳过检查降低基线，不把 1572 个诊断假定为 1572 个独立 bug。

验收：每批 targeted tests 通过，类型诊断减少且无新增类别；全量测试最终复跑。Windows 文件权限、路径、MIME 要用适合平台的验收，不能放松 Linux 安全约束。

### 批次 E：执行和导出（BUG-01/02/03、PPT-01）

用报告中的后端聚焦用例复现：完成报告是否重复总结；子 Agent 失败与 verdict 是否到达父模型；guard 拒绝是否计数并禁用工具；取消是否关闭异步生成器。为每个失败检查 mocks 是否符合当前合同，然后修真实根因，防止误修为直接终止所有复杂任务。

为导出中的 webp 等类型提供可移植映射。PPT wasm 缺失需确认官方构建流程和许可，补齐可重建依赖或向用户明确功能不可用，不造占位二进制。验收包含真实文件可打开、取消/异常资源回收及至少 Windows 当前环境；Linux 未测则明确标注。

### 批次 F：部署收尾（CFG-01、OPS-01/02、PERF-01）

明确公开知识和多租户策略后配置 CORS/ACL/tenant；同步审查旧数据默认公开的迁移影响。安排数据库容器重新创建以应用 loopback 绑定时，先检查连接者并备份，保留卷，不运行 down -v。当前前端、API、auth 已是 loopback，数据库既有容器尚不是。

生产迁移改为可控流程；业务 readiness 与基础健康检查分开。首屏先测量再做压缩、代码分割。验收 docker ps 实际绑定、代理/SSE、重启恢复、数据保留、冷启动性能和不泄露秘密的日志。任何未完成的真实业务验证要继续列为未完成。

## 常用复现命令

以下命令从仓库根目录执行。原始全量测试包含已知失败；退出码非 0 是诊断结果，不能当作修复完成。

```powershell
pnpm typecheck
pnpm exec jest --config jest.config.chat.cjs --runInBand
pnpm audit --prod
node --test scripts/local-auth-login.test.cjs
agent-api/.venv/Scripts/python.exe scripts/audit_backend_offline.py tests -q --tb=short
agent-api/.venv/Scripts/python.exe scripts/audit_backend_offline.py tests/test_stream_tool_loop_persistence.py tests/test_subagent_collab_fixes.py tests/test_html_artifact_service.py -q --tb=short
agent-api/.venv/Scripts/python.exe scripts/check_release.py
docker exec axiom-frontend nginx -t
curl.exe --noproxy "*" http://127.0.0.1:3200/healthz
curl.exe --noproxy "*" http://127.0.0.1:8000/health/ready
```

其他 Jest 配置名称以仓库 `jest.config.*.cjs` 为准；Chat 与部分 Agent 配置覆盖重叠，不重复统计。依赖审计工具建议装独立临时环境，不污染应用 requirements。离线测试运行器只能限制 Python socket，不能作为运行不可信测试的安全沙箱。

## 每批交付模板

```text
问题编号与状态：
复现条件与根因：
修改文件及行为变化：
验收命令与真实结果：
未覆盖/未通过项目：
凭据、数据迁移或用户输入依赖：
恢复方式（不要覆盖用户已有工作）：
下一批问题：
```

只有报告中相应问题的验收证据齐全，才把状态改为“已解决”。允许明确记录未完成，不允许把页面能打开、镜像健康或 mock 成功当作全项目验收。
