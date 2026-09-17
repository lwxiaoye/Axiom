"""「我的文件」用户文件工作区（ADR-047 §6.6）。

沙箱管算、本服务管存：字节落盘 USER_FILES_DIR/{user_id}/、元数据落 MySQL agent_user_file。
三条通路的存储底座：execute_in_sandbox 产物入库（source=generated，默认 TTL）、用户上传（uploaded，
默认永久）、模型 list_files/read_file 按需读取。

安全边界（§6.6.2）：
- 所有操作按 user_id 归属校验——查不到或不属于该用户一律当不存在处理（不泄露他人文件是否存在）；
- 配额（字节 + 文件数）在写入前校验，防变网盘；
- 过期清理是惰性的：list/读取时顺手清掉本用户的过期项，不引调度器。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session, engine
from app.models import AgentUserFile, AgentUserFileVersion, AgentUserFolder
from app.services.files.storage import get_file_storage
from app.services.files import deliverable

logger = logging.getLogger(__name__)


class UserFileError(Exception):
    """业务校验失败（配额/大小/不存在），message 直接可给前端。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _root() -> Path:
    """存储根目录。相对路径=相对进程 cwd（容器内 /app，经 ./:/app bind mount 持久到宿主）。"""
    return Path(settings.USER_FILES_DIR)


def _storage():
    return get_file_storage()


def _safe_name(filename: str) -> str:
    """取纯文件名并去掉路径分隔等危险成分，落盘用。"""
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip().replace("\x00", "")
    if name in ("", ".", ".."):
        name = "file"
    return name[:200]


# 可直接读写原文的文本类扩展名。写工具（update/create）只允许这些——
# docx/xlsx/pdf 等二进制格式的"修改"，写纯文本进去只会把文件写坏，属沙箱 execute_in_sandbox 的活。
_TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".xml", ".yaml", ".yml", ".html"}
# 预览返回的原文上限（比模型侧 MAX_TEXT_CHARS 宽，预览是给人看的）
_PREVIEW_MAX_CHARS = 50000


def _is_text_file(filename: str, mime: str = "") -> bool:
    if (mime or "").startswith("text/"):
        return True
    return os.path.splitext(str(filename or "").lower())[1] in _TEXT_EXTS


