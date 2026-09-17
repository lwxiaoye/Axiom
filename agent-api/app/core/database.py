from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# v2.40：显式加大连接池。默认 pool_size=5 在长任务 + list_files 高频下易耗尽。
# 注意：aiomysql + 当前 SQLAlchemy 组合下 pool_pre_ping 会因 ping(reconnect)
# 签名不兼容直接把启动打挂（TypeError），故不启用 pre_ping。
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# 业务 MySQL 链的 head revision（migrations/versions/mysql/ 的最新 revision，逐字一致）。
# MIGRATE_ON_STARTUP=false 时 main.py 用它做启动门禁：只判 alembic_version_mysql 非空拦不住
# 「两条迁移命令只跑了 -n runtime 那条」——库里停在旧 revision 照样绿灯，与 migrations/README.md
# 承诺的 fail fast 不符。与 runtime_db.RUNTIME_SCHEMA_HEAD 同款语义、同款严格度。
# 新增 mysql 迁移后必须同步改这里；忘了会被 tests/test_schema_head_sync.py 拦下。
MYSQL_SCHEMA_HEAD = "mysql_0022_merge_work_folders"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
