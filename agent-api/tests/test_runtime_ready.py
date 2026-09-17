"""Generic Skill runtime preflight: declared deps only, cached, no PPT merge."""
from __future__ import annotations

import io
import json
import shlex
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.skills.runtime_ready import (
    collect_runtime_needs,
    ensure_generic_runtime,
    parse_skill_runtime,
    probe_runtime_need,
    requirement_satisfied,
)
from app.services.skills.runtime_ready import RuntimeNeed


def test_skill_json_runtime_is_preferred_over_requirements():
    need = parse_skill_runtime({
        "skill.json": json.dumps({
            "name": "report",
            "runtime": {"bins": ["pandoc"], "python": ["docx"]},
        }).encode(),
        "requirements.txt": b"pandas==1.0\n",
    })
    assert need == RuntimeNeed(bins=("pandoc",), python=("docx",))


def test_requirements_txt_is_used_when_skill_json_has_no_runtime():
    need = parse_skill_runtime({"requirements.txt": b"# x\npandas==2.0\npython-docx\n-e git+https://x\n"})
    assert need is not None
    assert "pandas==2.0" in need.python
    assert "python-docx" in need.python
    assert need.bins == ()


def test_no_declared_runtime_means_no_preflight():
    assert parse_skill_runtime({"SKILL.md": b"just docs", "scripts/run.py": b"print(1)"}) is None


def test_first_party_ppt_is_not_collected_for_generic_preflight():
    needs = collect_runtime_needs([
        {
            "name": "ppt-studio",
            "slug": "ppt-studio",
            "files": {"skill.json": json.dumps({"runtime": {"bins": ["node"]}}).encode()},
        },
        {
            "name": "report-writer",
            "slug": "report-writer",
            "files": {"skill.json": json.dumps({"runtime": {"python": ["docx"]}}).encode()},
        },
    ])
    assert "ppt-studio" not in needs
    assert needs["report-writer"].python == ("docx",)


def test_unavailable_or_empty_packages_are_skipped():
    assert collect_runtime_needs([
        {"unavailable": True, "name": "broken", "files": {}},
        {"name": "empty", "slug": "empty", "files": {}},
        None,
    ]) == {}


class ProbeSandbox:
    def __init__(self, missing=(), versions=None):
        self.missing = set(missing)
        self.versions = dict(versions or {})
        self.calls = 0

    async def execute(self, command, options):
        self.calls += 1
        argv = shlex.split(command)
        assert argv[:2] == ["python3", "-c"]
        assert options.timeout_ms == 15_000
        stdout = io.StringIO()
        def fake_version(name):
            if name in self.versions:
                return self.versions[name]
            if f"python:{name}" in self.missing:
                raise Exception("not installed")
            return "1.0.0"

        with (
            patch("shutil.which", side_effect=lambda name: None if f"bin:{name}" in self.missing else "/usr/bin/" + name),
            patch(
                "importlib.util.find_spec",
                side_effect=lambda name: None if f"python:{name}" in self.missing else object(),
            ),
            patch("importlib.metadata.version", fake_version),
            patch("sys.stdout", stdout),
        ):
            # The probe prints missing names from shutil/importlib patched above.
            # Execute the generated script in this interpreter.
            exec(argv[2], {})
        return SimpleNamespace(ok=True, stdout=stdout.getvalue(), stderr="")


@pytest.mark.asyncio
async def test_probe_reports_missing_bin_and_python_once():
    result = await probe_runtime_need(
        ProbeSandbox({"bin:pandoc", "python:docx"}),
        RuntimeNeed(bins=("pandoc",), python=("python-docx",)),
    )
    assert result["status"] == "blocked"
    assert "bin:pandoc" in result["missing"]
    assert "python:python-docx" in result["missing"]
    assert "不会反复探测" in result["message"]


@pytest.mark.asyncio
async def test_probe_ready_when_everything_exists():
    result = await probe_runtime_need(
        ProbeSandbox(),
        RuntimeNeed(bins=("pandoc",), python=("docx",)),
    )
    assert result["status"] == "ready"
    assert result["missing"] == []


class _Session:
    def __init__(self):
        self.runtime_preflight = {}


