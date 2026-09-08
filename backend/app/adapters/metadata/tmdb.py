from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.base import MetadataProvider
from app.core.http import AsyncTtlCache, SafeAsyncHttpClient, SerializedRateLimiter
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    MetadataRecord,
    ProbeResult,
    SeasonRecord,
    normalize_country_codes,
)


class TmdbExternalIds(BaseModel):
    model_config = ConfigDict(extra="ignore")

    imdb_id: str | None = None
    tvdb_id: int | None = None


class TmdbAlternativeTitle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    name: str | None = None


class TmdbAlternativeTitles(BaseModel):
    model_config = ConfigDict(extra="ignore")

    titles: list[TmdbAlternativeTitle] = Field(default_factory=list)
    results: list[TmdbAlternativeTitle] = Field(default_factory=list)


class TmdbProductionCountry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    iso_3166_1: str | None = None


class TmdbGenre(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TmdbDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    genres: list[TmdbGenre] = Field(default_factory=list)
    title: str | None = None
    name: str | None = None
    original_title: str | None = None
    original_name: str | None = None
    original_language: str | None = None
    origin_country: list[str] = Field(default_factory=list)
    production_countries: list[TmdbProductionCountry] = Field(default_factory=list)
    release_date: str | None = None
    first_air_date: str | None = None
    number_of_seasons: int | None = Field(default=None, ge=0)
    number_of_episodes: int | None = Field(default=None, ge=0)
    poster_path: str | None = None
    backdrop_path: str | None = None
    status: str | None = None
    external_ids: TmdbExternalIds | None = None
    alternative_titles: TmdbAlternativeTitles | None = None


class TmdbSearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TmdbSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[TmdbSearchItem] = Field(default_factory=list)


class TmdbEpisode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    episode_number: int = Field(ge=0)
    air_date: str | None = None


class TmdbSeason(BaseModel):
    model_config = ConfigDict(extra="ignore")

    season_number: int = Field(ge=0)
    episodes: list[TmdbEpisode] = Field(default_factory=list)


class TmdbProvider(MetadataProvider):
    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = "https://api.themoviedb.org",
        allowed_hosts: tuple[str, ...] = ("api.themoviedb.org",),
        transport: httpx.AsyncBaseTransport | None = None,
        proxy: httpx.Proxy | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        min_interval_seconds: float = 0.25,
        cache_ttl_seconds: int = 24 * 60 * 60,
        cache_max_entries: int = 512,
        allow_future_episodes: bool = False,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        limiter: SerializedRateLimiter | None = None,
    ) -> None:
        if not access_token:
            raise AppError("TMDB_NOT_CONFIGURED", "TMDB Access Token 未配置", status_code=409)
        self._access_token = access_token
        self.http = SafeAsyncHttpClient(
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
            transport=transport,
            proxy=proxy,
        )
        self.limiter = limiter or SerializedRateLimiter(min_interval_seconds, sleep=sleep)
        self.cache: AsyncTtlCache[Any] = AsyncTtlCache(
            ttl_seconds=cache_ttl_seconds, max_entries=cache_max_entries
        )
        self.allow_future_episodes = allow_future_episodes
        self.today = today
        self.sleep = sleep

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="tmdb",
            name="TMDB",
            adapter_type="metadata",
            version="1.0",
            enabled=True,
            mode="LIVE_READ_ONLY",
            description="真实 TMDB 只读详情、搜索、外部 ID 和已播剧集矩阵",
            capabilities={
                "get_by_tmdb_id": True,
                "search": True,
                "external_ids": True,
                "tv_episode_matrix": True,
                "write_operations": False,
            },
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def probe(self) -> ProbeResult:
        try:
            payload = await self._get_json("/3/authentication", {})
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise AppError(
                    "TMDB_RESPONSE_INVALID",
                    "TMDB 认证响应格式无效",
                    status_code=502,
                )
            return ProbeResult(healthy=True, message="TMDB Access Token 验证成功")
        except AppError as exc:
            return ProbeResult(healthy=False, error_code=exc.error_code, message=exc.message)

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        cache_key = f"{path}|{sorted((key, str(value)) for key, value in params.items())}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return cached
        for attempt in range(3):
            await self.limiter.acquire(path)
            response = await self.http.request(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
            )
            if response.status_code == 429:
                if attempt == 2:
                    raise AppError(
                        "TMDB_RATE_LIMITED",
                        "TMDB 请求受到限速，请稍后重试",
                        status_code=429,
                        retryable=True,
                    )
                await self.sleep(float(2**attempt))
                continue
            if response.status_code in {401, 403}:
                raise AppError("TMDB_AUTH_FAILED", "TMDB Access Token 无效", status_code=401)
            if response.status_code == 404:
                raise AppError("TMDB_NOT_FOUND", "TMDB 中没有对应影视条目", status_code=404)
            if response.status_code >= 500:
                raise AppError(
                    "TMDB_UNAVAILABLE",
                    "TMDB 服务暂时不可用",
                    status_code=502,
                    retryable=True,
                )
            if not 200 <= response.status_code < 300:
                raise AppError("TMDB_HTTP_ERROR", "TMDB 返回了无法处理的状态", status_code=502)
            payload = response.json()
            await self.cache.set(cache_key, payload)
            return payload
        raise AppError("TMDB_RATE_LIMITED", "TMDB 请求受到限速", retryable=True)

    @staticmethod
    def _kind(media_type: MediaType) -> str:
        return "movie" if media_type == MediaType.MOVIE else "tv"

    async def _details(
        self, media_type: MediaType, tmdb_id: int, language: str, *, append: bool
    ) -> TmdbDetails:
        params: dict[str, Any] = {"language": language}
        if append:
            params["append_to_response"] = "external_ids,alternative_titles"
        try:
            payload = await self._get_json(f"/3/{self._kind(media_type)}/{tmdb_id}", params)
            return TmdbDetails.model_validate(payload)
        except ValidationError as exc:
            raise AppError("TMDB_VALIDATION_ERROR", "TMDB 详情字段校验失败") from exc

    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord:
        chinese, english = await asyncio.gather(
            self._details(media_type, tmdb_id, "zh-CN", append=True),
            self._details(media_type, tmdb_id, "en-US", append=False),
        )
        chinese_title = chinese.title or chinese.name
        english_title = english.title or english.name
        original_title = chinese.original_title or chinese.original_name
        aliases: list[str] = []
        if chinese.alternative_titles is not None:
            for alias in chinese.alternative_titles.titles + chinese.alternative_titles.results:
                value = alias.title or alias.name
                if value and value not in aliases:
                    aliases.append(value)
        for value in (chinese_title, english_title, original_title):
            if value and value not in aliases:
                aliases.append(value)
        year = self._year(chinese.release_date or chinese.first_air_date)
        external_ids = self._external_id_dict(chinese.external_ids)
        matrix: dict[int, list[int]] | None = None
        seasons: list[SeasonRecord] = []
        if media_type == MediaType.TV and (chinese.number_of_seasons or 0) > 0:
            matrix, seasons = await self._episode_matrix(tmdb_id, chinese.number_of_seasons or 0)
        return MetadataRecord(
            tmdb_id=chinese.id,
            imdb_id=external_ids.get("imdb_id"),
            media_type=media_type,
            title=chinese_title or english_title or original_title or f"TMDB {tmdb_id}",
            chinese_title=chinese_title,
            english_title=english_title,
            original_title=original_title,
            original_language=chinese.original_language,
            country_codes=self._country_codes(media_type, chinese),
            aliases=aliases,
            year=year,
            genre_ids=[genre.id for genre in chinese.genres],
            number_of_seasons=chinese.number_of_seasons,
            number_of_episodes=chinese.number_of_episodes,
            episode_matrix=matrix,
            seasons=seasons,
            poster_path=chinese.poster_path,
            backdrop_path=chinese.backdrop_path,
            status=chinese.status,
            confidence=1.0,
            external_ids=external_ids,
        )

    async def search(
        self, media_type: MediaType, title: str, year: int | None = None
    ) -> list[MetadataRecord]:
        params: dict[str, Any] = {
            "query": title,
            "language": "zh-CN",
            "include_adult": "false",
            "page": 1,
        }
        if year is not None:
            params["year" if media_type == MediaType.MOVIE else "first_air_date_year"] = year
        try:
            payload = await self._get_json(f"/3/search/{self._kind(media_type)}", params)
            search_response = TmdbSearchResponse.model_validate(payload)
        except ValidationError as exc:
            raise AppError("TMDB_VALIDATION_ERROR", "TMDB 搜索结果校验失败") from exc
        candidates: list[MetadataRecord] = []
        for raw in search_response.results[:5]:
            candidates.append(await self.get_by_tmdb_id(media_type, raw.id))
        return candidates

    async def get_external_ids(self, media_type: MediaType, tmdb_id: int) -> dict[str, str]:
        try:
            payload = await self._get_json(
                f"/3/{self._kind(media_type)}/{tmdb_id}/external_ids", {}
            )
            return self._external_id_dict(TmdbExternalIds.model_validate(payload))
        except ValidationError as exc:
            raise AppError("TMDB_VALIDATION_ERROR", "TMDB 外部 ID 校验失败") from exc

    async def get_country_codes(
        self, media_type: MediaType, tmdb_id: int
    ) -> list[str] | None:
        details = await self._details(media_type, tmdb_id, "en-US", append=False)
        return self._country_codes(media_type, details)

    async def get_tv_episode_matrix(self, tmdb_id: int) -> dict[int, list[int]] | None:
        details = await self._details(MediaType.TV, tmdb_id, "zh-CN", append=False)
        if (details.number_of_seasons or 0) <= 0:
            return None
        matrix, _ = await self._episode_matrix(tmdb_id, details.number_of_seasons or 0)
        return matrix

    @staticmethod
    def _country_codes(
        media_type: MediaType, details: TmdbDetails
    ) -> list[str] | None:
        values: list[object] = []
        if media_type == MediaType.TV:
            values.extend(details.origin_country)
        values.extend(country.iso_3166_1 for country in details.production_countries)
        return normalize_country_codes(values)

    async def _episode_matrix(
        self, tmdb_id: int, season_count: int
    ) -> tuple[dict[int, list[int]], list[SeasonRecord]]:
        """Return the aired-episode matrix and one summary per season.

        Both come out of the same season payloads, so they are built together
        rather than paying for the requests twice.
        """

        matrix: dict[int, list[int]] = {}
        summaries: list[SeasonRecord] = []
        for season_number in range(1, season_count + 1):
            try:
                payload = await self._get_json(
                    f"/3/tv/{tmdb_id}/season/{season_number}", {"language": "zh-CN"}
                )
                season = TmdbSeason.model_validate(payload)
            except ValidationError as exc:
                raise AppError("TMDB_VALIDATION_ERROR", "TMDB 季集字段校验失败") from exc
            numbered = [episode for episode in season.episodes if episode.episode_number > 0]
            episodes = [
                episode.episode_number
                for episode in numbered
                if self._episode_is_allowed(episode.air_date)
            ]
            if episodes:
                matrix[season.season_number] = episodes
            summaries.append(self._season_summary(season.season_number, numbered))
        return matrix, summaries

    def _season_summary(self, season_number: int, episodes: list[TmdbEpisode]) -> SeasonRecord:
        today = self.today()
        aired: list[date] = []
        unaired = 0
        for episode in episodes:
            parsed = self._air_date(episode.air_date)
            if parsed is not None and parsed <= today:
                aired.append(parsed)
            else:
                # No air date at all counts as not yet aired: TMDB leaves it
                # blank for episodes that have not been scheduled.
                unaired += 1
        return SeasonRecord(
            season_number=season_number,
            episode_count=len(episodes),
            aired_episode_count=len(aired),
            last_air_date=max(aired) if aired else None,
            # A season with no episodes listed is not "complete", it is unknown.
            is_complete=bool(episodes) and unaired == 0,
        )

    @staticmethod
    def _air_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _episode_is_allowed(self, air_date: str | None) -> bool:
        if self.allow_future_episodes:
            return True
        if not air_date:
            return False
        try:
            return date.fromisoformat(air_date) <= self.today()
        except ValueError:
            return False

    @staticmethod
    def _year(value: str | None) -> int | None:
        if not value or len(value) < 4:
            return None
        try:
            return int(value[:4])
        except ValueError:
            return None

    @staticmethod
    def _external_id_dict(value: TmdbExternalIds | None) -> dict[str, str]:
        if value is None:
            return {}
        result: dict[str, str] = {}
        if value.imdb_id:
            result["imdb_id"] = value.imdb_id
        if value.tvdb_id is not None:
            result["tvdb_id"] = str(value.tvdb_id)
        return result
