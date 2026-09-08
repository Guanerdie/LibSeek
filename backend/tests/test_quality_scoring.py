"""Release quality is scored on its own, and by the operator's priorities.

The old score mixed identity with quality, and identity was worth more than
half of it -- so every candidate for one media item scored within a hair of
the others.  Measured on the live library: SNL Korea's 144 candidates spanned
0.329, and 1 Night 2 Days' 285 spanned 0.201.  The number could not rank
versions because it was mostly measuring what they had in common.

The stated priorities these defaults encode: picture quality first, size as
its proxy, at least a few seeders rather than as many as possible, free leech
barely counted.
"""

from __future__ import annotations

import pytest

from app.services.quality import (
    QualityWeights,
    score_release_quality,
    seeder_score,
    size_score,
    source_score,
)

GB = 1024**3


def _score(**overrides: object) -> float:
    base: dict[str, object] = {
        "resolution": "1080p",
        "source": "WEB-DL",
        "size_bytes": 8 * GB,
        "seeders": 10,
        "download_factor": 1.0,
    }
    base.update(overrides)
    return score_release_quality(**base).score  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Picture quality comes first
# ---------------------------------------------------------------------------


def test_a_higher_resolution_wins_all_else_equal() -> None:
    assert _score(resolution="2160p") > _score(resolution="1080p") > _score(resolution="720p")


def test_resolution_outweighs_seeders() -> None:
    """"优先画质" -- a 720p with a crowd loses to a 2160p with a few."""

    plenty_of_seeders = _score(resolution="720p", seeders=200)
    better_picture = _score(resolution="2160p", seeders=5)

    assert better_picture > plenty_of_seeders


def test_4k_web_dl_beats_1080p_bluray() -> None:
    """The weights were tuned until this held: resolution leads source."""

    four_k = _score(resolution="2160p", source="WEB-DL", size_bytes=18 * GB, seeders=8)
    ten_eighty = _score(
        resolution="1080p", source="BluRay", size_bytes=25 * GB, seeders=20, download_factor=0
    )

    assert four_k > ten_eighty


def test_an_unknown_resolution_sits_between_576p_and_720p() -> None:
    """Missing metadata is not punished like 480p, but a known 720p still wins.

    Knowing a release is 720p is worth something; knowing nothing is not.
    """

    assert _score(resolution="576p") < _score(resolution=None) < _score(resolution="720p")


# ---------------------------------------------------------------------------
# Bigger is better, without a cap to guess
# ---------------------------------------------------------------------------


def test_a_bigger_release_always_scores_higher() -> None:
    assert _score(size_bytes=60 * GB) > _score(size_bytes=20 * GB) > _score(size_bytes=2 * GB)


def test_size_keeps_rising_but_with_diminishing_returns() -> None:
    step_low = size_score(10 * GB, reference_bytes=8 * GB) - size_score(
        5 * GB, reference_bytes=8 * GB
    )
    step_high = size_score(105 * GB, reference_bytes=8 * GB) - size_score(
        100 * GB, reference_bytes=8 * GB
    )

    assert step_low > step_high > 0


def test_an_unknown_size_is_not_treated_as_zero() -> None:
    assert size_score(None, reference_bytes=8 * GB) > 0


# ---------------------------------------------------------------------------
# Seeders are a floor, not a race
# ---------------------------------------------------------------------------


def test_reaching_the_floor_is_most_of_the_value() -> None:
    at_floor = seeder_score(3, floor=3)
    far_above = seeder_score(300, floor=3)

    assert at_floor >= 0.7
    # Going from 3 to 300 is worth less than getting from 1 to 3.
    assert far_above - at_floor < at_floor - seeder_score(1, floor=3)


def test_below_the_floor_is_penalised() -> None:
    assert seeder_score(1, floor=3) < seeder_score(2, floor=3) < seeder_score(3, floor=3)


def test_a_dead_torrent_scores_zero_on_seeders() -> None:
    assert seeder_score(0, floor=3) == 0.0


def test_the_floor_is_configurable() -> None:
    weights = QualityWeights(seeder_floor=10)

    assert seeder_score(5, floor=weights.seeder_floor) < seeder_score(5, floor=3)


