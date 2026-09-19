"""Focused contracts for immutable Harness ExecutionProfile snapshots."""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.services.chat import execution_profile
from app.services.agent_harness import run_store


def _evidence(message: str = "请解释这个概念", **kwargs):
    return execution_profile.freeze_profile_evidence(message=message, **kwargs)


def _record(evidence: dict) -> dict:
    return {
        "status": "unresolved",
        "resolver_version": evidence["resolver_version"],
        "evidence_hash": evidence["evidence_hash"],
        "evidence": evidence,
        "snapshot": None,
    }


def test_plain_request_resolves_interactive_with_full_contract():
    snapshot = execution_profile.resolve_profile_snapshot(_evidence())
    assert snapshot["id"] == "interactive"
    assert snapshot["artifact_kind"] is None
    assert snapshot["authoring_backend"] is None
    for key in (
        "policy_hash", "capability_policy", "loop_policy", "workspace_policy",
        "qa_contract", "fallback_policy",
    ):
        assert key in snapshot
    assert execution_profile.validate_profile_snapshot(snapshot) == snapshot


def test_ppt_intent_keeps_initial_profile_neutral_until_skill_selection():
    evidence = _evidence("请设计一份 10 页的 PPT 演示文稿")
    assert evidence["ppt_artifact_intent"] is True
    snapshot = execution_profile.resolve_profile_snapshot(evidence)
    assert snapshot["id"] == "interactive"
    assert snapshot["resolution_reason"] == "default_interactive"


def test_explicit_first_party_ppt_skill_resolves_artifact_coding():
    evidence = _evidence(
        "主题是三国演义",
        skill_ids=["skill-uuid"],
        selected_skills=[{"id": "skill-uuid", "name": "ppt-studio"}],
    )
    snapshot = execution_profile.resolve_profile_snapshot(evidence)
    assert snapshot["id"] == "artifact_coding"
    assert snapshot["artifact_kind"] == "presentation"
    assert snapshot["authoring_backend"] == "pptd"
    assert snapshot["resolution_reason"] == "explicit_ppt_skill"
    assert snapshot["workspace_policy"]["disposable_run"] is True
    assert snapshot["workspace_policy"]["project_root"] == "/workspace/tmp/ppt-project"
    assert snapshot["loop_policy"]["max_steps"] == 48
    assert snapshot["loop_policy"]["max_wall_seconds"] == 3600
    assert snapshot["loop_policy"]["max_output_tokens"] == 400_000
    assert snapshot["fallback_policy"]["mode"] == "disabled"


def test_explicit_third_party_ppt_skill_uses_its_native_authoring_stack():
    evidence = _evidence(
        "请制作一份四页团队周会 PPT",
        skill_ids=["skill-openakita"],
        selected_skills=[{
            "id": "skill-openakita",
            "name": "openakita/skills@ppt-creator",
        }],
    )
    assert evidence["explicit_ppt_skill"] is True
    assert evidence["explicit_ppt_backend"] == "skill_native"

    snapshot = execution_profile.resolve_profile_snapshot(evidence)

    assert snapshot["id"] == "interactive"
    assert snapshot["artifact_kind"] is None
    assert snapshot["authoring_backend"] is None
    assert snapshot["qa_contract"] == {"mode": "default"}
    assert snapshot["resolution_reason"] == "explicit_skill_native_ppt"


def test_cross_run_resume_inherits_artifact_coding_without_ppt_words():
    evidence = _evidence("继续", inherited_profile_id="artifact_coding")
    snapshot = execution_profile.resolve_profile_snapshot(evidence)
    assert snapshot["id"] == "artifact_coding"
    assert snapshot["resolution_reason"] == "resume_inherited_artifact_coding"


def test_nested_profile_record_still_exposes_ppt_loop_policy():
    snapshot = execution_profile.resolve_profile_snapshot(_evidence(
        "请设计一份 10 页的 PPT",
        skill_ids=["ppt-studio"],
        selected_skills=[{"id": "ppt-studio", "name": "ppt-studio"}],
    ))
    record = {"status": "finalized", "snapshot": snapshot}
    policy = execution_profile.effective_loop_policy(record)
    assert policy["max_output_tokens"] == 400_000
    assert policy["max_wall_seconds"] == 3600
    assert policy["max_steps"] == 48
    assert execution_profile.unwrap_profile_dict(record)["id"] == "artifact_coding"


