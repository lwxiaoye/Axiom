"""Skill 目录（agent-api 自持，见 skill_catalog）→ 主对话沙箱的取包桥接（ADR-047 §6.6）。

技能包文件树由 `skill_catalog.load_skill_package_source` 在进程内给出（内置包读磁盘、导入包解
zip、内容型技能物化单个 SKILL.md），这里把它整理成 `{相对路径: bytes}`，供
`sandbox_executor.execute_in_sandbox(skill_packages=...)` 挂进 `/workspace/skills/<slug>/`。

2026-09-18 之前这里逐文件回源 auth-api `/ai/skill/files|file|package`——Java 下线后那些接口
只剩 404，取包永远失败。改为进程内后没有「文本通道/二进制通道」之分：字节从磁盘或 zip 直接来。

约束：
- 上限对齐工作台沙箱（200 文件 / 20MB / 每 skill），防超大包撑爆沙箱。
- ACL：只对已由 `_fetch_trusted_skills` 校验过 enabled 的 skill 取包（调用方传可信 record_id），
  取包本身再按 token 做一次可见性校验。
- **取包失败不静默**：失败的技能以 `unavailable=True` 占位记录返回（见 `_unavailable_pkg`），
  因为系统提示词与时间线事件此时已经声称"脚本已挂在沙箱里"，丢弃 = 平台替模型撒谎。
- `entrypoint.sh` 主对话侧**不执行**，只当作"含脚本"的信号（见 `_has_scripts`）。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_FILES = 200
_MAX_BYTES = 20 * 1024 * 1024
_SCRIPT_SUFFIXES = (".sh", ".py", ".js", ".ts")
_MAX_SLUG_LEN = 64
_PPT_ENGINE_WASM_REL = "scripts/local-export/pptd_wasm_bg.wasm"
_PPT_EXPORT_OVERLAYS = (
    "skill.json",
    "scripts/local-export/normalize-theme-styles.mjs",
    "scripts/local-export/export-pptd.mjs",
)
_PACKAGE_CACHE_TTL_SECONDS = 600.0
_PACKAGE_CACHE_MAX = 24
_package_fetch_cache: Dict[str, tuple[float, dict]] = {}
_package_fetch_inflight: Dict[str, asyncio.Task] = {}
_RETIRED_SKILL_TOKENS = (
    "run" + "_code",
    "create" + "_file",
    "update" + "_file",
    "/workspace/" + "inputs",
    "/workspace/" + "outputs",
)

_PPT_RUNTIME_STEP0 = """### step0. Use the platform runtime
AXIOM has already validated and mounted the presentation runtime. Do not probe Node.js, npm,
npx, Python, browser, dependency versions, PATH, sandbox, or package status before authoring.
Use the injected `沙箱精确目录` as `SKILL_DIR` and author only under `/workspace/tmp/ppt-project`.
Never `ls`/`find` `/workspace/skills` to guess the slug. Use the mounted `scripts/run_export.py`
wrapper directly, then deliver with `publish_ppt_artifact`. Runtime commands, paths, stdout/stderr,
exit codes, intermediate files, and dependency details are internal observations; never copy them
into public progress or the final answer. If export actually fails, describe only the user-visible
impact and continue with every deliverable that can still be completed.

The sandbox does not provide an `apply_patch` command. Create and update `.pptd`, `.page`, and
supporting project files with `write_file` / `edit_file` (or `bash`). PPTD is the
required authoring backend: never bypass this workflow with python-pptx or another presentation
generator. Do not call `use_skill` again if this skill is already injected, and do not re-read
SKILL.md from disk. User-uploaded photos from the session workspace appear under
`/workspace/tmp/ppt-project/media/`. The skill directory is a toolkit, not the project.

