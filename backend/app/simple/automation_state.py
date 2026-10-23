from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.simple.models import (
    ActivityLog,
    AutomationJob,
    AutomationJobState,
    AutomationRun,
    Download,
)

_OUTCOMES = (
    "downloaded",
    "existing_download",
    "awaiting_library",
    "no_match",
    "dry_run",
    "skipped",
    "unknown",
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def automation_job_outcome(
    job: AutomationJob,
    *,
    download_created_at: datetime | None = None,
    download_submitted_at: datetime | None = None,
    submission_logged: bool = False,
) -> str | None:
    """Keep execution success separate from an actual new download submission.

    Legacy decisions have no explicit outcome. Only a submission timestamp and
    matching audit event within this job prove a new submission; a newly created
    download record alone can also mean qBittorrent already had the torrent.
    """
    if job.state == AutomationJobState.FAILED:
        return "failed"
    if job.state != AutomationJobState.SUCCEEDED:
        return None
    decision = job.decision or {}
    explicit = decision.get("outcome")
    if isinstance(explicit, str) and explicit in _OUTCOMES:
        return explicit
    if decision.get("download_skipped"):
        return "skipped"
    if decision.get("mode") == "dry-run" and job.selected_candidate_id:
        return "dry_run"
    if job.download_id:
        if (
            submission_logged
            and download_submitted_at is not None
            and job.finished_at is not None
            and _utc(job.created_at) <= _utc(download_submitted_at) <= _utc(job.finished_at)
        ):
            return "downloaded"
        if decision.get("download_state") in {"COMPLETED", "SEEDING"}:
            return "awaiting_library"
        if download_created_at is not None and _utc(download_created_at) < _utc(job.created_at):
            return "existing_download"
        return "unknown"
    if job.selected_candidate_id is None and "candidate_count" in decision:
        return "no_match"
    return "unknown"


async def automation_job_outcomes(
    session: AsyncSession, jobs: Sequence[AutomationJob]
) -> dict[str, str | None]:
    """Classify a page of jobs with one query, including conservative old-data handling."""
    if not jobs:
        return {}
    submission = exists().where(
        ActivityLog.event == "DOWNLOAD_SUBMITTED",
        ActivityLog.details["job_id"].as_string() == AutomationJob.id,
        ActivityLog.details["download_id"].as_string() == AutomationJob.download_id,
        ActivityLog.created_at >= AutomationJob.created_at,
        ActivityLog.created_at <= AutomationJob.finished_at,
    )
    rows = await session.execute(
        select(AutomationJob.id, Download.created_at, Download.submitted_at, submission)
        .outerjoin(Download, Download.id == AutomationJob.download_id)
        .where(AutomationJob.id.in_([job.id for job in jobs]))
    )
    evidence = {
        job_id: (created_at, submitted_at, logged)
        for job_id, created_at, submitted_at, logged in rows
    }
    return {
        job.id: automation_job_outcome(
            job,
            download_created_at=evidence[job.id][0],
            download_submitted_at=evidence[job.id][1],
            submission_logged=evidence[job.id][2],
        )
        for job in jobs
    }


async def automation_run_outcomes(
    session: AsyncSession, runs: Sequence[AutomationRun]
) -> dict[str, dict[str, int]]:
    """Include pre-search skips without inflating the number of generated jobs."""
    counts = {run.id: dict.fromkeys(_OUTCOMES, 0) for run in runs}
    if not runs:
        return counts
    jobs = list(
        await session.scalars(select(AutomationJob).where(AutomationJob.run_id.in_(counts)))
    )
    outcomes = await automation_job_outcomes(session, jobs)
    succeeded: dict[str, int] = dict.fromkeys(counts, 0)
    for job in jobs:
        if job.state == AutomationJobState.SUCCEEDED:
            succeeded[job.run_id] += 1
        outcome = outcomes[job.id]
        if outcome in _OUTCOMES:
            counts[job.run_id][outcome] += 1
    for run in runs:
        counts[run.id]["existing_download"] += run.skipped_existing_count
        counts[run.id]["awaiting_library"] += run.awaiting_library_count
        # Retention may have removed old jobs while retaining the run counters.
        counts[run.id]["unknown"] += max(0, run.succeeded_count - succeeded[run.id])
    return counts


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
