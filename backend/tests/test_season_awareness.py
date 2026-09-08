"""A series that is still airing must still be automatable.

Optimisation 8 in ``NEXT_OPTIMIZATION_PLAN.md``.  Its finished seasons are
ordinary season packs; the season currently going out is not, and judging each
season on its own is what separates them.  The plan deferred the design to a
document that does not exist, so the rule implemented here is: a single-season
pack is accepted only when that season has finished airing *and* the library is
still missing episodes from it.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.adapters.metadata.tmdb import TmdbEpisode, TmdbProvider
from app.models.enums import MediaType
from app.schemas.adapters import SeasonRecord
from app.simple.automation import _season_context, _season_pack_reason
from app.simple.integrations import _sync_media_seasons
from app.simple.models import (
    Episode,
    EpisodeState,
    LibraryMediaItem,
    MediaSeason,
    MediaState,
    ReleaseCandidate,
)


def _candidate(seasons: list[int], title: str = "Show.S01.COMPLETE.1080p") -> ReleaseCandidate:
    return ReleaseCandidate(
        search_id="search-1",
        site_id="avistaz",
        torrent_id="t1",
        title=title,
        size_bytes=1_000,
        seeders=5,
        score=0.9,
        season_coverage=seasons,
    )


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def test_a_finished_season_with_missing_episodes_is_accepted() -> None:
    assert (
        _season_pack_reason(
            _candidate([1]),
            complete_seasons=frozenset({1}),
            seasons_with_missing_episodes=frozenset({1}),
        )
        is None
    )


def test_a_season_still_airing_is_rejected() -> None:
    reason = _season_pack_reason(
        _candidate([2]),
        complete_seasons=frozenset({1}),
        seasons_with_missing_episodes=frozenset({2}),
    )

    assert reason == "第 2 季尚未播完，季包不完整"


def test_a_season_the_library_already_has_is_rejected() -> None:
    """Otherwise an airing show keeps re-downloading the season it completed."""

    reason = _season_pack_reason(
        _candidate([1]),
        complete_seasons=frozenset({1}),
        seasons_with_missing_episodes=frozenset({2}),
    )

    assert reason == "第 1 季本地没有缺集"


def test_a_multi_season_pack_is_left_to_the_existing_rules() -> None:
    assert (
        _season_pack_reason(
            _candidate([1, 2]),
            complete_seasons=frozenset({1}),
            seasons_with_missing_episodes=frozenset({1}),
        )
        is None
    )


def test_a_pack_with_no_season_label_is_left_to_the_existing_rules() -> None:
    assert (
        _season_pack_reason(
            _candidate([]),
            complete_seasons=frozenset({1}),
            seasons_with_missing_episodes=frozenset({1}),
        )
        is None
    )


def test_unknown_season_data_never_rejects_a_pack() -> None:
    """An entry identified before seasons were tracked must keep working."""

    assert (
        _season_pack_reason(
            _candidate([1]),
            complete_seasons=None,
            seasons_with_missing_episodes=None,
        )
        is None
    )


def test_season_zero_specials_are_ignored() -> None:
    assert (
        _season_pack_reason(
            _candidate([0]),
            complete_seasons=frozenset({1}),
            seasons_with_missing_episodes=frozenset({1}),
        )
        is None
    )


# ---------------------------------------------------------------------------
# Reading the context out of the database
# ---------------------------------------------------------------------------


async def _series(session, *, tmdb_id: int) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"season-{tmdb_id}",
        media_type=MediaType.TV,
        tmdb_id=tmdb_id,
        title=f"Series {tmdb_id}",
        year=2026,
        state=MediaState.READY,
    )
    session.add(media)
    await session.flush()
    return media


@pytest.mark.asyncio
async def test_the_context_reports_complete_seasons_and_missing_episodes(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = await _series(session, tmdb_id=900)
        session.add_all(
            [
                MediaSeason(media_id=media.id, season_number=1, is_complete=True),
                MediaSeason(media_id=media.id, season_number=2, is_complete=False),
                Episode(
                    media_id=media.id,
                    season_number=2,
                    episode_number=3,
                    state=EpisodeState.MISSING,
                ),
                Episode(
                    media_id=media.id,
                    season_number=1,
                    episode_number=1,
                    state=EpisodeState.AVAILABLE,
                ),
            ]
        )
        await session.commit()

        complete, missing = await _season_context(session, media)

        assert complete == frozenset({1})
        assert missing == frozenset({2})


@pytest.mark.asyncio
async def test_a_movie_has_no_season_context(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="season-movie",
            media_type=MediaType.MOVIE,
            tmdb_id=901,
            title="Movie",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()

        assert await _season_context(session, media) == (None, None)


@pytest.mark.asyncio
async def test_a_series_with_no_season_rows_reports_unknown(session_factory) -> None:
    async with session_factory() as session:
        media = await _series(session, tmdb_id=902)
        await session.commit()

        complete, missing = await _season_context(session, media)

        assert complete is None
        assert missing is None


# ---------------------------------------------------------------------------
# Persisting what the provider reported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_season_rows_are_created_from_the_provider(session_factory) -> None:
    async with session_factory() as session:
        media = await _series(session, tmdb_id=903)

        await _sync_media_seasons(
            session,
            media.id,
            [
                SeasonRecord(
                    season_number=1,
                    episode_count=10,
                    aired_episode_count=10,
                    last_air_date=date(2026, 3, 1),
                    is_complete=True,
                ),
                SeasonRecord(
                    season_number=2, episode_count=10, aired_episode_count=4, is_complete=False
                ),
            ],
        )
        await session.commit()

        rows = {
            row.season_number: row
            for row in await session.scalars(select(MediaSeason))
        }
        assert rows[1].is_complete is True
        assert rows[1].aired_episode_count == 10
        assert rows[1].last_air_date is not None
        assert rows[2].is_complete is False


@pytest.mark.asyncio
async def test_re_identifying_updates_rather_than_duplicates(session_factory) -> None:
    async with session_factory() as session:
        media = await _series(session, tmdb_id=904)
        await _sync_media_seasons(
            session,
            media.id,
            [SeasonRecord(season_number=1, episode_count=10, aired_episode_count=4)],
        )
        await session.commit()

        await _sync_media_seasons(
            session,
            media.id,
            [
                SeasonRecord(
                    season_number=1, episode_count=10, aired_episode_count=10, is_complete=True
                )
            ],
        )
        await session.commit()

        rows = list(await session.scalars(select(MediaSeason)))
        assert len(rows) == 1
        assert rows[0].is_complete is True


@pytest.mark.asyncio
async def test_a_season_the_provider_dropped_is_removed(session_factory) -> None:
    """A stale "complete" row would keep authorising packs for a dead season."""

    async with session_factory() as session:
        media = await _series(session, tmdb_id=905)
        await _sync_media_seasons(
            session,
            media.id,
            [
                SeasonRecord(season_number=1, is_complete=True),
                SeasonRecord(season_number=2, is_complete=True),
            ],
        )
        await session.commit()

        await _sync_media_seasons(
            session, media.id, [SeasonRecord(season_number=1, is_complete=True)]
        )
        await session.commit()

        rows = list(await session.scalars(select(MediaSeason)))
        assert [row.season_number for row in rows] == [1]


# ---------------------------------------------------------------------------
# Deciding "finished" from TMDB air dates
# ---------------------------------------------------------------------------


def _provider() -> TmdbProvider:
    return TmdbProvider(
        access_token="token",
        today=lambda: date(2026, 9, 8),
    )


def test_a_season_whose_episodes_have_all_aired_is_complete() -> None:
    summary = _provider()._season_summary(
        1,
        [
            TmdbEpisode(episode_number=1, air_date="2026-01-01"),
            TmdbEpisode(episode_number=2, air_date="2026-01-08"),
        ],
    )

    assert summary.is_complete is True
    assert summary.aired_episode_count == 2
    assert summary.last_air_date == date(2026, 1, 8)


def test_a_season_with_an_episode_still_to_come_is_not_complete() -> None:
    summary = _provider()._season_summary(
        2,
        [
            TmdbEpisode(episode_number=1, air_date="2026-09-01"),
            TmdbEpisode(episode_number=2, air_date="2026-09-15"),
        ],
    )

    assert summary.is_complete is False
    assert summary.aired_episode_count == 1


def test_an_episode_with_no_air_date_counts_as_not_yet_aired() -> None:
    """TMDB leaves the date blank for episodes that are not scheduled."""

    summary = _provider()._season_summary(
        3,
        [
            TmdbEpisode(episode_number=1, air_date="2026-01-01"),
            TmdbEpisode(episode_number=2, air_date=None),
        ],
    )

    assert summary.is_complete is False


def test_a_season_listing_no_episodes_is_unknown_not_complete() -> None:
    summary = _provider()._season_summary(4, [])

    assert summary.is_complete is False
    assert summary.episode_count == 0


def test_an_episode_airing_today_counts_as_aired() -> None:
    summary = _provider()._season_summary(
        5, [TmdbEpisode(episode_number=1, air_date="2026-09-08")]
    )

    assert summary.is_complete is True


def test_a_malformed_air_date_does_not_crash_the_summary() -> None:
    summary = _provider()._season_summary(
        6, [TmdbEpisode(episode_number=1, air_date="not-a-date")]
    )

    assert summary.is_complete is False
    assert summary.aired_episode_count == 0

