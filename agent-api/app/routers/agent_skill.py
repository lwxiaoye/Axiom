"""
智能体技能（Agent Skill）路由，契约对齐前端 src/views/workflow/api/skill.api.ts（v1.9 §10.5.7）。

- 技能 = 版本化技能包（content 为 SKILL.md 说明书），挂载到对话 Agent 后注入运行时；
- 创建 = 按名称+描述异步生成说明书（creationStatus: creating -> ready/failed）；
- 导入 = zip 包（skill.json 元数据 + SKILL.md 内容）；
- 沙箱执行运行时未启用前，内容以能力说明书形态被 agent 节点注入（manifest D-11）。
"""
import asyncio
import base64
import io
import json
import logging
import time
import uuid
import zipfile
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import UserContext, current_user
from app.core.model_endpoint import get_model_base_url
from app.core.config import settings
from app.core.database import async_session
from app.models import AgentSkill, AgentSkillVersion
from app.services.platform import zip_guard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skill", tags=["agent-skill"])

VALID_CATEGORIES = {"search", "tool", "coding", "data", "analysis", "communication", "other"}
CONTENT_LIMIT = 200_000

# 技能包解压体积闸（zip_guard）：上传只卡了压缩后 5MB，而 5MB 的 DEFLATE 能解出数 GB。
# 导入与挂载共用同一组阈值——挂载走的是同一批入库字节，两边口径必须一致。
SKILL_ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024
SKILL_ZIP_MAX_ENTRY_BYTES = 50 * 1024 * 1024
SKILL_ZIP_MAX_ENTRIES = 5_000

SYSTEM_SKILL_SEEDS = [
    {
        "id": "sys-skill-summary",
        "name": "文本总结",
        "description": "长文本摘要与要点提取：先抓结论，再列关键论据，保留数字与专有名词。",
        "category": ["analysis"],
        "content": (
            "# 文本总结技能\n\n## 能力\n对任意长度中文/英文文本生成结构化摘要。\n\n## 执行步骤\n"
            "1. 通读全文，识别文体（新闻/报告/对话/制度条文）；\n"
            "2. 输出一句话结论（TL;DR）；\n"
            "3. 按重要性列出 3-7 条要点，保留关键数字、日期与专有名词；\n"
            "4. 如文本包含行动项，单独列出「待办/行动」小节。\n\n## 约束\n不添加原文没有的事实；不确定处标注“原文未明确”。"
        ),
    },
    {
        "id": "sys-skill-table",
        "name": "表格分析",
        "description": "解读表格数据：找趋势、异常与对比结论，并给出下一步建议。",
        "category": ["data", "analysis"],
        "content": (
            "# 表格分析技能\n\n## 能力\n解读 Markdown/CSV 形态的表格数据。\n\n## 执行步骤\n"
            "1. 先复述表头，确认度量口径与单位；\n"
            "2. 计算/比较关键行列（最大、最小、同比环比）；\n"
            "3. 指出异常值并给出可能解释；\n"
            "4. 输出「结论 + 建议」两段式回答。\n\n## 约束\n计算过程要展示；数据不足时说明缺口而不是猜测。"
        ),
    },
]


