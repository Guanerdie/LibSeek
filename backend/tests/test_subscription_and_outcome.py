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
from app.simple.automation import (
    _choose_candidate,
    get_policy,
    set_media_minimum_score,
    set_media_subscription,
    set_media_subscriptions,
    update_policy,
)
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    AutomationPolicy,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
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


# ---------------------------------------------------------------------------
# Bulk subscribing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_whole_filtered_selection_can_be_subscribed_at_once(session_factory) -> None:
    async with session_factory() as session:
        items = [await _media(session, tmdb_id=1100 + index) for index in range(3)]

        policy = await set_media_subscriptions(
            session, media_ids=[item.id for item in items], subscribed=True
        )

        assert set(policy.selected_media_ids) == {item.id for item in items}


@pytest.mark.asyncio
async def test_a_bulk_subscribe_does_not_duplicate_existing_entries(session_factory) -> None:
    async with session_factory() as session:
        first = await _media(session, tmdb_id=1110)
        second = await _media(session, tmdb_id=1111)
        await set_media_subscription(session, media_id=first.id, subscribed=True)

        policy = await set_media_subscriptions(
            session, media_ids=[first.id, second.id], subscribed=True
        )

        assert policy.selected_media_ids == [first.id, second.id]


@pytest.mark.asyncio
async def test_an_unknown_id_is_skipped_rather_than_failing_the_batch(session_factory) -> None:
    """The filter that produced the list may be a moment out of date."""

    async with session_factory() as session:
        real = await _media(session, tmdb_id=1120)

        policy = await set_media_subscriptions(
            session, media_ids=[real.id, "does-not-exist"], subscribed=True
        )

        assert policy.selected_media_ids == [real.id]


@pytest.mark.asyncio
async def test_bulk_unsubscribe_leaves_untouched_entries_alone(session_factory) -> None:
    async with session_factory() as session:
        keep = await _media(session, tmdb_id=1130)
        drop = await _media(session, tmdb_id=1131)
        await set_media_subscriptions(
            session, media_ids=[keep.id, drop.id], subscribed=True
        )

        policy = await set_media_subscriptions(
            session, media_ids=[drop.id], subscribed=False
        )

        assert policy.selected_media_ids == [keep.id]


# ---------------------------------------------------------------------------
# Per-media score override
# ---------------------------------------------------------------------------


def _candidate(score: float) -> ReleaseCandidate:
    return ReleaseCandidate(
        search_id="s",
        site_id="avistaz",
        torrent_id="t",
        title="Some.Show.S01.COMPLETE.1080p",
        size_bytes=1_000,
        seeders=8,
        score=score,
        reasons=["TMDB_ID_EXACT"],
    )


def _policy_for_score(minimum: float) -> AutomationPolicy:
    return AutomationPolicy(
        id="t",
        minimum_score=minimum,
        minimum_seeders=1,
        allow_warnings=False,
        site_ids=["avistaz"],
        automate_variety=False,
        scope_mode="filters",
        regions=[],
        selected_media_ids=[],
    )


def test_without_an_override_the_policy_threshold_applies() -> None:
    picked, _ = _choose_candidate(
        _policy_for_score(0.7), [_candidate(0.65)], media_type=MediaType.TV
    )

    assert picked is None


def test_a_looser_override_lets_one_show_through() -> None:
    candidate = _candidate(0.65)

    picked, _ = _choose_candidate(
        _policy_for_score(0.7),
        [candidate],
        media_type=MediaType.TV,
        minimum_score=0.6,
    )

    assert picked is candidate


def test_a_stricter_override_holds_one_show_back() -> None:
    picked, rejected = _choose_candidate(
        _policy_for_score(0.6),
        [_candidate(0.65)],
        media_type=MediaType.TV,
        minimum_score=0.8,
    )

    assert picked is None
    assert "评分低于本片单独设置的门槛" in rejected[0]["reasons"]


def test_an_override_of_zero_is_honoured_not_treated_as_unset() -> None:
    """0 is falsy; using `or` here would silently fall back to the policy."""

    candidate = _candidate(0.1)

    picked, _ = _choose_candidate(
        _policy_for_score(0.7),
        [candidate],
        media_type=MediaType.TV,
        minimum_score=0.0,
    )

    assert picked is candidate


@pytest.mark.asyncio
async def test_setting_and_clearing_the_override(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, tmdb_id=1140)

        updated = await set_media_minimum_score(
            session, media_id=media.id, minimum_score=0.45
        )
        assert updated.minimum_score_override == 0.45

        cleared = await set_media_minimum_score(
            session, media_id=media.id, minimum_score=None
        )
        assert cleared.minimum_score_override is None
