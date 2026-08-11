from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.models.entities import WorkerHeartbeat
from app.workers.identity import JOB_WORKER_HEARTBEAT_PREFIX


async def check(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    stale_after_seconds: int | None = None,
    checked_at: datetime | None = None,
) -> int:
    factory = session_factory or SessionFactory
    stale_seconds = (
        get_settings().worker_heartbeat_stale_seconds
        if stale_after_seconds is None
        else stale_after_seconds
    )
    async with factory() as session:
        latest = await session.scalar(
            select(func.max(WorkerHeartbeat.last_seen_at)).where(
                WorkerHeartbeat.worker_id.like(f"{JOB_WORKER_HEARTBEAT_PREFIX}%")
            )
        )
    if latest is None:
        return 1
    now = checked_at or utc_now()
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=now.tzinfo)
    return int(now - latest > timedelta(seconds=stale_seconds))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