class SkillUpsertRequest(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[list] = None


# 后台生成任务强引用集合：create_task 只留弱引用，无引用的任务可能被 GC 中断
_background_tasks: set = set()


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _get_visible_skill(session, skill_id: str, user_id: str) -> AgentSkill:
    """系统技能全员可见；个人技能仅属主可见（防跨用户读取说明书内容）。"""
    skill = (await session.execute(select(AgentSkill).where(AgentSkill.id == skill_id))).scalar_one_or_none()
    if skill is None:
        raise HTTPException(404, "技能不存在")
    if skill.source != "system" and skill.owner_user_id != user_id:
        raise HTTPException(403, "无权访问该技能")
    return skill


def _skill_dict(skill: AgentSkill) -> dict:
    try:
        category = json.loads(skill.category_json or "[]")
    except json.JSONDecodeError:
        category = []
    return {
        "id": skill.id,
        "parentId": None,
        "type": "skill",
        "source": skill.source,
        "name": skill.name,
        "description": skill.description or "",
        "category": category if isinstance(category, list) else [],
        "currentVersionId": skill.current_version_id,
        "creationStatus": skill.creation_status,
        "creationError": skill.creation_error,
        "createTime": skill.create_time.isoformat(sep=" ", timespec="seconds") if skill.create_time else None,
        "updateTime": skill.update_time.isoformat(sep=" ", timespec="seconds") if skill.update_time else None,
    }


def _version_dict(version: AgentSkillVersion) -> dict:
    import_source = None
    if version.import_source_json:
        try:
            import_source = json.loads(version.import_source_json)
        except json.JSONDecodeError:
            import_source = None
    return {
        "id": version.id,
        "skillId": version.skill_id,
        "versionName": version.version_name,
        "importSource": import_source,
        "createdAt": version.create_time.isoformat(sep=" ", timespec="seconds") if version.create_time else None,
    }


def _clean_categories(raw) -> str:
    values = [item for item in (raw or []) if isinstance(item, str) and item in VALID_CATEGORIES]
    return json.dumps(values, ensure_ascii=False)


async def _seed_system_skills() -> None:
    """首次访问时补齐系统技能（固定 id，幂等）。"""
    async with async_session() as session:
        existing = (
            (await session.execute(select(AgentSkill.id).where(AgentSkill.source == "system"))).scalars().all()
        )
        existing_ids = set(existing)
        for seed in SYSTEM_SKILL_SEEDS:
            if seed["id"] in existing_ids:
                continue
            version_id = uuid.uuid4().hex
            session.add(
                AgentSkill(
                    id=seed["id"],
                    source="system",
                    name=seed["name"],
                    description=seed["description"],
                    category_json=json.dumps(seed["category"], ensure_ascii=False),
                    creation_status="ready",
                    current_version_id=version_id,
                    owner_user_id="system",
                )
            )
            session.add(
                AgentSkillVersion(id=version_id, skill_id=seed["id"], version_name="v1", content=seed["content"])
            )
        await session.commit()


async def _generate_skill_content(skill_id: str, name: str, description: str, user_id: str) -> None:
    """后台生成技能说明书：成功 -> ready + 新版本；失败 -> failed + 原因。"""
    error_text = ""
    content = ""
    try:
        from app.routers.workflow import _prepare_llm

        class _U:
            pass

        user = _U()
        user.user_id = user_id
        api_key, default_model = await _prepare_llm(user)  # type: ignore[arg-type]
        if not api_key or not default_model:
            raise RuntimeError("当前用户没有可用的模型凭证，无法生成技能包")
        prompt = (
            "为智能体平台生成一份技能包说明书（SKILL.md，Markdown 格式）。"
            "说明书将注入对话 Agent 的 system prompt，指导模型执行该技能。\n"
            f"技能名称：{name}\n技能描述：{description or '（未提供，按名称合理推断）'}\n\n"
            "要求：包含「# 技能名」「## 能力」「## 执行步骤」（编号步骤）「## 输入输出约定」「## 约束」五部分，"
            "面向模型的可执行指令口吻，不超过 600 字，直接输出 Markdown，不要额外解释。"
        )
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{get_model_base_url().rstrip('/')}/chat/completions",
                json={"model": default_model, "messages": [{"role": "user", "content": prompt}], "stream": False},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"模型调用失败: {resp.status_code}")
            data = resp.json()
            content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("模型返回空内容")
    except Exception as exc:  # 后台任务：一切异常转 failed 状态
        logger.warning("skill generation failed: %s", exc)
        error_text = str(exc)[:1000]

    async with async_session() as session:
        skill = (await session.execute(select(AgentSkill).where(AgentSkill.id == skill_id))).scalar_one_or_none()
        if skill is None:
            return
        if error_text:
            skill.creation_status = "failed"
            skill.creation_error = error_text
        else:
            version = AgentSkillVersion(
                id=uuid.uuid4().hex, skill_id=skill_id, version_name="v1", content=content[:CONTENT_LIMIT]
            )
            session.add(version)
            skill.creation_status = "ready"
            skill.creation_error = None
            skill.current_version_id = version.id
        await session.commit()