def _decode_text(data: bytes) -> str:
    """utf-8 优先，容错 gbk（中文环境常见），最后 replace 兜底。"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _row_to_dict(row: AgentUserFile) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "mime": row.mime or "",
        "size": row.size_bytes,
        "source": row.source,
        "threadId": row.thread_id,
        "folderId": row.folder_id,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        # 交付物判据：过程脚本/材料默认不进「我的文件」清单，但仍落库供模型继续用。
        "deliverable": deliverable.is_deliverable(row.filename, row.source),
    }


async def _rows_with_existing_bytes(rows, *, source: str) -> list:
    """过滤元数据仍在但磁盘字节已丢失的幽灵文件，防止它们再次被选择或交给模型。"""
    rows = list(rows or [])
    if not rows:
        return []
    # v2.40：并行 stat 上限，避免上百文件同时打穿 MinIO/urllib3 连接池。
    _sem = asyncio.Semaphore(12)

    async def _exists_limited(path: str) -> bool:
        async with _sem:
            return await _storage().exists(path)

    exists = await asyncio.gather(*[_exists_limited(row.storage_path) for row in rows])
    kept = []
    for row, available in zip(rows, exists):
        if available:
            kept.append(row)
        else:
            logger.debug(
                "%s 忽略磁盘缺失的文件记录 file=%s path=%s",
                source, row.id, row.storage_path,
            )
    return kept


async def existing_file_ids(user_id: str, file_ids: list[str]) -> set[str]:
    """返回仍归属该用户且磁盘字节真实存在的文件 id 集合。"""
    clean = {str(file_id or "").strip() for file_id in file_ids}
    clean.discard("")
    if not clean:
        return set()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.user_id == user_id,
                    AgentUserFile.id.in_(clean),
                    or_(
                        AgentUserFile.expires_at.is_(None),
                        AgentUserFile.expires_at >= datetime.now(),
                    ),
                )
            )
        ).scalars().all()
    rows = await _rows_with_existing_bytes(rows, source="existing_file_ids")
    return {str(row.id) for row in rows}


async def get_files_by_ids(user_id: str, file_ids: list[str]) -> list[dict]:
    """Return current, owned file metadata in caller order.

    Runtime chat history stores only immutable file references.  Hydrating the
    title/size/version from this source keeps an old message honest after a
    file is renamed, replaced, expired, or removed.
    """
    ordered = [str(file_id or "").strip() for file_id in file_ids]
    clean = list(dict.fromkeys(file_id for file_id in ordered if file_id))
    if not clean:
        return []
    async with async_session() as session:
        rows = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.user_id == user_id,
                    AgentUserFile.id.in_(clean),
                    or_(AgentUserFile.expires_at.is_(None), AgentUserFile.expires_at >= datetime.now()),
                )
            )
        ).scalars().all()
        versions = (
            await session.execute(
                select(
                    AgentUserFileVersion.file_id,
                    func.max(AgentUserFileVersion.version_no),
                )
                .where(
                    AgentUserFileVersion.user_id == user_id,
                    AgentUserFileVersion.file_id.in_(clean),
                )
                .group_by(AgentUserFileVersion.file_id)
            )
        ).all()
    available = await _rows_with_existing_bytes(rows, source="get_files_by_ids")
    version_by_id = {str(file_id): int(version_no or 1) for file_id, version_no in versions}
    by_id = {}
    for row in available:
        item = _row_to_dict(row)
        item["versionNo"] = version_by_id.get(str(row.id), 1)
        by_id[str(row.id)] = item
    return [by_id[file_id] for file_id in ordered if file_id in by_id]


async def _purge_expired(session, user_id: str) -> None:
    """惰性清理该用户的过期文件（磁盘 + 记录）。失败只记日志，不阻塞主操作。"""
    rows = (
        await session.execute(
            select(AgentUserFile).where(
                AgentUserFile.user_id == user_id,
                AgentUserFile.expires_at.isnot(None),
                AgentUserFile.expires_at < datetime.now(),
            )
        )
    ).scalars().all()
    if not rows:
        return
    for row in rows:
        try:
            await _storage().delete(row.storage_path)
            await asyncio.to_thread(lambda r=row: _purge_preview_caches(user_id, r.id))
        except Exception as e:  # noqa: BLE001
            logger.warning("清理过期文件存储失败 %s: %s", row.storage_path, e)
    await _delete_version_artifacts(session, user_id, [r.id for r in rows])  # 版本级联（Phase B）
    await session.execute(
        delete(AgentUserFile).where(AgentUserFile.id.in_([r.id for r in rows]))
    )
    await session.commit()


async def list_recent_file_names(user_id: str, *, limit: int = 12) -> list[dict]:
    """续做/断点用的轻量清单：只取最近 N 条元数据，避免整库 stat 拖慢回合（v2.44）。

    返回 [{filename, size}]，不扫全量 exists；真实可读性由后续工具/list_files 再校验。
    """
    lim = max(1, min(int(limit or 12), 30))
    async with async_session() as session:
        await _purge_expired(session, user_id)
        rows = (
            await session.execute(
                select(AgentUserFile)
                .where(AgentUserFile.user_id == user_id)
                .order_by(AgentUserFile.created_at.desc())
                .limit(lim * 3)  # 多取一些，再过滤无名字的空行
            )
        ).scalars().all()
    out: list[dict] = []
    for row in rows:
        name = str(getattr(row, "filename", None) or getattr(row, "name", None) or "").strip()
        if not name:
            continue
        if deliverable.is_staging_checkpoint_source(getattr(row, "source", "")):
            continue
        out.append({
            "filename": name,
            "size": getattr(row, "size_bytes", None) or getattr(row, "size", None) or "",
        })
        if len(out) >= lim:
            break
    return out


async def list_resume_file_names(
    user_id: str,
    thread_id: Optional[str],
    *,
    selected_file_ids: Optional[Iterable[str]] = None,
    revision_target: Optional[dict] = None,
    limit: int = 12,
) -> list[dict]:
    """Return only files visible to a recovery context.

    The recovery path is intentionally different from the user-wide recency helper: it never uses
    a user-wide recency query.  Visibility is the union of the current session workspace and
    explicitly selected ``+``/RevisionTarget IDs, with the normal user/expiry/byte checks.
    """
    if not thread_id:
        return []
    lim = max(1, min(int(limit or 12), 30))
    selected = {
        str(file_id or "").strip()
        for file_id in (selected_file_ids or [])
        if str(file_id or "").strip()
    }
    target = revision_target if isinstance(revision_target, dict) else {}
    revision_id = str(
        target.get("file_id") or target.get("fileId") or target.get("id") or ""
    ).strip()
    if revision_id:
        selected.add(revision_id)

    visibility = AgentUserFile.thread_id == str(thread_id)
    if selected:
        visibility = or_(visibility, AgentUserFile.id.in_(selected))
    async with async_session() as session:
        rows = (
            await session.execute(
                select(AgentUserFile)
                .where(
                    AgentUserFile.user_id == user_id,
                    visibility,
                    or_(
                        AgentUserFile.expires_at.is_(None),
                        AgentUserFile.expires_at >= datetime.now(),
                    ),
                )
                .order_by(AgentUserFile.created_at.desc())
                .limit(lim * 3)
            )
        ).scalars().all()
    rows = await _rows_with_existing_bytes(rows, source="list_resume_file_names")
    out: list[dict] = []
    for row in rows:
        name = str(getattr(row, "filename", None) or getattr(row, "name", None) or "").strip()
        if not name or deliverable.is_staging_checkpoint_source(getattr(row, "source", "")):
            continue
        item = _row_to_dict(row)
        item["size"] = getattr(row, "size_bytes", None) or getattr(row, "size", None) or ""
        out.append(item)
        if len(out) >= lim:
            break
    return out


async def list_files(
    user_id: str, folder_id: Optional[str] = None, *, deliverables_only: bool = False
) -> dict:
    """文件清单（过滤过期）+ 文件夹列表 + 配额用量。

    folder_id 语义：None/""/"__root__" → 顶层（未分类文件 + 文件夹列表）；
    "__all__" → 全部文件（扁平，兼容旧行为）；具体 id → 该文件夹内文件（校验归属，查不到 404）。
    quota/count 恒按该用户全部文件计（与当前视图无关）。generated 产物带来源对话标题（threadTitle）。

    deliverables_only（2026-07-27）：只有面向用户的「我的文件」页走 True——藏掉生成脚本
    之类的过程文件。默认 False 是必需的：workspace_sync / paths / 模型 list_files 工具都靠
    全量文件区；过滤会让沙箱里凭空少文件。配额仍按全量计。
    """
    async with async_session() as session:
        await _purge_expired(session, user_id)
        all_rows = (
            await session.execute(
                select(AgentUserFile)
                .where(AgentUserFile.user_id == user_id)
                .order_by(AgentUserFile.created_at.desc())
            )
        ).scalars().all()
        # 元数据与 bind-mounted 文件目录跨介质，历史部署/清理异常可能只剩 DB 行。
        # 清单必须以真实可读字节为准，否则用户选中的卡片必然在工具执行时 404，模型
        # 还可能再按同名猜中另一份文件。这里只隐藏并报警，不自动删除可审计元数据。
        all_rows = await _rows_with_existing_bytes(all_rows, source="list_files")
        folder_rows = (
            await session.execute(
                select(AgentUserFolder)
                .where(AgentUserFolder.user_id == user_id)
                .order_by(AgentUserFolder.created_at.desc())
            )
        ).scalars().all()
        # 视图过滤
        if folder_id in (None, "", "__root__"):
            view_rows = [r for r in all_rows if not r.folder_id]
        elif folder_id == "__all__":
            view_rows = list(all_rows)
        else:
            if not any(f.id == folder_id for f in folder_rows):
                raise UserFileError("文件夹不存在", status_code=404)
            view_rows = [r for r in all_rows if r.folder_id == folder_id]
        view_rows = [
            r for r in view_rows
            if not deliverable.is_staging_checkpoint_source(r.source)
        ]
        # 编辑/审核预览产物可在当轮卡片内打开，但不能污染正式「我的文件」清单。
        view_rows = [r for r in view_rows if r.source != "preview"]
        if deliverables_only:
            view_rows = [
                r for r in view_rows
                if deliverable.is_deliverable(r.filename, r.source)
                # 伴生编辑源要带着：前端靠它在同一份清单里配对出 pptx「编辑」能力
                or deliverable.is_editor_companion(r.filename)
            ]
        # 来源对话标题：一次批量查 ChatThread（产物回溯「这个文件是哪次对话生成的」）
        thread_ids = {r.thread_id for r in view_rows if r.thread_id}
        titles: dict = {}
        if thread_ids:
            from app.models import ChatThread
            trows = (
                await session.execute(
                    select(ChatThread.id, ChatThread.title).where(ChatThread.id.in_(thread_ids))
                )
            ).all()
            titles = {tid: (title or "").strip() for tid, title in trows}
            # 标题为空的对话（老数据 / 首轮标题未落库）：用首条用户消息兜底，
            # 让产物仍能归到「对应的那次对话」而非塌进「其他产物」。只补空标题的那批，量小。
            blank_tids = {tid for tid in thread_ids if not titles.get(tid)}
            if blank_tids:
                from app.models import ChatMessage
                mrows = (
                    await session.execute(
                        select(ChatMessage.thread_id, ChatMessage.content)
                        .where(
                            ChatMessage.thread_id.in_(blank_tids),
                            ChatMessage.role == "user",
                        )
                        .order_by(ChatMessage.created_at.asc())
                    )
                ).all()
                for tid, content in mrows:
                    if not titles.get(tid):  # 每个对话取最早一条用户消息
                        titles[tid] = (content or "").strip()[:50]
    used = sum(r.size_bytes or 0 for r in all_rows)
    folder_counts: dict = {}
    for r in all_rows:
        if r.folder_id:
            # 文件夹计数必须与点进去看到的条数同口径；伴生编辑源不计数。
            if deliverables_only and not deliverable.is_deliverable(r.filename, r.source):
                continue
            folder_counts[r.folder_id] = folder_counts.get(r.folder_id, 0) + 1
    files = []
    for r in view_rows:
        d = _row_to_dict(r)
        d["threadTitle"] = titles.get(r.thread_id) if r.thread_id else None
        files.append(d)
    folders = [
        {
            "id": f.id,
            "name": f.name,
            "fileCount": folder_counts.get(f.id, 0),
            "createdAt": f.created_at.isoformat() if f.created_at else None,
        }
        for f in folder_rows
    ]
    return {
        "files": files,
        "folders": folders,
        "quota": {
            "usedBytes": used,
            "quotaBytes": settings.USER_FILES_QUOTA_MB * 1024 * 1024,
            "count": len(all_rows),
            "maxCount": settings.USER_FILES_MAX_COUNT,
        },
    }


async def create_folder(user_id: str, name: str) -> dict:
    """新建文件夹（校验名长度/重名/数量上限，D4/D8）。"""
    clean = (name or "").strip()
    if not clean:
        raise UserFileError("文件夹名不能为空")
    if len(clean) > 50:
        raise UserFileError("文件夹名过长（最多 50 字）")
    async with async_session() as session:
        existing = (
            await session.execute(
                select(func.count(AgentUserFolder.id)).where(AgentUserFolder.user_id == user_id)
            )
        ).scalar_one()
        if existing >= settings.USER_FILES_MAX_FOLDERS:
            raise UserFileError(f"文件夹数已达上限（{settings.USER_FILES_MAX_FOLDERS} 个）")
        dup = (
            await session.execute(
                select(AgentUserFolder.id).where(
                    AgentUserFolder.user_id == user_id, AgentUserFolder.name == clean
                )
            )
        ).first()
        if dup:
            raise UserFileError("已有同名文件夹")
        folder = AgentUserFolder(id=uuid.uuid4().hex, user_id=user_id, name=clean)
        session.add(folder)
        try:
            await session.commit()
        except IntegrityError:
            # 查重到插入之间仍有 TOCTOU 窗口（DB 层唯一约束需要 models.py 迁移配合，超出本文件改动范围）；
            # 这里至少把撞车转成明确的业务错误而不是让 500 冒出去
            await session.rollback()
            raise UserFileError("已有同名文件夹")
        await session.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "fileCount": 0,
        "createdAt": folder.created_at.isoformat() if folder.created_at else None,
    }


async def rename_folder(user_id: str, folder_id: str, name: str) -> dict:
    """重命名文件夹（校验归属 + 重名）。"""
    clean = (name or "").strip()
    if not clean:
        raise UserFileError("文件夹名不能为空")
    if len(clean) > 50:
        raise UserFileError("文件夹名过长（最多 50 字）")
    async with async_session() as session:
        folder = (
            await session.execute(
                select(AgentUserFolder).where(
                    AgentUserFolder.id == folder_id, AgentUserFolder.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if folder is None:
            raise UserFileError("文件夹不存在", status_code=404)
        dup = (
            await session.execute(
                select(AgentUserFolder.id).where(
                    AgentUserFolder.user_id == user_id,
                    AgentUserFolder.name == clean,
                    AgentUserFolder.id != folder_id,
                )
            )
        ).first()
        if dup:
            raise UserFileError("已有同名文件夹")
        folder.name = clean
        try:
            await session.commit()
        except IntegrityError:
            # 同 create_folder：查重到写入之间的 TOCTOU 窗口，DB 唯一约束缺失时仅能兜底撞车报错
            await session.rollback()
            raise UserFileError("已有同名文件夹")
    return {"id": folder_id, "name": clean}


async def delete_folder(user_id: str, folder_id: str) -> None:
    """删除文件夹：里面的文件 folder_id 置空回「未分类」，不删文件（D7）。"""
    async with async_session() as session:
        folder = (
            await session.execute(
                select(AgentUserFolder).where(
                    AgentUserFolder.id == folder_id, AgentUserFolder.user_id == user_id
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if folder is None:
            raise UserFileError("文件夹不存在", status_code=404)
        await session.execute(
            update(AgentUserFile)
            .where(AgentUserFile.user_id == user_id, AgentUserFile.folder_id == folder_id)
            .values(folder_id=None)
        )
        await session.delete(folder)
        await session.commit()


async def move_file(user_id: str, file_id: str, folder_id: Optional[str]) -> dict:
    """移动文件到文件夹（folder_id=None 移出到未分类）。移入任何文件夹自动清 TTL 转永久（D6）。"""
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id, AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None or (row.expires_at and row.expires_at < datetime.now()):
            # 已过期的行不许被移动接口"复活"（folder_id 分支会清空 expires_at 转永久，
            # 必须先挡住过期行，否则等于绕开 TTL 生命周期）
            raise UserFileError("文件不存在或已过期", status_code=404)
        if folder_id:
            folder = (
                await session.execute(
                    select(AgentUserFolder.id).where(
                        AgentUserFolder.id == folder_id, AgentUserFolder.user_id == user_id
                    ).with_for_update()
                )
            ).first()
            if not folder:
                raise UserFileError("目标文件夹不存在", status_code=404)
            row.folder_id = folder_id
            row.expires_at = None  # 归入文件夹=用户想留着，转永久（D6）
        else:
            row.folder_id = None
        await session.commit()
        await session.refresh(row)
        return _row_to_dict(row)


async def overwrite_file(
    user_id: str,
    file_id: str,
    data: bytes,
    *,
    filename: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    change_summary: Optional[str] = None,
    created_by: str = "agent",
    version_source: str = "generated",
    expected_sha256: Optional[str] = None,
    expected_folder_id: Optional[str] = None,
) -> dict:
    """原地覆盖已有文件的字节，保持 file_id 不变（供沙箱「原地编辑」选中文件用，ADR-047 §6.6）。

    配额按大小增量核（只算变大的部分）；文件不属于该用户即 404；覆盖后清预览缓存
    （版式文档转 PDF 的缓存会过时）。每次覆盖产生新版本快照，旧内容先补录基线（Phase B）。
    """
    max_bytes = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise UserFileError(f"文件超过 {settings.USER_FILES_MAX_SIZE_MB}MB 限制", status_code=413)
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile)
                .where(AgentUserFile.id == file_id, AgentUserFile.user_id == user_id)
                .with_for_update()  # 行锁串行化同文件并发写：版本号 max+1 不再竞态
            )
        ).scalar_one_or_none()
        if row is None:
            raise UserFileError("文件不存在", status_code=404)
        if expected_folder_id is not None and (row.folder_id or "") != expected_folder_id:
            raise UserFileError("目标文件已被移动，为避免修改其它文件夹的文件，请重新读取后再编辑", status_code=409)
        if row.expires_at is not None and row.expires_at < datetime.now():
            # 已过期只是还没被惰性清理：视同不存在（对齐 update_file_content），不许经由
            # 覆写悄悄续期复活——当前调用方都做了前置过期校验，此守卫防将来的新调用方漏检
            raise UserFileError("文件不存在", status_code=404)
        if row.expires_at is not None:
            # 覆写=内容被主动更新，续期而非照旧沿用 expires_at——否则刚写入
            # 的新字节/新版本会被下一次惰性清理（_purge_expired）当过期数据一并删除
            row.expires_at = (
                datetime.now() + timedelta(days=settings.USER_FILES_GENERATED_TTL_DAYS)
                if settings.USER_FILES_GENERATED_TTL_DAYS > 0 else None
            )
        delta = len(data) - int(row.size_bytes or 0)
        if delta > 0:
            used = (
                await session.execute(
                    select(func.coalesce(func.sum(AgentUserFile.size_bytes), 0)).where(
                        AgentUserFile.user_id == user_id
                    )
                )
            ).scalar_one()
            if int(used) + delta > settings.USER_FILES_QUOTA_MB * 1024 * 1024:
                raise UserFileError(f"存储空间不足（配额 {settings.USER_FILES_QUOTA_MB}MB），请先清理")
        # 覆写前先取旧字节：存量文件补录基线 v1，保证旧版本永不覆盖（Phase B §3.2 规则 2）
        old_data = await _read_current_bytes(row)
        if expected_sha256 and hashlib.sha256(old_data or b"").hexdigest() != expected_sha256:
            raise UserFileError(
                "目标文件在任务执行期间已被更新，为避免覆盖较新的版本，本次写入已停止；"
                "请重新读取文件后再继续修改",
                status_code=409,
            )

        await _storage().write_bytes(row.storage_path, data, content_type=row.mime or "")
        row.size_bytes = len(data)
        if filename:
            row.filename = _safe_name(filename)
        await _ensure_baseline_version(session, row, old_data)
        ver = await _add_version(
            session, row, data,
            source=version_source,
            created_by=created_by,
            thread_id=thread_id,
            run_id=run_id,
            change_summary=change_summary,
        )
        # 内容已变：清掉该文件的全部旧预览缓存（新缓存按新内容哈希在下次预览时生成）
        await asyncio.to_thread(lambda: _purge_preview_caches(user_id, file_id))
        try:
            await session.commit()
        except Exception:
            # 磁盘已写新内容而 DB 提交失败：用已读到的旧字节补偿回滚磁盘 + 清本次快照，
            # 保证「磁盘当前内容 ↔ DB 元数据」不背离（第二轮评审 P1 原子性）
            if old_data is not None:
                await _storage().write_bytes(row.storage_path, old_data, content_type=row.mime or "")
            await _storage().delete(ver.storage_path)
            raise
        await session.refresh(row)
    await _prune_old_versions(user_id, file_id)
    out = _row_to_dict(row)
    out["versionNo"] = int(ver.version_no)
    # 覆盖已使旧预览缓存失效（上方 unlink）；后台重新预热，改后点开同样秒显
    schedule_preview_prewarm(user_id, file_id, row.filename)
    return out


async def find_generated_file(user_id: str, filename: str, thread_id: Optional[str]) -> Optional[dict]:
    """找本会话内已生成的同名产物（供 execute_in_sandbox 修复重跑时原地更新，不堆重名副本）。

    只匹配 source=generated/research 且同 thread——跨会话/用户上传的同名文件不碰。取最新一个。
    """
    if not thread_id:
        return None
    async with async_session() as session:
        from .work_folders import folder_for_thread
        folder = await folder_for_thread(user_id, thread_id, session=session)
        rows = (
            await session.execute(
                select(AgentUserFile)
                .where(
                    AgentUserFile.user_id == user_id,
                    AgentUserFile.thread_id == thread_id,
                    AgentUserFile.filename == _safe_name(filename),
                    AgentUserFile.source.in_(("generated", "research")),
                    AgentUserFile.folder_id == folder["id"] if folder else True,
                    # 过滤逻辑上已过期但尚未被惰性清理的行——否则会被当同名产物"续命"，
                    # 而不是走 save_file 正常新建
                    or_(AgentUserFile.expires_at.is_(None), AgentUserFile.expires_at >= datetime.now()),
                )
                .order_by(AgentUserFile.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
    rows = await _rows_with_existing_bytes(rows, source="find_generated_file")
    return _row_to_dict(rows[0]) if rows else None


async def list_generated_files(
    user_id: str, thread_id: Optional[str], limit: int = 12
) -> list[dict]:
    """列出本会话内助手已生成并保存的产物（最新在前），供上下文告知模型「可原地修改」。

    只取 source=generated/research 且同 thread 的未过期文件——用户上传的原件不在此列（改它们要
    另存新文件，语义不同）。用于让「换个色调 / 改一下 X」这类后续修改请求命中已有产物、
    在其基础上原地覆盖，而不是从头重新生成一份新文件。深度研究报告 source=research，
    必须算进本会话产物，否则追问「改一下这份报告」会找不到文件。
    """
    if not thread_id:
        return []
    async with async_session() as session:
        from .work_folders import folder_for_thread
        folder = await folder_for_thread(user_id, thread_id, session=session)
        rows = (
            await session.execute(
                select(AgentUserFile)
                .where(
                    AgentUserFile.user_id == user_id,
                    AgentUserFile.thread_id == thread_id,
                    AgentUserFile.source.in_(("generated", "research")),
                    AgentUserFile.folder_id == folder["id"] if folder else True,
                    or_(AgentUserFile.expires_at.is_(None),
                        AgentUserFile.expires_at >= datetime.now()),
                )
                .order_by(AgentUserFile.created_at.desc())
                .limit(max(1, int(limit)))
            )
        ).scalars().all()
    rows = await _rows_with_existing_bytes(rows, source="list_generated_files")
    return [_row_to_dict(r) for r in rows]


# ===== 版本快照（实施说明 Phase B §3.2）=====
# AgentUserFile 是当前指针（既有读路径全部不变）；每个版本一份完整快照，
# 落 {user_id}/versions/{file_id}/ 下，不计用户配额，随文件删除/过期级联清理。


def _version_dict(v: AgentUserFileVersion) -> dict:
    return {
        "id": v.id,
        "fileId": v.file_id,
        "versionNo": v.version_no,
        "filename": v.filename,
        "mime": v.mime or "",
        "size": int(v.size_bytes or 0),
        "source": v.source,
        "status": v.status,
        "threadId": v.source_thread_id,
        "runId": v.source_run_id,
        "changeSummary": v.change_summary,
        "createdBy": v.created_by,
        "createdAt": v.created_at.isoformat() if v.created_at else None,
    }


async def _add_version(
    session,
    file_row: AgentUserFile,
    data: bytes,
    *,
    source: str,
    created_by: str,
    status: str = "active",
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> AgentUserFileVersion:
    """写一个版本快照（磁盘 + 记录，记录随调用方 session 一起提交）。"""
    next_no = (
        await session.execute(
            select(func.coalesce(func.max(AgentUserFileVersion.version_no), 0)).where(
                AgentUserFileVersion.file_id == file_row.id
            )
        )
    ).scalar_one() + 1
    vid = uuid.uuid4().hex
    safe = _safe_name(file_row.filename)
    rel_path = f"{file_row.user_id}/versions/{file_row.id}/{vid}_{safe}"
    await _storage().write_bytes(rel_path, data, content_type=file_row.mime or "")
    ver = AgentUserFileVersion(
        id=vid,
        file_id=file_row.id,
        user_id=file_row.user_id,
        version_no=int(next_no),
        storage_path=rel_path,
        filename=safe,
        mime=file_row.mime or "",
        size_bytes=len(data),
        source=source,
        status=status,
        source_thread_id=thread_id,
        source_run_id=run_id,
        change_summary=(change_summary or None) and str(change_summary)[:255],
        created_by=created_by,
    )
    session.add(ver)
    return ver


async def _ensure_baseline_version(session, file_row: AgentUserFile, old_data: Optional[bytes]) -> None:
    """版本表为空的存量文件：先把当前（旧）内容补录为 v1，保证"旧版本永不覆盖"（§3.2 规则 2）。"""
    if old_data is None:
        return
    existing = (
        await session.execute(
            select(func.count(AgentUserFileVersion.id)).where(
                AgentUserFileVersion.file_id == file_row.id
            )
        )
    ).scalar_one()
    if int(existing) > 0:
        return
    await _add_version(
        session, file_row, old_data,
        source=file_row.source or "uploaded",
        created_by="user",
        thread_id=file_row.thread_id,
        change_summary="历史版本（自动补录）",
    )


async def _read_current_bytes(row: AgentUserFile) -> Optional[bytes]:
    """读当前指针的存储字节（供覆写前补录基线）；字节缺失返回 None 不阻塞主操作。"""
    try:
        return await _storage().read_bytes(row.storage_path)
    except Exception:  # noqa: BLE001
        return None


async def _prune_old_versions(user_id: str, file_id: str) -> None:
    """超出每文件版本数上限时修剪最旧的版本（快照文件 + 记录，独立事务尽力而为）。

    防反复编辑绕过用户配额无限吃磁盘（版本快照不计配额）；上限宽松（默认 100），
    正常使用打不到；失败只记日志不影响主写入。
    """
    cap = int(getattr(settings, "USER_FILES_MAX_VERSIONS_PER_FILE", 0) or 0)
    if cap <= 0:
        return
    try:
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(AgentUserFileVersion)
                    .where(
                        AgentUserFileVersion.file_id == file_id,
                        AgentUserFileVersion.user_id == user_id,
                    )
                    .order_by(AgentUserFileVersion.version_no.desc())
                )
            ).scalars().all()
            excess = rows[cap:]
            if not excess:
                return
            for v in excess:
                await _storage().delete(v.storage_path)
            await session.execute(
                delete(AgentUserFileVersion).where(
                    AgentUserFileVersion.id.in_([v.id for v in excess])
                )
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("版本修剪失败 %s: %s", file_id, e)


async def _delete_version_artifacts(session, user_id: str, file_ids: list) -> None:
    """级联清理一批文件的全部版本（快照目录 + 记录）。失败只记日志。"""
    if not file_ids:
        return
    for fid in file_ids:
        try:
            await _storage().delete_prefix(f"{user_id}/versions/{fid}")
        except Exception as e:  # noqa: BLE001, PERF203
            logger.warning("清理版本目录失败 %s: %s", fid, e)
    await session.execute(
        delete(AgentUserFileVersion).where(AgentUserFileVersion.file_id.in_(list(file_ids)))
    )


async def save_file(
    user_id: str,
    filename: str,
    data: bytes,
    *,
    source: str = "uploaded",
    thread_id: Optional[str] = None,
    mime: str = "",
    folder_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """写入一个文件（配额校验 → 落盘 → 落库）。generated 默认带 TTL，uploaded 永久。

    folder_id 可选：文件夹视图内上传直接归位（校验夹归属，查不到即 404）。
    """
    max_bytes = settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise UserFileError(f"文件超过 {settings.USER_FILES_MAX_SIZE_MB}MB 限制", status_code=413)

    # 并发配额守卫（P1-15 残余）：聚合 SELECT ... FOR UPDATE 在用户 0 文件时只加互相兼容的
    # 纯 gap 锁，串不住「都读到 0/未超限」的并发校验；改用 MySQL 命名锁按用户维度互斥整段
    # 「校验→插入→提交」。锁走独立连接：GET_LOCK/RELEASE_LOCK 是连接级语义必须同连接成对，
    # 连接损坏时服务端随连接失效自动释放，不会残留死锁
    lock_key = f"uf_quota:{user_id}"
    async with engine.connect() as lock_conn:
        got_lock = (
            await lock_conn.execute(text("SELECT GET_LOCK(:k, 5)"), {"k": lock_key})
        ).scalar()
        if not got_lock:
            logger.warning("save_file 配额锁等待超时，本次降级为无锁校验 user=%s", user_id)
        try:
            async with async_session() as session:
                await _purge_expired(session, user_id)
                if not folder_id and thread_id and source == "generated" and (
                    deliverable.is_deliverable(filename, source)
                    or deliverable.is_editor_companion(filename)
                ):
                    from .work_folders import folder_for_thread
                    work_folder = await folder_for_thread(user_id, thread_id, session=session)
                    folder_id = work_folder["id"] if work_folder else None
                if folder_id:
                    owned = (
                        await session.execute(
                            select(AgentUserFolder.id).where(
                                AgentUserFolder.id == folder_id, AgentUserFolder.user_id == user_id
                            ).with_for_update()
                        )
                    ).scalar_one_or_none()
                    if owned is None:
                        raise UserFileError("文件夹不存在", status_code=404)
                count, used = (
                    await session.execute(
                        select(
                            func.count(AgentUserFile.id),
                            func.coalesce(func.sum(AgentUserFile.size_bytes), 0),
                        )
                        .where(AgentUserFile.user_id == user_id)
                        .with_for_update()  # user_id 有索引：REPEATABLE READ 下 InnoDB 对该值的间隙也加锁，
                        # 把并发 save_file 的配额校验串行化在本行锁后面，避免都读到同一份"未超限"旧值
                    )
                ).one()
                if count >= settings.USER_FILES_MAX_COUNT:
                    raise UserFileError(f"文件数已达上限（{settings.USER_FILES_MAX_COUNT} 个），请先清理")
                if int(used) + len(data) > settings.USER_FILES_QUOTA_MB * 1024 * 1024:
                    raise UserFileError(f"存储空间不足（配额 {settings.USER_FILES_QUOTA_MB}MB），请先清理")

                file_id = uuid.uuid4().hex
                safe = _safe_name(filename)
                rel_path = f"{user_id}/{file_id}_{safe}"
                await _storage().write_bytes(rel_path, data, content_type=mime or mimetypes.guess_type(safe)[0] or "")

                expires_at = None
                if (
                    source in ("generated", "preview") or deliverable.is_staging_checkpoint_source(source)
                ) and not folder_id and settings.USER_FILES_GENERATED_TTL_DAYS > 0:
                    expires_at = datetime.now() + timedelta(days=settings.USER_FILES_GENERATED_TTL_DAYS)

                row = AgentUserFile(
                    id=file_id,
                    user_id=user_id,
                    filename=safe,
                    mime=mime or (mimetypes.guess_type(safe)[0] or ""),
                    size_bytes=len(data),
                    source=source,
                    thread_id=thread_id,
                    storage_path=rel_path,
                    expires_at=expires_at,
                    folder_id=folder_id or None,
                )
                session.add(row)
                # 版本 v1（Phase B §3.2 规则 1/3）：上传或新建即建首版快照
                ver = await _add_version(
                    session, row, data,
                    source=source,
                    created_by="user" if source == "uploaded" else "agent",
                    thread_id=thread_id,
                    run_id=run_id,
                )
                try:
                    await session.commit()
                except Exception:
                    await _storage().delete(rel_path)
                    await _storage().delete(ver.storage_path)
                    raise
                await session.refresh(row)
                out = _row_to_dict(row)
                out["versionNo"] = int(ver.version_no)
                # 生成的版式文档后台预热预览缓存（用户最可能立刻点开的就是刚生成的产物）
                if source in ("generated", "research"):
                    schedule_preview_prewarm(user_id, file_id, safe)
                return out
        finally:
            if got_lock:
                try:
                    await lock_conn.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": lock_key})
                except Exception:  # noqa: BLE001
                    pass


async def get_file(user_id: str, file_id: str) -> tuple[AgentUserFile, str]:
    """按归属取文件记录 + 存储 key。不存在/不属于该用户/已过期/字节缺失 → UserFileError(404)。"""
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id, AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None or (row.expires_at and row.expires_at < datetime.now()):
            raise UserFileError("文件不存在或已过期", status_code=404)
    if not await _storage().exists(row.storage_path):
        logger.warning("文件记录在但存储字节缺失: %s", row.storage_path)
        raise UserFileError("文件不存在或已过期", status_code=404)
    return row, row.storage_path


async def read_bytes(user_id: str, file_id: str) -> tuple[AgentUserFile, bytes]:
    """读回文件字节（模型 read_file 工具 / 后续 execute_in_sandbox input_files 用）。"""
    row, key = await get_file(user_id, file_id)
    data = await _storage().read_bytes(key)
    return row, data


async def read_many_bytes(user_id: str, file_ids: list) -> dict:
    """批量读回字节：{file_id: bytes}。

    契约（workspace_sync / 沙箱镜像）：读不到的 id **不进结果**（幽灵元数据、过期、
    存储缺失一律静默跳过），调用方据缺省键自行记入 skipped，不得因单文件失败整批炸掉。
    并发读存储，避免 200 个文件串行往返。
    """
    ids = [str(fid).strip() for fid in (file_ids or []) if str(fid or "").strip()]
    if not ids:
        return {}

    async def _one(fid: str):
        try:
            _row, data = await read_bytes(user_id, fid)
            return fid, data
        except UserFileError:
            return fid, None
        except Exception as exc:  # noqa: BLE001
            logger.info("read_many_bytes 跳过 file_id=%s: %s", fid, exc)
            return fid, None

    pairs = await asyncio.gather(*[_one(fid) for fid in ids])
    return {fid: data for fid, data in pairs if data is not None}


async def get_content(
    user_id: str,
    file_id: str,
    *,
    newapi_key: str = "",
    ocr_embedded_images: bool = False,
    ocr_visual: bool = False,
    audit_context: Optional[dict] = None,
) -> dict:
    """读取文件内容。UI 预览默认不调视觉模型；对话/子智能体显式开启后读图。"""
    from app.services.files import document_parse_service

    row, data = await read_bytes(user_id, file_id)
    base = {"id": row.id, "filename": row.filename, "mime": row.mime or "", "size": row.size_bytes}
    if (row.mime or "").startswith("image/") and not ocr_embedded_images:
        return {**base, "kind": "image", "text": "", "truncated": False}
    if _is_text_file(row.filename, row.mime):
        text = _decode_text(data)
        truncated = len(text) > _PREVIEW_MAX_CHARS
        return {**base, "kind": "text", "text": text[:_PREVIEW_MAX_CHARS], "truncated": truncated}
    # 解析管线支持 pdf/docx/pptx/xlsx；其余二进制（可执行文件等）过去会掉进
    # 「当文本读」兜底 → 预览弹窗满屏乱码。先探测：头部含 NUL 即二进制，回 kind=binary
    # 由前端引导下载（office 常见格式前端已直接走 blob 渲染器，不经此接口）。
    # 注意 xlsx/xlsm 本质是 zip（头部必含 NUL），必须先放行到 parse_upload，否则缩略图/
    # 内容预览拿不到解析后的表格文本（2026-07-23）。
    ext = os.path.splitext(str(row.filename or "").lower())[1]
    visual_image = ocr_embedded_images and (
        (row.mime or "").startswith("image/") or ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    )
    if ext not in (".pdf", ".docx", ".pptx", ".xlsx", ".xlsm") and not visual_image and b"\x00" in data[:4096]:
        return {**base, "kind": "binary", "text": "", "truncated": False}
    parsed = await document_parse_service.parse_upload(
        row.filename,
        data,
        newapi_key=newapi_key,
        ocr_embedded_images=ocr_embedded_images,
        ocr_visual=ocr_visual,
        audit_context=audit_context,
    )
    return {
        **base,
        "kind": str(parsed.get("kind") or ""),
        "text": str(parsed.get("text") or ""),
        "truncated": bool(parsed.get("truncated")),
        "status": str(parsed.get("status") or "ok"),
        "note": parsed.get("note"),
    }


async def update_file_content(
    user_id: str,
    file_id: str,
    content: str,
    *,
    thread_id: Optional[str] = None,
    change_summary: Optional[str] = None,
    run_id: Optional[str] = None,
    expected_sha256: Optional[str] = None,
    expected_folder_id: Optional[str] = None,
) -> dict:
    """全量覆写一个文本文件的内容（模型 update_file/edit_file 工具）。

    仅限文本类格式（_TEXT_EXTS）；配额按增量核（旧尺寸让位新内容）。
    每次写入产生新版本快照，旧内容先补录基线（Phase B）。
    """
    data = str(content or "").encode("utf-8")
    if len(data) > settings.USER_FILES_MAX_SIZE_MB * 1024 * 1024:
        raise UserFileError(f"内容超过 {settings.USER_FILES_MAX_SIZE_MB}MB 限制", status_code=413)

    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile)
                .where(AgentUserFile.id == file_id, AgentUserFile.user_id == user_id)
                .with_for_update()  # 行锁串行化同文件并发写（同 overwrite_file）
            )
        ).scalar_one_or_none()
        if row is None or (row.expires_at and row.expires_at < datetime.now()):
            raise UserFileError("文件不存在或已过期", status_code=404)
        if expected_folder_id is not None and (row.folder_id or "") != expected_folder_id:
            raise UserFileError("目标文件已被移动，为避免修改其它文件夹的文件，请重新读取后再编辑", status_code=409)
        if not _is_text_file(row.filename, row.mime):
            raise UserFileError(
                f"《{row.filename}》不是纯文本格式，直接覆写会损坏文件；"
                "请用 bash 在 /workspace/files/ 中配合 python-docx、python-pptx、"
                "openpyxl 等对应库修改并写回原路径"
            )
        used = (
            await session.execute(
                select(func.coalesce(func.sum(AgentUserFile.size_bytes), 0)).where(
                    AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one()
        if int(used) - (row.size_bytes or 0) + len(data) > settings.USER_FILES_QUOTA_MB * 1024 * 1024:
            raise UserFileError(f"存储空间不足（配额 {settings.USER_FILES_QUOTA_MB}MB），请先清理")

        old_data = await _read_current_bytes(row)
        if expected_sha256 and hashlib.sha256(old_data or b"").hexdigest() != expected_sha256:
            raise UserFileError(
                "目标文件在任务执行期间已被更新，为避免覆盖较新的版本，本次写入已停止；"
                "请重新读取文件后再继续修改",
                status_code=409,
            )
        await _storage().write_bytes(row.storage_path, data, content_type=row.mime or "")
        await session.execute(
            update(AgentUserFile).where(AgentUserFile.id == row.id).values(size_bytes=len(data))
        )
        row.size_bytes = len(data)
        await _ensure_baseline_version(session, row, old_data)
        ver = await _add_version(
            session, row, data,
            source="generated",
            created_by="agent",
            thread_id=thread_id,
            run_id=run_id,
            change_summary=change_summary,
        )
        try:
            await session.commit()
        except Exception:
            # 补偿回滚（同 overwrite_file）：磁盘回旧字节 + 清本次快照，元数据与磁盘不背离
            if old_data is not None:
                await _storage().write_bytes(row.storage_path, old_data, content_type=row.mime or "")
            await _storage().delete(ver.storage_path)
            raise
    await _prune_old_versions(user_id, file_id)
    out = _row_to_dict(row)
    out["versionNo"] = int(ver.version_no)
    return out


async def save_draft_version(
    user_id: str,
    file_id: str,
    data: bytes,
    *,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> dict:
    """把一份产物存为**草稿版本**（§4.3.4：审查未通过不转正）——当前指针内容不动。

    先补录存量基线，再挂 status=draft 的新版本；用户可在版本历史下载诊断，
    修复重跑通过后由 overwrite_file 产生正式版本。
    """
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile)
                .where(AgentUserFile.id == file_id, AgentUserFile.user_id == user_id)
                .with_for_update()  # 行锁串行化同文件并发写（同 overwrite_file）
            )
        ).scalar_one_or_none()
        if row is None:
            raise UserFileError("文件不存在", status_code=404)
        old_data = await _read_current_bytes(row)
        await _ensure_baseline_version(session, row, old_data)
        ver = await _add_version(
            session, row, data,
            source="generated",
            created_by="agent",
            status="draft",
            thread_id=thread_id,
            run_id=run_id,
            change_summary=change_summary or "审查未通过的草稿",
        )
        try:
            await session.commit()
        except Exception:
            # 草稿不动当前指针，提交失败只需清掉本次快照文件
            await _storage().delete(ver.storage_path)
            raise
    await _prune_old_versions(user_id, file_id)
    out = _row_to_dict(row)
    out["versionNo"] = int(ver.version_no)
    out["draft"] = True
    return out


async def list_versions(user_id: str, file_id: str) -> dict:
    """文件的版本历史（新→旧）。ACL 经文件归属校验，查不到/已过期即 404。"""
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id, AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None or (row.expires_at and row.expires_at < datetime.now()):
            raise UserFileError("文件不存在或已过期", status_code=404)
        vers = (
            await session.execute(
                select(AgentUserFileVersion)
                .where(AgentUserFileVersion.file_id == file_id)
                .order_by(AgentUserFileVersion.version_no.desc())
            )
        ).scalars().all()
    return {"file": _row_to_dict(row), "versions": [_version_dict(v) for v in vers]}


async def get_revision_target_snapshot(user_id: str, file_id: str) -> dict:
    """冻结一次原位修改的目标快照。

    Harness 用 file_id + 当前字节哈希识别“用户说的还是不是刚才那个版本”，并保留来源
    Run/版本用于审计与续作。这里只读取，不改变文件或版本指针。
    """
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id,
                    AgentUserFile.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None or (row.expires_at and row.expires_at < datetime.now()):
            raise UserFileError("文件不存在或已过期", status_code=404)
        latest = (
            await session.execute(
                select(AgentUserFileVersion)
                .where(AgentUserFileVersion.file_id == file_id)
                .order_by(AgentUserFileVersion.version_no.desc())
                .limit(1)
            )
        ).scalars().first()
        payload = await _storage().read_bytes(row.storage_path)
    return {
        "file_id": row.id,
        "filename": row.filename,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "version_id": latest.id if latest else None,
        "version_no": int(latest.version_no) if latest else None,
        "original_run_id": latest.source_run_id if latest else None,
        "source_thread_id": (latest.source_thread_id if latest else None) or row.thread_id,
    }


async def read_version_bytes(user_id: str, file_id: str, version_id: str) -> tuple:
    """按归属读一个版本快照的字节。返回 (版本记录, bytes)；不存在/越权/所属文件已过期/磁盘缺失 → 404。"""
    async with async_session() as session:
        ver = (
            await session.execute(
                select(AgentUserFileVersion).where(
                    AgentUserFileVersion.id == version_id,
                    AgentUserFileVersion.file_id == file_id,
                    AgentUserFileVersion.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if ver is None:
            raise UserFileError("版本不存在", status_code=404)
        # 版本表本身不记录过期时间，需联查所属文件——所属文件已过期（尚未被惰性清理）时，
        # 历史版本也按"不存在"处理，避免绕开 TTL 语义读到本该不可见的内容
        file_row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id, AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if file_row is None or (file_row.expires_at and file_row.expires_at < datetime.now()):
            raise UserFileError("版本不存在", status_code=404)
    try:
        data = await _storage().read_bytes(ver.storage_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("版本存储字节读取失败 %s: %s", ver.storage_path, exc)
        raise UserFileError("版本文件已丢失", status_code=404)
    return ver, data


async def restore_version(user_id: str, file_id: str, version_id: str) -> dict:
    """恢复历史版本为当前版（§3.2 规则 4）：不覆盖旧快照，以历史字节创建新的 restored
    版本并写回当前指针；后续版本全部保留。"""
    ver, data = await read_version_bytes(user_id, file_id, version_id)
    # 读取历史版本与写回当前版之间可能有人刚完成编辑。把当前字节哈希交给 overwrite_file
    # 的行锁内校验，变更过就返回 409，不能用一次“恢复”悄悄覆盖对方的新版本。
    _, current = await read_bytes(user_id, file_id)
    return await overwrite_file(
        user_id, file_id, data,
        created_by="user",
        version_source="restored",
        change_summary=f"恢复自 v{ver.version_no}",
        expected_sha256=hashlib.sha256(current).hexdigest(),
    )


async def create_text_file(
    user_id: str, filename: str, content: str, *, thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """新建一个文本文件到「我的文件」（模型 create_file 工具；source=generated，默认带 TTL）。"""
    safe = _safe_name(filename)
    if not _is_text_file(safe):
        raise UserFileError(
            "仅支持新建纯文本类文件（.txt/.md/.csv/.json 等）；"
            "Word/Excel 等格式请用 bash 配合对应 Python 库生成到 /workspace/files/"
        )
    return await save_file(
        user_id, safe, str(content or "").encode("utf-8"), source="generated", thread_id=thread_id,
        run_id=run_id,
    )


async def save_generated_artifact(
    user_id: str, filename: str, content: str, *, thread_id: Optional[str] = None
) -> dict:
    """把对话流式产出的 HTML 产物落进「我的文件」（产物与文件打通，2026-07-13）。

    幂等语义（与 execute_in_sandbox 修复重跑同规则）：同会话同名 generated 文件视为同一产物的迭代——
    内容未变直接返回既有文件（unchanged=True，不产生新版本）；内容变化走 overwrite_file
    原地更新（file_id 不变、版本 +1，版本历史可回滚）；无同名则新建（source=generated 带 TTL）。
    重复触发（前端多次收尾 pass、regenerate 相同内容）因此天然去重。
    """
    safe = _safe_name(filename)
    from app.services.files.html_artifact_service import bundle_html_for_thread

    bundled = await bundle_html_for_thread(
        user_id=user_id,
        thread_id=str(thread_id or ""),
        html=str(content or ""),
        html_path=safe,
    )
    if bundled.missing:
        raise UserFileError(
            "HTML 本地图片无法解析，未保存：" + "、".join(bundled.missing[:8]),
            status_code=422,
        )
    data = bundled.data
    if not data:
        raise UserFileError("产物内容为空")
    existing = await find_generated_file(user_id, safe, thread_id)
    if existing is not None:
        _, old = await read_bytes(user_id, existing["id"])
        if hashlib.sha256(old).digest() == hashlib.sha256(data).digest():
            return {**existing, "unchanged": True}
        out = await overwrite_file(
            user_id, existing["id"], data,
            thread_id=thread_id, change_summary="对话产物更新", created_by="agent",
            expected_sha256=hashlib.sha256(old).hexdigest(),
            expected_folder_id=existing.get("folderId") or "",
        )
        return {**out, "unchanged": False}
    out = await save_file(
        user_id, safe, data, source="generated", thread_id=thread_id,
        mime=mimetypes.guess_type(safe)[0] or "text/html",
    )
    return {**out, "unchanged": False}


async def save_generated_bytes(
    user_id: str,
    filename: str,
    data: bytes,
    *,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Persist a binary/text workflow deliverable with main-chat version semantics."""
    safe = _safe_name(filename)
    if not data:
        raise UserFileError("产物内容为空")
    existing = await find_generated_file(user_id, safe, thread_id)
    if existing is not None:
        _, old = await read_bytes(user_id, str(existing["id"]))
        if hashlib.sha256(old).digest() == hashlib.sha256(data).digest():
            return {**existing, "unchanged": True}
        out = await overwrite_file(
            user_id,
            str(existing["id"]),
            data,
            filename=safe,
            thread_id=thread_id,
            run_id=run_id,
            change_summary="工作流产物更新",
            created_by="agent",
            expected_sha256=hashlib.sha256(old).hexdigest(),
            expected_folder_id=existing.get("folderId") or "",
        )
        return {**out, "unchanged": False}
    out = await save_file(
        user_id,
        safe,
        data,
        source="generated",
        thread_id=thread_id,
        run_id=run_id,
        mime=mimetypes.guess_type(safe)[0] or "application/octet-stream",
    )
    return {**out, "unchanged": False}