# ---------------------------------------------------------------------------
# Free leech barely counts
# ---------------------------------------------------------------------------


def test_free_leech_helps_only_a_little() -> None:
    free = _score(download_factor=0)
    paid = _score(download_factor=1)

    assert free > paid
    # Worth less than a single resolution step.
    assert free - paid < _score(resolution="2160p") - _score(resolution="1080p")


# ---------------------------------------------------------------------------
# Source ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        ("Remux", "BluRay"),
        ("BluRay", "WEB-DL"),
        ("WEB-DL", "WEBRip"),
        ("WEBRip", "HDTV"),
    ],
)
def test_the_source_ladder_is_ordered(better: str, worse: str) -> None:
    assert source_score(better) > source_score(worse)


def test_a_compound_source_name_takes_its_best_match() -> None:
    """Release metadata is inconsistent: "Blu-ray Remux" must read as remux."""

    assert source_score("Blu-ray Remux") == source_score("Remux")


# ---------------------------------------------------------------------------
# The scale itself
# ---------------------------------------------------------------------------


def test_the_score_stays_inside_zero_and_one() -> None:
    best = _score(
        resolution="2160p", source="Remux", size_bytes=200 * GB, seeders=500, download_factor=0
    )
    worst = _score(resolution="480p", source="DVDRip", size_bytes=1, seeders=0)

    assert 0.0 <= worst < best <= 1.0


def test_a_top_release_reaches_the_high_end_of_the_scale() -> None:
    """The old formula topped out around 0.79, so "0.8" was unreachable."""

    assert (
        _score(
            resolution="2160p",
            source="Remux",
            size_bytes=80 * GB,
            seeders=40,
            download_factor=0,
        )
        > 0.9
    )


def test_two_releases_of_one_title_actually_separate() -> None:
    """The whole point: the number must rank versions, not confirm identity."""

    best = _score(resolution="2160p", source="Remux", size_bytes=90 * GB, seeders=30)
    worst = _score(resolution="720p", source="HDTV", size_bytes=1 * GB, seeders=4)

    assert best - worst > 0.4


# ---------------------------------------------------------------------------
# Weights are the operator's
# ---------------------------------------------------------------------------


def test_weights_are_relative_and_need_not_add_up() -> None:
    doubled = QualityWeights(
        resolution=88, size=48, source=24, seeders=26, promotion=6, preferences=8
    )

    assert score_release_quality(
        resolution="2160p", source="WEB-DL", size_bytes=8 * GB, seeders=10,
        download_factor=1, weights=doubled,
    ).score == pytest.approx(
        score_release_quality(
            resolution="2160p", source="WEB-DL", size_bytes=8 * GB, seeders=10,
            download_factor=1, weights=QualityWeights(),
        ).score,
        abs=0.01,
    )


def test_zeroing_a_weight_removes_that_component() -> None:
    ignore_seeders = QualityWeights(seeders=0)

    many = score_release_quality(
        resolution="1080p", source="WEB-DL", size_bytes=8 * GB, seeders=200,
        download_factor=1, weights=ignore_seeders,
    ).score
    few = score_release_quality(
        resolution="1080p", source="WEB-DL", size_bytes=8 * GB, seeders=1,
        download_factor=1, weights=ignore_seeders,
    ).score

    assert many == few


def test_all_weights_zero_falls_back_to_the_defaults() -> None:
    """Otherwise every candidate would score identically and nothing could rank."""

    flat = QualityWeights(
        resolution=0, size=0, source=0, seeders=0, promotion=0, preferences=0
    )

    assert flat.normalised() == QualityWeights().normalised()


def test_the_breakdown_explains_the_number() -> None:
    result = score_release_quality(
        resolution="2160p", source="Remux", size_bytes=40 * GB, seeders=12, download_factor=0
    )

    assert result.components["resolution"] == 1.0
    assert result.components["promotion"] == 1.0
    assert 0 < result.components["size"] < 1
    assert set(result.components) == {
        "resolution",
        "size",
        "source",
        "seeders",
        "promotion",
        "preferences",
    }
