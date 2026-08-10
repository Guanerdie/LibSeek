from datetime import UTC, datetime

from app.models.entities import MediaItem
from app.models.enums import IdentityConfidence, MediaType, MetadataStatus
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.services.matching import (
    MatchPreferences,
    score_metadata_match,
    score_torrent_candidate,
)


def media_item() -> MediaItem:
    now = datetime.now(UTC)
    return MediaItem(
        source="nextfind",
        source_item_id="nextfind:123",
        media_type=MediaType.TV,
        tmdb_id=123,
        title="中文剧",
        year=2026,
        missing_episodes=["S01E03", "S01E04"],
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )


def metadata() -> MetadataRecord:
    return MetadataRecord(
        tmdb_id=123,
        imdb_id="tt0123",
        media_type=MediaType.TV,
        title="中文剧",
        chinese_title="中文剧",
        english_title="Example Show",
        original_title="Original Show",
        year=2026,
        episode_matrix={1: [1, 2, 3, 4]},
        confidence=1,
    )


def test_metadata_score_explains_exact_and_conflicting_identity() -> None:
    exact = score_metadata_match(media_item(), metadata(), expected_tmdb_id=123)
    assert exact.score == 1
    assert exact.reasons == ["TMDB_ID_EXACT", "MEDIA_TYPE_MATCH", "YEAR_MATCH", "TITLE_EXACT"]
    assert not exact.conflicts

    conflict = metadata().model_copy(update={"media_type": MediaType.MOVIE, "year": 2025})
    result = score_metadata_match(media_item(), conflict, expected_tmdb_id=123)
    assert "MEDIA_TYPE_CONFLICT" in result.conflicts
    assert "YEAR_CONFLICT" in result.conflicts


def test_candidate_score_has_reasons_but_never_auto_approves() -> None:
    candidate = TorrentCandidate(
        site_id="avistaz",
        torrent_id="1",
        release_title="Example Show 2026 S01E03-E04 1080p WEB-DL",
        details_ref="avistaz:details:one",
        media_type=MediaType.TV,
        tmdb_id=123,
        year=2026,
        season=1,
        episodes=[3, 4],
        collection_type="episode",
        resolution="1080p",
        source="WEB-DL",
        subtitles=["Chinese"],
        seeders=10,
        download_factor=0,
        hit_and_run=False,
        size_bytes=1000,
    )
    scored = score_torrent_candidate(
        metadata(),
        candidate,
        missing_episodes=["S01E03", "S01E04"],
        preferences=MatchPreferences(
            resolutions=("1080p",),
            sources=("WEB-DL",),
            subtitles=("Chinese",),
            max_size_bytes=2000,
        ),
    )
    for reason in (
        "TMDB_ID_EXACT",
        "MEDIA_TYPE_MATCH",
        "YEAR_MATCH",
        "SEASON_EXACT",
        "EPISODE_COVERAGE_EXACT",
        "PREFERRED_RESOLUTION",
        "PREFERRED_SUBTITLE",
        "ACTIVE_SEEDERS",
    ):
        assert reason in scored.match_reasons
    assert scored.match_score is not None and scored.match_score <= 1
    assert "approved" not in scored.model_dump_json().casefold()


def test_candidate_score_emits_risk_warnings() -> None:
    risky = TorrentCandidate(
        site_id="avistaz",
        torrent_id="2",
        release_title="Wrong Show 2025 S01E02-E04 2160p",
        details_ref="avistaz:details:two",
        media_type=MediaType.TV,
        tmdb_id=999,
        year=2025,
        season=1,
        episodes=[2, 4],
        size_bytes=5000,
        seeders=0,
        hit_and_run=None,
    )
    scored = score_torrent_candidate(
        metadata(),
        risky,
        missing_episodes=["S01E03", "S01E04"],
        preferences=MatchPreferences(max_size_bytes=2000, possible_duplicate=True),
    )
    for warning in (
        "NO_SEEDERS",
        "ID_MISMATCH",
        "YEAR_MISMATCH",
        "PARTIAL_PACK",
        "HNR_UNKNOWN",
        "EPISODE_OVERLAP",
        "OVERSIZED",
        "POSSIBLE_DUPLICATE",
    ):
        assert warning in scored.warnings


def test_season_pack_is_not_reported_as_exact_episode_coverage() -> None:
    season_pack = TorrentCandidate(
        site_id="avistaz",
        torrent_id="season-pack",
        release_title="Example Show 2026 S01 1080p WEB-DL",
        details_ref="avistaz:details:season-pack",
        media_type=MediaType.TV,
        tmdb_id=123,
        year=2026,
        season=1,
        episodes=None,
        collection_type="season",
        seeders=1,
    )
    scored = score_torrent_candidate(
        metadata(),
        season_pack,
        missing_episodes=["S01E03", "S01E04"],
        preferences=MatchPreferences(),
    )

    assert "EPISODE_COVERAGE_EXACT" not in scored.match_reasons
    assert "SEASON_PACK_COVERS_TARGET_SEASON" in scored.match_reasons
    assert "EPISODE_COVERAGE_UNKNOWN" in scored.warnings