async def save_preview_bytes(
    user_id: str, filename: str, data: bytes, *, run_id: Optional[str] = None
) -> dict:
    """Store an expiring preview artifact that never appears in 我的文件."""
    return await save_file(
        user_id,
        filename,
        data,
        source="preview",
        run_id=run_id,
        mime=mimetypes.guess_type(_safe_name(filename))[0] or "application/octet-stream",
    )


# 单文件内容预览上限：连同操作指引 < LARGE_THRESHOLD(6000)，保证整块进模型不被相关性检索裁掉。
_ATTACH_CONTENT_CAP = 4500
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# agent-api 侧能直接解析出文本的格式（文本类 + docx/pdf）——给模型内容预览
_READABLE_DOC_EXTS = {".docx", ".pdf", ".pptx"}


async def _parse_chat_attachment(
    filename: str,
    data: bytes,
    *,
    newapi_key: str,
    audit_context: Optional[dict] = None,
) -> dict:
    """给主对话的解析设总预算，慢 OCR 不得阻塞整轮回答。"""
    from app.services.files import document_parse_service

    try:
        return await asyncio.wait_for(
            document_parse_service.parse_upload(
                filename,
                data,
                newapi_key=newapi_key,
                audit_context=audit_context,
            ),
            timeout=max(1, int(settings.CHAT_ATTACHMENT_PARSE_TIMEOUT_SECONDS)),
        )
    except asyncio.TimeoutError:
        return {
            "text": "", "status": "partial",
            "note": "文件解析耗时较长；本轮可继续用文件工具处理",
        }


