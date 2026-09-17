"""Persist interview sessions and evidence-backed answer attempts in business MySQL.

Revision ID: mysql_0012_interview_sessions
Revises: mysql_0011_global_sub_skins
Create Date: 2026-09-03
"""

import re

from alembic import context, op
import sqlalchemy as sa

revision = "mysql_0012_interview_sessions"
down_revision = "mysql_0011_global_sub_skins"
branch_labels = None
depends_on = None


_UNIQUES = {
    "agent_interview_session": ("uq_interview_session_thread", ("thread_id",)),
    "agent_interview_turn": ("uq_interview_turn_run", ("run_id",)),
}
_INDEXES = {
    "agent_interview_session": ("ix_agent_interview_session_user_id", ("user_id",)),
    "agent_interview_turn": ("ix_agent_interview_turn_session_id", ("session_id",)),
}
_CLIENT_DEFAULTS = {"version", "status", "pressure_level", "policy_version", "assisted"}


def _table_columns() -> dict[str, list[sa.Column]]:
    # Keep this revision independent of ORM models that may change in later releases.
    return {"agent_interview_session": [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("thread_id", sa.String(64), sa.ForeignKey("ai_chat_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="preparing"),
        sa.Column("pressure_level", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("materials_json", sa.JSON(), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=True),
        sa.Column("question_bank_json", sa.JSON(), nullable=False),
        sa.Column("current_question_json", sa.JSON(), nullable=True),
        sa.Column("review_json", sa.JSON(), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="interview-v1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    ], "agent_interview_turn": [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("agent_interview_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("answer_message_id", sa.Integer(), sa.ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("committed_version", sa.Integer(), nullable=True),
        sa.Column("question_id", sa.String(64), nullable=True),
        sa.Column("question_json", sa.JSON(), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("evaluation_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("assisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
    ]}


def _type_signature(column_type, dialect) -> str:
    signature = str(column_type.compile(dialect=dialect)).upper()
    # MySQL reflection includes the obsolete integer display width; signedness remains significant.
    return re.sub(r"\b(?:INTEGER|INT)(?:\(\d+\))?(?!\w)", "INTEGER", signature)


def _default_signature(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > 1 and value[0] == value[-1] == "'":
        value = value[1:-1].replace("''", "'")
    if value.lower() in {"now()", "current_timestamp", "current_timestamp()"}:
        return "CURRENT_TIMESTAMP"
    return value


def _has_prefix(index: dict) -> bool:
    return bool((index.get("dialect_options") or {}).get("mysql_length"))


def _validate_existing_table(inspector, table: str, columns: list[sa.Column]) -> bool:
    """Return whether a safe, ordinary index must be added; reject incompatible tables."""
    problems = []
    reflected = {column["name"]: column for column in inspector.get_columns(table)}
    expected_names = {column.name for column in columns}
    if set(reflected) != expected_names:
        problems.append(
            f"columns differ (missing={sorted(expected_names - set(reflected))}, "
            f"extra={sorted(set(reflected) - expected_names)})"
        )
    for column in columns:
        actual = reflected.get(column.name)
        if actual is None:
            continue
        expected_type = _type_signature(column.type, inspector.bind.dialect)
        actual_type = _type_signature(actual["type"], inspector.bind.dialect)
        if actual_type != expected_type:
            problems.append(f"{column.name} type {actual_type!r}, expected {expected_type!r}")
        if actual["nullable"] != column.nullable:
            problems.append(f"{column.name} nullable={actual['nullable']}, expected {column.nullable}")
        expected_default = _default_signature(column.server_default.arg if column.server_default is not None else None)
        actual_default = _default_signature(actual.get("default"))
        # create_all uses client defaults for these fields; accepted turns explicitly populate them.
        if actual_default != expected_default and not (column.name in _CLIENT_DEFAULTS and actual_default is None):
            problems.append(f"{column.name} server default differs")

    primary_key = tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ())
    if primary_key != tuple(column.name for column in columns if column.primary_key):
        problems.append(f"primary key differs: {primary_key}")

    indexes = inspector.get_indexes(table)
    unique_constraints = inspector.get_unique_constraints(table)
    unique_columns = {tuple(item.get("column_names") or ()) for item in unique_constraints}
    unique_columns.update(tuple(item.get("column_names") or ()) for item in indexes if item.get("unique"))
    if unique_columns != {_UNIQUES[table][1]} or any(item.get("unique") and _has_prefix(item) for item in indexes):
        problems.append(f"unique columns differ: {sorted(unique_columns, key=repr)}")

    expected_fks = set()
    for column in columns:
        for foreign_key in column.foreign_keys:
            target_table, target_column = foreign_key.target_fullname.rsplit(".", 1)
            expected_fks.add(((column.name,), target_table, (target_column,), "CASCADE"))
    actual_fks = set()
    for foreign_key in inspector.get_foreign_keys(table):
        if foreign_key.get("referred_schema") not in (None, inspector.default_schema_name):
            problems.append(f"foreign key refers to another schema: {foreign_key.get('name')}")
        actual_fks.add((
            tuple(foreign_key.get("constrained_columns") or ()),
            foreign_key.get("referred_table"),
            tuple(foreign_key.get("referred_columns") or ()),
            str((foreign_key.get("options") or {}).get("ondelete", "")).upper(),
        ))
    if actual_fks != expected_fks:
        problems.append("foreign keys or ON DELETE CASCADE differ")

    index_name, index_columns = _INDEXES[table]
    usable_index = any(
        not item.get("unique") and not _has_prefix(item) and tuple(item.get("column_names") or ()) == index_columns
        for item in indexes
    )
    if not usable_index and any(item["name"] == index_name for item in indexes):
        problems.append(f"required index name {index_name!r} already has incompatible columns")
    if problems:
        raise RuntimeError(
            f"Cannot adopt existing {table} for {revision}: " + "; ".join(problems)
            + ". Existing data was not changed; reconcile the schema before retrying."
        )
    return not usable_index


def upgrade() -> None:
    tables = _table_columns()
    existing = set()
    add_indexes = set()
    if not context.is_offline_mode():
        inspector = sa.inspect(op.get_bind())
        # Validate both tables before any DDL: MySQL cannot roll back an earlier CREATE TABLE.
        for table, columns in tables.items():
            if inspector.has_table(table):
                existing.add(table)
                if _validate_existing_table(inspector, table, columns):
                    add_indexes.add(table)

    for table, columns in tables.items():
        if table not in existing:
            unique_name, unique_columns = _UNIQUES[table]
            op.create_table(table, *columns, sa.UniqueConstraint(*unique_columns, name=unique_name), mysql_charset="utf8mb4")
            add_indexes.add(table)
        if table in add_indexes:
            index_name, index_columns = _INDEXES[table]
            op.create_index(index_name, table, list(index_columns))


def downgrade() -> None:
    op.drop_table("agent_interview_turn")
    op.drop_table("agent_interview_session")
