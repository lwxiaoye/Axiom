"""Session workspace REST. Not 「我的文件」; drafts stay in the thread workspace."""
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.auth import UserContext, current_user
from app.core.config import settings
from app.services.agent_harness import workspace_service
from app.services.agent_harness.workspace_service import WorkspaceError

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _wrap(exc: WorkspaceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/{thread_id}")
async def list_workspace(
    thread_id: str,
    q: str = "",
    kind: str = "",
    user: UserContext = Depends(current_user),
):
    try:
        return await workspace_service.list_workspace(
            user.user_id, thread_id, query=q, kind=kind,
        )
    except WorkspaceError as exc:
        raise _wrap(exc)


@router.post("/{thread_id}/files")
async def upload_workspace_file(
    thread_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(current_user),
):
    max_bytes = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.USER_FILES_MAX_SIZE_MB}MB 限制")
    try:
        row = await workspace_service.save_asset(
            user.user_id,
            thread_id,
            file.filename or "untitled",
            data,
            mime=file.content_type or "",
        )
        return row
    except WorkspaceError as exc:
        raise _wrap(exc)


@router.post("/{thread_id}/files/new")
async def create_workspace_file(
    thread_id: str,
    name: str = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    try:
        return await workspace_service.create_empty_asset(user.user_id, thread_id, name)
    except WorkspaceError as exc:
        raise _wrap(exc)


@router.get("/{thread_id}/versions")
async def list_workspace_versions(
    thread_id: str,
    file_id: str,
    user: UserContext = Depends(current_user),
):
    try:
        return {"items": await workspace_service.list_versions(user.user_id, thread_id, file_id)}
    except WorkspaceError as exc:
        raise _wrap(exc)


@router.get("/{thread_id}/download")
async def download_workspace_file(
    thread_id: str,
    file_id: str,
    user: UserContext = Depends(current_user),
):
    try:
        name, data, mime = await workspace_service.read_workspace_bytes(
            user.user_id, thread_id, file_id,
        )
    except WorkspaceError as exc:
        raise _wrap(exc)
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    return Response(content=data, media_type=mime or "application/octet-stream", headers=headers)


@router.delete("/{thread_id}/file")
async def delete_workspace_file(
    thread_id: str,
    file_id: str,
    user: UserContext = Depends(current_user),
):
    try:
        await workspace_service.delete_asset(user.user_id, thread_id, file_id)
    except WorkspaceError as exc:
        raise _wrap(exc)
    return {"ok": True}


@router.delete("/{thread_id}")
async def clear_workspace(
    thread_id: str,
    confirm: bool = False,
    user: UserContext = Depends(current_user),
):
    try:
        await workspace_service.clear_workspace(user.user_id, thread_id, confirm=confirm)
    except WorkspaceError as exc:
        raise _wrap(exc)
    return {"ok": True}
