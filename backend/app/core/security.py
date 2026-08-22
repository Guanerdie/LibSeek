from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from app.errors import AppError

SENSITIVE_KEY = re.compile(
    r"(password|passwd|cookie|authorization|token|pid|passkey|download_url|tracker|announce)",
    re.I,
)
URL_VALUE = re.compile(r"(?i)https?://[^\s<>'\"]+")
WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\)[^\r\n,;]+")
POSIX_PATH = re.compile(r"(?<![\w:])/(?!/)[^/\s,;]+(?:/[^\s,;]+)*")
PUBLIC_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
AddressResolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


def validate_external_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or host not in allowed_hosts:
        raise AppError(
            "EXTERNAL_HOST_NOT_ALLOWED",
            "外部地址不在允许访问的白名单中",
            status_code=400,
        )
    if parsed.username or parsed.password:
        raise AppError("INVALID_EXTERNAL_URL", "外部地址格式无效", status_code=400)
    return url


async def validate_public_external_target(
    url: str,
    allowed_hosts: tuple[str, ...],
    *,
    resolver: AddressResolver | None = None,
    resolve_timeout: float = 5.0,
    resolve_dns: bool = True,
) -> str:
    validated = validate_external_url(url, allowed_hosts)
    parsed = urlparse(validated)
    host = (parsed.hostname or "").casefold()
    labels = host.split(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise AppError(
            "EXTERNAL_TARGET_NOT_PUBLIC",
            "连接测试仅允许使用公网 DNS 主机名",
            status_code=400,
        )
    if (
        len(host) > 253
        or len(labels) < 2
        or host.endswith(".")
        or host == "localhost"
        or host.endswith(LOCAL_HOST_SUFFIXES)
        or any(PUBLIC_DNS_LABEL.fullmatch(label) is None for label in labels)
    ):
        raise AppError(
            "EXTERNAL_TARGET_NOT_PUBLIC",
            "连接测试仅允许使用公网 DNS 主机名",
            status_code=400,
        )
    if not resolve_dns:
        return validated
    target_resolver = resolver or _resolve_target_addresses
    try:
        addresses = await asyncio.wait_for(
            target_resolver(host, parsed.port or 443),
            timeout=max(0.1, resolve_timeout),
        )
        parsed_addresses = tuple(ipaddress.ip_address(value) for value in addresses)
    except (TimeoutError, OSError, ValueError) as exc:
        raise AppError(
            "EXTERNAL_TARGET_RESOLUTION_FAILED",
            "连接测试无法解析目标主机",
            status_code=502,
            retryable=True,
        ) from exc
    if not parsed_addresses or any(
        not address.is_global or address.is_multicast or address.is_unspecified
        for address in parsed_addresses
    ):
        raise AppError(
            "EXTERNAL_TARGET_NOT_PUBLIC",
            "连接测试拒绝访问非公网目标",
            status_code=400,
        )
    return validated


async def _resolve_target_addresses(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


def sanitize_details(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else sanitize_details(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_details(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_details(item) for item in value]
    if isinstance(value, str):
        value = sanitize_public_text(value)
    return value


def sanitize_public_text(value: str) -> str:
    value = re.sub(r"(?i)(passkey|token|pid)=([^&\s]+)", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)(authorization|cookie):\s*[^\r\n]+", r"\1: [REDACTED]", value)
    value = URL_VALUE.sub("[URL_REDACTED]", value)
    value = WINDOWS_PATH.sub("[PATH_REDACTED]", value)
    return POSIX_PATH.sub("[PATH_REDACTED]", value)
