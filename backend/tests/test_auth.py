from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    create_session_token,
    parse_session_token,
)
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import IdentityReview, MediaItem, MetadataMatch
from app.models.enums import (
    AuthRole,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
)
from app.schemas.adapters import MetadataRecord

LOCAL_USERNAME = "local-admin"
LOCAL_PASSWORD = "test-password"
SIGNING_KEY = "test-signing-key-with-at-least-32-characters"


def auth_settings(role: AuthRole = AuthRole.ADMIN) -> Settings:
    return Settings(
        _env_file=None,
        auth_local_username=LOCAL_USERNAME,
        auth_local_password=LOCAL_PASSWORD,
        auth_session_signing_key=SIGNING_KEY,
        auth_local_role=role,
    )


@pytest.fixture
def auth_client_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Iterator[Callable[[Settings], httpx.AsyncClient]]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    def create(settings: Settings) -> httpx.AsyncClient:
        app.dependency_overrides[get_settings] = lambda: settings
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    yield create
    app.dependency_overrides.clear()


async def login(
    client: httpx.AsyncClient,
    *,
    username: str = LOCAL_USERNAME,
    password: str = LOCAL_PASSWORD,
) -> httpx.Response:
    csrf = await client.get("/api/auth/csrf")
    assert csrf.status_code == 200
    token = csrf.json()["csrf_token"]
    return await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={CSRF_HEADER_NAME: token},
    )


@pytest.mark.asyncio
async def test_auth_fails_closed_without_complete_secrets(auth_client_factory) -> None:
    settings = Settings(
        _env_file=None,
        auth_local_username=None,
        auth_local_password=None,
        auth_session_signing_key=None,
    )
    async with auth_client_factory(settings) as client:
        response = await client.get("/api/auth/csrf")
    assert response.status_code == 503
    assert response.json()["error_code"] == "AUTH_NOT_CONFIGURED"


def test_auth_material_rejects_weak_key_and_oversized_username() -> None:
    assert (
        Settings(
            _env_file=None,
            auth_local_username="valid",
            auth_local_password="password",
            auth_session_signing_key="too-short",
        ).auth_material()
        is None
    )
    assert (
        Settings(
            _env_file=None,
            auth_local_username="u" * 121,
            auth_local_password="password",
            auth_session_signing_key=SIGNING_KEY,
        ).auth_material()
        is None
    )


@pytest.mark.asyncio
async def test_login_requires_bootstrap_csrf_and_hides_credential_mismatch(
    auth_client_factory,
) -> None:
    async with auth_client_factory(auth_settings()) as client:
        missing_csrf = await client.post(
            "/api/auth/login",
            json={"username": LOCAL_USERNAME, "password": LOCAL_PASSWORD},
        )
        wrong_username = await login(client, username="wrong-user")
        wrong_password = await login(client, password="wrong-password")

    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error_code"] == "CSRF_TOKEN_REQUIRED"
    for response in (wrong_username, wrong_password):
        assert response.status_code == 401
        assert response.json() == {
            "error_code": "AUTH_LOGIN_FAILED",
            "message": "用户名或密码错误",
            "details": {},
        }


