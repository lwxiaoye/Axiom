"""Working folders: real isolated SQL, real file bytes, tool and version contracts."""
import hashlib
import importlib.util
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import AgentUserFile, AgentUserFileVersion, AgentUserFolder, ChatMessage, ChatThread
from app.services.agent_harness import orchestrator, workspace_context
from app.services.chat.tools import build_tools
from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.paths import build_path_tools
from app.services.chat.tools.workspace_sync import WorkspaceSync
from app.services.files import user_file_service as files, work_folders
from app.services.files.storage.local import LocalFileStorage


@compiles(MEDIUMTEXT, "sqlite")
def mediumtext_on_sqlite(_element, _compiler, **_kw):
    return "TEXT"


@pytest_asyncio.fixture
async def folder_db(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def locks(connection, _record):
        connection.create_function("GET_LOCK", 2, lambda *_: 1)
        connection.create_function("RELEASE_LOCK", 1, lambda *_: 1)

    async with engine.begin() as connection:
        for model in (ChatThread, ChatMessage, AgentUserFolder, AgentUserFile, AgentUserFileVersion):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for module in (files, work_folders, orchestrator):
        monkeypatch.setattr(module, "async_session", factory)
    monkeypatch.setattr(files, "engine", engine)
    monkeypatch.setattr(settings, "USER_FILES_DIR", str(tmp_path))
    monkeypatch.setattr(files, "_storage", LocalFileStorage)
    monkeypatch.setattr(files, "schedule_preview_prewarm", lambda *_: None)
    async with factory() as session:
        session.add_all([
            AgentUserFolder(id="a", user_id="u1", name="工作 A"),
            AgentUserFolder(id="b", user_id="u1", name="工作 B"),
            AgentUserFolder(id="other", user_id="u2", name="他人文件夹"),
            ChatThread(id="t1", user_id="u1", workspace_folder_id="a"),
            ChatThread(id="t2", user_id="u1", workspace_folder_id="a"),
            ChatThread(id="plain", user_id="u1"),
        ])
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_folder_binding_is_owned_immutable_and_restored(folder_db):
    harness = orchestrator.HarnessOrchestrator()
    tid = await harness._ensure_thread(None, "u1", workspace_folder_id="a")
    assert (await harness.get_thread_model_setting("u1", tid))["workspace_folder"] == {"id": "a", "name": "工作 A"}
    assert await harness._ensure_thread(tid, "u1") == tid
    for requested, status in [("b", 409), ("other", 404), ("missing", 404)]:
        with pytest.raises(HTTPException) as exc:
            await harness._ensure_thread(tid, "u1", workspace_folder_id=requested)
        assert exc.value.status_code == status
    with pytest.raises(HTTPException) as exc:
        await harness._ensure_thread(None, "u1", workspace_folder_id="a", origin="interview")
    assert exc.value.status_code == 422
    assert await work_folders.folder_for_thread("u2", tid) is None
    await files.delete_folder("u1", "a")
    with pytest.raises(HTTPException) as exc:
        await harness._ensure_thread(tid, "u1")
    assert exc.value.status_code == 404
    assert (await harness.get_thread_model_setting("u1", tid))["workspace_folder"]["unavailable"] is True


@pytest.mark.asyncio
async def test_only_final_outputs_auto_save_to_folder(folder_db):
    final = await files.save_file("u1", "result.md", b"final", source="generated", thread_id="t1")
    uploaded = await files.save_file("u1", "my-script.py", b"print(1)", source="uploaded", folder_id="a")
    reference = await files.save_file("u1", "reference.png", b"reference", source="material", thread_id="t1")
    scratch = await files.save_file("u1", "gen.py", b"print(2)", source="generated", thread_id="t1")
    attachment = await files.save_file("u1", "attached.txt", b"attachment", source="workspace", thread_id="t1")
    assert final["folderId"] == uploaded["folderId"] == "a"
    assert final["expiresAt"] is uploaded["expiresAt"] is None
    assert reference["folderId"] is scratch["folderId"] is attachment["folderId"] is None
    assert {row["filename"] for row in await work_folders.list_folder_files("u1", "a")} == {"result.md", "my-script.py"}
    with pytest.raises(files.UserFileError) as exc:
        await work_folders.list_folder_files("u1", "other")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_folder_upload_visible_editable_across_threads_and_versioned(folder_db):
    original = await files.save_file("u1", "brief.txt", b"old content", source="uploaded", folder_id="a")
    hidden = await files.save_file("u1", "brief.txt", b"unrelated secret", source="uploaded", folder_id="b")
    tools = {tool.name: tool for tool in await build_tools(
        token="", knowledge_ids=None, web_enabled=False, user_id="u1", thread_id="t2",
        workspace_folder_id="a", action_authority="mutate", turn_intent="execution",
    )}
    read = await tools["read_file"].execute({"path": "brief.txt"})
    assert "old content" in read.model_content and "unrelated secret" not in read.model_content
    await tools["edit_file"].execute({"path": "brief.txt", "old_string": "old", "new_string": "updated"})
    assert (await files.read_bytes("u1", original["id"]))[1] == b"updated content"
    assert (await files.read_bytes("u1", hidden["id"]))[1] == b"unrelated secret"
    async with folder_db() as session:
        versions = (await session.execute(select(AgentUserFileVersion).where(
            AgentUserFileVersion.file_id == original["id"],
        ).order_by(AgentUserFileVersion.version_no))).scalars().all()
    assert [version.version_no for version in versions] == [1, 2]
    assert (await files.read_bytes("u1", original["id"]))[0].folder_id == "a"
    sync = WorkspaceSync("u1", thread_id="t2", workspace_folder_id="a", scope_to_thread=True)
    assert (await sync.load())["files"] == {"brief.txt": b"updated content"}
    await sync.persist([{"path": "brief.txt", "data": b"edited by bash"}])
    assert (await files.read_bytes("u1", original["id"]))[1] == b"edited by bash"


@pytest.mark.asyncio
async def test_internal_scripts_remain_readable_and_uploaded_scripts_save_edits(folder_db):
    original = await files.save_file("u1", "user.py", b"print(1)", source="uploaded", folder_id="a")
    tools = {tool.name: tool for tool in build_path_tools(user_id="u1", thread_id="t1", workspace_folder_id="a", scope_to_thread=True)}
    await tools["write_file"].execute({"path": "gen.py", "content": "print(2)"})
    assert "print(2)" in (await tools["read_file"].execute({"path": "gen.py"})).model_content
    sync = WorkspaceSync("u1", thread_id="t1", workspace_folder_id="a", scope_to_thread=True)
    assert set((await sync.load())["files"]) == {"gen.py", "user.py"}
    saved = await sync.persist([{"path": "user.py", "data": b"print(3)"}, {"path": "temporary.py", "data": b"skip"}])
    assert [item["id"] for item in saved] == [original["id"]]
    assert (await files.read_bytes("u1", original["id"]))[1] == b"print(3)"
    assert sync.intermediate_skipped == ["temporary.py"]


@pytest.mark.asyncio
async def test_selected_folder_inventory_failure_is_not_an_empty_workspace(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(files, "list_files", AsyncMock(side_effect=RuntimeError("file service unavailable")))
    sync = WorkspaceSync("u1", thread_id="t1", workspace_folder_id="a", scope_to_thread=True)
    with pytest.raises(RuntimeError, match="file service unavailable"):
        await sync.load()


@pytest.mark.asyncio
async def test_read_hash_and_folder_move_protect_concurrent_edits(folder_db):
    row = await files.save_file("u1", "brief.txt", b"old content", source="uploaded", folder_id="a")
    tools = {tool.name: tool for tool in build_path_tools(user_id="u1", thread_id="t1", workspace_folder_id="a", scope_to_thread=True)}
    await tools["read_file"].execute({"path": "brief.txt"})
    await files.overwrite_file("u1", row["id"], b"old content plus user edit")
    with pytest.raises(ToolSoftError, match="已被更新"):
        await tools["edit_file"].execute({"path": "brief.txt", "old_string": "old", "new_string": "new"})
    sync = WorkspaceSync("u1", thread_id="t1", workspace_folder_id="a", scope_to_thread=True)
    await sync.load()
    await files.move_file("u1", row["id"], "b")
    assert await sync.persist([{"path": "brief.txt", "data": b"wrong folder"}]) == []
    assert sync.conflict_files == ["brief.txt"]
    assert (await files.read_bytes("u1", row["id"]))[1] == b"old content plus user edit"


@pytest.mark.asyncio
async def test_generated_reuse_never_overwrites_a_moved_output(folder_db):
    first = await files.save_generated_bytes("u1", "report.txt", b"first", thread_id="t1")
    await files.move_file("u1", first["id"], "b")
    second = await files.save_generated_bytes("u1", "report.txt", b"second", thread_id="t1")
    assert second["id"] != first["id"] and second["folderId"] == "a"
    assert (await files.read_bytes("u1", first["id"]))[1] == b"first"
    assert [row["id"] for row in await files.list_generated_files("u1", "t1")] == [second["id"]]


@pytest.mark.asyncio
async def test_revision_selection_and_compiler_use_the_folder_upload(folder_db, monkeypatch):
    row = await files.save_file("u1", "brief.txt", b"old content", source="uploaded", folder_id="a")
    rows = await work_folders.list_folder_files("u1", "a")
    target = work_folders.choose_revision_file(rows, "请修改 brief.txt，将旧内容改为新版。")
    assert target["id"] == row["id"]
    snapshot = await files.get_revision_target_snapshot("u1", target["id"])
    assert snapshot["sha256"] == hashlib.sha256(b"old content").hexdigest()
    assert work_folders.choose_revision_file(rows, "修改 missing.txt") is None
    assert work_folders.choose_revision_file([*rows, {**row, "id": "duplicate"}], "修改 brief.txt") is None
    from unittest.mock import AsyncMock
    monkeypatch.setattr("app.services.agent_harness.workspace_service.inventory_for_compiler", AsyncMock(return_value={}))
    monkeypatch.setattr("app.services.agent_harness.workspace_service.text_snippets", AsyncMock(return_value=[]))
    block = await workspace_context.compile_workspace_status(run_id="test", user_id="u1", thread_id="t2")
    assert "brief.txt" in block and "/workspace/files" in block
    messages = [{"role": "system", "content": "rules"}]
    workspace_context.upsert_workspace_status(messages, block)
    workspace_context.upsert_workspace_status(messages, block)
    assert len(messages) == 2


def test_migration_adopts_existing_column_and_index():
    path = Path(__file__).parents[1] / "migrations/versions/mysql/0015_work_folders.py"
    spec = importlib.util.spec_from_file_location("work_folders_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE ai_chat_threads (id VARCHAR(64) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO ai_chat_threads(id) VALUES ('existing')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert "workspace_folder_id" in {column["name"] for column in inspect(connection).get_columns("ai_chat_threads")}
        assert connection.execute(text("SELECT id FROM ai_chat_threads")).scalar() == "existing"
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("moved", [False, True])
async def test_background_bash_restores_original_file_and_folder_baseline(folder_db, monkeypatch, moved):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services.chat.tools import shell
    from app.services.sandbox import session_pool

    original = await files.save_file("u1", "brief.txt", b"original", source="uploaded", folder_id="a")
    session = SimpleNamespace(jobs={}, sandbox=SimpleNamespace(
        get_job_status=AsyncMock(return_value=SimpleNamespace(running=False, exit_code=0, error="")),
    ))
    monkeypatch.setattr(session_pool, "peek", lambda _key: session)
    monkeypatch.setattr(session_pool, "release_job", lambda sess, job: sess.jobs.pop(job))
    monkeypatch.setattr("app.services.agent_harness.artifact_checkpoint.capture_work_staging", AsyncMock(return_value=None))
    calls = []

    async def execute(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            assert command.startswith("cd /workspace/files &&\n")
            loaded = await kwargs["workspace_loader"]({})
            assert loaded["files"]["brief.txt"] == b"original"
            session.jobs["j1"] = {"collect_workspace": True, "files_baseline": {"brief.txt": (8, 1)}}
            return SimpleNamespace(error=None, exit_code=None, job_id="j1")
        assert kwargs["workspace_baseline"] == {"brief.txt": (8, 1)}
        assert kwargs.get("workspace_loader") is None
        return SimpleNamespace(error=None, exit_code=0, workspace_changes=[{"path": "brief.txt", "data": b"edited"}],
                               workspace_deleted=None, workspace_oversized=None, outputs_migrated=None,
                               outputs_conflicts=None, review=None, truncated=False)

    monkeypatch.setattr(shell.sandbox_executor, "execute_in_sandbox", execute)
    tool = shell.build_shell_tools(user_id="u1", thread_id="t1", run_id="r1", workspace_folder_id="a", scope_to_thread=True)[0]
    await tool.execute({"command": "long-edit brief.txt"})
    assert session.jobs["j1"]["file_write_state"]["path_to_id"] == {"brief.txt": original["id"]}
    if moved:
        await files.move_file("u1", original["id"], "b")
    receipt = await tool.execute({"command": "job status j1"})
    assert (await files.read_bytes("u1", original["id"]))[1] == (b"original" if moved else b"edited")
    if moved:
        assert "没有写回" in receipt.model_content
    else:
        assert receipt.artifacts[0]["file_id"] == original["id"]
    assert session.jobs == {}