_VISION_IMAGE_MAX_BYTES = 1_200_000
_VISION_IMAGE_MAX_EDGE = 1600


def _build_vision_data_url(filename: str, data: bytes, mime: str = "") -> str:
    # Build vision data URL for chat attachments from My Files.
    raw = data or b""
    if not raw:
        return ""
    guessed = (mime or "").strip() or (mimetypes.guess_type(filename)[0] or "")
    if not guessed.startswith("image/"):
        guessed = "image/jpeg"
    payload = raw
    out_mime = guessed
    if len(payload) > _VISION_IMAGE_MAX_BYTES:
        try:
            from io import BytesIO
            from PIL import Image  # type: ignore
            img = Image.open(BytesIO(payload))
            img.load()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            edge = max(w, h) or 1
            if edge > _VISION_IMAGE_MAX_EDGE:
                scale = _VISION_IMAGE_MAX_EDGE / edge
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            payload = buf.getvalue()
            out_mime = "image/jpeg"
        except Exception as exc:  # noqa: BLE001
            logger.info("vision thumbnail failed %s: %s", filename, exc)
            return ""
    if len(payload) > _VISION_IMAGE_MAX_BYTES:
        return ""
    b64 = base64.b64encode(payload).decode("ascii")
    if len(b64) > 1_800_000:
        return ""
    return f"data:{out_mime};base64,{b64}"


