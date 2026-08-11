from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import WorkerHeartbeat
from app.workers.healthcheck import check
from app.workers.identity import JOB_WORKER_HEARTBEAT_PREFIX, job_worker_id


def test_job_worker_id_has_stable_idempotent_prefix() -> None:
    assert job_worker_id("host-a:42") == "job-worker:host-a:42"
    assert job_worker_id(" job-worker:host-a:42 ") == "job-worker:host-a:42"
    assert job_worker_id("x" * 200).startswith(JOB_WORKER_HEARTBEAT_PREFIX)
    assert len(job_worker_id("x" * 200)) == 180


def test_job_worker_id_rejects_empty_instance() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        job_worker_id(" job-worker: ")


@pytest.mark.asyncio
async def test_healthcheck_ignores_fresh_legacy_and_specialized_worker_heartbeats(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        session.add_all(
            [
                WorkerHeartbeat(worker_id="legacy-host:10", last_seen_at=now),
                WorkerHeartbeat(
                    worker_id="automation-preflight:host:11", last_seen_at=now
                ),
                WorkerHeartbeat(
                    worker_id="automation-preflight-ready:fingerprint:host:11",
                    last_seen_at=now,
                ),
                WorkerHeartbeat(worker_id="download-executor:host:12", last_seen_at=now),
                WorkerHeartbeat(
                    worker_id="download-executor-ready:fingerprint:host:12",
                    last_seen_at=now,
                ),
                WorkerHeartbeat(
                    worker_id=job_worker_id("host:13"),
                    last_seen_at=now - timedelta(seconds=31),
                ),
            ]
        )
        await session.commit()

    assert (
        await check(
            session_factory,
            stale_after_seconds=30,
            checked_at=now,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_healthcheck_accepts_only_fresh_prefixed_job_worker_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    worker_id = job_worker_id("host:13")
    async with session_factory() as session:
        session.add(WorkerHeartbeat(worker_id=worker_id, last_seen_at=now))
        await session.commit()

    assert (
        await check(
            session_factory,
            stale_after_seconds=30,
            checked_at=now,
        )
        == 0
    )

    async with session_factory() as session:
        await session.execute(
            update(WorkerHeartbeat)
            .where(WorkerHeartbeat.worker_id == worker_id)
            .values(last_seen_at=now - timedelta(seconds=31))
        )
        session.add(
            WorkerHeartbeat(worker_id="legacy-host:14", last_seen_at=now)
        )
        await session.commit()

    assert (
        await check(
            session_factory,
            stale_after_seconds=30,
            checked_at=now,
        )
        == 1
    )
