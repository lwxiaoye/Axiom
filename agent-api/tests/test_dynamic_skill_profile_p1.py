"""Focused P1 regression for model-selected first-party Skill profile activation."""

import pytest

from app.services.chat import execution_profile
from app.services.chat.tools.shell import build_shell_tools
from app.services.chat.tools.workspace import build_workspace_tools
from app.services.agent_harness import run_store


def _text(value):
    return str(getattr(value, "model_content", value) or "")


@pytest.mark.asyncio
async def test_model_use_skill_switches_same_run_to_pptd_tools(monkeypatch):
    runtime_profile = {"id": "interactive", "resolution_reason": "default_interactive"}
    persisted = []

    async def trusted(ids, _token):
        return [{
            "id": ids[0],
            "name": "ppt-studio",
            "instructions": "# PPTD\n用 bash 创作并导出。",
        }]

    async def persist(run_id, record):
        persisted.append((run_id, dict(record)))
        return True

    monkeypatch.setattr(
        "app.services.chat.turn_context_builder._fetch_trusted_skills", trusted,
    )
    monkeypatch.setattr(
        "app.services.chat.turn_context_builder.persist_skill_state", persist,
    )

    workspace = build_workspace_tools(
        user_id="u1",
        token="token",
        run_id="run-1",
        runtime_profile=runtime_profile,
        allow_dynamic_profile=True,
    )
    shell = build_shell_tools(
        user_id="u1",
        run_id="run-1",
        execution_profile=runtime_profile,
        dynamic_profile_enabled=True,
    )

    # The frozen interactive schema already contains the guarded artifact closure.  It is not
    # executable until the model has successfully loaded the authoritative first-party Skill.
    assert {tool.name for tool in shell} == {"bash", "fetch_ppt_asset", "publish_ppt_artifact"}
    use_skill = next(tool for tool in workspace if tool.name == "use_skill")
    await use_skill.execute({"skill_id": "ppt-studio"})

    assert runtime_profile["id"] == "artifact_coding"
    assert runtime_profile["authoring_backend"] == "pptd"
    assert runtime_profile["qa_contract"]["require_structural_qa"] is True
    assert "publish_ppt_artifact" in {tool.name for tool in shell}
    assert persisted
    assert persisted[-1][0] == "run-1"
    assert persisted[-1][1]["execution_profile_id"] == "artifact_coding"
    assert persisted[-1][1]["selection_source"] == "model"


@pytest.mark.asyncio
async def test_explicit_third_party_skill_stays_native_and_interactive(monkeypatch):
    runtime_profile = {
        "id": "interactive",
        "resolution_reason": "explicit_skill_native_ppt",
    }

    async def trusted(ids, _token):
        return [{
            "id": ids[0],
            "name": "github-native-slides",
            "instructions": "Use the package's own generator.",
        }]

    monkeypatch.setattr(
        "app.services.chat.turn_context_builder._fetch_trusted_skills", trusted,
    )
    monkeypatch.setattr(
        "app.services.chat.turn_context_builder.persist_skill_state",
        lambda *_args, **_kwargs: _persist_ok(),
    )

    workspace = build_workspace_tools(
        user_id="u1",
        token="token",
        run_id="run-2",
        runtime_profile=runtime_profile,
        allow_dynamic_profile=False,
    )
    shell = build_shell_tools(
        user_id="u1",
        run_id="run-2",
        execution_profile=runtime_profile,
        dynamic_profile_enabled=False,
    )
    use_skill = next(tool for tool in workspace if tool.name == "use_skill")
    await use_skill.execute({"skill_id": "github-native-slides"})

    assert runtime_profile["id"] == "interactive"
    assert [tool.name for tool in shell] == ["bash"]


async def _persist_ok():
    return True


def test_first_party_detection_uses_skill_fact_not_request_text():
    assert execution_profile.profile_id_for_skill({"id": "ppt-studio"}) == "artifact_coding"
    assert execution_profile.profile_id_for_skill({"id": "third-party-ppt"}) == "interactive"
    assert execution_profile.profile_id_for_skill({"name": "a presentation helper"}) == "interactive"


def test_recovery_segment_projects_loaded_first_party_skill_profile():
    base = execution_profile.resolve_profile_snapshot(
        execution_profile.freeze_profile_evidence(message="普通对话")
    )
    state = {
        "execution_profile": {"status": "finalized", "snapshot": base},
        "skill_state": {
            "version": 1,
            "skills": [{
                "skill_id": "ppt-studio",
                "name": "ppt-studio",
                "selection_source": "model",
                "selection_method": "use_skill",
                "status": "loaded",
                "execution_profile_id": "artifact_coding",
            }],
        },
    }

    effective = run_store._validated_execution_profile_from_state(state)

    assert effective["id"] == "artifact_coding"
    assert effective["resolution_reason"] == "model_use_skill_first_party_ppt"


def test_recovery_segment_keeps_explicit_native_skill_interactive():
    base = execution_profile.resolve_profile_snapshot(
        execution_profile.freeze_profile_evidence(message="普通对话")
    )
    state = {
        "execution_profile": {"status": "finalized", "snapshot": base},
        "skill_state": {
            "version": 1,
            "skills": [{
                "skill_id": "github-native-slides",
                "name": "github-native-slides",
                "selection_source": "explicit",
                "selection_method": "plus",
                "status": "loaded",
                "execution_profile_id": "interactive",
            }],
        },
    }

    effective = run_store._validated_execution_profile_from_state(state)

    assert effective["id"] == "interactive"