_DOC_VISION_EXTS = {".pdf", ".ppt", ".pptx", ".doc", ".docx"}
_VISION_DOC_PAGES = 8
_VISION_DOC_RENDER_SCALE = 1.35
_VISION_DOC_TIMEOUT_S = 45.0
_DOCUMENT_PAGE_GUARD = (
    "【文档页图已注入】下面多模态消息里附有附件的逐页渲染图。"
    "请直接看这些图评价版式、层次、配图和观感。"
    "禁止再 to-pdf / pdftoppm / 解包 PPTX 做质检，也禁止把 PDF/PNG 写进 /workspace/files 或「我的文件」。"
    "用户没有要求保存或导出时，不要产生任何文件交付物。"
)


def _attachment_scalar(att, key: str) -> str:
    if isinstance(att, dict):
        return str(att.get(key) or "")
    return str(getattr(att, key, "") or "")


async def document_page_data_urls(
    user_id: str,
    attachments: Optional[list] = None,
    *,
    max_pages: int = _VISION_DOC_PAGES,
) -> tuple[list[str], str]:
    """Rasterize PPT/PDF/Word attachments into vision data URLs. Never writes 我的文件.

    PDF is rendered in-process with pypdfium2. Office files reuse the existing
    LibreOffice preview cache (sandbox convert to PDF, then the same renderer).
    """
    from app.services.files import document_parse_service

    urls: list[str] = []
    labels: list[str] = []
    limit = max(1, int(max_pages or _VISION_DOC_PAGES))
    for att in attachments or []:
        filename = _attachment_scalar(att, "filename") or "document"
        ext = os.path.splitext(filename.lower())[1]
        file_id = _attachment_scalar(att, "file_id").strip()
        if ext not in _DOC_VISION_EXTS or not file_id:
            continue
        try:
            if ext == ".pdf":
                _row, data = await read_bytes(user_id, file_id)
                pdf_bytes = data
            else:
                pdf_bytes = await asyncio.wait_for(
                    get_preview_pdf(user_id, file_id),
                    timeout=_VISION_DOC_TIMEOUT_S,
                )
            pages, total = await asyncio.to_thread(
                document_parse_service._render_pdf_pages,
                pdf_bytes,
                limit,
                scale=_VISION_DOC_RENDER_SCALE,
            )
        except Exception:  # noqa: BLE001
            logger.info("document page render skipped file=%s", filename, exc_info=True)
            continue
        shown = 0
        for index, png in enumerate(pages, start=1):
            url = _build_vision_data_url(f"{filename}-p{index}.png", png, "image/png")
            if not url:
                continue
            urls.append(url)
            shown += 1
        if shown:
            extra = f"，共 {total} 页仅附图前 {shown} 页" if total > shown else f"，{shown} 页"
            labels.append(f"《{filename}》{extra}")
        if len(urls) >= limit:
            break
    if not urls:
        return [], ""
    note = _DOCUMENT_PAGE_GUARD + "已附图：" + "；".join(labels) + "。"
    return urls, note


