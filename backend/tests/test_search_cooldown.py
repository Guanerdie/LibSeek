"""Test search cooldown backoff logic."""

from datetime import timedelta

import pytest

from app.simple.automation import _record_search_outcome, _search_cooldown
from app.simple.models import LibraryMediaItem, MediaType


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
