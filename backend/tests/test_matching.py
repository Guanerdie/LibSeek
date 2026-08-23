from __future__ import annotations

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


def test_a_single_season_pack_does_not_cover_missing_episodes_across_seasons() -> None:
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

    assert "PARTIAL_PACK" in scored.warnings
    assert "SEASON_PACK_COVERS_TARGET_SEASON" not in scored.match_reasons
