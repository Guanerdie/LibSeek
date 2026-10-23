from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.enums import MediaType
from app.simple.automation_coverage import (
    candidate_adds_missing_content,
    evaluate_download_coverage,
    existing_download_coverage,
)
from app.simple.models import (
    Download,
    DownloadCleanupState,
    DownloadState,
    Episode,
    EpisodeState,
    LibraryMediaItem,
    MediaSeason,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
)


def media(media_type: MediaType = MediaType.TV) -> LibraryMediaItem:
    identity = str(uuid4())
    return LibraryMediaItem(
        id=identity,
        source_item_id=identity,
        title="Coverage series",
        media_type=media_type,
        state=MediaState.READY,
    )


def candidate(
    *, seasons: list[int] | None = None, episodes: list[str] | None = None
) -> ReleaseCandidate:
    return ReleaseCandidate(
        id=str(uuid4()),
        search_id=str(uuid4()),
        site_id="avistaz",
        torrent_id=str(uuid4()),
        title="Coverage series",
        season_coverage=seasons or [],
        episode_coverage=episodes or [],
        score=1,
    )


def download(
    item: LibraryMediaItem,
    release: ReleaseCandidate | None = None,
    state: DownloadState = DownloadState.COMPLETED,
    cleanup: DownloadCleanupState = DownloadCleanupState.NONE,
) -> Download:
    return Download(
        id=str(uuid4()),
        media_id=item.id,
        candidate_id=release.id if release else str(uuid4()),
        name=item.title,
        state=state,
        cleanup_state=cleanup,
    )


def episode(
    item: LibraryMediaItem,
    season: int,
    number: int,
    state: EpisodeState = EpisodeState.MISSING,
) -> Episode:
    return Episode(media_id=item.id, season_number=season, episode_number=number, state=state)


@pytest.mark.parametrize(
    "state",
    [
        DownloadState.QUEUED,
        DownloadState.DOWNLOADING,
        DownloadState.PAUSED,
        DownloadState.COMPLETED,
        DownloadState.SEEDING,
    ],
)
def test_movie_valid_download_skips_without_searching(state):
    item = media(MediaType.MOVIE)
    coverage = evaluate_download_coverage(item, [(download(item, state=state), None)])
    assert coverage is not None and coverage.skip
    assert coverage.waiting_for_library == (
        state in {DownloadState.COMPLETED, DownloadState.SEEDING}
    )


@pytest.mark.parametrize(
    "state,cleanup",
    [
        (DownloadState.ERROR, DownloadCleanupState.NONE),
        (DownloadState.COMPLETED, DownloadCleanupState.DELETED),
        (DownloadState.COMPLETED, DownloadCleanupState.VANISHED),
    ],
)
def test_failed_or_removed_download_does_not_suppress_search(state, cleanup):
    item = media(MediaType.MOVIE)
    assert (
        evaluate_download_coverage(item, [(download(item, state=state, cleanup=cleanup), None)])
        is None
    )


@pytest.mark.parametrize("state", [DownloadState.SUBMITTING, DownloadState.OUTCOME_UNKNOWN])
def test_uncertain_submission_waits_for_reconciliation_even_for_new_season(state):
    item = media()
    release = candidate(seasons=[1])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release, state), release)],
        episodes=[episode(item, 2, 1)],
    )
    assert coverage is not None and coverage.skip
    assert coverage.awaiting_confirmation
    assert not coverage.waiting_for_library
    assert coverage.uncovered_episode_codes == {"S02E01"}


@pytest.mark.parametrize("seasons", [[], [1]])
def test_legacy_completed_series_without_missing_metadata_waits_for_import(seasons):
    item = media()
    release = candidate(seasons=seasons)
    coverage = evaluate_download_coverage(item, [(download(item, release), release)])
    assert coverage is not None and coverage.skip
    assert coverage.waiting_for_library
    assert "NextFind" in coverage.reason


