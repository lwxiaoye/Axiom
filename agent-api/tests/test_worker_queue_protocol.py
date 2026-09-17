"""Mixed-version queue regression; optionally exercise an isolated PostgreSQL schema."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.runtime_models import AgentRun, AgentRunJob
from app.services.agent_harness import run_store
from app.services.connectors import crypto


@pytest_asyncio.fixture
async def queue_db(monkeypatch):
    url = os.environ.get("HARNESS_QUEUE_TEST_DATABASE_URL", "sqlite+aiosqlite://")
    schema = f"qa_worker_{uuid.uuid4().hex}"
    admin = None
    if url.startswith("postgresql"):
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"options": f"-c search_path={schema}"})
    else:
        engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_store, "runtime_session", lambda: factory)
    monkeypatch.setattr(crypto, "credential_key_id", lambda: "local-key")
    try:
        async with engine.begin() as conn:
            for model in (AgentRun, AgentRunJob):
                await conn.run_sync(model.__table__.create)
        yield factory
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


async def seed_run(factory, rid="run-local", key="local-key"):
    async with factory() as session:
        session.add(AgentRun(id=rid, thread_id=rid, user_id="queue-regression", state={
            "pending_input": {"credential_key_id": key} if key else {},
        }))
        await session.commit()
    await run_store.enqueue_job(rid)


async def legacy_candidates(factory):
    # This is the pre-key-scope worker's actual eligibility predicate.
    now = datetime.utcnow()
    async with factory() as session:
        return (await session.execute(select(AgentRunJob.run_id).where(or_(
            and_(AgentRunJob.status == "queued", or_(
                AgentRunJob.available_at.is_(None), AgentRunJob.available_at <= now,
            )),
            and_(AgentRunJob.status == "leased", AgentRunJob.lease_expires_at <= now),
        )))).scalars().all()


async def expire_lease(factory, job):
    async with factory() as session:
        row = await session.get(AgentRunJob, job["id"])
        row.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()


@pytest.mark.asyncio
async def test_legacy_worker_cannot_take_new_or_expired_scoped_jobs(queue_db):
    await seed_run(queue_db)
    assert await legacy_candidates(queue_db) == []
    job = await run_store.claim_next_job("worker-a")
    assert job["run_id"] == "run-local"
    await expire_lease(queue_db, job)
    assert await legacy_candidates(queue_db) == []
    takeover = await run_store.claim_next_job("worker-b")
    assert takeover["id"] == job["id"]
    assert takeover["attempt_count"] == 2
    assert not await run_store.heartbeat_job(job["id"], "worker-a")
    assert not await run_store.finish_job(job["id"], "worker-a", status="completed")
    assert await run_store.heartbeat_job(job["id"], "worker-b")
    assert await run_store.finish_job(job["id"], "worker-b", status="completed")


@pytest.mark.asyncio
async def test_current_worker_filters_foreign_keys_and_retains_legacy_support(queue_db):
    await seed_run(queue_db, "run-foreign", "foreign-key")
    await seed_run(queue_db, "run-legacy", None)
    assert await legacy_candidates(queue_db) == ["run-legacy"]
    job = await run_store.claim_next_job("worker-a")
    assert job["run_id"] == "run-legacy"
    assert await run_store.claim_next_job("worker-a") is None
    async with queue_db() as session:
        foreign = await session.scalar(select(AgentRunJob).where(AgentRunJob.run_id == "run-foreign"))
        assert foreign.attempt_count == 0
        assert foreign.lease_owner is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wake", ["recovery", "input", "completion_gap"])
async def test_wake_preserves_owner_deadline_and_scoped_requeue(queue_db, wake):
    await seed_run(queue_db)
    job = await run_store.claim_next_job("worker-a")
    deadline = datetime.utcnow() + timedelta(seconds=30)
    assert await run_store.enqueue_job("run-local", wake_reason=wake, available_at=deadline)
    assert await run_store.heartbeat_job(job["id"], "worker-a")
    assert await run_store.claim_next_job("worker-b") is None
    assert await run_store.finish_job(job["id"], "worker-a", status="waiting")
    async with queue_db() as session:
        row = await session.get(AgentRunJob, job["id"])
        assert row.available_at == deadline
        assert row.wake_reason == wake
        assert row.attempt_count == 1
        row.available_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
    assert await legacy_candidates(queue_db) == []
    resumed = await run_store.claim_next_job("worker-b")
    assert resumed["id"] == job["id"]
    assert resumed["wake_reason"] == wake


@pytest.mark.asyncio
async def test_same_worker_never_duplicates_its_expired_scoped_lease(queue_db):
    await seed_run(queue_db)
    job = await run_store.claim_next_job("worker-a")
    await expire_lease(queue_db, job)
    assert await run_store.claim_next_job("worker-a") is None
    async with queue_db() as session:
        row = await session.get(AgentRunJob, job["id"])
        assert row.attempt_count == 1
        assert row.lease_expires_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_recovery_backoff_never_exposes_scoped_job_to_legacy_worker(queue_db):
    await seed_run(queue_db)
    job = await run_store.claim_next_job("worker-a")
    await expire_lease(queue_db, job)
    async with queue_db() as session:
        row = await session.get(AgentRunJob, job["id"])
        row.attempt_count = row.max_attempts
        await session.commit()
    assert await run_store.claim_next_job("worker-b") is None
    async with queue_db() as session:
        row = await session.get(AgentRunJob, job["id"])
        assert row.attempt_count == 0
        assert row.lease_owner is None
        row.available_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
    assert await legacy_candidates(queue_db) == []
    assert (await run_store.claim_next_job("worker-b"))["id"] == job["id"]


@pytest.mark.asyncio
async def test_waiting_input_and_existing_queued_jobs_upgrade_on_resume(queue_db):
    await seed_run(queue_db)
    async with queue_db() as session:
        row = await session.scalar(select(AgentRunJob).where(AgentRunJob.run_id == "run-local"))
        row.status = "queued"
        await session.commit()
    job = await run_store.claim_next_job("worker-a")
    await expire_lease(queue_db, job)
    assert await legacy_candidates(queue_db) == []
    assert await run_store.finish_job(job["id"], "worker-a", status="waiting")
    assert await run_store.enqueue_job("run-local", wake_reason="input")
    assert await legacy_candidates(queue_db) == []
    assert (await run_store.claim_next_job("worker-b"))["id"] == job["id"]


@pytest.mark.asyncio
async def test_postgres_workers_do_not_claim_the_same_job_concurrently(queue_db):
    if queue_db.kw["bind"].dialect.name != "postgresql":
        pytest.skip("SKIP LOCKED requires PostgreSQL")
    await seed_run(queue_db)
    claims = await asyncio.gather(
        run_store.claim_next_job("worker-a"),
        run_store.claim_next_job("worker-b"),
    )
    claimed = [job for job in claims if job is not None]
    assert len(claimed) == 1
    assert claimed[0]["attempt_count"] == 1
