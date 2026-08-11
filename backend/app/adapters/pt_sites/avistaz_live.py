from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.adapters.base import PtSiteAdapter
from app.core.http import SafeAsyncHttpClient, SafeHttpResult, SerializedRateLimiter
from app.core.security import validate_external_url
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    ProbeResult,
    TorrentCandidate,
    TorrentDetails,
    TorrentSearchRequest,
)


class AvistaZAuthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str = Field(validation_alias=AliasChoices("token", "access_token", "jwt"))


class AvistaZMovieTv(BaseModel):
    """Public media identifiers nested by the Jackett-compatible AvistaZ API."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    tmdb: Any = Field(default=None, validation_alias=AliasChoices("tmdb", "tmdb_id"))
    imdb: Any = Field(default=None, validation_alias=AliasChoices("imdb", "imdb_id"))
    tvdb: Any = Field(default=None, validation_alias=AliasChoices("tvdb", "tvdb_id"))
    media_type: Any = Field(default=None, validation_alias=AliasChoices("media_type", "type"))


class AvistaZRawCandidate(BaseModel):
    # Drop unmodelled fields instead of retaining personal download URLs, PID-bearing links,
    # announce URLs, or passkeys returned by the upstream API.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    torrent_id: Any = Field(validation_alias=AliasChoices("torrent_id", "id"))
    release_title: Any = Field(validation_alias=AliasChoices("release_title", "title", "name"))
    media_type: Any = Field(default=None, validation_alias=AliasChoices("media_type", "type"))
    category: Any = None
    movie_tv: AvistaZMovieTv | None = None
    tmdb_id: Any = Field(default=None, validation_alias=AliasChoices("tmdb_id", "tmdb"))
    imdb_id: Any = Field(default=None, validation_alias=AliasChoices("imdb_id", "imdb"))
    season: Any = None
    episodes: Any = None
    collection_type: Any = None
    resolution: Any = Field(
        default=None, validation_alias=AliasChoices("resolution", "video_quality")
    )
    source: Any = Field(default=None, validation_alias=AliasChoices("source", "media"))
    codec: Any = Field(default=None, validation_alias=AliasChoices("codec", "format"))
    hdr: Any = None
    audio: Any = None
    subtitles: Any = Field(
        default=None, validation_alias=AliasChoices("subtitles", "subtitle")
    )
    size_bytes: Any = Field(
        default=None, validation_alias=AliasChoices("size_bytes", "size", "file_size")
    )
    file_count: Any = None
    seeders: Any = Field(default=None, validation_alias=AliasChoices("seeders", "seed"))
    leechers: Any = Field(default=None, validation_alias=AliasChoices("leechers", "leech"))
    completed: Any = None
    download_factor: Any = Field(
        default=None,
        validation_alias=AliasChoices("download_factor", "download_multiply"),
    )
    upload_factor: Any = Field(
        default=None,
        validation_alias=AliasChoices("upload_factor", "upload_multiply"),
    )
    hit_and_run: Any = None
    info_hash: Any = None
    download_url: Any = Field(
        default=None, validation_alias=AliasChoices("download", "download_url")
    )
    published_at: Any = Field(
        default=None,
        validation_alias=AliasChoices("published_at", "created_at", "created_at_iso"),
    )


class AvistaZSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[AvistaZRawCandidate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_envelope(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"results": value}
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("results"), list):
            return value
        data = value.get("data")
        if isinstance(data, list):
            return {**value, "results": data}
        if isinstance(data, dict):
            nested = data.get("results")
            if isinstance(nested, list):
                return {**value, "results": nested}
        return value


class AvistaZAdapter(PtSiteAdapter):
    def __init__(
        self,
        *,
        username: str,
        password: str,
        pid: str,
        base_url: str = "https://avistaz.to",
        allowed_hosts: tuple[str, ...] = ("avistaz.to",),
        transport: httpx.AsyncBaseTransport | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_response_bytes: int = 10 * 1024 * 1024,
        min_interval_seconds: float = 6.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        request_gate: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
        before_request: Callable[[], Awaitable[None]] | None = None,
        enable_torrent_fetch: bool = False,
    ) -> None:
        if not username or not password or not pid:
            raise AppError("AVISTAZ_NOT_CONFIGURED", "AvistaZ 运行时凭据未配置", status_code=409)
        self._username = username
        self._password = password
        self._pid = pid
        self._token: str | None = None
        self.http = SafeAsyncHttpClient(
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
            transport=transport,
        )
        self.limiter = SerializedRateLimiter(min_interval_seconds, sleep=sleep)
        self.sleep = sleep
        self.request_gate = request_gate
        self.before_request = before_request
        self.enable_torrent_fetch = enable_torrent_fetch
        self._site_lock = asyncio.Lock()
        self._candidate_memory: dict[str, TorrentCandidate] = {}
        self._download_memory: dict[str, str] = {}

    def set_before_request_guard(
        self, guard: Callable[[], Awaitable[None]] | None
    ) -> None:
        self.before_request = guard

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="avistaz",
            name="AvistaZ",
            adapter_type="pt_site",
            version="1.0",
            enabled=True,
            mode="LIVE_READ_ONLY_SEARCH",
            description="真实 AvistaZ 认证与只读候选搜索；下载端点硬禁用",
            capabilities={
                "external_id_search": True,
                "text_search": True,
                "imdb_search": True,
                "tmdb_search": True,
                "tvdb_search": True,
                "category_filter": True,
                "promotion_parsing": True,
                "hit_and_run_parsing": True,
                "direct_torrent_download": True,
                "fetch_torrent_enabled": self.enable_torrent_fetch,
                "write_operations": False,
            },
        )

    async def aclose(self) -> None:
        self._token = None
        self._download_memory.clear()
        self._candidate_memory.clear()
        await self.http.aclose()

    async def probe(self) -> ProbeResult:
        try:
            await self._authenticate()
            return ProbeResult(healthy=True, message="AvistaZ 只读认证成功")
        except AppError as exc:
            return ProbeResult(healthy=False, error_code=exc.error_code, message=exc.message)

    async def validate_session(self) -> bool:
        return self._token is not None

    async def get_account_state(self) -> dict[str, str | int | float | bool | None]:
        return {"authenticated_in_memory": self._token is not None, "mode": "READ_ONLY_SEARCH"}

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SafeHttpResult:
        async with self._site_lock:
            for attempt in range(3):
                gate = self.request_gate(path) if self.request_gate else nullcontext()
                async with gate:
                    await self.limiter.acquire(path)
                    response = await self.http.request(
                        method,
                        path,
                        params=params,
                        json_body=json_body,
                        headers=headers,
                        before_send=self.before_request,
                    )
                if response.status_code != 429:
                    return response
                if attempt == 2:
                    raise AppError(
                        "AVISTAZ_RATE_LIMITED",
                        "AvistaZ 请求受到限速，请稍后重试",
                        status_code=429,
                        retryable=True,
                    )
                await self.sleep(float(2**attempt))
        raise AppError("AVISTAZ_RATE_LIMITED", "AvistaZ 请求受到限速", retryable=True)

    async def _authenticate(self) -> None:
        response = await self._send(
            "POST",
            "/api/v1/jackett/auth",
            json_body={"username": self._username, "password": self._password, "pid": self._pid},
            headers={"Accept": "application/json"},
        )
        if response.status_code in {401, 403, 412}:
            raise AppError("AVISTAZ_AUTH_FAILED", "AvistaZ 认证失败", status_code=401)
        if response.status_code >= 500:
            raise AppError(
                "AVISTAZ_UNAVAILABLE",
                "AvistaZ 服务暂时不可用",
                status_code=502,
                retryable=True,
            )
        if not 200 <= response.status_code < 300:
            raise AppError("AVISTAZ_HTTP_ERROR", "AvistaZ 返回了无法处理的状态")
        try:
            auth = AvistaZAuthResponse.model_validate(response.json())
        except ValidationError as exc:
            raise AppError("AVISTAZ_VALIDATION_ERROR", "AvistaZ 认证响应格式无效") from exc
        self._token = auth.token

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        if self._token is None:
            await self._authenticate()
        params = self._search_params(request)
        for auth_attempt in range(2):
            token = self._token
            if token is None:
                await self._authenticate()
                token = self._token
            response = await self._send(
                "GET",
                "/api/v1/jackett/torrents",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            if response.status_code in {401, 412}:
                self._token = None
                if auth_attempt == 0:
                    await self._authenticate()
                    continue
                raise AppError("AVISTAZ_AUTH_EXPIRED", "AvistaZ 会话已失效", status_code=401)
            if response.status_code >= 500:
                raise AppError(
                    "AVISTAZ_UNAVAILABLE",
                    "AvistaZ 服务暂时不可用",
                    status_code=502,
                    retryable=True,
                )
            if not 200 <= response.status_code < 300:
                raise AppError("AVISTAZ_HTTP_ERROR", "AvistaZ 搜索返回了无法处理的状态")
            try:
                payload = response.json()
                parsed = AvistaZSearchResponse.model_validate(payload)
                candidates = [self._normalize(raw, request.type) for raw in parsed.results]
            except ValidationError as exc:
                raise AppError("AVISTAZ_VALIDATION_ERROR", "AvistaZ 搜索响应格式无效") from exc
            for raw, candidate in zip(parsed.results, candidates, strict=True):
                self._remember_download_url(candidate.torrent_id, raw.download_url)
            self._candidate_memory.update(
                {candidate.torrent_id: candidate for candidate in candidates}
            )
            return candidates
        raise AppError("AVISTAZ_AUTH_EXPIRED", "AvistaZ 会话已失效", status_code=401)

    async def get_torrent_details(self, torrent_id: str) -> TorrentDetails:
        candidate = self._candidate_memory.get(torrent_id)
        if candidate is None:
            raise AppError("TORRENT_NOT_FOUND", "当前只读搜索缓存中没有该候选", status_code=404)
        return TorrentDetails(candidate=candidate, description=None, files=[])

    async def fetch_torrent(self, torrent_id: str) -> bytes:
        if not self.enable_torrent_fetch:
            raise AppError(
                "PHASE_NOT_ENABLED",
                "当前阶段禁止访问 AvistaZ download URL 或下载 .torrent",
                status_code=403,
            )
        download_url = self._download_memory.get(torrent_id)
        if download_url is None:
            raise AppError(
                "TORRENT_REFERENCE_NOT_IN_SESSION",
                "当前认证会话中没有该种子的临时下载引用，请先重新搜索",
                status_code=409,
            )
        for auth_attempt in range(2):
            if self._token is None:
                await self._authenticate()
            response = await self._send(
                "GET",
                download_url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/x-bittorrent, application/octet-stream",
                },
            )
            if response.status_code in {401, 412}:
                self._token = None
                if auth_attempt == 0:
                    continue
                raise AppError("AVISTAZ_AUTH_EXPIRED", "AvistaZ 会话已失效", status_code=401)
            if response.status_code >= 500:
                raise AppError(
                    "AVISTAZ_UNAVAILABLE",
                    "AvistaZ 服务暂时不可用",
                    status_code=502,
                    retryable=True,
                )
            if not 200 <= response.status_code < 300:
                raise AppError("AVISTAZ_TORRENT_FETCH_FAILED", "AvistaZ 种子获取失败")
            lowered_type = response.content_type.casefold()
            if "html" in lowered_type or "json" in lowered_type or not response.content.startswith(
                b"d"
            ):
                raise AppError(
                    "AVISTAZ_TORRENT_INVALID_RESPONSE",
                    "AvistaZ 种子响应格式无效",
                    status_code=502,
                )
            return response.content
        raise AppError("AVISTAZ_AUTH_EXPIRED", "AvistaZ 会话已失效", status_code=401)

    def _remember_download_url(self, torrent_id: str, value: Any) -> None:
        raw_url = self._text(value)
        if raw_url is None:
            return
        resolved = urljoin(f"{self.http.base_url}/", raw_url)
        validated = validate_external_url(resolved, self.http.allowed_hosts)
        if self._origin(validated) != self._origin(self.http.base_url):
            raise AppError(
                "AVISTAZ_DOWNLOAD_ORIGIN_NOT_ALLOWED",
                "AvistaZ 种子下载地址与认证站点不同源",
                status_code=502,
            )
        self._download_memory[torrent_id] = validated

    @staticmethod
    def _origin(value: str) -> tuple[str, str, int]:
        parsed = urlparse(value)
        return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port or 443

    @staticmethod
    def _search_params(request: TorrentSearchRequest) -> dict[str, Any]:
        values = request.model_dump(exclude_none=True)
        params: dict[str, Any] = {}
        for key, value in values.items():
            if value in ([], ""):
                continue
            api_key = {
                "video_quality": "video_quality[]",
                "language": "language[]",
                "subtitle": "subtitle[]",
                "discount": "discount[]",
            }.get(key, key)
            params[api_key] = value.value if isinstance(value, MediaType) else value
        return params

    def _normalize(
        self, raw: AvistaZRawCandidate, requested_type: MediaType | None
    ) -> TorrentCandidate:
        torrent_id = str(raw.torrent_id)
        release_title = str(raw.release_title).strip()
        nested = raw.movie_tv
        media_type = self._media_type(
            raw.media_type or raw.category or (nested.media_type if nested else None),
            requested_type,
        )
        parsed_season, parsed_episodes = self._season_episodes(release_title)
        season = self._integer(raw.season) or parsed_season
        episodes = self._episode_list(raw.episodes) or parsed_episodes
        collection_type = self._text(raw.collection_type)
        if collection_type is None:
            collection_type = "season" if season is not None and not episodes else "episode"
        internal_ref = hashlib.sha256(f"avistaz|{torrent_id}".encode()).hexdigest()[:32]
        return TorrentCandidate(
            site_id="avistaz",
            torrent_id=torrent_id,
            release_title=release_title,
            details_ref=f"avistaz:details:{internal_ref}",
            media_type=media_type,
            tmdb_id=self._integer(raw.tmdb_id)
            or self._integer(nested.tmdb if nested else None),
            imdb_id=self._text(raw.imdb_id) or self._text(nested.imdb if nested else None),
            year=self._year(release_title),
            season=season,
            episodes=episodes,
            collection_type=collection_type,
            resolution=self._named_text(raw.resolution) or self._resolution(release_title),
            source=self._named_text(raw.source),
            codec=self._named_text(raw.codec),
            hdr=self._string_list(raw.hdr),
            audio=self._string_list(raw.audio),
            subtitles=self._string_list(raw.subtitles),
            size_bytes=self._size(raw.size_bytes),
            file_count=self._integer(raw.file_count),
            seeders=self._integer(raw.seeders),
            leechers=self._integer(raw.leechers),
            completed=self._integer(raw.completed),
            download_factor=self._number(raw.download_factor),
            upload_factor=self._number(raw.upload_factor),
            hit_and_run=self._boolean(raw.hit_and_run),
            info_hash=self._text(raw.info_hash),
            published_at=self._datetime(raw.published_at),
        )

    @staticmethod
    def _media_type(value: Any, fallback: MediaType | None) -> MediaType:
        normalized = str(value or "").casefold()
        if any(token in normalized for token in ("tv", "series", "电视剧")):
            return MediaType.TV
        if any(token in normalized for token in ("movie", "film", "电影")):
            return MediaType.MOVIE
        return fallback or MediaType.MOVIE

    @staticmethod
    def _season_episodes(title: str) -> tuple[int | None, list[int] | None]:
        match = re.search(
            r"(?i)\bS(\d{1,2})(?:E(\d{1,3})(?:-?E?(\d{1,3}))?)?\b", title
        )
        if match is None:
            return None, None
        season = int(match.group(1))
        start = int(match.group(2)) if match.group(2) else None
        end = int(match.group(3)) if match.group(3) else None
        if start is None:
            return season, None
        if end is not None and start <= end <= start + 200:
            return season, list(range(start, end + 1))
        return season, [start]

    @staticmethod
    def _episode_list(value: Any) -> list[int] | None:
        if isinstance(value, list):
            result = [item for item in (AvistaZAdapter._integer(part) for part in value) if item]
            return sorted(set(result)) or None
        if isinstance(value, str):
            result = [int(part) for part in re.findall(r"\d+", value)]
            return sorted(set(result)) or None
        return None

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result >= 0 else None

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if result >= 0 else None

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        result = str(value).strip()
        return result or None

    @staticmethod
    def _named_text(value: Any) -> str | None:
        if isinstance(value, dict):
            for key in ("name", "title", "label", "value", "slug"):
                result = AvistaZAdapter._text(value.get(key))
                if result:
                    return result
            return None
        if isinstance(value, list):
            for item in value:
                result = AvistaZAdapter._named_text(item)
                if result:
                    return result
            return None
        return AvistaZAdapter._text(value)

    @staticmethod
    def _string_list(value: Any) -> list[str] | None:
        if isinstance(value, list):
            result = [str(item).strip() for item in value if str(item).strip()]
            return result or None
        if isinstance(value, str):
            result = [item.strip() for item in re.split(r"[,/|]", value) if item.strip()]
            return result or None
        return None

    @staticmethod
    def _boolean(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, str)):
            normalized = str(value).casefold()
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
        return None

    @staticmethod
    def _size(value: Any) -> int | None:
        direct = AvistaZAdapter._integer(value)
        if direct is not None:
            return direct
        if not isinstance(value, str):
            return None
        match = re.search(r"(?i)(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)", value)
        if match is None:
            return None
        units = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
        unit = match.group(2).upper().replace("IB", "B")
        factor = units.get(unit)
        return int(float(match.group(1)) * factor) if factor else None

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _year(title: str) -> int | None:
        matches = re.findall(r"\b(19\d{2}|20\d{2}|21\d{2})\b", title)
        return int(matches[-1]) if matches else None

    @staticmethod
    def _resolution(title: str) -> str | None:
        match = re.search(r"(?i)\b(2160p|1080p|1080i|720p|576p|480p)\b", title)
        return match.group(1).lower() if match else None
