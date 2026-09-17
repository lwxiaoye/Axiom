"""Exercise adoption and DDL on isolated SQLite, plus MySQL SQL generation; no shared DB."""

import importlib.util
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from app.interview_models import InterviewSession, InterviewTurn


_PATH = Path(__file__).resolve().parents[1] / "migrations/versions/mysql/0012_interview_sessions.py"
_SPEC = importlib.util.spec_from_file_location("interview_migration_0012", _PATH)
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)

SESSION = "agent_interview_session"
TURN = "agent_interview_turn"


@pytest.fixture
def db(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        metadata = sa.MetaData()
        sa.Table("ai_chat_threads", metadata, sa.Column("id", sa.String(64), primary_key=True))
        sa.Table("ai_chat_messages", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        metadata.create_all(connection)
        session = InterviewSession.__table__.to_metadata(metadata)
        turn = InterviewTurn.__table__.to_metadata(metadata)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: False))
        statements = []

        @sa.event.listens_for(connection, "before_cursor_execute")
        def record(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().split()[0].upper() in {"CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE"}:
                statements.append(statement)

        yield SimpleNamespace(connection=connection, metadata=metadata, session=session, turn=turn, statements=statements)
    engine.dispose()


def _seed(db):
    db.connection.execute(db.metadata.tables["ai_chat_threads"].insert().values(id="thread-1"))
    db.connection.execute(db.metadata.tables["ai_chat_messages"].insert().values(id=1))
    db.connection.execute(db.session.insert().values(
        id="session-1", thread_id="thread-1", user_id="student-1", config_json={"role": "产品经理"},
        materials_json={"resume": "合成简历"}, question_bank_json=[], version=7, status="paused",
    ))
    db.connection.execute(db.turn.insert().values(
        id="turn-1", session_id="session-1", run_id="run-1", answer_message_id=1,
        action="answer", expected_version=6, committed_version=7, input_json={}, answer_text="我的原始回答",
    ))


def _rows(db):
    return (
        db.connection.execute(sa.select(db.session)).mappings().all(),
        db.connection.execute(sa.select(db.turn)).mappings().all(),
    )


def test_fresh_database_creates_both_tables_and_can_be_validated_again(db):
    migration.upgrade()
    inspector = sa.inspect(db.connection)
    assert inspector.has_table(SESSION) and inspector.has_table(TURN)
    assert inspector.get_unique_constraints(SESSION)[0]["column_names"] == ["thread_id"]
    assert inspector.get_unique_constraints(TURN)[0]["column_names"] == ["run_id"]
    assert all(item["options"]["ondelete"] == "CASCADE" for item in inspector.get_foreign_keys(TURN))
    db.statements.clear()
    migration.upgrade()
    assert db.statements == []


def test_create_all_tables_are_adopted_without_ddl_or_data_changes(db):
    db.metadata.create_all(db.connection)
    _seed(db)
    before = _rows(db)
    inspector = sa.inspect(db.connection)
    assert inspector.get_columns(SESSION)[3]["default"] is None
    db.statements.clear()
    migration.upgrade()
    assert db.statements == []
    assert _rows(db) == before


@pytest.mark.parametrize("index_table,index_name", [
    (SESSION, "ix_agent_interview_session_user_id"),
    (TURN, "ix_agent_interview_turn_session_id"),
])
def test_missing_ordinary_index_is_added_without_touching_answers(db, index_table, index_name):
    db.metadata.create_all(db.connection)
    _seed(db)
    before = _rows(db)
    db.connection.execute(sa.text(f"DROP INDEX {index_name}"))
    db.statements.clear()
    migration.upgrade()
    assert len(db.statements) == 1 and db.statements[0].startswith(f"CREATE INDEX {index_name}")
    assert any(item["name"] == index_name for item in sa.inspect(db.connection).get_indexes(index_table))
    assert _rows(db) == before


def test_existing_session_and_missing_turn_creates_only_turn(db):
    db.session.create(db.connection)
    db.statements.clear()
    migration.upgrade()
    assert len(db.statements) == 2
    assert "CREATE TABLE agent_interview_turn" in db.statements[0]
    assert "CREATE INDEX ix_agent_interview_turn_session_id" in db.statements[1]


def _corrupt_table(table, corruption):
    if corruption == "missing_column":
        table._columns.remove(table.c.expected_version)
    elif corruption == "extra_column":
        table.append_column(sa.Column("unexpected_required", sa.String(20), nullable=False))
    elif corruption == "wrong_type":
        table.c.expected_version.type = sa.BigInteger()
    elif corruption == "wrong_length":
        table.c.run_id.type = sa.String(63)
    elif corruption == "wrong_nullable":
        table.c.answer_text.nullable = True
    elif corruption == "wrong_default":
        table.c.assisted.server_default = sa.DefaultClause("9")
    elif corruption == "wrong_primary_key":
        table.c.id.primary_key = False
        table.append_constraint(sa.PrimaryKeyConstraint(table.c.run_id))
    elif corruption == "missing_unique":
        table.constraints.difference_update(item for item in list(table.constraints) if isinstance(item, sa.UniqueConstraint))
    elif corruption == "extra_unique":
        table.append_constraint(sa.UniqueConstraint("answer_message_id"))
    elif corruption == "missing_foreign_key":
        table.constraints.difference_update(item for item in list(table.constraints) if isinstance(item, sa.ForeignKeyConstraint))
    elif corruption == "wrong_foreign_target":
        table.constraints.difference_update(item for item in list(table.constraints) if isinstance(item, sa.ForeignKeyConstraint))
        table.append_constraint(sa.ForeignKeyConstraint(["session_id"], ["ai_chat_threads.id"], ondelete="CASCADE"))
    elif corruption == "missing_cascade":
        for item in table.constraints:
            if isinstance(item, sa.ForeignKeyConstraint):
                item.ondelete = None
    elif corruption == "wrong_cascade":
        for item in table.constraints:
            if isinstance(item, sa.ForeignKeyConstraint):
                item.ondelete = "RESTRICT"


@pytest.mark.parametrize("corruption,error", [
    ("missing_column", "columns differ"), ("extra_column", "columns differ"),
    ("wrong_type", "expected_version type"), ("wrong_length", "run_id type"),
    ("wrong_nullable", "answer_text nullable"), ("wrong_default", "assisted server default"),
    ("wrong_primary_key", "primary key differs"), ("missing_unique", "unique columns differ"),
    ("extra_unique", "unique columns differ"), ("missing_foreign_key", "foreign keys"),
    ("wrong_foreign_target", "foreign keys"), ("missing_cascade", "foreign keys"), ("wrong_cascade", "foreign keys"),
])
def test_incompatible_second_table_fails_before_creating_missing_first_table(db, corruption, error):
    _corrupt_table(db.turn, corruption)
    db.turn.create(db.connection)
    db.statements.clear()
    with pytest.raises(RuntimeError, match=error):
        migration.upgrade()
    assert db.statements == []
    assert not sa.inspect(db.connection).has_table(SESSION)


def test_wrong_named_ordinary_index_is_not_overwritten(db):
    db.metadata.create_all(db.connection)
    name = "ix_agent_interview_session_user_id"
    db.connection.execute(sa.text(f"DROP INDEX {name}"))
    db.connection.execute(sa.text(f"CREATE INDEX {name} ON {SESSION} (status)"))
    db.statements.clear()
    with pytest.raises(RuntimeError, match="required index name"):
        migration.upgrade()
    assert db.statements == []


def _mysql_inspector(db, **overrides):
    inspector = sa.inspect(db.connection)
    methods = {name: getattr(inspector, name) for name in (
        "get_columns", "get_pk_constraint", "get_unique_constraints", "get_indexes", "get_foreign_keys",
    )}
    methods.update(overrides)
    return SimpleNamespace(bind=SimpleNamespace(dialect=mysql.dialect()), default_schema_name="main", **methods)


def test_mysql_integer_display_width_and_generated_unique_name_are_compatible(db):
    db.metadata.create_all(db.connection)
    columns = sa.inspect(db.connection).get_columns(SESSION)
    next(column for column in columns if column["name"] == "version")["type"] = mysql.INTEGER(display_width=11)
    inspector = _mysql_inspector(db, get_columns=lambda _table: columns,
        get_unique_constraints=lambda _table: [{"name": "thread_id", "column_names": ["thread_id"]}])
    assert migration._validate_existing_table(inspector, SESSION, migration._table_columns()[SESSION]) is False


@pytest.mark.parametrize("corruption,error", [("unsigned", "version type"), ("prefix_unique", "unique columns differ"), ("foreign_schema", "another schema")])
def test_mysql_reflection_rejects_semantically_different_constraints(db, corruption, error):
    db.metadata.create_all(db.connection)
    inspector = sa.inspect(db.connection)
    overrides = {}
    if corruption == "unsigned":
        columns = inspector.get_columns(SESSION)
        next(column for column in columns if column["name"] == "version")["type"] = mysql.INTEGER(unsigned=True)
        overrides["get_columns"] = lambda _table: columns
    elif corruption == "prefix_unique":
        indexes = inspector.get_indexes(SESSION) + [{"name": "thread_id", "column_names": ["thread_id"], "unique": True, "dialect_options": {"mysql_length": {"thread_id": 10}}}]
        overrides["get_indexes"] = lambda _table: indexes
    else:
        foreign_keys = inspector.get_foreign_keys(SESSION)
        foreign_keys[0]["referred_schema"] = "another_database"
        overrides["get_foreign_keys"] = lambda _table: foreign_keys
    with pytest.raises(RuntimeError, match=error):
        migration._validate_existing_table(_mysql_inspector(db, **overrides), SESSION, migration._table_columns()[SESSION])


def test_offline_mysql_sql_emits_complete_schema_without_inspection(monkeypatch):
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(migration, "op", Operations(context))
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: True))

    def reject_inspection(*_args, **_kwargs):
        pytest.fail("Offline migration must not inspect or connect to a database")

    monkeypatch.setattr(migration.sa, "inspect", reject_inspection)
    migration.upgrade()
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 2
    assert sql.count("CREATE INDEX") == 2
    assert sql.count("ON DELETE CASCADE") == 3
    assert "UNIQUE (thread_id)" in sql and "UNIQUE (run_id)" in sql
    assert "question_bank_json JSON NOT NULL" in sql
    assert "DEFAULT 'preparing'" in sql
