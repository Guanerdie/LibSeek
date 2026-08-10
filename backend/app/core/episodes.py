from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

EpisodeMatrix = dict[int, list[int]]

_EPISODE_CODE = re.compile(r"(?i)^S(\d{1,2})E(\d{1,3})$")
_SEASON_CODE = re.compile(r"(?i)^S?(\d{1,2})$")
_EPISODE_NUMBER = re.compile(r"(?i)^E?(\d{1,3})$")


def _bounded_number(value: object, pattern: re.Pattern[str], *, minimum: int) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an episode number")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and (match := pattern.fullmatch(value.strip())):
        number = int(match.group(1))
    else:
        raise ValueError("invalid episode number")
    if not minimum <= number <= 999:
        raise ValueError("episode number is outside the supported range")
    return number


def _season_number(value: object) -> int:
    number = _bounded_number(value, _SEASON_CODE, minimum=0)
    if number > 99:
        raise ValueError("season number is outside the supported range")
    return number


def _episode_number(value: object) -> int:
    return _bounded_number(value, _EPISODE_NUMBER, minimum=1)


def _episode_pair(value: object, season_hint: int | None = None) -> tuple[int, int]:
    if isinstance(value, str) and (match := _EPISODE_CODE.fullmatch(value.strip())):
        return _season_number(match.group(1)), _episode_number(match.group(2))
    if isinstance(value, Mapping):
        season = next(
            (
                value[key]
                for key in ("season", "season_number", "seasonNumber")
                if key in value
            ),
            season_hint,
        )
        episode = next(
            (
                value[key]
                for key in ("episode", "episode_number", "episodeNumber")
                if key in value
            ),
            None,
        )
        if season is None or episode is None:
            raise ValueError("episode object requires season and episode numbers")
        return _season_number(season), _episode_number(episode)
    if season_hint is None:
        raise ValueError("episode number requires a season")
    return season_hint, _episode_number(value)


def normalize_episode_matrix(value: object) -> EpisodeMatrix | None:
    if value is None:
        return None

    result: dict[int, set[int]] = {}

    def add(item: object, season_hint: int | None = None) -> None:
        season, episode = _episode_pair(item, season_hint)
        result.setdefault(season, set()).add(episode)

    if isinstance(value, Mapping):
        episode_record_keys = {
            "season",
            "season_number",
            "seasonNumber",
            "episode",
            "episode_number",
            "episodeNumber",
        }
        if episode_record_keys.intersection(value):
            add(value)
        else:
            for raw_season, raw_episodes in value.items():
                season = _season_number(raw_season)
                result.setdefault(season, set())
                if isinstance(raw_episodes, Sequence) and not isinstance(
                    raw_episodes, (str, bytes, bytearray)
                ):
                    for episode in raw_episodes:
                        add(episode, season)
                else:
                    add(raw_episodes, season)
    elif isinstance(value, str):
        tokens = [token for token in re.split(r"[\s,;|]+", value.strip()) if token]
        if not tokens:
            return {}
        for token in tokens:
            add(token)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            add(item)
    else:
        raise ValueError("episode matrix must be a mapping or episode list")

    return {season: sorted(episodes) for season, episodes in sorted(result.items())}


def normalize_episode_codes(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("missing episodes must be a list")
    pairs = {_episode_pair(item) for item in value}
    return [f"S{season:02d}E{episode:02d}" for season, episode in sorted(pairs)]


def derive_missing_episode_codes(
    aired_matrix: object,
    local_matrix: object,
    upstream_missing: object,
) -> list[str] | None:
    try:
        fallback = normalize_episode_codes(upstream_missing)
    except ValueError:
        fallback = None
    try:
        local = normalize_episode_matrix(local_matrix)
        aired = normalize_episode_matrix(aired_matrix)
    except ValueError:
        return fallback
    if local is None or aired is None:
        return fallback

    local_pairs = {
        (season, episode) for season, episodes in local.items() for episode in episodes
    }
    missing_pairs = sorted(
        (season, episode)
        for season, episodes in aired.items()
        for episode in episodes
        if (season, episode) not in local_pairs
    )
    return [f"S{season:02d}E{episode:02d}" for season, episode in missing_pairs]
