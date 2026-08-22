from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from app.api.routes import configuration as configuration_routes
from app.core import security as security_module
from app.core.auth import CSRF_HEADER_NAME
from app.core.config import Settings, apply_runtime_configuration, get_settings
from app.core.runtime_config import RuntimeConfigError, runtime_store
from app.main import app
from app.models.enums import AuthRole
from app.schemas.adapters import ProbeResult


def runtime_settings(root: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "runtime_config_dir": root,
        "auth_local_username": None,
        "auth_local_password": None,
        "auth_session_signing_key": None,
        "auth_local_username_file": None,
        "auth_local_password_file": None,
        "auth_session_signing_key_file": None,
        "nextfind_username_file": None,
        "nextfind_password_file": None,
        "tmdb_access_token_file": None,
        "avistaz_username_file": None,
        "avistaz_password_file": None,
        "avistaz_pid_file": None,
        "qb_base_url_file": None,
        "qb_username_file": None,
        "qb_password_file": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def client_factory() -> Iterator[Callable[[Settings], httpx.AsyncClient]]:
    def create(settings: Settings) -> httpx.AsyncClient:
        app.dependency_overrides[get_settings] = lambda: settings
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield create
    app.dependency_overrides.clear()


async def setup_admin(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/auth/setup", json={"username": "admin", "password": "local-admin-password"}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def avistaz_payload(*, base_url: str = "https://pt.example.test") -> dict[str, object]:
    return {
        "architecture": "avistaz",
        "base_url": base_url,
        "username": "avistaz-user",
        "password": "avistaz-secret",
        "pid": "avistaz-pid-secret",
    }


def nexusphp_payload(*, base_url: str = "https://nexus.example.test") -> dict[str, object]:
    return {
        "architecture": "nexusphp",
        "site_id": "nexus-test",
        "display_name": "Nexus Test",
        "base_url": base_url,
        "cookie": "nexus-cookie-secret",
        "passkey": "nexus-passkey-secret",
    }


def complete_payload(*, qb_url: str = "https://qb.local:8080") -> dict[str, object]:
    return {
        "nextfind": {
            "base_url": "https://nextfind.example.test",
            "username": "next-user",
            "password": "next-secret",
        },
        "tmdb": {"token": "tmdb-secret"},
        "pt_site": avistaz_payload(),
        "qbittorrent": {
            "url": qb_url,
            "username": "qb-user",
            "password": "qb-secret",
            "save_path": "/downloads",
            "category": "unin",
        },
    }


@pytest.mark.asyncio
async def test_configuration_requires_admin_csrf_and_never_returns_secrets(
    tmp_path: Path, client_factory: Callable[[Settings], httpx.AsyncClient]
) -> None:
    async with client_factory(runtime_settings(tmp_path)) as client:
        csrf = await setup_admin(client)
        missing_csrf = await client.put("/api/configuration", json=complete_payload())
        saved = await client.put(
            "/api/configuration", json=complete_payload(), headers={CSRF_HEADER_NAME: csrf}
        )
        loaded = await client.get("/api/configuration")

    assert missing_csrf.status_code == 403
    assert saved.status_code == loaded.status_code == 200
    assert loaded.headers["cache-control"] == "no-store"
    body = loaded.json()
    assert body["nextfind"] == {
        "base_url": "https://nextfind.example.test",
        "username": "next-user",
        "password_configured": True,
        "configured": True,
    }
    assert body["pt_site"] == {
        "architecture": "avistaz",
        "base_url": "https://pt.example.test",
        "username": "avistaz-user",
        "password_configured": True,
        "pid_configured": True,
        "configured": True,
        "runtime_supported": True,
        "search_ready": True,
    }
    assert body["pt_sites"] == {
        "avistaz": body["pt_site"],
        "nexusphp": None,
    }
    assert [item["architecture"] for item in body["pt_site_architectures"]] == [
        "avistaz",
        "nexusphp",
    ]
    assert body["configuration_complete"] is True
    serialized = json.dumps(saved.json()) + json.dumps(body)
    for secret in (
        "next-secret",
        "tmdb-secret",
        "avistaz-secret",
        "avistaz-pid-secret",
        "qb-secret",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_empty_secrets_preserve_identity_and_changing_identity_clears_them(
    tmp_path: Path, client_factory: Callable[[Settings], httpx.AsyncClient]
) -> None:
    settings = runtime_settings(tmp_path)
    async with client_factory(settings) as client:
        csrf = await setup_admin(client)
        first = await client.put(
            "/api/configuration", json=complete_payload(), headers={CSRF_HEADER_NAME: csrf}
        )
        retained = await client.put(
            "/api/configuration",
            json={
                "nextfind": {
                    "base_url": "https://nextfind.example.test",
                    "username": "next-user",
                    "password": "",
                },
                "pt_site": {
                    "architecture": "avistaz",
                    "base_url": "https://pt.example.test",
                    "username": "avistaz-user",
                    "password": "",
                    "pid": "",
                },
                "qbittorrent": {
                    "url": "https://qb.local:8080", "username": "qb-user", "password": ""
                },
            },
            headers={CSRF_HEADER_NAME: csrf},
        )
        stored_after_retained = runtime_store(settings).configuration()
        changed = await client.put(
            "/api/configuration",
            json={
                "nextfind": {
                    "base_url": "https://nextfind-two.example.test",
                    "username": "next-user-two",
                    "password": "",
                },
                "pt_site": {
                    "architecture": "avistaz",
                    "base_url": "https://pt-two.example.test",
                    "username": "avistaz-user-two",
                    "password": "",
                    "pid": "",
                },
                "qbittorrent": {
                    "url": "https://qb-two.local:8080", "username": "qb-user-two", "password": ""
                },
            },
            headers={CSRF_HEADER_NAME: csrf},
        )

    assert first.status_code == retained.status_code == changed.status_code == 200
    assert stored_after_retained.nextfind_password == "next-secret"
    assert stored_after_retained.avistaz_site is not None
    assert stored_after_retained.avistaz_site.password == "avistaz-secret"
    assert stored_after_retained.avistaz_site.pid == "avistaz-pid-secret"
    assert stored_after_retained.qb_password == "qb-secret"
    stored = runtime_store(settings).configuration()
    assert stored.nextfind_password is None
    assert stored.avistaz_site is not None
    assert stored.avistaz_site.password is None
    assert stored.avistaz_site.pid is None
    assert stored.qb_password is None


def test_v1_configuration_migrates_without_losing_avistaz_credentials(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path, avistaz_base_url="https://legacy-pt.example.test")
    store = runtime_store(settings)
    store.integrations_dir.mkdir(parents=True)
    store.integrations_path.write_text(
        json.dumps(
            {
                "version": 1,
                "nextfind": {"username": "legacy-next", "password": "legacy-next-secret"},
                "tmdb": {"token": "legacy-tmdb-secret"},
                "avistaz": {
                    "username": "legacy-pt",
                    "password": "legacy-pt-secret",
                    "pid": "legacy-pt-pid",
                },
                "qbittorrent": {"url": "https://qb.legacy.test", "allow_insecure_http": False},
            }
        ),
        encoding="utf-8",
    )

    migrated = store.configuration()
    assert migrated.nextfind_base_url == "https://nextfind.example"
    assert migrated.avistaz_site is not None
    assert migrated.avistaz_site.base_url == "https://legacy-pt.example.test"
    assert migrated.avistaz_site.password == "legacy-pt-secret"
    store.save_configuration(
        {
            "pt_site": {
                "architecture": "avistaz",
                "base_url": "https://legacy-pt.example.test",
                "username": "legacy-pt",
            }
        }
    )
    persisted = json.loads(store.integrations_path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["pt_sites"]["avistaz"]["password"] == "legacy-pt-secret"
    assert persisted["pt_sites"]["avistaz"]["pid"] == "legacy-pt-pid"


def test_v2_keeps_both_pt_architectures_when_the_active_site_changes(tmp_path: Path) -> None:
    store = runtime_store(runtime_settings(tmp_path))
    store.save_configuration({"pt_site": avistaz_payload()})
    store.save_configuration({"pt_site": nexusphp_payload()})
    nexus_active = store.configuration()
    assert nexus_active.pt_site is nexus_active.nexusphp_site
    assert nexus_active.avistaz_site is not None
    assert nexus_active.nexusphp_site is not None
    store.save_configuration(
        {
            "pt_site": {
                "architecture": "avistaz",
                "base_url": "https://pt.example.test",
                "username": "avistaz-user",
            }
        }
    )
    avistaz_active = store.configuration()
    assert avistaz_active.pt_site is avistaz_active.avistaz_site
    assert avistaz_active.avistaz_site is not None
    assert avistaz_active.avistaz_site.password == "avistaz-secret"
    assert avistaz_active.nexusphp_site is not None
    assert avistaz_active.nexusphp_site.cookie == "nexus-cookie-secret"


@pytest.mark.asyncio
async def test_configuration_returns_both_pt_site_snapshots_without_secrets(
    tmp_path: Path, client_factory: Callable[[Settings], httpx.AsyncClient]
) -> None:
    async with client_factory(runtime_settings(tmp_path)) as client:
        csrf = await setup_admin(client)
        first = await client.put(
            "/api/configuration",
            json={"pt_site": avistaz_payload()},
            headers={CSRF_HEADER_NAME: csrf},
        )
        second = await client.put(
            "/api/configuration",
            json={"pt_site": nexusphp_payload()},
            headers={CSRF_HEADER_NAME: csrf},
        )

    assert first.status_code == second.status_code == 200
    body = second.json()
    assert body["pt_site"]["architecture"] == "nexusphp"
    assert body["pt_sites"]["avistaz"] == {
        "architecture": "avistaz",
        "base_url": "https://pt.example.test",
        "username": "avistaz-user",
        "password_configured": True,
        "pid_configured": True,
        "configured": True,
        "runtime_supported": True,
        "search_ready": False,
    }
    assert body["pt_sites"]["nexusphp"] == body["pt_site"]
    serialized = json.dumps(body)
    assert "avistaz-secret" not in serialized
    assert "nexus-cookie-secret" not in serialized


@pytest.mark.asyncio
async def test_nexusphp_can_be_configured_but_remains_unavailable_for_runtime_search(
    tmp_path: Path, client_factory: Callable[[Settings], httpx.AsyncClient]
) -> None:
    async with client_factory(runtime_settings(tmp_path)) as client:
        csrf = await setup_admin(client)
        response = await client.put(
            "/api/configuration",
            json={"pt_site": nexusphp_payload()},
            headers={CSRF_HEADER_NAME: csrf},
        )

    assert response.status_code == 200
    pt_site = response.json()["pt_site"]
    assert pt_site["architecture"] == "nexusphp"
    assert pt_site["configured"] is True
    assert pt_site["runtime_supported"] is False
    assert pt_site["search_ready"] is False
    assert "nexus-cookie-secret" not in json.dumps(response.json())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group", "payload"),
    (
        ("nextfind", {"base_url": "http://nextfind.example.test", "username": "reader"}),
        ("nextfind", {"base_url": "https://user:pass@nextfind.example.test", "username": "reader"}),
        (
            "nextfind",
            {
                "base_url": "https://nextfind.example.test/?token=secret",
                "username": "reader",
            },
        ),
        (
            "pt_site",
            {
                "architecture": "avistaz",
                "base_url": "https://pt.example.test/path",
                "username": "reader",
            },
        ),
        (
            "pt_site",
            {
                "architecture": "nexusphp",
                "site_id": "nexus-test",
                "display_name": "Nexus Test",
                "base_url": "http://nexus.example.test",
            },
        ),
    ),
)
async def test_nextfind_and_pt_site_urls_must_be_https_origins(
    tmp_path: Path,
    client_factory: Callable[[Settings], httpx.AsyncClient],
    group: str,
    payload: dict[str, object],
) -> None:
    async with client_factory(runtime_settings(tmp_path)) as client:
        csrf = await setup_admin(client)
        response = await client.put(
            "/api/configuration", json={group: payload}, headers={CSRF_HEADER_NAME: csrf}
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "CONFIGURATION_INVALID"


@pytest.mark.asyncio
async def test_configuration_schema_errors_are_never_cached(
    tmp_path: Path, client_factory: Callable[[Settings], httpx.AsyncClient]
) -> None:
    async with client_factory(runtime_settings(tmp_path)) as client:
        csrf = await setup_admin(client)
        response = await client.put(
            "/api/configuration",
            json={"unknown_group": {}},
            headers={CSRF_HEADER_NAME: csrf},
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "API_VALIDATION_ERROR"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_qb_http_requires_explicit_opt_in(
    tmp_path: Path, client_factory: Callable[[Settings], httpx.AsyncClient]
) -> None:
    async with client_factory(runtime_settings(tmp_path)) as client:
        csrf = await setup_admin(client)
        rejected = await client.put(
            "/api/configuration",
            json={
                "qbittorrent": {
                    "url": "http://qb.local:8080",
                    "username": "qb-user",
                    "password": "qb-secret",
                }
            },
            headers={CSRF_HEADER_NAME: csrf},
        )
        allowed = await client.put(
            "/api/configuration",
            json={
                "qbittorrent": {
                    "url": "http://qb.local:8080",
                    "username": "qb-user",
                    "password": "qb-secret",
                    "allow_insecure_http": True,
                }
            },
            headers={CSRF_HEADER_NAME: csrf},
        )

    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "CONFIGURATION_INVALID"
    assert allowed.status_code == 200
    assert allowed.json()["qbittorrent"]["allow_insecure_http"] is True


def test_runtime_configuration_derives_custom_hosts_without_enabling_qb_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = runtime_settings(
        tmp_path,
        enable_qb_write=False,
        allowed_external_hosts=("nextfind.example.test", "pt.example.test"),
    )
    runtime_store(base).save_configuration(complete_payload())
    effective = apply_runtime_configuration(base)

    assert effective.nextfind_allowed_hosts == ("nextfind.example.test",)
    assert effective.avistaz_allowed_hosts == ("pt.example.test",)
    assert effective.qb_allowed_hosts == ("qb.local",)
    assert effective.enable_qb_write is False

    monkeypatch.setenv("UNIN_RUNTIME_CONFIG_DIR", str(tmp_path))
    reloaded = get_settings()
    assert reloaded.tmdb_token_value() == "tmdb-secret"
    assert reloaded.enable_qb_write is False


def test_tampered_persisted_qb_url_is_rejected(tmp_path: Path) -> None:
    settings = runtime_settings(tmp_path)
    store = runtime_store(settings)
    store.save_configuration(
        {"qbittorrent": {"url": "https://qb.local", "allow_insecure_http": False}}
    )
    payload = json.loads(store.integrations_path.read_text(encoding="utf-8"))
    payload["qbittorrent"]["url"] = "https://qb.local/?token=leaked"
    store.integrations_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeConfigError):
        store.configuration()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/api/configuration/tests/nextfind",
        "/api/configuration/tests/tmdb",
        "/api/configuration/tests/pt-sites/avistaz",
        "/api/configuration/tests/pt-sites/nexusphp",
        "/api/configuration/tests/qbittorrent",
    ),
)
async def test_connection_tests_require_admin_session_and_csrf_before_route_execution(
    tmp_path: Path,
    client_factory: Callable[[Settings], httpx.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    settings = runtime_settings(tmp_path)

    def forbidden_effective_settings(_settings: Settings) -> Settings:
        raise AssertionError("connection test route executed before authentication")

    monkeypatch.setattr(
        configuration_routes, "_effective_settings", forbidden_effective_settings
    )
    async with client_factory(settings) as admin_client:
        await setup_admin(admin_client)
        without_csrf = await admin_client.post(path)
        async with client_factory(settings) as anonymous_client:
            without_session = await anonymous_client.post(path)
    viewer_settings = runtime_settings(
        tmp_path,
        auth_local_username="viewer",
        auth_local_password="viewer-password",
        auth_session_signing_key="viewer-test-signing-key-with-at-least-32-characters",
        auth_local_role=AuthRole.VIEWER,
    )
    async with client_factory(viewer_settings) as viewer_client:
        bootstrap = await viewer_client.get("/api/auth/csrf")
        assert bootstrap.status_code == 200
        viewer_login = await viewer_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewer-password"},
            headers={CSRF_HEADER_NAME: bootstrap.json()["csrf_token"]},
        )
        assert viewer_login.status_code == 200
        wrong_role = await viewer_client.post(
            path,
            headers={CSRF_HEADER_NAME: viewer_login.json()["csrf_token"]},
        )

    assert without_session.status_code == 401
    assert without_session.json()["error_code"] == "AUTH_REQUIRED"
    assert without_csrf.status_code == 403
    assert without_csrf.json()["error_code"] == "CSRF_TOKEN_REQUIRED"
    assert wrong_role.status_code == 403
    assert wrong_role.json()["error_code"] == "AUTH_ROLE_FORBIDDEN"
    assert not runtime_store(settings).integrations_path.exists()


@pytest.mark.asyncio
async def test_connection_tests_use_only_bounded_read_only_adapter_actions_and_do_not_save(
    tmp_path: Path,
    client_factory: Callable[[Settings], httpx.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = runtime_settings(tmp_path)
    actions: dict[str, list[str]] = {
        "nextfind": [],
        "tmdb": [],
        "avistaz": [],
        "qbittorrent": [],
    }
    constructor_values: dict[str, dict[str, object]] = {}
    validated_targets: list[tuple[str, tuple[str, ...]]] = []

    async def allow_public_target(
        url: str,
        allowed_hosts: tuple[str, ...],
        **_kwargs: object,
    ) -> str:
        validated_targets.append((url, allowed_hosts))
        return url

    class FakeNextFindAdapter:
        def __init__(self, **kwargs: object) -> None:
            constructor_values["nextfind"] = kwargs

        async def probe(self) -> ProbeResult:
            actions["nextfind"].append("probe")
            return ProbeResult(healthy=True, message="NextFind 测试连接成功")

        async def aclose(self) -> None:
            actions["nextfind"].append("close")

    class FakeTmdbProvider:
        def __init__(self, **kwargs: object) -> None:
            constructor_values["tmdb"] = kwargs

        async def probe(self) -> ProbeResult:
            actions["tmdb"].append("probe")
            return ProbeResult(healthy=True, message="TMDB 测试连接成功")

        async def aclose(self) -> None:
            actions["tmdb"].append("close")

    class FakeAvistaZAdapter:
        def __init__(self, **kwargs: object) -> None:
            constructor_values["avistaz"] = kwargs

        async def probe(self) -> ProbeResult:
            actions["avistaz"].append("probe")
            return ProbeResult(healthy=True, message="AvistaZ 测试连接成功")

        async def aclose(self) -> None:
            actions["avistaz"].append("close")

    class FakeQbittorrentAdapter:
        def __init__(self, **kwargs: object) -> None:
            constructor_values["qbittorrent"] = kwargs

        async def authenticate(self) -> None:
            actions["qbittorrent"].append("authenticate")

        async def get_version(self) -> str:
            actions["qbittorrent"].append("get_version")
            return "5.0.4"

        async def get_web_api_version(self) -> str:
            actions["qbittorrent"].append("get_web_api_version")
            return "2.11.4"

        async def aclose(self) -> None:
            actions["qbittorrent"].append("close")

    monkeypatch.setattr(
        configuration_routes, "validate_public_external_target", allow_public_target
    )
    monkeypatch.setattr(configuration_routes, "NextFindAdapter", FakeNextFindAdapter)
    monkeypatch.setattr(configuration_routes, "TmdbProvider", FakeTmdbProvider)
    monkeypatch.setattr(configuration_routes, "AvistaZAdapter", FakeAvistaZAdapter)
    monkeypatch.setattr(
        configuration_routes, "QbittorrentReadOnlyAdapter", FakeQbittorrentAdapter
    )

    async with client_factory(settings) as client:
        csrf = await setup_admin(client)
        saved = await client.put(
            "/api/configuration",
            json=complete_payload(),
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert saved.status_code == 200
        integration_path = runtime_store(settings).integrations_path
        before = integration_path.read_bytes()
        responses = [
            await client.post(
                "/api/configuration/tests/nextfind",
                headers={CSRF_HEADER_NAME: csrf},
            ),
            await client.post(
                "/api/configuration/tests/tmdb",
                headers={CSRF_HEADER_NAME: csrf},
            ),
            await client.post(
                "/api/configuration/tests/pt-sites/avistaz",
                headers={CSRF_HEADER_NAME: csrf},
            ),
            await client.post(
                "/api/configuration/tests/qbittorrent",
                headers={CSRF_HEADER_NAME: csrf},
            ),
        ]
        after = integration_path.read_bytes()

    assert before == after
    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert [response.json()["target"] for response in responses] == [
        "nextfind",
        "tmdb",
        "pt_site",
        "qbittorrent",
    ]
    assert all(response.json()["healthy"] is True for response in responses)
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    assert actions == {
        "nextfind": ["probe", "close"],
        "tmdb": ["probe", "close"],
        "avistaz": ["probe", "close"],
        "qbittorrent": [
            "authenticate",
            "get_version",
            "get_web_api_version",
            "close",
        ],
    }
    assert validated_targets == [
        ("https://nextfind.example.test", ("nextfind.example.test",)),
        ("https://pt.example.test", ("pt.example.test",)),
    ]
    assert constructor_values["nextfind"]["password"] == "next-secret"
    assert constructor_values["tmdb"]["access_token"] == "tmdb-secret"
    assert constructor_values["avistaz"]["password"] == "avistaz-secret"
    assert constructor_values["avistaz"]["pid"] == "avistaz-pid-secret"
    assert constructor_values["avistaz"]["enable_torrent_fetch"] is False
    assert constructor_values["qbittorrent"]["password"] == "qb-secret"
    serialized = "".join(response.text for response in responses)
    for secret in (
        "next-secret",
        "tmdb-secret",
        "avistaz-secret",
        "avistaz-pid-secret",
        "qb-secret",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_nexusphp_connection_test_uses_saved_cookie_without_passkey_or_config_write(
    tmp_path: Path,
    client_factory: Callable[[Settings], httpx.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = runtime_settings(tmp_path)
    actions: list[str] = []
    constructor_values: dict[str, object] = {}
    validated_targets: list[tuple[str, tuple[str, ...]]] = []

    async def allow_public_target(
        url: str,
        allowed_hosts: tuple[str, ...],
        **_kwargs: object,
    ) -> str:
        validated_targets.append((url, allowed_hosts))
        return url

    class FakeNexusPhpConnectionProbe:
        def __init__(self, base_url: str, **kwargs: object) -> None:
            constructor_values["base_url"] = base_url
            constructor_values.update(kwargs)

        async def probe(self) -> ProbeResult:
            actions.append("probe")
            return ProbeResult(healthy=True, message="NexusPHP Cookie 会话验证成功")

        async def aclose(self) -> None:
            actions.append("close")

    monkeypatch.setattr(
        configuration_routes, "validate_public_external_target", allow_public_target
    )
    monkeypatch.setattr(
        configuration_routes,
        "NexusPhpConnectionProbe",
        FakeNexusPhpConnectionProbe,
    )
    async with client_factory(settings) as client:
        csrf = await setup_admin(client)
        saved = await client.put(
            "/api/configuration",
            json={"pt_site": nexusphp_payload()},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert saved.status_code == 200
        integration_path = runtime_store(settings).integrations_path
        before = integration_path.read_bytes()
        response = await client.post(
            "/api/configuration/tests/pt-sites/nexusphp",
            headers={CSRF_HEADER_NAME: csrf},
        )
        after = integration_path.read_bytes()

    assert response.status_code == 200
    assert response.json()["healthy"] is True
    assert response.json()["target"] == "pt_site"
    assert response.headers["cache-control"] == "no-store"
    assert before == after
    assert actions == ["probe", "close"]
    assert validated_targets == [
        ("https://nexus.example.test", ("nexus.example.test",))
    ]
    assert constructor_values["base_url"] == "https://nexus.example.test"
    assert constructor_values["allowed_hosts"] == ("nexus.example.test",)
    assert constructor_values["cookie_header"] == "nexus-cookie-secret"
    assert "passkey" not in constructor_values
    assert "nexus-cookie-secret" not in response.text
    assert "nexus-passkey-secret" not in response.text


@pytest.mark.asyncio
async def test_public_connection_tests_reject_private_dns_before_adapter_construction(
    tmp_path: Path,
    client_factory: Callable[[Settings], httpx.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = runtime_settings(tmp_path)
    resolved: list[tuple[str, int]] = []

    async def private_resolver(host: str, port: int) -> tuple[str, ...]:
        resolved.append((host, port))
        return ("10.0.0.8",)

    def forbidden_adapter(**_kwargs: object) -> None:
        raise AssertionError("adapter constructed before private target was rejected")

    monkeypatch.setattr(security_module, "_resolve_target_addresses", private_resolver)
    monkeypatch.setattr(configuration_routes, "NextFindAdapter", forbidden_adapter)
    monkeypatch.setattr(configuration_routes, "AvistaZAdapter", forbidden_adapter)
    payload = complete_payload()
    nextfind = payload["nextfind"]
    assert isinstance(nextfind, dict)
    nextfind["base_url"] = "https://next.private.example"
    payload["pt_site"] = avistaz_payload(base_url="https://pt.private.example")

    async with client_factory(settings) as client:
        csrf = await setup_admin(client)
        saved = await client.put(
            "/api/configuration",
            json=payload,
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert saved.status_code == 200
        integration_path = runtime_store(settings).integrations_path
        before = integration_path.read_bytes()
        nextfind_response = await client.post(
            "/api/configuration/tests/nextfind",
            headers={CSRF_HEADER_NAME: csrf},
        )
        avistaz_response = await client.post(
            "/api/configuration/tests/pt-sites/avistaz",
            headers={CSRF_HEADER_NAME: csrf},
        )
        after = integration_path.read_bytes()

    assert before == after
    assert resolved == [
        ("next.private.example", 443),
        ("pt.private.example", 443),
    ]
    for response in (nextfind_response, avistaz_response):
        assert response.status_code == 400
        assert response.json()["error_code"] == "EXTERNAL_TARGET_NOT_PUBLIC"
        assert response.headers["cache-control"] == "no-store"
        assert "next-secret" not in response.text
        assert "avistaz-secret" not in response.text
        assert "avistaz-pid-secret" not in response.text