@pytest.mark.asyncio
async def test_ensure_generic_runtime_skips_when_nothing_declared():
    sandbox = ProbeSandbox()
    gap = await ensure_generic_runtime(
        sandbox,
        [{"name": "docs", "slug": "docs", "files": {"SKILL.md": b"hi"}}],
        _Session(),
    )
    assert gap is None
    assert sandbox.calls == 0


@pytest.mark.asyncio
async def test_ensure_generic_runtime_caches_and_does_not_reprobe():
    sandbox = ProbeSandbox()
    session = _Session()
    packages = [{
        "name": "report-writer",
        "slug": "report-writer",
        "files": {"skill.json": json.dumps({"runtime": {"python": ["docx"]}}).encode()},
    }]
    first = await ensure_generic_runtime(sandbox, packages, session)
    second = await ensure_generic_runtime(sandbox, packages, session)
    assert first is None
    assert second is None
    assert sandbox.calls == 1
    assert session.runtime_preflight["skill:report-writer"]["status"] == "ready"


def test_execute_in_sandbox_invokes_generic_runtime_preflight():
    import ast
    import inspect

    from app.services.sandbox import sandbox_executor

    tree = ast.parse(inspect.getsource(sandbox_executor.execute_in_sandbox))
    called = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.append(func.id)
        elif isinstance(func, ast.Attribute):
            called.append(func.attr)
    assert "ensure_generic_runtime" in called
    assert "_pptd_runtime_preflight" in called
    source = inspect.getsource(sandbox_executor.execute_in_sandbox)
    ppt_at = source.find("_needs_pptd_preflight")
    generic_at = source.find("ensure_generic_runtime")
    assert ppt_at != -1 and generic_at != -1 and ppt_at < generic_at


@pytest.mark.asyncio
async def test_blocked_generic_runtime_is_cached():
    sandbox = ProbeSandbox({"python:docx"})
    session = _Session()
    packages = [{
        "name": "report-writer",
        "slug": "report-writer",
        "files": {"skill.json": json.dumps({"runtime": {"python": ["docx"]}}).encode()},
    }]
    first = await ensure_generic_runtime(sandbox, packages, session)
    second = await ensure_generic_runtime(sandbox, packages, session)
    assert first and "缺少 python:docx" in first
    assert second == first
    assert sandbox.calls == 1


class InstallSandbox(ProbeSandbox):
    def __init__(self, missing=(), *, pip_ok=True, versions=None):
        super().__init__(missing, versions)
        self.pip_ok = pip_ok
        self.pip_commands = []

    async def set_env_prep_network(self, enabled: bool) -> bool:
        return True

    async def execute(self, command, options):
        if "pip install" in command:
            self.pip_commands.append((command, getattr(options, "phase", None)))
            if self.pip_ok:
                self.missing.clear()
            return SimpleNamespace(
                ok=self.pip_ok,
                stdout="",
                stderr="" if self.pip_ok else "Could not find a version",
                termination_reason=None if self.pip_ok else "network_error",
            )
        return await super().execute(command, options)


@pytest.mark.asyncio
async def test_missing_python_dep_attempts_env_prep_install_then_reprobes():
    sandbox = InstallSandbox({"python:docx"})
    session = _Session()
    packages = [{
        "name": "report-writer",
        "slug": "report-writer",
        "files": {"skill.json": json.dumps({"runtime": {"python": ["docx"]}}).encode()},
    }]
    gap = await ensure_generic_runtime(sandbox, packages, session)
    assert gap is None
    assert sandbox.pip_commands
    assert sandbox.pip_commands[0][1] == "env_prep"


@pytest.mark.asyncio
async def test_hydrate_non_ppt_uses_work_root(monkeypatch):
    from app.services.agent_harness import artifact_checkpoint as ac
    from app.services.agent_harness.workspace_service import WORK_ROOT

    monkeypatch.setattr(ac.session_pool, "has_live_session", lambda _rid: False)
    pulled = {}

    async def _pull(**kwargs):
        pulled.update(kwargs)
        return {"restored": True}

    monkeypatch.setattr(ac, "_pull_workspace", _pull)
    meta, note = await ac.hydrate_ppt_staging(
        run_id="run-doc",
        user_id="u1",
        thread_id="t1",
        user_wants_resume=False,
        execution_profile={"id": "standard"},
    )
    assert meta and meta["restored"] is True
    assert pulled.get("project_root") == WORK_ROOT
    assert "新的一轮" in note


