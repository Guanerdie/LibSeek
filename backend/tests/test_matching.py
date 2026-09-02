from __future__ import annotations

import pytest

from app.models.enums import MediaType
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.services.matching import MatchPreferences, score_torrent_candidate


def candidate(title: str, **values: object) -> TorrentCandidate:
    torrent_id = str(values.pop("torrent_id", "candidate"))
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id=torrent_id,
        release_title=title,
        details_ref=f"avistaz:details:{torrent_id}",
        media_type=MediaType.MOVIE,
        year=2012,
        seeders=3,
        hit_and_run=False,
        **values,
    )


def test_title_fallback_requires_a_complete_title_boundary() -> None:
    metadata = MetadataRecord(
        tmdb_id=10,
        media_type=MediaType.MOVIE,
        title="快乐的结局",
        english_title="Happy Ending",
        aliases=["해피 엔딩"],
        year=2012,
    )

    exact = score_torrent_candidate(
        metadata,
        candidate("Happy.Ending.2012.1080p.WEB-DL"),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )
    longer_title = score_torrent_candidate(
        metadata,
        candidate("My.Happy.Ending.2012.1080p.WEB-DL", torrent_id="longer"),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )
    suffixed_title = score_torrent_candidate(
        metadata,
        candidate("Happy.Ending.Romance.2012.1080p.WEB-DL", torrent_id="suffix"),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )

    assert "TITLE_EXACT" in exact.match_reasons
    assert "TITLE_EXACT" not in longer_title.match_reasons
    assert "TITLE_EXACT" not in suffixed_title.match_reasons


def test_every_available_external_identity_must_be_consistent() -> None:
    metadata = MetadataRecord(
        tmdb_id=10,
        imdb_id="tt0010",
        media_type=MediaType.MOVIE,
        title="Identity Movie",
        year=2012,
    )
    scored = score_torrent_candidate(
        metadata,
        candidate(
            "Identity.Movie.2012.1080p.WEB-DL",
            tmdb_id=10,
            imdb_id="tt9999",
        ),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )

    assert "TMDB_ID_EXACT" in scored.match_reasons
    assert "ID_MISMATCH" in scored.warnings


def test_an_unverifiable_site_identity_is_not_silently_accepted() -> None:
    metadata = MetadataRecord(
        tmdb_id=10,
        media_type=MediaType.MOVIE,
        title="Identity Movie",
        year=2012,
    )
    scored = score_torrent_candidate(
        metadata,
        candidate("Identity.Movie.2012.1080p.WEB-DL", imdb_id="tt0010"),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )

    assert "ID_UNVERIFIED" in scored.warnings


def test_a_complete_season_pack_does_not_depend_on_local_missing_episodes() -> None:
    metadata = MetadataRecord(
        tmdb_id=10,
        media_type=MediaType.TV,
        title="Series",
        year=2012,
    )
    scored = score_torrent_candidate(
        metadata,
        TorrentCandidate(
            site_id="avistaz",
            torrent_id="single-season",
            release_title="Series.S01.2012.1080p.WEB-DL",
            details_ref="avistaz:details:single-season",
            media_type=MediaType.TV,
            tmdb_id=10,
            year=2012,
            season=1,
            collection_type="season",
            seeders=3,
            hit_and_run=False,
        ),
        missing_episodes=["S01E01", "S02E01"],
        preferences=MatchPreferences(),
    )

    assert "TV_COMPLETE_SEASON_PACK" in scored.match_reasons
    assert "PARTIAL_PACK" not in scored.warnings
    assert "TV_PACK_UNVERIFIED" not in scored.warnings


def test_a_complete_season_title_is_not_mistaken_for_a_complete_series() -> None:
    metadata = MetadataRecord(
        tmdb_id=11,
        media_type=MediaType.TV,
        title="Series",
        year=2012,
    )
    scored = score_torrent_candidate(
        metadata,
        TorrentCandidate(
            site_id="avistaz",
            torrent_id="complete-season-title",
            release_title="Series.Complete.Season.1.2012.1080p.WEB-DL",
            details_ref="avistaz:details:complete-season-title",
            media_type=MediaType.TV,
            tmdb_id=11,
            year=2012,
            season=1,
            seeders=3,
            hit_and_run=False,
        ),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )

    assert "TV_COMPLETE_SEASON_PACK" in scored.match_reasons
    assert "TV_COMPLETE_SERIES_PACK" not in scored.match_reasons


