import pytest
from types import SimpleNamespace

from app.core.auth import UserContext
from app.routers import workflow


class _AclSaveSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        return None

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_save_acl_stores_each_selected_department_as_an_atomic_acl_row(monkeypatch):
    session = _AclSaveSession()

    async def allow_owner(*_args, **_kwargs):
        return SimpleNamespace(id="app-1", tenant_id="0"), "OWNER"

    monkeypatch.setattr(workflow, "async_session", lambda: session)
    monkeypatch.setattr(workflow, "_require_permission", allow_owner)

    await workflow.save_acl(
        workflow.AclSaveRequest(
            appId="app-1",
            items=[
                workflow.AclItem(
                    subjectType="DEPARTMENT",
                    subjectId="2047142367087898626,2085195868708065282,2085195904057659394",
                )
            ],
        ),
        UserContext(user_id="owner-1", username="owner"),
    )

    assert [row.subject_id for row in session.added if isinstance(row, workflow.WorkflowAcl)] == [
        "2047142367087898626",
        "2085195868708065282",
        "2085195904057659394",
    ]


@pytest.mark.asyncio
async def test_save_acl_accepts_multiple_role_ids_from_the_role_selector(monkeypatch):
    session = _AclSaveSession()

    async def allow_owner(*_args, **_kwargs):
        return SimpleNamespace(id="app-1", tenant_id="0"), "OWNER"

    monkeypatch.setattr(workflow, "async_session", lambda: session)
    monkeypatch.setattr(workflow, "_require_permission", allow_owner)

    await workflow.save_acl(
        workflow.AclSaveRequest(
            appId="app-1",
            items=[
                workflow.AclItem(subjectType="ROLE", subjectId=["role-1", "role-2"]),
            ],
        ),
        UserContext(user_id="owner-1", username="owner"),
    )

    assert [row.subject_id for row in session.added if isinstance(row, workflow.WorkflowAcl)] == ["role-1", "role-2"]