def _attachment_as_dict(att: object) -> dict:
    if isinstance(att, dict):
        return dict(att)
    if hasattr(att, "model_dump"):
        return att.model_dump()
    return {
        key: getattr(att, key, None)
        for key in ("filename", "text", "file_id", "sha256", "kind", "image_url", "preview_url", "status", "note")
    }


async def restore_vision_image_attachments(user_id: str, attachments: list | None) -> list:
    """Rehydrate durable image references only at the native-vision boundary.

    Keep the original bytes and resolution.  Pure-text models use the existing OCR path instead,
    so this storage read never adds a second vision call or moves it before the public preamble.
    """
    restored: list = []
    for att in attachments or []:
        row = _attachment_as_dict(att)
        file_id = str(row.get("file_id") or "").strip()
        if row.get("kind") != "image" or row.get("image_url") or not file_id:
            restored.append(att)
            continue
        try:
            saved, data = await read_bytes(user_id, file_id)
            filename = str(getattr(saved, "filename", "") or row.get("filename") or "")
            mime = str(getattr(saved, "mime", "") or "").split(";", 1)[0].strip().lower()
            if not mime.startswith("image/"):
                mime = mimetypes.guess_type(filename)[0] or ""
            if not data or not mime.startswith("image/"):
                raise UserFileError("持久附件不是可读取的图片", status_code=422)
            expected_sha256 = str(row.get("sha256") or "").strip().lower()
            if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
                raise UserFileError("图片内容已变更，请重新上传", status_code=409)
            row["image_url"] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:  # noqa: BLE001
            logger.info("native-vision image restore failed file=%s", file_id, exc_info=True)
            row.update({
                "image_url": "",
                "status": "failed",
                "note": "图片原文件不可读取，请重新上传",
                "text": "（图片原文件不可读取，本轮未能查看图片，请重新上传）",
            })
        restored.append(row)
    return restored


