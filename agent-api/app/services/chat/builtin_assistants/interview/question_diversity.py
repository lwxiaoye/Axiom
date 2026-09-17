"""Bounded, private question history for choosing a fresh interview angle."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence

from sqlalchemy import select

from app.interview_models import InterviewSession, InterviewTurn
from app.models import ChatThread
from .contracts import InterviewDomainError

MAX_SCANNED_SESSIONS = 30
MAX_RELATED_SESSIONS = 5
MAX_RECENT_QUESTIONS = 18
MAX_HISTORY_CHARS = 9000
ANGLES = {
    "decision": "一个关键选择：比较过哪些可行方案，为什么这样选",
    "diagnosis": "一个具体问题：怎样发现、定位并修正",
    "validation": "一个实际结果：怎样检查是否达到了目标",
    "collaboration": "一次协作：怎样分工、同步信息或处理分歧",
    "tradeoff": "一项现实限制：时间、资源或需求冲突下怎样取舍",
    "reflection": "一次调整：回头看哪些做法会改变，理由是什么",
    "adaptation": "一个条件变化：在材料已有任务上改变一个条件，怎样调整做法",
}


def question_key(text: str) -> str:
    # Keep code operators: changing < to > can change the question's meaning.
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r'[\s，。？！、；：,.?;:"“”‘’()（）\[\]【】]', "", text)


async def build_question_strategy(session, row: InterviewSession) -> dict:
    """Read only this user's related interviews, without answers or grades."""
    config = row.config_json
    jd_hash = (row.materials_json.get("jd") or {}).get("sha256")
    candidates = (await session.execute(select(
        InterviewSession.id,
        InterviewSession.config_json["job_title"].as_string().label("job_title"),
        InterviewSession.materials_json["jd"]["sha256"].as_string().label("jd_hash"),
        InterviewSession.current_question_json,
    ).join(ChatThread, ChatThread.id == InterviewSession.thread_id).where(
        InterviewSession.user_id == row.user_id, ChatThread.user_id == row.user_id,
        ChatThread.origin == "interview", ChatThread.app_id.is_(None),
        InterviewSession.id != row.id, InterviewSession.version > 0,
    ).order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
      .limit(MAX_SCANNED_SESSIONS))).all()
    related = [item for item in candidates if (
        question_key(item.job_title or "") == question_key(config["job_title"])
        or (jd_hash and item.jd_hash == jd_hash)
    )][:MAX_RELATED_SESSIONS]

    questions_by_session = {item.id: [] for item in related}
    previous_angles = set()
    if related:
        history = (await session.execute(select(
            InterviewTurn.session_id, InterviewTurn.question_json,
            InterviewTurn.input_json["question_strategy"]["opening_angle"].as_string().label("opening_angle"),
        ).where(
            InterviewTurn.session_id.in_(questions_by_session),
            InterviewTurn.committed_version.isnot(None),
        ).order_by(InterviewTurn.committed_at.desc(), InterviewTurn.id.desc()).limit(240))).all()
        for item in history:
            if item.question_json:
                questions_by_session[item.session_id].append(item.question_json)
            if item.opening_angle in ANGLES:
                previous_angles.add(item.opening_angle)

    recent, seen, chars = [], set(), 0
    for item in related:
        # The current question has already been asked even if it has no answer.
        asked = [item.current_question_json, *questions_by_session[item.id]]
        for question in asked:
            if not question:
                continue
            text = str(question.get("text") or "")
            key = question_key(text)
            if not key or key in seen:
                continue
            seen.add(key)
            record = {"text": text, "competency": str(question.get("competency") or ""),
                      "type": question.get("type"), "is_followup": bool(question.get("parent_question_id"))}
            size = len(text) + len(record["competency"])
            if len(recent) >= MAX_RECENT_QUESTIONS or chars + size > MAX_HISTORY_CHARS:
                continue
            recent.append(record)
            chars += size

    # Freeze the choice once; retries/recovery reuse it instead of rolling again.
    available = [key for key in ANGLES if key not in previous_angles] or list(ANGLES)
    index = int(hashlib.sha256(row.id.encode()).hexdigest(), 16) % len(available)
    opening_angle = available[index]
    return {"recent_questions": recent, "opening_angle": opening_angle,
            "opening_guidance": ANGLES[opening_angle]}


def validate_question_novelty(questions: list[dict], strategy: dict, existing: Sequence[dict] = ()) -> None:
    """Reject verbatim repeats; the model compares meaning and material coverage."""
    recent = {question_key(item["text"]) for item in strategy.get("recent_questions", [])}
    seen = {question_key(item["text"]) for item in existing}
    for question in questions:
        key = question_key(question["text"])
        if key in seen:
            raise InterviewDomainError("本场候选题出现重复，请换一个具体考察点。", code="duplicate_bank_question", status_code=422)
        seen.add(key)
        if not question.get("parent_question_id") and key in recent:
            raise InterviewDomainError(
                "候选主问题与近期实际问过的题目重复。请对照 question_strategy，换材料中的细节和考察角度后重新提交；不要只改题号或标点。",
                code="recent_question_repeated", status_code=422,
            )