def test_a_single_episode_is_not_an_automatic_replacement_pack() -> None:
    metadata = MetadataRecord(
        tmdb_id=23,
        media_type=MediaType.TV,
        title="Long Runner",
        year=1992,
        episode_matrix={1: [1473]},
    )
    scored = score_torrent_candidate(
        metadata,
        TorrentCandidate(
            site_id="avistaz",
            torrent_id="episode-1473",
            release_title="Long.Runner.S01E1473.1080p.WEB-DL",
            details_ref="avistaz:details:episode-1473",
            media_type=MediaType.TV,
            tmdb_id=23,
            season=1,
            episodes=[1473],
            collection_type="season",
            seeders=3,
            hit_and_run=False,
        ),
        missing_episodes=["S01E1473"],
        preferences=MatchPreferences(),
    )

    assert "TV_COMPLETE_SEASON_PACK" not in scored.match_reasons
    assert "TV_COMPLETE_SERIES_PACK" not in scored.match_reasons
    assert "TV_PACK_UNVERIFIED" in scored.warnings


@pytest.mark.parametrize(
    ("title", "episodes", "collection_type", "file_count"),
    [
        ("Series.S01.E01.1080p.WEB-DL", None, "season", 2),
        ("Series.S01.Special.1080p.WEB-DL", None, "season", 2),
        ("Series.S01E01-E02.1080p.WEB-DL", [1, 2], "season", 2),
        ("Series.S01.1080p.WEB-DL", None, "season", 1),
        ("Series.Complete.Series.Trailer.1080p.WEB-DL", None, "complete_series", 12),
        ("Series.Incomplete.Season.1.1080p.WEB-DL", None, "season", 12),
    ],
)
def test_an_ambiguous_tv_release_is_not_treated_as_a_complete_pack(
    title: str,
    episodes: list[int] | None,
    collection_type: str,
    file_count: int,
) -> None:
    metadata = MetadataRecord(
        tmdb_id=25,
        media_type=MediaType.TV,
        title="Series",
        year=2020,
    )
    scored = score_torrent_candidate(
        metadata,
        TorrentCandidate(
            site_id="avistaz",
            torrent_id=f"ambiguous-{file_count}-{len(episodes or [])}-{title}",
            release_title=title,
            details_ref="avistaz:details:ambiguous-tv-release",
            media_type=MediaType.TV,
            tmdb_id=25,
            year=2020,
            season=1,
            episodes=episodes,
            collection_type=collection_type,
            file_count=file_count,
            seeders=3,
            hit_and_run=False,
        ),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )

    assert "TV_COMPLETE_SEASON_PACK" not in scored.match_reasons
    assert "TV_COMPLETE_SERIES_PACK" not in scored.match_reasons
    assert "TV_PACK_UNVERIFIED" in scored.warnings


def test_a_complete_series_title_is_recognized_as_a_replacement_pack() -> None:
    metadata = MetadataRecord(
        tmdb_id=24,
        media_type=MediaType.TV,
        title="Finished Series",
        year=2020,
    )
    scored = score_torrent_candidate(
        metadata,
        TorrentCandidate(
            site_id="avistaz",
            torrent_id="complete-series",
            release_title="Finished.Series.Complete.S01-S03.1080p.WEB-DL",
            details_ref="avistaz:details:complete-series",
            media_type=MediaType.TV,
            tmdb_id=24,
            year=2020,
            seeders=3,
            hit_and_run=False,
        ),
        missing_episodes=None,
        preferences=MatchPreferences(),
    )

    assert "TV_COMPLETE_SERIES_PACK" in scored.match_reasons
    assert "TV_PACK_UNVERIFIED" not in scored.warnings
