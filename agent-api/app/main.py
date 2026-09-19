import logging
import os
import asyncio
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine, Base, async_session, MYSQL_SCHEMA_HEAD
import app.models  # noqa: F401
import app.interview_models  # noqa: F401
from app.services.platform.migration import migrate_threads_json_if_needed
from app.models import ChatModel, EmbeddingModel

# 让业务模块（app.*）的 INFO 日志可见，便于排查鉴权等流程
# uvicorn 不会给根 logger 装 handler，需自行配置，否则 INFO 会被丢弃
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def _migrate_embedding_model():
    """给 ai_embedding_model 加平台配置列（幂等）。"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        for col, ddl in [
            ("api_key", "ALTER TABLE ai_embedding_model ADD COLUMN api_key VARCHAR(512) NULL"),
            ("base_url", "ALTER TABLE ai_embedding_model ADD COLUMN base_url VARCHAR(512) NULL"),
            ("is_active", "ALTER TABLE ai_embedding_model ADD COLUMN is_active TINYINT DEFAULT 0"),
            ("test_status", "ALTER TABLE ai_embedding_model ADD COLUMN test_status VARCHAR(32) NULL"),
            ("test_message", "ALTER TABLE ai_embedding_model ADD COLUMN test_message VARCHAR(512) NULL"),
            ("last_test_time", "ALTER TABLE ai_embedding_model ADD COLUMN last_test_time DATETIME NULL"),
        ]:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 列已存在


async def _migrate_agent_skill():
    """给 agent_skill_version 加技能包文件树列、agent_skill 加分发/内置标记列（幂等）。"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE agent_skill_version ADD COLUMN package_b64 MEDIUMTEXT NULL",
            # 技能分发（管理员分发到全平台 / 撤回）与内置标记，见 models.AgentSkill 注释
            "ALTER TABLE agent_skill ADD COLUMN distributed_by_user_id VARCHAR(64) NULL",
            "ALTER TABLE agent_skill ADD COLUMN builtin TINYINT NOT NULL DEFAULT 0",
        ]:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 列已存在


