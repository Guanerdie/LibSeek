from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.adapters.base import PtSiteAdapter
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    ProbeResult,
    TorrentCandidate,
    TorrentDetails,
    TorrentSearchRequest,
)


class RateLimiter(Protocol):
    async def acquire(self, operation: str) -> None: ...


class NoopRateLimiter:
    async def acquire(self, operation: str) -> None:
        del operation


class AvistaZMockTransport(httpx.MockTransport):
    def __init__(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/jackett/auth" and request.method == "POST":
                return httpx.Response(200, json={"success": True})
            if request.url.path == "/api/v1/jackett/torrents" and request.method == "GET":
                return httpx.Response(200, json={"results": []})
            return httpx.Response(404, json={"error": "mock route not found"})

        super().__init__(handler)


class AvistaZMockAdapter(PtSiteAdapter):
    def __init__(
        self,
        fixtures: list[TorrentCandidate] | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.fixtures = fixtures or []
        self.limiter = limiter or NoopRateLimiter()

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="avistaz-mock",
            name="AvistaZ",
            adapter_type="pt_site",
            version="1.0",
            enabled=False,
            mode="MOCK_ONLY",
            description="本阶段未启用；只提供能力声明、模型和 Mock 契约",
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
                "real_network": False,
                "fetch_torrent_enabled": False,
            },
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(healthy=False, error_code="PHASE_NOT_ENABLED", message="本阶段未启用")

    async def validate_session(self) -> bool:
        return False

    async def get_account_state(self) -> dict[str, str | int | float | bool | None]:
        return {"enabled": False, "mode": "MOCK_ONLY"}

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        await self.limiter.acquire("search")
        results = self.fixtures
        if request.tmdb is not None:
            results = [item for item in results if item.tmdb_id == request.tmdb]
        if request.imdb is not None:
            results = [item for item in results if item.imdb_id == request.imdb]
        if request.type is not None:
            results = [item for item in results if item.media_type == request.type]
        if request.search:
            needle = request.search.casefold()
            results = [item for item in results if needle in item.title.casefold()]
        start = (request.page - 1) * request.limit
        return results[start : start + request.limit]

    async def get_torrent_details(self, torrent_id: str) -> TorrentDetails:
        await self.limiter.acquire("details")
        for item in self.fixtures:
            if item.torrent_id == torrent_id:
                return TorrentDetails(candidate=item, description="Mock fixture", files=[])
        raise AppError("TORRENT_NOT_FOUND", "Mock 中没有对应候选", status_code=404)

    async def fetch_torrent(self, torrent_id: str) -> bytes:
        del torrent_id
        raise AppError(
            "PHASE_NOT_ENABLED",
            "当前阶段禁止请求或下载 .torrent 文件",
            status_code=403,
        )


def example_candidate() -> TorrentCandidate:
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id="mock-1",
        release_title="Example 2026 1080p",
        details_ref="mock:mock-1",
        media_type=MediaType.MOVIE,
        tmdb_id=1,
        resolution="1080p",
        size_bytes=1024,
        seeders=1,
        published_at=datetime.now(UTC),
        match_score=1.0,
        match_reasons=["mock fixture"],
    )