@pytest.mark.asyncio
async def test_capture_work_staging_commits_tree(monkeypatch):
    from app.services.agent_harness import artifact_checkpoint as ac

    class _Result:
        stdout = "WORK_PACKED"
        output_files = [{"name": ac.WORK_CHECKPOINT_NAME, "content": b"tgz-bytes"}]
        error = None
        ok = True

    async def _execute(*_a, **_kw):
        return _Result()

    committed = {}

    async def _commit(**kwargs):
        committed.update(kwargs)
        return {"workspace_object_id": "tree-w"}

    monkeypatch.setattr(ac.sandbox_executor, "execute_in_sandbox", _execute)
    monkeypatch.setattr(
        "app.services.agent_harness.workspace_service.commit_tree_snapshot",
        _commit,
    )
    saved = await ac.capture_work_staging(run_id="r1", thread_id="t1", user_id="u1")
    assert saved and saved["workspace_object_id"] == "tree-w"
    assert committed["blob"] == b"tgz-bytes"
    assert committed["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_capture_work_staging_skips_when_budget_too_small():
    from app.services.agent_harness import artifact_checkpoint as ac

    saved = await ac.capture_work_staging(
        run_id="r1", thread_id="t1", user_id="u1", timeout_ms=200,
    )
    assert saved is None


@pytest.mark.asyncio
async def test_version_mismatch_is_not_ready():
    sandbox = ProbeSandbox(versions={"pydantic": "2.7.4"})
    result = await probe_runtime_need(
        sandbox,
        RuntimeNeed(bins=(), python=("pydantic==1.10.0",)),
    )
    assert result["status"] == "blocked"
    assert any("pydantic==1.10.0" in item for item in result["missing"])


def test_requirements_keep_version_markers():
    from app.services.skills.runtime_ready import parse_requirements_txt, split_requirement

    specs = parse_requirements_txt(b"pydantic==1.10.0\n")
    assert specs == ("pydantic==1.10.0",)
    assert split_requirement("pydantic==1.10.0") == ("pydantic", "==1.10.0")


@pytest.mark.parametrize(
    ("spec", "installed", "ok"),
    [
        ("pydantic==1.10.0", "2.7.4", False),
        ("pydantic~=2.6.0", "2.7.4", False),
        ("pydantic==2.7.*", "2.7.4", True),
        ("pydantic>=2.7", "2.7.4", True),
        ("pydantic", "2.7.4", True),
        ("pydantic==2.7.4", "2.7.4", True),
        ("pydantic~=2.7.0", "2.7.4", True),
        ("pydantic==2.7.*", "2.8.0", False),
        ("pydantic>=2", None, False),
    ],
)
def test_requirement_satisfied_uses_pep440(spec, installed, ok):
    assert requirement_satisfied(spec, installed) is ok


@pytest.mark.asyncio
async def test_probe_compatible_release_and_wildcard_against_host_pydantic():
    sandbox = ProbeSandbox(versions={"pydantic": "2.7.4"})
    too_new = await probe_runtime_need(
        sandbox, RuntimeNeed(bins=(), python=("pydantic~=2.6.0",)),
    )
    assert too_new["status"] == "blocked"
    assert any("pydantic~=2.6.0" in item for item in too_new["missing"])

    wildcard = await probe_runtime_need(
        sandbox, RuntimeNeed(bins=(), python=("pydantic==2.7.*",)),
    )
    assert wildcard["status"] == "ready"
    assert wildcard["missing"] == []


@pytest.mark.asyncio
async def test_env_prep_install_keeps_original_requirement_spec():
    sandbox = InstallSandbox(versions={"pydantic": "2.7.4"})
    session = _Session()
    packages = [{
        "name": "typed",
        "slug": "typed",
        "files": {"requirements.txt": b"pydantic~=2.6.0\n"},
    }]
    await ensure_generic_runtime(sandbox, packages, session)
    assert sandbox.pip_commands
    command = sandbox.pip_commands[0][0]
    assert "pydantic~=2.6.0" in command
    assert "pydantic==2.7" not in command
