from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from app.errors import AppError
from app.models.enums import AuthRole

SESSION_COOKIE_NAME = "unin_session"
CSRF_COOKIE_NAME = "unin_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
PASSWORD_DIGEST_ALGORITHM = "pbkdf2_sha256"
PASSWORD_DIGEST_VERSION = "v1"
PASSWORD_DIGEST_ITERATIONS = 600_000
_PASSWORD_SALT_BYTES = 16
_PASSWORD_DERIVED_KEY_BYTES = 32

_ROLE_RANK = {
    AuthRole.VIEWER: 10,
    AuthRole.OPERATOR: 20,
    AuthRole.ADMIN: 30,
}


@dataclass(frozen=True)
class Principal:
    username: str
    role: AuthRole
    issued_at: int
    expires_at: int
    csrf_digest: str


@dataclass(frozen=True)
class AuthMaterial:
    username: str
    credential_kind: Literal["plaintext", "pbkdf2_sha256"]
    role: AuthRole
    credential: str = field(repr=False)
    signing_key: str = field(repr=False)


def new_csrf_token() -> str:
    return f"s1_{secrets.token_urlsafe(32)}"


def create_session_token(
    *,
    username: str,
    role: AuthRole,
    csrf_token: str,
    signing_key: str,
    ttl_seconds: int,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {
        "sub": username,
        "role": role.value,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
        "csrf": _csrf_digest(csrf_token),
    }
    encoded = _b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )
    signed = f"v1.{encoded}"
    return f"{signed}.{_sign(signing_key, signed)}"


def parse_session_token(
    token: str,
    signing_key: str,
    *,
    now: int | None = None,
) -> Principal:
    if len(token) > 4096:
        raise _invalid_session()
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise _invalid_session()
    signed = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(parts[2], _sign(signing_key, signed)):
        raise _invalid_session()
    try:
        payload: Any = json.loads(_b64decode(parts[1]))
        username = payload["sub"]
        role = AuthRole(payload["role"])
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        csrf_digest = payload["csrf"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_session() from exc
    if (
        not isinstance(username, str)
        or not 1 <= len(username) <= 120
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or not isinstance(csrf_digest, str)
        or len(csrf_digest) != 64
    ):
        raise _invalid_session()
    current = int(time.time()) if now is None else now
    if issued_at > current + 60:
        raise _invalid_session()
    if expires_at <= current:
        raise AppError("AUTH_SESSION_EXPIRED", "登录会话已过期，请重新登录", status_code=401)
    return Principal(
        username=username,
        role=role,
        issued_at=issued_at,
        expires_at=expires_at,
        csrf_digest=csrf_digest,
    )


def create_bootstrap_csrf(signing_key: str, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    signed = f"b1.{issued_at}.{secrets.token_urlsafe(24)}"
    return f"{signed}.{_sign(signing_key, signed)}"


def validate_bootstrap_csrf(
    *,
    cookie_token: str | None,
    header_token: str | None,
    signing_key: str,
    ttl_seconds: int,
    now: int | None = None,
) -> None:
    token = _matching_csrf_tokens(cookie_token, header_token)
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "b1":
        raise _invalid_csrf()
    signed = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _sign(signing_key, signed)):
        raise _invalid_csrf()
    try:
        issued_at = int(parts[1])
    except ValueError as exc:
        raise _invalid_csrf() from exc
    current = int(time.time()) if now is None else now
    if issued_at > current + 60 or current - issued_at > ttl_seconds:
        raise AppError("CSRF_TOKEN_EXPIRED", "CSRF 令牌已过期，请重新获取", status_code=403)


def validate_session_csrf(
    *,
    cookie_token: str | None,
    header_token: str | None,
    principal: Principal,
) -> None:
    token = _matching_csrf_tokens(cookie_token, header_token)
    if not hmac.compare_digest(_csrf_digest(token), principal.csrf_digest):
        raise _invalid_csrf()


def verify_local_credentials(
    *,
    username: str,
    password: str,
    expected_username: str,
    expected_password: str,
) -> bool:
    username_matches = hmac.compare_digest(
        _credential_digest(username), _credential_digest(expected_username)
    )
    password_matches = hmac.compare_digest(
        _credential_digest(password), _credential_digest(expected_password)
    )
    return username_matches and password_matches


def create_password_digest(
    password: str,
    *,
    iterations: int = PASSWORD_DIGEST_ITERATIONS,
) -> str:
    if iterations < 1:
        raise ValueError("password digest iterations must be positive")
    salt = secrets.token_bytes(_PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_PASSWORD_DERIVED_KEY_BYTES,
    )
    return "$".join(
        (
            PASSWORD_DIGEST_ALGORITHM,
            PASSWORD_DIGEST_VERSION,
            str(iterations),
            _b64encode(salt),
            _b64encode(digest),
        )
    )


def verify_password_digest(password: str, encoded_digest: str) -> bool:
    try:
        algorithm, version, raw_iterations, raw_salt, raw_expected = encoded_digest.split("$")
        if algorithm != PASSWORD_DIGEST_ALGORITHM or version != PASSWORD_DIGEST_VERSION:
            return False
        iterations = int(raw_iterations)
        if not 1 <= iterations <= PASSWORD_DIGEST_ITERATIONS:
            return False
        salt = _decode_password_component(raw_salt, expected_length=_PASSWORD_SALT_BYTES)
        expected = _decode_password_component(
            raw_expected,
            expected_length=_PASSWORD_DERIVED_KEY_BYTES,
        )
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_PASSWORD_DERIVED_KEY_BYTES,
    )
    return hmac.compare_digest(actual, expected)


