import json
from types import SimpleNamespace

import pytest

from app.services.campus_assistant import main_chat_skin_service
from app.services.workflows import sub_agent_skin_service


class _Result:
    def __init__(self, *, one=None, rows=None, scalars=None):
        self._one = one
        self._rows = list(rows or [])
        self._scalars = list(scalars or [])

    def scalar_one_or_none(self):
        return self._one

    def scalar_one(self):
        return self._one

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.deleted = []
        self.refreshed = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement, *_args, **_kwargs):
        if not self.results:
            return _Result()
        return self.results.pop(0)

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        pass

    async def refresh(self, row, *_args, **_kwargs):
        # A real AsyncSession expires server-onupdate columns (updated_at) after the UPDATE is
        # flushed; the service must reload the row before serializing it, or the attribute read
        # becomes sync IO inside the async session (MissingGreenlet -> HTTP 500).
        self.refreshed.append(row)

    async def commit(self):
        self.commits += 1


def _main_skin(**overrides):
    manifest = {
        "kind": "axiom-main-chat-skin",
        "scope": "main_chat",
        "name": "包内名称",
        "description": "包内说明",
        "assets": [],
    }
    values = {
        "id": "mcs_1",
        "tenant_id": "1",
        "skin_key": "campus-blueprint",
        "version": "1.0.0",
        "schema_version": 1,
        "name": "本地名称",
        "description": "本地备注",
        "renderer_key": "decorated-chat-v1",
        "manifest_json": json.dumps(manifest, ensure_ascii=False),
        "content_hash": "a" * 64,
        "source_type": "imported",
        "status": "active",
        "installed_by": "u1",
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sub_skin(**overrides):
    manifest = {
        "kind": "axiom-sub-agent-skin",
        "scope": "sub_agent",
        "name": "包内名称",
        "description": "包内说明",
        "assets": [],
    }
    values = {
        "id": "sas_1",
        "skin_key": "campus-agent",
        "assignment_key": "skin-campus-agent-v1-0-0",
        "version": "1.0.0",
        "schema_version": 1,
        "name": "本地名称",
        "description": "本地备注",
        "renderer_key": "decorated-agent-run-v1",
        "manifest_json": json.dumps(manifest, ensure_ascii=False),
        "content_hash": "b" * 64,
        "source_type": "imported",
        "status": "active",
        "installed_by": "u1",
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_main_chat_metadata_edit_does_not_mutate_portable_manifest(monkeypatch):
    row = _main_skin()
    original_manifest = row.manifest_json
    original_hash = row.content_hash
    session = _Session([_Result(one=row), _Result(one=0)])
    monkeypatch.setattr(main_chat_skin_service, "async_session", lambda: session)

    result = await main_chat_skin_service.update_skin_metadata(
        SimpleNamespace(tenant_id="1", user_id="admin"),
        row.id,
        name="  客户侧显示名  ",
        description="  仅本系统备注  ",
    )

    assert result["name"] == "客户侧显示名"
    assert row.manifest_json == original_manifest
    assert row.content_hash == original_hash
    assert json.loads(row.manifest_json)["name"] == "包内名称"
    assert session.commits == 1
    assert session.refreshed == [row]


@pytest.mark.asyncio
async def test_main_chat_delete_is_blocked_while_any_release_references_version(monkeypatch):
    row = _main_skin()
    session = _Session([_Result(one=row), _Result(one=2)])
    monkeypatch.setattr(main_chat_skin_service, "async_session", lambda: session)

    with pytest.raises(main_chat_skin_service.MainChatSkinError, match="仍被 2 个"):
        await main_chat_skin_service.delete_skin(
            SimpleNamespace(tenant_id="1", user_id="admin"), row.id,
        )
    assert session.deleted == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_main_chat_delete_removes_unreferenced_import_and_assets(monkeypatch):
    row = _main_skin()
    session = _Session([_Result(one=row), _Result(one=0), _Result()])
    monkeypatch.setattr(main_chat_skin_service, "async_session", lambda: session)

    result = await main_chat_skin_service.delete_skin(
        SimpleNamespace(tenant_id="1", user_id="admin"), row.id,
    )

    assert result == {"deleted": True, "id": row.id}
    assert session.deleted == [row]
    assert session.commits == 1


def test_sub_agent_reference_parser_matches_only_the_declared_preset():
    key = "skin-campus-agent-v1-0-0"
    graph = {"fastgpt": {"chatConfig": {"presentation": {"preset": key}}}}
    assert sub_agent_skin_service._workflow_references_preset(json.dumps(graph), key)
    assert not sub_agent_skin_service._workflow_references_preset(json.dumps(graph), "skin-other-v1")


def test_sub_agent_fallback_parser_replaces_direct_and_fastgpt_presentation():
    key = "skin-campus-agent-v1-0-0"
    direct = json.dumps({
        "chatConfig": {
            "presentation": {
                "schemaVersion": 1,
                "preset": key,
                "copy": {"welcomeTitle": "旧欢迎词"},
            },
        },
    })
    nested = json.dumps({
        "fastgpt": {
            "chatConfig": {
                "presentation": {
                    "schemaVersion": 1,
                    "preset": key,
                },
            },
        },
    })

    direct_updated, direct_changed = sub_agent_skin_service._fallback_workflow_presentation(
        direct,
        key,
    )
    nested_updated, nested_changed = sub_agent_skin_service._fallback_workflow_presentation(
        nested,
        key,
    )
    unrelated, unrelated_changed = sub_agent_skin_service._fallback_workflow_presentation(
        direct,
        "skin-other-v1",
    )

    assert direct_changed is True
    assert nested_changed is True
    assert json.loads(direct_updated)["chatConfig"]["presentation"] == {
        "schemaVersion": 1,
        "preset": sub_agent_skin_service.DEFAULT_PRESET_KEY,
    }
    assert json.loads(nested_updated)["fastgpt"]["chatConfig"]["presentation"] == {
        "schemaVersion": 1,
        "preset": sub_agent_skin_service.DEFAULT_PRESET_KEY,
    }
    assert unrelated_changed is False
    assert unrelated == direct


@pytest.mark.asyncio
async def test_sub_agent_metadata_edit_keeps_package_identity(monkeypatch):
    row = _sub_skin()
    original_manifest = row.manifest_json
    original_hash = row.content_hash
    # require row, current assignments, candidate definitions, historical versions
    session = _Session([
        _Result(one=row),
        _Result(one=0),
        _Result(rows=[]),
        _Result(scalars=[]),
    ])
    monkeypatch.setattr(sub_agent_skin_service, "is_platform_admin", lambda _user: True)

    result = await sub_agent_skin_service.update_skin_metadata(
        session,
        SimpleNamespace(tenant_id="0", user_id="admin"),
        row.id,
        name="运行页显示名",
        description="本地备注",
    )

    assert result["name"] == "运行页显示名"
    assert row.manifest_json == original_manifest
    assert row.content_hash == original_hash
    assert session.refreshed == [row]


@pytest.mark.asyncio
async def test_sub_agent_delete_falls_current_references_back_to_default(monkeypatch):
    row = _sub_skin(source_type="imported")
    assignment = SimpleNamespace(
        app_id="agent-1",
        draft_preset_key=row.assignment_key,
        published_preset_key=row.assignment_key,
        draft_updated_by="old-user",
    )
    definition = SimpleNamespace(
        app_id="agent-1",
        draft_json=json.dumps({
            "chatConfig": {"presentation": {"preset": row.assignment_key}},
        }),
        published_json=json.dumps({
            "fastgpt": {
                "chatConfig": {"presentation": {"preset": row.assignment_key}},
            },
        }),
    )
    original_published_json = definition.published_json
    session = _Session([
        _Result(one=row),
        _Result(rows=[assignment]),
        _Result(rows=[definition]),
        _Result(),
    ])
    monkeypatch.setattr(sub_agent_skin_service, "is_platform_admin", lambda _user: True)

    result = await sub_agent_skin_service.delete_skin(
        session,
        SimpleNamespace(tenant_id="0", user_id="admin"),
        row.id,
    )

    assert result == {"deleted": True, "id": row.id, "fallbackAgentCount": 1}
    assert assignment.draft_preset_key == sub_agent_skin_service.DEFAULT_PRESET_KEY
    assert assignment.published_preset_key == sub_agent_skin_service.DEFAULT_PRESET_KEY
    assert assignment.draft_updated_by == "admin"
    assert json.loads(definition.draft_json)["chatConfig"]["presentation"] == {
        "schemaVersion": 1,
        "preset": sub_agent_skin_service.DEFAULT_PRESET_KEY,
    }
    assert definition.published_json == original_published_json
    assert (
        json.loads(definition.published_json)["fastgpt"]["chatConfig"]["presentation"]["preset"]
        == row.assignment_key
    )
    assert session.deleted == [row]


@pytest.mark.asyncio
async def test_sub_agent_delete_soft_removes_unreferenced_shipped_package(monkeypatch):
    row = _sub_skin(source_type="builtin-package")
    session = _Session([
        _Result(one=row),
        _Result(rows=[]),
        _Result(rows=[]),
    ])
    monkeypatch.setattr(sub_agent_skin_service, "is_platform_admin", lambda _user: True)

    result = await sub_agent_skin_service.delete_skin(
        session,
        SimpleNamespace(tenant_id="0", user_id="admin"),
        row.id,
    )

    assert result == {"deleted": True, "id": row.id, "fallbackAgentCount": 0}
    assert row.status == sub_agent_skin_service.REMOVED_STATUS
    assert session.deleted == []


@pytest.mark.asyncio
async def test_sub_agent_delete_removes_unreferenced_import_and_assets(monkeypatch):
    row = _sub_skin(source_type="imported")
    session = _Session([
        _Result(one=row),
        _Result(rows=[]),
        _Result(rows=[]),
        _Result(),
    ])
    monkeypatch.setattr(sub_agent_skin_service, "is_platform_admin", lambda _user: True)

    result = await sub_agent_skin_service.delete_skin(
        session,
        SimpleNamespace(tenant_id="0", user_id="admin"),
        row.id,
    )

    assert result == {"deleted": True, "id": row.id, "fallbackAgentCount": 0}
    assert session.deleted == [row]


@pytest.mark.asyncio
async def test_explicit_import_reactivates_a_removed_shipped_package():
    row = _sub_skin(source_type="builtin-package", status=sub_agent_skin_service.REMOVED_STATUS)
    session = _Session([_Result(one=row)])
    parsed = SimpleNamespace(content_hash=row.content_hash)

    installed_row, installed = await sub_agent_skin_service._install_parsed(
        session,
        parsed,
        installed_by="admin",
        source_type="imported",
        reactivate_existing=True,
    )

    assert installed is True
    assert installed_row is row
    assert row.status == sub_agent_skin_service.ACTIVE_STATUS


def test_import_retargets_old_version_preset_in_draft_json():
    old_key = "skin-campus-agent-v1-0-0"
    new_key = "skin-campus-agent-v1-1-1"
    raw = json.dumps({
        "chatConfig": {
            "presentation": {
                "schemaVersion": 1,
                "preset": old_key,
                "copy": {"welcomeTitle": "迎新"},
            },
        },
    })
    updated, changed = sub_agent_skin_service._retarget_workflow_presentation(raw, old_key, new_key)
    assert changed is True
    presentation = json.loads(updated)["chatConfig"]["presentation"]
    assert presentation["preset"] == new_key
    assert presentation["copy"] == {"welcomeTitle": "迎新"}


def test_builtin_bootstrap_skips_a_removed_shipped_package():
    removed = _sub_skin(source_type="builtin-package", status=sub_agent_skin_service.REMOVED_STATUS)
    active = _sub_skin(id="sas_2", status="active")
    assert sub_agent_skin_service.should_skip_builtin_install([removed]) is True
    assert sub_agent_skin_service.should_skip_builtin_install([active]) is False
    assert sub_agent_skin_service.should_skip_builtin_install([]) is False
