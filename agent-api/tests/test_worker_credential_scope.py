from datetime import datetime, timedelta

import pytest
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.runtime_models import AgentRun, AgentRunJob
from app.services.agent_harness import run_store
from app.services.connectors import crypto


@pytest.mark.asyncio
async def test_scope_is_authoritative_and_not_forwarded_to_model_input(monkeypatch):
    write = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(run_store, "patch_run_state", write)
    monkeypatch.setattr(crypto, "credential_key_id", lambda: "local-key")
    monkeypatch.setattr(crypto, "encrypt_secret", lambda _token: "encrypted-test-token")
    assert await run_store.store_pending_input("run", {"credential_key_id": "untrusted"}, access_token="test-token")
    payload = write.call_args.args[1]["pending_input"]
    assert payload == {"credential_key_id": "local-key", "access_token_cipher": "encrypted-test-token"}
    monkeypatch.setattr(run_store, "get_run_state", AsyncMock(return_value={"state": {"pending_input": payload}}))
    monkeypatch.setattr(crypto, "decrypt_secret", lambda _cipher: "test-token")
    assert await run_store.load_pending_input("run") == {"access_token": "test-token"}


@pytest.mark.asyncio
async def test_worker_skips_foreign_key_without_leasing_or_delaying_it(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_store, "runtime_session", lambda: factory)
    monkeypatch.setattr(crypto, "credential_key_id", lambda: "local-key")
    try:
        async with engine.begin() as connection:
            for model in (AgentRun, AgentRunJob):
                await connection.run_sync(model.__table__.create)
        async with factory() as session:
            for index, key in enumerate(("foreign-key", "local-key", None)):
                rid = f"run-{index}"
                session.add(AgentRun(id=rid, thread_id=rid, user_id="test", state={
                    "pending_input": {"credential_key_id": key} if key else {},
                }))
                session.add(AgentRunJob(id=f"job-{index}", run_id=rid, status="queued",
                                        available_at=datetime.utcnow() - timedelta(seconds=10-index)))
            await session.commit()
        assert (await run_store.claim_next_job("worker"))["run_id"] == "run-1"
        assert (await run_store.claim_next_job("worker"))["run_id"] == "run-2"
        assert await run_store.claim_next_job("worker") is None
        async with factory() as session:
            foreign = await session.get(AgentRunJob, "job-0")
            assert foreign.status == "queued"
            assert foreign.attempt_count == 0
            assert foreign.lease_owner is None
    finally:
        await engine.dispose()
