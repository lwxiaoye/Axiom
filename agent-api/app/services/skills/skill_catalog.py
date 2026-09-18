"""agent-api 自持的 Skill 目录：技能广场、@Skill、use_skill 与演示文稿助手的**唯一**来源。

为什么有这个模块（2026-09-18）：
- Skill 目录原先由 JeecgBoot(Java) 的 `/ai/skill/*` 提供。Java 下线后 auth-api 只剩一个
  永远返回 `[]` 的 `/ai/skill/list` 桩、`/ai/skill/readme|files|file|package` 一律 404。
  主对话仍按旧路回源，于是：Skill 广场空壳、@Skill 永远空、演示文稿助手因为
  「未找到已启用的 ppt-studio」在 waiting_system 里无限自动恢复。
- 真相其实一直在本进程：`agent_skill` / `agent_skill_version` 两张表（`routers/agent_skill.py`
  的系统技能 + 用户导入的技能包），以及 `services/skills/builtin/` 下随代码发布的内置技能包
  （ppt-studio）。这里把三者收成一个目录，并给出与旧 auth-api 记录**同形**的字段
  （`skillId`/`id`/`name`/`description`/`enabled`/`version`），下游消费者
  （turn_context_builder、ppt_policy、presentation/prepare、slides_compile、main_tool_turn）
  一行不改就能继续按 `skillId` + `enabled` 工作。

内置技能如何进目录：
- 目录**只读库表**（这样 ACL、启停、版本都只有一处事实），内置包靠幂等播种
  `ensure_builtin_skills_seeded()` 注册成 `source=system` 的记录：id 就用目录名（`ppt-studio`），
  与演示文稿助手钉死的 canonical id 一致；版本号取包内 `skill.json` 的 `version`，
  SKILL.md 正文写进 `content`。包**文件树不进库**——`import_source_json` 记 `{"builtin": slug}`，
  取包时从磁盘读（磁盘才是真相，升级镜像即升级技能，不留一份会过期的 zip 副本）。
- 播种在 API 启动期（main.py）与首次读目录时各跑一次（进程级标记），worker 进程不经过
  main.py 的 lifespan，靠后者自足。

ACL：系统技能全员可见；个人技能仅属主可见。token → 用户走 `user_from_token`（带缓存），
解析不到用户时只返回系统技能——演示文稿助手的硬门槛（ppt-studio 可见）不能被 auth-api
抖动误判成「技能不存在」。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from app.core.database import async_session
from app.models import AgentSkill, AgentSkillVersion

logger = logging.getLogger(__name__)

BUILTIN_ROOT = Path(__file__).resolve().parent / "builtin"
# 内置包目录名即技能 id：只允许 kebab-case，避免把任意字符串拼进路径。
_BUILTIN_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
# 与 routers/agent_skill.py 的 _SKILL_PACKAGE_MAX_FILES/_SKILL_PACKAGE_MAX_BYTES、
# skill_package_bridge._MAX_FILES/_MAX_BYTES 同口径：一个包挂进沙箱最多多少文件/字节。
BUILTIN_MAX_FILES = 200
BUILTIN_MAX_BYTES = 20 * 1024 * 1024
# 内置包里不该挂进沙箱的目录：单测与 fixtures 是仓库自检用，模型用不到，白占挂载配额。
_BUILTIN_SKIP_DIRS = {"tests", "__pycache__", ".git"}

# 进程级「已播种」标记：目录每轮都读，播种只需成功一次。失败不置位，下次再试。
_builtin_seeded = False


# ---------------------------------------------------------------- 内置包（磁盘）
def builtin_skill_dir(slug: str) -> Optional[Path]:
    """内置技能目录（不存在/名字不合法返回 None）。只接受 BUILTIN_ROOT 的直接子目录。"""
    value = str(slug or "").strip()
    if not _BUILTIN_SLUG_RE.match(value):
        return None
    path = BUILTIN_ROOT / value
    if not path.is_dir():
        return None
    try:
        if path.resolve().parent != BUILTIN_ROOT.resolve():
            return None
    except OSError:
        return None
    return path


def builtin_skill_slugs() -> list[str]:
    """随代码发布的内置技能：`builtin/<slug>/SKILL.md` 存在即算一个。"""
    if not BUILTIN_ROOT.is_dir():
        return []
    out: list[str] = []
    for child in sorted(BUILTIN_ROOT.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file() and _BUILTIN_SLUG_RE.match(child.name):
            out.append(child.name)
    return out


def load_builtin_manifest(slug: str) -> dict:
    """`skill.json` 元数据 + SKILL.md 正文。缺 skill.json 时按目录名兜底。"""
    root = builtin_skill_dir(slug)
    if root is None:
        raise FileNotFoundError(f"内置技能不存在：{slug}")
    meta: dict = {}
    manifest = root / "skill.json"
    if manifest.is_file():
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except Exception as exc:  # noqa: BLE001
            logger.warning("内置技能 %s 的 skill.json 解析失败，按目录名兜底: %s", slug, exc)
    content = (root / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    category = meta.get("category")
    if not isinstance(category, list):
        category = ["other"]
    return {
        "slug": slug,
        "name": str(meta.get("name") or slug).strip() or slug,
        "description": str(meta.get("description") or "").strip(),
        "category": [str(item) for item in category if isinstance(item, str)],
        "version": str(meta.get("version") or "").strip() or "v1",
        "content": content,
    }


def builtin_package_files(slug: str) -> tuple[dict[str, bytes], Optional[str]]:
    """内置包文件树 {相对路径: bytes} + entrypoint。按 BUILTIN_MAX_FILES/BYTES 截断（与导入包同配额）。"""
    root = builtin_skill_dir(slug)
    if root is None:
        raise FileNotFoundError(f"内置技能不存在：{slug}")
    files: dict[str, bytes] = {}
    entrypoint: Optional[str] = None
    total = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel_parts = path.relative_to(root).parts
        if any(part in _BUILTIN_SKIP_DIRS for part in rel_parts[:-1]):
            continue
        rel = "/".join(rel_parts)
        if len(files) >= BUILTIN_MAX_FILES:
            logger.warning("内置技能 %s 文件数超过 %d，其余未挂载", slug, BUILTIN_MAX_FILES)
            break
        data = path.read_bytes()
        if total + len(data) > BUILTIN_MAX_BYTES:
            logger.warning("内置技能 %s 超出 %dMB 挂载预算，跳过 %s", slug, BUILTIN_MAX_BYTES // 1024 // 1024, rel)
            continue
        total += len(data)
        files[rel] = data
        low = rel.lower()
        if low == "entrypoint.sh" or low.endswith("/entrypoint.sh"):
            entrypoint = rel
    return files, entrypoint


def builtin_slug_of_version(version: Optional[AgentSkillVersion]) -> Optional[str]:
    """版本行是否指向内置包：`import_source_json.builtin` 即目录名。"""
    if version is None or not version.import_source_json:
        return None
    try:
        meta = json.loads(version.import_source_json)
    except Exception:  # noqa: BLE001
        return None
    slug = str((meta or {}).get("builtin") or "").strip() if isinstance(meta, dict) else ""
    return slug or None


# ---------------------------------------------------------------- 播种（幂等）
def _content_digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


async def ensure_builtin_skills_seeded(*, force: bool = False) -> None:
    """把 `builtin/<slug>/` 注册进 agent_skill 表（幂等、可升级）。

    - 记录不存在 → 插入 system 技能 + 版本（version_name = skill.json 的 version）。
    - 记录存在但当前版本的 version_name 或 SKILL.md 摘要与磁盘不同 → 追加一个新版本并切换
      current_version_id（不改写旧版本行，保留历史）。
    - 名称/描述/分类跟随 skill.json 更新（这些是包的事实，不是管理员的编辑对象）。
    进程内只在首次成功后跳过；DB 抖动导致失败则下次再试。
    """
    global _builtin_seeded
    if _builtin_seeded and not force:
        return
    slugs = builtin_skill_slugs()
    if not slugs:
        _builtin_seeded = True
        return
    async with async_session() as session:
        for slug in slugs:
            try:
                manifest = load_builtin_manifest(slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning("内置技能 %s 读取失败，跳过播种: %s", slug, exc)
                continue
            digest = _content_digest(manifest["content"])
            skill = (await session.execute(select(AgentSkill).where(AgentSkill.id == slug))).scalar_one_or_none()
            current: Optional[AgentSkillVersion] = None
            if skill is not None and skill.current_version_id:
                current = (
                    await session.execute(
                        select(AgentSkillVersion).where(AgentSkillVersion.id == skill.current_version_id)
                    )
                ).scalar_one_or_none()
            current_meta: dict = {}
            if current is not None and current.import_source_json:
                try:
                    current_meta = json.loads(current.import_source_json) or {}
                except Exception:  # noqa: BLE001
                    current_meta = {}
            up_to_date = (
                current is not None
                and current.version_name == manifest["version"]
                and str(current_meta.get("contentDigest") or "") == digest
            )
            if skill is None:
                skill = AgentSkill(
                    id=slug,
                    source="system",
                    name=manifest["name"],
                    description=manifest["description"][:1024],
                    category_json=json.dumps(manifest["category"], ensure_ascii=False),
                    creation_status="ready",
                    owner_user_id="system",
                )
                session.add(skill)
            else:
                skill.source = "system"
                skill.name = manifest["name"]
                skill.description = manifest["description"][:1024]
                skill.category_json = json.dumps(manifest["category"], ensure_ascii=False)
                skill.creation_status = "ready"
                skill.creation_error = None
            if not up_to_date:
                version = AgentSkillVersion(
                    id=uuid.uuid4().hex,
                    skill_id=slug,
                    version_name=manifest["version"],
                    content=manifest["content"],
                    package_b64=None,  # 文件树从磁盘读，见模块说明
                    import_source_json=json.dumps(
                        {"builtin": slug, "contentDigest": digest}, ensure_ascii=False
                    ),
                )
                session.add(version)
                skill.current_version_id = version.id
                logger.info("内置技能 %s 已注册/升级到目录：version=%s", slug, manifest["version"])
        await session.commit()
    _builtin_seeded = True


# ---------------------------------------------------------------- 目录（库表）
def serialize_skill(skill: AgentSkill, version: Optional[AgentSkillVersion] = None) -> dict:
    """技能记录的唯一序列化：`/agent-api/skill/*` 路由与进程内目录共用，字段不会分叉。

    `enabled`：说明书生成完成且有当前版本才算可用（creating/failed 的个人技能不可用）。
    `skillId`/`recordId` 与 `id` 同值——旧 auth-api 目录里「技能 id」与「记录 id」是两个字段，
    下游按 `skillId or id` 取技能 id、按 `id` 取记录 id，这里三者相同即可兼容。
    """
    try:
        category = json.loads(skill.category_json or "[]")
    except json.JSONDecodeError:
        category = []
    enabled = 1 if (skill.creation_status == "ready" and skill.current_version_id) else 0
    version_name = str(version.version_name or "").strip() if version is not None else ""
    return {
        "id": skill.id,
        "skillId": skill.id,
        "recordId": skill.id,
        "parentId": None,
        "type": "skill",
        "source": skill.source,
        "name": skill.name,
        "description": skill.description or "",
        "category": category if isinstance(category, list) else [],
        "currentVersionId": skill.current_version_id,
        "creationStatus": skill.creation_status,
        "creationError": skill.creation_error,
        "enabled": enabled,
        "version": version_name or None,
        "versionName": version_name or None,
        "createTime": skill.create_time.isoformat(sep=" ", timespec="seconds") if skill.create_time else None,
        "updateTime": skill.update_time.isoformat(sep=" ", timespec="seconds") if skill.update_time else None,
    }


async def _user_id_from_token(token: str) -> Optional[str]:
    from app.core.auth import user_from_token

    try:
        user = await user_from_token(str(token or ""))
    except Exception:  # noqa: BLE001
        return None
    return str(user.user_id) if user is not None and user.user_id else None


async def _visible_skills(session, user_id: Optional[str], skill_ids: Optional[list[str]] = None):
    query = select(AgentSkill)
    if user_id:
        query = query.where((AgentSkill.source == "system") | (AgentSkill.owner_user_id == user_id))
    else:
        query = query.where(AgentSkill.source == "system")
    if skill_ids is not None:
        query = query.where(AgentSkill.id.in_(skill_ids))
    return (await session.execute(query)).scalars().all()


async def _current_versions(session, skills) -> dict[str, AgentSkillVersion]:
    version_ids = [s.current_version_id for s in skills if s.current_version_id]
    if not version_ids:
        return {}
    rows = (
        await session.execute(select(AgentSkillVersion).where(AgentSkillVersion.id.in_(version_ids)))
    ).scalars().all()
    return {row.id: row for row in rows}


async def list_catalog_records(token: str) -> list[dict]:
    """当前用户可见且 enabled 的技能记录（系统技能置前）。形状见 serialize_skill。"""
    try:
        await ensure_builtin_skills_seeded()
    except Exception:  # noqa: BLE001 — 播种失败不该让目录整体消失，库里已有的照常列出
        logger.warning("内置技能播种失败，本次只列库里已有的技能", exc_info=True)
    user_id = await _user_id_from_token(token)
    async with async_session() as session:
        skills = await _visible_skills(session, user_id)
        versions = await _current_versions(session, skills)
    records = [
        serialize_skill(skill, versions.get(skill.current_version_id or ""))
        for skill in skills
    ]
    records = [r for r in records if r["enabled"] == 1]
    records.sort(key=lambda r: (0 if r["source"] == "system" else 1, r["name"]))
    return records


async def load_skill_readme(record_id: str, token: str) -> str:
    """SKILL.md 正文（当前版本 content）。不可见/不存在返回 ""。"""
    rid = str(record_id or "").strip()
    if not rid:
        return ""
    user_id = await _user_id_from_token(token)
    async with async_session() as session:
        skills = await _visible_skills(session, user_id, [rid])
        if not skills:
            return ""
        versions = await _current_versions(session, skills)
    version = versions.get(skills[0].current_version_id or "")
    return str(version.content or "").strip() if version is not None else ""


async def load_skill_package_source(record_id: str, token: str) -> Optional[dict]:
    """技能包文件树，形状与 skill_package_bridge 的取包结果一致：
    {files, entrypoint, error, declared_scripts, unmounted, channel}。

    返回 None = 目录里没有这条记录（不存在/无权限），由调用方按「取包失败」占位处理。
    内置包从磁盘读；导入包解 package_b64 的 zip；内容型技能物化单个 SKILL.md。
    """
    rid = str(record_id or "").strip()
    if not rid:
        return None
    user_id = await _user_id_from_token(token)
    async with async_session() as session:
        skills = await _visible_skills(session, user_id, [rid])
        if not skills:
            return None
        versions = await _current_versions(session, skills)
    skill = skills[0]
    version = versions.get(skill.current_version_id or "")
    if version is None:
        return {
            "files": {}, "entrypoint": None, "error": "技能没有可用版本（说明书尚未生成或生成失败）",
            "declared_scripts": None, "unmounted": {}, "channel": "local",
        }
    slug = builtin_slug_of_version(version)
    if slug:
        try:
            files, entrypoint = builtin_package_files(slug)
        except Exception as exc:  # noqa: BLE001
            return {
                "files": {}, "entrypoint": None, "error": f"内置技能包读取失败（{exc}）",
                "declared_scripts": None, "unmounted": {}, "channel": "builtin",
            }
        return {
            "files": files,
            "entrypoint": entrypoint,
            "error": None if files else "内置技能包是空的",
            "declared_scripts": _rels_have_scripts(files.keys(), entrypoint),
            "unmounted": {"binary": [], "over_file_limit": [], "over_byte_budget": [], "fetch_failed": []},
            "channel": "builtin",
        }
    if version.package_b64:
        import base64

        from app.services.skills.skill_package_bridge import _extract_zip_package

        try:
            raw = base64.b64decode(version.package_b64)
        except Exception as exc:  # noqa: BLE001
            return {
                "files": {}, "entrypoint": None, "error": f"技能包 base64 解码失败（{exc}）",
                "declared_scripts": None, "unmounted": {}, "channel": "zip",
            }
        return _extract_zip_package(raw)
    content = str(version.content or "").encode("utf-8")
    files = {"SKILL.md": content} if content else {}
    return {
        "files": files,
        "entrypoint": None,
        "error": None if files else "技能说明书为空",
        "declared_scripts": False,
        "unmounted": {"binary": [], "over_file_limit": [], "over_byte_budget": [], "fetch_failed": []},
        "channel": "local",
    }


_SCRIPT_SUFFIXES = (".sh", ".py", ".js", ".ts")


def _rels_have_scripts(rels: Any, entrypoint: Optional[str] = None) -> bool:
    if entrypoint:
        return True
    return any(
        str(rel).lower().endswith(_SCRIPT_SUFFIXES) or "entrypoint" in str(rel).lower()
        for rel in (rels or [])
    )
