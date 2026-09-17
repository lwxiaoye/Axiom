from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.agent_api.access_service import AgentApiPrincipal


class _MemoryStore:
    def __init__(self):
        self.sessions = {}
        self.files = {}

    async def get_session(self, app_id, api_key_id, session_id):
        return self.sessions.get((app_id, api_key_id, session_id))

    async def create_session(self, **values):
        row = SimpleNamespace(**values)
        self.sessions[(row.app_id, row.api_key_id, row.session_id)] = row
        return row

    async def get_file(self, external_session_id, file_id):
        return self.files.get((external_session_id, file_id))

    async def create_file(self, **values):
        row = SimpleNamespace(**values)
        self.files[(row.external_session_id, row.id)] = row
        return row


class _MemoryStorage:
    def __init__(self):
        self.values = {}

    async def write_bytes(self, key, data, *, content_type=""):
        self.values[key] = bytes(data)

    async def exists(self, key):
        return key in self.values


@pytest.fixture
def service():
    from app.services.agent_api.external_session_service import ExternalAgentSessionService

    return ExternalAgentSessionService(store=_MemoryStore(), storage=_MemoryStorage())


PRINCIPAL_A = AgentApiPrincipal("key-a", "app-a", "publisher-a", "qza_a")
PRINCIPAL_B = AgentApiPrincipal("key-b", "app-a", "publisher-a", "qza_b")


@pytest.mark.asyncio
async def test_new_api_session_populates_legacy_required_metadata(service):
    session = await service.get_or_create_external_session(PRINCIPAL_A, "chat-a")

    stored = await service._store.get_session("app-a", "key-a", "chat-a")
    assert session.id == stored.id
    assert stored.published_version == 0
    assert stored.visitor_id == "key-a"
    assert stored.origin == "publisher-agent-api"


@pytest.mark.asyncio
async def test_same_session_id_is_isolated_by_api_key(service):
    assert (await service.get_or_create_external_session(PRINCIPAL_A, "shared")).id != (
        await service.get_or_create_external_session(PRINCIPAL_B, "shared")
    ).id


@pytest.mark.asyncio
async def test_file_cannot_be_resolved_by_another_key_scope(service):
    from app.services.agent_api.external_session_service import ExternalSessionAccessError

    session_a = await service.get_or_create_external_session(PRINCIPAL_A, "shared")
    session_b = await service.get_or_create_external_session(PRINCIPAL_B, "shared")
    external_file = await service.save_external_file(
        session_a, "brief.pdf", "application/pdf", b"data"
    )

    with pytest.raises(ExternalSessionAccessError, match="external_file_not_found"):
        await service.resolve_external_file(session_b, external_file.id)