@router.get("/list")
async def list_skills(
    keyword: Optional[str] = None,
    source: Optional[str] = None,
    user: UserContext = Depends(current_user),
):
    await _seed_system_skills()
    source_filter = (source or "").strip().lower()
    async with async_session() as session:
        if source_filter == "personal":
            query = select(AgentSkill).where(AgentSkill.source == "personal", AgentSkill.owner_user_id == user.user_id)
        elif source_filter == "system":
            query = select(AgentSkill).where(AgentSkill.source == "system")
        else:
            query = select(AgentSkill).where(
                (AgentSkill.source == "system") | (AgentSkill.owner_user_id == user.user_id)
            )
        rows = (await session.execute(query)).scalars().all()
    items = [_skill_dict(row) for row in rows]
    if keyword:
        key = keyword.strip().lower()
        items = [i for i in items if key in i["name"].lower() or key in (i["description"] or "").lower()]
    # 系统技能置前，其余按创建时间倒序
    items.sort(key=lambda i: (0 if i["source"] == "system" else 1, -(time.mktime(time.strptime(i["createTime"], "%Y-%m-%d %H:%M:%S")) if i["createTime"] else 0)))
    return items


@router.post("/add")
async def add_skill(payload: SkillUpsertRequest, user: UserContext = Depends(current_user)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "技能名称不能为空")
    skill = AgentSkill(
        id=uuid.uuid4().hex,
        source="personal",
        name=name[:128],
        description=(payload.description or "")[:1024],
        category_json=_clean_categories(payload.category),
        creation_status="creating",
        owner_user_id=user.user_id,
    )
    async with async_session() as session:
        session.add(skill)
        await session.commit()
        await session.refresh(skill)
        result = _skill_dict(skill)
    _spawn_background(_generate_skill_content(result["id"], result["name"], result["description"], user.user_id))
    return result


@router.put("/edit")
async def edit_skill(payload: SkillUpsertRequest, user: UserContext = Depends(current_user)):
    if not payload.id:
        raise HTTPException(400, "缺少技能 ID")
    async with async_session() as session:
        skill = (await session.execute(select(AgentSkill).where(AgentSkill.id == payload.id))).scalar_one_or_none()
        if skill is None:
            raise HTTPException(404, "技能不存在")
        if skill.source == "system" or skill.owner_user_id != user.user_id:
            raise HTTPException(403, "只能编辑自己创建的技能")
        if payload.name is not None:
            skill.name = payload.name.strip()[:128] or skill.name
        if payload.description is not None:
            skill.description = payload.description[:1024]
        if payload.category is not None:
            skill.category_json = _clean_categories(payload.category)
        await session.commit()
        await session.refresh(skill)
        return _skill_dict(skill)


