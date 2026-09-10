"""Keep the history tables from growing without bound.

Every automation cycle adds a run, its jobs, and searches with up to a few
hundred candidates each; every action adds an activity row.  Nothing removed
any of it, so a deployment kept every row it ever wrote.  Old history is pruned
here -- but never a row that still steers behaviour:

- the latest job and the latest search of each media item, which the media
  page reads to explain why nothing was downloaded;
- jobs still pending, running or waiting, and failures nobody has retried yet,
  because automation skips a media item while such a failure stands;
- a search whose candidate a download points at.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import Delete, Update, delete, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.simple.models import (
    ActivityLog,
    AutomationJob,
    AutomationJobState,
    AutomationRun,
    AutomationRunState,
    Download,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)

_logger = logging.getLogger(__name__)
# Rows per statement.  Each batch commits on its own so the first prune after
# months of history never holds SQLite's write lock for long.
_BATCH = 500
_FIRST_PRUNE_DELAY = timedelta(minutes=5)
_PRUNE_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class PruneResult:
    jobs: int = 0
    runs: int = 0
    searches: int = 0
    candidates: int = 0
    activity: int = 0

    @property
    def total(self) -> int:
        return self.jobs + self.runs + self.searches + self.candidates + self.activity


def _batches(ids: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(ids), _BATCH):
        yield ids[start : start + _BATCH]


async def _execute(session: AsyncSession, statement: Delete | Update) -> int:
    result = await session.execute(
        statement, execution_options={"synchronize_session": False}
    )
    return int(cast("CursorResult[Any]", result).rowcount or 0)


async def _prune_jobs(session: AsyncSession, cutoff: datetime) -> int:
    latest = (
        select(AutomationJob.media_id, func.max(AutomationJob.created_at).label("created_at"))
        .group_by(AutomationJob.media_id)
        .subquery()
    )
    ids = list(
        await session.scalars(
            select(AutomationJob.id)
            .join(latest, latest.c.media_id == AutomationJob.media_id)
            .where(
                AutomationJob.created_at < cutoff,
                AutomationJob.created_at < latest.c.created_at,
                # A superseded job has a newer copy doing its work; a success
                # is finished.  Anything else may still hold a media item back.
                or_(
                    AutomationJob.superseded_at.is_not(None),
                    AutomationJob.state == AutomationJobState.SUCCEEDED,
                ),
            )
        )
    )
    deleted = 0
    for batch in _batches(ids):
        # A retry keeps a pointer to the job it replaced; the lineage ends
        # here rather than dangling.
        await _execute(
            session,
            update(AutomationJob)
            .where(AutomationJob.retry_of_job_id.in_(batch))
            .values(retry_of_job_id=None),
        )
        deleted += await _execute(session, delete(AutomationJob).where(AutomationJob.id.in_(batch)))
        await session.commit()
    return deleted


async def _prune_runs(session: AsyncSession, cutoff: datetime) -> int:
    ids = list(
        await session.scalars(
            select(AutomationRun.id).where(
                AutomationRun.created_at < cutoff,
                AutomationRun.state.in_((AutomationRunState.SUCCEEDED, AutomationRunState.FAILED)),
                ~exists().where(AutomationJob.run_id == AutomationRun.id),
            )
        )
    )
    deleted = 0
    for batch in _batches(ids):
        deleted += await _execute(session, delete(AutomationRun).where(AutomationRun.id.in_(batch)))
        await session.commit()
    return deleted


async def _prune_searches(session: AsyncSession, cutoff: datetime) -> tuple[int, int]:
    latest = (
        select(ReleaseSearch.media_id, func.max(ReleaseSearch.created_at).label("created_at"))
        .group_by(ReleaseSearch.media_id)
        .subquery()
    )
    downloaded = select(ReleaseCandidate.search_id).join(
        Download, Download.candidate_id == ReleaseCandidate.id
    )
    ids = list(
        await session.scalars(
            select(ReleaseSearch.id)
            .join(latest, latest.c.media_id == ReleaseSearch.media_id)
            .where(
                ReleaseSearch.created_at < cutoff,
                ReleaseSearch.created_at < latest.c.created_at,
                ReleaseSearch.state.in_((SearchState.SUCCEEDED, SearchState.FAILED)),
                ReleaseSearch.id.not_in(downloaded),
            )
        )
    )
    searches = 0
    candidates = 0
    for batch in _batches(ids):
        candidate_ids = select(ReleaseCandidate.id).where(ReleaseCandidate.search_id.in_(batch))
        # Kept jobs may still point at an old search; they lose the link, not
        # their stored decision.
        await _execute(
            session,
            update(AutomationJob)
            .where(AutomationJob.search_id.in_(batch))
            .values(search_id=None),
        )
        await _execute(
            session,
            update(AutomationJob)
            .where(AutomationJob.selected_candidate_id.in_(candidate_ids))
            .values(selected_candidate_id=None),
        )
        candidates += await _execute(
            session, delete(ReleaseCandidate).where(ReleaseCandidate.search_id.in_(batch))
        )
        searches += await _execute(
            session, delete(ReleaseSearch).where(ReleaseSearch.id.in_(batch))
        )
        await session.commit()
    return searches, candidates


async def _prune_activity(session: AsyncSession, cutoff: datetime) -> int:
    deleted = await _execute(session, delete(ActivityLog).where(ActivityLog.created_at < cutoff))
    await session.commit()
    return deleted


async def prune_history(
    session: AsyncSession, *, retention_days: int, now: datetime | None = None
) -> PruneResult:
    cutoff = (now or utc_now()) - timedelta(days=retention_days)
    # Jobs before runs: a run goes only once it has no jobs left.
    jobs = await _prune_jobs(session, cutoff)
    runs = await _prune_runs(session, cutoff)
    searches, candidates = await _prune_searches(session, cutoff)
    activity = await _prune_activity(session, cutoff)
    return PruneResult(
        jobs=jobs, runs=runs, searches=searches, candidates=candidates, activity=activity
    )


async def _stopped_within(stop: asyncio.Event, delay: timedelta) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay.total_seconds())
    except TimeoutError:
        return False
    return True


async def history_retention_loop(
    stop: asyncio.Event,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    delay = _FIRST_PRUNE_DELAY
    while not await _stopped_within(stop, delay):
        delay = _PRUNE_INTERVAL
        retention_days = get_settings().history_retention_days
        if retention_days <= 0:
            continue
        try:
            async with session_factory() as session:
                result = await prune_history(session, retention_days=retention_days)
        except Exception:
            # One failed prune must not end the loop; the next day retries.
            _logger.exception("History pruning failed")
            continue
        if result.total:
            _logger.info("Pruned history older than %d days: %s", retention_days, result)
