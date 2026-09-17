"""Owner-only interview projection on the existing protected chat API."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import UserContext, current_user
from app.services.chat.builtin_app_access import require_thread_access
from app.services.chat.builtin_assistants.interview.contracts import InterviewDomainError
from app.services.chat.builtin_assistants.interview.service import get_interview_session

router = APIRouter(tags=["interview"])


@router.get("/chat/threads/{thread_id}/interview")
async def interview_snapshot(thread_id: str, user: UserContext = Depends(current_user)):
    await require_thread_access(user, thread_id, expected_scope="interview")
    try:
        return await get_interview_session(user_id=str(user.user_id), thread_id=thread_id)
    except InterviewDomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