async def _migrate_chat_columns():
    """给会话/消息表加置顶与反馈列（幂等）。过渡迁移，随 Alembic 引入后收敛。"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE ai_chat_threads ADD COLUMN pinned SMALLINT DEFAULT 0",
            "ALTER TABLE ai_chat_messages ADD COLUMN feedback VARCHAR(8) NULL",
            # P0 附件生命周期：用户消息附件元数据快照（刷新/历史回放可见）
            "ALTER TABLE ai_chat_messages ADD COLUMN attachments_json TEXT NULL",
            # 图片缩略图落库（preview_url data URL，数十 KB/张 × ≤10 张）：TEXT 64KB 不够，
            # 升 MEDIUMTEXT。MODIFY 幂等——已是 MEDIUMTEXT 时再执行是无害 no-op。
            "ALTER TABLE ai_chat_messages MODIFY COLUMN attachments_json MEDIUMTEXT NULL",
            # 历史遗留列（工作流运行会话 / 子智能体子线程，功能已删）：模型仍映射它们，
            # 主对话按 app_id IS NULL / parent_thread_id IS NULL 过滤存量行。
            "ALTER TABLE ai_chat_threads ADD COLUMN app_id VARCHAR(64) NULL",
            "ALTER TABLE ai_chat_threads ADD COLUMN ai_app_type VARCHAR(32) NULL",
            "ALTER TABLE ai_chat_threads ADD COLUMN parent_thread_id VARCHAR(64) NULL",
            "ALTER TABLE ai_chat_threads ADD COLUMN subagent_id VARCHAR(64) NULL",
            # v1.95：会话下一轮模型 + 最近已受理 Run 模型（模型切换不改活动 Run）
            "ALTER TABLE ai_chat_threads ADD COLUMN model VARCHAR(255) NULL",
            "ALTER TABLE ai_chat_threads ADD COLUMN last_run_model VARCHAR(255) NULL",
            "ALTER TABLE ai_chat_threads ADD COLUMN workspace_folder_id VARCHAR(64) NULL",
            "CREATE INDEX ix_ai_chat_threads_workspace_folder_id ON ai_chat_threads (workspace_folder_id)",
            # P0 刷新丢失修复：消息级 run 归属 + 状态（completed/cancelled/interrupted，NULL=旧数据）
            "ALTER TABLE ai_chat_messages ADD COLUMN run_id VARCHAR(64) NULL",
            "ALTER TABLE ai_chat_messages ADD COLUMN status VARCHAR(16) NULL",
            "ALTER TABLE ai_chat_messages ADD INDEX idx_ai_chat_messages_run_id (run_id)",
            # 终态执行轨迹的不可变展示投影；活动 Run/Plan 仍只认 Runtime PG。
            "ALTER TABLE ai_chat_messages ADD COLUMN execution_trace_json MEDIUMTEXT NULL",
            # 对话日志以该字段精确关联一问一答；生产仍必须由 Alembic 迁移，开发环境兜底补列。
            "ALTER TABLE ai_chat_messages ADD COLUMN turn_id VARCHAR(64) NULL",
            # 知识库检索参数：设置抽屉里可改，此前保存后被静默丢弃（服务端只收 name/description）。
            "ALTER TABLE agent_knowledge_base ADD COLUMN top_k INT NOT NULL DEFAULT 5",
            "ALTER TABLE agent_knowledge_base ADD COLUMN score_threshold DOUBLE NOT NULL DEFAULT 0.3",
            # 知识库级检索方式与混合检索权重：旧库默认纯向量，行为与加列前一致。
            "ALTER TABLE agent_knowledge_base ADD COLUMN retrieval_mode VARCHAR(16) NOT NULL DEFAULT 'VECTOR'",
            "ALTER TABLE agent_knowledge_base ADD COLUMN semantic_weight DOUBLE NOT NULL DEFAULT 0.5",
            "ALTER TABLE agent_knowledge_base ADD COLUMN keyword_weight DOUBLE NOT NULL DEFAULT 0.5",
        ]:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 列已存在


async def _assert_chat_message_columns():
    """关键列启动断言（P2 三批 / P1 迁移门禁配套）：run_id/status/
    execution_trace_json、turn_id 是历史恢复、对账回填与对话日志的核心
    字段。MIGRATE_ON_STARTUP=true 时它兜住 _migrate_chat_columns 宽容 except 吞掉的真失败
    （权限不足/锁超时）；=false 时它兜住「部署忘跑 alembic upgrade」。**无论开关都必须跑**，
    缺列即拒绝启动，而不是容器健康启动后消息落库才开始报错。"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        folder_column = (await conn.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_chat_threads' "
            "AND COLUMN_NAME = 'workspace_folder_id'"
        ))).first()
        if not folder_column:
            raise RuntimeError("ai_chat_threads.workspace_folder_id 缺失，请执行 MySQL Alembic 迁移后重启")
        rows = (await conn.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_chat_messages' "
            "AND COLUMN_NAME IN ('run_id', 'status', 'execution_trace_json', 'turn_id')"
        ))).fetchall()
        missing = {"run_id", "status", "execution_trace_json", "turn_id"} - {str(r[0]) for r in rows}
        if missing:
            raise RuntimeError(
                f"ai_chat_messages 关键列缺失（缺 {sorted(missing)}），拒绝启动——"
                "MIGRATE_ON_STARTUP=true 时请检查数据库 DDL 权限/锁等待；"
                "=false 时请先执行 alembic 迁移（见 migrations/README.md），修复后重启")


async def _migrate_user_file_columns():
    """给「我的文件」表加文件夹归属列（幂等，ADR-047 §6.6）。agent_user_folder 新表由
    create_all 自动建；此处只补 agent_user_file 的 folder_id 列（create_all 不 ALTER 旧表）。"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE agent_user_file ADD COLUMN folder_id VARCHAR(64) NULL",
        ]:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 列已存在


async def _migrate_connector_installation():
    """给连接器绑定表补 installation_id 列（幂等）。

    第一批上线时授权是一段式（OAuth App，令牌即权限）；改成 GitHub App 后多了「安装到账号
    并勾选仓库」这一段，安装 id 必须记下来才知道能读哪些仓库。表本身由 create_all 建，
    但**它不会 ALTER 已经建好的表**——首批部署过的环境缺这一列，这里补上。"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE agent_connector_binding ADD COLUMN installation_id VARCHAR(64) NULL",
            # 2026-07-29：Gmail / Outlook / Canva 三家的 access token 都是小时级过期，
            # 必须存 refresh token 才能续。GitHub App 那条路故意没启用过期，所以第一批不需要。
            "ALTER TABLE agent_connector_binding ADD COLUMN refresh_cipher TEXT NULL",
            "ALTER TABLE agent_connector_binding ADD COLUMN token_expires_at DATETIME NULL",
        ]:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 列已存在


