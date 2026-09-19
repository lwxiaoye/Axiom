"""auth-api 用户资料 → sys_user 同步：upsert、5 分钟节流、失败不抛。隔离 SQLite，不碰共享库。"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import SysUser
from app.services import sys_user_sync as sync


@pytest_asyncio.fixture
async def sys_user_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(SysUser.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(sync, "async_session", factory)
    monkeypatch.setattr(sync, "_last_synced_at", {})
    try:
        yield factory
    finally:
        await engine.dispose()


async def load(factory, user_id):
    async with factory() as session:
        return (await session.execute(select(SysUser).where(SysUser.id == user_id))).scalar_one_or_none()


INFO = {"id": "u-alice", "username": "alice", "realname": "爱丽丝", "avatar": "uploads/" + "a" * 32 + ".png"}


@pytest.mark.asyncio
async def test_insert_then_throttle_then_update(sys_user_db):
    assert await sync.sync_sys_user_profile(INFO, now=1000.0) is True
    row = await load(sys_user_db, "u-alice")
    assert (row.username, row.realname, row.avatar) == ("alice", "爱丽丝", INFO["avatar"])

    # 5 分钟内即使资料变了也不写：节流优先（改完资料最多等一个窗口）
    changed = {**INFO, "realname": "Alice"}
    assert await sync.sync_sys_user_profile(changed, now=1000.0 + sync.SYNC_INTERVAL_SECONDS - 1) is False
    assert (await load(sys_user_db, "u-alice")).realname == "爱丽丝"

    # 窗口过了：只更新 username/realname/avatar 三列
    assert await sync.sync_sys_user_profile(changed, now=1000.0 + sync.SYNC_INTERVAL_SECONDS) is True
    assert (await load(sys_user_db, "u-alice")).realname == "Alice"

    # 再过一个窗口、资料未变：不发 UPDATE
    assert await sync.sync_sys_user_profile(changed, now=1000.0 + 2 * sync.SYNC_INTERVAL_SECONDS) is False


@pytest.mark.asyncio
async def test_incomplete_user_info_is_ignored(sys_user_db):
    assert await sync.sync_sys_user_profile({"realname": "无名"}, now=1.0) is False
    assert await sync.sync_sys_user_profile({"id": "u-x"}, now=1.0) is False
    assert await sync.sync_sys_user_profile("not-a-dict", now=1.0) is False  # type: ignore[arg-type]
    assert await load(sys_user_db, "u-x") is None


@pytest.mark.asyncio
async def test_db_failure_is_swallowed_and_retried_next_time(monkeypatch, sys_user_db):
    class Broken:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(sync, "async_session", lambda: Broken())
    assert await sync.sync_sys_user_profile(INFO, now=5.0) is False
    # 失败要把节流占位撤回，否则库恢复后还要白等 5 分钟
    assert "u-alice" not in sync._last_synced_at
    monkeypatch.setattr(sync, "async_session", sys_user_db)
    assert await sync.sync_sys_user_profile(INFO, now=6.0) is True


@pytest.mark.asyncio
async def test_auth_verify_calls_sync_without_breaking_on_error(monkeypatch):
    """鉴权回源拿到 userInfo 后要调同步；同步炸了鉴权仍成功。"""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.core import auth

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"success": True, "result": {"userInfo": INFO, "roles": ["user"]}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    seen = []

    async def exploding_sync(info):
        seen.append(dict(info))
        raise RuntimeError("boom")

    monkeypatch.setattr(sync, "sync_sys_user_profile", exploding_sync)
    monkeypatch.setattr(auth.settings, "AUTH_API_BASE", f"http://127.0.0.1:{server.server_port}")
    try:
        user = await auth._verify_token_with_auth_api("t")
    finally:
        server.shutdown()
        server.server_close()
    assert user.user_id == "u-alice" and user.real_name == "爱丽丝"
    assert seen and seen[0]["avatar"] == INFO["avatar"]