def test_explicit_match_photos_are_frozen_into_ppt_qa_contract():
    snapshot = execution_profile.resolve_profile_snapshot(_evidence(
        "帮我生成一份关于库里的 PPT，要包含几张库里的比赛照片",
        skill_ids=["ppt-studio"],
        selected_skills=[{"id": "ppt-studio", "name": "ppt-studio"}],
    ))
    requirement = snapshot["qa_contract"]["image_requirement"]
    assert requirement["mode"] == "searched_photos"
    assert requirement["min_images"] == 3
    assert requirement["min_sources"] == 0
    assert "库里" in requirement["brief"]


def test_distinct_photo_sources_are_frozen_into_ppt_qa_contract():
    snapshot = execution_profile.resolve_profile_snapshot(_evidence(
        "请制作 PPT，必须使用至少 3 张不同来源的库里 NBA 真实比赛现场照片",
        skill_ids=["ppt-studio"],
        selected_skills=[{"id": "ppt-studio", "name": "ppt-studio"}],
    ))
    requirement = snapshot["qa_contract"]["image_requirement"]
    assert requirement["min_images"] == 3
    assert requirement["min_sources"] == 3


def test_cross_run_resume_inherits_photo_requirement_and_current_opt_out_wins():
    inherited = {
        "mode": "searched_photos",
        "min_images": 4,
        "min_sources": 0,
        "brief": "需要四张真实比赛照片",
    }
    resumed = execution_profile.resolve_profile_snapshot(_evidence(
        "继续",
        inherited_profile_id="artifact_coding",
        inherited_image_requirement=inherited,
    ))
    assert resumed["qa_contract"]["image_requirement"] == inherited

    opted_out = execution_profile.resolve_profile_snapshot(_evidence(
        "继续，但不要照片",
        inherited_profile_id="artifact_coding",
        inherited_image_requirement=inherited,
    ))
    assert opted_out["qa_contract"]["image_requirement"]["mode"] == "none"


def test_worker_resolution_uses_frozen_intent_fact_not_live_ppt_policy(monkeypatch):
    from app.services.skills import ppt_policy

    evidence = _evidence("请做一份 PPT")
    monkeypatch.setattr(ppt_policy, "is_ppt_artifact_request", lambda *_args, **_kwargs: False)
    snapshot = execution_profile.resolve_profile_snapshot(evidence)
    assert snapshot["id"] == "interactive"


def test_cas_conflict_reuses_same_candidate_without_re_resolving(monkeypatch):
    evidence = _evidence("帮我生成一份 PPT")
    state = {
        "schema_version": run_store.HARNESS_STATE_SCHEMA_VERSION,
        "phase": "routing",
        "execution_profile": _record(evidence),
    }
    version = {"value": 4}
    transitions = {"count": 0}

    async def _get(_run_id):
        return {"state": dict(state), "version": version["value"]}

    async def _transition(_run_id, *, expected_version, patch, phase=None):
        assert phase is None
        transitions["count"] += 1
        if transitions["count"] == 1:
            version["value"] += 1  # unrelated checkpoint won the first CAS
            return None
        assert expected_version == version["value"]
        state.update(patch)
        version["value"] += 1
        return {"state": dict(state), "version": version["value"]}

    original = execution_profile.resolve_profile_snapshot
    resolutions = {"count": 0}

    def _resolve(value):
        resolutions["count"] += 1
        return original(value)

    monkeypatch.setattr(run_store, "get_run_state", _get)
    monkeypatch.setattr(run_store, "transition_run_state", _transition)
    monkeypatch.setattr(execution_profile, "resolve_profile_snapshot", _resolve)

    snapshot = asyncio.run(run_store.finalize_execution_profile("r1"))
    assert snapshot["id"] == "interactive"
    assert transitions["count"] == 2
    assert resolutions["count"] == 1


def test_repeated_worker_restores_finalized_snapshot_without_resolver(monkeypatch):
    evidence = _evidence("帮我做一份演示文稿")
    state = {
        "schema_version": run_store.HARNESS_STATE_SCHEMA_VERSION,
        "phase": "routing",
        "execution_profile": _record(evidence),
    }
    version = {"value": 2}

    async def _get(_run_id):
        return {"state": dict(state), "version": version["value"]}

    async def _transition(_run_id, *, expected_version, patch, phase=None):
        assert expected_version == version["value"]
        state.update(patch)
        version["value"] += 1
        return {"state": dict(state), "version": version["value"]}

    monkeypatch.setattr(run_store, "get_run_state", _get)
    monkeypatch.setattr(run_store, "transition_run_state", _transition)
    first = asyncio.run(run_store.finalize_execution_profile("r1"))

    def _must_not_resolve(_value):
        raise AssertionError("finalized profile was inferred again")

    monkeypatch.setattr(execution_profile, "resolve_profile_snapshot", _must_not_resolve)
    second = asyncio.run(run_store.finalize_execution_profile("r1"))
    restored = asyncio.run(run_store.load_finalized_execution_profile("r1"))
    assert first == second == restored


