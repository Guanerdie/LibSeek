from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from weakref import WeakKeyDictionary

import httpx

from app.core.security import validate_external_url
from app.errors import AppError

# Real desktop browser strings.  The point is not to impersonate anyone -- it
# is that httpx's default "python-httpx/0.28.1" is the single most obvious
# automation signal a PT site can read, and several of them block on it
# outright.
BROWSER_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:141.0) Gecko/20100101 Firefox/141.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
)


def pick_user_agent(seed: str | None = None) -> str:
    """Choose one browser string, stable for a given ``seed``.

    Deliberately *not* rotated per request.  A logged-in session whose
    User-Agent changes between requests is a stronger bot signal than a boring
    one that never changes -- real browsers do not do that.  Seeding by site id
    means one deployment presents one consistent identity per site, while
    different deployments do not all look identical.
    """

    if seed is None:
        return random.choice(BROWSER_USER_AGENTS)
    digest = hashlib.sha256(seed.encode()).digest()
    return BROWSER_USER_AGENTS[digest[0] % len(BROWSER_USER_AGENTS)]


def backoff_delay(
    attempt: int,
    *,
    retry_after: float | None = None,
    base_seconds: float = 2.0,
    jitter_seconds: float = 1.0,
    random_source: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with jitter, never shorter than ``Retry-After``.

    The jitter matters as much as the growth: a fleet of retries landing on
    exactly 2s, 4s, 8s is itself a recognisable pattern, and identical delays
    make every client retry in lockstep after a site-wide blip.
    """

    # 2.0 rather than 2: mypy types ``int ** int`` as Any, because a negative
    # exponent would produce a float.
    delay = base_seconds * (2.0 ** max(0, attempt))
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay + random_source() * max(0.0, jitter_seconds)


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
        proxy: httpx.Proxy | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.base_url = validate_external_url(base_url.rstrip("/"), allowed_hosts)
        self.allowed_hosts = allowed_hosts
        self.max_response_bytes = max_response_bytes
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            proxy=proxy,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": user_agent} if user_agent else None,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urlparse(url)
        default_port = 443 if parsed.scheme.casefold() == "https" else 80
        return (
            parsed.scheme.casefold(),
            (parsed.hostname or "").casefold(),
            parsed.port or default_port,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
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
                if before_send is not None:
                    await before_send()
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise AppError(
                    "UPSTREAM_TIMEOUT",
                    "外部只读请求超时",
                    status_code=504,
                    retryable=True,
                ) from exc
            except httpx.ProxyError as exc:
                proxy_auth_failed = "407" in str(exc)
                raise AppError(
                    (
                        "OUTBOUND_PROXY_AUTH_FAILED"
                        if proxy_auth_failed
                        else "OUTBOUND_PROXY_CONNECTION_FAILED"
                    ),
                    "出站代理认证失败" if proxy_auth_failed else "出站代理连接失败",
                    status_code=502,
                    retryable=not proxy_auth_failed,
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
                redirect_url = urljoin(current_url, location)
                validate_external_url(redirect_url, self.allowed_hosts)
                has_authorization = any(
                    key.casefold() == "authorization" for key in (headers or {})
                )
                if (current_body is not None or has_authorization) and self._origin(
                    redirect_url
                ) != self._origin(current_url):
                    raise AppError(
                        "UPSTREAM_CROSS_ORIGIN_REDIRECT",
                        "携带认证信息的外部请求拒绝跨源跳转",
                        status_code=502,
                    )
                current_url = redirect_url
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
            except httpx.TimeoutException as exc:
                raise AppError(
                    "UPSTREAM_TIMEOUT",
                    "外部只读请求响应读取超时",
                    status_code=504,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise AppError(
                    "UPSTREAM_NETWORK_ERROR",
                    "外部只读请求响应读取中断",
                    status_code=502,
                    retryable=True,
                ) from exc
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


_shared_rate_limiters: MutableMapping[
    asyncio.AbstractEventLoop, dict[tuple[str, float], SerializedRateLimiter]
] = WeakKeyDictionary()


def shared_rate_limiter(key: str, min_interval_seconds: float) -> SerializedRateLimiter:
    """Return the process-wide limiter for ``key``, creating it on first use.

    Adapters are built per search and closed immediately afterwards, so a
    limiter owned by the adapter starts over on every job: its
    ``_last_request_at`` is ``None`` again and the very first request of the
    next job goes out with no delay.  A run of twenty jobs therefore hit the
    site at full speed even though ``min_interval_seconds`` was configured, and
    the setting only ever throttled requests *within* a single search.  Sharing
    the limiter across instances is what makes the interval hold end to end.

    The registry is keyed by event loop because ``asyncio.Lock`` binds to the
    loop that first awaits it; a limiter leaking across loops would raise
    "bound to a different event loop" in the next test.  It is also keyed by
    the interval so that changing the setting at runtime yields a fresh
    limiter rather than silently keeping the old pace.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop yet: sharing would be meaningless, and caching under a loop
        # we cannot name would strand the entry.
        return SerializedRateLimiter(min_interval_seconds)
    interval = max(0.0, min_interval_seconds)
    limiters = _shared_rate_limiters.setdefault(loop, {})
    limiter = limiters.get((key, interval))
    if limiter is None:
        limiter = SerializedRateLimiter(interval)
        limiters[(key, interval)] = limiter
    return limiter


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
