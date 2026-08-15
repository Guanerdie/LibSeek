from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from app.tools import local_setup_wizard as wizard
from app.tools.local_setup_wizard import (
    LocalSetupWriter,
    SetupError,
    WizardHTTPServer,
)

SENTINEL = "PRIVATE-SETUP-SENTINEL-9f42d0"
SETUP_TOKEN = "T" * 43

PLAINTEXT_CREDENTIALS = (
    "AUTH_LOCAL_USERNAME",
    "AUTH_LOCAL_PASSWORD",
    "AUTH_SESSION_SIGNING_KEY",
    "NEXTFIND_USERNAME",
    "NEXTFIND_PASSWORD",
    "TMDB_ACCESS_TOKEN",
    "AVISTAZ_USERNAME",
    "AVISTAZ_PASSWORD",
    "AVISTAZ_PID",
    "QB_BASE_URL",
    "QB_USERNAME",
    "QB_PASSWORD",
)

DANGEROUS_DISABLED_FLAGS = (
    "ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE",
    "ENABLE_DOWNLOAD_EXECUTOR",
    "ENABLE_AVISTAZ_TORRENT_FETCH",
    "ENABLE_QB_WRITE",
    "ENABLE_DOWNLOAD_MONITOR",
    "ENABLE_AUTOMATION_ENGINE",
    "ENABLE_MEDIA_IMPORT_CONTROL_PLANE",
)


