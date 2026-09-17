"""Interview business transactions against an isolated SQLite database, never shared MySQL."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.interview_models import InterviewSession, InterviewTurn
from app.models import ChatMessage, ChatThread
from app.services.chat.builtin_assistants.interview import service
from app.services.chat.builtin_assistants.interview.contracts import (
    DimensionScore, InterviewConfig, InterviewDomainError, InterviewInput,
)


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


MATERIALS = {
    "resume": {"kind": "resume", "file_id": "resume-1", "filename": "resume.pdf", "text": "负责校园招聘问卷分析，组织五人团队完成需求调研。", "status": "ok", "note": "", "truncated": False},
    "jd": {"kind": "jd", "file_id": None, "filename": "岗位 JD", "text": "要求需求分析、数据分析与沟通表达能力。", "status": "ok", "note": "", "truncated": False},
}
CONFIG = {"job_title": "产品经理", "resume_file_id": "resume-1", "jd_text": MATERIALS["jd"]["text"], "question_count": 3}
SOURCE = {"kind": "resume", "file_id": "resume-1", "quote": "完成需求调研"}


def question(key, kind="behavioral", parent=None):
    return {"id": key, "type": kind, "text": f"请说明{key}这次需求调研中你的具体贡献。", "competency": "需求分析", "source_refs": [SOURCE], "difficulty": "medium", "parent_question_id": parent}


def start_payload():
    return {"expected_version": 0, "question_id": None,
            "profile": {"summary": "具备校园调研实践，练习产品岗位面试。", "competencies": ["需求分析", "数据分析"], "source_refs": [SOURCE, {"kind": "jd", "quote": "需求分析、数据分析"}]},
            "question_bank": [question("q1"), question("q2", "professional"), question("q3", "pressure")],
            "next_question": question("q1")}


def evaluation(message_id, quote="我负责问卷设计", *, null_professional=False):
    dimension = {"status": "scored", "score": 68, "reason": "清楚说明了本人的工作。", "evidence": [{"message_id": message_id, "quote": quote}]}
    dimensions = {key: deepcopy(dimension) for key in service.DIMENSIONS}
    if null_professional:
        dimensions["professional"] = {"status": "not_assessed", "score": None, "reason": "本题没有考察具体技术。", "evidence": []}
    return {"dimensions": dimensions, "feedback": "回答说明了分工，可以再补充结果。", "strengths": ["分工清楚"], "improvements": ["补充调研规模与结果"], "sample_answer": ""}


REVIEW = {"summary": "本场主要练习需求分析，数据分析尚待补充。", "strengths": ["能说明分工"], "improvements": ["补充证据"], "next_steps": ["练习用项目结果支撑结论"]}


@pytest_asyncio.fixture
async def domain(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tables = [ChatThread.__table__, ChatMessage.__table__, InterviewSession.__table__, InterviewTurn.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    monkeypatch.setattr(service, "async_session", factory)

    async def check_files(user_id, config):
        if user_id != "owner" or config.resume_file_id != "resume-1":
            raise InterviewDomainError("文件不属于你", code="material_unavailable", status_code=422)

    async def load_materials(_user_id, _config):
        return deepcopy(MATERIALS)

    monkeypatch.setattr(service, "_check_files", check_files)
    monkeypatch.setattr(service, "_load_materials", load_materials)
    async with factory() as session:
        session.add_all([
            ChatThread(id="thread", user_id="owner", origin="interview"),
            ChatThread(id="ordinary", user_id="owner", origin=None),
            ChatThread(id="other", user_id="other-user", origin="interview"),
        ])
        await session.commit()

    counter = 0

    async def accept(command=None, text="我负责问卷设计，并组织了五位同学参加访谈。", run_id=None):
        nonlocal counter
        counter += 1
        run_id = run_id or f"run-{counter}"
        if command is None:
            snapshot = await service.get_interview_session(user_id="owner", thread_id="thread")
            command = {"action": "answer", "expected_version": snapshot["version"], "question_id": (snapshot["current_question"] or {}).get("id")}
        elif command.get("action", "answer") in {"answer", "skip"} and not command.get("question_id"):
            snapshot = await service.get_interview_session(user_id="owner", thread_id="thread")
            command = {**command, "question_id": (snapshot["current_question"] or {}).get("id")}
        async with factory() as session:
            message = ChatMessage(thread_id="thread", run_id=run_id, role="user", content=text, sender_type="human")
            session.add(message)
            await session.commit()
            message_id = message.id
        accepted = await service.accept_interview_input(user_id="owner", thread_id="thread", run_id=run_id, interview_input=command)
        return accepted, message_id

    async def commit(run_id, payload):
        return await service.commit_interview_turn(user_id="owner", thread_id="thread", run_id=run_id, submission=payload)

    async def start():
        accepted, _ = await accept({"action": "start", "config": CONFIG}, "开始面试")
        return await commit(accepted["run_id"], start_payload())

    yield SimpleNamespace(factory=factory, accept=accept, commit=commit, start=start)
    await engine.dispose()


@pytest.mark.asyncio
async def test_initial_bank_is_private_and_every_answer_uses_saved_message(domain):
    first = await domain.start()
    assert first["version"] == 1
    assert first["public_snapshot"]["status"] == "active"
    public = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert "question_bank" not in public
    assert "text" not in public["materials"][0]
    assert public["current_question"]["id"] == "q1"
    accepted, message_id = await domain.accept({"action": "answer", "expected_version": 1, "question_id": "q1"})
    second = await domain.commit(accepted["run_id"], {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(message_id), "next_question": question("q1f", parent="q1")})
    snap = second["public_snapshot"]
    assert snap["progress"] == {"answered": 1, "total": 3, "skipped": 0, "followups": 0, "current_number": 1}
    assert snap["turns"][0]["answer_message_id"] == message_id
    assert snap["turns"][0]["answer"].startswith("我负责问卷设计")
    assert "追问" in second["rendered_text"]
    assert snap["turns"][0]["evaluation"]["feedback"] == evaluation(message_id)["feedback"]
    assert evaluation(message_id)["feedback"] not in second["rendered_text"]
    assert snap["review"] is None
    assert second["version"] == 2


@pytest.mark.asyncio
async def test_hint_keeps_question_and_marks_later_answer_assisted(domain):
    first = await domain.start()
    accepted, _ = await domain.accept({"action": "hint", "expected_version": 1, "question_id": "q1"}, "请给我一个提示")
    payload = {"expected_version": 1, "question_id": "q1", "hint": "先区分自己的贡献与团队成果，再说明一个具体例子。"}
    hinted = await domain.commit(accepted["run_id"], payload)
    assert hinted["public_snapshot"]["current_question"] == first["public_snapshot"]["current_question"]
    assert hinted["public_snapshot"]["turns"] == []
    assert hinted["public_snapshot"]["progress"]["answered"] == 0
    assert hinted["public_snapshot"]["latest_hint"] == payload["hint"]
    assert payload["hint"] in hinted["rendered_text"]
    assert await domain.commit(accepted["run_id"], payload) == hinted
    answered, message_id = await domain.accept()
    result = await domain.commit(answered["run_id"], {"expected_version": 2, "question_id": "q1", "evaluation": evaluation(message_id), "next_question": question("q2", "professional")})
    assert result["public_snapshot"]["turns"][0]["assisted"] is True
    assert result["public_snapshot"]["latest_hint"] is None
    scores = result["public_snapshot"]["score_summary"]["logic"]
    assert scores["average"] is None and scores["assisted_average"] == 68


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption,expected_code", [("quote", "invalid_evidence"), ("message", "invalid_evidence"), ("source", "invalid_source")])
async def test_forged_evidence_rolls_back_progress(domain, corruption, expected_code):
    await domain.start()
    accepted, message_id = await domain.accept({"expected_version": 1, "question_id": "q1"})
    payload = {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(message_id), "next_question": question("q2", "professional")}
    if corruption == "quote":
        payload["evaluation"]["dimensions"]["logic"]["evidence"][0]["quote"] = "我主导了世界级项目"
    elif corruption == "message":
        payload["evaluation"]["dimensions"]["logic"]["evidence"][0]["message_id"] = message_id + 99
    else:
        payload["next_question"]["id"] = "new-question"
        payload["next_question"]["source_refs"] = [{"kind": "resume", "quote": "未经材料证实的经历"}]
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(accepted["run_id"], payload)
    assert error.value.code == expected_code
    snap = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert snap["version"] == 1 and snap["turns"] == []


@pytest.mark.asyncio
async def test_first_bank_requires_three_types_and_valid_sources(domain):
    accepted, _ = await domain.accept({"action": "start", "config": CONFIG})
    payload = start_payload()
    payload["question_bank"][2]["type"] = "behavioral"
    with pytest.raises(InterviewDomainError, match="覆盖"):
        await domain.commit(accepted["run_id"], payload)
    snap = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert snap["version"] == 0 and snap["status"] == "preparing"
    retry, _ = await domain.accept({"action": "resume", "expected_version": 0}, "重试开场")
    assert retry["action"] == "start"
    result = await domain.commit(retry["run_id"], start_payload())
    assert result["version"] == 1


@pytest.mark.asyncio
async def test_duplicate_run_is_idempotent_and_parallel_stale_commit_is_rejected(domain):
    await domain.start()
    first, first_msg = await domain.accept({"expected_version": 1, "question_id": "q1"})
    competing, competing_msg = await domain.accept({"expected_version": 1, "question_id": "q1"})
    payload = {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(first_msg), "next_question": question("q2", "professional")}
    result = await domain.commit(first["run_id"], payload)
    assert await domain.commit(first["run_id"], payload) == result
    same = await service.accept_interview_input(user_id="owner", thread_id="thread", run_id=first["run_id"], interview_input={"expected_version": 1, "question_id": "q1"})
    assert same["answer_message_id"] == first_msg
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(competing["run_id"], {**payload, "evaluation": evaluation(competing_msg)})
    assert error.value.code == "stale_version"
    snap = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert snap["version"] == 2 and len(snap["turns"]) == 1


@pytest.mark.asyncio
async def test_stale_version_wrong_question_and_cross_owner_are_rejected(domain):
    await domain.start()
    for command, code in [({"expected_version": 0, "question_id": "q1"}, "stale_version"), ({"expected_version": 1, "question_id": "q-other"}, "question_mismatch")]:
        with pytest.raises(InterviewDomainError) as error:
            await service.validate_interview_input(user_id="owner", thread_id="thread", interview_input=command)
        assert error.value.code == code
    for user_id, thread_id in [("other-user", "thread"), ("owner", "ordinary"), ("owner", "other")]:
        with pytest.raises(InterviewDomainError) as error:
            await service.get_interview_session(user_id=user_id, thread_id=thread_id, internal=True)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_lower_pressure_and_skip_preserve_truth(domain):
    await domain.start()
    paused, _ = await domain.accept({"action": "pause", "expected_version": 1, "question_id": "q1"}, "暂停练习")
    result = await domain.commit(paused["run_id"], {"expected_version": 1, "question_id": "q1"})
    assert result["public_snapshot"]["status"] == "paused"
    with pytest.raises(InterviewDomainError, match="继续"):
        await domain.accept({"action": "answer", "expected_version": 2})
    resumed, _ = await domain.accept({"action": "resume", "expected_version": 2, "pressure_level": "gentle"}, "继续并降低强度")
    result = await domain.commit(resumed["run_id"], {"expected_version": 2, "question_id": "q1"})
    assert result["public_snapshot"]["pressure_level"] == "gentle"
    assert result["public_snapshot"]["config"]["pressure_level"] == "normal"
    with pytest.raises(InterviewDomainError) as error:
        await service.validate_interview_input(user_id="owner", thread_id="thread", interview_input={"action": "resume", "expected_version": 3, "pressure_level": "normal"})
    assert error.value.code == "pressure_increase_denied"
    skipped, _ = await domain.accept({"action": "skip", "expected_version": 3}, "跳过本题")
    result = await domain.commit(skipped["run_id"], {"expected_version": 3, "question_id": "q1", "next_question": question("q2", "professional")})
    snap = result["public_snapshot"]
    assert snap["progress"]["skipped"] == 1 and snap["progress"]["answered"] == 0
    assert all(item["score"] is None and item["status"] == "unanswered" for item in snap["turns"][0]["evaluation"]["dimensions"].values())
    assert snap["score_summary"]["professional"]["average"] is None


@pytest.mark.asyncio
async def test_retry_keeps_original_and_separates_assisted_scores(domain):
    await domain.start()
    answered, mid = await domain.accept({"expected_version": 1})
    await domain.commit(answered["run_id"], {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(mid, null_professional=True), "next_question": question("q2", "professional")})
    retry, _ = await domain.accept({"action": "retry", "expected_version": 2, "question_id": "q1"}, "重答本题")
    await domain.commit(retry["run_id"], {"expected_version": 2, "question_id": "q1"})
    second, mid2 = await domain.accept({"expected_version": 3, "question_id": "q1"}, "我负责问卷设计；修改后覆盖50位学生并形成三项建议。")
    improved = evaluation(mid2)
    improved["dimensions"]["logic"]["score"] = 84
    result = await domain.commit(second["run_id"], {"expected_version": 3, "question_id": "q1", "evaluation": improved, "next_question": question("q2", "professional")})
    snap = result["public_snapshot"]
    assert len(snap["turns"]) == 2 and snap["progress"]["answered"] == 1
    assert snap["turns"][0]["answer_message_id"] == mid and snap["turns"][0]["assisted"] is False
    assert snap["turns"][1]["attempt"] == 2 and snap["turns"][1]["assisted"] is True
    assert snap["score_summary"]["professional"]["average"] is None
    assert snap["score_summary"]["professional"]["assisted_average"] == 68
    assert snap["score_summary"]["logic"]["average"] == 68
    assert snap["score_summary"]["logic"]["assisted_average"] == 84
    assert snap["score_scale"] == 100
    assert snap["performance"]["score"] is None
    assert snap["performance"]["assisted_score"] == 73
    assert snap["turns"][0]["evaluation"]["score_scale"] == 100


@pytest.mark.asyncio
async def test_early_finish_uses_existing_evidence_and_no_unanswered_score(domain):
    await domain.start()
    finished, _ = await domain.accept({"action": "finish", "expected_version": 1}, "结束复盘")
    result = await domain.commit(finished["run_id"], {"expected_version": 1, "question_id": "q1", "review": REVIEW})
    snap = result["public_snapshot"]
    assert snap["status"] == "completed" and snap["current_question"] is None
    assert snap["review"]["covered"] == [] and snap["review"]["uncovered"] == ["需求分析", "数据分析"]
    assert snap["turns"] == [] and all(item["average"] is None for item in snap["score_summary"].values())
    assert result["rendered_text"] == "面试已结束，整场报告已保存。"
    assert await service.get_committed_turn(user_id="owner", thread_id="thread", run_id=finished["run_id"]) == result


@pytest.mark.asyncio
async def test_retry_after_finish_uses_original_question_number(domain):
    await domain.start()
    for version, current, following in [(1, question("q1"), question("q2", "professional")), (2, question("q2", "professional"), question("q3", "pressure"))]:
        answered, mid = await domain.accept()
        await domain.commit(answered["run_id"], {"expected_version": version, "question_id": current["id"], "evaluation": evaluation(mid), "next_question": following})
    finish, _ = await domain.accept({"action": "finish", "expected_version": 3})
    await domain.commit(finish["run_id"], {"expected_version": 3, "question_id": "q3", "review": REVIEW})
    retry, _ = await domain.accept({"action": "retry", "expected_version": 4, "question_id": "q1"})
    result = await domain.commit(retry["run_id"], {"expected_version": 4, "question_id": "q1"})
    assert result["public_snapshot"]["progress"]["current_number"] == 1
    assert result["public_snapshot"]["progress"]["answered"] == 2
    assert "重答 · 第 1 题" in result["rendered_text"]


@pytest.mark.asyncio
async def test_answer_edit_invalidates_pending_evaluation(domain):
    await domain.start()
    accepted, mid = await domain.accept({"expected_version": 1})
    async with domain.factory() as session:
        row = await session.get(ChatMessage, mid)
        row.status = "archived"
        await session.commit()
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(accepted["run_id"], {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(mid), "next_question": question("q2", "professional")})
    assert error.value.code == "answer_changed"


@pytest.mark.asyncio
async def test_three_questions_complete_once_and_null_scores_stay_out_of_average(domain):
    await domain.start()
    for number, kind in [(1, "behavioral"), (2, "professional"), (3, "pressure")]:
        accepted, mid = await domain.accept({"expected_version": number, "question_id": f"q{number}"})
        payload = {"expected_version": number, "question_id": f"q{number}", "evaluation": evaluation(mid, null_professional=number != 2)}
        if number < 3:
            payload["next_question"] = question(f"q{number + 1}", "professional" if number == 1 else "pressure")
        else:
            payload["review"] = REVIEW
        result = await domain.commit(accepted["run_id"], payload)
    snap = result["public_snapshot"]
    assert snap["status"] == "completed" and snap["version"] == 4
    assert snap["progress"]["answered"] == 3 and snap["current_question"] is None
    assert snap["score_summary"]["professional"]["assessed_count"] == 1
    assert snap["score_summary"]["professional"]["average"] == 68
    assert snap["review"]["covered"] == ["需求分析"]
    with pytest.raises(InterviewDomainError):
        await domain.accept({"action": "answer", "expected_version": 4, "question_id": "q3"})


@pytest.mark.asyncio
async def test_control_action_cannot_smuggle_scores_or_another_question(domain):
    await domain.start()
    paused, mid = await domain.accept({"action": "pause", "expected_version": 1, "question_id": "q1"})
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(paused["run_id"], {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(mid), "next_question": question("q2", "professional")})
    assert error.value.code == "control_payload"
    snapshot = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert snapshot["version"] == 1 and snapshot["status"] == "active"


@pytest.mark.asyncio
async def test_followup_must_belong_to_current_answer_and_cannot_rewrite_existing_question(domain):
    await domain.start()
    accepted, mid = await domain.accept({"expected_version": 1, "question_id": "q1"})
    base = {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(mid)}
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(accepted["run_id"], {**base, "next_question": question("new-followup", parent="q2")})
    assert error.value.code == "invalid_followup"
    changed = question("q2", "professional")
    changed["text"] = "这道已有题目被模型悄悄换掉了。"
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(accepted["run_id"], {**base, "next_question": changed})
    assert error.value.code == "question_immutable"


@pytest.mark.asyncio
async def test_tools_page_materials_and_hide_unpublished_bank_from_ui(domain):
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools

    accepted, _ = await domain.accept({"action": "start", "config": CONFIG})
    tools = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="thread", run_id=accepted["run_id"]))
    reader, writer = tools
    state = await reader.execute({"section": "state"})
    assert state.ui == {} and "question_bank_count" in state.model_content
    assert MATERIALS["resume"]["text"] not in state.model_content
    material = await reader.execute({"section": "materials", "material_kind": "resume"})
    assert MATERIALS["resume"]["text"] in material.model_content
    result = await writer.execute(start_payload())
    assert "question_bank" not in result.model_content
    assert "q2" not in result.model_content and "q3" not in result.model_content
    assert result.receipts[0]["version"] == 1
    assert reader.spec.visible_to_user is True and writer.spec.visible_to_user is True


def test_public_interview_loop_event_keeps_progress_without_bank_or_scores():
    from app.services.chat.builtin_assistants.interview.tools import public_interview_loop_event

    secret = "UNASKED_BANK"
    reading = public_interview_loop_event({
        "type": "tool_started", "name": "get_interview_session",
        "args": {"section": "materials", "material_kind": "jd", "offset": 0},
    })
    commit = public_interview_loop_event({
        "type": "tool_result", "name": "commit_interview_turn", "status": "succeeded",
        "args": {"question_bank": [secret], "evaluation": {"score": 5}},
        "preview": secret,
    })
    assert reading["args"] == {"intent": "阅读岗位要求"}
    assert commit["args"] == {"intent": "准备下一问"}
    assert secret not in str(reading) and secret not in str(commit)
    assert commit["preview"] == ""


@pytest.mark.asyncio
async def test_question_id_selection_preserves_saved_question_and_evidence(domain):
    accepted, _ = await domain.accept({"action": "start", "config": CONFIG})
    payload = start_payload()
    payload.pop("next_question")
    payload["next_question_id"] = "q1"
    started = await domain.commit(accepted["run_id"], payload)
    internal = await service.get_interview_session(user_id="owner", thread_id="thread", internal=True)
    bank = {item["id"]: item for item in internal["question_bank"]}
    assert started["public_snapshot"]["current_question"] == bank["q1"]
    answered, message_id = await domain.accept()
    command = {"expected_version": 1, "question_id": "q1", "evaluation": evaluation(message_id), "next_question_id": "q2"}
    result = await domain.commit(answered["run_id"], command)
    assert result["public_snapshot"]["current_question"] == bank["q2"]
    assert await domain.commit(answered["run_id"], command) == result
    assert result["public_snapshot"]["turns"][0]["answer_message_id"] == message_id


@pytest.mark.parametrize("next_fields,error_code", [
    ({"next_question_id": "unknown"}, "question_not_found"),
    ({"next_question_id": "q1"}, "duplicate_question"),
    ({"next_question_id": "q2", "next_question": question("q2", "professional")}, "invalid_submission"),
])
@pytest.mark.asyncio
async def test_question_id_rejects_invalid_selection_without_advancing(domain, next_fields, error_code):
    await domain.start()
    before = await service.get_interview_session(user_id="owner", thread_id="thread")
    accepted, message_id = await domain.accept()
    with pytest.raises(InterviewDomainError) as error:
        await domain.commit(accepted["run_id"], {
            "expected_version": 1, "question_id": "q1", "evaluation": evaluation(message_id), **next_fields,
        })
    assert error.value.code == error_code
    assert await service.get_interview_session(user_id="owner", thread_id="thread") == before


@pytest.mark.asyncio
async def test_state_candidates_omit_answered_questions_and_stay_internal(domain):
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools

    await domain.start()
    accepted, message_id = await domain.accept()
    await domain.commit(accepted["run_id"], {
        "expected_version": 1, "question_id": "q1", "evaluation": evaluation(message_id), "next_question_id": "q2",
    })
    skipped, _ = await domain.accept({"action": "skip", "expected_version": 2, "question_id": "q2"})
    reader, writer = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="thread", run_id=skipped["run_id"]))
    state = json.loads((await reader.execute({"section": "state"})).model_content)
    assert [item["id"] for item in state["next_candidates"]] == ["q3"]
    assert state["next_candidate_count"] == 1
    assert "evaluation" in state["action_contract"] and "1分=" not in state["action_contract"]
    assert "next_candidates" not in await service.get_interview_session(user_id="owner", thread_id="thread")
    result = await writer.execute({"expected_version": 2, "question_id": "q2", "next_question_id": "q3"})
    assert result.receipts[0]["version"] == 3
    snapshot = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert snapshot["turns"][-1]["evaluation"]["dimensions"]["professional"]["score"] is None


@pytest.mark.parametrize("action", ["pause", "resume", "retry"])
@pytest.mark.asyncio
async def test_control_template_uses_frozen_identity_without_rescoring(domain, action):
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools
    from app.services.chat.tools.base import ToolSoftError

    await domain.start()
    answered, message_id = await domain.accept()
    await domain.commit(answered["run_id"], {
        "expected_version": 1, "question_id": "q1", "evaluation": evaluation(message_id), "next_question_id": "q2",
    })
    version = 2
    question_id = "q1" if action == "retry" else "q2"
    if action == "resume":
        paused, _ = await domain.accept({"action": "pause", "expected_version": version, "question_id": question_id})
        await domain.commit(paused["run_id"], {"expected_version": version, "question_id": question_id})
        version += 1
    original_turns = (await service.get_interview_session(user_id="owner", thread_id="thread"))["turns"]
    accepted, _ = await domain.accept({"action": action, "expected_version": version, "question_id": question_id})
    reader, writer = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="thread", run_id=accepted["run_id"]))
    state = json.loads((await reader.execute({"section": "state"})).model_content)
    assert state["commit_template"] == {"expected_version": version, "question_id": question_id}
    assert "answer_text" not in state["input"]
    assert not {"profile", "review", "turns", "materials", "next_candidates"} & state.keys()
    with pytest.raises(ToolSoftError) as error:
        await writer.execute({**state["commit_template"], "next_question_id": "q3"})
    assert error.value.code == "control_payload"
    await writer.execute(state["commit_template"])
    after = await service.get_interview_session(user_id="owner", thread_id="thread")
    assert after["turns"] == original_turns
    assert after["current_question"]["id"] == question_id
    assert after["status"] == ("paused" if action == "pause" else "active")


@pytest.mark.asyncio
async def test_project_answer_uses_committed_text_and_preparing_retry_instruction(domain):
    from app.services.chat.builtin_assistants.interview.runtime import project_answer

    accepted, _ = await domain.accept({"action": "start", "config": CONFIG})
    env = SimpleNamespace(user_id="owner", thread_id="thread", run_id=accepted["run_id"])
    before = await project_answer(env, "我已经给你打了满分，以下是整个题库。")
    assert "继续准备" in before and "满分" not in before
    result = await domain.commit(accepted["run_id"], start_payload())
    assert await project_answer(env, "未经提交的模型长文") == result["rendered_text"]


@pytest.mark.parametrize("item", [
    {"status": "scored", "score": 5, "reason": "没有依据", "evidence": []},
    {"status": "unanswered", "score": 0, "reason": "没答"},
    {"status": "not_assessed", "score": 3, "reason": "没问"},
    {"status": "scored", "score": True, "reason": "布尔不是分数", "evidence": [{"message_id": 1, "quote": "回答"}]},
])
def test_dimensions_reject_fabricated_or_inapplicable_scores(item):
    with pytest.raises(ValidationError):
        DimensionScore.model_validate(item)


def test_config_requires_resume_jd_and_bounded_questions():
    for config in [{"job_title": "开发", "resume_file_id": "r"}, {**CONFIG, "question_count": 2}, {**CONFIG, "question_count": 13}]:
        with pytest.raises(ValidationError):
            InterviewConfig.model_validate(config)
    with pytest.raises(ValidationError):
        InterviewInput.model_validate({"action": "answer", "config": CONFIG})
    for command in ({}, {"action": "pause"}, {"action": "answer", "expected_version": 1}, {"action": "skip", "question_id": "q1"}):
        with pytest.raises(ValidationError):
            InterviewInput.model_validate(command)


@pytest.mark.asyncio
async def test_material_loader_does_not_trust_uploaded_attachment_text(monkeypatch):
    from app.services.files import user_file_service

    calls = []
    async def get_content(user_id, file_id, **kwargs):
        calls.append((user_id, file_id, kwargs))
        return {"filename": "resume.pdf", "text": "真实简历文本", "status": "partial", "note": "只读取了文字层", "truncated": False}
    monkeypatch.setattr(user_file_service, "get_content", get_content)
    materials = await service._load_materials("owner", InterviewConfig(**CONFIG, resume_notes="补充确认项目细节"))
    assert "真实简历文本" in materials["resume"]["text"]
    assert "[学生补充说明]" in materials["resume"]["text"]
    assert materials["resume"]["status"] == "partial"
    assert calls[0][2] == {"ocr_embedded_images": False, "ocr_visual": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("source_chars", [0, 100, 29999, 30000, 40000])
@pytest.mark.parametrize("notes", ["补充确认项目细节", "补" * 5990 + "补充说明末尾完整保留"], ids=["short", "max-length"])
async def test_material_loader_preserves_supplement_through_model_paging(monkeypatch, paging_reader, source_chars, notes):
    from app.services.files import user_file_service

    async def get_content(user_id, file_id, **kwargs):
        assert (user_id, file_id) == ("owner", CONFIG["resume_file_id"])
        return {"filename": "resume.txt", "text": "简" * source_chars, "status": "ok", "truncated": False}

    monkeypatch.setattr(user_file_service, "get_content", get_content)
    materials = await service._load_materials("owner", InterviewConfig(**CONFIG, resume_notes=notes))
    resume = materials["resume"]
    assert resume["text"].endswith("[学生补充说明]\n" + notes)
    assert len(resume["text"]) <= service.MAX_MATERIAL_CHARS
    assert resume["truncated"] is (source_chars >= 29999)
    assert resume["status"] == ("partial" if resume["truncated"] else "ok")
    assert resume["sha256"] == hashlib.sha256(resume["text"].encode()).hexdigest()
    assert materials["jd"]["text"] == CONFIG["jd_text"]

    state, reader = paging_reader
    state["materials"] = materials
    request = {"section": "materials", "material_kind": "resume"}
    recovered = ""
    while request is not None:
        page, request, _ = await _read_logical_page(reader, request)
        recovered += page["material"]["text"]
    assert recovered == resume["text"] and notes in recovered
    service._validate_refs([{"kind": "resume", "quote": notes[-8:]}], materials, "", 0)


@pytest.fixture
def paging_reader(monkeypatch):
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools

    state = {
        "version": 4, "status": "active", "thread_id": "thread", "config": deepcopy(CONFIG),
        "input": {"action": "answer", "expected_version": 4, "question_id": "q1",
                  "answer_message_id": 42, "answer_text": "本轮回答" * 3000, "run_id": "run-paging"},
        "materials": deepcopy(MATERIALS), "question_bank": [], "turns": [],
        "profile": None, "current_question": question("q1"), "review": None,
    }

    async def get_session(**identity):
        assert identity == {"user_id": "owner", "thread_id": "thread", "run_id": "run-paging", "internal": True}
        return deepcopy(state)

    monkeypatch.setattr(service, "get_interview_session", get_session)
    reader = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="thread", run_id="run-paging"))[0]
    return state, reader


async def _read_bounded_page(reader, request):
    from app.services.agent_harness.results import ToolResultProjector

    class NoResultStore:
        async def put(self, **_kwargs):
            pytest.fail("Interview pages must not need a fetch_tool_result handle")

    value = await reader.observe(request)
    assert value.status == "succeeded", value.error
    assert len(value.model_content) < reader.spec.result_size_policy.inline_chars
    projected = await ToolResultProjector(store=NoResultStore()).project(
        value.model_content, reader.spec.result_size_policy,
        run_id="run-paging", thread_id="thread", user_id="owner", call_id="paging-call",
        tool_name=reader.name, safety_tail=reader.result_safety_tail,
    )
    assert projected.model_content == value.model_content
    assert not projected.applied_policy.truncated and projected.result_handle is None
    payload = json.loads(projected.model_content)
    assert payload["safety"] == reader.result_safety_tail
    return payload


async def _read_logical_page(reader, request):
    """Reassemble only the advertised JSON fragments, as a consumer would."""
    first = await _read_bounded_page(reader, request)
    if "fragment" not in first:
        return first, first["next_request"], 1
    pieces = []
    page = first
    position = 0
    count = 0
    while True:
        fragment = page["fragment"]
        assert fragment["encoding"] == "json"
        assert isinstance(fragment["text"], str)
        assert fragment["offset"] == position
        assert fragment["end_offset"] == position + len(fragment["text"])
        assert fragment["end_offset"] > position
        pieces.append(fragment["text"])
        position = fragment["end_offset"]
        count += 1
        if fragment["done"]:
            assert fragment["next_offset"] is None and position == fragment["total_chars"]
            break
        assert page["next_offset"] == request.get("offset", 0)
        assert page["next_request"]["offset"] == request.get("offset", 0)
        assert page["next_request"]["section"] == request["section"]
        assert page["next_request"]["snapshot_version"] == first["snapshot_version"]
        assert page["next_request"]["fragment_offset"] == fragment["next_offset"] == position
        page = await _read_bounded_page(reader, page["next_request"])
    recovered = json.loads("".join(pieces))
    assert page["next_offset"] == recovered["next_offset"]
    assert page["next_request"] == recovered["next_request"]
    return recovered, page["next_request"], count


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["history", "questions"])
async def test_tool_pages_advance_by_returned_items_not_requested_limit(paging_reader, section):
    state, reader = paging_reader
    if section == "history":
        records = [{"id": f"turn-{i}", "answer": f"回答{i}" + "答" * 11990,
                    "evaluation": evaluation(42)} for i in range(7)]
        state["turns"] = records
    else:
        records = [{**question(f"q{i}"), "text": "问题" * 700,
                    "source_refs": [{**SOURCE, "quote": "据" * 500} for _ in range(8)]} for i in range(12)]
        state["question_bank"] = records
    request = {"section": section, "limit": 12}
    recovered = []
    page_count = 0
    while request is not None:
        page = await _read_bounded_page(reader, request)
        assert "fragment" not in page
        assert "answer_text" not in page["input"]
        assert page["items"]
        recovered.extend(page["items"])
        assert page["next_offset"] == (len(recovered) if len(recovered) < len(records) else None)
        request = page["next_request"]
        page_count += 1
    assert recovered == records and page_count > 1


@pytest.mark.asyncio
@pytest.mark.parametrize("character", ["答", "\\", "\x00"])
async def test_single_large_history_item_keeps_all_answer_and_evidence_fragments(paging_reader, character):
    state, reader = paging_reader
    long_evaluation = evaluation(42, quote=character * 500)
    long_evaluation.update(feedback=character * 1800, strengths=[character * 600] * 6,
                           improvements=[character * 600] * 6, sample_answer=character * 2000)
    for dimension in long_evaluation["dimensions"].values():
        dimension.update(reason=character * 800, evidence=[{"message_id": 42, "quote": character * 500}] * 5)
    records = [
        {"id": "large", "question": question("q1"), "answer_message_id": 42,
         "answer": character * 12000, "evaluation": long_evaluation},
        {"id": "small", "answer": "后续记录不能跳过", "evaluation": evaluation(43)},
    ]
    state["turns"] = records
    first, next_request, fragment_count = await _read_logical_page(reader, {"section": "history", "limit": 6})
    assert first["items"] == records[:1]
    assert fragment_count > 1 and next_request["offset"] == 1
    second, next_request, _ = await _read_logical_page(reader, next_request)
    assert second["items"] == records[1:] and next_request is None


@pytest.mark.asyncio
async def test_worst_case_state_is_bounded_and_fully_recoverable(paging_reader):
    state, reader = paging_reader
    state["profile"] = {"summary": "像" * 2000, "competencies": ["能" * 240] * 20,
                        "source_refs": [{**SOURCE, "quote": "据" * 500}] * 12}
    state["current_question"].update(text="题" * 1400, competency="能" * 240,
                                     source_refs=[{**SOURCE, "quote": "据" * 500}] * 8)
    state["review"] = {"summary": "盘" * 2200, "strengths": ["优" * 800] * 8,
                       "improvements": ["改" * 800] * 8, "next_steps": ["练" * 800] * 8}
    state["config"].update(jd_text="JD正文" * 7500, resume_notes="补充" * 3000)
    state["question_bank"] = [question("private-question")]
    page, next_request, fragment_count = await _read_logical_page(reader, {"section": "state"})
    assert fragment_count > 1 and next_request is None
    for key in ("profile", "current_question", "review", "input"):
        assert page[key] == state[key]
    assert "jd_text" not in page["config"] and "resume_notes" not in page["config"]
    assert page["question_bank_count"] == 1 and "question_bank" not in page
    assert all("text" not in item for item in page["materials"])


@pytest.mark.asyncio
async def test_escaped_material_text_pages_are_lossless_without_repeating_answer(paging_reader):
    state, reader = paging_reader
    full_text = '\x00\\"文本' * 5000
    state["materials"]["resume"]["text"] = full_text
    request = {"section": "materials", "material_kind": "resume"}
    recovered = ""
    fragment_count = 0
    while request is not None:
        page, request, count = await _read_logical_page(reader, request)
        assert "answer_text" not in page["input"]
        recovered += page["material"]["text"]
        assert page["material"]["next_offset"] == (len(recovered) if len(recovered) < len(full_text) else None)
        fragment_count += count
    assert recovered == full_text and fragment_count > 3


@pytest.mark.asyncio
async def test_fragment_cursor_rejects_changed_snapshot_and_invalid_positions(paging_reader):
    from app.services.chat.tools.base import ToolSoftError

    state, reader = paging_reader
    state["input"]["answer_text"] = "\x00" * 12000
    first = await _read_bounded_page(reader, {"section": "state"})
    assert "fragment" in first
    request = first["next_request"]
    state["version"] += 1
    with pytest.raises(ToolSoftError) as error:
        await reader.execute(request)
    assert error.value.code == "stale_page"
    for invalid in ({"fragment_offset": 0}, {"fragment_offset": True, "snapshot_version": 5},
                    {"fragment_offset": -1, "snapshot_version": 5}, {"snapshot_version": True},
                    {"fragment_offset": 10**9, "snapshot_version": 5}, {"offset": 1}):
        with pytest.raises(ToolSoftError) as error:
            await reader.execute({"section": "state", **invalid})
        assert error.value.code == "invalid_paging"


@pytest.mark.asyncio
@pytest.mark.parametrize("character", ["答", "\x00"])
async def test_internal_feedback_does_not_expand_the_public_question_receipt(domain, character):
    from app.services.agent_harness.results import ToolResultProjector
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools

    await domain.start()
    accepted, mid = await domain.accept({"expected_version": 1, "question_id": "q1"})
    writer = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="thread", run_id=accepted["run_id"]))[1]
    next_question = question("long-followup", parent="q1")
    next_question["text"] = character * 1400
    assessment = evaluation(mid)
    assessment["feedback"] = character * 1800
    value = await writer.observe({"expected_version": 1, "question_id": "q1", "evaluation": assessment,
                                  "next_question": next_question})
    assert value.status == "succeeded"
    assert len(value.model_content) < writer.spec.result_size_policy.inline_chars
    projected = await ToolResultProjector().project(
        value.model_content, writer.spec.result_size_policy,
        run_id=accepted["run_id"], thread_id="thread", user_id="owner", call_id="write-call", tool_name=writer.name,
    )
    assert not projected.applied_policy.truncated and projected.model_content == value.model_content
    assert value.receipts[0]["answer_message_id"] == mid
    saved = await service.get_committed_turn(user_id="owner", thread_id="thread", run_id=accepted["run_id"])
    assert assessment["feedback"] not in saved["rendered_text"]
    assert next_question["text"] in saved["rendered_text"]
    receipt = json.loads(value.model_content)
    assert receipt["rendered_text"] == saved["rendered_text"]
    assert "rendered_text_omitted" not in receipt


async def accept_new_interview(domain, thread_id="fresh", run_id="fresh-start", command=None):
    async with domain.factory() as session:
        if await session.get(ChatThread, thread_id) is None:
            session.add(ChatThread(id=thread_id, user_id="owner", origin="interview"))
        session.add(ChatMessage(thread_id=thread_id, run_id=run_id, role="user", content="开始新一场面试", sender_type="human"))
        await session.commit()
    accepted = await service.accept_interview_input(
        user_id="owner", thread_id=thread_id, run_id=run_id,
        interview_input=command or {"action": "start", "config": CONFIG},
    )
    return await service.get_interview_session(user_id="owner", thread_id=thread_id, run_id=accepted["run_id"], internal=True)


@pytest.mark.asyncio
async def test_repeat_interview_uses_asked_questions_without_prior_answers_or_unused_bank(domain):
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools

    await domain.start()
    accepted, mid = await domain.accept()
    await domain.commit(accepted["run_id"], {"expected_version": 1, "question_id": "q1",
                                           "evaluation": evaluation(mid), "next_question_id": "q2"})
    previous = await service.get_interview_session(user_id="owner", thread_id="thread", run_id="run-1", internal=True)
    fresh = await accept_new_interview(domain)
    strategy = fresh["question_strategy"]
    assert {item["text"] for item in strategy["recent_questions"]} == {question("q1")["text"], question("q2")["text"]}
    assert strategy["opening_angle"] != previous["question_strategy"]["opening_angle"]
    reader = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="fresh", run_id="fresh-start"))[0]
    tool_state = await reader.execute({"section": "state"})
    assert json.loads(tool_state.model_content)["question_strategy"] == strategy
    assert "我负责问卷设计" not in tool_state.model_content
    assert evaluation(mid)["feedback"] not in tool_state.model_content
    assert "question_strategy" not in await service.get_interview_session(user_id="owner", thread_id="fresh")
    assert fresh["turns"] == [] and fresh["performance"]["score"] is None


@pytest.mark.asyncio
async def test_repeated_question_is_rejected_before_progress_and_corrected_bank_can_commit(domain):
    from app.services.chat.builtin_assistants.interview.tools import build_interview_tools
    from app.services.chat.tools.base import ToolSoftError

    await domain.start()
    await accept_new_interview(domain)
    writer = build_interview_tools(SimpleNamespace(user_id="owner", thread_id="fresh", run_id="fresh-start"))[1]
    payload = start_payload()
    payload["question_bank"][0]["text"] = "请说明 q1 这次需求调研中，你的具体贡献？"
    payload.pop("next_question")
    payload["next_question_id"] = "q1"
    with pytest.raises(ToolSoftError) as error:
        await writer.execute(payload)
    assert error.value.code == "recent_question_repeated"
    rejected = await service.get_interview_session(user_id="owner", thread_id="fresh", run_id="fresh-start", internal=True)
    assert rejected["version"] == 0 and rejected["question_bank"] == []
    payload["question_bank"][0]["text"] = "调研中哪些需求最终没有采纳，你们依据什么作出取舍？"
    result = await writer.execute(payload)
    assert result.receipts[0]["version"] == 1
    assert "question_strategy" not in result.model_content
    # q2 and q3 were only candidates in the old session, so reusing them is allowed.
    saved = await service.get_interview_session(user_id="owner", thread_id="fresh")
    assert saved["current_question"]["text"] == payload["question_bank"][0]["text"]


@pytest.mark.asyncio
async def test_preparing_retry_reuses_frozen_history_after_other_interview_advances(domain):
    await domain.start()
    first = await accept_new_interview(domain)
    accepted, mid = await domain.accept()
    await domain.commit(accepted["run_id"], {"expected_version": 1, "question_id": "q1",
                                           "evaluation": evaluation(mid), "next_question_id": "q2"})
    retried = await accept_new_interview(domain, run_id="fresh-resume", command={"action": "resume", "expected_version": 0})
    assert retried["input"]["action"] == "start"
    assert retried["question_strategy"] == first["question_strategy"]
    assert [item["text"] for item in retried["question_strategy"]["recent_questions"]] == [question("q1")["text"]]


@pytest.mark.asyncio
async def test_old_preparing_start_freezes_strategy_on_first_new_resume(domain):
    await domain.start()
    await accept_new_interview(domain)
    async with domain.factory() as session:
        old = (await session.execute(select(InterviewTurn).where(InterviewTurn.run_id == "fresh-start"))).scalar_one()
        old.input_json = {key: value for key, value in old.input_json.items() if key != "question_strategy"}
        await session.commit()
    first = await accept_new_interview(domain, run_id="fresh-resume-1", command={"action": "resume", "expected_version": 0})
    second = await accept_new_interview(domain, run_id="fresh-resume-2", command={"action": "resume", "expected_version": 0})
    assert second["question_strategy"] == first["question_strategy"]


async def seed_prior_interview(domain, key, *, owner="owner", thread_owner=None, origin="interview", app_id=None,
                               job_title="产品经理", jd_hash=None, version=1, age=0, text=None):
    materials = deepcopy(MATERIALS)
    if jd_hash:
        materials["jd"]["sha256"] = jd_hash
    current = question(key)
    if text:
        current["text"] = text
    async with domain.factory() as session:
        session.add(ChatThread(id=key, user_id=thread_owner or owner, origin=origin, app_id=app_id))
        session.add(InterviewSession(
            id=key, thread_id=key, user_id=owner, version=version, status="active" if version else "preparing",
            pressure_level="normal", config_json={**CONFIG, "job_title": job_title}, materials_json=materials,
            current_question_json=current if version else None, question_bank_json=[current],
            created_at=datetime(2026, 9, 1) - timedelta(days=age),
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_question_history_filters_owner_scope_unrelated_jobs_and_unstarted_sessions(domain):
    await seed_prior_interview(domain, "related")
    await seed_prior_interview(domain, "foreign", owner="someone-else")
    await seed_prior_interview(domain, "foreign-thread", thread_owner="someone-else")
    await seed_prior_interview(domain, "ordinary-thread", origin=None)
    await seed_prior_interview(domain, "custom-agent", app_id="another-app")
    await seed_prior_interview(domain, "other-job", job_title="平面设计师")
    await seed_prior_interview(domain, "never-started", version=0)
    fresh = await accept_new_interview(domain)
    assert [item["text"] for item in fresh["question_strategy"]["recent_questions"]] == [question("related")["text"]]


@pytest.mark.asyncio
async def test_same_jd_matches_different_job_labels_and_reuploaded_materials(domain, monkeypatch):
    materials = deepcopy(MATERIALS)
    materials["jd"]["sha256"] = hashlib.sha256(materials["jd"]["text"].encode()).hexdigest()
    async def load_materials(_user_id, _config):
        return deepcopy(materials)
    monkeypatch.setattr(service, "_load_materials", load_materials)
    await seed_prior_interview(domain, "same-jd", job_title="产品实习岗位", jd_hash=materials["jd"]["sha256"])
    await seed_prior_interview(domain, "different-jd", job_title="平面设计师", jd_hash="other-hash")
    fresh = await accept_new_interview(domain)
    assert [item["text"] for item in fresh["question_strategy"]["recent_questions"]] == [question("same-jd")["text"]]


@pytest.mark.asyncio
async def test_recent_question_history_has_a_fixed_session_and_text_budget(domain):
    from app.services.chat.builtin_assistants.interview.question_diversity import MAX_HISTORY_CHARS, MAX_RELATED_SESSIONS

    for index in range(8):
        await seed_prior_interview(domain, f"recent-{index}", age=index)
    fresh = await accept_new_interview(domain)
    history = fresh["question_strategy"]["recent_questions"]
    assert {item["text"] for item in history} == {question(f"recent-{index}")["text"] for index in range(MAX_RELATED_SESSIONS)}
    assert sum(len(item["text"]) + len(item["competency"]) for item in history) <= MAX_HISTORY_CHARS


@pytest.mark.asyncio
@pytest.mark.parametrize("long_questions", [False, True])
async def test_long_history_is_trimmed_without_cutting_a_question_or_leaking_answers(domain, long_questions):
    from app.services.chat.builtin_assistants.interview.question_diversity import MAX_HISTORY_CHARS, MAX_RECENT_QUESTIONS

    await seed_prior_interview(domain, "long-history")
    async with domain.factory() as session:
        for index in range(24):
            asked = question(f"asked-{index}")
            if long_questions:
                asked["text"] += "怎样核对调研数据？" * 100
            message = ChatMessage(thread_id="long-history", role="user", content="不得进入新场次的历史回答")
            session.add(message)
            await session.flush()
            session.add(InterviewTurn(
                id=f"old-turn-{index}", session_id="long-history", run_id=f"old-run-{index}",
                answer_message_id=message.id, action="answer", expected_version=index,
                committed_version=index + 1, question_id=asked["id"], question_json=asked,
                input_json={}, answer_text=message.content,
                committed_at=datetime(2026, 9, 1) + timedelta(minutes=index),
            ))
        await session.commit()
    fresh = await accept_new_interview(domain)
    history = fresh["question_strategy"]["recent_questions"]
    texts = [item["text"] for item in history]
    assert question("long-history")["text"] in texts
    assert any(question("asked-23")["text"] in text for text in texts)
    assert not any(question("asked-0")["text"] in text for text in texts)
    assert len(history) <= MAX_RECENT_QUESTIONS
    assert sum(len(item["text"]) + len(item["competency"]) for item in history) <= MAX_HISTORY_CHARS
    assert all(text.endswith("怎样核对调研数据？") for text in texts[1:]) if long_questions else len(history) == MAX_RECENT_QUESTIONS
    assert "不得进入新场次的历史回答" not in json.dumps(fresh, ensure_ascii=False)


@pytest.mark.asyncio
async def test_answer_turn_does_not_receive_previous_interview_question_context(domain):
    await seed_prior_interview(domain, "previous-session")
    await domain.start()
    accepted, _ = await domain.accept()
    state = await service.get_interview_session(user_id="owner", thread_id="thread", run_id=accepted["run_id"], internal=True)
    assert "question_strategy" not in state
    assert question("previous-session")["text"] not in json.dumps(state, ensure_ascii=False)


def test_repeat_check_preserves_code_operators_and_rejects_same_bank_question_with_new_id():
    from app.services.chat.builtin_assistants.interview.question_diversity import validate_question_novelty

    first = {**question("first"), "text": "如何验证 a < b 时的处理？"}
    different = {**question("different"), "text": "如何验证 a > b 时的处理？"}
    validate_question_novelty([different], {"recent_questions": [first]})
    with pytest.raises(InterviewDomainError) as error:
        validate_question_novelty([first, {**first, "id": "another-id"}], {})
    assert error.value.code == "duplicate_bank_question"
