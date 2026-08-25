from __future__ import annotations

import pytest

from app.core.episodes import (
    derive_missing_episode_codes,
    normalize_episode_codes,
    normalize_episode_matrix,
)


def test_long_running_episode_numbers_are_preserved_across_normalization() -> None:
    assert normalize_episode_matrix({1: [1, 1473, "E1473"]}) == {
        1: [1, 1473]
    }
    assert normalize_episode_codes(["S01E1473"]) == ["S01E1473"]
    assert derive_missing_episode_codes(
        {1: [1472, 1473]},
        {1: [1472]},
        None,
    ) == ["S01E1473"]


def test_episode_numbers_still_have_a_defensive_upper_bound() -> None:
    with pytest.raises(ValueError, match="outside the supported range"):
        normalize_episode_matrix({1: [100_000]})
