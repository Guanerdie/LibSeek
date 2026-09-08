"""Weekly variety shows must be automatable without drowning in back catalogue.

Grounded in what production actually contained: 127 media items had been
retried 1267 times between them, and the top offenders were all Korean variety
shows.  Radio Star (TMDB 2007, episode 718), Amazing Saturday (81 candidates,
every one a single episode, zero season packs), SNL Korea (134 seeded season
packs scoring up to 0.789, rejected purely on year).
"""

from __future__ import annotations

import pytest

from app.models.enums import MediaType
from app.services.variety import (
    is_ongoing,
    is_variety,
    is_within_latest_window,
    single_episode_number,
)
from app.simple.automation import _choose_candidate
from app.simple.integrations import settled_media_state
from app.simple.models import (
    AutomationPolicy,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
)

REALITY = 10764
TALK = 10767


def _policy(**overrides: object) -> AutomationPolicy:
    values: dict[str, object] = {
        "id": "test",
        "minimum_score": 0.5,
        "minimum_seeders": 1,
        "allow_warnings": False,
        "site_ids": ["avistaz"],
        "automate_variety": False,
        "scope_mode": "filters",
        "regions": [],
        "selected_media_ids": [],
    }
    values.update(overrides)
    return AutomationPolicy(**values)  # type: ignore[arg-type]


def _candidate(
    title: str,
    *,
    score: float = 0.6,
    seeders: int = 10,
    warnings: list[str] | None = None,
    reasons: list[str] | None = None,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        search_id="s1",
        site_id="avistaz",
        torrent_id=title[:20],
        title=title,
        size_bytes=1_000_000,
        seeders=seeders,
        score=score,
        warnings=warnings or [],
        reasons=reasons or ["TMDB_ID_EXACT"],
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_reality_and_talk_genres_are_variety() -> None:
    assert is_variety([REALITY])
    assert is_variety([35, REALITY])
    assert is_variety([TALK])


def test_a_drama_is_not_variety() -> None:
    """Verified against 盗墓王, which was correctly left alone."""

    assert not is_variety([10759, 16, 10765])
    assert not is_variety([])
    assert not is_variety(None)


def test_only_a_running_show_keeps_chasing() -> None:
    assert is_ongoing("Returning Series")
    assert not is_ongoing("Ended")
    assert not is_ongoing("Canceled")
    assert not is_ongoing(None)


# ---------------------------------------------------------------------------
# Episode parsing -- these titles are copied from production
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Radio Star E718 1080p WEB-DL AAC H.264-MMR", 718),
        ("Amazing Saturday E426 720p HDTV AAC H.264-NEXT", 426),
        ("1 Night 2 Days S04E338 1080p VIU WEB-DL AAC 2.0 H.264-MMR", 338),
        ("Amazing Saturday E409 1080p TVING WEB-DL AAC 2.0 x264-AppleTor", 409),
        # A batch of old episodes is not "the latest episode".
        ("Amazing Saturday (2025 BATCH) E348-E398 1080p AMZN WEB-DL", None),
        ("Show E12-E24 1080p", None),
        # Season packs carry no single-episode number.
        ("Saturday Night Live Korea S17 (2026) 1080p CPNG WEB-DL", None),
        ("Radio Star S01 (2026) 1080p NHKP WEB-DL AAC2.0", None),
        ("Some.Drama.S02.COMPLETE.1080p", None),
    ],
)
def test_episode_numbers_are_read_off_real_release_names(
    title: str, expected: int | None
) -> None:
    assert single_episode_number(title) == expected


def test_the_release_group_suffix_is_not_mistaken_for_an_episode() -> None:
    """``x264-AppleTor`` and ``H.264-MMR`` must not parse as episodes."""

    assert single_episode_number("Show S01 1080p WEB-DL AAC 2.0 x264-AppleTor") is None


def test_the_latest_window_covers_exactly_the_newest_n() -> None:
    assert is_within_latest_window(718, 718, 5)
    assert is_within_latest_window(714, 718, 5)
    assert not is_within_latest_window(713, 718, 5)
    # A number beyond the newest is not a real episode.
    assert not is_within_latest_window(719, 718, 5)


def test_a_zero_window_turns_the_feature_off() -> None:
    assert not is_within_latest_window(718, 718, 0)


# ---------------------------------------------------------------------------
# Selection -- the three shows that were stuck, and why
# ---------------------------------------------------------------------------


def test_snl_korea_was_blocked_only_by_the_year() -> None:
    """134 seeded season packs, top score 0.789, all rejected on year alone."""

    candidate = _candidate(
        "Saturday Night Live Korea S17 (2026) 1080p CPNG WEB-DL",
        score=0.789,
        warnings=["YEAR_MISMATCH"],
        reasons=["TITLE_EXACT", "MEDIA_TYPE_MATCH"],
    )

    blocked, _ = _choose_candidate(
        _policy(), [candidate], media_type=MediaType.TV, variety=False
    )
    allowed, _ = _choose_candidate(
        _policy(), [candidate], media_type=MediaType.TV, variety=True
    )

    assert blocked is None
    assert allowed is candidate


def test_amazing_saturday_has_only_single_episodes(
) -> None:
    """81 candidates in production, every one a single episode, no packs."""

    candidates = [
        _candidate(f"Amazing Saturday E{number} 1080p TVING WEB-DL")
        for number in (405, 406, 407, 408, 409)
    ]

    blocked, _ = _choose_candidate(
        _policy(), candidates, media_type=MediaType.TV, variety=False
    )
    picked, _ = _choose_candidate(
        _policy(),
        candidates,
        media_type=MediaType.TV,
        variety=True,
        latest_episode_window=5,
    )

    assert blocked is None
    assert picked is not None
    assert "E409" in picked.title or "E40" in picked.title


