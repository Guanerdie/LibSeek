from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

from app.core.pt_site_rules import effective_hnr_rule
from app.models.enums import MediaType
from app.schemas.adapters import MetadataRecord, TorrentCandidate

_RELEASE_BOUNDARY_TOKENS = {
    "complete",
    "season",
    "remux",
    "bluray",
    "bdrip",
    "web",
    "webdl",
    "webrip",
    "hdtv",
    "uhd",
    "dvd",
    "xvid",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "av1",
}

_TV_COMPLETE_SERIES_MARKERS = (
    "complete series",
    "series pack",
    "box set",
    "boxset",
    "全集",
    "全套",
)
_TV_COMPLETE_SEASON_MARKERS = (
    "complete season",
    "full season",
    "season pack",
    "全季",
)
_TV_GENERIC_COMPLETE_MARKERS = ("完整版",)
_TV_NON_PACK_MARKERS = (
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
    "特典",
    "特辑",
    "花絮",
    "预告",
)
_TV_SERIES_COLLECTION_TYPES = {
    "box_set",
    "boxset",
    "collection",
    "complete",
    "complete_series",
    "full_series",
    "series",
    "series_pack",
}
_TV_SEASON_COLLECTION_TYPES = {
    "complete_season",
    "full_season",
    "pack",
    "season",
    "season_pack",
}


class MediaForScoring(Protocol):
    media_type: MediaType
    title: str
    year: int | None


@dataclass(frozen=True)
class MatchPreferences:
    resolutions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    audio: tuple[str, ...] = ()
    subtitles: tuple[str, ...] = ()
    max_size_bytes: int | None = None
    possible_duplicate: bool = False


@dataclass(frozen=True)
class MetadataMatchScore:
    score: float
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def score_metadata_match(
    media: MediaForScoring, candidate: MetadataRecord, *, expected_tmdb_id: int | None
) -> MetadataMatchScore:
    score = 0.0
    reasons: list[str] = []
    conflicts: list[str] = []
    if expected_tmdb_id is not None and candidate.tmdb_id == expected_tmdb_id:
        score += 0.5
        reasons.append("TMDB_ID_EXACT")
    if candidate.media_type == media.media_type:
        score += 0.2
        reasons.append("MEDIA_TYPE_MATCH")
    else:
        conflicts.append("MEDIA_TYPE_CONFLICT")
    if media.year is not None and candidate.year is not None:
        if media.year == candidate.year:
            score += 0.15
            reasons.append("YEAR_MATCH")
        else:
            conflicts.append("YEAR_CONFLICT")
    title_values = [
        candidate.chinese_title,
        candidate.english_title,
        candidate.original_title,
        *candidate.aliases,
    ]
    source_title = _normalized_title(media.title)
    if any(source_title and source_title == _normalized_title(value) for value in title_values):
        score += 0.15
        reasons.append("TITLE_EXACT")
    return MetadataMatchScore(score=min(1.0, score), reasons=reasons, conflicts=conflicts)


def score_torrent_candidate(
    metadata: MetadataRecord,
    candidate: TorrentCandidate,
    *,
    missing_episodes: list[str] | None,
    preferences: MatchPreferences,
) -> TorrentCandidate:
    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    exact_external_identity = False
    if candidate.tmdb_id is not None:
        if candidate.tmdb_id == metadata.tmdb_id:
            reasons.append("TMDB_ID_EXACT")
            exact_external_identity = True
        else:
            warnings.append("ID_MISMATCH")
    if candidate.imdb_id:
        if metadata.imdb_id is None:
            warnings.append("ID_UNVERIFIED")
        elif candidate.imdb_id.casefold() == metadata.imdb_id.casefold():
            reasons.append("IMDB_ID_EXACT")
            exact_external_identity = True
        else:
            warnings.append("ID_MISMATCH")

    if "TMDB_ID_EXACT" in reasons:
        score += 0.32
    elif "IMDB_ID_EXACT" in reasons:
        score += 0.28

    if _release_title_matches(metadata, candidate.release_title):
        reasons.append("TITLE_EXACT")
        if not exact_external_identity:
            score += 0.32

    if candidate.media_type == metadata.media_type:
        score += 0.14
        reasons.append("MEDIA_TYPE_MATCH")
    else:
        warnings.append("ID_MISMATCH")

    # Local episode gaps are intentionally not part of automated selection.
    # The operator replaces an incomplete local TV item with a complete series
    # or season pack after download, so only the release's pack shape matters.
    del missing_episodes
    if metadata.media_type == MediaType.TV:
        pack_reason = _tv_pack_reason(candidate)
        if pack_reason == "TV_COMPLETE_SERIES_PACK":
            score += 0.09
            reasons.append(pack_reason)
        elif pack_reason == "TV_COMPLETE_SEASON_PACK":
            score += 0.07
            reasons.append(pack_reason)
        else:
            warnings.append("TV_PACK_UNVERIFIED")

    if candidate.year is not None and metadata.year is not None:
        if candidate.year == metadata.year:
            score += 0.1
            reasons.append("YEAR_MATCH")
        else:
            warnings.append("YEAR_MISMATCH")

    if _preferred(candidate.resolution, preferences.resolutions):
        score += 0.07
        reasons.append("PREFERRED_RESOLUTION")
    if _preferred(candidate.source, preferences.sources):
        score += 0.04
        reasons.append("PREFERRED_SOURCE")
    if _list_preferred(candidate.audio, preferences.audio):
        score += 0.03
        reasons.append("PREFERRED_AUDIO")
    if _list_preferred(candidate.subtitles, preferences.subtitles):
        score += 0.04
        reasons.append("PREFERRED_SUBTITLE")
    if candidate.seeders is not None and candidate.seeders > 0:
        score += min(0.04, 0.01 + candidate.seeders / 1000)
        reasons.append("ACTIVE_SEEDERS")
    else:
        warnings.append("NO_SEEDERS")
    if candidate.download_factor is not None and candidate.download_factor < 1:
        score += 0.03
        reasons.append("PROMOTION_ACTIVE")
    if preferences.max_size_bytes is not None and candidate.size_bytes is not None:
        if candidate.size_bytes > preferences.max_size_bytes:
            warnings.append("OVERSIZED")
        else:
            score += 0.02
            reasons.append("SIZE_WITHIN_LIMIT")
    if not effective_hnr_rule(candidate.site_id, candidate.hit_and_run).known:
        warnings.append("HNR_UNKNOWN")
    if preferences.possible_duplicate:
        warnings.append("POSSIBLE_DUPLICATE")

    return candidate.model_copy(
        update={
            "match_score": min(1.0, round(score, 4)),
            "match_reasons": list(dict.fromkeys(reasons)),
            "warnings": list(dict.fromkeys(warnings)),
        }
    )


