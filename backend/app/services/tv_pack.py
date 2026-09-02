from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

TV_COMPLETE_SERIES_PACK = "TV_COMPLETE_SERIES_PACK"
TV_COMPLETE_SEASON_PACK = "TV_COMPLETE_SEASON_PACK"

_COMPLETE_SERIES_MARKERS = (
    "complete series",
    "series pack",
    "box set",
    "boxset",
    "全集",
    "全套",
)
_COMPLETE_SEASON_MARKERS = (
    "complete season",
    "full season",
    "season pack",
    "全季",
)
_GENERIC_COMPLETE_MARKERS = ("完整版",)
_NON_PACK_MARKERS = (
    "bonus",
    "extra",
    "extras",
    "featurette",
    "incomplete",
    "partial",
    "sample",
    "special",
    "specials",
    "trailer",
    "preview",
    "teaser",
    "promo",
    "ova",
    "behind the scenes",
    "特典",
    "特辑",
    "花絮",
    "预告",
)
_SERIES_COLLECTION_TYPES = {
    "box_set",
    "boxset",
    "collection",
    "complete",
    "complete_series",
    "full_series",
    "series",
    "series_pack",
}
_SEASON_COLLECTION_TYPES = {
    "complete_season",
    "full_season",
    "pack",
    "season",
    "season_pack",
}

_EXPLICIT_EPISODE = re.compile(
    r"(?:\bs\d{1,2}[ ._-]*(?:episode|ep|e)[ ._-]*\d{1,5}\b|\b\d{1,2}x\d{1,5}\b)",
    re.IGNORECASE,
)
_SPECIAL_SEASON = re.compile(r"\bs0{1,2}(?!\d)", re.IGNORECASE)
_SEASON_CONTEXT = re.compile(
    r"(?:\bs\d{1,2}(?!\d)|\bseason[ ._-]*\d{1,2}\b)", re.IGNORECASE
)
_SEASON_RANGE = re.compile(
    r"(?:\bs\d{1,2}\s*[-~–—]\s*s\d{1,2}\b|"
    r"\bseason[ ._-]*\d{1,2}\s*[-~–—]\s*(?:season[ ._-]*)?\d{1,2}\b)",
    re.IGNORECASE,
)
_PARTIAL_NUMBERED_PACK = re.compile(
    r"(?:^|\s)(?:part|pt|vol|volume|cour)\s*\d+(?:\s|$)", re.IGNORECASE
)


def classify_tv_pack(
    title: str,
    *,
    collection_type: str | None = None,
    seasons: Sequence[int] | None = None,
    episodes: Sequence[int | str] | None = None,
    file_count: int | None = None,
) -> str | None:
    """Classify only TV releases that are safe for unattended replacement."""

    normalized_raw_title = unicodedata.normalize("NFKC", title).casefold()
    normalized_title = re.sub(r"[_\W]+", " ", normalized_raw_title).strip()
    title_words = set(normalized_title.split())
    normalized_collection = re.sub(
        r"[^a-z0-9]+", "_", (collection_type or "").strip().casefold()
    ).strip("_")
    known_seasons = tuple(seasons or ())

    if episodes or _EXPLICIT_EPISODE.search(normalized_raw_title):
        return None
    if 0 in known_seasons or _SPECIAL_SEASON.search(normalized_raw_title):
        return None
    if file_count is not None and file_count <= 1:
        return None
    if any(_has_marker(normalized_title, marker) for marker in _NON_PACK_MARKERS):
        return None
    if _PARTIAL_NUMBERED_PACK.search(normalized_title):
        return None

    if any(_has_marker(normalized_title, marker) for marker in _COMPLETE_SERIES_MARKERS):
        return TV_COMPLETE_SERIES_PACK
    if any(_has_marker(normalized_title, marker) for marker in _COMPLETE_SEASON_MARKERS):
        return TV_COMPLETE_SEASON_PACK

    has_season_context = bool(known_seasons or _SEASON_CONTEXT.search(normalized_raw_title))
    title_is_generically_complete = "complete" in title_words or any(
        _has_marker(normalized_title, marker) for marker in _GENERIC_COMPLETE_MARKERS
    )
    if title_is_generically_complete:
        return TV_COMPLETE_SEASON_PACK if has_season_context else TV_COMPLETE_SERIES_PACK

    # A range is a multi-season pack, not proof that it covers the entire series.
    if _SEASON_RANGE.search(normalized_raw_title):
        return TV_COMPLETE_SEASON_PACK
    if normalized_collection in _SERIES_COLLECTION_TYPES:
        return TV_COMPLETE_SERIES_PACK
    if normalized_collection in _SEASON_COLLECTION_TYPES:
        return TV_COMPLETE_SEASON_PACK
    if has_season_context:
        return TV_COMPLETE_SEASON_PACK
    return None


def tv_pack_rank(reason: str | None) -> int:
    if reason == TV_COMPLETE_SERIES_PACK:
        return 2
    if reason == TV_COMPLETE_SEASON_PACK:
        return 1
    return 0


def _has_marker(normalized_title: str, marker: str) -> bool:
    if marker.isascii():
        return bool(re.search(rf"(?:^|\s){re.escape(marker)}(?:$|\s)", normalized_title))
    return marker in normalized_title
