# 版本化迁移（MySQL 与 Runtime PostgreSQL）

把启动期隐式 DDL（`app/main.py` 的 create_all + `_migrate_*` 系列、`app/core/runtime_db.py`
的裸 SQL 段）收编进 Alembic：正式环境大表 ALTER 不再发生在应用启动锁窗口内，滚动发布多副本
不再各自并发跑 DDL。

2026-09-19 源码核对：MySQL head 为 `mysql_0024_drop_orchestration`，Runtime head 为 `runtime_0024_drop_eval_runs`。这是迁移文件与代码常量的版本，不代表任何运行数据库已经升级。

`mysql_0024_drop_orchestration` / `runtime_0024_drop_eval_runs` 是**删表迁移**（工作流编排整体下线：agent_workflow_* / agent_api_* / agent_external_* / agent_capability_registry / app_info_capability_registry / ai_agent_index_event，以及 Runtime 的 workflow_evaluation_runs）。upgrade 不可逆——downgrade 只重建空表结构，数据不可恢复；上线前先确认这些表里没有还需要的数据（产品口径：线上没有已发布的工作流应用），并按「删表迁移要手工跑」的约定由人执行，不要靠 `MIGRATE_ON_STARTUP=true` 兜底（启动期 create_all 不会删表）。

两条独立迁移链（互不干扰，各自独立 version 表）：

| 目标 | 库 | URL 来源 | versions 目录 | version 表 |
| --- | --- | --- | --- | --- |
| `mysql` | 业务库 | `settings.DATABASE_URL`（aiomysql→pymysql） | `versions/mysql/` | `alembic_version_mysql` |
| `runtime` | Runtime PG 域库 | `runtime_db._resolve_url()`（psycopg） | `versions/runtime/` | `alembic_version_runtime` |

迁移入口会先调用 `run.load_local_env()`，因此与 `run.py` 一样读取 `.env` 和可选的 `.env.han`；Runtime URL 未明确配置时从 checkpoint 地址派生 `agent_runtime`。执行前须确认解析到的目标库，不能按“在本机执行”推断连接的是本机数据库。

## 本地开发

日常不在 Docker 里跑 agent-api。本机 venv：

```bash
cd agent-api
source .venv/bin/activate
alembic -c migrations/alembic.ini -n mysql   upgrade head
alembic -c migrations/alembic.ini -n runtime upgrade head
```

改完 Python 请跑服务的会话重启 `python -u ./run.py`。

## 生产 runbook

服务器部署前先跑迁移（容器内，或任何装有依赖、能连库的环境）：

```bash
# 先构建包含最新 migrations 的新镜像，但不替换线上容器
docker compose build agent-api agent-worker

# 用新镜像启动临时容器执行迁移
docker compose run --rm --no-deps agent-api \
  alembic -c migrations/alembic.ini -n mysql upgrade head

docker compose run --rm --no-deps agent-api \
  alembic -c migrations/alembic.ini -n runtime upgrade head

# 迁移成功后，才替换正式 API / Worker
docker compose up -d --force-recreate agent-api agent-worker
```

然后在选定环境中明确配置 `MIGRATE_ON_STARTUP=false` 启动应用：启动期跳过 DDL，MySQL 与 Runtime 分别校验 version 表中的 revision 必须等于代码 head，并执行聊天关键列校验。不能只检查数据库连通，或假定 Compose 自动把该变量设为 false。

- `-n` 选目标（version 目录由 ini section 决定，stock alembic CLI 无法用环境变量切换）；
  可同时设 `ALEMBIC_TARGET=mysql|runtime` 作交叉校验，两者冲突时 env.py 拒绝执行。
- **存量库**：从实际 revision 顺序 `upgrade head`。迁移可能执行补列、索引调整及历史数据回填，不能描述为“无害 no-op、只落版本标记”。缺少 version 表的旧库先核对实际 schema 与 baseline 的采纳逻辑；不得直接 `stamp head` 跳过历史迁移。
- **全新环境**：baseline 内含 `metadata.create_all`，空库一条命令建齐；runtime 目标由 env.py
  先确保 `agent_runtime` 库存在（best-effort，无权限时需 DBA 预建）。