def test_completed_season_does_not_hide_new_season():
    item = media()
    release = candidate(seasons=[1])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release), release)],
        episodes=[episode(item, 1, 4), episode(item, 2, 1)],
    )
    assert coverage is not None and not coverage.skip
    assert coverage.covered_seasons == {1}
    assert coverage.uncovered_episode_codes == {"S02E01"}
    assert not candidate_adds_missing_content(candidate(seasons=[1]), coverage)
    assert candidate_adds_missing_content(candidate(seasons=[2]), coverage)
    assert candidate_adds_missing_content(candidate(seasons=[1, 2]), coverage)
    assert not candidate_adds_missing_content(candidate(seasons=[3]), coverage)


@pytest.mark.parametrize(
    "state", [DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED]
)
def test_active_transfer_temporarily_defers_new_season_until_finished(state):
    item = media()
    release = candidate(seasons=[1])
    existing = download(item, release, state)
    missing = [episode(item, 2, 1)]
    coverage = evaluate_download_coverage(item, [(existing, release)], episodes=missing)
    assert coverage is not None and coverage.skip
    assert not coverage.waiting_for_library
    assert coverage.uncovered_episode_codes == {"S02E01"}
    assert "进行中" in coverage.reason
    for finished_state in (DownloadState.COMPLETED, DownloadState.SEEDING):
        existing.state = finished_state
        coverage = evaluate_download_coverage(item, [(existing, release)], episodes=missing)
        assert coverage is not None and not coverage.skip
        assert coverage.uncovered_episode_codes == {"S02E01"}


def test_download_union_covers_missing_content_and_finished_only_status_is_accurate():
    item = media()
    old = candidate(seasons=[1])
    new = candidate(episodes=["S02E01"])
    downloads = [(download(item, old), old), (download(item, new, DownloadState.DOWNLOADING), new)]
    missing = [episode(item, 1, 4), episode(item, 2, 1)]
    coverage = evaluate_download_coverage(item, downloads, episodes=missing)
    assert coverage is not None and coverage.skip
    assert not coverage.waiting_for_library
    downloads[1][0].state = DownloadState.COMPLETED
    coverage = evaluate_download_coverage(item, downloads, episodes=missing)
    assert coverage is not None and coverage.waiting_for_library


def test_single_episode_never_covers_other_episodes_in_same_season():
    item = media()
    release = candidate(seasons=[1], episodes=["s01e1"])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release), release)],
        episodes=[episode(item, 1, 1), episode(item, 1, 2)],
    )
    assert coverage is not None and not coverage.skip
    assert coverage.covered_seasons == set()
    assert coverage.covered_episode_codes == {"S01E01"}
    assert coverage.uncovered_episode_codes == {"S01E02"}
    assert not candidate_adds_missing_content(candidate(episodes=["S01E01"]), coverage)
    assert candidate_adds_missing_content(candidate(episodes=["S01E02"]), coverage)
    assert not candidate_adds_missing_content(candidate(episodes=["S01E03"]), coverage)
    assert candidate_adds_missing_content(candidate(seasons=[1]), coverage)


def test_unknown_or_malformed_coverage_does_not_hide_explicit_new_missing_episode():
    item = media()
    for release in [candidate(), candidate(seasons=[1], episodes=["invalid"])]:
        release.title = "Coverage Complete Series"
        coverage = evaluate_download_coverage(
            item, [(download(item, release), release)], episodes=[episode(item, 1, 2)]
        )
        assert coverage is not None and not coverage.skip
        assert not coverage.covered_seasons


def test_available_episodes_do_not_count_as_missing_but_downloading_ones_do():
    item = media()
    release = candidate(episodes=["S01E02"])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release), release)],
        episodes=[
            episode(item, 1, 1, EpisodeState.AVAILABLE),
            episode(item, 1, 2, EpisodeState.DOWNLOADING),
        ],
    )
    assert coverage is not None and coverage.skip
    assert coverage.missing_episode_codes == {"S01E02"}


def test_known_new_season_without_episode_rows_reopens_search():
    item = media()
    release = candidate(seasons=[1])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release), release)],
        seasons=[
            MediaSeason(
                media_id=item.id,
                season_number=1,
                is_complete=True,
                episode_count=10,
                aired_episode_count=10,
            ),
            MediaSeason(
                media_id=item.id,
                season_number=2,
                is_complete=True,
                episode_count=10,
                aired_episode_count=10,
            ),
        ],
    )
    assert coverage is not None and not coverage.skip
    assert coverage.uncovered_seasons == {2}
    assert candidate_adds_missing_content(candidate(seasons=[2]), coverage)
    assert not candidate_adds_missing_content(candidate(seasons=[1]), coverage)