@router.delete("/delete")
async def delete_skill(id: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        skill = (await session.execute(select(AgentSkill).where(AgentSkill.id == id))).scalar_one_or_none()
        if skill is None:
            return {"success": True}
        if skill.source == "system" or skill.owner_user_id != user.user_id:
            raise HTTPException(403, "只能删除自己创建的技能")
        versions = (
            (await session.execute(select(AgentSkillVersion).where(AgentSkillVersion.skill_id == id))).scalars().all()
        )
        for version in versions:
            await session.delete(version)
        await session.delete(skill)
        await session.commit()
    return {"success": True}


@router.post("/import")
async def import_skill(file: UploadFile = File(...), user: UserContext = Depends(current_user)):
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "技能包不能超过 5MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "不是合法的 zip 文件")

    # 5MB 是**压缩后**的体积，挡不住解压炸弹：先按中央目录声明的解压后大小判总量/单条目/
    # 条目数，再用 ZipReadBudget 在真正 read 时按实际字节兜底（声明值可伪造）。
    metadata: dict = {}
    content = ""
    md_fallback = ""
    has_scripts = False
    try:
        zip_guard.ensure_zip_within_limits(
            archive,
            label="技能包",
            max_total_bytes=SKILL_ZIP_MAX_TOTAL_BYTES,
            max_entry_bytes=SKILL_ZIP_MAX_ENTRY_BYTES,
            max_entries=SKILL_ZIP_MAX_ENTRIES,
        )
        budget = zip_guard.ZipReadBudget(
            label="技能包",
            max_total_bytes=SKILL_ZIP_MAX_TOTAL_BYTES,
            max_entry_bytes=SKILL_ZIP_MAX_ENTRY_BYTES,
        )
        for entry in archive.namelist():
            if entry.endswith("/"):
                continue
            lower = entry.lower()
            if lower.endswith("skill.json") and not metadata:
                blob = budget.read(archive, entry)
                try:
                    metadata = json.loads(blob.decode("utf-8"))
                except Exception:
                    raise HTTPException(400, "skill.json 解析失败")
            elif lower.endswith("skill.md") and not content:
                content = budget.read(archive, entry).decode("utf-8", errors="replace")
            elif lower.endswith(".md") and not md_fallback:
                md_fallback = budget.read(archive, entry).decode("utf-8", errors="replace")
            elif lower.endswith((".sh", ".py", ".js", ".ts")) or "entrypoint" in lower:
                has_scripts = True
    except zip_guard.ZipBombError as exc:
        logger.warning("拒绝导入疑似解压炸弹技能包 %s: %s", file.filename, exc.reason)
        raise HTTPException(400, exc.message)
    content = (content or md_fallback)[:CONTENT_LIMIT]

    name = str(metadata.get("name") or "").strip() or (file.filename or "导入技能").rsplit(".", 1)[0]
    if not content and not metadata:
        raise HTTPException(400, "包内缺少 skill.json 或 SKILL.md，无法识别技能元数据")

    # 无条件保留整个技能包文件树（说明书之外的资源文件与脚本一并入库），供沙箱部署；base64 存库。
    # 执行形态（hasScripts）不在存储层区分，由 load_skill_packages 按包内容派生（ADR-043）。
    package_b64 = base64.b64encode(raw).decode("ascii")

    skill = AgentSkill(
        id=uuid.uuid4().hex,
        source="personal",
        name=name[:128],
        description=str(metadata.get("description") or "")[:1024],
        category_json=_clean_categories(metadata.get("category")),
        creation_status="ready",
        owner_user_id=user.user_id,
    )
    version = AgentSkillVersion(
        id=uuid.uuid4().hex,
        skill_id=skill.id,
        version_name="v1",
        content=content or None,
        package_b64=package_b64,
        import_source_json=json.dumps(
            {
                "originalFilename": file.filename or "",
                "importedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hasScripts": has_scripts,
            },
            ensure_ascii=False,
        ),
    )
    skill.current_version_id = version.id
    async with async_session() as session:
        session.add(skill)
        session.add(version)
        await session.commit()
        await session.refresh(skill)
        result = _skill_dict(skill)
    return result


@router.get("/versions")
async def list_versions(skillId: str, user: UserContext = Depends(current_user)):
    async with async_session() as session:
        await _get_visible_skill(session, skillId, user.user_id)
        rows = (
            (
                await session.execute(
                    select(AgentSkillVersion)
                    .where(AgentSkillVersion.skill_id == skillId)
                    .order_by(AgentSkillVersion.create_time.desc())
                )
            )
            .scalars()
            .all()
        )
    return [_version_dict(row) for row in rows]


@router.get("/content")
async def skill_content(skillId: str, versionId: Optional[str] = None, user: UserContext = Depends(current_user)):
    """技能说明书内容（默认当前版本），供面板预览与 agent 注入。"""
    async with async_session() as session:
        skill = await _get_visible_skill(session, skillId, user.user_id)
        target_version = versionId or skill.current_version_id
        version = None
        if target_version:
            version = (
                await session.execute(select(AgentSkillVersion).where(AgentSkillVersion.id == target_version))
            ).scalar_one_or_none()
        if version is None:
            version = (
                await session.execute(
                    select(AgentSkillVersion)
                    .where(AgentSkillVersion.skill_id == skillId)
                    .order_by(AgentSkillVersion.create_time.desc())
                )
            ).scalar_one_or_none()
    if version is None:
        return {"skillId": skillId, "versionId": None, "versionName": None, "content": ""}
    return {
        "skillId": skillId,
        "versionId": version.id,
        "versionName": version.version_name,
        "content": version.content or "",
    }


async def load_skill_contents(
    skill_ids: list[str], owner_user_id: Optional[str] = None, limit_each: int = 2000
) -> list[dict]:
    """内部复用：agent 执行器批量取技能说明书（当前版本，截断）。

    传入 owner_user_id 时仅返回系统技能与该用户自己的技能，防止图定义伪造 skillId 越权读取。
    """
    if not skill_ids:
        return []
    async with async_session() as session:
        query = select(AgentSkill).where(AgentSkill.id.in_(skill_ids))
        if owner_user_id is not None:
            query = query.where((AgentSkill.source == "system") | (AgentSkill.owner_user_id == owner_user_id))
        skills = (await session.execute(query)).scalars().all()
        version_ids = [s.current_version_id for s in skills if s.current_version_id]
        versions = {}
        if version_ids:
            rows = (
                (await session.execute(select(AgentSkillVersion).where(AgentSkillVersion.id.in_(version_ids))))
                .scalars()
                .all()
            )
            versions = {row.id: row for row in rows}
    result = []
    for skill in skills:
        version = versions.get(skill.current_version_id or "")
        content = (version.content or "") if version else ""
        result.append(
            {
                "skillId": skill.id,
                "name": skill.name,
                "description": skill.description or "",
                "content": content[:limit_each],
            }
        )
    return result


# 单技能文件树注入上限（防超大包撑爆沙箱）
_SKILL_PACKAGE_MAX_FILES = 200
_SKILL_PACKAGE_MAX_BYTES = 20 * 1024 * 1024


async def load_skill_packages(skill_ids: list[str], owner_user_id: Optional[str] = None) -> list[dict]:
    """内部复用：取技能文件树，供容器沙箱部署。

    返回 [{skillId, versionId, name, hasScripts, entrypoint, files:{相对路径:bytes}}]。
    有 package_b64 的技能解压 zip（剥掉公共顶层目录）；无整包的内容型技能物化单个 SKILL.md。
    hasScripts 按解出的文件树派生（脚本扩展名或 entrypoint，ADR-043）——新导入无条件存整包，
    旧数据仅脚本包存过整包，两者派生结果一致。ACL 与 load_skill_contents 一致。

    包体解压后超限时抛 zip_guard.ZipBombError（消息面向用户），由调用方按节点失败处理——
    与沙箱故障同一条「不降级」原则（ADR-043）。
    """
    if not skill_ids:
        return []
    async with async_session() as session:
        query = select(AgentSkill).where(AgentSkill.id.in_(skill_ids))
        if owner_user_id is not None:
            query = query.where((AgentSkill.source == "system") | (AgentSkill.owner_user_id == owner_user_id))
        skills = (await session.execute(query)).scalars().all()
        version_ids = [s.current_version_id for s in skills if s.current_version_id]
        versions = {}
        if version_ids:
            rows = (
                (await session.execute(select(AgentSkillVersion).where(AgentSkillVersion.id.in_(version_ids))))
                .scalars()
                .all()
            )
            versions = {row.id: row for row in rows}

    result = []
    for skill in skills:
        version = versions.get(skill.current_version_id or "")
        if version is None:
            continue
        files, entrypoint = _extract_skill_files(version)
        result.append(
            {
                "skillId": skill.id,
                "versionId": version.id,
                "name": skill.name,
                "hasScripts": _files_have_scripts(files, entrypoint),
                "entrypoint": entrypoint,
                "files": files,
            }
        )
    return result


_SCRIPT_SUFFIXES = (".sh", ".py", ".js", ".ts")


def _files_have_scripts(files: dict[str, bytes], entrypoint: Optional[str]) -> bool:
    """执行形态为版本内容的派生属性（ADR-043）：含脚本/entrypoint 的版本才需要沙箱执行。"""
    if entrypoint:
        return True
    return any(
        rel.lower().endswith(_SCRIPT_SUFFIXES) or "entrypoint" in rel.lower() for rel in files
    )


def _extract_skill_files(version: AgentSkillVersion) -> tuple[dict[str, bytes], Optional[str]]:
    """技能版本 -> {相对路径: bytes} + entrypoint 相对路径（若有）。

    体积超限抛 :class:`zip_guard.ZipBombError`（**不降级**）：挂载路径每次挂技能都会重新
    解一次同一个包，静默截断等于让攻击者反复触发；技能实际不可用就必须明确失败（ADR-043
    同款语义）。导入闸只在入库时跑一次，历史入库数据仍要在这里再判一次。
    """
    if not version.package_b64:
        # 内容型技能：物化 SKILL.md
        content = (version.content or "").encode("utf-8")
        return ({"SKILL.md": content} if content else {}), None
    try:
        raw = base64.b64decode(version.package_b64)
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        content = (version.content or "").encode("utf-8")
        return ({"SKILL.md": content} if content else {}), None

    zip_guard.ensure_zip_within_limits(
        archive,
        label="技能包",
        max_total_bytes=SKILL_ZIP_MAX_TOTAL_BYTES,
        max_entry_bytes=SKILL_ZIP_MAX_ENTRY_BYTES,
        max_entries=SKILL_ZIP_MAX_ENTRIES,
    )
    names = [n for n in archive.namelist() if not n.endswith("/")]
    # 剥掉 zip 公共顶层目录（很多包解压后带一层 skill-name/）
    common_prefix = ""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1 and all("/" in n for n in names):
        common_prefix = tops.pop() + "/"

    files: dict[str, bytes] = {}
    entrypoint: Optional[str] = None
    total = 0
    # 注入预算与导入闸同阈值（超限抛错=明确失败）；下面的 _SKILL_PACKAGE_MAX_BYTES(20MB)
    # 是更靠前的「注入多少进沙箱」软上限，命中它照旧优雅截断，两者不冲突。
    budget = zip_guard.ZipReadBudget(
        label="技能包",
        max_total_bytes=SKILL_ZIP_MAX_TOTAL_BYTES,
        max_entry_bytes=SKILL_ZIP_MAX_ENTRY_BYTES,
    )
    for name in names:
        if len(files) >= _SKILL_PACKAGE_MAX_FILES:
            break
        rel = name[len(common_prefix):] if common_prefix and name.startswith(common_prefix) else name
        rel = rel.lstrip("/")
        if not rel or ".." in rel.split("/"):  # 防路径穿越
            continue
        try:
            data = budget.read(archive, name)
        except zip_guard.ZipBombError:
            raise  # 体积超限=技能不可用，明确失败（区别于单文件解码坏了跳过）
        except Exception:  # noqa: BLE001
            continue
        total += len(data)
        if total > _SKILL_PACKAGE_MAX_BYTES:
            break
        files[rel] = data
        if rel.lower() == "entrypoint.sh" or rel.lower().endswith("/entrypoint.sh"):
            entrypoint = rel
    return files, entrypoint