async def _migrate_connector_multi_account():
    """连接器绑定改成**一个 provider 可连多个账户**（幂等）。

    做三件事，每件都单独 try —— 任何一件已经做过都不该阻断后面两件：
      1. 补 account_selected 列（账户级选中开关，默认 1 = 新账户立即可用）
      2. 把 account_login 的 NULL 回填成空串
      3. 唯一键 (user_id, provider) → (user_id, provider, account_login)

    **第 2 步不能省**：MySQL 的唯一索引把每个 NULL 都当作互不相等，
    如果存量行的 account_login 是 NULL，换上新唯一键之后同一个 provider 就能插进
    任意多条 NULL 行——唯一性静默失效，而且不报错。回填成空串后它们才彼此相等。

    **第 3 步的顺序是「先删后加」**：MySQL 不允许两个同名索引，但这两个名字不同，
    所以即使删除失败（比如已经删过）也不影响新增；反过来若先加后删，中途失败会留下
    一张只有旧约束的表，而代码已经按多账户在写——那才是真的坏。
    """
    from sqlalchemy import text
    async with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE agent_connector_binding "
            "ADD COLUMN account_selected SMALLINT NOT NULL DEFAULT 1",
            "UPDATE agent_connector_binding SET account_login = '' "
            "WHERE account_login IS NULL",
            "ALTER TABLE agent_connector_binding DROP INDEX uq_connector_user_provider",
            "ALTER TABLE agent_connector_binding "
            "ADD UNIQUE KEY uq_connector_user_provider_account "
            "(user_id, provider, account_login)",
        ]:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 已经做过