def test_unaired_future_season_does_not_reopen_fulfilled_missing_content():
    item = media()
    release = candidate(seasons=[1])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release), release)],
        episodes=[episode(item, 1, 1)],
        seasons=[
            MediaSeason(
                media_id=item.id,
                season_number=2,
                is_complete=False,
                episode_count=10,
                aired_episode_count=0,
            )
        ],
    )
    assert coverage is not None and coverage.skip


def test_old_pack_does_not_cover_new_episodes_of_unfinished_season():
    item = media()
    release = candidate(seasons=[1])
    coverage = evaluate_download_coverage(
        item,
        [(download(item, release), release)],
        episodes=[episode(item, 1, 5)],
        seasons=[
            MediaSeason(
                media_id=item.id,
                season_number=1,
                is_complete=False,
                episode_count=10,
                aired_episode_count=5,
            )
        ],
    )
    assert coverage is not None and not coverage.skip
    assert not coverage.covered_seasons


@pytest.mark.parametrize("complete", [False, True])
def test_individual_downloads_can_cover_known_season_without_episode_rows(complete):
    item = media()
    releases = [candidate(episodes=["S01E01"]), candidate(episodes=["S01E02"])]
    downloads = [(download(item, release), release) for release in releases]
    seasons = [
        MediaSeason(
            media_id=item.id,
            season_number=1,
            is_complete=complete,
            episode_count=2 if complete else 10,
            aired_episode_count=2,
        )
    ]
    coverage = evaluate_download_coverage(item, downloads, seasons=seasons)
    assert coverage is not None and coverage.skip and coverage.waiting_for_library
    assert not coverage.covered_seasons
    assert not coverage.uncovered_seasons
    seasons[0].aired_episode_count = 3
    seasons[0].episode_count = 3
    coverage = evaluate_download_coverage(item, downloads, seasons=seasons)
    assert coverage is not None and not coverage.skip
    assert coverage.uncovered_seasons == {1}
    assert candidate_adds_missing_content(candidate(episodes=["S01E03"]), coverage)
    assert not candidate_adds_missing_content(candidate(episodes=["S01E02"]), coverage)


def test_confirmed_library_is_not_reported_as_waiting_for_import():
    item = media(MediaType.MOVIE)
    item.library_confirmed_at = datetime.now(UTC)
    coverage = evaluate_download_coverage(item, [(download(item), None)])
    assert coverage is not None and coverage.skip
    assert not coverage.waiting_for_library


def test_records_for_other_media_are_not_used():
    item, other = media(), media()
    assert evaluate_download_coverage(item, [(download(other), None)]) is None


async def test_batch_loader_returns_partial_coverage_and_omits_failed_downloads(session_factory):
    completed, partial, failed, fresh = media(MediaType.MOVIE), media(), media(), media()
    async with session_factory() as session:
        session.add_all([completed, partial, failed, fresh])
        for item, state in [
            (completed, DownloadState.COMPLETED),
            (partial, DownloadState.COMPLETED),
            (failed, DownloadState.ERROR),
        ]:
            release = candidate(seasons=[1])
            session.add(ReleaseSearch(id=release.search_id, media_id=item.id, site_ids=["avistaz"]))
            session.add(release)
            session.add(download(item, release, state))
        session.add(episode(partial, 2, 1))
        await session.commit()
        result = await existing_download_coverage(session, [completed, partial, failed, fresh])
        assert set(result) == {completed.id, partial.id}
        assert result[completed.id].skip and result[completed.id].waiting_for_library
        assert not result[partial.id].skip
        assert result[partial.id].uncovered_episode_codes == {"S02E01"}


async def test_empty_batch_needs_no_database(session_factory):
    async with session_factory() as session:
        assert await existing_download_coverage(session, []) == {}
