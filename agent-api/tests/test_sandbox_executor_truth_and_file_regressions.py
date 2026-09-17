from types import SimpleNamespace

import pytest

from app.services.agent_harness import model_driver
from app.services.files import user_file_service
from app.services.sandbox.sandbox_executor import SandboxExecutionResult


class _FakeResult:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many or []

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return self._many


class _FakeSession:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _query):
        return self.result


@pytest.mark.asyncio
async def test_overwrite_missing_file_raises_domain_error_not_name_error(monkeypatch):
    monkeypatch.setattr(user_file_service, "async_session", lambda: _FakeSession(_FakeResult(one=None)))
    with pytest.raises(user_file_service.UserFileError, match="文件不存在"):
        await user_file_service.overwrite_file("u1", "missing", b"data")


@pytest.mark.asyncio
async def test_find_generated_file_uses_rows_collection(monkeypatch):
    row = SimpleNamespace(id="f1", filename="a.docx")
    monkeypatch.setattr(user_file_service, "async_session", lambda: _FakeSession(_FakeResult(many=[row])))

    async def visible(rows, **_kwargs):
        assert rows == [row]
        return rows

    monkeypatch.setattr(user_file_service, "_rows_with_existing_bytes", visible)
    monkeypatch.setattr(user_file_service, "_row_to_dict", lambda value: {"id": value.id})
    assert await user_file_service.find_generated_file("u1", "a.docx", "t1") == {"id": "f1"}


@pytest.mark.asyncio
async def test_generic_executor_reports_nonzero_without_raising(monkeypatch):
    """迁移说明（2026-07-27 execute_in_sandbox 退休，能力迁到 bash）：

    原用例断言「脚本非零退出且无产物 → ToolSoftError(script_error)」。bash 是**有意换了口径**：
    命令非零退出是正常结果（模型要看 stderr 自纠），只有沙箱层失败（provider 不可用）才软失败。
    这条差异有意为之，故本用例改为断言新口径；软失败那条由 test_bash_tool.py 覆盖。
    """
    from app.services.chat.tools.shell import build_shell_tools
    from app.services.sandbox import sandbox_executor

    async def fake_run(*_args, **_kwargs):
        return SandboxExecutionResult(ok=False, exit_code=1, stderr="SyntaxError: invalid syntax")

    monkeypatch.setattr(sandbox_executor, "execute_in_sandbox", fake_run)
    bash = build_shell_tools(run_id="r1")[0]
    value = await bash.execute({"command": "python3 -c 'broken('"})
    text = value.model_content
    assert "exit_code=1" in text and "SyntaxError" in text


@pytest.mark.asyncio
async def test_empty_artifact_is_not_persisted():
    """行为保留（原 test_execute_in_sandbox_does_not_persist_empty_artifact）：0 字节产物不落库。

    落进「我的文件」就是给用户一个打不开的东西，比不落更糟。execute_in_sandbox 的 outputs/ 链路有这条
    守卫，统一文件系统的 files/ 回写路径必须等价保留 —— 迁移时差点漏掉。
    """
    from app.services.chat.tools.workspace_sync import WorkspaceSync

    sync = WorkspaceSync("u1")
    saved = await sync.persist([{"path": "empty.pptx", "data": b""}])
    assert saved == []
    assert sync.empty_skipped == ["empty.pptx"]
    assert "空文件" in sync.persist_notice()


# 已按设计消失（原 test_execute_in_sandbox_generated_but_unsaved_is_persist_failure）：
# 那条断言「脚本生成了产物但没存进「我的文件」→ 落库失败」，前提是 outputs/ 与「我的文件」
# 是两个地方、需要一次显式收割。统一文件系统之后 **写进 /workspace/files 就是已保存**，
# 「生成了但没保存」这个状态不存在了，故不再有对应用例。
