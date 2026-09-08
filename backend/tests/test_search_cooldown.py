"""Test search cooldown backoff logic."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.simple.automation import _record_search_outcome, _search_cooldown
from app.simple.models import AutomationPolicy, LibraryMediaItem, MediaType
from app.simple.schemas import AutomationPolicyUpdate


def test_search_cooldown_progression():
    """Cooldown increases: 1 day → 3 days → 7 days (cap)."""
    assert _search_cooldown(0) == timedelta(0)
    assert _search_cooldown(1) == timedelta(days=1)
    assert _search_cooldown(2) == timedelta(days=3)
    assert _search_cooldown(3) == timedelta(days=7)
    assert _search_cooldown(99) == timedelta(days=7)


def test_record_search_outcome_miss():
    """Miss increments counter and sets next_search_at."""
    media = LibraryMediaItem(
        id="test-1",
        tmdb_id=12345,
        media_type=MediaType.MOVIE,
        title="Test Movie",
        year=2025,
        search_miss_count=0,
    )

    _record_search_outcome(media, found_candidate=False)
    assert media.search_miss_count == 1
    assert media.last_searched_at is not None
    assert media.next_search_at is not None
    first_cooldown = media.next_search_at - media.last_searched_at
    assert first_cooldown == timedelta(days=1)

    _record_search_outcome(media, found_candidate=False)
    assert media.search_miss_count == 2
    second_cooldown = media.next_search_at - media.last_searched_at
    assert second_cooldown == timedelta(days=3)


def test_record_search_outcome_found():
    """Finding a candidate resets miss count and clears cooldown."""
    media = LibraryMediaItem(
        id="test-2",
        tmdb_id=67890,
        media_type=MediaType.TV,
        title="Test Show",
        year=2024,
        search_miss_count=5,
    )

    _record_search_outcome(media, found_candidate=True)
    assert media.search_miss_count == 0
    assert media.last_searched_at is not None
    assert media.next_search_at is None


def _policy(*, tier_1: int, tier_2: int, tier_3: int) -> AutomationPolicy:
    return AutomationPolicy(
        id="test",
        cooldown_tier_1_hours=tier_1,
        cooldown_tier_2_hours=tier_2,
        cooldown_tier_3_hours=tier_3,
    )


def test_the_policy_tiers_replace_the_hard_coded_ladder():
    policy = _policy(tier_1=6, tier_2=24, tier_3=72)

    assert _search_cooldown(0, policy) == timedelta(0)
    assert _search_cooldown(1, policy) == timedelta(hours=6)
    assert _search_cooldown(2, policy) == timedelta(hours=24)
    assert _search_cooldown(3, policy) == timedelta(hours=72)
    assert _search_cooldown(99, policy) == timedelta(hours=72)


def test_the_default_policy_reproduces_the_original_ladder():
    """An existing deployment must not change pace just because it upgraded."""

    policy = _policy(tier_1=24, tier_2=72, tier_3=168)

    assert _search_cooldown(1, policy) == timedelta(days=1)
    assert _search_cooldown(2, policy) == timedelta(days=3)
    assert _search_cooldown(3, policy) == timedelta(days=7)


def test_without_a_policy_the_module_defaults_still_apply():
    assert _search_cooldown(1) == timedelta(days=1)
    assert _search_cooldown(3) == timedelta(days=7)


def test_record_search_outcome_uses_the_policy_tiers():
    media = LibraryMediaItem(
        id="test-3",
        tmdb_id=222,
        media_type=MediaType.MOVIE,
        title="Configurable",
        year=2026,
        search_miss_count=0,
    )
    policy = _policy(tier_1=6, tier_2=24, tier_3=72)

    _record_search_outcome(media, found_candidate=False, policy=policy)

    assert media.next_search_at is not None
    assert media.last_searched_at is not None
    assert media.next_search_at - media.last_searched_at == timedelta(hours=6)


def test_a_decreasing_ladder_is_rejected_by_the_schema():
    """Backoff running backwards would retry the second miss sooner."""

    with pytest.raises(ValidationError):
        AutomationPolicyUpdate(
            cooldown_tier_1_hours=72,
            cooldown_tier_2_hours=24,
            cooldown_tier_3_hours=168,
        )


def test_a_flat_ladder_is_allowed():
    policy = AutomationPolicyUpdate(
        cooldown_tier_1_hours=24,
        cooldown_tier_2_hours=24,
        cooldown_tier_3_hours=24,
    )

    assert policy.cooldown_tier_3_hours == 24