@pytest.mark.asyncio
async def test_login_sets_hardened_cookies_and_me_returns_principal(
    auth_client_factory,
) -> None:
    async with auth_client_factory(auth_settings()) as client:
        response = await login(client)
        me = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["username"] == LOCAL_USERNAME
    assert response.json()["role"] == "admin"
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if item.startswith(f"{SESSION_COOKIE_NAME}="))
    csrf_cookie = next(item for item in cookies if item.startswith(f"{CSRF_COOKIE_NAME}="))
    assert "httponly" in session_cookie.casefold()
    assert "samesite=strict" in session_cookie.casefold()
    assert "path=/api" in session_cookie.casefold()
    assert "httponly" not in csrf_cookie.casefold()
    assert "samesite=strict" in csrf_cookie.casefold()
    assert me.status_code == 200
    assert me.json() == {"username": LOCAL_USERNAME, "role": "admin"}
    assert me.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_tampered_and_expired_sessions_are_rejected(auth_client_factory) -> None:
    settings = auth_settings()
    async with auth_client_factory(settings) as client:
        response = await login(client)
        assert response.status_code == 200
        session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
        assert session_cookie is not None
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, f"{session_cookie}x", path="/api")
        tampered = await client.get("/api/auth/me")

        expired_token = create_session_token(
            username=LOCAL_USERNAME,
            role=AuthRole.ADMIN,
            csrf_token="expired-csrf",
            signing_key=SIGNING_KEY,
            ttl_seconds=300,
            now=1,
        )
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, expired_token, path="/api")
        expired = await client.get("/api/auth/me")

    assert tampered.status_code == 401
    assert tampered.json()["error_code"] == "AUTH_SESSION_INVALID"
    assert expired.status_code == 401
    assert expired.json()["error_code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_existing_session_is_rejected_after_server_role_changes(
    auth_client_factory,
) -> None:
    async with auth_client_factory(auth_settings(AuthRole.ADMIN)) as client:
        response = await login(client)
        assert response.status_code == 200
        app.dependency_overrides[get_settings] = lambda: auth_settings(AuthRole.VIEWER)
        stale = await client.get("/api/auth/me")
        refreshed_csrf = await client.get("/api/auth/csrf")
        relogin = await client.post(
            "/api/auth/login",
            json={"username": LOCAL_USERNAME, "password": LOCAL_PASSWORD},
            headers={CSRF_HEADER_NAME: refreshed_csrf.json()["csrf_token"]},
        )

    assert stale.status_code == 401
    assert stale.json()["error_code"] == "AUTH_SESSION_STALE"
    assert refreshed_csrf.status_code == 200
    assert refreshed_csrf.json()["csrf_token"].startswith("b1.")
    assert relogin.status_code == 200
    assert relogin.json()["role"] == "viewer"


def test_malformed_base64_session_is_normalized_to_app_error() -> None:
    signed = "v1.a"
    digest = hmac.new(
        SIGNING_KEY.encode(), signed.encode(), hashlib.sha256
    ).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    with pytest.raises(AppError) as caught:
        parse_session_token(f"{signed}.{signature}", SIGNING_KEY)
    assert caught.value.error_code == "AUTH_SESSION_INVALID"


@pytest.mark.asyncio
async def test_mutations_require_matching_session_csrf(auth_client_factory) -> None:
    async with auth_client_factory(auth_settings()) as client:
        response = await login(client)
        csrf_token = response.json()["csrf_token"]
        missing = await client.post("/api/discovery-runs")
        invalid = await client.post(
            "/api/discovery-runs",
            headers={CSRF_HEADER_NAME: f"{csrf_token}-wrong"},
        )

    assert missing.status_code == 403
    assert missing.json()["error_code"] == "CSRF_TOKEN_REQUIRED"
    assert invalid.status_code == 403
    assert invalid.json()["error_code"] == "CSRF_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_role_hierarchy_blocks_viewer_and_operator_but_allows_admin_operator_actions(
    auth_client_factory,
) -> None:
    async with auth_client_factory(auth_settings(AuthRole.VIEWER)) as viewer_client:
        viewer_login = await login(viewer_client)
        viewer_write = await viewer_client.post(
            "/api/discovery-runs",
            headers={CSRF_HEADER_NAME: viewer_login.json()["csrf_token"]},
        )
    assert viewer_write.status_code == 403
    assert viewer_write.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"

    async with auth_client_factory(auth_settings(AuthRole.OPERATOR)) as operator_client:
        operator_login = await login(operator_client)
        operator_approve = await operator_client.post(
            "/api/approval-requests/missing/approve",
            json={
                "acknowledges_hnr": True,
                "acknowledges_seeding": True,
                "acknowledges_plan_only": True,
            },
            headers={CSRF_HEADER_NAME: operator_login.json()["csrf_token"]},
        )
        operator_revoke = await operator_client.post(
            "/api/approval-requests/missing/revoke",
            json={},
            headers={CSRF_HEADER_NAME: operator_login.json()["csrf_token"]},
        )
    assert operator_approve.status_code == 403
    assert operator_approve.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"
    assert operator_revoke.status_code == 403
    assert operator_revoke.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"

    async with auth_client_factory(auth_settings(AuthRole.ADMIN)) as admin_client:
        admin_login = await login(admin_client)
        admin_operator_action = await admin_client.post(
            "/api/discovery-runs",
            headers={CSRF_HEADER_NAME: admin_login.json()["csrf_token"]},
        )
    assert admin_operator_action.status_code == 409
    assert admin_operator_action.json()["error_code"] == "NEXTFIND_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_client_operator_field_cannot_spoof_audit_actor(
    session_factory: async_sessionmaker[AsyncSession],
    auth_client_factory,
) -> None:
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id="auth-actor-media",
        media_type=MediaType.MOVIE,
        title="Actor Test",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.NEEDS_CONFIRMATION,
        discovered_at=now,
        updated_at=now,
    )
    metadata = MetadataRecord(
        tmdb_id=86420,
        media_type=MediaType.MOVIE,
        title="Actor Test",
        english_title="Actor Test",
        original_title="Actor Test",
        year=2026,
        confidence=1,
    )
    async with session_factory() as session:
        session.add(media)
        await session.flush()
        match = MetadataMatch(
            media_id=media.id,
            tmdb_id=metadata.tmdb_id,
            rank=1,
            score=1,
            match_reasons=["TITLE_EXACT"],
            conflicts=[],
            candidate_snapshot=metadata.model_dump(mode="json"),
        )
        session.add(match)
        await session.commit()

    async with auth_client_factory(auth_settings()) as client:
        response = await login(client)
        confirmed = await client.post(
            f"/api/media/{media.id}/identity-confirmations",
            json={"metadata_match_id": match.id, "operator": "spoofed-client-actor"},
            headers={CSRF_HEADER_NAME: response.json()["csrf_token"]},
        )

    assert confirmed.status_code == 201
    assert confirmed.json()["confirmed_by"] == LOCAL_USERNAME
    async with session_factory() as session:
        review = await session.get(IdentityReview, confirmed.json()["id"])
    assert review is not None
    assert review.confirmed_by == LOCAL_USERNAME


