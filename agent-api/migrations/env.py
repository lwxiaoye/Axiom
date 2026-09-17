"""双目标 Alembic env（P1 2026-07-17）：MySQL 业务库（-n mysql）与 Runtime PG 域库（-n runtime）。

URL 从应用配置读取（与运行时一致）：
- mysql   → app.core.config.settings.DATABASE_URL，异步驱动转同步（mysql+aiomysql → mysql+pymysql）；
- runtime → app.core.runtime_db._resolve_url()（postgresql+psycopg 同步/异步同驱动，直接可用；
  裸 postgresql:// 显式补 +psycopg，容器内没有 psycopg2）。

各目标独立 version 表（alembic_version_mysql / alembic_version_runtime）与独立 versions 目录
（migrations/versions/<target>/），互不干扰；也不与旧 alembic/ 脚手架（MySQL 采纳式 baseline，
默认 alembic_version 表）冲突。目标选择以 `-n <target>` 为准（version 目录由 ini section 决定，
stock alembic CLI 无法用环境变量切换它）；ALEMBIC_TARGET 环境变量作为交叉校验，两者冲突即拒绝。
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

# Keep manual Alembic runs on Windows aligned with ``python run.py``: the
# optional .env.han override can select the local runtime database, and its
# derived RUNTIME_DATABASE_URL must be visible before settings are imported.
from run import load_local_env

load_local_env()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_VALID_TARGETS = ("mysql", "runtime")


def _resolve_target() -> str:
    env_target = (os.environ.get("ALEMBIC_TARGET") or "").strip().lower()
    section = (config.config_ini_section or "").strip().lower()
    ini_target = section if section in _VALID_TARGETS else ""
    if env_target and env_target not in _VALID_TARGETS:
        raise RuntimeError(f"ALEMBIC_TARGET 必须是 mysql|runtime，得到 {env_target!r}")
    if env_target and ini_target and env_target != ini_target:
        raise RuntimeError(
            f"迁移目标冲突：-n {ini_target} 与 ALEMBIC_TARGET={env_target} 不一致，拒绝执行")
    if not ini_target:
        raise RuntimeError(
            "请用 -n mysql / -n runtime 显式选择迁移目标（version 目录由 ini section 决定；"
            "只设 ALEMBIC_TARGET 环境变量不够）。示例：alembic -c migrations/alembic.ini -n runtime upgrade head")
    return ini_target


def _sync_url(target: str) -> str:
    if target == "mysql":
        from app.core.config import settings
        url = settings.DATABASE_URL
        for prefix in ("mysql+aiomysql://", "mysql+asyncmy://"):
            if url.startswith(prefix):
                return "mysql+pymysql://" + url[len(prefix):]
        return url
    from app.core import runtime_db
    url = runtime_db._resolve_url()
    if not url:
        raise RuntimeError(
            "Runtime 域库未配置（RUNTIME_DATABASE_URL / CHECKPOINT_DATABASE_URL 均为空），"
            "无法执行 runtime 目标迁移")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _target_metadata(target: str):
    """autogenerate 用的 metadata（upgrade 不依赖它）。"""
    if target == "mysql":
        from app.core.database import Base
        import app.models  # noqa: F401  注册所有业务表
        import app.interview_models  # noqa: F401
        return Base.metadata
    from app.core.runtime_db import RuntimeBase
    import app.runtime_models  # noqa: F401  注册所有 Runtime 表
    return RuntimeBase.metadata


def _ensure_runtime_database(url: str) -> None:
    """agent_runtime 库不存在时先建（对齐 runtime_db._ensure_database_exists）：全新环境 +
    MIGRATE_ON_STARTUP=false 时没有其他人建库。best-effort——无权限/已存在时静默继续，
    随后的正式连接是权威判定。"""
    base, dbname = url.rsplit("/", 1)
    dbname = dbname.split("?", 1)[0]
    try:
        admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT",
                              poolclass=pool.NullPool)
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        admin.dispose()
    except Exception:  # noqa: BLE001
        pass


TARGET = _resolve_target()
URL = _sync_url(TARGET)
VERSION_TABLE = f"alembic_version_{TARGET}"
if TARGET == "runtime":
    _ensure_runtime_database(URL)


def run_migrations_offline() -> None:
    context.configure(
        url=URL,
        target_metadata=_target_metadata(TARGET),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(URL, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=_target_metadata(TARGET),
            version_table=VERSION_TABLE,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
