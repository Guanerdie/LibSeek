"""Aggregates behind the monitoring page.

Optimisation 7 in ``NEXT_OPTIMIZATION_PLAN.md``.  Everything here is derived
from rows the app already writes -- no new instrumentation, no new table.  In
particular "site response time" is the wall-clock span of a ``ReleaseSearch``
(``created_at`` to ``finished_at``), which is what the operator actually waits
for; a per-HTTP-request timer would need a table of its own and would measure
something the user never sees.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.simple.models import (
    AutomationRun,
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)

# One week is the window the plan asks for, and it is also about as far back as
# a daily-use deployment keeps enough searches to be meaningful.
_TREND_DAYS = 7
_RECENT_RUNS = 20


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes even for ``DateTime(timezone=True)``."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _search_trend(session: AsyncSession, *, since: datetime) -> list[dict[str, object]]:
    rows = list(
        await session.scalars(
            select(ReleaseSearch).where(ReleaseSearch.created_at >= since)
        )
    )
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "succeeded": 0})
    for row in rows:
        if row.state not in {SearchState.SUCCEEDED, SearchState.FAILED}:
            # A search still running is not yet evidence either way.
            continue
        day = _as_utc(row.created_at).date().isoformat()
        buckets[day]["total"] += 1
        if row.state == SearchState.SUCCEEDED:
            buckets[day]["succeeded"] += 1

    today = utc_now().date()
    trend: list[dict[str, object]] = []
    for offset in range(_TREND_DAYS - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        bucket = buckets.get(day, {"total": 0, "succeeded": 0})
        total = bucket["total"]
        trend.append(
            {
                "date": day,
                "total": total,
                "succeeded": bucket["succeeded"],
                # None rather than 0 for a day with no searches: a flat zero
                # would read as "everything failed".
                "success_rate": (bucket["succeeded"] / total) if total else None,
            }
        )
    return trend


async def _site_latency(session: AsyncSession, *, since: datetime) -> list[dict[str, object]]:
    rows = list(
        await session.scalars(
            select(ReleaseSearch).where(
                ReleaseSearch.created_at >= since,
                ReleaseSearch.finished_at.is_not(None),
            )
        )
    )
    totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.finished_at is None:
            continue
        seconds = (_as_utc(row.finished_at) - _as_utc(row.created_at)).total_seconds()
        if seconds < 0:
            continue
        for site_id in row.site_ids:
            totals[site_id].append(seconds)

    return [
        {
            "site_id": site_id,
            "searches": len(samples),
            "average_seconds": round(sum(samples) / len(samples), 2),
            "slowest_seconds": round(max(samples), 2),
        }
        for site_id, samples in sorted(totals.items())
        if samples
    ]


async def _recent_runs(session: AsyncSession) -> list[dict[str, object]]:
    runs = list(
        await session.scalars(
            select(AutomationRun).order_by(AutomationRun.created_at.desc()).limit(_RECENT_RUNS)
        )
    )
    history: list[dict[str, object]] = []
    for run in runs:
        duration: float | None = None
        if run.started_at is not None and run.finished_at is not None:
            duration = round(
                (_as_utc(run.finished_at) - _as_utc(run.started_at)).total_seconds(), 2
            )
        history.append(
            {
                "id": run.id,
                "trigger": run.trigger,
                "state": run.state.value,
                "created_count": run.created_count,
                "succeeded_count": run.succeeded_count,
                "failed_count": run.failed_count,
                "deferred_count": run.deferred_count,
                "created_at": _as_utc(run.created_at).isoformat(),
                "duration_seconds": duration,
            }
        )
    return history


async def _library_coverage(session: AsyncSession) -> dict[str, object]:
    rows = list(
        await session.execute(
            select(LibraryMediaItem.state, func.count()).group_by(LibraryMediaItem.state)
        )
    )
    by_state = {state.value: count for state, count in rows}
    total = sum(by_state.values())
    # "Covered" is the part of the library the operator no longer has to think
    # about: already downloaded, or downloading right now.
    covered = by_state.get(MediaState.COMPLETE.value, 0) + by_state.get(
        MediaState.DOWNLOADING.value, 0
    )
    return {
        "total": total,
        "covered": covered,
        "coverage_rate": (covered / total) if total else None,
        "by_state": by_state,
    }


async def _download_health(session: AsyncSession) -> dict[str, object]:
    rows = list(
        await session.execute(select(Download.state, func.count()).group_by(Download.state))
    )
    by_state = {state.value: count for state, count in rows}
    return {
        "total": sum(by_state.values()),
        "errored": by_state.get(DownloadState.ERROR.value, 0),
        "by_state": by_state,
    }


# Buckets for the score histogram behind the "minimum score" setting.  0.05 is
# fine enough to show where the mass sits without turning into noise.
_SCORE_BUCKET = 0.05
_SCORE_WINDOW_DAYS = 30


async def score_distribution(session: AsyncSession) -> dict[str, object]:
    """How candidate scores are spread, so the threshold can be set with eyes open.

    The setting is a bare number box today, and it is the single most
    consequential one: production sat at 0.7 while only 2% of candidates ever
    reached it, so automation rejected almost everything and looked broken.
    Showing the distribution turns "0.7" from a guess into a decision.
    """

    since = utc_now() - timedelta(days=_SCORE_WINDOW_DAYS)
    scores = [
        float(score)
        for score in await session.scalars(
            select(ReleaseCandidate.score)
            .join(ReleaseSearch, ReleaseSearch.id == ReleaseCandidate.search_id)
            .where(ReleaseSearch.created_at >= since)
        )
    ]
    buckets: list[dict[str, object]] = []
    steps = round(1 / _SCORE_BUCKET)
    for index in range(steps):
        low = round(index * _SCORE_BUCKET, 2)
        high = round(low + _SCORE_BUCKET, 2)
        # The last bucket is closed so a perfect 1.0 is counted somewhere.
        count = sum(
            1
            for score in scores
            if score >= low and (score < high or (index == steps - 1 and score <= high))
        )
        buckets.append({"low": low, "high": high, "count": count})
    return {
        "window_days": _SCORE_WINDOW_DAYS,
        "total": len(scores),
        "buckets": buckets,
    }


async def collect_stats(session: AsyncSession) -> dict[str, object]:
    since = utc_now() - timedelta(days=_TREND_DAYS)
    return {
        "window_days": _TREND_DAYS,
        "generated_at": utc_now().isoformat(),
        "search_trend": await _search_trend(session, since=since),
        "site_latency": await _site_latency(session, since=since),
        "recent_runs": await _recent_runs(session),
        "library_coverage": await _library_coverage(session),
        "download_health": await _download_health(session),
    }
