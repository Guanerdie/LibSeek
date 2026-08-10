from __future__ import annotations

from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditEvent, DiscoveryRun, Job
from app.models.enums import JobStatus

ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT)


async def find_active_discovery_run(session: AsyncSession, source: str) -> DiscoveryRun | None:
    statement = (
        select(DiscoveryRun)
        .join(Job, Job.run_id == DiscoveryRun.id)
        .where(
            DiscoveryRun.source == source,
            Job.job_type == f"DISCOVER_{source.upper()}",
            Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(DiscoveryRun.created_at.desc())
        .limit(1)
    )
    return cast(DiscoveryRun | None, await session.scalar(statement))


async def list_discovery_runs(
    session: AsyncSession, page: int, page_size: int
) -> tuple[list[DiscoveryRun], int]:
    total = await session.scalar(select(func.count()).select_from(DiscoveryRun)) or 0
    statement = (
        select(DiscoveryRun)
        .order_by(DiscoveryRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await session.scalars(statement)).all()), total


async def list_run_audit_events(session: AsyncSession, run_id: str) -> list[AuditEvent]:
    statement = (
        select(AuditEvent)
        .where(AuditEvent.entity_type == "discovery_run", AuditEvent.entity_id == run_id)
        .order_by(AuditEvent.created_at.asc())
    )
    return list((await session.scalars(statement)).all())
