"""How good a release is, separately from whether it is the right title.

The old single score mixed two unrelated questions.  Identity ("is this the
film I asked for") was worth 0.46 of it and release quality only 0.36, so
every candidate for one media item landed within a hair of every other: SNL
Korea's seventeen seasons scored 0.685 to 0.692.  The number could not rank
versions because it was mostly measuring something all of them shared.

Identity is a gate, handled by the warnings in ``matching.py`` and the
rejection rules in ``automation.py``.  What is left here is the part that
actually differs between two releases of the same thing, on a real 0-1 scale:
resolution, size, source, seeders, promotion.

Weights are per-deployment because "best" is a preference, not a fact.  The
defaults follow the operator this was built for: picture quality first, size
as its proxy, seeders as a floor rather than a driver, free leech barely
counted.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ordered ladders.  Unknown sits mid-pack: absent metadata should not beat a
# known-poor release, nor be punished as if it were one.
_RESOLUTION_RANK: dict[str, float] = {
    "2160p": 1.0,
    "4k": 1.0,
    "1440p": 0.88,
    "1080p": 0.8,
    "1080i": 0.68,
    "720p": 0.45,
    "576p": 0.25,
    "480p": 0.15,
}
_UNKNOWN_RESOLUTION = 0.35

_SOURCE_RANK: dict[str, float] = {
    "remux": 1.0,
    "bluray": 0.92,
    "blu-ray": 0.92,
    "bd": 0.9,
    "uhd": 0.95,
    "web-dl": 0.78,
    "webdl": 0.78,
    "webrip": 0.62,
    "web": 0.7,
    "hdtv": 0.45,
    "dvdrip": 0.3,
    "dvd": 0.3,
    "hdrip": 0.35,
}
_UNKNOWN_SOURCE = 0.4


@dataclass(frozen=True)
class QualityWeights:
    """Relative importance of each component; normalised before use."""

    resolution: float = 44.0
    size: float = 24.0
    source: float = 12.0
    seeders: float = 13.0
    promotion: float = 3.0
    preferences: float = 4.0

    # Seeders below this are treated as a risk, above it as merely fine --
    # "at least a few" rather than "as many as possible".
    seeder_floor: int = 3
    # Size reference in bytes.  Bigger always scores higher, with diminishing
    # returns, so a remux beats a web-dl without a cap having to be guessed.
    size_reference_bytes: int = 8 * 1024**3

    def normalised(self) -> dict[str, float]:
        parts = {
            "resolution": max(0.0, self.resolution),
            "size": max(0.0, self.size),
            "source": max(0.0, self.source),
            "seeders": max(0.0, self.seeders),
            "promotion": max(0.0, self.promotion),
            "preferences": max(0.0, self.preferences),
        }
        total = sum(parts.values())
        if total <= 0:
            # Everything zeroed would make every candidate identical; fall back
            # to the defaults rather than returning a meaningless flat score.
            return QualityWeights().normalised()
        return {key: value / total for key, value in parts.items()}


def resolution_score(value: str | None) -> float:
    if not value:
        return _UNKNOWN_RESOLUTION
    return _RESOLUTION_RANK.get(value.strip().casefold(), _UNKNOWN_RESOLUTION)


def source_score(value: str | None) -> float:
    if not value:
        return _UNKNOWN_SOURCE
    key = value.strip().casefold().replace(" ", "")
    if key in _SOURCE_RANK:
        return _SOURCE_RANK[key]
    # Release names are inconsistent ("WEB-DL 2160p", "Blu-ray Remux"); take
    # the strongest ladder entry the string contains.
    hits = [rank for name, rank in _SOURCE_RANK.items() if name in key]
    return max(hits) if hits else _UNKNOWN_SOURCE


def size_score(size_bytes: int | None, *, reference_bytes: int) -> float:
    """Bigger is better, with diminishing returns and no cap to guess.

    ``x / (x + reference)`` keeps the ordering strictly increasing -- a 60 GB
    remux always outranks a 20 GB encode -- while staying inside 0-1 so the
    threshold keeps meaning the same thing.  At the reference size the
    component is worth half its weight.
    """

    if size_bytes is None or size_bytes <= 0:
        # Unknown size is not a virtue and not a crime.
        return 0.3
    reference = max(1, reference_bytes)
    return size_bytes / (size_bytes + reference)


def seeder_score(seeders: int | None, *, floor: int) -> float:
    """A floor, not a race.

    Reaching the floor is most of the value; beyond it more seeders help only
    a little, because the difference between 30 and 300 seeders does not change
    whether the download finishes.
    """

    count = max(0, seeders or 0)
    if count == 0:
        return 0.0
    threshold = max(1, floor)
    if count < threshold:
        return 0.7 * (count / threshold)
    return 0.7 + 0.3 * (1 - 1 / (1 + (count - threshold) / 10))


def preference_score(
    *,
    preferred_resolution: bool,
    preferred_source: bool,
    preferred_audio: bool,
    preferred_subtitle: bool,
) -> float:
    """The operator's explicit lists, as one small component.

    The ladders above already rank resolution and source on their own merits;
    this is the extra nudge for "and it is on my list".
    """

    hits = sum(
        (
            preferred_resolution,
            preferred_source,
            preferred_audio,
            preferred_subtitle,
        )
    )
    return hits / 4


@dataclass(frozen=True)
class QualityBreakdown:
    """The final score plus every component, so the UI can explain a pick."""

    score: float
    components: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return {"score": self.score, "components": dict(self.components)}


def score_release_quality(
    *,
    resolution: str | None,
    source: str | None,
    size_bytes: int | None,
    seeders: int | None,
    download_factor: float | None,
    preferred_resolution: bool = False,
    preferred_source: bool = False,
    preferred_audio: bool = False,
    preferred_subtitle: bool = False,
    weights: QualityWeights | None = None,
) -> QualityBreakdown:
    weights = weights or QualityWeights()
    share = weights.normalised()
    components = {
        "resolution": resolution_score(resolution),
        "size": size_score(size_bytes, reference_bytes=weights.size_reference_bytes),
        "source": source_score(source),
        "seeders": seeder_score(seeders, floor=weights.seeder_floor),
        # download_factor 0 means free leech, 1 means it counts in full.
        "promotion": 1.0 if (download_factor is not None and download_factor < 1) else 0.0,
        "preferences": preference_score(
            preferred_resolution=preferred_resolution,
            preferred_source=preferred_source,
            preferred_audio=preferred_audio,
            preferred_subtitle=preferred_subtitle,
        ),
    }
    total = sum(components[key] * share[key] for key in components)
    return QualityBreakdown(
        score=round(min(1.0, max(0.0, total)), 4),
        components={key: round(value, 4) for key, value in components.items()},
    )
