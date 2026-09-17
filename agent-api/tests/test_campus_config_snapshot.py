"""Published campus snapshots must freeze independently of later draft edits."""

import inspect
from types import SimpleNamespace

import pytest

from app.services.chat.builtin_assistants.campus_services import config_service
from app.services.chat.builtin_assistants.campus_services import java_knowledge
from app.services.chat.builtin_assistants.campus_services.runtime_service import (
    resolve_published_snapshot,
    snapshot_from_release,
    snapshot_from_state,
)


def test_snapshot_from_release_copies_bindings_and_domains():
    release = SimpleNamespace(
        id="rel_pub",
        version_no=2,
        policy_version="campus-policy-v1",
        model_id="m1",
        config_hash="abc",
    )
    knowledge_ids = ["kb-old"]
    domains = [{"host": "school.edu.cn", "include_subdomains": True}]
    snap = snapshot_from_release(release, knowledge_ids, domains)
    knowledge_ids.append("kb-draft")
    domains[0]["host"] = "changed.edu.cn"
    domains.append({"host": "evil.com", "include_subdomains": True})
    assert snap["release_id"] == "rel_pub"
    assert snap["version_no"] == 2
    assert snap["knowledge_ids"] == ["kb-old"]
    assert snap["official_domains"] == [{"host": "school.edu.cn", "include_subdomains": True}]
    assert snap["code"] == "campus_services"


def test_snapshot_from_state_rejects_incomplete_or_foreign_payloads():
    assert snapshot_from_state(None) is None
    assert snapshot_from_state({"code": "campus_services"}) is None
    assert snapshot_from_state({
        "code": "presentation",
        "release_id": "r1",
        "knowledge_ids": ["k1"],
    }) is None
    ok = snapshot_from_state({
        "code": "campus_services",
        "release_id": "r1",
        "knowledge_ids": ["k1"],
        "official_domains": [{"host": "school.edu.cn", "include_subdomains": True}],
    })
    assert ok is not None
    assert ok["release_id"] == "r1"


def test_runtime_resolver_reads_published_release_not_draft():
    source = inspect.getsource(resolve_published_snapshot)
    assert "current_release_id" in source
    assert "draft_release_id" not in source
    assert "STATUS_PUBLISHED" in source


def test_config_payload_includes_available_models():
    source = inspect.getsource(config_service._serialize_config)
    assert "available_models" in source
    assert "_list_enabled_models" in source


def test_campus_knowledge_permissions_accept_all_three_tiers():
    assert config_service.RETRIEVAL_PERMISSIONS == frozenset({"VIEWER", "EDITOR", "OWNER"})
    assert java_knowledge.permission_of({"currentPermission": "editor"}) == "EDITOR"
    assert java_knowledge.permission_of({"currentPermission": "USER"}) == "VIEWER"


def test_publish_promotes_draft_without_rewriting_history():
    publish_src = inspect.getsource(config_service.publish_draft)
    assert "current_release_id = draft.id" in publish_src
    assert "draft_release_id = None" in publish_src
    assert "STATUS_PUBLISHED" in publish_src
    rollback_src = inspect.getsource(config_service.rollback_release)
    assert "_clone_release" in rollback_src
    assert "rollback_from=" in rollback_src


def test_main_chat_skin_is_part_of_release_hash_but_not_harness_snapshot():
    base = dict(
        model_id="m1",
        official_domains=[],
        knowledge_bindings=[{"knowledge_id": "kb1", "enabled": True}],
        policy_version="campus-policy-v1",
    )
    standard = config_service.compute_config_hash(**base, main_chat_skin_id=None)
    skinned = config_service.compute_config_hash(**base, main_chat_skin_id="mcs_1")

    assert standard != skinned
    assert "main_chat_skin_id" not in inspect.getsource(snapshot_from_release)


@pytest.mark.asyncio
async def test_publish_validation_requires_an_official_domain(monkeypatch):
    async def _model_available(_session, _model_id):
        return True

    async def _skin_available(*_args, **_kwargs):
        return None

    monkeypatch.setattr(config_service, "_model_available", _model_available)
    monkeypatch.setattr(config_service, "validate_skin_selection", _skin_available)
    result = await config_service.validate_payload(
        SimpleNamespace(access_token="", tenant_id="tenant-1"),
        model_id="m1",
        official_domains=[],
        knowledge_bindings=[],
        session=object(),
    )

    assert any("至少配置一个学校官方域名" in item for item in result["errors"])
