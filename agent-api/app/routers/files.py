"""「我的文件」REST（ADR-047 §6.6.4）。

用户私有文件工作区的五个端点，全部按 UserContext.user_id 归属校验；
业务校验失败由 user_file_service.UserFileError 统一携带 status_code 抛出。
"""
from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.core.auth import UserContext, current_user
from app.core.config import settings
from app.services.files import user_file_service
from app.services.files.user_file_service import UserFileError

router = APIRouter(prefix="/files", tags=["files"])


@router.get("")
async def list_files(
    folder_id: str | None = None,
    show_all: bool = False,
    user: UserContext = Depends(current_user),
):
    """文件清单（可按 folder_id 过滤：空/__root__=顶层未分类+文件夹，__all__=全部，具体 id=夹内）+ 配额。

    默认只列**交付物**：Agent 发布的文档类产物，以及用户在本页主动上传的文件。
    对话框附件、生成脚本、中间数据、download_url 材料照常落库但不在这里露出。
    模型侧与沙箱同步走 service 默认全量。

    show_all=true 是逃生口：藏起来不等于删掉。
    """
    try:
        return await user_file_service.list_files(
            user.user_id, folder_id, deliverables_only=not show_all
        )
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/folders")
async def create_folder(
    name: str = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """新建文件夹。"""
    try:
        return await user_file_service.create_folder(user.user_id, name)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.patch("/folders/{folder_id}")
async def rename_folder(
    folder_id: str,
    name: str = Body(..., embed=True),
    user: UserContext = Depends(current_user),
):
    """重命名文件夹。"""
    try:
        return await user_file_service.rename_folder(user.user_id, folder_id, name)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, user: UserContext = Depends(current_user)):
    """删除文件夹（里面文件回未分类，不删文件）。"""
    try:
        await user_file_service.delete_folder(user.user_id, folder_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True}


@router.post("/{file_id}/move")
async def move_file(
    file_id: str,
    folder_id: str | None = Body(None, embed=True),
    user: UserContext = Depends(current_user),
):
    """移动文件到文件夹（folder_id=null 移出到未分类）。"""
    try:
        return await user_file_service.move_file(user.user_id, file_id, folder_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    folder_id: str | None = Form(None),
    user: UserContext = Depends(current_user),
):
    """上传存入「我的文件」（source=uploaded，默认永久）。folder_id 可选：文件夹视图内上传直接归位。"""
    max_bytes = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    too_large = f"文件超过 {settings.USER_FILES_MAX_SIZE_MB}MB 上限"
    # ① Content-Length 快速预检：声明就超限的直接 413，省去下面的分块循环
    #    （multipart 头略大于文件本体，放宽 1MB 只做粗筛，精确判断交给 ②）。
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + (1 << 20):
        raise HTTPException(status_code=413, detail=too_large)
    # ② 流式分块读，超阈值立即中断——原 `await file.read()` 无条件把整个请求体读进内存，
    #    任一登录用户发个超大 body 即可把 worker 打 OOM。此处把峰值从「无界」收敛到「有界」：
    #    join 瞬间 parts 与结果并存，约 2×max_bytes，而非 max_bytes+1MB。
    #    注：starlette 解析阶段已把 body spool 到磁盘临时文件，磁盘层面的兜底仍需前置
    #    nginx `client_max_body_size`（本改动只堵应用层 RAM OOM 这条最致命的路径）。
    parts: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1 << 20)  # 1MB/次
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail=too_large)
        parts.append(chunk)
    content = b"".join(parts)
    try:
        return await user_file_service.save_file(
            user.user_id, file.filename or "file", content, source="uploaded",
            folder_id=folder_id or None,
        )
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/save-artifact")
async def save_artifact(
    filename: str = Body(..., embed=True),
    content: str = Body(..., embed=True),
    thread_id: str | None = Body(None, embed=True),
    user: UserContext = Depends(current_user),
):
    """对话流式产出的 HTML 产物自动落进「我的文件」（产物与文件打通，2026-07-13）。

    幂等：同会话同名产物内容未变返回既有文件（unchanged=true）；变化则原地覆盖出
    新版本（版本历史可回滚）；无同名新建（source=generated 带 TTL）。前端产物流收尾时调用。
    """
    max_bytes = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    if len(content.encode("utf-8")) > max_bytes:
        raise HTTPException(status_code=413, detail=f"产物超过 {settings.USER_FILES_MAX_SIZE_MB}MB 上限")
    try:
        return await user_file_service.save_generated_artifact(
            user.user_id, filename, content, thread_id=thread_id or None,
        )
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))




