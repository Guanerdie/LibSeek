from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    create_password_digest,
    create_session_token,
    parse_session_token,
    verify_password_digest,
)
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.entities import IdentityReview, Job, MediaItem, MetadataMatch
from app.models.enums import (
    AuthRole,
    IdentityConfidence,
    JobStatus,
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


def runtime_auth_settings(runtime_config_dir) -> Settings:
    return Settings(
        _env_file=None,
        runtime_config_dir=runtime_config_dir,
        auth_local_username=None,
        auth_local_password=None,
        auth_session_signing_key=None,
        auth_local_username_file=None,
        auth_local_password_file=None,
        auth_session_signing_key_file=None,
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
async def test_uninitialized_auth_reports_setup_required(
    tmp_path,
    auth_client_factory,
) -> None:
    settings = runtime_auth_settings(tmp_path)
    async with auth_client_factory(settings) as client:
        status = await client.get("/api/auth/setup-status")
        csrf = await client.get("/api/auth/csrf")
        login_response = await client.post(
            "/api/auth/login",
            json={"username": "not-created", "password": "not-created-password"},
        )

    assert status.status_code == 200
    assert status.json() == {
        "admin_initialized": False,
        "configuration_complete": False,
    }
    assert csrf.status_code == 409
    assert csrf.json()["error_code"] == "AUTH_SETUP_REQUIRED"
    assert login_response.status_code == 409
    assert login_response.json()["error_code"] == "AUTH_SETUP_REQUIRED"


@pytest.mark.asyncio
async def test_setup_creates_admin_and_authenticated_session(
    tmp_path,
    auth_client_factory,
) -> None:
    settings = runtime_auth_settings(tmp_path)
    async with auth_client_factory(settings) as client:
        response = await client.post(
            "/api/auth/setup",
            json={"username": "first-admin", "password": "new-local-password"},
        )
        me = await client.get("/api/auth/me")
        status = await client.get("/api/auth/setup-status")

    assert response.status_code == 200
    assert response.json()["username"] == "first-admin"
    assert response.json()["role"] == "admin"
    assert response.json()["csrf_token"].startswith("s1_")
    assert me.status_code == 200
    assert me.json() == {"username": "first-admin", "role": "admin"}
    assert status.json()["admin_initialized"] is True
    cookies = response.headers.get_list("set-cookie")
    assert any(item.startswith(f"{SESSION_COOKIE_NAME}=") for item in cookies)
    assert any(item.startswith(f"{CSRF_COOKIE_NAME}=") for item in cookies)


@pytest.mark.asyncio
async def test_setup_is_atomic_and_persists_hashed_credentials_across_clients(
    tmp_path,
    auth_client_factory,
) -> None:
    settings = runtime_auth_settings(tmp_path)
    password = "persistent-local-password"
    async with auth_client_factory(settings) as first_client:
        created = await first_client.post(
            "/api/auth/setup",
            json={"username": "persistent-admin", "password": password},
        )
        duplicate = await first_client.post(
            "/api/auth/setup",
            json={"username": "second-admin", "password": "second-password"},
        )

    persisted_text = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*") if path.is_file()
    )
    assert created.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "AUTH_ALREADY_CONFIGURED"
    assert password not in persisted_text
    assert "pbkdf2_sha256$v1$" in persisted_text

    async with auth_client_factory(runtime_auth_settings(tmp_path)) as restarted_client:
        logged_in = await login(
            restarted_client,
            username="persistent-admin",
            password=password,
        )
    assert logged_in.status_code == 200
    assert logged_in.json()["username"] == "persistent-admin"


@pytest.mark.asyncio
async def test_complete_legacy_auth_remains_initialized_and_cannot_be_replaced(
    tmp_path,
    auth_client_factory,
) -> None:
    settings = auth_settings()
    settings.runtime_config_dir = tmp_path
    async with auth_client_factory(settings) as client:
        status = await client.get("/api/auth/setup-status")
        replacement = await client.post(
            "/api/auth/setup",
            json={"username": "replacement", "password": "replacement-password"},
        )
        logged_in = await login(client)

    assert status.status_code == 200
    assert status.json()["admin_initialized"] is True
    assert replacement.status_code == 409
    assert replacement.json()["error_code"] == "AUTH_ALREADY_CONFIGURED"
    assert logged_in.status_code == 200


@pytest.mark.asyncio
async def test_partial_legacy_auth_fails_closed_instead_of_opening_setup(
    tmp_path,
    auth_client_factory,
) -> None:
    settings = Settings(
        _env_file=None,
        runtime_config_dir=tmp_path,
        auth_local_username="partial-user",
        auth_local_password=None,
        auth_session_signing_key=None,
        auth_local_username_file=None,
        auth_local_password_file=None,
        auth_session_signing_key_file=None,
    )
    async with auth_client_factory(settings) as client:
        status = await client.get("/api/auth/setup-status")
        setup_response = await client.post(
            "/api/auth/setup",
            json={"username": "attacker", "password": "attacker-password"},
        )

    assert status.status_code == 503
    assert status.json()["error_code"] == "AUTH_NOT_CONFIGURED"
    assert setup_response.status_code == 503
    assert setup_response.json()["error_code"] == "AUTH_NOT_CONFIGURED"


def test_versioned_pbkdf2_password_digest_round_trip_and_rejects_malformed() -> None:
    password = "not-written-in-cleartext"
    encoded = create_password_digest(password, iterations=1)

    assert encoded.startswith("pbkdf2_sha256$v1$1$")
    assert password not in encoded
    assert verify_password_digest(password, encoded) is True
    assert verify_password_digest(f"{password}-wrong", encoded) is False
    assert verify_password_digest(password, "pbkdf2_sha256$v1$bad$salt$digest") is False


def test_first_setup_ports_remain_bound_to_loopback() -> None:
    compose = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text(encoding="utf-8")

    assert '- "127.0.0.1:${API_PORT:-8000}:8000"' in compose
    assert '- "127.0.0.1:${FRONTEND_PORT:-9527}:80"' in compose


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
    digest = hmac.new(SIGNING_KEY.encode(), signed.encode(), hashlib.sha256).digest()
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
async def test_viewer_can_read_metadata_resolution_job_status_without_mutation_access(
    session_factory: async_sessionmaker[AsyncSession],
    auth_client_factory,
) -> None:
    now = datetime.now(UTC)
    media = MediaItem(
        source="nextfind",
        source_item_id="viewer-resolution-job",
        media_type=MediaType.MOVIE,
        title="Viewer Resolution Job",
        identity_confidence=IdentityConfidence.NEEDS_CONFIRMATION,
        metadata_status=MetadataStatus.UNRESOLVED,
        discovered_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        session.add(media)
        await session.flush()
        job = Job(
            job_type=f"RESOLVE_METADATA:{media.id}",
            status=JobStatus.PENDING,
            payload={"media_id": media.id, "read_only": True},
        )
        session.add(job)
        await session.commit()

    async with auth_client_factory(auth_settings(AuthRole.VIEWER)) as client:
        logged_in = await login(client)
        lookup = await client.get(f"/api/media/{media.id}/resolve-jobs/{job.id}")
        forbidden_mutation = await client.post(
            f"/api/media/{media.id}/resolve",
            headers={CSRF_HEADER_NAME: logged_in.json()["csrf_token"]},
        )

    assert lookup.status_code == 200
    assert lookup.json()["job_id"] == job.id
    assert lookup.json()["status"] == "PENDING"
    assert forbidden_mutation.status_code == 403
    assert forbidden_mutation.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"


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
    assert "/api/download-executions/{execution_id}/download-job" in paths
    assert "/api/download-executions/{execution_id}/reconcile" in paths
    assert "/api/media/{media_id}/resolve-jobs/{job_id}" in paths
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
        viewer_lookup = await client.get("/api/download-executions/missing/download-job")

    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error_code"] == "CSRF_TOKEN_REQUIRED"
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"
    assert viewer_lookup.status_code == 404
    assert viewer_lookup.json()["error_code"] == "DOWNLOAD_EXECUTION_NOT_FOUND"