def test_finalized_profile_cannot_be_revised_by_generic_cas(monkeypatch):
    evidence = _evidence("帮我生成 PPT")
    snapshot = execution_profile.resolve_profile_snapshot(evidence)

    class Row:
        state = {
            "schema_version": run_store.HARNESS_STATE_SCHEMA_VERSION,
            "phase": "routing",
            "execution_profile": {
                "status": "finalized",
                "resolver_version": evidence["resolver_version"],
                "evidence_hash": evidence["evidence_hash"],
                "evidence": evidence,
                "snapshot": snapshot,
            },
        }
        state_version = 8
        started_at = None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return Row()

        async def commit(self):
            raise AssertionError("immutable revision must not commit")

    monkeypatch.setattr(run_store, "runtime_session", lambda: lambda: Session())
    with pytest.raises(execution_profile.ExecutionProfileError, match="immutable"):
        asyncio.run(run_store.transition_run_state(
            "r1",
            expected_version=8,
            patch={"execution_profile": {"status": "unresolved"}},
        ))


@pytest.mark.asyncio
async def test_policy_rejected_run_is_never_selected_as_resume_source(monkeypatch):
    from app.services.agent_harness.public_errors import (
        SENSITIVE_WORDS_REJECTION_MESSAGE,
    )

    policy_run = SimpleNamespace(
        id="run-policy",
        state={
            **run_store.new_run_state(),
            "terminal_reason": SENSITIVE_WORDS_REJECTION_MESSAGE,
            "policy_rejection_context_quarantined": False,
        },
        agent_mode="standard",
        status="failed",
        error=SENSITIVE_WORDS_REJECTION_MESSAGE,
    )
    valid_run = SimpleNamespace(
        id="run-valid",
        state=run_store.new_run_state(),
        agent_mode="standard",
        status="completed",
        error=None,
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self, rows):
            self.rows = rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return Result(self.rows)

    rows = [policy_run, valid_run]
    monkeypatch.setattr(
        run_store,
        "runtime_session",
        lambda: lambda: Session(rows),
    )
    source = await run_store.load_latest_finalized_execution_profile_source(
        thread_id="thread-1",
        user_id="user-1",
    )
    assert source is not None
    assert source["run_id"] == "run-valid"

    rows[:] = [policy_run]
    source = await run_store.load_latest_finalized_execution_profile_source(
        thread_id="thread-1",
        user_id="user-1",
        preferred_run_id="run-policy",
    )
    assert source is None


def test_worker_finalizes_before_input_resolution_and_failure_is_not_downgraded(monkeypatch):
    from app import worker

    async def _fail(_run_id):
        raise execution_profile.ExecutionProfileError("profile store unavailable")

    monkeypatch.setattr(run_store, "finalize_execution_profile", _fail)
    with pytest.raises(execution_profile.ExecutionProfileError, match="unavailable"):
        asyncio.run(worker._finalize_worker_execution_profile("r1", {"message": "hello"}))

    source = inspect.getsource(worker._execute_job)
    assert source.index("_finalize_worker_execution_profile") < source.index("_resolve_worker_input")


def test_accept_persists_evidence_before_enqueue():
    from app.services.agent_harness.orchestrator import HarnessOrchestrator

    source = inspect.getsource(HarnessOrchestrator.accept_harness_run)
    inherit_at = source.index("load_latest_finalized_execution_profile")
    freeze_at = source.index("freeze_profile_evidence")
    store_at = source.index("store_execution_profile_evidence")
    enqueue_at = source.index("enqueue_job")
    assert inherit_at < freeze_at < store_at < enqueue_at


