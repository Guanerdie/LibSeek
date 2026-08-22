from pathlib import Path

import pytest

from app.core.config import Settings


def _clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name, raising=False)
        monkeypatch.delenv(field_name.upper(), raising=False)


def test_example_dotenv_uses_supported_comma_separated_and_empty_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    env_file = Path(__file__).resolve().parents[2] / ".env.example"

    settings = Settings(_env_file=env_file)

    assert settings.allowed_external_hosts == (
        "nextfind.example",
        "api.themoviedb.org",
        "avistaz.to",
    )
    assert settings.nextfind_allowed_hosts == ("nextfind.example",)
    assert settings.qb_plan_tags == ()
    assert settings.max_candidate_size_bytes is None
    assert settings.auth_material() is None
    assert settings.tmdb_configured is False


def test_tuple_settings_parse_compose_style_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    monkeypatch.setenv("ALLOWED_EXTERNAL_HOSTS", "Example.COM, api.example.com")
    monkeypatch.setenv("NEXTFIND_ALLOWED_HOSTS", "NextFind.EXAMPLE.com")
    monkeypatch.setenv("QB_PLAN_TAGS", "unin, imported")

    settings = Settings(_env_file=None)

    assert settings.allowed_external_hosts == ("example.com", "api.example.com")
    assert settings.nextfind_allowed_hosts == ("nextfind.example.com",)
    assert settings.qb_plan_tags == ("unin", "imported")


def test_empty_proxy_credentials_do_not_enable_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    monkeypatch.setenv("OUTBOUND_PROXY_URL", "http://proxy.example.com:7890")
    monkeypatch.setenv("OUTBOUND_PROXY_USERNAME", "")
    monkeypatch.setenv("OUTBOUND_PROXY_PASSWORD", "")

    proxy = Settings(_env_file=None).outbound_proxy()

    assert proxy is not None
    assert proxy.auth is None
