from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.security import validate_external_url
from app.errors import AppError


@dataclass(frozen=True)
class SafeHttpResult:
    status_code: int
    content_type: str
    content: bytes
    retry_after: str | None

    def json(self) -> Any:
        if "json" not in self.content_type.casefold():
            raise AppError("UPSTREAM_NON_JSON", "上游返回了非 JSON 响应", status_code=502)
        try:
            return json.loads(self.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AppError(
                "UPSTREAM_INVALID_JSON", "上游 JSON 响应格式无效", status_code=502
            ) from exc


class SafeAsyncHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: tuple[str, ...],
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = validate_external_url(base_url.rstrip("/"), allowed_hosts)
        self.allowed_hosts = allowed_hosts
        self.max_response_bytes = max_response_bytes
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> SafeHttpResult:
        current_url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        current_method = method
        current_params = params
        current_body = json_body
        for _ in range(4):
            validate_external_url(current_url, self.allowed_hosts)
            request = self.client.build_request(
                current_method,
                current_url,
                params=current_params,
                json=current_body,
                headers=headers,
            )
            try:
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise AppError(
                    "UPSTREAM_TIMEOUT",
                    "外部只读请求超时",
                    status_code=504,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise AppError(
                    "UPSTREAM_NETWORK_ERROR",
                    "外部只读网络请求失败",
                    status_code=502,
                    retryable=True,
                ) from exc
            if response.is_redirect:
                location = response.headers.get("location")
                redirect_status = response.status_code
                await response.aclose()
                if not location:
                    raise AppError("UPSTREAM_INVALID_REDIRECT", "上游返回了无效跳转")
                current_url = urljoin(current_url, location)
                validate_external_url(current_url, self.allowed_hosts)
                current_params = None
                if redirect_status in {301, 302, 303}:
                    current_method, current_body = "GET", None
                continue
            content = bytearray()
            try:
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise AppError(
                            "UPSTREAM_RESPONSE_TOO_LARGE",
                            "上游响应体超过安全限制",
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
        raise AppError("UPSTREAM_TOO_MANY_REDIRECTS", "上游跳转次数过多", status_code=502)


class SerializedRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.monotonic = monotonic
        self.sleep = sleep
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    async def acquire(self, operation: str) -> None:
        del operation
        async with self._lock:
            now = self.monotonic()
            if self._last_request_at is not None:
                wait = self.min_interval_seconds - (now - self._last_request_at)
                if wait > 0:
                    await self.sleep(wait)
            self._last_request_at = self.monotonic()


class AsyncTtlCache[T]:
    def __init__(self, *, ttl_seconds: int, max_entries: int) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._values: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            cached = self._values.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= time.monotonic():
                del self._values[key]
                return None
            self._values.move_to_end(key)
            return value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            self._values[key] = (time.monotonic() + self.ttl_seconds, value)
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)