@pytest.mark.asyncio
async def test_logout_requires_csrf_and_clears_both_cookies(auth_client_factory) -> None:
    async with auth_client_factory(auth_settings()) as client:
        response = await login(client)
        csrf_token = response.json()["csrf_token"]
        missing = await client.post("/api/auth/logout")
        logged_out = await client.post(
            "/api/auth/logout",
            headers={CSRF_HEADER_NAME: csrf_token},
        )
        me = await client.get("/api/auth/me")

    assert missing.status_code == 403
    assert logged_out.status_code == 204
    cleared = logged_out.headers.get_list("set-cookie")
    assert any(
        item.startswith(f"{SESSION_COOKIE_NAME}=") and "max-age=0" in item.casefold()
        for item in cleared
    )
    assert any(
        item.startswith(f"{CSRF_COOKIE_NAME}=") and "max-age=0" in item.casefold()
        for item in cleared
    )
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_openapi_has_control_plane_but_no_qb_write_routes(
    auth_client_factory,
) -> None:
    async with auth_client_factory(auth_settings()) as client:
        response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/approval-requests/{approval_id}/execution-intents" in paths
    assert "/api/approval-requests/{approval_id}/execute" in paths
    assert "/api/approval-requests/{approval_id}/download-execution" in paths
    assert "/api/download-executions" in paths
    assert "/api/download-executions/{execution_id}" in paths
    assert "/api/download-executions/{execution_id}/reconcile" in paths
    qb_paths = {
        path: sorted(methods)
        for path, methods in paths.items()
        if path.startswith("/api/downloaders/qbittorrent")
    }
    assert qb_paths == {
        "/api/downloaders/qbittorrent/status": ["get"],
        "/api/downloaders/qbittorrent/torrents": ["get"],
    }


@pytest.mark.asyncio
async def test_execution_control_plane_mutations_require_admin_role_and_csrf(
    auth_client_factory,
) -> None:
    async with auth_client_factory(auth_settings(AuthRole.VIEWER)) as client:
        logged_in = await login(client)
        csrf_token = logged_in.json()["csrf_token"]
        missing_csrf = await client.post(
            "/api/approval-requests/missing/execution-intents",
            json={},
        )
        forbidden = await client.post(
            "/api/approval-requests/missing/execution-intents",
            json={},
            headers={CSRF_HEADER_NAME: csrf_token},
        )

    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error_code"] == "CSRF_TOKEN_REQUIRED"
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"