def verify_runtime_credentials(
    *,
    username: str,
    password: str,
    expected_username: str,
    expected_password_digest: str,
) -> bool:
    username_matches = hmac.compare_digest(
        _credential_digest(username), _credential_digest(expected_username)
    )
    password_matches = verify_password_digest(password, expected_password_digest)
    return username_matches and password_matches


def verify_auth_material_credentials(
    *,
    username: str,
    password: str,
    material: AuthMaterial,
) -> bool:
    if material.credential_kind == "plaintext":
        return verify_local_credentials(
            username=username,
            password=password,
            expected_username=material.username,
            expected_password=material.credential,
        )
    return verify_runtime_credentials(
        username=username,
        password=password,
        expected_username=material.username,
        expected_password_digest=material.credential,
    )


def role_allows(actual: AuthRole, required: AuthRole) -> bool:
    return _ROLE_RANK[actual] >= _ROLE_RANK[required]


def _matching_csrf_tokens(cookie_token: str | None, header_token: str | None) -> str:
    if not cookie_token or not header_token:
        raise AppError("CSRF_TOKEN_REQUIRED", "状态变更请求缺少 CSRF 令牌", status_code=403)
    if len(cookie_token) > 1024 or len(header_token) > 1024:
        raise _invalid_csrf()
    if not hmac.compare_digest(cookie_token, header_token):
        raise _invalid_csrf()
    return cookie_token


def _credential_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _csrf_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sign(key: str, value: str) -> str:
    digest = hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}")
    except (binascii.Error, ValueError, TypeError) as exc:
        raise _invalid_session() from exc


def _decode_password_component(value: str, *, expected_length: int) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            f"{value}{padding}",
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError("invalid password digest encoding") from exc
    if len(decoded) != expected_length:
        raise ValueError("invalid password digest component length")
    return decoded


def _invalid_session() -> AppError:
    return AppError("AUTH_SESSION_INVALID", "登录会话无效，请重新登录", status_code=401)


def _invalid_csrf() -> AppError:
    return AppError("CSRF_TOKEN_INVALID", "CSRF 令牌无效", status_code=403)