async def ensure_text_model_image_ocr(
    user_id: str,
    attachments: list | None,
    *,
    newapi_key: str = "",
    audit_context: Optional[dict] = None,
) -> list:
    """Text-model Run enrichment after the durable Run exists.

    ``/chat/upload`` intentionally performs no paid OCR.  Here we enrich current-turn images and
    rich documents once with explicit Run attribution.  "My Files" attachments already carry the
    instruction envelope produced by ``build_chat_attachments`` and are not parsed a second time.
    """
    from app.services.files import document_parse_service

    filled: list = []
    for att in attachments or []:
        row = _attachment_as_dict(att)
        kind = str(row.get("kind") or "")
        file_id = str(row.get("file_id") or "").strip()
        text = str(row.get("text") or "").strip()
        ext = os.path.splitext(str(row.get("filename") or "").lower())[1]
        current_turn_document = (
            ext in _READABLE_DOC_EXTS
            and file_id
            and not text.startswith("用户选中了「我的文件」")
        )
        needs_ocr = bool(
            (kind == "image" and file_id and (not text or text.startswith("（")))
            or current_turn_document
        )
        if not needs_ocr:
            filled.append(att)
            continue
        try:
            _saved, data = await read_bytes(user_id, file_id)
            parsed = await document_parse_service.parse_upload(
                str(row.get("filename") or "image"),
                data,
                newapi_key=newapi_key,
                ocr_embedded_images=True,
                ocr_visual=True,
                audit_context=audit_context,
            )
            ocr_text = str(parsed.get("text") or "").strip()
            if ocr_text:
                row["text"] = ocr_text
                row["status"] = parsed.get("status") or row.get("status")
                row["note"] = parsed.get("note") or row.get("note")
            filled.append(row)
        except Exception:  # noqa: BLE001
            logger.info("text-model image OCR skipped file=%s", file_id, exc_info=True)
            filled.append(att)
    return filled


async def build_chat_attachments(
    user_id: str,
    file_ids: list,
    *,
    newapi_key: str = "",
    audit_context: Optional[dict] = None,
) -> list[dict]:
    """把 composer 选中的「我的文件」解析为**可操作**的对话上下文块（附件形态：filename/kind/text/image_url/file_id）。

    关键：每个块都明确告诉模型「用户选中了这个文件、沙箱路径是什么、怎么操作」——
    - 纯文本：给内容预览 + 路径版 read_file / edit_file / write_file；
    - docx/pptx/xlsx/pdf：给内容预览/结构提示 + bash（python-docx / python-pptx / openpyxl 等）；
    - 图片：vision 直传（若体积允许）+ 沙箱 /workspace/files/<名> 供嵌入/处理；
    - **pptx/xlsx 等二进制**：不按文本解码，只给 bash 路径操作指引。
    file_id 字段仅供上层门控与落库追踪；**工具面已是路径寻址**，附件文案不得再教
    update_file / execute_in_sandbox / edit_docx 等已下线工具（否则 prompt 与 tool surface 打架）。
    单轮最多处理 10 个文件；超出的不静默丢弃，追加一条 status=failed 的占位块（被
    _attachments_meta/_attachments_degradation_note 捕获后会硬注入模型，逼模型如实告知用户，
    不能假装"已看过全部选中文件"）。
    """
    from app.services.files import document_parse_service

    all_ids = list(file_ids or [])
    kept_ids, dropped_ids = all_ids[:10], all_ids[10:]
    out: list[dict] = []
    for fid in kept_ids:
        try:
            row, data = await read_bytes(user_id, str(fid))
        except UserFileError as e:
            # 失效文件回查原文件名（审计项 27）：行还在（仅过期/磁盘缺失）时用原 filename，
            # 不能把内部 UUID 当文件名甩给用户；行都没了才退回友好占位。
            display_name = "已失效的文件"
            try:
                async with async_session() as _s:
                    stale_row = (await _s.execute(
                        select(AgentUserFile).where(
                            AgentUserFile.id == str(fid), AgentUserFile.user_id == user_id)
                    )).scalar_one_or_none()
                if stale_row is not None and stale_row.filename:
                    display_name = str(stale_row.filename)
            except Exception:  # noqa: BLE001
                pass
            out.append({
                "filename": display_name, "kind": "text", "image_url": "", "file_id": str(fid),
                "text": f"（用户选中的文件《{display_name}》不可用：{e}）",
                "status": "failed", "note": "文件不可用（可能已删除或过期）",
            })
            continue

        name = row.filename
        ext = os.path.splitext(name.lower())[1]
        fid_s = row.id
        is_image = (row.mime or "").startswith("image/") or ext in _IMAGE_EXTS
        is_readable = _is_text_file(name, row.mime) or ext in _READABLE_DOC_EXTS

        att_status, att_note = "ok", None
        if is_readable:
            try:
                parsed = await _parse_chat_attachment(
                    name,
                    data,
                    newapi_key=newapi_key,
                    audit_context=audit_context,
                )
                content = str(parsed.get("text") or "")
                att_status = str(parsed.get("status") or "ok")
                att_note = parsed.get("note")
            except Exception as e:  # noqa: BLE001
                logger.warning("「我的文件」附件解析失败 %s: %s", name, e)
                content = ""
                att_status, att_note = "failed", "文件解析失败"
            if _is_text_file(name, row.mime):
                edit = (
                    f"如需**修改**它：局部小改优先 edit_file(path='{name}', old_string=原文片段, "
                    f"new_string=新内容)（精确替换，不必回传全文）；整体重写用 write_file(path='{name}', content=...)；"
                    f"复杂处理用 bash，沙箱路径 /workspace/files/{name}。"
                )
            else:
                edit = (
                    f"如需**编辑/转换**它，用 bash 在 /workspace/files/{name} 上跑对应库"
                    f"（python-docx / python-pptx / openpyxl / pypdf 等），"
                    "写回同路径即原位更新「我的文件」；不要另起无关副本交差。"
                    "若用户只是让你看/评价这份文档，平台可能已把页图直传给视觉能力："
                    "直接看图作答，不要 to-pdf 或把中间 PDF/PNG 写进「我的文件」。"
                )
            head = (
                f"用户选中了「我的文件」中的《{name}》，本轮请针对这个文件作答/操作。"
                f"沙箱路径：/workspace/files/{name}（read_file/edit_file/write_file 的 path 填「{name}」即可）。"
                f"{edit}"
            )
            content = content[:_ATTACH_CONTENT_CAP].strip()
            text = f"{head}\n--- 内容预览 ---\n{content}" if content else head
            out.append({
                "filename": name, "kind": "text", "image_url": "", "file_id": fid_s, "text": text,
                "status": att_status, "note": att_note,
            })

        elif is_image:
            ocr = ""
            image_status, image_note = "ok", None
            try:
                parsed = await _parse_chat_attachment(
                    name,
                    data,
                    newapi_key=newapi_key,
                    audit_context=audit_context,
                )
                ocr = str(parsed.get("text") or "").strip()
                image_status = str(parsed.get("status") or "ok")
                image_note = parsed.get("note")
            except Exception:  # noqa: BLE001
                ocr = ""
                image_status, image_note = "failed", "图片识别失败"
            # Harness: perception must match execution. Empty image_url forces OCR-only.
            image_url = _build_vision_data_url(name, data, row.mime or "")
            vision_hint = (
                "以多模态方式直传给视觉模型"
                if image_url
                else "因体积过大未直传像素，仅提供 OCR/路径"
            )
            text = (
                f"用户选中了图片《{name}》，本轮请针对它操作。"
                f"原图已{vision_hint}；"
                f"真实像素文件在 /workspace/files/{name}。"
                "若用户要求做成 PPT/海报/网页等产物，**必须嵌入这份真实文件**"
                "（python-pptx / Pillow / HTML img 等直接引用该路径），"
                "禁止换成占位图、假 URL、或凭空生成另一张图冒充用户素材。"
            )
            if ocr and not ocr.startswith("（"):
                text += f"\n图片视觉识别结果：\n{ocr[:6000]}"
            if not image_url:
                text += (
                    "\n（原图像素未直传：请务必用 /workspace/files/ 下的真实文件做裁剪/嵌入，"
                    "禁止臆造图片路径。）"
                )
            # Harness：OCR/视觉识别是可选增强；像素文件可用才是交付真相。
            # 若把 OCR 失败标成 status=failed，降级提示会强迫模型说「没读到附件」，
            # 与「必须嵌入 /workspace/files 原图」互相打架，弱模型会放弃嵌真图。
            if image_status in ("failed", "partial") and (image_url or data is not None):
                reason = image_note or ("图片识别失败" if image_status == "failed" else "识别不完整")
                image_note = (
                    f"{reason}；像素文件仍在 /workspace/files/{name}，"
                    "做 PPT/海报/网页必须嵌入该真实文件，禁止因识别失败改用占位图"
                )
                image_status = "ok"
            out.append({
                "filename": name,
                "kind": "image",
                "image_url": image_url,
                "file_id": fid_s,
                "text": text,
                "status": image_status,
                "note": image_note,
            })

        else:
            typ = (ext.lstrip(".") or "二进制").upper()
            text = (
                f"用户选中了《{name}》，这是一个 {typ} 文件，本轮请针对它操作。"
                f"沙箱路径：/workspace/files/{name}。"
                "用 bash + 对应库处理（xlsx→openpyxl，pptx→python-pptx，pdf→pypdf 等），"
                "修改后写回同路径即原位更新「我的文件」；需要另做一份时用明确新文件名。"
                "不要调用已下线的 office 专用工具；通用执行只使用 bash。"
            )
            out.append({"filename": name, "kind": "binary", "image_url": "", "file_id": fid_s, "text": text})

    if dropped_ids:
        logger.info("build_chat_attachments 超过单轮 10 个文件上限，丢弃 %d 个: %s", len(dropped_ids), dropped_ids)
        out.append({
            "filename": f"另有 {len(dropped_ids)} 个文件未处理", "kind": "text", "image_url": "", "file_id": "",
            "text": (f"（用户本轮选中的文件超过单轮最多处理 10 个的上限，以下 {len(dropped_ids)} 个未被读取："
                     f"{', '.join(str(x) for x in dropped_ids)}；如需查看请让用户分批重新选择，"
                     f"不要当作已检查过全部选中文件。）"),
            "status": "failed", "note": f"超过单轮 10 个文件上限，另有 {len(dropped_ids)} 个未处理",
        })

    return out


