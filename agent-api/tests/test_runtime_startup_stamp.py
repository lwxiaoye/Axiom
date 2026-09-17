import asyncio
import unittest
from types import SimpleNamespace


class RuntimeStartupStampTest(unittest.TestCase):
    def test_runtime_startup_ddl_stamps_schema_head(self):
        from app.core import runtime_db

        class Connection:
            def __init__(self):
                self.statements = []

            async def execute(self, statement, params=None):
                self.statements.append((str(statement), params or {}))

        conn = Connection()
        asyncio.run(runtime_db._stamp_runtime_schema_head(conn))

        joined = "\n".join(statement for statement, _params in conn.statements)
        self.assertIn("alembic_version_runtime", joined)
        self.assertTrue(
            any(
                params.get("revision") == runtime_db.RUNTIME_SCHEMA_HEAD
                for _statement, params in conn.statements
            )
        )

    def test_startup_ddl_refuses_to_skip_an_existing_revision(self):
        from app.core import runtime_db

        class Connection:
            async def execute(self, statement, _params=None):
                sql = str(statement)
                if "to_regclass('alembic_version_runtime')" in sql:
                    return SimpleNamespace(scalar=lambda: True)
                if "SELECT version_num" in sql:
                    return SimpleNamespace(scalar=lambda: "runtime_0015_model_cache_audit")
                if "to_regclass('agent_runs')" in sql:
                    return SimpleNamespace(scalar=lambda: True)
                raise AssertionError(sql)

        with self.assertRaisesRegex(RuntimeError, "禁止用 create_all"):
            asyncio.run(runtime_db._guard_startup_ddl_revision(Connection()))

    def test_startup_ddl_allows_a_database_already_at_head(self):
        from app.core import runtime_db

        class Connection:
            async def execute(self, statement, _params=None):
                sql = str(statement)
                if "to_regclass('alembic_version_runtime')" in sql:
                    return SimpleNamespace(scalar=lambda: True)
                if "SELECT version_num" in sql:
                    return SimpleNamespace(scalar=lambda: runtime_db.RUNTIME_SCHEMA_HEAD)
                if "to_regclass('agent_runs')" in sql:
                    return SimpleNamespace(scalar=lambda: True)
                raise AssertionError(sql)

        asyncio.run(runtime_db._guard_startup_ddl_revision(Connection()))


if __name__ == "__main__":
    unittest.main()
