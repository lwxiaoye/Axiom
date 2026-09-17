"""OpenSandbox SDK contract tests without requiring a running server."""

from types import SimpleNamespace

import pytest

from app.services.sandbox.base import ExecuteOptions, FileWriteEntry
from app.services.sandbox.opensandbox_adapter import OpenSandboxAdapter


class _FakeConnectionConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeRunOpts:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeHandlers:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeWriteEntry:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeCommands:
    def __init__(self):
        self.calls = []

    async def run(self, command, *, opts=None, handlers=None):
        self.calls.append((command, opts, handlers))
        await handlers.kwargs["on_stdout"](SimpleNamespace(text="hello\n"))
        await handlers.kwargs["on_stderr"](SimpleNamespace(text="warning\n"))
        return SimpleNamespace(
            exit_code=0,
            logs=SimpleNamespace(stdout=[], stderr=[]),
            error=None,
        )


class _FakeFiles:
    def __init__(self):
        self.writes = []
        self.max_batch = None

    async def write_files(self, entries):
        if self.max_batch is not None and len(entries) > self.max_batch:
            raise RuntimeError(f"batch too large: {len(entries)}")
        self.writes.append(entries)

    async def read_bytes(self, path):
        return path.encode()


class _FakeSandbox:
    created = []

    def __init__(self):
        self.id = "sb-1"
        self.commands = _FakeCommands()
        self.files = _FakeFiles()
        self.destroyed = False

    @classmethod
    async def create(cls, image, **kwargs):
        sandbox = cls()
        cls.created.append((image, kwargs, sandbox))
        return sandbox

    async def destroy(self):
        self.destroyed = True