@router.post("/slides/compile")
async def compile_slides(
    slides_filename: str = Body(..., embed=True),
    deck_filename: str = Body(..., embed=True),
    thread_id: str | None = Body(None, embed=True),
    user: UserContext = Depends(current_user),
    x_access_token: str = Header("", alias="X-Access-Token"),
):
    """幻灯片手改后直接重编译 PPTX 并覆盖「我的文件」（2026-07-30）。

    前端 `compileSlidesDeck` 调这里。实现在 `slides_compile.compile_slides_to_deck`：
    沙箱挂 PPT 技能包跑 build_deck，耗时几十秒到两分钟属正常。
    注意：本路由必须写在 `/{file_id}/...` 之前，否则 `slides` 会被当成 file_id。
    """
    from app.services.files.slides_compile import SlidesCompileError, compile_slides_to_deck

    token = (x_access_token or "").replace("Bearer ", "").strip()
    try:
        return await compile_slides_to_deck(
            user.user_id,
            token,
            slides_filename=slides_filename,
            deck_filename=deck_filename,
            thread_id=thread_id or None,
        )
    except SlidesCompileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

@router.get("/{file_id}/content")
async def file_content(file_id: str, user: UserContext = Depends(current_user)):
    """预览内容：文本类回原文，docx/pdf 回解析文本，图片回 kind=image（前端走 download 展示）。"""
    try:
        return await user_file_service.get_content(user.user_id, file_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/{file_id}/download")
async def download_file(file_id: str, user: UserContext = Depends(current_user)):
    """下载原件（归属校验；兼容 local/minio 存储后端）。"""
    try:
        row, data = await user_file_service.read_bytes(user.user_id, file_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    from urllib.parse import quote
    return Response(
        content=data,
        media_type=row.mime or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.filename)}"},
    )


@router.get("/{file_id}/preview")
async def preview_file(file_id: str, user: UserContext = Depends(current_user)):
    """在线预览，按格式分两种响应（前端 `fetchUserFilePreview` 按 Content-Type 分流）：

    - 版式文档（doc/docx/ppt/pptx/xls/xlsx）：沙箱 LibreOffice 转 PDF 回流 `application/pdf`。
      首次转换起沙箱（约 10-30s），结果按 file_id+内容哈希落盘缓存，之后秒回。
    - 文本类（txt/md/json/csv/log/xml/yaml/html）：不需要转换，直接回 JSON
      `{kind:"text", content, truncated, ...}`（原文上限 1MB，超出截断并标记）。
    - 其余格式：400，detail 是带扩展名的可读原因，前端原样展示、不得静默。
    """
    try:
        result = await user_file_service.get_preview(user.user_id, file_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    if result.get("kind") == "pdf":
        return Response(content=result["data"], media_type="application/pdf")
    return result


@router.get("/{file_id}/versions")
async def list_file_versions(file_id: str, user: UserContext = Depends(current_user)):
    """版本历史（Phase B §3.3）：新→旧，含草稿版（status=draft）。"""
    try:
        return await user_file_service.list_versions(user.user_id, file_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/{file_id}/versions/{version_id}/download")
async def download_file_version(
    file_id: str, version_id: str, user: UserContext = Depends(current_user)
):
    """下载某个版本快照（含草稿版，供诊断）。"""
    try:
        ver, data = await user_file_service.read_version_bytes(user.user_id, file_id, version_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    from urllib.parse import quote
    name = f"v{ver.version_no}_{ver.filename}"
    return Response(
        content=data,
        media_type=ver.mime or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.post("/{file_id}/versions/{version_id}/restore")
async def restore_file_version(
    file_id: str, version_id: str, user: UserContext = Depends(current_user)
):
    """恢复历史版本为当前版：不删后续历史，以历史字节创建新的 restored 版本（§3.2 规则 4）。"""
    try:
        return await user_file_service.restore_version(user.user_id, file_id, version_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.delete("/{file_id}")
async def delete_file(file_id: str, user: UserContext = Depends(current_user)):
    try:
        await user_file_service.delete_file(user.user_id, file_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True}


@router.post("/{file_id}/keep")
async def keep_file(file_id: str, user: UserContext = Depends(current_user)):
    """「保留」：generated 产物清 TTL 转永久。"""
    try:
        return await user_file_service.keep_file(user.user_id, file_id)
    except UserFileError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