def _example_dotenv() -> str:
    lines = [
        "# synthetic local setup template",
        "POSTGRES_DB=unin",
        "POSTGRES_USER=unin",
        "POSTGRES_PASSWORD=change-me-before-production",
        "DATABASE_URL=postgresql+psycopg://unin:change-me-before-production@postgres:5432/unin",
        "AUTH_LOCAL_ROLE=admin",
        "NEXTFIND_BASE_URL=https://nextfind.example",
        "TMDB_BASE_URL=https://api.themoviedb.org",
        "AVISTAZ_BASE_URL=https://avistaz.to",
        "QB_ALLOWED_HOSTS=",
        "QB_ALLOW_INSECURE_HTTP=false",
        "QB_TARGET_SAVE_PATH=",
        "QB_ALLOWED_SAVE_PATHS=",
        "QB_TARGET_CATEGORY=",
        "COMPOSE_PROFILES=",
        "ENABLE_TMDB_LIVE=false",
        "ENABLE_AVISTAZ_LIVE_SEARCH=false",
        "ENABLE_QB_READ_ONLY=false",
        *(f"{name}=false" for name in DANGEROUS_DISABLED_FLAGS),
        *(f"{name}=" for name in PLAINTEXT_CREDENTIALS),
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "unin"
    root.mkdir()
    (root / ".env.example").write_text(_example_dotenv(), encoding="utf-8")
    return root


def _discovery_payload(marker: str = SENTINEL) -> dict[str, object]:
    return {
        "groups": ["discovery"],
        "discovery": {
            "auth_local_username": f"local-user-{marker}",
            "auth_local_password": f"local-password-{marker}",
            "nextfind_username": f"nextfind-user-{marker}",
            "nextfind_password": f"nextfind-password-{marker}",
            "tmdb_access_token": f"tmdb-token-{marker}",
        },
    }


def _all_groups_payload(*, qb_url: str = "https://QB.INTERNAL:8443/api") -> dict[str, object]:
    return {
        "groups": ["discovery", "avistaz", "qb"],
        "discovery": cast_dict(_discovery_payload()["discovery"]),
        "avistaz": {
            "avistaz_username": f"avistaz-user-{SENTINEL}",
            "avistaz_password": f"avistaz-password-{SENTINEL}",
            "avistaz_pid": f"avistaz-pid-{SENTINEL}",
        },
        "qb": {
            "qb_base_url": qb_url,
            "qb_username": f"qb-user-{SENTINEL}",
            "qb_password": f"qb-password-{SENTINEL}",
            "qb_target_save_path": "/srv/downloads/complete",
            "qb_target_category": "unin",
            "qb_allow_insecure_http": qb_url.startswith("http://"),
        },
    }


def cast_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            result[name] = value
    return result


@contextlib.contextmanager
def _running_server(writer: LocalSetupWriter) -> Iterator[WizardHTTPServer]:
    server = WizardHTTPServer(writer, SETUP_TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def _request(
    server: WizardHTTPServer,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=dict(headers or {}))
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {name.casefold(): value for name, value in response.getheaders()}
        return response.status, response_headers, response_body
    finally:
        connection.close()


def _api_request(
    server: WizardHTTPServer,
    path: str,
    payload: object,
    *,
    origin: str | None = None,
    token: str = SETUP_TOKEN,
    content_type: str = "application/json",
) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return _request(
        server,
        "POST",
        path,
        body=body,
        headers={
            "Origin": server.origin if origin is None else origin,
            wizard.TOKEN_HEADER: token,
            "Content-Type": content_type,
        },
    )


def test_page_uses_fragment_token_and_has_no_secret_prefill(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    with _running_server(writer) as server:
        status, headers, body = _request(server, "GET", "/")

    page = body.decode()
    assert status == 200
    assert SETUP_TOKEN not in page
    assert "window.location.hash" in page
    assert "history.replaceState(null, '', '/')" in page
    assert "auth-signing-key" not in page
    assert "auth_session_signing_key" not in page
    assert not re.search(r'<input[^>]+type="password"[^>]+value=', page)
    assert 'autocomplete="new-password"' in page
    assert "border-radius: 12px" not in page
    assert "background: #f3f5f4" in page
    assert "background: #237a45" in page
    assert "步骤 1：本地账号与影视来源" in page
    assert "TMDB 访问令牌" in page
    assert "qBittorrent Web 地址" in page
    assert "qBittorrent 下载保存路径" in page
    assert "selector.disabled = true" in page
    assert "state.recommended_group" in page
    assert "setup.cmd -ReplaceExisting" in page
    assert headers["cache-control"].startswith("no-store")
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_state_exposes_only_group_completion_and_never_reads_secret(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalSetupWriter(project_root)
    secret_path = writer.secrets_dir / "auth_local_password.txt"
    secret_path.write_text(SENTINEL, encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.suffix == ".txt":
            raise AssertionError("existing Secret content must never be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    state = writer.state()
    serialized = json.dumps(state)

    assert SENTINEL not in serialized
    assert ".txt" not in serialized
    groups = cast_dict(state["groups"])
    discovery = cast_dict(groups["discovery"])
    assert discovery == {"configured": False, "incomplete": True}
    assert cast_dict(groups["avistaz"]) == {"configured": False, "incomplete": False}


def test_http_state_returns_group_summary_only(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    with _running_server(writer) as server:
        status, _, body = _api_request(server, "/api/state", {})

    response = json.loads(body)
    assert status == 200
    assert response["ok"] is True
    assert set(response["groups"]) == {"discovery", "avistaz", "qb"}
    assert response["groups"]["discovery"] == {"configured": False, "incomplete": False}
    assert "files" not in body.decode()


def test_state_recommends_the_next_unconfigured_group(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    assert writer.state()["recommended_group"] == "discovery"

    writer.save(_discovery_payload())
    after_discovery = writer.state()
    assert after_discovery["recommended_group"] == "avistaz"
    assert cast_dict(cast_dict(after_discovery["groups"])["discovery"])["configured"] is True

    all_payload = _all_groups_payload()
    writer.save({"groups": ["avistaz"], "avistaz": all_payload["avistaz"]})
    assert writer.state()["recommended_group"] == "qb"

    writer.save({"groups": ["qb"], "qb": all_payload["qb"]})
    final_state = writer.state()
    assert final_state["recommended_group"] is None
    assert final_state["all_configured"] is True


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    (
        ({"Host": "localhost:9999"}, 400),
        ({"Origin": "http://evil.invalid"}, 403),
        ({wizard.TOKEN_HEADER: "X" * 43}, 403),
        ({"Content-Type": "application/json; charset=utf-8"}, 415),
    ),
)
def test_http_rejects_bad_host_origin_token_and_content_type(
    project_root: Path,
    headers: dict[str, str],
    expected_status: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    writer = LocalSetupWriter(project_root)
    with _running_server(writer) as server:
        request_headers = {
            "Origin": server.origin,
            wizard.TOKEN_HEADER: SETUP_TOKEN,
            "Content-Type": "application/json",
            **headers,
        }
        status, _, body = _request(
            server,
            "POST",
            "/api/state",
            body=b"{}",
            headers=request_headers,
        )

    assert status == expected_status
    assert SENTINEL not in body.decode()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_http_rejects_oversized_body(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    with _running_server(writer) as server:
        status, _, _ = _request(
            server,
            "POST",
            "/api/save",
            body=b"x" * (wizard.MAX_REQUEST_BODY_BYTES + 1),
            headers={
                "Origin": server.origin,
                wizard.TOKEN_HEADER: SETUP_TOKEN,
                "Content-Type": "application/json",
            },
        )

    assert status == 413
    assert not (project_root / ".env").exists()


@pytest.mark.parametrize(
    "raw_body",
    (
        b'{"groups":["discovery"],"groups":["discovery"],"discovery":{}}',
        (
            b'{"groups":["discovery"],"discovery":{'
            b'"auth_local_username":"user",'
            b'"auth_local_password":"first",'
            b'"auth_local_password":"second",'
            b'"nextfind_username":"nextfind",'
            b'"nextfind_password":"password",'
            b'"tmdb_access_token":"token"}}'
        ),
    ),
)
def test_http_rejects_duplicate_json_keys_at_any_depth(project_root: Path, raw_body: bytes) -> None:
    writer = LocalSetupWriter(project_root)
    with _running_server(writer) as server:
        status, _, body = _request(
            server,
            "POST",
            "/api/save",
            body=raw_body,
            headers={
                "Origin": server.origin,
                wizard.TOKEN_HEADER: SETUP_TOKEN,
                "Content-Type": "application/json",
            },
        )

    response = json.loads(body)
    assert status == 400
    assert response["error_code"] == "BAD_REQUEST"
    assert not list((project_root / "secrets").glob("*.txt"))
    assert not (project_root / ".env").exists()


def test_discovery_save_generates_signing_key_and_safe_new_env(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    with _running_server(writer) as server:
        status, _, body = _api_request(server, "/api/save", _discovery_payload())

    response = json.loads(body)
    assert status == 200
    assert response == {
        "ok": True,
        "groups": ["discovery"],
        "files": [
            "auth_local_username.txt",
            "auth_local_password.txt",
            "auth_session_signing_key.txt",
            "nextfind_username.txt",
            "nextfind_password.txt",
            "tmdb_access_token.txt",
        ],
        "env_created": True,
        "external_connections_tested": False,
    }
    signing_key = (project_root / "secrets/auth_session_signing_key.txt").read_text(
        encoding="utf-8"
    )
    assert len(signing_key) >= 64
    assert SENTINEL not in signing_key
    assert re.fullmatch(r"[A-Za-z0-9_-]+", signing_key)
    assert not (project_root / "secrets/avistaz_username.txt").exists()
    assert not (project_root / "secrets/qb_base_url.txt").exists()

    env = _dotenv(project_root / ".env")
    assert env["POSTGRES_PASSWORD"] != "change-me-before-production"
    assert env["POSTGRES_PASSWORD"] in env["DATABASE_URL"]
    assert all(env[name] == "" for name in PLAINTEXT_CREDENTIALS)
    assert env["ENABLE_TMDB_LIVE"] == "true"
    assert env["ENABLE_AVISTAZ_LIVE_SEARCH"] == "false"
    assert env["ENABLE_QB_READ_ONLY"] == "false"
    assert all(env[name] == "false" for name in DANGEROUS_DISABLED_FLAGS)

    if os.name != "nt":
        assert stat.S_IMODE((project_root / "secrets").stat().st_mode) == 0o700
        assert stat.S_IMODE((project_root / ".env").stat().st_mode) == 0o600
        assert (
            stat.S_IMODE((project_root / "secrets/auth_local_password.txt").stat().st_mode) == 0o600
        )


def test_all_groups_save_is_syntax_only_and_never_uses_dns(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def external_call_forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("setup save must not use DNS or connect to an external service")

    monkeypatch.setattr(socket, "getaddrinfo", external_call_forbidden)
    monkeypatch.setattr(socket, "create_connection", external_call_forbidden)
    writer = LocalSetupWriter(project_root)
    result = writer.save(_all_groups_payload())

    assert result["external_connections_tested"] is False
    assert len(list((project_root / "secrets").glob("*.txt"))) == 12
    env = _dotenv(project_root / ".env")
    assert env["QB_ALLOWED_HOSTS"] == "qb.internal"
    assert env["QB_ALLOW_INSECURE_HTTP"] == "false"
    assert env["QB_TARGET_SAVE_PATH"] == "/srv/downloads/complete"
    assert env["QB_ALLOWED_SAVE_PATHS"] == "/srv/downloads/complete"
    assert env["QB_TARGET_CATEGORY"] == "unin"
    assert env["ENABLE_TMDB_LIVE"] == "true"
    assert env["ENABLE_AVISTAZ_LIVE_SEARCH"] == "true"
    assert env["ENABLE_QB_READ_ONLY"] == "true"


def test_qb_http_requires_explicit_confirmation(project_root: Path) -> None:
    payload = _all_groups_payload(qb_url="http://192.0.2.50:8080")
    payload["groups"] = ["qb"]
    payload.pop("discovery")
    payload.pop("avistaz")
    qb = cast_dict(payload["qb"])
    qb["qb_allow_insecure_http"] = False
    writer = LocalSetupWriter(project_root)

    with pytest.raises(SetupError, match="INVALID_QB"):
        writer.save(payload)
    assert not (project_root / "secrets/qb_base_url.txt").exists()
    assert not (project_root / ".env").exists()

    qb["qb_allow_insecure_http"] = True
    writer.save(payload)
    env = _dotenv(project_root / ".env")
    assert env["QB_ALLOWED_HOSTS"] == "192.0.2.50"
    assert env["QB_ALLOW_INSECURE_HTTP"] == "true"


def test_existing_secret_is_not_read_and_requires_explicit_replace(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_writer = LocalSetupWriter(project_root)
    existing = first_writer.secrets_dir / "auth_local_password.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.suffix == ".txt":
            raise AssertionError("existing Secret content must never be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    with pytest.raises(SetupError, match="EXISTING_SECRET"):
        first_writer.save(_discovery_payload("replacement"))
    assert original_read_text(existing, encoding="utf-8") == f"old-{SENTINEL}"
    assert not (project_root / ".env").exists()

    replacement_writer = LocalSetupWriter(project_root, replace_existing=True)
    replacement_writer.save(_discovery_payload("replacement"))
    assert original_read_text(existing, encoding="utf-8") == "local-password-replacement"


@pytest.mark.parametrize(
    "extra",
    (
        {"secret_path": "../../outside.txt"},
        {"avistaz": {"avistaz_password": SENTINEL}},
    ),
)
def test_client_cannot_submit_paths_or_unselected_groups(
    project_root: Path, extra: dict[str, object]
) -> None:
    payload = _discovery_payload()
    payload.update(extra)
    writer = LocalSetupWriter(project_root)

    with pytest.raises(SetupError, match="BAD_REQUEST"):
        writer.save(payload)
    assert not (project_root / ".env").exists()
    assert not list((project_root / "secrets").glob("*.txt"))
    assert not (project_root.parent / "outside.txt").exists()


@pytest.mark.parametrize(
    "unsafe_line",
    (
        "UNSAFE_VALUE=${UNTRUSTED_SOURCE}",
        "UNSAFE_VALUE=$UNTRUSTED_SOURCE",
        "COMPOSE_PROFILES=download-execution",
        "COMPOSE_FILE=other.yaml",
    ),
)
def test_existing_env_rejects_interpolation_and_compose_control(
    project_root: Path, unsafe_line: str
) -> None:
    (project_root / ".env").write_text(_example_dotenv() + unsafe_line + "\n", encoding="utf-8")
    writer = LocalSetupWriter(project_root)

    with pytest.raises(SetupError, match="DOTENV_INVALID"):
        writer.save(_discovery_payload())
    assert not list((project_root / "secrets").glob("*.txt"))


def test_existing_env_plaintext_credentials_are_cleared(project_root: Path) -> None:
    existing = _example_dotenv()
    for name in ("AVISTAZ_USERNAME", "AVISTAZ_PASSWORD", "AVISTAZ_PID"):
        existing = existing.replace(f"{name}=\n", f"{name}={SENTINEL}-{name}\n")
    existing = existing.replace(
        "POSTGRES_PASSWORD=change-me-before-production",
        "POSTGRES_PASSWORD=existing-safe-pg-value",
    ).replace(
        "postgresql+psycopg://unin:change-me-before-production@postgres:5432/unin",
        "postgresql+psycopg://unin:existing-safe-pg-value@postgres:5432/unin",
    )
    (project_root / ".env").write_text(existing, encoding="utf-8")
    writer = LocalSetupWriter(project_root)
    payload = _all_groups_payload()["avistaz"]
    writer.save({"groups": ["avistaz"], "avistaz": payload})

    env_text = (project_root / ".env").read_text(encoding="utf-8")
    env = _dotenv(project_root / ".env")
    assert SENTINEL not in env_text
    assert all(env[name] == "" for name in PLAINTEXT_CREDENTIALS)
    assert env["ENABLE_AVISTAZ_LIVE_SEARCH"] == "true"


def test_every_save_disables_dangerous_execution_flags(project_root: Path) -> None:
    existing = _example_dotenv()
    for name in DANGEROUS_DISABLED_FLAGS:
        existing = existing.replace(f"{name}=false", f"{name}=true")
    existing = existing.replace(
        "POSTGRES_PASSWORD=change-me-before-production",
        "POSTGRES_PASSWORD=existing-safe-pg-value",
    ).replace(
        "postgresql+psycopg://unin:change-me-before-production@postgres:5432/unin",
        "postgresql+psycopg://unin:existing-safe-pg-value@postgres:5432/unin",
    )
    (project_root / ".env").write_text(existing, encoding="utf-8")

    LocalSetupWriter(project_root).save(_discovery_payload())

    env = _dotenv(project_root / ".env")
    assert all(env[name] == "false" for name in DANGEROUS_DISABLED_FLAGS)


def test_new_group_can_update_nonsecret_env_without_replace_flag(
    project_root: Path,
) -> None:
    existing = (
        _example_dotenv()
        .replace(
            "POSTGRES_PASSWORD=change-me-before-production",
            "POSTGRES_PASSWORD=existing-safe-pg-value",
        )
        .replace(
            "postgresql+psycopg://unin:change-me-before-production@postgres:5432/unin",
            "postgresql+psycopg://unin:existing-safe-pg-value@postgres:5432/unin",
        )
    )
    (project_root / ".env").write_text(existing, encoding="utf-8")
    qb = _all_groups_payload()["qb"]

    LocalSetupWriter(project_root).save({"groups": ["qb"], "qb": qb})

    env = _dotenv(project_root / ".env")
    assert env["QB_ALLOWED_HOSTS"] == "qb.internal"
    assert env["QB_TARGET_SAVE_PATH"] == "/srv/downloads/complete"
    assert env["ENABLE_QB_READ_ONLY"] == "true"


def test_unselected_plaintext_credentials_are_not_silently_deleted(
    project_root: Path,
) -> None:
    existing = _example_dotenv().replace(
        "AUTH_LOCAL_PASSWORD=\n", f"AUTH_LOCAL_PASSWORD=old-{SENTINEL}\n"
    )
    existing = existing.replace(
        "POSTGRES_PASSWORD=change-me-before-production",
        "POSTGRES_PASSWORD=existing-safe-pg-value",
    ).replace(
        "postgresql+psycopg://unin:change-me-before-production@postgres:5432/unin",
        "postgresql+psycopg://unin:existing-safe-pg-value@postgres:5432/unin",
    )
    env_path = project_root / ".env"
    env_path.write_text(existing, encoding="utf-8")
    writer = LocalSetupWriter(project_root)
    avistaz = _all_groups_payload()["avistaz"]

    with pytest.raises(SetupError, match="UNSELECTED_PLAINTEXT_SECRET"):
        writer.save({"groups": ["avistaz"], "avistaz": avistaz})

    assert f"AUTH_LOCAL_PASSWORD=old-{SENTINEL}" in env_path.read_text(encoding="utf-8")
    assert not list((project_root / "secrets").glob("*.txt"))


@pytest.mark.parametrize("failing_commit", (1, 7))
def test_first_and_last_commit_failure_roll_back_the_whole_transaction(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_commit: int,
) -> None:
    writer = LocalSetupWriter(project_root, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    original_commit = writer._commit
    commit_count = 0

    def fail_selected_commit(item: wizard.StagedFile) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == failing_commit:
            raise OSError(f"synthetic commit failure {SENTINEL}")
        original_commit(item)

    monkeypatch.setattr(writer, "_commit", fail_selected_commit)
    with pytest.raises(SetupError, match="WRITE_FAILED"):
        writer.save(_discovery_payload("new"))

    assert existing.read_text(encoding="utf-8") == f"old-{SENTINEL}"
    assert not (writer.secrets_dir / "auth_local_password.txt").exists()
    assert not (writer.secrets_dir / "auth_session_signing_key.txt").exists()
    assert not (project_root / ".env").exists()
    assert not list(writer.staging_dir.glob("*/*.tmp"))
    assert not list(writer.staging_dir.glob("*/*.rollback"))
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


def test_rollback_failure_is_stable_and_next_start_recovers(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalSetupWriter(project_root, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    original_commit = writer._commit
    original_replace = wizard.os.replace
    commit_count = 0

    def fail_third_commit(item: wizard.StagedFile) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise OSError(f"commit-{SENTINEL}")
        original_commit(item)

    def fail_rollback(source: object, destination: object) -> None:
        if str(source).endswith(".rollback"):
            raise OSError(f"rollback-{SENTINEL}")
        original_replace(source, destination)

    monkeypatch.setattr(writer, "_commit", fail_third_commit)
    monkeypatch.setattr(wizard.os, "replace", fail_rollback)
    with pytest.raises(SetupError) as raised:
        writer.save(_discovery_payload("new"))

    assert raised.value.code == "ROLLBACK_FAILED"
    assert SENTINEL not in str(raised.value)
    assert list(writer.staging_dir.glob("*/transaction.json"))

    monkeypatch.undo()
    LocalSetupWriter(project_root, replace_existing=True)

    assert existing.read_text(encoding="utf-8") == f"old-{SENTINEL}"
    assert not (writer.secrets_dir / "auth_local_password.txt").exists()
    assert not (project_root / ".env").exists()
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


def test_unknown_staging_residue_fails_closed_without_deleting_files(
    project_root: Path,
) -> None:
    writer = LocalSetupWriter(project_root)
    stale_run = writer.staging_dir / ("a" * 32)
    stale_run.mkdir()
    (stale_run / ("b" * 32 + ".tmp")).write_text(SENTINEL, encoding="utf-8")

    with pytest.raises(SetupError) as raised:
        LocalSetupWriter(project_root)

    assert raised.value.code == "ROLLBACK_FAILED"
    assert (stale_run / ("b" * 32 + ".tmp")).read_text(encoding="utf-8") == SENTINEL


def test_pre_manifest_crash_recovers_owned_staging_without_touching_unknown_files(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalSetupWriter(project_root, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")

    monkeypatch.setattr(writer, "_cleanup_staging", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        writer,
        "_write_manifest",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError(f"crash-{SENTINEL}")),
    )
    with pytest.raises(SetupError, match="WRITE_FAILED"):
        writer._stage_all(
            {existing: f"new-{SENTINEL}"},
            b"SAFE_VALUE=true\n",
            env_was_missing=True,
        )

    run_dirs = [path for path in writer.staging_dir.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / wizard.TRANSACTION_PREPARING_NAME).exists()
    assert not (run_dirs[0] / wizard.TRANSACTION_MANIFEST_NAME).exists()
    assert existing.stat().st_nlink == 2

    monkeypatch.undo()
    LocalSetupWriter(project_root, replace_existing=True)

    assert existing.read_text(encoding="utf-8") == f"old-{SENTINEL}"
    assert existing.stat().st_nlink == 1
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


def test_pre_manifest_recovery_refuses_unknown_content_without_deleting_it(
    project_root: Path,
) -> None:
    writer = LocalSetupWriter(project_root)
    run_dir = writer.staging_dir / ("c" * 32)
    run_dir.mkdir()
    writer._write_preparing_marker(run_dir)
    unknown = run_dir / "user-notes.txt"
    unknown.write_text(SENTINEL, encoding="utf-8")

    with pytest.raises(SetupError) as raised:
        LocalSetupWriter(project_root)

    assert raised.value.code == "ROLLBACK_FAILED"
    assert unknown.read_text(encoding="utf-8") == SENTINEL
    assert (run_dir / wizard.TRANSACTION_PREPARING_NAME).exists()


def test_committed_residue_is_cleaned_without_rolling_back(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalSetupWriter(project_root)
    monkeypatch.setattr(writer, "_cleanup_staging", lambda *args, **kwargs: None)
    writer.save(_discovery_payload("committed"))
    assert list(writer.staging_dir.glob("*/COMMITTED"))

    monkeypatch.undo()
    LocalSetupWriter(project_root)

    assert (writer.secrets_dir / "auth_local_username.txt").read_text(
        encoding="utf-8"
    ) == "local-user-committed"
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


def test_manifest_records_staged_identity_and_recovery_rejects_destination_tamper(
    project_root: Path,
) -> None:
    writer = LocalSetupWriter(project_root, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    staged = writer._stage_all(
        {existing: f"new-{SENTINEL}"},
        b"SAFE_VALUE=true\n",
        env_was_missing=True,
    )
    manifest_path = staged[0].temporary.parent / wizard.TRANSACTION_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    first_item = manifest["items"][0]
    assert (first_item["staged_device"], first_item["staged_inode"]) == staged[0].staged_identity

    writer._commit(staged[0])
    foreign = project_root / "foreign-destination"
    foreign.write_text(f"foreign-{SENTINEL}", encoding="utf-8")
    foreign_stat = foreign.stat()
    os.replace(foreign, existing)

    with pytest.raises(SetupError) as raised:
        LocalSetupWriter(project_root, replace_existing=True)

    assert raised.value.code == "ROLLBACK_FAILED"
    assert existing.read_text(encoding="utf-8") == f"foreign-{SENTINEL}"
    current_stat = existing.stat()
    assert (current_stat.st_dev, current_stat.st_ino) == (
        foreign_stat.st_dev,
        foreign_stat.st_ino,
    )
    assert manifest_path.exists()


def test_failure_after_committed_marker_never_rolls_back(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalSetupWriter(project_root)
    original_mark_committed = writer._mark_committed

    def mark_then_fail(staged: list[wizard.StagedFile]) -> None:
        original_mark_committed(staged)
        assert (staged[0].temporary.parent / wizard.TRANSACTION_COMMITTED_NAME).exists()
        raise OSError(f"after-marker-{SENTINEL}")

    monkeypatch.setattr(writer, "_mark_committed", mark_then_fail)

    result = writer.save(_discovery_payload("marker-visible"))

    assert result["ok"] is True
    assert (writer.secrets_dir / "auth_local_username.txt").read_text(
        encoding="utf-8"
    ) == "local-user-marker-visible"
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


def test_partial_cleanup_retains_committed_marker_and_next_start_finishes(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalSetupWriter(project_root, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    original_unlink = Path.unlink
    failed_once = False

    def fail_first_committed_rollback_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal failed_once
        if (
            not failed_once
            and path.suffix == ".rollback"
            and (path.parent / wizard.TRANSACTION_COMMITTED_NAME).exists()
        ):
            failed_once = True
            raise OSError(f"cleanup-{SENTINEL}")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_committed_rollback_cleanup)
    result = writer.save(_discovery_payload("committed-cleanup"))
    run_dirs = [path for path in writer.staging_dir.iterdir() if path.is_dir()]

    assert result["ok"] is True
    assert failed_once is True
    assert len(run_dirs) == 1
    assert (run_dirs[0] / wizard.TRANSACTION_COMMITTED_NAME).exists()
    assert (run_dirs[0] / wizard.TRANSACTION_MANIFEST_NAME).exists()

    monkeypatch.undo()
    LocalSetupWriter(project_root, replace_existing=True)

    assert existing.read_text(encoding="utf-8") == "local-user-committed-cleanup"
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


@pytest.mark.parametrize("cutpoint", ("manifest", "marker", "rmdir"))
def test_committed_cleanup_terminal_cutpoints_are_recovered_without_rollback(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    cutpoint: str,
) -> None:
    writer = LocalSetupWriter(project_root, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    original_unlink = Path.unlink
    original_rmdir = Path.rmdir
    failed_once = False

    def fail_selected_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal failed_once
        selected_name = {
            "manifest": wizard.TRANSACTION_MANIFEST_NAME,
            "marker": wizard.TRANSACTION_COMMITTED_NAME,
        }.get(cutpoint)
        if (
            not failed_once
            and selected_name is not None
            and path.name == selected_name
            and path.parent.parent == writer.staging_dir
        ):
            failed_once = True
            raise OSError(f"cleanup-{cutpoint}-{SENTINEL}")
        original_unlink(path, *args, **kwargs)

    def fail_selected_rmdir(path: Path) -> None:
        nonlocal failed_once
        if (
            not failed_once
            and cutpoint == "rmdir"
            and path.parent == writer.staging_dir
            and re.fullmatch(r"[0-9a-f]{32}", path.name)
        ):
            failed_once = True
            raise OSError(f"cleanup-rmdir-{SENTINEL}")
        original_rmdir(path)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    monkeypatch.setattr(Path, "rmdir", fail_selected_rmdir)

    result = writer.save(_discovery_payload(f"terminal-{cutpoint}"))
    assert result["ok"] is True
    assert failed_once is True
    assert [path for path in writer.staging_dir.iterdir() if path.is_dir()]

    monkeypatch.undo()
    LocalSetupWriter(project_root, replace_existing=True)

    assert existing.read_text(encoding="utf-8") == f"local-user-terminal-{cutpoint}"
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


def test_valid_manifest_with_unknown_artifact_fails_closed(
    project_root: Path,
) -> None:
    writer = LocalSetupWriter(project_root)
    destination = writer.secrets_dir / "auth_local_username.txt"
    staged = writer._stage_all(
        {destination: f"new-{SENTINEL}"},
        b"SAFE_VALUE=true\n",
        env_was_missing=True,
    )
    run_dir = staged[0].temporary.parent
    unknown = run_dir / ("f" * 32 + ".tmp")
    unknown.write_text(SENTINEL, encoding="utf-8")

    with pytest.raises(SetupError) as raised:
        LocalSetupWriter(project_root)

    assert raised.value.code == "ROLLBACK_FAILED"
    assert unknown.read_text(encoding="utf-8") == SENTINEL
    assert not destination.exists()


def test_setup_lock_rejects_multiple_hard_links(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    writer.lock_path.unlink()
    outside = project_root / "outside-lock"
    outside.write_bytes(b"\0")
    os.link(outside, writer.lock_path)

    with pytest.raises(SetupError, match="FILESYSTEM_UNSAFE"):
        LocalSetupWriter(project_root)

    assert outside.read_bytes() == b"\0"


def test_setup_lock_rejects_non_regular_file(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    writer.lock_path.unlink()
    writer.lock_path.mkdir()

    with pytest.raises(SetupError, match="FILESYSTEM_UNSAFE"):
        LocalSetupWriter(project_root)


def test_setup_lock_rejects_symlink_without_touching_target(project_root: Path) -> None:
    writer = LocalSetupWriter(project_root)
    writer.lock_path.unlink()
    outside = project_root / "outside-symlink-target"
    outside.write_text(SENTINEL, encoding="utf-8")
    try:
        writer.lock_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink creation is unavailable: {exc.__class__.__name__}")

    with pytest.raises(SetupError, match="FILESYSTEM_UNSAFE"):
        LocalSetupWriter(project_root)

    assert outside.read_text(encoding="utf-8") == SENTINEL


def test_windows_acl_marker_is_random_single_use_and_constant_time_checked(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_dir = project_root / "secrets" / ".setup-staging"
    marker_dir.mkdir(parents=True)
    marker = marker_dir / wizard.WINDOWS_ACL_MARKER_NAME
    nonce = "N" * 44
    marker.write_text(nonce, encoding="ascii")
    monkeypatch.setenv(wizard.WINDOWS_ACL_NONCE_ENV, nonce)
    compared: list[tuple[str, str]] = []
    original_compare = wizard.hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        compared.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(wizard.hmac, "compare_digest", recording_compare)

    assert wizard._consume_windows_acl_marker(project_root) is True
    assert compared == [(nonce, nonce)]
    assert wizard.WINDOWS_ACL_NONCE_ENV not in os.environ
    assert not marker.exists()
    assert wizard._consume_windows_acl_marker(project_root) is False


def test_windows_acl_marker_rejects_fixed_or_mismatched_environment(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_dir = project_root / "secrets" / ".setup-staging"
    marker_dir.mkdir(parents=True)
    marker = marker_dir / wizard.WINDOWS_ACL_MARKER_NAME
    marker.write_text("M" * 44, encoding="ascii")
    monkeypatch.setenv("UNIN_SETUP_ACL_READY", "1")
    monkeypatch.setenv(wizard.WINDOWS_ACL_NONCE_ENV, "N" * 44)

    assert wizard._consume_windows_acl_marker(project_root) is False
    assert wizard.WINDOWS_ACL_NONCE_ENV not in os.environ
    assert marker.exists()


@pytest.mark.parametrize("raise_error", (False, True))
def test_browser_open_failure_never_prints_fragment_token(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raise_error: bool,
) -> None:
    captured_url = ""

    def refuse_browser(url: str, *, new: int, autoraise: bool) -> bool:
        nonlocal captured_url
        captured_url = url
        if raise_error:
            raise RuntimeError(f"synthetic browser failure for {url}")
        return False

    monkeypatch.setattr(wizard.webbrowser, "open", refuse_browser)
    writer = LocalSetupWriter(project_root)
    completed = wizard.run_local_setup(writer, timeout_seconds=0.1)
    output = capsys.readouterr()
    fragment_token = captured_url.split("#", 1)[1]

    assert completed is False
    assert captured_url.startswith("http://127.0.0.1:")
    assert "/#" in captured_url
    assert fragment_token not in output.out
    assert "#" not in output.out
    assert output.err == ""
    assert "已关闭" in output.out


def test_setup_launcher_uses_current_sid_and_never_reads_secret_files() -> None:
    project = Path(__file__).resolve().parents[2]
    script = (project / "scripts/setup.ps1").read_text(encoding="utf-8")

    assert "WindowsIdentity]::GetCurrent().User" in script
    assert "SetAccessRuleProtection($true, $false)" in script
    assert "Get-Item -LiteralPath $LiteralPath -Force" in script
    assert "[IO.FileAttributes]::ReparsePoint" in script
    assert "-ne\n        0" in script
    assert "Assert-ProjectPathAncestorsSafe" in script
    assert "Assert-SingleHardLink" in script
    assert "fsutil.Source hardlink list" in script
    assert "Assert-ExistingSetupPathsSafe" in script
    assert script.count("Assert-ExistingSetupPathsSafe") >= 5
    assert "RandomNumberGenerator]::Create()" in script
    assert "UNIN_SETUP_ACL_NONCE" in script
    assert "setup.acl-marker" in script
    assert "UNIN_SETUP_ACL_READY" not in script
    assert "sys.version_info >= (3, 12)" in script
    assert "--recover-only" in script
    assert "[switch]$RecoverStagingOnly" in script
    assert script.index("$recoveryArguments =") < script.index(
        "# Recovery removes only strictly identified transaction artifacts."
    )
    assert "-ValidatePathsOnly" not in script
    assert "[switch]$ValidatePathsOnly" in script
    assert "[switch]$ReplaceExisting" in script
    assert "--replace-existing" in script
    assert "$env:USERNAME" not in script
    assert "Get-Content" not in script
    assert "ReadAllText" not in script
    assert "docker" not in script.casefold()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_setup_launcher_validation_accepts_existing_regular_paths(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source_project = Path(__file__).resolve().parents[2]
    copied_project = tmp_path / "regular-project"
    copied_scripts = copied_project / "scripts"
    staging = copied_project / "secrets" / ".setup-staging"
    copied_scripts.mkdir(parents=True)
    staging.mkdir(parents=True)
    copied_script = copied_scripts / "setup.ps1"
    shutil.copy2(source_project / "scripts/setup.ps1", copied_script)
    (copied_project / ".env").write_text("SAFE_VALUE=true\n", encoding="utf-8")
    (copied_project / "secrets/auth_local_password.txt").write_text(
        "synthetic-value", encoding="utf-8"
    )
    (staging / "setup.lock").write_bytes(b"\0")

    validated = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copied_script),
            "-ValidatePathsOnly",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert validated.returncode == 0, validated.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows hard-link contract")
@pytest.mark.parametrize(
    "relative_path",
    (
        Path(".env"),
        Path("secrets/auth_local_password.txt"),
        Path("secrets/.setup-staging/setup.lock"),
    ),
)
def test_setup_launcher_validation_rejects_configuration_hardlinks(
    tmp_path: Path, relative_path: Path
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source_project = Path(__file__).resolve().parents[2]
    copied_project = tmp_path / "hardlink-project"
    copied_scripts = copied_project / "scripts"
    copied_scripts.mkdir(parents=True)
    copied_script = copied_scripts / "setup.ps1"
    shutil.copy2(source_project / "scripts/setup.ps1", copied_script)
    linked_path = copied_project / relative_path
    linked_path.parent.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external-hardlink-target"
    external.write_text(SENTINEL, encoding="utf-8")
    os.link(external, linked_path)
    acl_env = os.environ.copy()
    acl_env["UNIN_TEST_ACL_PATH"] = str(external)

    def external_acl() -> str:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-Acl -LiteralPath $env:UNIN_TEST_ACL_PATH).Sddl",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=acl_env,
        )
        return result.stdout.strip()

    before = external_acl()
    validated = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copied_script),
            "-ValidatePathsOnly",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    after = external_acl()

    assert validated.returncode != 0
    assert before == after
    assert external.read_text(encoding="utf-8") == SENTINEL


@pytest.mark.skipif(os.name != "nt", reason="Windows staging recovery contract")
def test_setup_launcher_recovers_transaction_hardlink_before_strict_gate(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source_project = Path(__file__).resolve().parents[2]
    copied_project = tmp_path / "recovery-project"
    copied_scripts = copied_project / "scripts"
    copied_tools = copied_project / "backend" / "app" / "tools"
    copied_scripts.mkdir(parents=True)
    copied_tools.mkdir(parents=True)
    copied_script = copied_scripts / "setup.ps1"
    shutil.copy2(source_project / "scripts/setup.ps1", copied_script)
    shutil.copy2(
        source_project / "backend/app/tools/local_setup_wizard.py",
        copied_tools / "local_setup_wizard.py",
    )
    (copied_project / ".env.example").write_text(_example_dotenv(), encoding="utf-8")

    writer = LocalSetupWriter(copied_project, replace_existing=True)
    existing = writer.secrets_dir / "auth_local_username.txt"
    existing.write_text(f"old-{SENTINEL}", encoding="utf-8")
    writer._stage_all(
        {existing: f"new-{SENTINEL}"},
        b"SAFE_VALUE=true\n",
        env_was_missing=True,
    )
    assert existing.stat().st_nlink == 2

    recovered = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copied_script),
            "-RecoverStagingOnly",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert existing.read_text(encoding="utf-8") == f"old-{SENTINEL}"
    assert existing.stat().st_nlink == 1
    assert not [path for path in writer.staging_dir.iterdir() if path.is_dir()]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_setup_launcher_validation_rejects_junction_without_acl_changes(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source_project = Path(__file__).resolve().parents[2]
    copied_project = tmp_path / "copied-project"
    copied_scripts = copied_project / "scripts"
    copied_scripts.mkdir(parents=True)
    copied_script = copied_scripts / "setup.ps1"
    shutil.copy2(source_project / "scripts/setup.ps1", copied_script)
    safe_validation = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copied_script),
            "-ValidatePathsOnly",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert safe_validation.returncode == 0, safe_validation.stderr
    external = tmp_path / "external-target"
    external.mkdir()
    junction = copied_project / "secrets"
    process_env = os.environ.copy()
    process_env["UNIN_TEST_JUNCTION_PATH"] = str(junction)
    process_env["UNIN_TEST_JUNCTION_TARGET"] = str(external)

    create = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$ErrorActionPreference='Stop'; "
                "New-Item -ItemType Junction "
                "-Path $env:UNIN_TEST_JUNCTION_PATH "
                "-Target $env:UNIN_TEST_JUNCTION_TARGET | Out-Null"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=process_env,
    )
    if create.returncode != 0:
        pytest.skip("junction creation is unavailable")

    def acl_sddl(path: Path) -> str:
        acl_env = os.environ.copy()
        acl_env["UNIN_TEST_ACL_PATH"] = str(path)
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-Acl -LiteralPath $env:UNIN_TEST_ACL_PATH).Sddl",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=acl_env,
        )
        return result.stdout.strip()

    before = acl_sddl(external)
    try:
        validated = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(copied_script),
                "-ValidatePathsOnly",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        after = acl_sddl(external)
    finally:
        junction.rmdir()

    assert validated.returncode != 0
    assert before == after
    assert not (copied_project / "backend").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ancestor junction contract")
def test_setup_launcher_validation_rejects_reparse_project_root(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source_project = Path(__file__).resolve().parents[2]
    real_project = tmp_path / "real-project"
    real_scripts = real_project / "scripts"
    real_scripts.mkdir(parents=True)
    shutil.copy2(source_project / "scripts/setup.ps1", real_scripts / "setup.ps1")
    junction_project = tmp_path / "junction-project"
    process_env = os.environ.copy()
    process_env["UNIN_TEST_JUNCTION_PATH"] = str(junction_project)
    process_env["UNIN_TEST_JUNCTION_TARGET"] = str(real_project)
    create = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$ErrorActionPreference='Stop'; "
                "New-Item -ItemType Junction "
                "-Path $env:UNIN_TEST_JUNCTION_PATH "
                "-Target $env:UNIN_TEST_JUNCTION_TARGET | Out-Null"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=process_env,
    )
    if create.returncode != 0:
        pytest.skip("junction creation is unavailable")

    try:
        validated = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(junction_project / "scripts/setup.ps1"),
                "-ValidatePathsOnly",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        junction_project.rmdir()

    assert validated.returncode != 0


def test_timeout_is_exactly_fifteen_minutes() -> None:
    assert wizard.DEFAULT_TIMEOUT_SECONDS == 900


def test_python_entrypoint_explicitly_requires_312_or_newer() -> None:
    source = Path(wizard.__file__).read_text(encoding="utf-8")
    assert "sys.version_info < (3, 12)" in source
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in source
    assert "st_file_attributes" in source
