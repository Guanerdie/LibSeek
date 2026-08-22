from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.core.pt_site_rules import effective_hnr_rule
from app.models.entities import MediaItem
from app.models.enums import MediaType
from app.schemas.adapters import MetadataRecord, TorrentCandidate


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
    media: MediaItem, candidate: MetadataRecord, *, expected_tmdb_id: int | None
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

    if candidate.tmdb_id is not None:
        if candidate.tmdb_id == metadata.tmdb_id:
            score += 0.32
            reasons.append("TMDB_ID_EXACT")
        else:
            warnings.append("ID_MISMATCH")
    elif candidate.imdb_id and metadata.imdb_id:
        if candidate.imdb_id.casefold() == metadata.imdb_id.casefold():
            score += 0.28
            reasons.append("IMDB_ID_EXACT")
        else:
            warnings.append("ID_MISMATCH")

    if candidate.media_type == metadata.media_type:
        score += 0.14
        reasons.append("MEDIA_TYPE_MATCH")
    else:
        warnings.append("ID_MISMATCH")

    required = _required_episode_map(missing_episodes)
    if metadata.media_type == MediaType.TV and required:
        target_seasons = set(required)
        if candidate.season in target_seasons:
            score += 0.07
            reasons.append("SEASON_EXACT")
            required_episodes = required[candidate.season]
            if candidate.episodes is None and candidate.collection_type == "season":
                score += 0.07
                reasons.append("SEASON_PACK_COVERS_TARGET_SEASON")
            elif candidate.episodes:
                offered = set(candidate.episodes)
                overlap = offered & required_episodes
                if offered == required_episodes:
                    score += 0.09
                    reasons.append("EPISODE_COVERAGE_EXACT")
                elif required_episodes.issubset(offered):
                    score += 0.07
                    reasons.append("EPISODE_COVERAGE_COMPLETE")
                    warnings.append("EPISODE_OVERLAP")
                else:
                    warnings.append("PARTIAL_PACK")
                    if overlap:
                        warnings.append("EPISODE_OVERLAP")
        else:
            warnings.append("PARTIAL_PACK")

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


def _required_episode_map(values: list[str] | None) -> dict[int, set[int]]:
    result: dict[int, set[int]] = {}
    for value in values or []:
        match = re.fullmatch(r"S(\d{2})E(\d{2,3})", value)
        if match:
            result.setdefault(int(match.group(1)), set()).add(int(match.group(2)))
    return result


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
