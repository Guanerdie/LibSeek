from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.core.auth import CSRF_HEADER_NAME
from app.core.config import Settings, get_settings
from app.core.runtime_config import runtime_store
from app.main import app


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


@pytest.mark.asyncio
async def test_first_admin_password_accepts_six_characters_and_explains_short_password(
    tmp_path: Path,
) -> None:
    settings = runtime_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            rejected = await client.post(
                "/api/auth/setup", json={"username": "admin", "password": "12345"}
            )

            assert rejected.status_code == 422
            assert rejected.json()["error_code"] == "API_VALIDATION_ERROR"
            assert rejected.json()["message"] == "管理员密码至少需要 6 位"
            assert "12345" not in rejected.text
            assert runtime_store(settings).auth_record() is None

            accepted = await client.post(
                "/api/auth/setup", json={"username": "admin", "password": "123456"}
            )

        assert accepted.status_code == 200
        assert accepted.json()["username"] == "admin"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_login_failure_does_not_reveal_whether_username_or_password_was_wrong(
    tmp_path: Path,
) -> None:
    settings = runtime_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            setup = await client.post(
                "/api/auth/setup", json={"username": "admin", "password": "123456"}
            )
            assert setup.status_code == 200
            client.cookies.clear()
            bootstrap = await client.get("/api/auth/csrf")
            csrf = bootstrap.json()["csrf_token"]
            wrong_username = await client.post(
                "/api/auth/login",
                json={"username": "someone-else", "password": "123456"},
                headers={CSRF_HEADER_NAME: csrf},
            )
            wrong_password = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "654321"},
                headers={CSRF_HEADER_NAME: csrf},
            )

        assert wrong_username.status_code == wrong_password.status_code == 401
        assert wrong_username.json() == wrong_password.json()
        assert wrong_password.json()["message"] == "用户名或密码错误"
    finally:
        app.dependency_overrides.clear()
