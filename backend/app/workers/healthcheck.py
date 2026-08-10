from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.models.entities import WorkerHeartbeat


async def check() -> int:
    settings = get_settings()
    async with SessionFactory() as session:
        latest = await session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    if latest is None:
        return 1
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=utc_now().tzinfo)
    return int(utc_now() - latest > timedelta(seconds=settings.worker_heartbeat_stale_seconds))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))

