"""Following a show, and seeing why nothing was downloaded.

Two gaps this covers, both found by looking at the live deployment:

* The automation scope existed only as ``selected_media_ids`` on the policy,
  editable through a multi-select over the whole library.  It had drifted to
  three entries and had not been revisited, so automation effectively had
  nothing to do.
* ``AutomationJob.decision`` records exactly why a candidate was refused, but
  it was reachable only from the automation pages -- a media item sitting at
  READY looked the same whether the site had nothing, the score threshold
  rejected everything, or every candidate was a dead torrent.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.time import utc_now
from app.models.enums import MediaType
from app.simple.automation import get_policy, set_media_subscription, update_policy
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    LibraryMediaItem,
    MediaState,
)
from app.simple.schemas import AutomationPolicyUpdate
from app.simple.service import latest_automation_job


async def _media(session, *, tmdb_id: int) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"sub-{tmdb_id}",
        media_type=MediaType.TV,
        tmdb_id=tmdb_id,
        title=f"Show {tmdb_id}",
        year=2026,
        state=MediaState.READY,
    )
    session.add(media)
    await session.commit()
    return media


# ---------------------------------------------------------------------------
# Subscribing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribing_adds_the_media_to_the_scope(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1001)

        policy = await set_media_subscription(session, media_id=media.id, subscribed=True)

        assert policy.selected_media_ids == [media.id]


@pytest.mark.asyncio
async def test_unsubscribing_removes_it_again(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1002)
        await set_media_subscription(session, media_id=media.id, subscribed=True)

        policy = await set_media_subscription(session, media_id=media.id, subscribed=False)

        assert policy.selected_media_ids == []


@pytest.mark.asyncio
async def test_subscribing_twice_does_not_duplicate(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1003)
        await set_media_subscription(session, media_id=media.id, subscribed=True)

        policy = await set_media_subscription(session, media_id=media.id, subscribed=True)

        assert policy.selected_media_ids == [media.id]


@pytest.mark.asyncio
async def test_unsubscribing_something_never_subscribed_is_harmless(session_factory) -> None:
    async with session_factory() as session:
        first = await _media(session, tmdb_id=1004)
        second = await _media(session, tmdb_id=1005)
        await set_media_subscription(session, media_id=first.id, subscribed=True)

        policy = await set_media_subscription(session, media_id=second.id, subscribed=False)

        assert policy.selected_media_ids == [first.id]


@pytest.mark.asyncio
async def test_subscribing_never_changes_the_scope_mode(session_factory) -> None:
    """Flipping a policy-wide switch from one item would drop all the others."""

    async with session_factory() as session:
        media = await _media(session, tmdb_id=1006)
        await update_policy(session, AutomationPolicyUpdate(scope_mode="filters"))

        await set_media_subscription(session, media_id=media.id, subscribed=True)

        policy = await get_policy(session)
        assert policy.scope_mode == "filters"
        # The subscription is recorded, it simply is not what automation reads
        # while the scope is on filters -- which the API reports separately.
        assert policy.selected_media_ids == [media.id]


@pytest.mark.asyncio
async def test_other_subscriptions_survive_an_unsubscribe(session_factory) -> None:
    async with session_factory() as session:
        first = await _media(session, tmdb_id=1007)
        second = await _media(session, tmdb_id=1008)
        await set_media_subscription(session, media_id=first.id, subscribed=True)
        await set_media_subscription(session, media_id=second.id, subscribed=True)

        policy = await set_media_subscription(session, media_id=first.id, subscribed=False)

        assert policy.selected_media_ids == [second.id]


# ---------------------------------------------------------------------------
# Seeing the last automation outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_automation_history_reports_nothing(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1010)

        assert await latest_automation_job(session, media.id) is None


@pytest.mark.asyncio
async def test_the_most_recent_attempt_is_the_one_reported(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1011)
        session.add(
            AutomationJob(
                run_id="run-old",
                media_id=media.id,
                state=AutomationJobState.SUCCEEDED,
                created_at=utc_now() - timedelta(hours=2),
                decision={"candidate_count": 1, "selected_title": "旧的"},
            )
        )
        await session.commit()
        session.add(
            AutomationJob(
                run_id="run-new",
                media_id=media.id,
                state=AutomationJobState.SUCCEEDED,
                created_at=utc_now(),
                decision={
                    "candidate_count": 40,
                    "selected_title": None,
                    "rejected": [{"title": "Some.Release", "reasons": ["评分低于策略门槛"]}],
                },
            )
        )
        await session.commit()

        job = await latest_automation_job(session, media.id)

        assert job is not None
        assert job.run_id == "run-new"
        assert job.decision["candidate_count"] == 40
        assert job.decision["rejected"][0]["reasons"] == ["评分低于策略门槛"]


@pytest.mark.asyncio
async def test_another_medias_history_is_not_returned(session_factory) -> None:
    async with session_factory() as session:
        mine = await _media(session, tmdb_id=1012)
        theirs = await _media(session, tmdb_id=1013)
        session.add(
            AutomationJob(run_id="r", media_id=theirs.id, state=AutomationJobState.SUCCEEDED)
        )
        await session.commit()

        assert await latest_automation_job(session, mine.id) is None
