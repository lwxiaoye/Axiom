"""Transactional interview state; model generation stays in the existing Harness."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePath
import uuid

from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import async_session
from app.interview_models import InterviewSession, InterviewTurn
from app.models import ChatMessage, ChatThread
from .contracts import (
    InterviewConfig, InterviewDomainError, InterviewInput, InterviewQuestion,
    InterviewTurnCommit,
)
from .scoring import summarize_performance
from .question_diversity import build_question_strategy, validate_question_novelty

DIMENSIONS = ("professional", "logic", "expression")
PRESSURE_ORDER = {"gentle": 0, "normal": 1, "challenging": 2}
MATERIAL_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MAX_MATERIAL_CHARS = 30000
MAX_BANK_QUESTIONS = 96


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _input(value) -> InterviewInput:
    try:
        return value if isinstance(value, InterviewInput) else InterviewInput.model_validate(value or {})
    except ValidationError as exc:
        raise InterviewDomainError(str(exc), code="invalid_interview_input", status_code=422) from exc


async def _thread(session, user_id: str, thread_id: str, *, lock: bool = False):
    stmt = select(ChatThread).where(
        ChatThread.id == thread_id, ChatThread.user_id == user_id,
        ChatThread.origin == "interview", ChatThread.app_id.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise InterviewDomainError("面试会话不存在。", code="not_found", status_code=404)
    return row


async def _session(session, user_id: str, thread_id: str, *, lock: bool = False):
    stmt = select(InterviewSession).where(
        InterviewSession.thread_id == thread_id, InterviewSession.user_id == user_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def _turns(session, session_id: str) -> list[InterviewTurn]:
    rows = await session.execute(select(InterviewTurn).where(
        InterviewTurn.session_id == session_id,
        InterviewTurn.committed_version.isnot(None),
    ).order_by(InterviewTurn.committed_version, InterviewTurn.id))
    return list(rows.scalars().all())


def _validate_command(command: InterviewInput, row, turns: list[InterviewTurn]):
    if row is None:
        if command.action != "start":
            raise InterviewDomainError("请先上传简历并确认岗位 JD，开始一场面试。", code="not_started")
        if command.expected_version not in (None, 0):
            raise InterviewDomainError("面试版本已变化，请刷新后重试。", code="stale_version")
        return
    if command.action == "start" and (row.status != "preparing" or row.version != 0):
        raise InterviewDomainError("这场面试已经开始；更换材料请新建面试。", code="already_started")
    if command.expected_version is not None and command.expected_version != row.version:
        raise InterviewDomainError("面试进度已变化，请刷新当前题后重试。", code="stale_version")
    if command.pressure_level and PRESSURE_ORDER[command.pressure_level] > PRESSURE_ORDER[row.pressure_level]:
        raise InterviewDomainError("本场只能降低压力强度；提高强度请开始新的面试。", code="pressure_increase_denied", status_code=422)
    if command.action == "resume" and row.status == "preparing" and row.version == 0:
        return
    current = row.current_question_json or {}
    if command.action == "retry":
        if not command.question_id or not any(
            turn.action == "answer" and turn.question_id == command.question_id for turn in turns
        ):
            raise InterviewDomainError("只能重答本场已经提交过回答的题目。", code="retry_question_missing")
    elif command.question_id and command.question_id != current.get("id"):
        raise InterviewDomainError("这份回答对应的题目已经变化，请刷新当前题。", code="question_mismatch")
    if command.action in {"answer", "skip", "hint"} and (row.status != "active" or not current):
        raise InterviewDomainError("请先继续练习并确认当前题目。", code="not_active")
    if command.action == "pause" and row.status not in {"active", "paused"}:
        raise InterviewDomainError("当前面试不能暂停。", code="not_active")
    if command.action == "resume" and row.status not in {"active", "paused"}:
        raise InterviewDomainError("当前面试不能继续，请新建面试或重答已答题。", code="not_active")
    if command.action == "finish" and row.status not in {"active", "paused"}:
        raise InterviewDomainError("当前没有可结束的面试。", code="not_active")


async def _check_files(user_id: str, config: InterviewConfig):
    from app.services.files import user_file_service

    for file_id in (config.resume_file_id, config.jd_file_id):
        if not file_id:
            continue
        try:
            row, _ = await user_file_service.get_file(user_id, file_id)
        except user_file_service.UserFileError as exc:
            raise InterviewDomainError("简历或 JD 文件不存在、已过期或不属于你。", code="material_unavailable", status_code=422) from exc
        if PurePath(row.filename).suffix.lower() not in MATERIAL_EXTENSIONS:
            raise InterviewDomainError("简历和 JD 支持 PDF、DOCX、TXT 或 Markdown 文件。", code="material_type", status_code=422)
        if int(row.size_bytes or 0) > 20 * 1024 * 1024:
            raise InterviewDomainError("简历或 JD 文件超过 20 MiB。", code="material_size", status_code=422)


async def validate_interview_input(*, user_id: str, thread_id: str | None, interview_input=None) -> dict:
    """Read-only preflight. Acceptance repeats version and ownership checks under a lock."""
    command = _input(interview_input)
    if thread_id:
        async with async_session() as session:
            await _thread(session, user_id, thread_id)
            row = await _session(session, user_id, thread_id)
            turns = await _turns(session, row.id) if row else []
            _validate_command(command, row, turns)
    else:
        _validate_command(command, None, [])
    if command.config:
        await _check_files(user_id, command.config)
    return command.model_dump(mode="json")


async def _load_materials(user_id: str, config: InterviewConfig) -> dict:
    from app.services.files import user_file_service

    materials = {}
    for kind, file_id in (("resume", config.resume_file_id), ("jd", config.jd_file_id)):
        if kind == "jd" and config.jd_text:
            materials[kind] = {"kind": kind, "file_id": file_id, "filename": "岗位 JD（学生确认）", "text": config.jd_text, "status": "ok", "note": "", "truncated": False}
            continue
        try:
            parsed = await asyncio.wait_for(user_file_service.get_content(
                user_id, file_id, ocr_embedded_images=False, ocr_visual=False,
            ), timeout=30)
        except (user_file_service.UserFileError, asyncio.TimeoutError) as exc:
            raise InterviewDomainError("材料读取失败，请重新上传或补充可读取的文字版材料。", code="material_unavailable", status_code=422) from exc
        text = str(parsed.get("text") or "").strip()
        supplement = f"\n\n[学生补充说明]\n{config.resume_notes}" if kind == "resume" and config.resume_notes else ""
        truncated = bool(parsed.get("truncated")) or len(text) + len(supplement) > MAX_MATERIAL_CHARS
        # Reserve room for the student's explicit corrections before trimming the source.
        text = (text[:MAX_MATERIAL_CHARS - len(supplement)] + supplement).strip()
        if not text:
            raise InterviewDomainError("材料未解析出文字；请上传文字版 PDF/DOCX，或在简历补充说明中填写材料内容。", code="material_empty", status_code=422)
        status = str(parsed.get("status") or "ok")
        if truncated and status == "ok":
            status = "partial"
        materials[kind] = {
            "kind": kind, "file_id": file_id, "filename": str(parsed.get("filename") or ""),
            "text": text, "status": status,
            "note": str(parsed.get("note") or ("材料较长，仅依据已解析部分练习。" if truncated else "")),
            "truncated": truncated,
        }
    for material in materials.values():
        material["sha256"] = hashlib.sha256(material["text"].encode()).hexdigest()
    return materials


async def accept_interview_input(*, user_id: str, thread_id: str, run_id: str,
                                 interview_input=None, message: str = "", attachments=None) -> dict:
    """Freeze the real saved human input before the shared Worker job is enqueued."""
    command = _input(interview_input)
    materials = None
    # File parsing is outside the transaction and never invokes a model/OCR.
    if command.action == "start":
        await _check_files(user_id, command.config)
        materials = await _load_materials(user_id, command.config)
    async with async_session() as session:
        async with session.begin():
            await _thread(session, user_id, thread_id, lock=True)
            prior = (await session.execute(select(InterviewTurn).where(InterviewTurn.run_id == run_id))).scalar_one_or_none()
            if prior:
                owner = await session.get(InterviewSession, prior.session_id)
                if not owner or owner.thread_id != thread_id or owner.user_id != user_id:
                    raise InterviewDomainError("面试提交不存在。", code="not_found", status_code=404)
                return _accepted(prior)
            row = await _session(session, user_id, thread_id, lock=True)
            turns = await _turns(session, row.id) if row else []
            _validate_command(command, row, turns)
            saved_message = (await session.execute(select(ChatMessage).where(
                ChatMessage.thread_id == thread_id, ChatMessage.run_id == run_id,
                ChatMessage.role == "user", ChatMessage.status.notin_(["archived", "superseded"])
                | ChatMessage.status.is_(None),
            ).order_by(ChatMessage.id.desc()))).scalars().first()
            if saved_message is None or saved_message.sender_type not in (None, "", "human"):
                raise InterviewDomainError("找不到本轮已保存的本人回答，请重新发送。", code="answer_message_missing")
            if command.action == "answer" and not str(saved_message.content or "").strip():
                raise InterviewDomainError("请输入文字回答，或选择跳过本题。", code="answer_empty", status_code=422)
            if command.action == "answer" and len(str(saved_message.content or "")) > 12000:
                raise InterviewDomainError("单次回答请控制在 12000 字以内。", code="answer_too_long", status_code=422)
            if row is None:
                row = InterviewSession(
                    id=uuid.uuid4().hex, thread_id=thread_id, user_id=user_id, version=0,
                    status="preparing", pressure_level=command.config.pressure_level,
                    config_json=command.config.model_dump(mode="json"), materials_json=materials,
                    question_bank_json=[], policy_version="interview-v1",
                )
                session.add(row)
                await session.flush()
            elif command.action == "start" and row.config_json != command.config.model_dump(mode="json"):
                raise InterviewDomainError("这场面试的材料已冻结，请用原设置重试或新建面试。", code="config_frozen")
            question = row.current_question_json
            if command.action == "retry":
                question = next(turn.question_json for turn in reversed(turns) if turn.action == "answer" and turn.question_id == command.question_id)
            question_id = (question or {}).get("id")
            assisted = any(turn.action in {"answer", "hint"} and turn.question_id == question_id for turn in turns)
            action = "start" if command.action == "resume" and row.status == "preparing" else command.action
            frozen_input = command.model_dump(mode="json")
            if action == "start":
                first_start = (await session.execute(select(InterviewTurn).where(
                    InterviewTurn.session_id == row.id, InterviewTurn.action == "start",
                    InterviewTurn.input_json["question_strategy"]["opening_angle"].as_string().isnot(None),
                ).order_by(InterviewTurn.created_at, InterviewTurn.id).limit(1))).scalars().first()
                strategy = (first_start.input_json or {}).get("question_strategy") if first_start else None
                frozen_input["question_strategy"] = _json(strategy) if strategy is not None else await build_question_strategy(session, row)
            turn = InterviewTurn(
                id=uuid.uuid4().hex, session_id=row.id, run_id=run_id,
                answer_message_id=saved_message.id,
                action=action,
                expected_version=row.version, question_id=question_id, question_json=_json(question),
                input_json=frozen_input, answer_text=str(saved_message.content or ""),
                assisted=int(assisted),
            )
            session.add(turn)
            await session.flush()
            return _accepted(turn)


def _accepted(turn) -> dict:
    return {"action": turn.action, "expected_version": turn.expected_version,
            "question_id": turn.question_id, "answer_message_id": turn.answer_message_id,
            "run_id": turn.run_id}


def _validate_refs(refs, materials: dict, answer_text: str, message_id: int):
    for ref in refs:
        value = ref.model_dump() if hasattr(ref, "model_dump") else ref
        if value["kind"] == "answer":
            if value.get("message_id") != message_id or value["quote"] not in answer_text:
                raise InterviewDomainError("题目引用必须来自本轮真实回答。", code="invalid_evidence", status_code=422)
        else:
            source = materials.get(value["kind"]) or {}
            if value["quote"] not in source.get("text", "") or (
                value.get("file_id") and value["file_id"] != source.get("file_id")
            ):
                raise InterviewDomainError("题目或画像引用不在已确认的简历/JD材料中。", code="invalid_source", status_code=422)


def _validate_question(question: InterviewQuestion, row, turn):
    if not any(ref.kind in {"resume", "jd"} for ref in question.source_refs):
        raise InterviewDomainError("每道题需至少关联一处简历或 JD 依据。", code="missing_material_source", status_code=422)
    _validate_refs(question.source_refs, row.materials_json, turn.answer_text, turn.answer_message_id)
    if question.parent_question_id:
        root = (turn.question_json or {}).get("parent_question_id") or turn.question_id
        if turn.action != "answer" or question.parent_question_id != root:
            raise InterviewDomainError("新追问必须关联本轮回答的主问题。", code="invalid_followup", status_code=422)


def _validate_evaluation(evaluation, turn):
    for dimension in evaluation.dimensions.model_dump().values():
        for evidence in dimension["evidence"]:
            if evidence["message_id"] != turn.answer_message_id or evidence["quote"] not in turn.answer_text:
                raise InterviewDomainError("评价引用必须逐字来自本轮保存的回答。", code="invalid_evidence", status_code=422)


def _skip_evaluation():
    return {"score_scale": 100, "dimensions": {key: {"status": "unanswered", "score": None,
            "reason": "本题已跳过，没有作答证据。", "evidence": []} for key in DIMENSIONS},
            "feedback": "本题已跳过，不计入维度均分。", "strengths": [], "improvements": [], "sample_answer": ""}


def _answered_roots(turns) -> set[str]:
    return {(turn.question_json or {}).get("parent_question_id") or turn.question_id
            for turn in turns if turn.action in {"answer", "skip"} and turn.question_id}


def _apply_commit(row, turn, turns, command: InterviewTurnCommit):
    """Validate the entire transition before changing ORM fields."""
    if row.version != turn.expected_version or command.expected_version != turn.expected_version:
        raise InterviewDomainError("面试版本已变化，请读取当前状态后重新提交。", code="stale_version")
    if command.question_id != turn.question_id:
        raise InterviewDomainError("提交题目与本轮冻结题目不一致。", code="question_mismatch")
    action = turn.action
    if bool(command.hint) != (action == "hint"):
        raise InterviewDomainError("仅请求提示时提交 hint，且提示内容不能为空。", code="hint_payload", status_code=422)
    if action != "start" and action != "retry" and (row.current_question_json or {}).get("id") != turn.question_id:
        raise InterviewDomainError("当前问题已变化。", code="question_mismatch")
    bank = {question["id"]: _json(question) for question in row.question_bank_json or []}
    for question in command.question_bank:
        packed = question.model_dump(mode="json")
        if question.id in bank and bank[question.id] != packed:
            raise InterviewDomainError("不能改写已存在的题目，请为新题使用新的 ID。", code="question_immutable")
        if question.id not in bank:
            _validate_question(question, row, turn)
        bank[question.id] = packed
    next_question = command.next_question.model_dump(mode="json") if command.next_question else None
    if command.next_question:
        existing = bank.get(command.next_question.id)
        if existing is not None and existing != next_question:
            raise InterviewDomainError("下一题与已保存题库的内容不一致。", code="question_immutable")
        if existing is None:
            _validate_question(command.next_question, row, turn)
        bank[command.next_question.id] = next_question
    if command.next_question_id:
        next_question = _json(bank.get(command.next_question_id))
        if next_question is None:
            raise InterviewDomainError("指定题目不在本场题库中，请读取候选题后选择。", code="question_not_found", status_code=422)
    if len(bank) > MAX_BANK_QUESTIONS:
        raise InterviewDomainError("本场候选题过多，请结束复盘后开始新的面试。", code="question_bank_full")
    if action in {"start", "answer", "skip"}:
        start_turn = turn if action == "start" else next((item for item in turns if item.action == "start"), None)
        strategy = (start_turn.input_json or {}).get("question_strategy", {}) if start_turn else {}
        existing = row.question_bank_json or []
        existing_ids = {question["id"] for question in existing}
        new_questions = [question for key, question in bank.items() if key not in existing_ids]
        validate_question_novelty(new_questions, strategy, existing)
    if command.profile:
        _validate_refs(command.profile.source_refs, row.materials_json, turn.answer_text, turn.answer_message_id)
        if action != "start":
            raise InterviewDomainError("面试画像仅能在开场提交。", code="profile_frozen")
    evaluation = None
    review = command.review.model_dump(mode="json") if command.review else None
    status = row.status
    if action == "start":
        if not command.profile or not next_question:
            raise InterviewDomainError("开场需提交材料画像、候选题库和唯一首题。", code="start_incomplete", status_code=422)
        if len(bank) < row.config_json["question_count"] or {q["type"] for q in bank.values()} != {"behavioral", "professional", "pressure"}:
            raise InterviewDomainError("初始题库需达到约定题量并覆盖行为、专业与压力三类。", code="bank_coverage", status_code=422)
        source_kinds = {ref.kind for ref in command.profile.source_refs}
        source_kinds.update(ref["kind"] for question in bank.values() for ref in question["source_refs"])
        if not {"resume", "jd"}.issubset(source_kinds):
            raise InterviewDomainError("开场画像和题库应共同引用简历与岗位 JD 两类依据。", code="material_coverage", status_code=422)
        if any(question["parent_question_id"] for question in bank.values()):
            raise InterviewDomainError("开场题库不能预造已经发生的追问。", code="invalid_followup", status_code=422)
        if command.evaluation or review:
            raise InterviewDomainError("开场不能评价尚未发生的回答。", code="premature_evaluation", status_code=422)
        status = "active"
    elif action in {"answer", "skip"}:
        if action == "answer":
            if not command.evaluation:
                raise InterviewDomainError("请提交这份回答的三维评价，再提出下一问。", code="evaluation_required", status_code=422)
            _validate_evaluation(command.evaluation, turn)
            evaluation = command.evaluation.model_dump(mode="json")
        else:
            if command.evaluation:
                raise InterviewDomainError("跳题不能生成评分。", code="skip_not_scored", status_code=422)
            evaluation = _skip_evaluation()
        roots = _answered_roots(turns + [turn])
        if next_question:
            if next_question["id"] == turn.question_id or any(t.question_id == next_question["id"] and t.action in {"answer", "skip"} for t in turns):
                raise InterviewDomainError("不能将已答题作为新题；重答请使用重答操作。", code="duplicate_question")
            parent = next_question.get("parent_question_id")
            root = (turn.question_json or {}).get("parent_question_id") or turn.question_id
            if parent and (action != "answer" or parent != root):
                raise InterviewDomainError("追问必须关联本轮已回答的主问题。", code="invalid_followup", status_code=422)
            if not parent and len(roots) >= row.config_json["question_count"]:
                raise InterviewDomainError("已达到约定主问题数量，请提交复盘；本题追问仍可继续。", code="question_count_reached")
            if review:
                raise InterviewDomainError("仍有待答题时不能同时宣布整场完成。", code="conflicting_transition", status_code=422)
            status = "active"
        elif len(roots) >= row.config_json["question_count"] and review:
            status = "completed"
        else:
            raise InterviewDomainError("本轮需提交唯一下一题；提前结束由用户选择结束复盘。", code="next_question_required", status_code=422)
    else:
        if command.profile or command.question_bank or command.evaluation or next_question:
            raise InterviewDomainError("暂停、继续、重答和结束操作不能附带新题或虚构评分。", code="control_payload", status_code=422)
        if action == "finish":
            if review is None:
                raise InterviewDomainError("结束面试时请依据已答记录提交复盘。", code="review_required", status_code=422)
            next_question, status = None, "completed"
        else:
            if review:
                raise InterviewDomainError("当前操作不结束面试。", code="premature_review", status_code=422)
            next_question = _json(turn.question_json if action == "retry" else row.current_question_json)
            status = "paused" if action == "pause" else "active"
    row.question_bank_json = list(bank.values())
    row.current_question_json = next_question
    row.status = status
    row.review_json = review if status == "completed" else None
    if command.profile:
        row.profile_json = command.profile.model_dump(mode="json")
    pressure_level = turn.input_json.get("pressure_level")
    if pressure_level:
        row.pressure_level = pressure_level
    row.version += 1
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    turn.evaluation_json = evaluation
    turn.committed_version = row.version
    turn.committed_at = datetime.now(timezone.utc).replace(tzinfo=None)


def public_snapshot(row, turns, *, hint: str | None = None) -> dict:
    scores, performance = summarize_performance(turns)
    if row is None:
        return {"id": None, "thread_id": None, "version": 0, "status": "not_started", "config": None,
                "pressure_level": None, "profile": None, "current_question": None,
                "progress": {"answered": 0, "total": 0, "skipped": 0, "followups": 0, "current_number": 0},
                "turns": [], "review": None, "score_scale": 100, "score_summary": scores,
                "performance": performance, "latest_hint": None, "materials": []}
    attempts = defaultdict(int)
    public_turns = []
    for turn in turns:
        if turn.action not in {"answer", "skip"}:
            continue
        if turn.action == "answer":
            attempts[turn.question_id] += 1
        public_turns.append({
            "id": turn.id, "run_id": turn.run_id, "question_id": turn.question_id,
            "question": turn.question_json, "answer_message_id": turn.answer_message_id,
            "answer": turn.answer_text if turn.action == "answer" else "", "action": turn.action,
            "attempt": attempts[turn.question_id], "assisted": bool(turn.assisted),
            "evaluation": turn.evaluation_json, "committed_version": turn.committed_version,
        })
    answered = _answered_roots([turn for turn in turns if turn.action == "answer"])
    skipped = _answered_roots([turn for turn in turns if turn.action == "skip"]) - answered
    covered = list(dict.fromkeys((turn.question_json or {}).get("competency", "") for turn in turns if turn.action == "answer"))
    uncovered = [item for item in (row.profile_json or {}).get("competencies", []) if item not in covered]
    current = row.current_question_json
    root = (current or {}).get("parent_question_id") or (current or {}).get("id")
    total_answered = len(answered | skipped)
    ordered_roots = list(dict.fromkeys(
        (turn.question_json or {}).get("parent_question_id") or turn.question_id
        for turn in turns if turn.action in {"answer", "skip"} and turn.question_id
    ))
    number = ordered_roots.index(root) + 1 if root in ordered_roots else total_answered + 1 if current else total_answered
    review = {**row.review_json, "covered": covered, "uncovered": uncovered, "score_summary": scores} if row.review_json else None
    current_id = (current or {}).get("id")
    latest_hint = hint if hint and current_id else None
    if latest_hint is None and current_id:
        for turn in reversed(turns):
            if turn.action == "hint" and turn.question_id == current_id:
                stored = (turn.result_json or {}).get("hint") if isinstance(turn.result_json, dict) else None
                latest_hint = stored if isinstance(stored, str) and stored.strip() else None
                break
    return _json({
        "id": row.id, "thread_id": row.thread_id, "version": row.version, "status": row.status,
        "config": row.config_json, "pressure_level": row.pressure_level, "profile": row.profile_json,
        "current_question": current,
        "progress": {"answered": len(answered), "total": row.config_json["question_count"], "skipped": len(skipped),
                     "followups": sum(1 for turn in turns if turn.action == "answer" and (turn.question_json or {}).get("parent_question_id")),
                     "current_number": number},
        "turns": public_turns, "review": review, "score_scale": 100, "score_summary": scores,
        "performance": performance, "latest_hint": latest_hint,
        "materials": [{key: item.get(key) for key in ("kind", "file_id", "filename", "status", "note", "truncated")}
                      for item in row.materials_json.values()],
    })


def rendered_turn(turn, snapshot: dict, hint: str | None = None) -> str:
    """Public wording comes only from committed interview facts."""
    parts = []
    if turn.action == "hint":
        parts.append(f"**思路提示**\n\n{hint}\n\n当前题目保持不变。接下来的回答会标记为辅导后作答。")
    elif turn.action == "pause":
        parts.append("本场面试已暂停，当前问题和已答记录已经保存。点击继续练习即可接着回答。")
    elif turn.action == "retry":
        parts.append("已保留第一次回答。请重新回答下面这道题，这次会标记为看过反馈后的练习。")
    elif turn.action == "resume":
        parts.append("已继续这场面试。" + ("压力强度已降低。" if turn.input_json.get("pressure_level") else ""))
    elif turn.action == "start":
        parts.append("已根据你的简历与岗位 JD 准备好本场练习。我们一次只回答一个问题。")
        notes = [item["note"] or "材料部分解析，只依据当前可读取内容练习。" for item in snapshot["materials"] if item["status"] != "ok"]
        parts.extend(notes)
    if snapshot["review"]:
        parts.append("面试已结束，整场报告已保存。")
    elif snapshot["current_question"] and snapshot["status"] != "paused":
        question = snapshot["current_question"]
        label = "追问" if question.get("parent_question_id") else f"第 {snapshot['progress']['current_number']} 题"
        if turn.action == "retry":
            label = f"重答 · {label}"
        parts.append(f"**{label}**\n\n{question['text']}")
    return "\n\n".join(parts)


async def get_interview_session(*, user_id: str, thread_id: str, run_id: str | None = None, internal: bool = False) -> dict:
    async with async_session() as session:
        await _thread(session, user_id, thread_id)
        row = await _session(session, user_id, thread_id)
        turns = await _turns(session, row.id) if row else []
        result = public_snapshot(row, turns)
        result["thread_id"] = thread_id
        if internal and row:
            result["question_bank"] = _json(row.question_bank_json)
            result["materials"] = _json(row.materials_json)
            if run_id:
                turn = (await session.execute(select(InterviewTurn).where(InterviewTurn.run_id == run_id, InterviewTurn.session_id == row.id))).scalar_one_or_none()
                if turn:
                    result["input"] = {**_accepted(turn), "answer_text": turn.answer_text, "assisted": bool(turn.assisted), "pressure_level": turn.input_json.get("pressure_level")}
                    if turn.action == "start":
                        result["question_strategy"] = _json(turn.input_json.get("question_strategy") or {})
        return result


async def commit_interview_turn(*, user_id: str, thread_id: str, run_id: str, submission: dict) -> dict:
    try:
        command = InterviewTurnCommit.model_validate(submission)
    except ValidationError as exc:
        raise InterviewDomainError(str(exc), code="invalid_submission", status_code=422) from exc
    async with async_session() as session:
        async with session.begin():
            await _thread(session, user_id, thread_id, lock=True)
            row = await _session(session, user_id, thread_id, lock=True)
            if row is None:
                raise InterviewDomainError("面试尚未开始。", code="not_started")
            turn = (await session.execute(select(InterviewTurn).where(InterviewTurn.run_id == run_id, InterviewTurn.session_id == row.id).with_for_update())).scalar_one_or_none()
            if turn is None:
                raise InterviewDomainError("本轮输入尚未受理，不能提交面试状态。", code="input_not_accepted")
            if turn.result_json is not None:
                return _json(turn.result_json)
            saved = await session.get(ChatMessage, turn.answer_message_id)
            if saved is None or saved.thread_id != thread_id or saved.run_id != run_id or saved.content != turn.answer_text or saved.status in {"archived", "superseded"}:
                raise InterviewDomainError("本轮回答已被编辑或替换，不能沿用旧评价。", code="answer_changed")
            turns = await _turns(session, row.id)
            _apply_commit(row, turn, turns, command)
            snapshot = public_snapshot(row, turns + [turn], hint=command.hint)
            result = {"session_id": row.id, "thread_id": thread_id, "run_id": run_id,
                      "version": row.version, "action": turn.action, "answer_message_id": turn.answer_message_id,
                      "public_snapshot": snapshot, "rendered_text": rendered_turn(turn, snapshot, command.hint)}
            if command.hint:
                result["hint"] = command.hint
            turn.result_json = result
            await session.flush()
            return _json(result)


async def get_committed_turn(*, user_id: str, thread_id: str, run_id: str) -> dict | None:
    async with async_session() as session:
        await _thread(session, user_id, thread_id)
        row = await _session(session, user_id, thread_id)
        if row is None:
            return None
        turn = (await session.execute(select(InterviewTurn).where(InterviewTurn.run_id == run_id, InterviewTurn.session_id == row.id))).scalar_one_or_none()
        return _json(turn.result_json) if turn and turn.result_json else None


result_for_run = get_committed_turn