"""
_PPT_STEP0_RE = re.compile(
    r"(?ims)^###\s*step\s*0[^\n]*\n.*?(?=^###\s*step\s*1\b)",
)


def is_first_party_ppt_studio(name: object) -> bool:
    normalized = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    return normalized == "ppt-studio"


def _ppt_engine_wasm_bytes() -> Optional[bytes]:
    """Host copy of the PPTD WASM engine（内置包目录里不带 wasm，历史导入包也没有）."""
    here = Path(__file__).resolve()
    candidates = (
        here.parents[3] / "deploy" / "sandbox" / "pptd_wasm_bg.wasm",
        here.parent / "builtin" / "ppt-studio" / _PPT_ENGINE_WASM_REL,
    )
    for path in candidates:
        if path.is_file():
            return path.read_bytes()
    return None


def _drop_injected_ppt_engine_from_unmounted(unmounted: object) -> dict:
    copied = {
        str(key): list(items or [])
        for key, items in dict(unmounted or {}).items()
    }
    injected = {_PPT_ENGINE_WASM_REL, *_PPT_EXPORT_OVERLAYS}
    for key in ("binary", "fetch_failed"):
        copied[key] = [
            item for item in copied.get(key) or []
            if str(item).replace("\\", "/") not in injected
        ]
    return copied


def adapt_first_party_skill_package(name: str, files: dict[str, bytes]) -> dict[str, bytes]:
    """Adapt legacy first-party docs and fill runtime shims missing from marketplace packages.

    Documentation can be translated because it is model guidance. Executable files stay
    byte-for-byte except ppt-studio local-export overlays that repair YAML the WASM writer
    otherwise ignores. Older records also contain ``export_pptx.py`` but not the wrapper.
    """
    adapted = dict(files or {})
    if not is_first_party_ppt_studio(name):
        return adapted

    for rel, blob in list(adapted.items()):
        if not str(rel).lower().endswith((".md", ".txt")):
            continue
        text = bytes(blob).decode("utf-8", errors="ignore") if isinstance(blob, bytes) else str(blob)
        rewritten = normalize_skill_instructions(text, skill_name=name)
        if rewritten != text:
            adapted[rel] = rewritten.encode("utf-8")

    builtin_root = Path(__file__).resolve().parent / "builtin" / "ppt-studio"
    export_rel = "scripts/export_pptx.py"
    wrapper_rel = "scripts/run_export.py"
    if export_rel in adapted:
        wrapper = builtin_root / wrapper_rel
        if wrapper.is_file():
            adapted[wrapper_rel] = wrapper.read_bytes()
    for rel in _PPT_EXPORT_OVERLAYS:
        src = builtin_root / rel
        if src.is_file():
            adapted[rel] = src.read_bytes()
    if _PPT_ENGINE_WASM_REL not in adapted:
        engine = _ppt_engine_wasm_bytes()
        if engine:
            adapted[_PPT_ENGINE_WASM_REL] = engine
    return adapted


def validate_harness_skill_package(files: dict[str, bytes]) -> tuple[str, ...]:
    """Reject packages written for retired tool/workspace contracts.

    Runtime prompt translation is intentionally forbidden: accepting an incompatible package and
    hoping the model rewrites its instructions creates a second, implicit tool protocol.
    """
    violations: set[str] = set()
    for rel, blob in (files or {}).items():
        low_rel = str(rel or "").lower()
        if not low_rel.endswith((".md", ".txt", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml")):
            continue
        text = bytes(blob).decode("utf-8", errors="ignore") if isinstance(blob, bytes) else str(blob)
        for token in _RETIRED_SKILL_TOKENS:
            if token in text:
                violations.add(token)
    return tuple(sorted(violations))

_ZIP_BOMB_TOTAL = 200 * 1024 * 1024
_ZIP_BOMB_ENTRY = 50 * 1024 * 1024
_ZIP_BOMB_ENTRIES = 5_000


def package_integrity(
    files: Optional[dict],
    unmounted: Optional[dict],
    *,
    channel: str,
    error: Optional[str] = None,
) -> dict:
    """Stable mount-completeness fact for use_skill / bash observations."""
    mounted = dict(files or {})
    gaps = {
        str(key): list(items or [])
        for key, items in dict(unmounted or {}).items()
    }
    has_gap = any(gaps.get(key) for key in ("binary", "over_file_limit", "over_byte_budget", "fetch_failed"))
    if error and not mounted:
        status = "unavailable"
    elif has_gap:
        status = "incomplete"
    else:
        status = "complete"
    digest = hashlib.sha256()
    for name in sorted(mounted):
        blob = mounted[name]
        data = blob if isinstance(blob, (bytes, bytearray)) else str(blob or "").encode("utf-8")
        digest.update(str(name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes(data))
        digest.update(b"\0")
    return {
        "status": status,
        "channel": channel,
        "digest": digest.hexdigest() if mounted else None,
        "file_count": len(mounted),
        "byte_count": sum(
            len(blob) if isinstance(blob, (bytes, bytearray)) else len(str(blob or "").encode("utf-8"))
            for blob in mounted.values()
        ),
        "unmounted": gaps,
    }


def _strip_zip_prefix(names: list[str]) -> str:
    files = [name for name in names if name and not str(name).endswith("/")]
    tops = {name.split("/", 1)[0] for name in files if "/" in name}
    if len(tops) == 1 and files and all("/" in name for name in files):
        return tops.pop() + "/"
    return ""


def _extract_zip_package(raw: bytes) -> dict:
    """Skill ZIP（导入包的 package_b64 解码后）→ 与 skill_catalog 取包结果同形的字典。"""
    from app.services.platform import zip_guard

    files: dict[str, bytes] = {}
    entrypoint: Optional[str] = None
    over_file_limit: List[str] = []
    over_byte_budget: List[str] = []
    fetch_failed: List[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        zip_guard.ensure_zip_within_limits(
            archive,
            label="技能包",
            max_total_bytes=_ZIP_BOMB_TOTAL,
            max_entry_bytes=_ZIP_BOMB_ENTRY,
            max_entries=_ZIP_BOMB_ENTRIES,
        )
    except zip_guard.ZipBombError as exc:
        return {
            "files": {},
            "entrypoint": None,
            "error": exc.message,
            "declared_scripts": None,
            "unmounted": {},
            "channel": "zip",
        }
    except (zipfile.BadZipFile, OSError) as exc:
        return {
            "files": {},
            "entrypoint": None,
            "error": f"技能 ZIP 无法解压（{exc}）",
            "declared_scripts": None,
            "unmounted": {},
            "channel": "zip",
        }

    names = [name for name in archive.namelist() if name and not name.endswith("/")]
    prefix = _strip_zip_prefix(names)
    rels: List[str] = []
    rel_to_name: dict[str, str] = {}
    for name in names:
        rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
        rel = rel.lstrip("/")
        if not rel or ".." in rel.split("/"):
            continue
        rels.append(rel)
        rel_to_name[rel] = name
    budget = zip_guard.ZipReadBudget(
        label="技能包",
        max_total_bytes=_ZIP_BOMB_TOTAL,
        max_entry_bytes=_ZIP_BOMB_ENTRY,
    )
    total = 0
    for rel in rels:
        if len(files) >= _MAX_FILES:
            over_file_limit.append(rel)
            continue
        try:
            data = budget.read(archive, rel_to_name[rel])
        except zip_guard.ZipBombError as exc:
            return {
                "files": {},
                "entrypoint": None,
                "error": exc.message,
                "declared_scripts": _rels_have_scripts(rels),
                "unmounted": {},
                "channel": "zip",
            }
        except Exception:  # noqa: BLE001
            fetch_failed.append(rel)
            continue
        if total + len(data) > _MAX_BYTES:
            over_byte_budget.append(rel)
            continue
        total += len(data)
        files[rel] = data
        low = rel.lower()
        if low == "entrypoint.sh" or low.endswith("/entrypoint.sh"):
            entrypoint = rel
    error = None
    if not files:
        error = (
            f"ZIP 有 {len(rels)} 个条目，但没有一个文件解出成功"
            if rels else "技能 ZIP 是空的"
        )
    return {
        "files": files,
        "entrypoint": entrypoint,
        "error": error,
        "declared_scripts": _rels_have_scripts(rels),
        "unmounted": {
            "binary": [],
            "over_file_limit": over_file_limit,
            "over_byte_budget": over_byte_budget,
            "fetch_failed": fetch_failed,
        },
        "channel": "zip",
    }


def _attach_integrity(fetched: dict) -> dict:
    copied = dict(fetched or {})
    copied["integrity"] = package_integrity(
        copied.get("files"),
        copied.get("unmounted"),
        channel=str(copied.get("channel") or "local"),
        error=copied.get("error"),
    )
    return copied


def _short_digest(value: str) -> str:
    return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:6]


def _slugify(name: str, fallback: str) -> str:
    """技能名 → 文件系统安全的目录名（模型在 /workspace/skills/<slug>/ 下找脚本）。

    非字母数字一律折成 `_` 会制造**静默碰撞**：《a/b》与《a_b》都落成 `a_b`，同一沙箱目录里
    后写的整包覆盖先写的；更隐蔽的是 sandbox_executor 的 `session.written_skills` 也按 slug 去重，
    复用沙箱时第二个包**连写都不写**。所以只要名字被改写过（有字符被映射/丢弃，或超长截断），
    就缀上 record_id 的短摘要：没被改写的名字保持原样（模型看到的路径仍然好读），被改写的
    名字各自唯一。摘要取 record_id 而非原名——同一技能每一轮都要拿到同一个目录。
    """
    raw = str(name or "")
    keep = []
    for ch in raw:
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        elif ch in (" ", ".", "/", "\\", ":"):
            keep.append("_")
    slug = "".join(keep).strip("_")
    if not slug:
        safe_fallback = "".join(
            ch for ch in str(fallback or "") if ch.isalnum() or ch in ("-", "_")
        )
        return safe_fallback[:_MAX_SLUG_LEN] or f"skill_{_short_digest(fallback)}"
    if slug != raw or len(slug) > _MAX_SLUG_LEN:
        return f"{slug[:_MAX_SLUG_LEN - 7]}_{_short_digest(fallback)}"
    return slug


def package_slug(name: str, record_id: str) -> str:
    """返回主对话真实挂载目录名。

    提示词和取包链必须调用同一个函数，否则模型只能用 find/ls 猜目录，
    而工具守卫又会拦这类探测。
    """
    return _slugify(name, record_id)


async def _fetch_one_skill(record_id: str, token: str) -> dict:
    """进程内取单个 skill 的文件树字节。返回 {files:{rel:bytes}, entrypoint, error, ...}。

    目录里没有这条记录（不存在/已停用/无权限）时同样返回**带 error 的结果**而不是抛异常：
    调用方据此生成占位记录，回执里如实说"本轮没挂上"。declared_scripts=None 表示连文件
    树都不知道——与「确定不带脚本」必须分开，调用方按保守（可能带脚本）处理。
    """
    from app.services.skills import skill_catalog

    try:
        source = await skill_catalog.load_skill_package_source(record_id, token)
    except Exception as e:  # noqa: BLE001
        logger.warning("skill 包读取失败 %s: %s", record_id, e)
        return _attach_integrity({
            "files": {}, "entrypoint": None, "error": f"技能包读取失败（{e}）",
            "declared_scripts": None, "unmounted": {}, "channel": "local",
        })
    if source is None:
        return _attach_integrity({
            "files": {}, "entrypoint": None,
            "error": "技能目录里没有这条记录（不存在 / 已停用 / 无权限）",
            "declared_scripts": None, "unmounted": {}, "channel": "local",
        })
    return _attach_integrity(source)


def _copy_fetch_result(value: dict) -> dict:
    copied = dict(value or {})
    copied["files"] = dict(copied.get("files") or {})
    copied["unmounted"] = {
        str(key): list(items or [])
        for key, items in dict(copied.get("unmounted") or {}).items()
    }
    integrity = copied.get("integrity")
    if isinstance(integrity, dict):
        copied["integrity"] = dict(integrity)
        nested = integrity.get("unmounted")
        if isinstance(nested, dict):
            copied["integrity"]["unmounted"] = {
                str(key): list(items or [])
                for key, items in nested.items()
            }
    return copied


async def _fetch_one_skill_cached(record_id: str, token: str) -> dict:
    """Deduplicate the package materialization (zip decode / disk walk) within one worker.

    The ACL check still happens before this function.  The cache key includes a one-way digest of
    the access token, so a package fetched for one user is never reused for another user's ACL.
    Failures are deliberately not cached; transient DB errors must remain retryable.
    """
    token_digest = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:16]
    key = f"{record_id}\0{token_digest}"
    now = time.monotonic()
    cached = _package_fetch_cache.get(key)
    if cached and now - cached[0] < _PACKAGE_CACHE_TTL_SECONDS:
        return _copy_fetch_result(cached[1])

    task = _package_fetch_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_fetch_one_skill(record_id, token))
        _package_fetch_inflight[key] = task
    try:
        fetched = await task
    finally:
        if _package_fetch_inflight.get(key) is task:
            _package_fetch_inflight.pop(key, None)

    fetch_failed = list((fetched.get("unmounted") or {}).get("fetch_failed") or [])
    if fetched.get("files") and not fetch_failed:
        expired = [
            cache_key for cache_key, (cached_at, _value) in _package_fetch_cache.items()
            if now - cached_at >= _PACKAGE_CACHE_TTL_SECONDS
        ]
        for cache_key in expired:
            _package_fetch_cache.pop(cache_key, None)
        if len(_package_fetch_cache) >= _PACKAGE_CACHE_MAX:
            oldest = min(_package_fetch_cache, key=lambda cache_key: _package_fetch_cache[cache_key][0])
            _package_fetch_cache.pop(oldest, None)
        _package_fetch_cache[key] = (time.monotonic(), _copy_fetch_result(fetched))
    return _copy_fetch_result(fetched)


def _rels_have_scripts(rels: Any) -> bool:
    """文件树里**声明**了脚本没有（不看字节是否取回成功）。"""
    return any(
        str(rel).lower().endswith(_SCRIPT_SUFFIXES) or "entrypoint" in str(rel).lower()
        for rel in (rels or [])
    )


def _has_scripts(files: dict, entrypoint: Optional[str]) -> bool:
    """本包**实际挂进沙箱**的文件里有没有可执行脚本（ADR-043 的执行形态判据）。

    主对话侧的消费者：取包失败时据此决定回执的措辞强度——含脚本技能不可静默降级
    （工作流侧同一条约束是直接 raise，见 agent_executor._prepare_skill_sandbox）。
    ⚠️ `entrypoint`（entrypoint.sh）在主对话侧**只作为"有脚本"的信号，不会被执行**：
    执行它需要 skill_runtime.deploy_skills 那条部署链，主对话走的是 sandbox_executor 的
    文件注入，没有 setup 阶段（沙箱又断网，装依赖型 entrypoint 本来也跑不通）。
    技能若真依赖 setup，应把步骤写进 SKILL.md 由模型用 bash 显式执行。
    """
    if entrypoint:
        return True
    return _rels_have_scripts(files)


def is_unavailable(pkg: Any) -> bool:
    """这条记录是「取包失败的占位」而不是真包——挂载与匹配都必须跳过它。"""
    return bool(isinstance(pkg, dict) and pkg.get("unavailable"))


def _unavailable_pkg(skill: dict, record_id: str, name: str, *,
                     reason: str, declared_scripts: Optional[bool]) -> dict:
    """取包失败的占位记录（files 为空，`unavailable=True`）。

    为什么不是直接丢弃：丢弃后调用链上**没有任何一处**知道这个技能没挂上，而系统提示词
    （turn_context_builder）与时间线的 use_skill 事件都已经告诉模型"脚本已挂在
    /workspace/skills/ 下"。模型于是照 SKILL.md 去跑一个不存在的脚本，或者干脆假装跑过了。
    占位记录让 bash 的回执能如实说出"这个技能本轮不可用"。
    """
    return {
        "skillId": str(skill.get("id") or record_id),
        "recordId": record_id,
        "name": name,
        "slug": package_slug(name, record_id),
        "files": {},
        "entrypoint": None,
        # 未知（连文件树都没拿到）按**保守**算作含脚本：宁可多说一句"本轮不可用"，
        # 也不能让一个含脚本技能悄悄消失。
        "hasScripts": declared_scripts is not False,
        "scriptsKnown": declared_scripts is not None,
        "unmounted": {},
        "unavailable": True,
        "reason": reason,
        "channel": "local",
        "integrity": package_integrity({}, {}, channel="local", error=reason),
    }


async def fetch_skill_packages(skills: List[dict], token: str) -> List[dict]:
    """skills: [{record_id, name}]（record_id = agent_skill.id，与目录记录的 `id` 同源）。

    返回 [{skillId, name, slug, files:{相对路径:bytes}, hasScripts, entrypoint}]，仅含成功取到的文件。

    取包失败**不再静默丢弃**：失败的技能以 `unavailable=True` 的占位记录回来（`files` 为空），
    由调用方在回执里如实告诉模型"这个技能本轮没挂上、别去跑它的脚本"。整体失败（DB 不可达等）
    同样返回全量占位记录而不是空列表——空列表与"没选技能"无法区分，正是静默的来源。
    """
    wanted = [s for s in (skills or []) if str(s.get("record_id") or "").strip()]
    if not wanted:
        return []
    packages: List[dict] = []
    try:
        for s in wanted:
            record_id = str(s["record_id"]).strip()
            name = str(s.get("name") or record_id)
            got = await _fetch_one_skill_cached(record_id, token)
            files = got["files"]
            if not files:
                logger.warning("skill 包 %s（%s）未取到任何文件：%s",
                               record_id, name, got.get("error"))
                packages.append(_unavailable_pkg(
                    s, record_id, name,
                    reason=str(got.get("error") or "取包失败"),
                    declared_scripts=got.get("declared_scripts"),
                ))
                continue
            files = adapt_first_party_skill_package(name, files)
            unmounted = _drop_injected_ppt_engine_from_unmounted(got.get("unmounted") or {})
            violations = validate_harness_skill_package(files)
            if violations:
                reason = (
                    "Skill 使用已退休的工具或工作区契约："
                    + "、".join(violations)
                    + "。请迁移为 bash + /workspace/files 后重新导入。"
                )
                logger.warning("skill 包 %s（%s）不兼容 Agent Harness: %s", record_id, name, violations)
                packages.append(_unavailable_pkg(
                    s, record_id, name,
                    reason=reason,
                    declared_scripts=got.get("declared_scripts"),
                ))
                continue
            channel = str(got.get("channel") or "local")
            packages.append({
                "skillId": str(s.get("id") or record_id),
                "recordId": record_id,
                "name": name,
                "slug": package_slug(name, record_id),
                "files": files,
                "entrypoint": got["entrypoint"],
                "hasScripts": _has_scripts(files, got["entrypoint"]),
                "scriptsKnown": True,
                "unmounted": unmounted,
                "channel": channel,
                "integrity": got.get("integrity") or package_integrity(
                    files, unmounted, channel=channel, error=got.get("error"),
                ),
            })
    except Exception as e:  # noqa: BLE001
        logger.warning("skill 取包整体失败: %s", e)
        got_ids = {str(p.get("recordId") or "") for p in packages}
        for s in wanted:
            record_id = str(s["record_id"]).strip()
            if record_id in got_ids:
                continue
            packages.append(_unavailable_pkg(
                s, record_id, str(s.get("name") or record_id),
                reason=f"取包整体失败（{e}）", declared_scripts=None,
            ))
    return packages


def normalize_skill_instructions(value: object, *, skill_name: object = "") -> str:
    """Adapt only the authoritative first-party ``ppt-studio`` documentation."""
    text = str(value or "")
    if not is_first_party_ppt_studio(skill_name):
        return text
    normalized = (
        text.replace("execute_in_sandbox", "bash")
        .replace("run" + "_code", "bash")
        .replace("create" + "_file", "bash")
        .replace("update" + "_file", "bash")
        .replace("/workspace/inputs/", "/workspace/files/")
        .replace("/workspace/outputs/", "/workspace/files/")
    )
    if "open-kimi-ppt" not in normalized or "PPTD" not in normalized:
        return normalized

    normalized, step0_count = _PPT_STEP0_RE.subn(_PPT_RUNTIME_STEP0, normalized, count=1)
    if step0_count == 0:
        normalized = re.sub(
            r"(?im)^(##\s+PPT production workflow\s*)$",
            lambda match: f"{match.group(1)}\n\n{_PPT_RUNTIME_STEP0.rstrip()}",
            normalized,
            count=1,
        )

    normalized = re.sub(
        r"(?im)^\s*\d+\.\s*After completing and delivering any presentation,"
        r"[^\n]*(?:\n|$)",
        "",
        normalized,
    )
    normalized = normalized.replace(
        "Do not export the PPTX until the visual review passes.",
        "Visual QA is optional and must not delay `run_export.py` or `publish_ppt_artifact`.",
    )
    normalized = normalized.replace(
        "suggest a recommended page count and confirm with the user",
        "pick 6-10 pages and continue without waiting for confirmation",
    )
    normalized = re.sub(
        r"(?im)^.*npx open-kimi-ppt-skill serve.*(?:\n|$)",
        "",
        normalized,
    )
    return normalized.rstrip() + (
        "\n\n## AXIOM Agent Harness runtime adaptation (overrides conflicting steps)\n"
        "- Canonical loop: (1) skip runtime probes; (2) write PPTD under `/workspace/tmp/ppt-project` "
        "with `write_file`/`edit_file` (or bash); (3) `python3 $SKILL_DIR/scripts/run_export.py $PROJECT --output $PROJECT/deck.pptx "
        "--force`; (4) optional `export_images.py` — skip on failure; (5) `publish_ppt_artifact`. "
        "Do not wait for page-count confirmation, Chromium QA, npm, npx, or `--browser`.\n"
        "- The platform has already validated and mounted the presentation runtime. Do not probe "
        "or report Node.js, npm, npx, Python, browser, dependency versions, PATH, sandbox, or "
        "package status before authoring. Use the mounted `scripts/run_export.py` wrapper directly.\n"
        "- stdout, stderr, commands, exit codes, runtime paths, intermediate files, and package "
        "details are internal observations. Never copy them into public progress or the final answer; "
        "describe only the user-visible impact of a real blocker.\n"
        "- Do not append local editor serve commands, localhost URLs, or manual editor setup "
        "reminders after delivery unless the user explicitly asks how to open the PPTD project manually.\n"
        "- Never put `http://` or `https://` image sources in a `.page` file. Use `search_web`, "
        "stage each chosen result with `fetch_ppt_asset`, and reference the staged file as "
        "`media/<filename>` so offline export embeds the real image.\n"
        "- The sandbox does not provide an `apply_patch` command. Create or update PPTD project "
        "files with `write_file`/`edit_file` (or `bash`). Do not bypass PPTD "
        "with python-pptx or another presentation backend. User photos from the workspace "
        "drawer or chat input are in `$PROJECT/media/`; do not treat `/workspace/skills/` as the project.\n"
        "- Text `style: \"$title\"` belongs inside `content`, not on the element. Page "
        "`background` must be `{type: solid, color: \"#...\"}`. The exporter repairs these, "
        "but do not rely on the WASM default white / MiSans fallback.\n"
    )
