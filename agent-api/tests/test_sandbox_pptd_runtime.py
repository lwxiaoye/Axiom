"""The preflight must accept the same WASM locations as the real exporter."""

import contextlib
import io
import shlex
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.sandbox import sandbox_executor


IMAGE_WASM = "/opt/open-kimi-ppt/scripts/local-export/pptd_wasm_bg.wasm"
MOUNTED_WASM = "/workspace/skills/catalog-ppt/scripts/local-export/pptd_wasm_bg.wasm"
PACKAGES = [{"name": "ppt-studio", "slug": "catalog-ppt"}]


class ProbeSandbox:
    def __init__(self, files, *, missing_binary=""):
        self.files = files
        self.missing_binary = missing_binary
        self.calls = 0

    async def execute(self, command, options):
        self.calls += 1
        argv = shlex.split(command)
        assert argv[:2] == ["python3", "-c"]
        assert options.timeout_ms == 15_000
        stdout = io.StringIO()
        with (
            patch("shutil.which", side_effect=lambda name: None if name == self.missing_binary else "/usr/bin/" + name),
            patch("importlib.util.find_spec", return_value=object()),
            patch("os.path.isfile", side_effect=lambda path: path in self.files),
            patch("os.path.getsize", side_effect=lambda path: self.files[path]),
            contextlib.redirect_stdout(stdout),
        ):
            exec(argv[2], {})
        return SimpleNamespace(ok=True, stdout=stdout.getvalue(), stderr="")


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [{IMAGE_WASM: 788074}, {MOUNTED_WASM: 788074}])
async def test_pptd_preflight_accepts_image_or_mounted_wasm(files):
    assert await sandbox_executor._pptd_runtime_preflight(ProbeSandbox(files), PACKAGES) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [{}, {IMAGE_WASM: 0}, {MOUNTED_WASM: 0}])
async def test_pptd_preflight_still_rejects_missing_or_empty_wasm(files):
    result = await sandbox_executor._pptd_runtime_preflight(ProbeSandbox(files), PACKAGES)
    assert "缺少 PPTD-WASM" in result


@pytest.mark.asyncio
async def test_image_wasm_does_not_bypass_other_runtime_requirements():
    result = await sandbox_executor._pptd_runtime_preflight(
        ProbeSandbox({IMAGE_WASM: 788074}, missing_binary="node"), PACKAGES,
    )
    assert "缺少 Node.js" in result


@pytest.mark.asyncio
async def test_third_party_skill_is_not_subject_to_first_party_preflight():
    sandbox = ProbeSandbox({})
    assert await sandbox_executor._pptd_runtime_preflight(sandbox, [{"name": "other-ppt"}]) is None
    assert sandbox.calls == 0


@pytest.mark.asyncio
async def test_preflight_execution_failure_is_not_reported_as_ready():
    class FailedSandbox:
        async def execute(self, *_args):
            return SimpleNamespace(ok=False, stdout="", stderr="connection lost")

    assert "自检失败" in await sandbox_executor._pptd_runtime_preflight(FailedSandbox(), PACKAGES)