def test_the_back_catalogue_is_left_alone() -> None:
    """The whole point of "chase the latest": E100 must not come along."""

    candidates = [
        _candidate("Radio Star E100 1080p WEB-DL"),
        _candidate("Radio Star E717 1080p WEB-DL"),
        _candidate("Radio Star E718 1080p WEB-DL"),
    ]

    _, rejected = _choose_candidate(
        _policy(),
        candidates,
        media_type=MediaType.TV,
        variety=True,
        latest_episode_window=3,
    )

    old = [item for item in rejected if "E100" in str(item["title"])]
    assert old, "the old episode should have been rejected"
    assert any("不在最新" in reason for reason in old[0]["reasons"])  # type: ignore[union-attr]


def test_a_batch_of_old_episodes_is_not_accepted() -> None:
    candidates = [_candidate("Amazing Saturday (2025 BATCH) E348-E398 1080p AMZN WEB-DL")]

    picked, rejected = _choose_candidate(
        _policy(),
        candidates,
        media_type=MediaType.TV,
        variety=True,
        latest_episode_window=5,
    )

    assert picked is None
    assert any("既不是整季包也不是单集" in r for r in rejected[0]["reasons"])  # type: ignore[union-attr]


def test_a_drama_still_requires_a_complete_pack() -> None:
    """The relaxation must not leak into ordinary series."""

    candidates = [_candidate("Some Drama S02E05 1080p WEB-DL")]

    picked, rejected = _choose_candidate(
        _policy(), candidates, media_type=MediaType.TV, variety=False
    )

    assert picked is None
    assert any("整季包" in r for r in rejected[0]["reasons"])  # type: ignore[union-attr]


def test_a_variety_season_pack_is_still_preferred_when_one_exists() -> None:
    """Radio Star does have season packs; they should not be shut out."""

    pack = _candidate("Radio Star S01 (2026) 1080p NHKP WEB-DL", score=0.692)

    picked, _ = _choose_candidate(
        _policy(),
        [pack],
        media_type=MediaType.TV,
        variety=True,
        latest_episode_window=5,
    )

    assert picked is pack


def test_the_score_threshold_still_applies_to_variety() -> None:
    """Relaxing pack and year rules must not relax everything."""

    candidate = _candidate("Radio Star E718 1080p WEB-DL", score=0.2)

    picked, rejected = _choose_candidate(
        _policy(minimum_score=0.5),
        [candidate],
        media_type=MediaType.TV,
        variety=True,
        latest_episode_window=5,
    )

    assert picked is None
    assert any("评分低于策略门槛" in r for r in rejected[0]["reasons"])  # type: ignore[union-attr]


def test_a_dead_torrent_is_still_refused_for_variety() -> None:
    candidate = _candidate("Radio Star E718 1080p WEB-DL", seeders=0)

    picked, _ = _choose_candidate(
        _policy(),
        [candidate],
        media_type=MediaType.TV,
        variety=True,
        latest_episode_window=5,
    )

    assert picked is None


# ---------------------------------------------------------------------------
# Staying in the queue
# ---------------------------------------------------------------------------


def _media(*, genres: list[int], status: str | None) -> LibraryMediaItem:
    return LibraryMediaItem(
        source_item_id="variety-1",
        media_type=MediaType.TV,
        tmdb_id=65270,
        title="Radio Star",
        year=2007,
        genre_ids=genres,
        tmdb_status=status,
        state=MediaState.DOWNLOADING,
    )


def test_an_airing_variety_show_returns_to_the_queue() -> None:
    """Parking it at COMPLETE would end the chase after one episode."""

    media = _media(genres=[TALK], status="Returning Series")

    assert settled_media_state(media) == MediaState.READY


def test_a_finished_variety_show_is_complete() -> None:
    media = _media(genres=[REALITY], status="Canceled")

    assert settled_media_state(media) == MediaState.COMPLETE


def test_an_ordinary_series_is_complete() -> None:
    media = _media(genres=[18], status="Returning Series")

    assert settled_media_state(media) == MediaState.COMPLETE


# ---------------------------------------------------------------------------
# The switch: variety shows are out of automation unless asked for
# ---------------------------------------------------------------------------


def _library_item(*, genres: list[int]) -> LibraryMediaItem:
    return LibraryMediaItem(
        source_item_id="scope-1",
        media_type=MediaType.TV,
        tmdb_id=1,
        title="Show",
        year=2026,
        genre_ids=genres,
        state=MediaState.READY,
    )


def test_a_variety_show_is_out_of_scope_by_default() -> None:
    from app.simple.automation import _media_in_policy_scope

    policy = _policy()

    assert policy.automate_variety is False
    assert not _media_in_policy_scope(policy, _library_item(genres=[REALITY]))


def test_an_ordinary_series_is_unaffected_by_the_switch() -> None:
    from app.simple.automation import _media_in_policy_scope

    assert _media_in_policy_scope(_policy(), _library_item(genres=[18]))


def test_turning_the_switch_on_lets_variety_back_in() -> None:
    from app.simple.automation import _media_in_policy_scope

    policy = _policy(automate_variety=True)

    assert _media_in_policy_scope(policy, _library_item(genres=[TALK]))


def test_manual_selection_cannot_smuggle_a_variety_show_past_the_switch() -> None:
    """The check runs before scope, so an explicit pick does not override it."""

    from app.simple.automation import _media_in_policy_scope

    item = _library_item(genres=[REALITY])
    policy = _policy(scope_mode="selected", selected_media_ids=[item.id])

    assert not _media_in_policy_scope(policy, item)
