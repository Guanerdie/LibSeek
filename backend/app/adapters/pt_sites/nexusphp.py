from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import math
import re
import socket
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, NoReturn
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from soupsieve.util import SelectorSyntaxError

from app.adapters.base import PtSiteAdapter
from app.adapters.pt_sites.profiles import (
    NexusPhpSiteProfile,
    normalize_public_dns_host,
    url_origin,
)
from app.core.http import SafeHttpResult, SerializedRateLimiter
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    ProbeResult,
    TorrentCandidate,
    TorrentDetails,
    TorrentSearchRequest,
)

_TORRENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
_RESOLUTION = re.compile(r"(?i)\b(2160p|1080p|1080i|720p|576p|480p)\b")
_YEAR = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_SIZE = re.compile(r"(?i)^\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)\s*$")
AddressResolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


async def _resolve_target_addresses(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


class SameOriginNexusSession:
    """Small bounded HTTP session whose every redirect must retain the origin."""

    def __init__(
        self,
        base_url: str,
        *,
        allowed_hosts: tuple[str, ...],
        transport: httpx.AsyncBaseTransport | None = None,
        address_resolver: AddressResolver | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_response_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        try:
            normalized_allowed_hosts = frozenset(
                normalize_public_dns_host(host) for host in allowed_hosts
            )
            base_host = normalize_public_dns_host(urlsplit(base_url).hostname or "")
        except ValueError as exc:
            raise AppError(
                "NEXUSPHP_HOST_NOT_ALLOWED",
                "NexusPHP 目标主机不符合安全边界",
                status_code=400,
            ) from exc
        if not normalized_allowed_hosts or base_host not in normalized_allowed_hosts:
            raise AppError(
                "NEXUSPHP_HOST_NOT_ALLOWED",
                "NexusPHP 目标主机不在显式白名单中",
                status_code=400,
            )
        self.base_url = base_url.rstrip("/")
        self.origin = url_origin(self.base_url)
        self.allowed_hosts = normalized_allowed_hosts
        self.max_response_bytes = max_response_bytes
        self.address_resolver = address_resolver or _resolve_target_addresses
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        self.client.cookies.clear()
        await self.client.aclose()

    def _same_origin_url(self, current_url: str, target: str) -> str:
        resolved = urljoin(current_url, target)
        parsed = urlsplit(resolved)
        try:
            target_origin = url_origin(resolved)
            target_host = normalize_public_dns_host(parsed.hostname or "")
        except ValueError as exc:
            raise AppError(
                "NEXUSPHP_INVALID_REDIRECT",
                "NexusPHP 返回了无效跳转地址",
                status_code=502,
            ) from exc
        if (
            parsed.scheme.casefold() != "https"
            or parsed.username is not None
            or parsed.password is not None
            or target_origin != self.origin
        ):
            raise AppError(
                "NEXUSPHP_CROSS_ORIGIN_REDIRECT",
                "NexusPHP 请求拒绝跨源跳转",
                status_code=502,
            )
        if target_host not in self.allowed_hosts:
            raise AppError(
                "NEXUSPHP_HOST_NOT_ALLOWED",
                "NexusPHP 目标主机不在显式白名单中",
                status_code=502,
            )
        return resolved

    async def _validate_resolved_target(self, value: str) -> None:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port or 443
        try:
            addresses = await self.address_resolver(host, port)
            parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
        except (OSError, ValueError) as exc:
            raise AppError(
                "NEXUSPHP_HOST_RESOLUTION_FAILED",
                "NexusPHP 目标主机解析失败",
                status_code=502,
                retryable=True,
            ) from exc
        if not parsed_addresses or any(not address.is_global for address in parsed_addresses):
            raise AppError(
                "NEXUSPHP_HOST_ADDRESS_NOT_ALLOWED",
                "NexusPHP 目标主机解析到了非公网地址",
                status_code=400,
            )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SafeHttpResult:
        current_url = self._same_origin_url(f"{self.base_url}/", path.lstrip("/"))
        current_method = method
        current_params = params
        for _ in range(4):
            await self._validate_resolved_target(current_url)
            request = self.client.build_request(
                current_method,
                current_url,
                params=current_params,
                headers=headers,
            )
            try:
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise AppError(
                    "NEXUSPHP_TIMEOUT",
                    "NexusPHP 只读请求超时",
                    status_code=504,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise AppError(
                    "NEXUSPHP_NETWORK_ERROR",
                    "NexusPHP 只读网络请求失败",
                    status_code=502,
                    retryable=True,
                ) from exc
            if response.is_redirect:
                location = response.headers.get("location")
                redirect_status = response.status_code
                await response.aclose()
                if not location:
                    raise AppError(
                        "NEXUSPHP_INVALID_REDIRECT",
                        "NexusPHP 返回了无效跳转",
                        status_code=502,
                    )
                current_url = self._same_origin_url(current_url, location)
                current_params = None
                if redirect_status in {301, 302, 303}:
                    current_method = "GET"
                continue
            content = bytearray()
            try:
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise AppError(
                            "NEXUSPHP_RESPONSE_TOO_LARGE",
                            "NexusPHP 响应超过安全上限",
                            status_code=502,
                        )
            finally:
                await response.aclose()
            return SafeHttpResult(
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                content=bytes(content),
                retry_after=response.headers.get("retry-after"),
            )
        raise AppError(
            "NEXUSPHP_TOO_MANY_REDIRECTS",
            "NexusPHP 跳转次数过多",
            status_code=502,
        )


class NexusPhpHtmlParser:
    def __init__(self, profile: NexusPhpSiteProfile) -> None:
        self.profile = profile

    def parse(
        self,
        html: bytes | str,
        requested_type: MediaType | None,
        *,
        limit: int = 100,
    ) -> list[TorrentCandidate]:
        if not 1 <= limit <= 100:
            raise ValueError("NexusPHP parser limit must be between 1 and 100")
        soup = BeautifulSoup(html, "html.parser")
        self._reject_unsupported_page(soup)
        rows = self._select(soup, self.profile.selectors.row)
        if len(rows) > 500:
            raise AppError(
                "NEXUSPHP_TOO_MANY_ROWS",
                "NexusPHP 搜索结果超过安全上限",
                status_code=502,
            )
        candidates: list[TorrentCandidate] = []
        seen_ids: set[str] = set()
        for row in rows[:limit]:
            candidate = self._parse_row(row, requested_type)
            if candidate.torrent_id in seen_ids:
                raise AppError(
                    "NEXUSPHP_DUPLICATE_TORRENT_ID",
                    "NexusPHP 搜索页包含重复种子 ID",
                    status_code=502,
                )
            seen_ids.add(candidate.torrent_id)
            candidates.append(candidate)
        return candidates

    def _reject_unsupported_page(self, soup: BeautifulSoup) -> None:
        states = (
            (
                self.profile.page_states.challenge,
                "NEXUSPHP_CHALLENGE_UNSUPPORTED",
                "站点返回了不受支持的浏览器挑战；不会尝试绕过",
            ),
            (
                self.profile.page_states.captcha,
                "NEXUSPHP_CAPTCHA_REQUIRED",
                "站点要求验证码；必须由用户在浏览器中处理",
            ),
            (
                self.profile.page_states.login,
                "NEXUSPHP_LOGIN_REQUIRED",
                "NexusPHP 会话已失效或尚未登录",
            ),
        )
        for selector, error_code, message in states:
            if self._select_one(soup, selector) is not None:
                raise AppError(error_code, message, status_code=409)

    def _parse_row(self, row: Tag, requested_type: MediaType | None) -> TorrentCandidate:
        selectors = self.profile.selectors
        link = self._required_tag(row, selectors.details_link, "details_link")
        href = link.get("href")
        if not isinstance(href, str) or not href:
            self._missing("details_link")
        details_url = urljoin(f"{self.profile.base_url}/", href)
        try:
            details_origin = url_origin(details_url)
        except ValueError as exc:
            raise AppError(
                "NEXUSPHP_DETAILS_ORIGIN_NOT_ALLOWED",
                "种子详情链接格式无效",
                status_code=502,
            ) from exc
        if details_origin != url_origin(self.profile.base_url):
            raise AppError(
                "NEXUSPHP_DETAILS_ORIGIN_NOT_ALLOWED",
                "种子详情链接与 Profile 不同源",
                status_code=502,
            )
        torrent_values = parse_qs(urlsplit(details_url).query).get(
            self.profile.torrent_id_query_parameter
        )
        torrent_id = torrent_values[0] if torrent_values else None
        if torrent_id is None:
            self._missing("torrent_id")
        if _TORRENT_ID.fullmatch(torrent_id) is None:
            self._invalid("torrent_id")

        title_tag = self._required_tag(row, selectors.title, "title")
        if selectors.title_attribute:
            raw_title = title_tag.get(selectors.title_attribute)
            title = raw_title.strip() if isinstance(raw_title, str) else ""
        else:
            title = title_tag.get_text(" ", strip=True)
        if not title:
            self._missing("title")
        if len(title) > 500:
            self._invalid("title")

        size = self._parse_size(
            self._required_tag(row, selectors.size, "size").get_text(" ", strip=True)
        )
        seeders = self._parse_integer(
            self._required_tag(row, selectors.seeders, "seeders").get_text(" ", strip=True),
            "seeders",
        )
        leechers = self._optional_integer(row, selectors.leechers, "leechers")
        completed = self._optional_integer(row, selectors.completed, "completed")
        category = (
            self._required_tag(row, selectors.category, "category").get_text(" ", strip=True)
            if selectors.category is not None
            else None
        )
        if category is not None:
            media_type = self.profile.category_media_types.get(category)
            if media_type is None:
                raise AppError(
                    "NEXUSPHP_CATEGORY_UNMAPPED",
                    "NexusPHP 页面返回了 Profile 未声明的分类",
                    status_code=502,
                )
        else:
            media_type = requested_type
        if media_type is None:
            self._missing("media_type")
        discount_text = self._optional_text(row, selectors.discount)
        download_factor = self._discount_factor(discount_text)
        published_at = self._parse_datetime(self._optional_text(row, selectors.published_at))
        year_matches = _YEAR.findall(title)
        resolution_match = _RESOLUTION.search(title)
        opaque = hashlib.sha256(
            f"{self.profile.site_id}|{torrent_id}".encode()
        ).hexdigest()[:32]
        return TorrentCandidate(
            site_id=self.profile.site_id,
            torrent_id=torrent_id,
            release_title=title,
            details_ref=f"{self.profile.site_id}:details:{opaque}",
            media_type=media_type,
            year=int(year_matches[-1]) if year_matches else None,
            resolution=resolution_match.group(1).lower() if resolution_match else None,
            size_bytes=size,
            seeders=seeders,
            leechers=leechers,
            completed=completed,
            download_factor=download_factor,
            hit_and_run=None,
            published_at=published_at,
            warnings=["HNR_STATUS_UNKNOWN"],
        )

    def _required_tag(self, row: Tag, selector: str, field: str) -> Tag:
        value = self._select_one(row, selector)
        if not isinstance(value, Tag):
            self._missing(field)
        return value

    def _optional_text(self, row: Tag, selector: str | None) -> str | None:
        if selector is None:
            return None
        value = self._select_one(row, selector)
        if not isinstance(value, Tag):
            return None
        text = value.get_text(" ", strip=True)
        return text or None

    def _optional_integer(self, row: Tag, selector: str | None, field: str) -> int | None:
        text = self._optional_text(row, selector)
        return self._parse_integer(text, field) if text is not None else None

    def _discount_factor(self, text: str | None) -> float | None:
        if text is None:
            return None
        normalized = text.casefold()
        for label, factor in self.profile.discount_text_factors.items():
            if label.casefold() in normalized:
                return factor
        return None

    @staticmethod
    def _parse_integer(value: str, field: str) -> int:
        compact = value.replace(",", "").strip()
        if not compact.isdigit() or len(compact) > 19:
            NexusPhpHtmlParser._invalid(field)
        parsed = int(compact)
        if parsed > 2**63 - 1:
            NexusPhpHtmlParser._invalid(field)
        return parsed

    @staticmethod
    def _parse_size(value: str) -> int:
        match = _SIZE.fullmatch(value)
        if match is None:
            NexusPhpHtmlParser._invalid("size")
        number = float(match.group(1))
        if not math.isfinite(number):
            NexusPhpHtmlParser._invalid("size")
        unit = match.group(2).upper()
        binary = unit.endswith("IB")
        normalized = unit.replace("IB", "B")
        exponent = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4}[normalized]
        parsed = int(number * ((1024 if binary else 1000) ** exponent))
        if parsed > 2**63 - 1:
            NexusPhpHtmlParser._invalid("size")
        return parsed

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=UTC
                )
            except ValueError:
                return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _missing(field: str) -> NoReturn:
        raise AppError(
            "NEXUSPHP_FIELD_MISSING",
            "NexusPHP HTML 缺少 Profile 要求的字段",
            status_code=502,
            details={"field": field},
        )

    @staticmethod
    def _invalid(field: str) -> NoReturn:
        raise AppError(
            "NEXUSPHP_FIELD_INVALID",
            "NexusPHP HTML 字段格式无效",
            status_code=502,
            details={"field": field},
        )

    @staticmethod
    def _select(root: BeautifulSoup | Tag, selector: str) -> list[Tag]:
        try:
            return [value for value in root.select(selector) if isinstance(value, Tag)]
        except SelectorSyntaxError as exc:
            raise AppError(
                "NEXUSPHP_PROFILE_INVALID_SELECTOR",
                "NexusPHP Profile 包含无效 CSS selector",
                status_code=500,
            ) from exc

    @staticmethod
    def _select_one(root: BeautifulSoup | Tag, selector: str) -> Tag | None:
        try:
            value = root.select_one(selector)
            return value if isinstance(value, Tag) else None
        except SelectorSyntaxError as exc:
            raise AppError(
                "NEXUSPHP_PROFILE_INVALID_SELECTOR",
                "NexusPHP Profile 包含无效 CSS selector",
                status_code=500,
            ) from exc


class NexusPhpAdapter(PtSiteAdapter):
    """Conservative adapter skeleton for an explicitly reviewed site Profile."""

    def __init__(
        self,
        profile: NexusPhpSiteProfile,
        *,
        allowed_hosts: tuple[str, ...],
        cookie_header: str | None = None,
        passkey: str | None = None,
        enable_live_search: bool = False,
        enable_torrent_fetch: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        address_resolver: AddressResolver | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_response_bytes: int = 10 * 1024 * 1024,
        min_interval_seconds: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        request_gate: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
    ) -> None:
        self.profile = profile
        self._cookie_header = self._validate_memory_secret(cookie_header, "cookie")
        self._passkey = self._validate_memory_secret(passkey, "passkey")
        self.enable_live_search = enable_live_search
        self.enable_torrent_fetch = enable_torrent_fetch
        if (enable_live_search or enable_torrent_fetch) and request_gate is None:
            raise AppError(
                "NEXUSPHP_REQUEST_GATE_REQUIRED",
                "启用 NexusPHP 实时能力必须配置跨 Worker 站点请求门",
                status_code=409,
            )
        self.limiter = SerializedRateLimiter(min_interval_seconds, sleep=sleep)
        self.request_gate = request_gate
        self.parser = NexusPhpHtmlParser(profile)
        self.session = SameOriginNexusSession(
            profile.base_url,
            allowed_hosts=allowed_hosts,
            transport=transport,
            address_resolver=address_resolver,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
        )
        self._candidate_memory: dict[str, TorrentCandidate] = {}

    def __repr__(self) -> str:
        return (
            f"NexusPhpAdapter(site_id={self.profile.site_id!r}, "
            f"live_search={self.enable_live_search!r}, "
            f"torrent_fetch={self.enable_torrent_fetch!r})"
        )

    @staticmethod
    def _validate_memory_secret(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        invalid_ascii = any(not 32 <= ord(character) <= 126 for character in value)
        invalid_passkey_space = name == "passkey" and any(
            character.isspace() for character in value
        )
        if not value or len(value) > 4096 or invalid_ascii or invalid_passkey_space:
            raise AppError(
                "NEXUSPHP_SECRET_INVALID",
                f"NexusPHP {name} 运行时值格式无效",
                status_code=400,
            )
        return value

    def manifest(self) -> AdapterManifest:
        enabled = self.profile.enabled and self.enable_live_search
        return AdapterManifest(
            id=self.profile.site_id,
            name=self.profile.display_name,
            adapter_type="pt_site",
            version="0.1",
            enabled=enabled,
            mode="LIVE_READ_ONLY_SEARCH" if enabled else "DISABLED_BY_DEFAULT",
            description="声明式 NexusPHP 只读搜索骨架；仅适用于经过 fixture 验证的指定站点",
            capabilities={
                "text_search": True,
                "category_filter": True,
                "tmdb_search": self.profile.query.tmdb is not None,
                "imdb_search": self.profile.query.imdb is not None,
                "tvdb_search": self.profile.query.tvdb is not None,
                "captcha_bypass": False,
                "challenge_bypass": False,
                "fetch_torrent_enabled": self.enable_torrent_fetch,
                "write_operations": False,
            },
        )

    async def aclose(self) -> None:
        self._cookie_header = None
        self._passkey = None
        self._candidate_memory.clear()
        await self.session.aclose()

    async def probe(self) -> ProbeResult:
        if not self.profile.enabled or not self.enable_live_search:
            return ProbeResult(
                healthy=False,
                error_code="NEXUSPHP_SITE_DISABLED",
                message="该 NexusPHP 站点 Profile 或实时搜索开关未启用",
            )
        if self._cookie_header is None:
            return ProbeResult(
                healthy=False,
                error_code="NEXUSPHP_SESSION_NOT_CONFIGURED",
                message="NexusPHP 运行时会话未配置",
            )
        return ProbeResult(healthy=True, message="NexusPHP 只读适配器已配置，未执行网络探测")

    async def validate_session(self) -> bool:
        return bool(
            self.profile.enabled and self.enable_live_search and self._cookie_header is not None
        )

    async def get_account_state(self) -> dict[str, str | int | float | bool | None]:
        return {
            "site_id": self.profile.site_id,
            "profile_enabled": self.profile.enabled,
            "live_search_enabled": self.enable_live_search,
            "session_configured_in_memory": self._cookie_header is not None,
            "torrent_fetch_enabled": self.enable_torrent_fetch,
        }

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        self._require_live_search()
        params = self._search_params(request)
        if params is None:
            return []
        response = await self._request(
            "GET",
            self.profile.search_path,
            operation="search",
            params=params,
            headers=self._headers("text/html,application/xhtml+xml"),
        )
        self._validate_search_response(response)
        candidates = self.parser.parse(response.content, request.type, limit=request.limit)
        self._candidate_memory.update(
            {candidate.torrent_id: candidate for candidate in candidates}
        )
        return candidates

    async def get_torrent_details(self, torrent_id: str) -> TorrentDetails:
        candidate = self._candidate_memory.get(torrent_id)
        if candidate is None:
            raise AppError(
                "TORRENT_REFERENCE_NOT_IN_SESSION",
                "当前 NexusPHP 内存会话中没有该候选引用",
                status_code=409,
            )
        return TorrentDetails(candidate=candidate, description=None, files=[])

    async def fetch_torrent(self, torrent_id: str) -> bytes:
        if not self.profile.enabled or not self.enable_torrent_fetch:
            raise AppError(
                "NEXUSPHP_TORRENT_FETCH_DISABLED",
                "NexusPHP 种子获取默认关闭",
                status_code=403,
            )
        if self._cookie_header is None:
            raise AppError(
                "NEXUSPHP_SESSION_NOT_CONFIGURED",
                "NexusPHP 运行时会话未配置",
                status_code=409,
            )
        if torrent_id not in self._candidate_memory:
            raise AppError(
                "TORRENT_REFERENCE_NOT_IN_SESSION",
                "必须先在同一内存会话中重新搜索精确种子 ID",
                status_code=409,
            )
        if "{passkey}" in self.profile.download_path and self._passkey is None:
            raise AppError(
                "NEXUSPHP_PASSKEY_NOT_CONFIGURED",
                "该 Profile 的下载路径需要运行时 passkey",
                status_code=409,
            )
        path = self.profile.download_path.format(
            torrent_id=quote(torrent_id, safe=""),
            passkey=quote(self._passkey or "", safe=""),
        )
        response = await self._request(
            "GET",
            path,
            operation="torrent_fetch",
            headers=self._headers("application/x-bittorrent,application/octet-stream"),
        )
        if not 200 <= response.status_code < 300:
            raise AppError(
                "NEXUSPHP_TORRENT_FETCH_FAILED",
                "NexusPHP 种子获取失败",
                status_code=502,
            )
        if "html" in response.content_type.casefold() or not response.content.startswith(b"d"):
            raise AppError(
                "NEXUSPHP_TORRENT_INVALID_RESPONSE",
                "NexusPHP 种子响应格式无效",
                status_code=502,
            )
        return response.content

    def _require_live_search(self) -> None:
        if not self.profile.enabled or not self.enable_live_search:
            raise AppError(
                "NEXUSPHP_SITE_DISABLED",
                "该 NexusPHP 站点 Profile 或实时搜索开关未启用",
                status_code=409,
            )
        if self._cookie_header is None:
            raise AppError(
                "NEXUSPHP_SESSION_NOT_CONFIGURED",
                "NexusPHP 运行时会话未配置",
                status_code=409,
            )

    def _headers(self, accept: str) -> dict[str, str]:
        cookie = self._cookie_header
        if cookie is None:
            raise AppError(
                "NEXUSPHP_SESSION_NOT_CONFIGURED",
                "NexusPHP 运行时会话未配置",
                status_code=409,
            )
        return {"Accept": accept, "Cookie": cookie}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> SafeHttpResult:
        gate = self.request_gate
        if gate is None:
            raise AppError(
                "NEXUSPHP_REQUEST_GATE_REQUIRED",
                "NexusPHP 实时请求缺少跨 Worker 站点请求门",
                status_code=409,
            )
        async with gate(operation):
            await self.limiter.acquire(operation)
            return await self.session.request(method, path, params=params, headers=headers)

    def _search_params(self, request: TorrentSearchRequest) -> dict[str, Any] | None:
        query = self.profile.query
        params: dict[str, Any] = {}
        has_search_term = False
        if request.search:
            params[query.text] = request.search
            has_search_term = True
        for value, field_name in (
            (request.tmdb, query.tmdb),
            (request.imdb, query.imdb),
            (request.tvdb, query.tvdb),
        ):
            if value is not None and field_name is not None:
                params[field_name] = value
                has_search_term = True
        if not has_search_term:
            return None
        if request.type is not None:
            categories = self.profile.category_mapping.get(request.type)
            if categories:
                params[query.category] = list(categories)
        if query.page is not None:
            params[query.page] = request.page
        return params

    @staticmethod
    def _validate_search_response(response: SafeHttpResult) -> None:
        if response.status_code in {401, 403}:
            raise AppError(
                "NEXUSPHP_LOGIN_REQUIRED",
                "NexusPHP 会话已失效或尚未登录",
                status_code=409,
            )
        if response.status_code == 429:
            retry_after = NexusPhpAdapter._retry_after_seconds(response.retry_after)
            raise AppError(
                "NEXUSPHP_RATE_LIMITED",
                "NexusPHP 请求受到限速",
                status_code=429,
                retryable=True,
                details={"retry_after_seconds": retry_after} if retry_after is not None else None,
            )
        if response.status_code >= 500:
            raise AppError(
                "NEXUSPHP_UNAVAILABLE",
                "NexusPHP 站点暂时不可用",
                status_code=502,
                retryable=True,
            )
        if not 200 <= response.status_code < 300:
            raise AppError(
                "NEXUSPHP_HTTP_ERROR",
                "NexusPHP 返回了无法处理的状态",
                status_code=502,
            )
        if "html" not in response.content_type.casefold():
            raise AppError(
                "NEXUSPHP_NON_HTML_RESPONSE",
                "NexusPHP 搜索响应不是 HTML",
                status_code=502,
            )

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            seconds = (parsed - datetime.now(UTC)).total_seconds()
        if not math.isfinite(seconds):
            return None
        return min(3600.0, max(0.0, seconds))