async def _migrate_chat_thread_origin():
    """给会话表补 origin 列（幂等）。

    origin 区分主对话 / 内置助手（presentation、campus_services、interview）/ 旁路会话
    （side_chat）。create_all 不 ALTER 旧表，故此处补列。
    （历史上的 'delegation' 回填随子智能体委派一起删除；存量 delegation 行只是不再被列出。）"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE ai_chat_threads ADD COLUMN origin VARCHAR(16) NULL"
            ))
        except Exception:
            pass  # 列已存在


async def _seed_builtin_app_catalog():
    """为内置智能体补上 app_info 上架记录（幂等）。

    app_info / app_role / app_dept 原属 JeecgBoot(Java) 业务库，Java 下线后无人建表，
    builtin_app_access 查询即抛异常 → 所有内置智能体 503「应用目录暂时不可用」，
    登录后落地 /center/chat/campus 直接白屏。表已改由 create_all 建（见 models.py），
    这里补种数据：每个 BUILTIN_APP_SPECS 按 pc_url(route) 唯一，缺则插入、存在则跳过，
    不覆盖管理员后续的改名/换图标/下架操作。
    """
    from sqlalchemy import text
    from app.services.chat.builtin_app_access import BUILTIN_APP_SPECS, CATALOG_APP_TYPE

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        for spec in BUILTIN_APP_SPECS:
            try:
                existing = (
                    await conn.execute(
                        text("SELECT id FROM app_info WHERE pc_url = :route LIMIT 1"),
                        {"route": spec.route},
                    )
                ).first()
                if existing:
                    continue
                await conn.execute(
                    text(
                        "INSERT INTO app_info (id, app_name, app_remark, app_type, app_icon,"
                        " app_category, pc_url, h5_url, status, order_num, open_type,"
                        " del_flag, create_by)"
                        " VALUES (:id, :name, :remark, :type, :icon, :category, :route,"
                        " :route, '1', :order_num, 'route', 0, 'admin')"
                    ),
                    {
                        "id": f"builtin-{spec.preset}",
                        "name": spec.name,
                        "remark": spec.description,
                        "type": CATALOG_APP_TYPE,
                        "icon": spec.icon,
                        "category": spec.category,
                        "route": spec.route,
                        "order_num": spec.order_num,
                    },
                )
                log.info("已为内置智能体 %s 补建广场上架记录", spec.preset)
            except Exception:
                log.exception("补建 app_info 记录失败：%s", spec.preset)

    # 创建者展示信息；认证仍由 auth-api 负责，这里只为界面显示作者名。
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text(
                    "INSERT IGNORE INTO sys_user (id, username, realname, avatar)"
                    " VALUES ('1', 'admin', '管理员', '')"
                )
            )
        except Exception:
            log.exception("补建 sys_user 管理员档案失败")


async def _seed_builtin_skill_catalog():
    """把系统技能与随代码发布的内置技能包（services/skills/builtin/ppt-studio）注册进
    agent_skill 表（幂等）。

    Skill 目录原由 JeecgBoot(Java) 的 /ai/skill/* 提供，Java 下线后 auth-api 只剩空桩，
    技能广场、@Skill 与演示文稿助手全部落空。目录已改由 agent-api 自持（skill_catalog），
    内置包必须在库表里有一条 source=system 的记录才对目录可见；这里在启动期先播一次，
    worker 进程与首次读目录时还会再幂等一次（skill_catalog.ensure_builtin_skills_seeded）。
    """
    from app.routers.agent_skill import _seed_system_skills

    try:
        await _seed_system_skills()
    except Exception:
        logging.getLogger(__name__).exception("内置技能目录播种失败（首次读目录时会重试）")


async def _migrate_chat_message_sender_type():
    """给消息表补逐消息发送方字段（幂等）。

    不在 DDL 阶段猜测存量委派会话里的人类追问；新写入从源头标记，旧数据由
    读取接口做可删除的兼容降级。create_all 不 ALTER 旧表，故开发环境仍需过渡 DDL。
    """
    from sqlalchemy import text
    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE ai_chat_messages ADD COLUMN sender_type VARCHAR(24) NULL"
            ))
        except Exception:
            pass  # 列已存在


async def _migrate_user_file_version_constraints():
    """给版本表补 (file_id, version_no) 唯一索引（幂等，第二轮评审 P1 并发防护）。

    新库由 create_all 按模型建（模型已含 UniqueConstraint）；此处只补 Phase B 首日
    已由 create_all 建出、但缺约束的存量表。与写路径 FOR UPDATE 行锁构成双保险。
    """
    from sqlalchemy import text
    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE agent_user_file_version "
                "ADD CONSTRAINT uq_user_file_version_no UNIQUE (file_id, version_no)"
            ))
        except Exception:
            pass  # 约束已存在


async def _encrypt_embedding_keys():
    """把 ai_embedding_model.api_key 里的存量明文原地加密成 Fernet 密文（幂等）。

    对话/重排模型的密钥早已密文入库，embedding 是唯一明文的例外：线上那行 DashScope key
    直接躺在表里，一份库备份就能带走。不加新列，复用 api_key 列：新写入走
    embedding_service._store_key，读取走 _read_key（明文/密文都认），这里只负责把历史行
    转成密文。核心逻辑在 embedding_service.encrypt_plaintext_keys（纯函数，便于测试）。

    - 这是 DML 不是 DDL，所以放在 MIGRATE_ON_STARTUP 分支之外无条件执行：生产走 Alembic
      关掉启动期 DDL 时，密钥仍需迁移。
    - 解不开的伪密文（换过 CONNECTOR_SECRET_KEY）只告警不覆盖，管理员在页面重填即覆盖。
    - 任何异常只记日志不阻塞启动：迁移前 _read_key 对明文原样放行，服务不会因此不可用。
    """
    from sqlalchemy import select
    from app.services.knowledge import embedding_service

    log = logging.getLogger(__name__)
    try:
        async with async_session() as session:
            rows = (await session.execute(select(EmbeddingModel))).scalars().all()
            stats = embedding_service.encrypt_plaintext_keys(rows)
            if stats["migrated"]:
                await session.commit()
        log.info(
            "ai_embedding_model.api_key 密文迁移：迁移 %d 行，已是密文 %d 行，跳过 %d 行，空 %d 行",
            stats["migrated"], stats["already"], stats["skipped"], stats["empty"],
        )
    except Exception:  # noqa: BLE001
        log.warning("ai_embedding_model.api_key 密文迁移失败，跳过（下次启动重试）", exc_info=True)


async def _encrypt_platform_config_secrets():
    """把 agent_platform_config 里 web_search / ocr 两行的存量明文密钥字段原地加密（幂等）。

    对话模型（model_connection*）、重排模型（rerank_model）那些行本来就存 api_key_cipher，
    联网搜索与 OCR 的密钥（Serper/Tavily/Firecrawl/Jina/Cohere/视觉模型 key）却是明文躺在
    config_json 里。与 _encrypt_embedding_keys 同一套路：不改结构、只改密钥字段值，新写入走
    platform_config_service._encrypt_secrets，读取走 _decrypt_secrets（明文/密文都认），这里
    只负责把历史行转成密文。核心在 platform_config_service.migrate_plaintext_secrets。

    - DML 不是 DDL，放在 MIGRATE_ON_STARTUP 分支之外无条件执行。
    - 解不开的伪密文（换过 CONNECTOR_SECRET_KEY）只告警不覆盖，管理员在页面重填即覆盖。
    - 任何异常只记日志不阻塞启动：迁移前 _decrypt_secrets 对明文原样放行，服务不会因此不可用。
    """
    from app.services.platform import platform_config_service

    log = logging.getLogger(__name__)
    try:
        stats = await platform_config_service.migrate_plaintext_secrets()
        log.info(
            "agent_platform_config 密钥字段密文迁移：迁移 %d 个字段，已是密文 %d 个，跳过 %d 个，"
            "空 %d 个（扫描 %d 行）",
            stats["migrated"], stats["already"], stats["skipped"], stats["empty"], stats["rows"],
        )
    except Exception:  # noqa: BLE001
        log.warning("agent_platform_config 密钥字段密文迁移失败，跳过（下次启动重试）", exc_info=True)


async def _initialize_model_config() -> None:
    """仅在空表时按部署配置写入初始模型，避免新环境无法对话。"""
    from sqlalchemy import select

    async with async_session() as session:
        has_chat_model = await session.scalar(select(ChatModel.id).limit(1))
        if has_chat_model is None:
            model_ids = [
                item.strip()
                for item in settings.INITIAL_CHAT_MODELS.split(",")
                if item.strip()
            ]
            for index, model_id in enumerate(model_ids):
                session.add(ChatModel(
                    model_id=model_id,
                    display_name=model_id,
                    enabled=1,
                    is_default=1 if index == 0 else 0,
                    sort_order=index,
                ))

        has_embedding_model = await session.scalar(select(EmbeddingModel.id).limit(1))
        if (
            has_embedding_model is None
            and settings.DEFAULT_EMBEDDING_MODEL
            and settings.DEFAULT_EMBEDDING_DIMENSION > 0
        ):
            session.add(EmbeddingModel(
                model_id=settings.DEFAULT_EMBEDDING_MODEL,
                dimension=settings.DEFAULT_EMBEDDING_DIMENSION,
                enabled=1,
                is_default=1,
            ))
        await session.commit()


async def _migrate_model_connection_to_platform():
    """对话模型配置：旧「管理员按用户哈希」记录 → 平台级单记录（幂等）。

    产品要求管理员和普通用户都能直接用智能体，旧存法只对管理员自己生效。核心逻辑在
    model_connection.migrate_legacy_to_platform；这里只负责启动期触发并兜住异常。
    """
    from app.services.platform.model_connection import ensure_migrated
    await ensure_migrated()


# 启动期后台任务的强引用集合（B4 教训）：事件循环只持弱引用，见 lifespan 内 _spawn_startup_task
_startup_tasks: set = set()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    print("Starting Agent API...")
    if settings.MIGRATE_ON_STARTUP:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await migrate_threads_json_if_needed()
        await _migrate_embedding_model()
        await _migrate_agent_skill()
        await _migrate_chat_columns()
        await _migrate_user_file_columns()
        await _migrate_connector_installation()
        await _migrate_user_file_version_constraints()
        await _migrate_connector_multi_account()
        await _migrate_chat_thread_origin()
        await _migrate_chat_message_sender_type()
        await _seed_builtin_app_catalog()
        await _seed_builtin_skill_catalog()
    else:
        # P1 版本化迁移（migrations/README.md）：生产滚动发布注入 MIGRATE_ON_STARTUP=false，
        # schema 由部署前的 `alembic upgrade head` 管理——启动期不再执行任何 DDL，规避大表
        # ALTER 启动锁与多副本同时 DDL 的竞态。关键列断言与 Runtime 探测仍在下方无条件执行。
        # fail-fast（P1 五批）：MySQL 侧同样校验 Alembic revision 已 stamp——只关开关不跑
        # 迁移的库拒绝启动，而不是等到运行期查询才炸。
        # 2026-07-26 补严：此前只判非空——两条迁移命令要分别跑（-n mysql / -n runtime），漏了
        # mysql 那条时库停在旧 revision 照样绿灯放行，新列/新表运行期才炸。改为与 runtime 域
        # （runtime_db.RUNTIME_SCHEMA_HEAD）同款的 head 逐字比对。
        from sqlalchemy import text as _sa_text
        try:
            async with engine.connect() as _conn:
                _rev = (
                    await _conn.execute(_sa_text("SELECT version_num FROM alembic_version_mysql LIMIT 1"))
                ).scalar()
        except Exception as _e:  # noqa: BLE001
            raise RuntimeError(
                "MySQL Alembic 版本表不可读（MIGRATE_ON_STARTUP=false）——"
                "请先执行 `alembic -c migrations/alembic.ini -n mysql upgrade head`"
            ) from _e
        if not _rev:
            raise RuntimeError(
                "MySQL 迁移未执行（alembic_version_mysql 为空），拒绝启动——"
                "请先执行 `alembic -c migrations/alembic.ini -n mysql upgrade head`")
        if str(_rev) != MYSQL_SCHEMA_HEAD:
            raise RuntimeError(
                f"MySQL 迁移版本落后：当前 {_rev}，要求 {MYSQL_SCHEMA_HEAD}。"
                "请先执行 `alembic -c migrations/alembic.ini -n mysql upgrade head`"
                "（两条迁移命令须分别执行，勿只跑 -n runtime）")
        logging.getLogger(__name__).info(
            "MIGRATE_ON_STARTUP=false：跳过启动期 DDL（create_all/_migrate_* 系列），"
            "schema 以 Alembic 迁移为准（mysql revision=%s）", _rev)
    # 无论开关都要跑（DML）：存量明文 embedding key 原地加密，见 _encrypt_embedding_keys
    await _encrypt_embedding_keys()
    # 同上：联网搜索 / OCR 配置里的明文密钥字段原地加密，见 _encrypt_platform_config_secrets
    await _encrypt_platform_config_secrets()
    # 无论开关都要跑（DML）：管理员配的对话模型从旧的「按用户哈希」记录迁成平台默认，
    # 让所有登录用户都能用；幂等，见 model_connection.migrate_legacy_to_platform。
    # 这里只是提前到启动期把日志打出来，首次读取时也会自动做，失败不挡启动。
    await _migrate_model_connection_to_platform()
    # 无论开关都要跑：缺列即 fail fast（见 _assert_chat_message_columns docstring）
    await _assert_chat_message_columns()
    try:
        from app.core.runtime_db import init_runtime_tables
        runtime_ready = await init_runtime_tables(ddl=settings.MIGRATE_ON_STARTUP)
        if settings.RUNTIME_REQUIRED and not runtime_ready:
            # fail-closed（P0）：生产不允许「Runtime 未配置」静默降级——任务模式会退化成
            # 不可恢复的临时图、事件回放/R0/HITL 全失效。直接阻止启动。
            raise RuntimeError(
                "RUNTIME_REQUIRED=true 但 Runtime 域库未配置（RUNTIME_DATABASE_URL/"
                "CHECKPOINT_DATABASE_URL 均为空），拒绝启动")
        # 启动即对账（P0 刷新丢失修复）：①敏感词拒绝轮保留展示但隔离后续上下文；
        # ②僵尸 Run 标 failed（否则 get_active_run 永久返回它们，前端导航转圈）；
        # ③中断轮已流出正文从事件日志回填 MySQL 占位消息并补轨迹锚点——
        # 「执行被中断」在 transcript 可见，不再与「尚未回答」不可区分；④completed Run
        # 的消息缺失按事件全文原 id 重建（双库一致性核销）。
        from app.services.tasks.run_reconcile_service import reconcile_on_startup
        # v2.74: reconcile 可能因 MySQL 慢查询拖死启动（本地真机 20s+ 无响应）。
        # 给启动对账硬超时；失败/超时只记日志，不挡服务接流量。
        try:
            stats = await asyncio.wait_for(reconcile_on_startup(), timeout=12.0)
        except asyncio.TimeoutError:
            logging.getLogger(__name__).warning(
                "启动对账超时（12s），跳过本次对账继续启动"
            )
            stats = {}
        if any(stats.values()):
            logging.getLogger(__name__).info(
                "启动对账：敏感词拒绝轮隔离 %d 条，孤儿 Run %d 条，"
                "中断回填 %d 条，缺失消息重建 %d 条",
                stats.get("policy_quarantined", 0),
                stats.get("orphaned", 0), stats.get("backfilled", 0), stats.get("rebuilt", 0),
            )
    except Exception as e:  # noqa: BLE001
        if settings.RUNTIME_REQUIRED:
            # fail-closed（P0）：初始化失败不进入「静默降级继续接任务」——让编排层看到
            # 启动失败并重试/告警，而不是跑一个任务模式必坏的实例。
            logging.getLogger(__name__).critical(
                "【Runtime 域库初始化失败且 RUNTIME_REQUIRED=true】拒绝启动: %s", e)
            raise
        # 这不是普通降级：Runtime 层失效 = 压缩摘要永不生成（长会话被应急硬裁静默丢消息）、
        # R0 活动任务守卫失效、HITL 无法恢复、引用/记忆/事件回放全关——必须当事故排查
        logging.getLogger(__name__).error(
            "【Runtime 域库不可用】Task Run/压缩/HITL/引用/记忆整层持久化已静默关闭，"
            "请立即检查 agent_runtime 库与连接配置: %s", e
        )
    try:
        # Tool Gateway 崩溃对账（B6 配套，MySQL 侧）：running 残留置 unknown
        from app.services.gateway.tool_gateway import reconcile_orphan_running as gw_reconcile
        await gw_reconcile()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("Tool Gateway 启动对账失败（不阻塞启动）: %s", e)
    await _initialize_model_config()
    # 启动后台任务强引用托管（B4 教训，2026-07-26 收尾）：事件循环对 task 只持弱引用，
    # 裸 create_task 的长生命周期循环（心跳/轮询/清理）一旦被 GC，租约过期会让别的
    # worker 把在跑的 Run 判成僵尸。仓库已有五处正确实现（run_hub._spawn_bg 等），此处对齐。
    def _spawn_startup_task(coro) -> None:
        task = asyncio.create_task(coro)
        _startup_tasks.add(task)
        task.add_done_callback(_startup_tasks.discard)

    from app.services.tasks import runtime_event_bus
    _spawn_startup_task(runtime_event_bus.run_listener())

    if settings.RUN_SWEEP_INTERVAL_SECONDS > 0:
        # 用户 HITL 挂起过期清理（waiting_user/confirmation 超 TTL → failed）；
        # waiting_system/task_recovery 不受墙钟清理，靠恢复 Job 自动重排。
        # + 双库对账（P0-4）：敏感词拒绝轮隔离 + completed Run 消息缺失核销
        async def _run_sweep_loop() -> None:
            from app.services.tasks.run_reconcile_service import (
                ensure_terminal_anchor,
                reconcile_completed_messages,
                reconcile_policy_rejected_messages,
            )
            from app.services.tasks.task_run_service import sweep_expired_waiting
            while True:
                try:
                    swept = await sweep_expired_waiting(settings.RUN_WAITING_TTL_HOURS)
                    if swept:
                        logging.getLogger(__name__).info("HITL 挂起过期清理 %d 条", len(swept))
                    # 过期轮无正文锚点补偿（P0）：挂起轮通常没有助手正文，不补锚整段轨迹丢失；
                    # 同时补录 run.failed 终态帧进事件日志——订阅回放才不会裸 EOF（P0 三批）
                    from app.services.tasks.task_run_service import append_run_event
                    for item in swept:
                        await append_run_event(item["id"], "run.failed",
                                               {"message": "挂起超时未恢复，已过期"})
                        await ensure_terminal_anchor(
                            item["id"], item["thread_id"],
                            placeholder="（任务挂起超时未恢复，已过期）")
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).warning("挂起清理循环异常: %s", e)
                try:
                    quarantined = await reconcile_policy_rejected_messages(
                        window_hours=settings.RECONCILE_SWEEP_WINDOW_HOURS)
                    if quarantined:
                        logging.getLogger(__name__).info(
                            "双库对账隔离敏感词拒绝轮 %d 条", quarantined)
                    rebuilt = await reconcile_completed_messages(
                        window_hours=settings.RECONCILE_SWEEP_WINDOW_HOURS)
                    if rebuilt:
                        logging.getLogger(__name__).info("双库对账重建缺失消息 %d 条", rebuilt)
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).warning("双库对账循环异常: %s", e)
                await asyncio.sleep(settings.RUN_SWEEP_INTERVAL_SECONDS)

        _spawn_startup_task(_run_sweep_loop())
    if settings.RUN_HEARTBEAT_INTERVAL_SECONDS > 0:
        # Run 租约心跳（P1 多 worker）：把本进程持有（owner_instance_id）的活动执行态 Run
        # 周期续 heartbeat_at；其他 worker/启动对账只在租约过期（RUN_LEASE_TTL_SECONDS）后
        # 才把这些行判僵尸——多副本部署不再互杀。间隔必须显著小于 TTL（默认 15s vs 45s）。
        async def _run_heartbeat_loop() -> None:
            from app.services.tasks.task_run_service import heartbeat_owned_runs
            while True:
                try:
                    await heartbeat_owned_runs()
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).warning("Run 租约心跳循环异常: %s", e)
                await asyncio.sleep(settings.RUN_HEARTBEAT_INTERVAL_SECONDS)

        _spawn_startup_task(_run_heartbeat_loop())
    yield
    # Shutdown
    print("Shutting down Agent API...")
    try:
        # Run 级沙箱复用（2026-07-22）：优雅关停时把还活着的容器拆干净，别等它们自毁到点
        from app.services.sandbox import session_pool as sandbox_session_pool
        closed = await sandbox_session_pool.close_all()
        if closed:
            logging.getLogger(__name__).info("关停清理：销毁 %d 个沙箱会话", closed)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("关停清理沙箱会话失败: %s", e)
    await _cancel_background_tasks()


async def _cancel_background_tasks(timeout: float = 5.0) -> None:
    """关停时主动取消所有后台任务并限时等待。

    uvicorn 收到 SIGTERM 后由 asyncio.run 的收尾去取消残余任务并**无限期**等它们退出；
    只要有一个任务吞掉 CancelledError 或在 finally 里等一个永远不回的 I/O，进程就永远
    退不出——热重载表现为「Finished server process」之后再无下文，docker stop 则等到
    SIGKILL。这里先自己取消一遍，超时的任务连协程名和挂起位置一起点名进日志。"""
    me = asyncio.current_task()
    app_dir = os.path.dirname(os.path.abspath(__file__))

    def _ours(task: "asyncio.Task") -> bool:
        # 只动本项目自己起的任务（协程定义在 app/ 下）；uvicorn 的 serve / lifespan
        # 主任务正等着我们返回，取消它们等于把关停流程自己掐断。
        coro = task.get_coro()
        code = getattr(coro, "cr_code", None) or getattr(coro, "gi_code", None)
        filename = getattr(code, "co_filename", "") or ""
        return filename.startswith(app_dir)

    pending = [t for t in asyncio.all_tasks()
               if t is not me and not t.done() and _ours(t)]
    if not pending:
        return
    for task in pending:
        task.cancel()
    _, alive = await asyncio.wait(pending, timeout=timeout)
    if not alive:
        return
    log = logging.getLogger(__name__)
    for task in alive:
        frames = task.get_stack(limit=3)
        where = " <- ".join(f"{f.f_code.co_name}@{os.path.basename(f.f_code.co_filename)}:{f.f_lineno}"
                            for f in frames) or "?"
        log.warning("关停：后台任务 %.1fs 内未响应取消，进程可能退不出: %r 挂在 %s",
                    timeout, task.get_coro(), where)


app = FastAPI(
    title="Agent API",
    description="Agent综合平台后端服务",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include router
app.include_router(api_router, prefix="/agent-api")


@app.get("/")
async def root():
    return {"message": "Agent API is running"}


async def _runtime_db_state() -> str:
    """Runtime PG 探测口径：ok 同时要求连通且 Alembic revision 与代码 head 一致。"""
    from app.core import runtime_db
    if not runtime_db.runtime_enabled():
        return "disabled"
    return "ok" if await runtime_db.readiness_probe() else "error"


@app.get("/health")
async def health():
    """存活探针：恒 200，但暴露 Runtime 域库状态与事件落库失败计数（P0-4 可观测）。
    runtime_db=error 意味着事件回放/R0 守卫/HITL/压缩整层失效，监控应据此告警。"""
    from app.services.tasks.task_run_service import get_event_persist_failures
    state = await _runtime_db_state()
    return {
        "status": "ok" if state != "error" else "degraded",
        "runtime_db": state,
        "event_persist_failures": get_event_persist_failures(),
    }


@app.get("/health/ready")
async def health_ready():
    """就绪探针（P0-4/P0 fail-closed）：Runtime PG 不可达返回 503；RUNTIME_REQUIRED=true
    时「未配置（disabled）」同样 503——生产禁止静默降级接任务。仅裸机本地（required=false）
    才放行 disabled。本地 run.py 受管 Worker 退出时同样不可就绪。"""
    from fastapi.responses import JSONResponse
    from app.core.local_worker import worker_state
    state = await _runtime_db_state()
    local_worker = worker_state()
    not_ready = (
        state == "error"
        or (settings.RUNTIME_REQUIRED and state == "disabled")
        or local_worker == "unavailable"
    )
    if not_ready:
        return JSONResponse(status_code=503, content={"status": "not_ready", "runtime_db": state, "local_worker": local_worker})
    return {"status": "ready", "runtime_db": state, "local_worker": local_worker}