def _install_sdk(monkeypatch):
    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (_FakeSandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    _FakeSandbox.created = []


@pytest.mark.asyncio
async def test_create_uses_current_sdk_contract(monkeypatch):
    _install_sdk(monkeypatch)
    adapter = OpenSandboxAdapter(
        {
            "domain": "http://opensandbox:8080",
            "api_key": "secret",
            "use_server_proxy": True,
            "cpu": "1",
            "memory": "1Gi",
            "image": "agent-sandbox-py:local",
        }
    )

    await adapter.create()

    image, kwargs, _sandbox = _FakeSandbox.created[0]
    assert image == "agent-sandbox-py:local"
    assert kwargs["connection_config"].kwargs == {
        "domain": "http://opensandbox:8080",
        "api_key": "secret",
        "use_server_proxy": True,
    }
    assert kwargs["resource"] == {"cpu": "1", "memory": "1Gi"}
    assert kwargs["timeout"].total_seconds() >= 1800


@pytest.mark.asyncio
async def test_execute_uses_keyword_sdk_options_and_caps_output(monkeypatch):
    _install_sdk(monkeypatch)
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    seen = []

    result = await adapter.execute(
        "echo hello",
        ExecuteOptions(
            working_directory="/workspace",
            timeout_ms=2500,
            env={"A": "B"},
            max_output_bytes=5,
            on_stdout=seen.append,
        ),
    )

    sandbox = _FakeSandbox.created[0][2]
    command, opts, handlers = sandbox.commands.calls[0]
    assert command == "echo hello"
    assert opts.kwargs["working_directory"] == "/workspace"
    assert opts.kwargs["timeout"].total_seconds() == 2.5
    assert opts.kwargs["envs"] == {"A": "B"}
    assert handlers.kwargs["skip_accumulation"] is True
    assert result.stdout == "hello"
    assert result.stderr == "warni"
    assert result.truncated is True
    assert seen == [b"hello\n"]


@pytest.mark.asyncio
async def test_write_read_and_delete_use_current_sdk_methods(monkeypatch):
    _install_sdk(monkeypatch)
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    await adapter.write_files([FileWriteEntry(path="a.txt", data=b"abc", mode=0o644)])
    sandbox = _FakeSandbox.created[0][2]
    entry = sandbox.files.writes[0][0]
    assert entry.kwargs == {"path": "/workspace/a.txt", "data": b"abc", "mode": 644}

    read = await adapter.read_files(["a.txt"])
    assert read[0].data == b"/workspace/a.txt"

    await adapter.delete()
    assert sandbox.destroyed is True


@pytest.mark.asyncio
async def test_write_files_splits_large_batches_for_server_proxy_uploads(monkeypatch):
    _install_sdk(monkeypatch)
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    entries = [
        FileWriteEntry(path=f"f{i}.txt", data=f"file-{i}".encode("utf-8"), mode=0o644)
        for i in range(25)
    ]
    await adapter.create()
    sandbox = _FakeSandbox.created[0][2]
    sandbox.files.max_batch = 10

    await adapter.write_files(entries)

    assert [len(batch) for batch in sandbox.files.writes] == [10, 10, 5]
    written_paths = [entry.kwargs["path"] for batch in sandbox.files.writes for entry in batch]
    assert written_paths == [f"/workspace/f{i}.txt" for i in range(25)]


@pytest.mark.asyncio
async def test_execute_timeout_exception_is_not_a_script_error(monkeypatch):
    class TimeoutCommands(_FakeCommands):
        async def run(self, command, *, opts=None, handlers=None):
            raise TimeoutError("command timed out after 2.5s")

    class TimeoutSandbox(_FakeSandbox):
        def __init__(self):
            super().__init__()
            self.commands = TimeoutCommands()

    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (TimeoutSandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    TimeoutSandbox.created = []
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    result = await adapter.execute("sleep 30", ExecuteOptions(timeout_ms=2500))
    assert result.timed_out is True
    assert result.termination_reason == "timeout"
    assert result.exit_code == 124
    assert result.ok is False


@pytest.mark.asyncio
async def test_execute_network_exception_is_classified(monkeypatch):
    class NetCommands(_FakeCommands):
        async def run(self, command, *, opts=None, handlers=None):
            raise ConnectionError("dns lookup failed")

    class NetSandbox(_FakeSandbox):
        def __init__(self):
            super().__init__()
            self.commands = NetCommands()

    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (NetSandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    NetSandbox.created = []
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    result = await adapter.execute("curl example.com")
    assert result.timed_out is False
    assert result.termination_reason == "network_error"
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_execution_error_timeout_sets_timed_out(monkeypatch):
    class ErrorCommands(_FakeCommands):
        async def run(self, command, *, opts=None, handlers=None):
            await handlers.kwargs["on_stdout"](SimpleNamespace(text=""))
            await handlers.kwargs["on_stderr"](SimpleNamespace(text=""))
            return SimpleNamespace(
                exit_code=0,
                logs=SimpleNamespace(stdout=[], stderr=[]),
                error=SimpleNamespace(name="TimeoutError", value="deadline exceeded"),
            )

    class ErrorSandbox(_FakeSandbox):
        def __init__(self):
            super().__init__()
            self.commands = ErrorCommands()

    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (ErrorSandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    ErrorSandbox.created = []
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    result = await adapter.execute("sleep 30")
    assert result.timed_out is True
    assert result.termination_reason == "timeout"
    assert result.exit_code == 124


@pytest.mark.asyncio
async def test_env_prep_without_egress_api_is_network_denied(monkeypatch):
    class NoEgressSandbox(_FakeSandbox):
        async def patch_egress_rules(self, rules):
            raise RuntimeError("egress not configured")

    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (NoEgressSandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    NoEgressSandbox.created = []
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    result = await adapter.execute("python3 -m pip install docx", ExecuteOptions(phase="env_prep"))
    assert result.termination_reason == "network_denied"
    assert result.ok is False
    assert NoEgressSandbox.created  # created to try egress
    sandbox = NoEgressSandbox.created[0][2]
    assert sandbox.commands.calls == []


@pytest.mark.asyncio
async def test_execute_background_returns_job_id(monkeypatch):
    class BgCommands(_FakeCommands):
        async def run(self, command, *, opts=None, handlers=None):
            self.calls.append((command, opts, handlers))
            return SimpleNamespace(id="exec-9", exit_code=None, error=None, logs=SimpleNamespace(stdout=[], stderr=[]))

    class BgSandbox(_FakeSandbox):
        def __init__(self):
            super().__init__()
            self.commands = BgCommands()

    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (BgSandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    BgSandbox.created = []
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    result = await adapter.execute_background("make all")
    assert result.job_id == "exec-9"
    opts = BgSandbox.created[0][2].commands.calls[0][1]
    assert opts.kwargs.get("background") is True


@pytest.mark.asyncio
async def test_env_prep_restore_failure_destroys_sandbox_and_fails(monkeypatch):
    class LeakySandbox(_FakeSandbox):
        def __init__(self):
            super().__init__()
            self.deleted = False

        async def patch_egress_rules(self, rules):
            return None

        async def delete_egress_rules(self, targets):
            raise RuntimeError("policy sidecar down")

        async def destroy(self):
            self.deleted = True
            self.destroyed = True

    monkeypatch.setattr(
        OpenSandboxAdapter,
        "_sdk",
        lambda _self: (LeakySandbox, _FakeConnectionConfig, _FakeHandlers, _FakeRunOpts, _FakeWriteEntry),
    )
    LeakySandbox.created = []
    adapter = OpenSandboxAdapter({"domain": "http://opensandbox:8080"})
    result = await adapter.execute("python3 -m pip install docx", ExecuteOptions(phase="env_prep"))
    assert result.termination_reason == "network_policy_restore_failed"
    assert result.ok is False
    assert adapter.network_leaked is True
    sandbox = LeakySandbox.created[0][2]
    assert sandbox.destroyed is True
    assert adapter._sandbox is None