- **`MIGRATE_ON_STARTUP=true` 的范围**：MySQL 仍有过渡性 `create_all/_migrate_*`，不能据此推断所有新版迁移都已执行。Runtime 只允许空库初始化或已在当前 head 的库执行启动 DDL；旧 revision，或已有 `agent_runs` 却没有 revision 的库会被 `_guard_startup_ddl_revision` 拒绝，必须先走正式迁移。新建空库的自动采纳不能用于旧库升级。

## 新增 DDL 的流程

1. 改 `app/models.py` / `app/runtime_models.py`（模型仍是形状事实源）。
2. 生成迁移（autogenerate 已接对应 metadata，产物必须人工审阅）：
   ```bash
   alembic -c migrations/alembic.ini -n runtime revision --autogenerate -m "add xxx"
   ```
3. 在隔离测试库验证从上一 revision 升级及所需回填，再提交；共享开发库只做经授权的迁移。**不要**用启动期裸 ALTER 或直接 stamp 替代 Alembic。
4. 只核对源码链头时运行 `python -m pytest tests/test_schema_head_sync.py -q`。`alembic ... --sql` 会加载本仓库的 `env.py`；其中 Runtime 目标会尝试确保数据库存在，因此不能把它当成保证无连接、无写入的静态检查。

## 旧 `alembic/` 脚手架已删除（2026-07-26）

根目录旧 `alembic/` + `agent-api/alembic.ini`（MySQL 单目标，versions 0001–0004，默认
`alembic_version` 表）是 Phase 0 的采纳式脚手架，从未在任何运行库上 stamp 激活，也无任何
compose / Dockerfile / CI / 脚本引用。它的 `0001_baseline.downgrade()` 是
`Base.metadata.drop_all(bind)`（执行期现取最新 metadata＝覆盖全部现存业务表），而其 README
同时指引 `alembic stamp 0001_baseline` 与示例命令 `alembic downgrade -1`——由于
`down_revision=None`，照做即**无二次确认清空整个 MySQL 业务库**，且本目录体系表名不同、拦不
住也不报警。已整体删除，`/app` 下不再有兜底 `alembic.ini`：不带 `-c migrations/alembic.ini`
的裸 `alembic` 命令会直接报「找不到配置」而不是静默跑上旧链。

MySQL DDL 一律走本目录 `mysql` 链。历史库若曾误 stamp 过旧链，遗留的 `alembic_version` 表与
本体系无关，可自行清理。

## Schema head 常量

`MIGRATE_ON_STARTUP=false` 的启动门禁会把库里的 revision 与代码常量逐字比对（落后即拒绝启动）：

| 目标 | 常量 | 位置 |
| --- | --- | --- |
| `mysql` | `MYSQL_SCHEMA_HEAD` | `app/core/database.py` |
| `runtime` | `RUNTIME_SCHEMA_HEAD` | `app/core/runtime_db.py` |

MySQL 的两条 `0012` 分支（面试与管理员审计）已经由 `mysql_0013_merge` 合流，再经过对话日志、工作文件夹、删皮肤、删编排迁移到当前 head；不要凭文件编号挑一条分支单独 stamp。历史迁移里创建/修改工作流、Agent API 表的脚本仍保留在链上（全新库会先建后删，存量库行为不变；`mysql_0018` 补了表存在性守卫）。Runtime `0020`–`0022` 给审计表加的 workflow_execution_id / external_* 归属列保留为可空列，编排删除后不再有写入方。

新增迁移后**必须同步改对应常量**；忘了会被 `tests/test_schema_head_sync.py` 拦下（该测试读
`versions/<target>/` 算出真实链头再断言），不会拖到部署期才炸。
