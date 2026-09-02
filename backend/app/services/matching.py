from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

from app.core.pt_site_rules import effective_hnr_rule
from app.models.enums import MediaType
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.services.tv_pack import classify_tv_pack

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


def _tv_pack_reason(candidate: TorrentCandidate) -> str | None:
    return classify_tv_pack(
        candidate.release_title,
        collection_type=candidate.collection_type,
        seasons=[candidate.season] if candidate.season is not None else None,
        episodes=candidate.episodes,
        file_count=candidate.file_count,
    )


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
