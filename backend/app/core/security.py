from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from app.errors import AppError

SENSITIVE_KEY = re.compile(
    r"(password|passwd|cookie|authorization|token|pid|passkey|download_url|tracker|announce)",
    re.I,
)


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
        value = re.sub(r"(?i)(passkey|token|pid)=([^&\s]+)", r"\1=[REDACTED]", value)
        value = re.sub(r"(?i)(authorization|cookie):\s*[^\r\n]+", r"\1: [REDACTED]", value)
    return value