async def delete_file(user_id: str, file_id: str) -> None:
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id, AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise UserFileError("文件不存在", status_code=404)
        await _storage().delete(row.storage_path)
        await asyncio.to_thread(lambda: _purge_preview_caches(user_id, file_id))
        await _delete_version_artifacts(session, user_id, [row.id])  # 版本快照级联（Phase B）
        await session.execute(delete(AgentUserFile).where(AgentUserFile.id == row.id))
        await session.commit()


# ===== 高保真预览（版式文档 → PDF）=====
# docx/pptx 的纯前端渲染保真度有限（浏览器缺字体出 tofu 乱码符号、复杂版式走样）。
# 这里经沙箱 LibreOffice 转 PDF（字体在沙箱内嵌入渲染），浏览器原生展示 PDF——
# 与文档工厂同一转换内核（doc_factory 已验 Word→PDF 含中文）。
_PREVIEW_PDF_EXTS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
_PREVIEW_CONVERT_TIMEOUT_MS = 120_000


def _preview_cache_dir(user_id: str) -> Path:
    return _root() / user_id / ".preview"


def _preview_cache_path(user_id: str, file_id: str, content_sha: str) -> Path:
    """转换结果按 file_id + 内容哈希落盘缓存（2026-07-23）。

    早期实现只按 file_id 命名并假设「office 文件内容不可变」——但 PPT 经「幻灯片编辑→
    重新编译」会原地生成新版本（同 file_id 内容变了），旧假设失效导致缩略图/预览读到
    过期 PDF（现象：10 页 16:9 的 deck 缩略图错成某个旧版本甚至 A4 单页）。改为把当前
    内容哈希编进缓存名——内容一变缓存名就变，天然不命中旧缓存，不依赖各写入路径记得清缓存。"""
    return _preview_cache_dir(user_id) / f"{file_id}_{content_sha}.pdf"


def _purge_preview_caches(user_id: str, file_id: str) -> None:
    """删除某文件的全部预览缓存（各内容版本 + 早期按 file_id 命名的遗留缓存）。
    删除/过期清理时调用；get_preview_pdf 写新缓存前也顺带清掉同文件的旧内容缓存。"""
    d = _preview_cache_dir(user_id)
    try:
        # file_id 是定长 hex，`{file_id}*` 只会命中该文件自己的 `.pdf`/`_<sha>.pdf`
        for p in d.glob(f"{file_id}*.pdf"):
            p.unlink(missing_ok=True)
    except OSError:
        pass


def preview_pdf_supported(filename: str) -> bool:
    return os.path.splitext(str(filename or "").lower())[1] in _PREVIEW_PDF_EXTS


# 预热任务持引用防 GC（裸 create_task 可能被回收，B4 教训）
_preview_prewarm_tasks: set = set()

# 预览转换按文件互斥（深扫收尾 2026-07-26）：预热 fire-and-forget 与用户点开预览会同窗
# 竞速——两边都查缓存未命中就各起一个 LibreOffice 沙箱转同一个文件（浪费并发槽位与
# 10-30s 容器），且 _write_cache 的 glob 清理会让两个不同内容哈希的并发写者互删对方刚写的
# 缓存。按 file_id 串行化整段「查缓存→转换→写缓存」。进程内有效（同 context_service._lock_for）。
_preview_locks: dict = {}


def _preview_lock_for(file_id: str) -> asyncio.Lock:
    lock = _preview_locks.get(file_id)
    if lock is None:
        lock = asyncio.Lock()
        _preview_locks[file_id] = lock
        if len(_preview_locks) > 512:  # 防无界增长：满了整体换新（锁只用于降重，不保正确性）
            _preview_locks.clear()
            _preview_locks[file_id] = lock
    return lock


def schedule_preview_prewarm(user_id: str, file_id: str, filename: str) -> None:
    """生成/覆盖版式文档后，后台预热预览 PDF 缓存（2026-07-20 进入预览慢优化）。

    首次预览要起沙箱跑 LibreOffice 转 PDF（容器冷启动 + soffice 冷启动，约 10-30s），
    用户点开时干等。改为文件一落地就后台预转：用户还在看回答/继续操作时缓存已就绪，
    点开即命中秒显。best-effort——不支持转换的格式（图片/文本）直接跳过；失败只记日志、
    绝不影响保存与对话主流程；无运行中事件循环（同步上下文）时静默跳过。"""
    if not preview_pdf_supported(filename):
        return

    async def _run() -> None:
        try:
            await get_preview_pdf(user_id, file_id)
        except Exception as e:  # noqa: BLE001
            logger.info("预览预热跳过 file=%s: %s", file_id, e)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run())
    _preview_prewarm_tasks.add(task)
    task.add_done_callback(_preview_prewarm_tasks.discard)


async def get_preview_pdf(user_id: str, file_id: str) -> bytes:
    """版式文档 → PDF 预览字节。首次转换起沙箱（约 10-30s），之后命中缓存秒回。

    缓存按「file_id + 当前内容哈希」命名（见 _preview_cache_path）：先读当前字节算哈希再查
    缓存，内容一变即不命中旧缓存，重编译后的 PPT 不会再读到过期版本。"""
    row, key = await get_file(user_id, file_id)
    if not preview_pdf_supported(row.filename):
        raise UserFileError("该格式不支持转换预览", status_code=400)
    async with _preview_lock_for(str(file_id)):
        return await _get_preview_pdf_locked(user_id, file_id, row, key)


async def _get_preview_pdf_locked(user_id: str, file_id: str, row, key: str) -> bytes:
    from app.services.sandbox import office_convert

    # 先读当前字节（转换本来也要读）并按内容哈希定位缓存——命中即返回，不命中才转换。
    # 读字节在锁内：并发的第二个等待者进来时能命中第一个刚写好的缓存，不再重复转换
    data = await _storage().read_bytes(key)
    content_sha = hashlib.sha256(data).hexdigest()[:16]
    cache = _preview_cache_path(user_id, file_id, content_sha)
    if await asyncio.to_thread(cache.is_file):
        return await asyncio.to_thread(cache.read_bytes)

    # 与文档排版 / 格式转换工具同一条 soffice 链路（含中文字体别名），见 sandbox/office_convert.py
    try:
        pdf = await office_convert.convert_with_soffice(
            row.filename, data, "pdf", timeout_ms=_PREVIEW_CONVERT_TIMEOUT_MS,
        )
    except office_convert.OfficeConvertError as exc:
        logger.warning("预览转换失败 file=%s %s", row.filename, exc)
        raise UserFileError("预览转换失败，请稍后重试或下载后本地查看", status_code=502) from exc

    def _write_cache() -> None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        # 写当前内容缓存前，清掉该文件的旧内容缓存（遗留 file_id.pdf / 历史版本），避免堆积
        for p in cache.parent.glob(f"{file_id}*.pdf"):
            if p.name != cache.name:
                p.unlink(missing_ok=True)
        cache.write_bytes(pdf)

    await asyncio.to_thread(_write_cache)
    return pdf


async def keep_file(user_id: str, file_id: str) -> dict:
    """「保留」：清 expires_at 转永久（generated 产物免于 TTL 清理）。"""
    async with async_session() as session:
        row = (
            await session.execute(
                select(AgentUserFile).where(
                    AgentUserFile.id == file_id, AgentUserFile.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None or (row.expires_at and row.expires_at < datetime.now()):
            raise UserFileError("文件不存在或已过期", status_code=404)
        await session.execute(
            update(AgentUserFile).where(AgentUserFile.id == row.id).values(expires_at=None)
        )
        await session.commit()
        row.expires_at = None
        return _row_to_dict(row)
