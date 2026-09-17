"""Generic Skill runtime preflight: check declared dependencies once per Run.

Install / networked env_prep is a later batch. This module must not create a
sandbox, must not run untrusted entrypoint.sh, and must skip first-party PPTD
(which has its own exact preflight).
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from typing import Any, Optional

from app.services.sandbox.base import ExecuteOptions

_IDENT = re.compile(r"^[A-Za-z0-9._-]+$")
_REQ_NAME = re.compile(r"^[A-Za-z0-9._-]+")
_IMPORT_ALIASES = {
    "beautifulsoup4": "bs4",
    "opencv-python": "cv2",
    "opencv_python": "cv2",
    "pillow": "PIL",
    "python-docx": "docx",
    "python_docx": "docx",
    "pyyaml": "yaml",
    "scikit-learn": "sklearn",
}


@dataclass(frozen=True)
class RuntimeNeed:
    bins: tuple[str, ...]
    python: tuple[str, ...]

    def signature(self) -> str:
        raw = json.dumps({"bins": self.bins, "python": self.python}, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def is_empty(self) -> bool:
        return not self.bins and not self.python


def _clean_names(values: Any) -> tuple[str, ...]:
    out: list[str] = []
    for item in values or []:
        name = str(item or "").strip()
        if not name or not _IDENT.match(name):
            continue
        if name not in out:
            out.append(name)
    return tuple(out)


def parse_requirement_line(line: str) -> Optional[str]:
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("-"):
        return None
    stripped = stripped.split(";", 1)[0].strip()
    if not _REQ_NAME.match(stripped):
        return None
    return stripped


def split_requirement(spec: str) -> tuple[str, str]:
    raw = str(spec or "").strip()
    match = _REQ_NAME.match(raw)
    name = match.group(0) if match else raw
    marker = raw[len(name):].strip()
    if marker.startswith("["):
        closer = marker.find("]")
        if closer >= 0:
            marker = marker[closer + 1:].strip()
    return name, marker


def parse_requirements_txt(blob: bytes) -> tuple[str, ...]:
    specs: list[str] = []
    text = bytes(blob or b"").decode("utf-8", errors="ignore")
    for line in text.splitlines():
        spec = parse_requirement_line(line)
        if spec and spec not in specs:
            specs.append(spec)
    return tuple(specs)


def parse_skill_runtime(files: Optional[dict]) -> Optional[RuntimeNeed]:
    mounted = dict(files or {})
    raw = mounted.get("skill.json") or mounted.get("SKILL.json")
    if raw:
        try:
            data = json.loads(bytes(raw).decode("utf-8", errors="ignore") or "{}")
        except Exception:  # noqa: BLE001
            data = None
        if isinstance(data, dict):
            runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
            bins = _clean_names(runtime.get("bins") or runtime.get("commands"))
            python_specs: list[str] = []
            for item in (
                runtime.get("python")
                or runtime.get("python_packages")
                or runtime.get("pip")
                or []
            ):
                spec = parse_requirement_line(str(item))
                if spec and spec not in python_specs:
                    python_specs.append(spec)
            python = tuple(python_specs)
            if bins or python:
                return RuntimeNeed(bins, python)
    req = mounted.get("requirements.txt")
    if req:
        python = parse_requirements_txt(req)
        if python:
            return RuntimeNeed((), python)
    return None


def collect_runtime_needs(skill_packages: Optional[list]) -> dict[str, RuntimeNeed]:
    from app.services.skills.skill_package_bridge import is_first_party_ppt_studio, is_unavailable

    out: dict[str, RuntimeNeed] = {}
    for pkg in skill_packages or []:
        if not isinstance(pkg, dict) or is_unavailable(pkg):
            continue
        if is_first_party_ppt_studio(pkg.get("name") or pkg.get("skillId")):
            continue
        need = parse_skill_runtime(pkg.get("files") or {})
        if need is None or need.is_empty():
            continue
        slug = str(pkg.get("slug") or pkg.get("skillId") or "skill").strip() or "skill"
        out[slug] = need
    return out


def _import_name(package: str) -> str:
    key = split_requirement(package)[0]
    aliased = _IMPORT_ALIASES.get(key.lower())
    if aliased:
        return aliased
    return key.replace("-", "_")


def requirement_satisfied(spec: str, installed: Optional[str]) -> bool:
    """Host-side PEP 440 check. Same constraint string used for probe and pip."""
    from packaging.requirements import Requirement
    from packaging.version import InvalidVersion, Version

    raw = str(spec or "").strip()
    if not raw:
        return True
    if installed is None or str(installed).strip() == "":
        return False
    try:
        req = Requirement(raw)
    except Exception:  # noqa: BLE001
        name, marker = split_requirement(raw)
        if not marker:
            return True
        try:
            req = Requirement(f"{name}{marker}")
        except Exception:  # noqa: BLE001
            return False
    if not req.specifier:
        return True
    try:
        return Version(str(installed)) in req.specifier
    except InvalidVersion:
        return False


async def _pip_install(sandbox, packages: list[str]) -> dict:  # noqa: ANN001
    if not packages:
        return {"status": "ready", "missing": [], "message": "runtime ready"}
    quoted = " ".join(shlex.quote(name) for name in packages)
    command = (
        "python3 -m pip install --disable-pip-version-check --no-input --quiet " + quoted
    )
    result = await sandbox.execute(
        command,
        ExecuteOptions(timeout_ms=120_000, phase="env_prep"),
    )
    reason = str(getattr(result, "termination_reason", "") or "")
    if reason in {"network_denied", "network_policy_restore_failed"} or not getattr(result, "ok", False):
        detail = (getattr(result, "stderr", None) or getattr(result, "stdout", None) or reason or "install failed").strip()
        code = reason or "network_denied"
        return {
            "status": "blocked",
            "missing": [f"python:{name}" for name in packages],
            "message": (
                f"运行环境能力缺口：缺少 {' '.join('python:' + n for n in packages)}。"
                f"受控联网安装未成功（{code}）：{detail[:240]}。"
                "本轮不会反复探测或重试。"
            ),
        }
    return {"status": "ready", "missing": [], "message": "runtime ready"}


async def probe_runtime_need(sandbox, need: RuntimeNeed) -> dict:  # noqa: ANN001
    bins = list(need.bins)
    reqs = []
    for orig in need.python:
        dist, _marker = split_requirement(orig)
        reqs.append((orig, _import_name(orig), dist))
    script = (
        "import importlib.metadata as md, importlib.util, json, shutil\n"
        f"bins={bins!r}\n"
        f"reqs={reqs!r}\n"
        "missing_bins=['bin:'+n for n in bins if not shutil.which(n)]\n"
        "installed={}\n"
        "for orig, spec_name, dist in reqs:\n"
        "    if importlib.util.find_spec(spec_name) is None:\n"
        "        installed[dist]=None\n"
        "        continue\n"
        "    try:\n"
        "        installed[dist]=md.version(dist)\n"
        "    except Exception:\n"
        "        installed[dist]=None\n"
        "print(json.dumps({'missing_bins': missing_bins, 'installed': installed}))\n"
    )
    result = await sandbox.execute(
        f"python3 -c {shlex.quote(script)}",
        ExecuteOptions(timeout_ms=15_000),
    )
    raw = (getattr(result, "stdout", None) or "").strip()
    payload = None
    if raw:
        try:
            payload = json.loads(raw.splitlines()[-1])
        except Exception:  # noqa: BLE001
            payload = None
    if not getattr(result, "ok", False) and not payload:
        return {
            "status": "blocked",
            "missing": [],
            "message": "运行环境自检失败，无法确认技能声明的依赖是否可用",
        }
    payload = payload if isinstance(payload, dict) else {}
    missing = [str(item) for item in (payload.get("missing_bins") or []) if item]
    installed = dict(payload.get("installed") or {})
    for orig in need.python:
        dist, _marker = split_requirement(orig)
        version = installed.get(dist)
        if not requirement_satisfied(orig, version):
            missing.append("python:" + orig)
    if missing:
        return {
            "status": "blocked",
            "missing": missing,
            "message": (
                "运行环境能力缺口：缺少 " + " ".join(missing)
                + "。请先由部署环境补齐组件并重建沙箱镜像，或等待平台受控联网安装；"
                "本轮不会反复探测或重试。"
            ),
        }
    return {"status": "ready", "missing": [], "message": "runtime ready"}


async def ensure_generic_runtime(
    sandbox,  # noqa: ANN001
    skill_packages: Optional[list],
    session: Any = None,
) -> Optional[str]:
    """Return a blocked observation, or None when ready / nothing to check."""
    needs = collect_runtime_needs(skill_packages)
    if not needs:
        return None
    cache = dict(getattr(session, "runtime_preflight", None) or {}) if session is not None else {}
    blocked: list[str] = []
    for slug, need in needs.items():
        key = f"skill:{slug}"
        cached = cache.get(key)
        if not isinstance(cached, dict) or cached.get("status") not in {"ready", "blocked"}:
            cached = cache.get(f"need:{need.signature()}")
        if isinstance(cached, dict) and cached.get("status") in {"ready", "blocked"}:
            result = dict(cached)
        else:
            result = await probe_runtime_need(sandbox, need)
            if result.get("status") == "blocked":
                missing = [str(item) for item in (result.get("missing") or [])]
                py_pkgs = [item[7:] for item in missing if item.startswith("python:")]
                if py_pkgs and callable(getattr(sandbox, "set_env_prep_network", None)):
                    result = await _pip_install(sandbox, py_pkgs)
                    if result.get("status") != "blocked":
                        result = await probe_runtime_need(sandbox, need)
        cache[key] = dict(result)
        cache[f"need:{need.signature()}"] = dict(result)
        if result.get("status") == "blocked":
            message = str(result.get("message") or "").strip()
            if message:
                blocked.append(message)
    if session is not None:
        session.runtime_preflight = cache
    if not blocked:
        return None
    unique: list[str] = []
    for message in blocked:
        if message not in unique:
            unique.append(message)
    return " ".join(unique)