@pytest.mark.asyncio
async def test_accept_delivery_followup_inherits_ppt_profile_and_skill_ids(monkeypatch):
    from app.core import runtime_db
    from app.services.agent_harness.orchestrator import HarnessOrchestrator
    from app.services.tasks import snapshot_service, task_run_service

    captured: dict[str, object] = {}
    ppt_skill_id = "extract_34dbff5e54c84a2e89e6bb13cb8dcd1e"
    service = HarnessOrchestrator()

    async def _ensure_thread(_thread_id, _user_id, *, origin=None, workspace_folder_id=None):
        _ = (origin, workspace_folder_id)
        return "thread-ppt"

    async def _prepare_chat(_user_id, model):
        return "key", model or "model"

    async def _thread_models(_user_id, _thread_id):
        # accept_harness_run 在 _ensure_thread 之后还会读线程的粘性模型/预设/工作文件夹（走库）
        return {}

    async def _record_thread_model(_user_id, _thread_id, _model, *, update_setting):
        # 受理成功后把本次 Run 模型记回线程（走库），本用例不关心
        _ = update_setting

    async def _load_profile(**kwargs):
        captured["profile_lookup"] = kwargs
        return {
            "run_id": "source-run-ppt",
            "execution_profile": {
                "id": "artifact_coding",
                "qa_contract": {"image_requirement": {
                    "mode": "searched_photos",
                    "min_images": 3,
                    "min_sources": 0,
                    "brief": "需要几张库里比赛照片",
                }},
            },
        }

    async def _resume_skills(_user_id, _thread_id, *, source_run_id=""):
        captured["skill_source_run_id"] = source_run_id
        return [ppt_skill_id]

    async def _create_run(**kwargs):
        captured["run_id"] = kwargs["run_id"]
        return kwargs["run_id"]

    async def _true(*_args, **_kwargs):
        return True

    async def _store_evidence(_run_id, evidence):
        captured["evidence"] = evidence
        return True

    async def _save_state(_run_id, state):
        captured["saved_state"] = state
        return True

    async def _store_pending(_run_id, pending, *, access_token=""):
        captured["pending"] = pending
        captured["access_token"] = access_token
        return True

    async def _record(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime_db, "runtime_enabled", lambda: True)
    monkeypatch.setattr(service, "_ensure_thread", _ensure_thread)
    monkeypatch.setattr(service, "get_thread_model_setting", _thread_models)
    monkeypatch.setattr(service, "_record_thread_run_model", _record_thread_model)
    monkeypatch.setattr(service, "prepare_chat", _prepare_chat)
    monkeypatch.setattr(run_store, "load_latest_finalized_execution_profile_source", _load_profile)
    monkeypatch.setattr(run_store, "initialize_run_state", _true)
    monkeypatch.setattr(run_store, "store_execution_profile_evidence", _store_evidence)
    monkeypatch.setattr(run_store, "store_pending_input", _store_pending)
    monkeypatch.setattr(run_store, "enqueue_job", _true)
    monkeypatch.setattr(snapshot_service, "resolve_resume_skill_ids", _resume_skills)
    monkeypatch.setattr(task_run_service, "create_run", _create_run)
    monkeypatch.setattr(task_run_service, "save_run_state", _save_state)
    monkeypatch.setattr(task_run_service, "record_sse_payload", _record)

    result = await service.accept_harness_run(
        user_id="user-1",
        thread_id="thread-ppt",
        message="请直接交付",
        model="model",
        token="token",
        regenerate=True,
    )

    assert result["thread_id"] == "thread-ppt"
    assert captured["evidence"]["inherited_profile_id"] == "artifact_coding"
    assert captured["evidence"]["inherited_image_requirement"]["min_images"] == 3
    # Skill recovery is deferred to the worker; accept only persists the resume source.
    assert captured["pending"]["resume_source_run_id"] == "source-run-ppt"
    assert captured["pending"]["skill_ids"] == []
    assert "saved_state" not in captured
    assert "skill_source_run_id" not in captured
    assert captured["access_token"] == "token"


def test_run_snapshot_projects_only_valid_finalized_profile(monkeypatch):
    evidence = _evidence("做一份演示文稿")
    profile = execution_profile.resolve_profile_snapshot(evidence)

    class Row:
        id = "r1"
        thread_id = "t1"
        user_id = "u1"
        status = "running"
        outcome = None
        state_version = 2
        state = {
            **run_store.new_run_state(),
            "execution_profile": {
                "status": "finalized",
                "snapshot": profile,
            },
        }

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return Row()

        async def scalar(self, *_args, **_kwargs):
            return 0

    monkeypatch.setattr(run_store, "runtime_session", lambda: lambda: Session())
    snapshot = asyncio.run(run_store.get_run_snapshot("r1"))
    assert snapshot is not None
    assert snapshot.execution_profile == profile


def test_run_snapshot_rejects_corrupt_finalized_profile(monkeypatch):
    evidence = _evidence("做一份演示文稿")
    profile = execution_profile.resolve_profile_snapshot(evidence)
    profile["policy_hash"] = "corrupt"

    class Row:
        id = "r1"
        thread_id = "t1"
        user_id = "u1"
        status = "running"
        outcome = None
        state_version = 2
        state = {
            **run_store.new_run_state(),
            "execution_profile": {
                "status": "finalized",
                "snapshot": profile,
            },
        }

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return Row()

        async def scalar(self, *_args, **_kwargs):
            return 0

    monkeypatch.setattr(run_store, "runtime_session", lambda: lambda: Session())
    with pytest.raises(execution_profile.ExecutionProfileError, match="policy hash"):
        asyncio.run(run_store.get_run_snapshot("r1"))