def _release_title_matches(metadata: MetadataRecord, release_title: str) -> bool:
    release_tokens = _title_tokens(re.sub(r"^(?:\s*\[[^\]]+\]\s*)+", "", release_title))
    if not release_tokens:
        return False

    titles = [
        metadata.title,
        metadata.chinese_title,
        metadata.english_title,
        metadata.original_title,
        *metadata.aliases,
    ]
    seen: set[tuple[str, ...]] = set()
    for title in titles:
        title_tokens = tuple(_title_tokens(title))
        if not title_tokens or title_tokens in seen:
            continue
        seen.add(title_tokens)
        if release_tokens[: len(title_tokens)] != list(title_tokens):
            continue
        if len(release_tokens) == len(title_tokens):
            return True
        if _is_release_boundary(release_tokens[len(title_tokens)]):
            return True
    return False


def _title_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[^\W_]+", normalized, re.UNICODE)


def _is_release_boundary(token: str) -> bool:
    return bool(
        token in _RELEASE_BOUNDARY_TOKENS
        or re.fullmatch(r"(?:19|20)\d{2}", token)
        or re.fullmatch(r"s\d{1,2}(?:e\d{1,5})?", token)
        or re.fullmatch(r"e\d{1,5}", token)
        or re.fullmatch(r"\d{3,4}[pi]", token)
    )


def _title_has_marker(normalized_title: str, marker: str) -> bool:
    if marker.isascii():
        return bool(re.search(rf"(?:^|\s){re.escape(marker)}(?:$|\s)", normalized_title))
    return marker in normalized_title


def _tv_pack_reason(candidate: TorrentCandidate) -> str | None:
    collection_type = re.sub(
        r"[^a-z0-9]+", "_", (candidate.collection_type or "").strip().casefold()
    ).strip("_")
    normalized_raw_title = unicodedata.normalize("NFKC", candidate.release_title).casefold()
    normalized_title = re.sub(
        r"[^\w]+",
        " ",
        normalized_raw_title,
    ).strip()
    title_words = set(normalized_title.split())
    title_has_episode = bool(re.search(r"\bS\d{1,2}[ ._-]*E\d{1,5}\b", normalized_raw_title, re.I))
    title_has_episode_range = bool(
        re.search(
            r"\bS\d{1,2}[ ._-]*E\d{1,5}[ ._]*[-~–—][ ._]*E?\d{1,5}\b",
            normalized_raw_title,
            re.I,
        )
    )
    if len(candidate.episodes or []) == 1 or (title_has_episode and not title_has_episode_range):
        return None

    title_is_series = any(
        _title_has_marker(normalized_title, marker) for marker in _TV_COMPLETE_SERIES_MARKERS
    )
    title_is_season = any(
        _title_has_marker(normalized_title, marker) for marker in _TV_COMPLETE_SEASON_MARKERS
    )
    title_is_generically_complete = "complete" in title_words or any(
        _title_has_marker(normalized_title, marker) for marker in _TV_GENERIC_COMPLETE_MARKERS
    )
    title_is_non_pack = any(
        _title_has_marker(normalized_title, marker) for marker in _TV_NON_PACK_MARKERS
    )
    title_has_season_range = bool(
        re.search(r"\bS\d{1,2}\s*[-~–—]\s*S\d{1,2}\b", normalized_raw_title, re.I)
    )

    if title_is_non_pack:
        return None
    if title_is_series:
        return "TV_COMPLETE_SERIES_PACK"
    if title_is_season:
        return "TV_COMPLETE_SEASON_PACK"
    if title_has_season_range:
        return "TV_COMPLETE_SERIES_PACK"
    if title_is_generically_complete:
        return (
            "TV_COMPLETE_SEASON_PACK" if candidate.season is not None else "TV_COMPLETE_SERIES_PACK"
        )
    if candidate.episodes:
        return None
    if candidate.file_count is not None and candidate.file_count <= 1:
        return None
    if collection_type in _TV_SERIES_COLLECTION_TYPES:
        return "TV_COMPLETE_SERIES_PACK"
    if collection_type in _TV_SEASON_COLLECTION_TYPES:
        return "TV_COMPLETE_SEASON_PACK"
    if candidate.season is not None:
        return "TV_COMPLETE_SEASON_PACK"
    return None


def _normalized_title(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _preferred(value: str | None, preferred: tuple[str, ...]) -> bool:
    if not value or not preferred:
        return False
    normalized = value.casefold()
    return any(option.casefold() in normalized for option in preferred)


def _list_preferred(values: list[str] | None, preferred: tuple[str, ...]) -> bool:
    if not values or not preferred:
        return False
    return any(_preferred(value, preferred) for value in values)
