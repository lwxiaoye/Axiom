import pytest

from app.core.auth import UserContext
from app.routers import workflow


class _QueryResult:
    def scalar(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []


class _CapturingSession:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return _QueryResult()


@pytest.mark.asyncio
async def test_my_agents_page_orders_by_creation_time_descending(monkeypatch):
    session = _CapturingSession()
    monkeypatch.setattr(workflow, "async_session", lambda: session)

    await workflow.page_apps(user=UserContext(user_id="owner-1", username="owner"))

    query_sql = str(session.statements[-1].compile(compile_kwargs={"literal_binds": True}))
    assert "ORDER BY agent_workflow_app.create_time DESC" in query_sql
