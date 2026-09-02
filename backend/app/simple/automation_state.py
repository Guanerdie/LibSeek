from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.simple.models import AutomationJob, AutomationJobState, AutomationRun


async def refresh_automation_run_summary(
    session: AsyncSession, run_id: str
) -> AutomationRun | None:
    """Recalculate persisted counters after recovery or external reconciliation."""

    await session.flush()
    run = await session.get(AutomationRun, run_id)
    if run is None:
        return None
    rows = await session.execute(
        select(AutomationJob.state, func.count())
        .where(AutomationJob.run_id == run_id)
        .group_by(AutomationJob.state)
    )
    counts = {state: int(count) for state, count in rows}
    run.created_count = sum(counts.values())
    run.succeeded_count = counts.get(AutomationJobState.SUCCEEDED, 0)
    run.failed_count = counts.get(AutomationJobState.FAILED, 0)
    run.deferred_count = sum(
        counts.get(state, 0)
        for state in (
            AutomationJobState.PENDING,
            AutomationJobState.RUNNING,
            AutomationJobState.RETRY_WAIT,
        )
    )
    return run
