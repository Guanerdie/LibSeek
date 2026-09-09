"""Changing the password, and staying signed in on a trusted device.

Neither existed.  The only way to change the password was to delete the runtime
credential file and run setup again, and every session expired after eight
hours whether or not the device was the owner's own laptop.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.core.auth import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from app.core.config import Settings, get_settings
from app.main import app

PASSWORD = "first-pass"


def runtime_settings(root: Path) -> Settings:
    return Settings(
        _env_file=None,
        runtime_config_dir=root,
        auth_local_username=None,
        auth_local_password=None,
        auth_session_signing_key=None,
        auth_local_username_file=None,
        auth_local_password_file=None,
        auth_session_signing_key_file=None,
    )


async def _bootstrap(client: httpx.AsyncClient) -> str:
    """Create the administrator and return the session's CSRF token."""

    response = await client.post(
        "/api/auth/setup", json={"username": "owner", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


async def _login(client: httpx.AsyncClient, *, password: str = PASSWORD, **extra: object):
    csrf = (await client.get("/api/auth/csrf")).json()["csrf_token"]
    return await client.post(
        "/api/auth/login",
        json={"username": "owner", "password": password, **extra},
        headers={CSRF_HEADER_NAME: csrf},
    )


def _session_max_age(response: httpx.Response) -> int:
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith(f"{SESSION_COOKIE_NAME}="):
            for piece in raw.split(";")[1:]:
                key, _, value = piece.strip().partition("=")
                if key.casefold() == "max-age":
                    return int(value)
    raise AssertionError("no session cookie was set")


async def _client(settings: Settings):
    app.dependency_overrides[get_settings] = lambda: settings
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


# ---------------------------------------------------------------------------
# Remember me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_sign_in_uses_the_short_session(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            await _bootstrap(client)
            client.cookies.clear()

            response = await _login(client, remember=False)

            assert response.status_code == 200
            assert _session_max_age(response) == settings.auth_session_ttl_seconds
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_asking_to_be_remembered_lasts_far_longer(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            await _bootstrap(client)
            client.cookies.clear()

            response = await _login(client, remember=True)

            assert response.status_code == 200
            assert _session_max_age(response) == settings.auth_session_remember_ttl_seconds
            assert _session_max_age(response) > settings.auth_session_ttl_seconds
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_client_that_omits_the_flag_gets_the_short_session(tmp_path: Path) -> None:
    """The Android app predates the flag; it must not get a month-long session."""

    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            await _bootstrap(client)
            client.cookies.clear()

            response = await _login(client)

            assert _session_max_age(response) == settings.auth_session_ttl_seconds
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Changing the password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_password_can_be_changed(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            csrf = await _bootstrap(client)

            changed = await client.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "second-pass"},
                headers={CSRF_HEADER_NAME: csrf},
            )

            assert changed.status_code == 200, changed.text
            client.cookies.clear()
            assert (await _login(client, password="second-pass")).status_code == 200
            client.cookies.clear()
            assert (await _login(client, password=PASSWORD)).status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_the_current_password_is_required(tmp_path: Path) -> None:
    """A stolen session cookie must not be enough to lock the owner out."""

    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            csrf = await _bootstrap(client)

            refused = await client.post(
                "/api/auth/password",
                json={"current_password": "not-it", "new_password": "second-pass"},
                headers={CSRF_HEADER_NAME: csrf},
            )

            assert refused.status_code == 401
            assert refused.json()["error_code"] == "AUTH_PASSWORD_MISMATCH"
            client.cookies.clear()
            assert (await _login(client)).status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_changing_the_password_keeps_this_browser_signed_in(tmp_path: Path) -> None:
    """Rotating the signing key invalidates this cookie too, so it is reissued."""

    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            csrf = await _bootstrap(client)

            await client.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "second-pass"},
                headers={CSRF_HEADER_NAME: csrf},
            )

            assert (await client.get("/api/auth/me")).status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_other_devices_are_signed_out(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            csrf = await _bootstrap(client)
            stale = dict(client.cookies)

            await client.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "second-pass"},
                headers={CSRF_HEADER_NAME: csrf},
            )

            client.cookies.clear()
            for name, value in stale.items():
                client.cookies.set(name, value)
            # That token was signed with the key the change rotated away.
            assert (await client.get("/api/auth/me")).status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_short_new_password_is_refused(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            csrf = await _bootstrap(client)

            response = await client.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "short"},
                headers={CSRF_HEADER_NAME: csrf},
            )

            assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_signing_in_is_required(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            await _bootstrap(client)
            client.cookies.clear()

            response = await client.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "second-pass"},
            )

            assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_the_new_password_never_appears_in_a_response(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    try:
        async with await _client(settings) as client:
            csrf = await _bootstrap(client)

            response = await client.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": "second-pass"},
                headers={CSRF_HEADER_NAME: csrf},
            )

            assert "second-pass" not in response.text
    finally:
        app.dependency_overrides.clear()
