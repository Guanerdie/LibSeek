from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import sanitize_details
from app.models.entities import AuditEvent, DiscoveryRun, Job
from app.models.enums import JobStatus
from app.repositories.discovery import find_active_discovery_run


async def create_discovery_run(
    session: AsyncSession,
    *,
    source: str = "nextfind",
    max_attempts: int = 3,
) -> tuple[DiscoveryRun, bool]:
    existing = await find_active_discovery_run(session, source)
    if existing is not None:
        return existing, True

    run = DiscoveryRun(source=source, status=JobStatus.PENDING)
    session.add(run)
    await session.flush()
    job = Job(
        job_type=f"DISCOVER_{source.upper()}",
        status=JobStatus.PENDING,
        payload={"source": source, "read_only": True},
        run_id=run.id,
        max_attempts=max_attempts,
    )
    session.add_all(
        [
            job,
            AuditEvent(
                event_type="DISCOVERY_RUN_CREATED",
                entity_type="discovery_run",
                entity_id=run.id,
                sanitized_details=sanitize_details({"source": source, "read_only": True}),
            ),
        ]
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await find_active_discovery_run(session, source)
        if existing is None:
            raise
        return existing, True
    return run, False

