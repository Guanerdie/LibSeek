from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import ValidationError

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.errors import AppError
from app.schemas.adapters import AdapterManifest
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile


class QbittorrentReadOnlyAdapter(ReadOnlyDownloaderAdapter):
    _INFO_HASH = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
    _ALLOWED_REQUESTS = frozenset(
        {
            ("POST", "/api/v2/auth/login"),
            ("GET", "/api/v2/app/version"),
            ("GET", "/api/v2/app/webapiVersion"),
            ("GET", "/api/v2/torrents/info"),
            ("GET", "/api/v2/torrents/files"),
            ("GET", "/api/v2/torrents/categories"),
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        allowed_hosts: tuple[str, ...],
        allow_insecure_http: bool = False,
        connect_timeout: float = 5,
        read_timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = self._validate_url(
            base_url.rstrip("/"), allowed_hosts, allow_insecure_http
        )
        if not username or not password:
            raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时凭据未配置", status_code=409)
        self._username = username
        self._password = password
        self._authenticated = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/json, text/plain;q=0.9"},
        )

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="qbittorrent-read-only",
            name="qBittorrent",
            adapter_type="downloader",
            version="1.0",
            enabled=True,
            mode="LIVE_READ_ONLY",
            description="SID 会话只读状态、版本、种子、文件和分类查询",
            capabilities={
                "login": True,
                "version": True,
                "web_api_version": True,
                "list_torrents": True,
                "list_files": True,
                "list_categories": True,
                "write_operations": False,
            },
        )

    async def __aenter__(self) -> QbittorrentReadOnlyAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._clear_session()
        await self._client.aclose()

    async def authenticate(self) -> None:
        self._clear_session()
        try:
            response = await self._request(
                "POST",
                "/api/v2/auth/login",
                require_auth=False,
                data={"username": self._username, "password": self._password},
            )
            if self._looks_like_html(response):
                raise AppError(
                    "QB_RESPONSE_INVALID",
                    "qBittorrent 登录返回了 HTML 页面",
                    status_code=502,
                )
            if response.text.strip() != "Ok.":
                raise AppError("QB_AUTH_FAILED", "qBittorrent 登录失败", status_code=401)
            if not self._client.cookies.get("SID"):
                raise AppError(
                    "QB_RESPONSE_INVALID", "qBittorrent 登录未返回 SID", status_code=502
                )
            self._authenticated = True
        finally:
            if not self._authenticated:
                self._clear_session()

    async def get_version(self) -> str:
        return self._text(await self._request("GET", "/api/v2/app/version"), "应用版本")

    async def get_web_api_version(self) -> str:
        return self._text(
            await self._request("GET", "/api/v2/app/webapiVersion"), "Web API 版本"
        )

    async def list_torrents(self) -> list[QbTorrent]:
        payload = self._json(await self._request("GET", "/api/v2/torrents/info"), list)
        try:
            return [QbTorrent.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise self._validation_error("qBittorrent 种子字段校验失败", exc) from exc

    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]:
        normalized = self._normalize_hash(info_hash)
        payload = self._json(
            await self._request(
                "GET", "/api/v2/torrents/files", params={"hash": normalized}
            ),
            list,
        )
        try:
            return [QbTorrentFile.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise self._validation_error("qBittorrent 文件字段校验失败", exc) from exc

    async def get_categories(self) -> dict[str, QbCategory]:
        payload = self._json(
            await self._request("GET", "/api/v2/torrents/categories"), dict
        )
        try:
            return {
                str(name): QbCategory.model_validate({"name": str(name), **value})
                for name, value in payload.items()
                if isinstance(value, dict)
            }
        except ValidationError as exc:
            raise self._validation_error("qBittorrent 分类字段校验失败", exc) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        require_auth: bool = True,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        normalized_method = method.upper()
        if (normalized_method, path) not in self._ALLOWED_REQUESTS:
            raise AppError(
                "QB_REQUEST_NOT_ALLOWED",
                "qBittorrent 请求不在只读白名单中",
                status_code=405,
            )
        if require_auth and not self._authenticated:
            raise AppError("QB_NOT_AUTHENTICATED", "qBittorrent SID 会话不存在", status_code=401)
        url = urljoin(f"{self._base_url}/", path.lstrip("/"))
        try:
            response = await self._client.request(
                normalized_method, url, params=params, data=data, follow_redirects=False
            )
        except httpx.TimeoutException as exc:
            raise AppError(
                "QB_TIMEOUT", "qBittorrent 只读请求超时", status_code=504, retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                "QB_NETWORK_ERROR", "无法连接 qBittorrent", status_code=502, retryable=True
            ) from exc
        if response.status_code in {401, 403}:
            self._clear_session()
            raise AppError("QB_AUTH_FAILED", "qBittorrent 登录或 SID 会话无效", status_code=401)
        if not 200 <= response.status_code < 300:
            raise AppError(
                "QB_HTTP_ERROR",
                "qBittorrent 返回了非 2xx 响应",
                status_code=502,
                details={"status_code": response.status_code},
            )
        return response

    def _clear_session(self) -> None:
        self._authenticated = False
        self._client.cookies.clear()

    @staticmethod
    def _validate_url(
        value: str, allowed_hosts: tuple[str, ...], allow_insecure_http: bool
    ) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        allowed_schemes = {"https", "http"} if allow_insecure_http else {"https"}
        if (
            parsed.scheme not in allowed_schemes
            or not host
            or host not in allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise AppError("QB_URL_NOT_ALLOWED", "qBittorrent 地址不符合安全配置", status_code=400)
        return value

    @staticmethod
    def _looks_like_html(response: httpx.Response) -> bool:
        content_type = response.headers.get("content-type", "").casefold()
        prefix = response.content.lstrip()[:32].lower()
        return "text/html" in content_type or prefix.startswith((b"<!doctype html", b"<html"))

    @classmethod
    def _text(cls, response: httpx.Response, field: str) -> str:
        if cls._looks_like_html(response):
            raise AppError(
                "QB_RESPONSE_INVALID",
                f"qBittorrent {field}返回了 HTML",
                status_code=502,
            )
        value = response.text.strip()
        if not value or len(value) > 64 or any(character in value for character in "<>\r\n"):
            raise AppError("QB_RESPONSE_INVALID", f"qBittorrent {field}格式无效", status_code=502)
        return value

    @classmethod
    def _json(
        cls,
        response: httpx.Response,
        expected: type[list[Any]] | type[dict[str, Any]],
    ) -> Any:
        if cls._looks_like_html(response):
            raise AppError("QB_RESPONSE_INVALID", "qBittorrent 返回了 HTML 页面", status_code=502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AppError(
                "QB_RESPONSE_INVALID",
                "qBittorrent 响应不是有效 JSON",
                status_code=502,
            ) from exc
        if not isinstance(payload, expected):
            raise AppError("QB_RESPONSE_INVALID", "qBittorrent JSON 结构无效", status_code=502)
        return payload

    @staticmethod
    def _validation_error(message: str, exc: ValidationError) -> AppError:
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        return AppError(
            "QB_RESPONSE_INVALID",
            message,
            status_code=502,
            details={"invalid_fields": fields[:20]},
        )

    @classmethod
    def _normalize_hash(cls, value: str) -> str:
        if not cls._INFO_HASH.fullmatch(value):
            raise AppError("QB_INFO_HASH_INVALID", "info_hash 格式无效", status_code=422)
        return value.lower()
