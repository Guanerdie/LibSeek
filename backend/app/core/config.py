from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.enums import AuthRole


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        enable_decoding=False,
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "UNIN 影视缺失资源编排系统"
    app_env: str = "development"
    api_prefix: str = "/api"
    database_url: str = "sqlite+aiosqlite:///./unin.db"
    automation_scheduler_enabled: bool = False
    automation_scheduler_poll_seconds: int = Field(default=60, ge=5, le=3600)
    # RSS reverse matching, off by default: it needs a site profile with an
    # rss_path and a runtime passkey, and polling a site on a schedule is the
    # operator's call to make, not a default.
    rss_matcher_enabled: bool = False
    rss_matcher_poll_seconds: int = Field(default=600, ge=60, le=3600)
    rss_matcher_window_hours: int = Field(default=1, ge=1, le=24)
    runtime_config_dir: Path = Field(
        default=Path("/var/lib/unin"),
        validation_alias=AliasChoices("UNIN_RUNTIME_CONFIG_DIR", "runtime_config_dir"),
    )
    auth_local_username: SecretStr | None = None
    auth_local_password: SecretStr | None = None
    auth_session_signing_key: SecretStr | None = None
    auth_local_username_file: Path | None = None
    auth_local_password_file: Path | None = None
    auth_session_signing_key_file: Path | None = None
    auth_local_role: AuthRole = AuthRole.ADMIN
    auth_session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=300, le=7 * 24 * 60 * 60)
    # Used when the sign-in asks to be remembered.  Longer than the plain
    # session because the point is not to retype a password every day on a
    # device that is already trusted.
    auth_session_remember_ttl_seconds: int = Field(
        default=30 * 24 * 60 * 60, ge=3600, le=180 * 24 * 60 * 60
    )
    auth_bootstrap_csrf_ttl_seconds: int = Field(default=10 * 60, ge=60, le=60 * 60)
    auth_cookie_secure: bool = False
    allowed_external_hosts: tuple[str, ...] = (
        "nextfind.example",
        "api.themoviedb.org",
        "avistaz.to",
    )
    external_connect_timeout_seconds: float = 5.0
    external_read_timeout_seconds: float = 30.0
    external_max_response_bytes: int = 10 * 1024 * 1024
    external_max_ndjson_line_bytes: int = 1024 * 1024
    outbound_proxy_url: str | None = None
    outbound_proxy_username: SecretStr | None = None
    outbound_proxy_password: SecretStr | None = None
    nextfind_base_url: str = "https://nextfind.example"
    nextfind_allowed_hosts: tuple[str, ...] = ("nextfind.example",)
    nextfind_username: SecretStr | None = None
    nextfind_password: SecretStr | None = None
    nextfind_username_file: Path | None = None
    nextfind_password_file: Path | None = None
    tmdb_base_url: str = "https://api.themoviedb.org"
    tmdb_access_token: SecretStr | None = None
    tmdb_access_token_file: Path | None = None
    tmdb_min_interval_seconds: float = 0.25
    tmdb_cache_ttl_seconds: int = 24 * 60 * 60
    tmdb_cache_max_entries: int = 512
    tmdb_allow_future_episodes: bool = False
    avistaz_base_url: str = "https://avistaz.to"
    avistaz_allowed_hosts: tuple[str, ...] = ("avistaz.to",)
    avistaz_username: SecretStr | None = None
    avistaz_password: SecretStr | None = None
    avistaz_pid: SecretStr | None = None
    avistaz_username_file: Path | None = None
    avistaz_password_file: Path | None = None
    avistaz_pid_file: Path | None = None
    avistaz_min_interval_seconds: float = 6.0
    pt_site_architecture: Literal["avistaz", "nexusphp"] = "avistaz"
    pt_site_id: str = "avistaz"
    pt_site_display_name: str = "AvistaZ"
    pt_site_runtime_configured: bool | None = None
    pt_site_runtime_supported: bool = True
    qb_base_url: SecretStr | None = None
    qb_username: SecretStr | None = None
    qb_password: SecretStr | None = None
    qb_base_url_file: Path | None = None
    qb_username_file: Path | None = None
    qb_password_file: Path | None = None
    qb_allowed_hosts: tuple[str, ...] = ()
    qb_allow_insecure_http: bool = False
    qb_target_category: str | None = None
    qb_target_save_path: str | None = None
    qb_plan_tags: tuple[str, ...] = ()
    enable_qb_write: bool = False
    torrent_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    torrent_max_files: int = Field(default=20_000, ge=1, le=100_000)
    preferred_resolutions: tuple[str, ...] = ("2160p", "1080p")
    preferred_sources: tuple[str, ...] = ("BluRay", "WEB-DL")
    preferred_audio: tuple[str, ...] = ()
    preferred_subtitles: tuple[str, ...] = ("Chinese", "中文")
    max_candidate_size_bytes: int | None = None
    @field_validator(
        "allowed_external_hosts",
        "nextfind_allowed_hosts",
        "avistaz_allowed_hosts",
        "preferred_resolutions",
        "preferred_sources",
        "preferred_audio",
        "preferred_subtitles",
        "qb_allowed_hosts",
        mode="before",
    )
    @classmethod
    def parse_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @field_validator(
        "qb_plan_tags",
        mode="before",
    )
    @classmethod
    def parse_string_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator(
        "auth_local_username_file",
        "auth_local_password_file",
        "auth_session_signing_key_file",
        "nextfind_username_file",
        "nextfind_password_file",
        "tmdb_access_token_file",
        "avistaz_username_file",
        "avistaz_password_file",
        "avistaz_pid_file",
        "qb_base_url_file",
        "qb_username_file",
        "qb_password_file",
        mode="before",
    )
    @classmethod
    def parse_optional_secret_file(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("max_candidate_size_bytes", mode="before")
    @classmethod
    def parse_optional_integer(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("outbound_proxy_url", mode="before")
    @classmethod
    def parse_optional_proxy_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("outbound_proxy_url")
    @classmethod
    def validate_outbound_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("invalid outbound proxy URL") from exc
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("invalid outbound proxy URL")
        return value.rstrip("/")

    @staticmethod
    def _read_secret(secret: SecretStr | None, path: Path | None) -> str | None:
        if path is not None:
            value = path.read_text(encoding="utf-8").strip()
            return value or None
        return secret.get_secret_value() if secret is not None else None

    def nextfind_credentials(self) -> tuple[str, str] | None:
        username = self._read_secret(self.nextfind_username, self.nextfind_username_file)
        password = self._read_secret(self.nextfind_password, self.nextfind_password_file)
        if not username or not password:
            return None
        return username, password

    def auth_material(self) -> tuple[str, str, str, AuthRole] | None:
        username = self._read_secret(self.auth_local_username, self.auth_local_username_file)
        password = self._read_secret(self.auth_local_password, self.auth_local_password_file)
        signing_key = self._read_secret(
            self.auth_session_signing_key, self.auth_session_signing_key_file
        )
        if (
            not username
            or len(username) > 120
            or not password
            or not signing_key
            or len(signing_key) < 32
        ):
            return None
        return username, password, signing_key, self.auth_local_role

    def tmdb_token_value(self) -> str | None:
        return self._read_secret(self.tmdb_access_token, self.tmdb_access_token_file)

    def avistaz_credentials(self) -> tuple[str, str, str] | None:
        username = self._read_secret(self.avistaz_username, self.avistaz_username_file)
        password = self._read_secret(self.avistaz_password, self.avistaz_password_file)
        pid = self._read_secret(self.avistaz_pid, self.avistaz_pid_file)
        if not username or not password or not pid:
            return None
        return username, password, pid

    def qb_credentials(self) -> tuple[str, str, str] | None:
        base_url = self._read_secret(self.qb_base_url, self.qb_base_url_file)
        username = self._read_secret(self.qb_username, self.qb_username_file)
        password = self._read_secret(self.qb_password, self.qb_password_file)
        if not base_url or not username or not password:
            return None
        return base_url, username, password

    def qb_base_url_value(self) -> str | None:
        return self._read_secret(self.qb_base_url, self.qb_base_url_file)

    def outbound_proxy(self) -> httpx.Proxy | None:
        if not self.outbound_proxy_url:
            return None
        username = (
            self.outbound_proxy_username.get_secret_value()
            if self.outbound_proxy_username is not None
            else None
        )
        password = (
            self.outbound_proxy_password.get_secret_value()
            if self.outbound_proxy_password is not None
            else None
        )
        auth = (username, password) if username and password else None
        return httpx.Proxy(self.outbound_proxy_url, auth=auth)

    @property
    def nextfind_configured(self) -> bool:
        try:
            return self.nextfind_credentials() is not None
        except OSError:
            return False

    @property
    def auth_configured(self) -> bool:
        try:
            return self.auth_material() is not None
        except OSError:
            return False

    @property
    def tmdb_configured(self) -> bool:
        try:
            return bool(self.tmdb_token_value())
        except OSError:
            return False

    @property
    def avistaz_configured(self) -> bool:
        try:
            return self.avistaz_credentials() is not None
        except OSError:
            return False

    @property
    def pt_site_configured(self) -> bool:
        if self.pt_site_runtime_configured is not None:
            return self.pt_site_runtime_configured
        return self.pt_site_architecture == "avistaz" and self.avistaz_configured

    @property
    def pt_site_search_ready(self) -> bool:
        return bool(
            self.pt_site_architecture == "avistaz"
            and self.pt_site_runtime_supported
            and self.pt_site_configured
        )

    @property
    def qb_configured(self) -> bool:
        try:
            return self.qb_credentials() is not None and bool(self.qb_allowed_hosts)
        except OSError:
            return False


def apply_runtime_configuration(base: Settings) -> Settings:
    from app.core.runtime_config import runtime_store

    runtime = runtime_store(base).configuration()
    updates = runtime.settings_overrides()
    if not updates:
        return base
    effective = Settings.model_validate({**base.model_dump(), **updates})
    from urllib.parse import urlsplit

    for url_name, hosts_name in (
        ("nextfind_base_url", "nextfind_allowed_hosts"),
        ("avistaz_base_url", "avistaz_allowed_hosts"),
    ):
        parsed = urlsplit(str(getattr(effective, url_name)))
        host = (parsed.hostname or "").casefold()
        if host:
            setattr(effective, hosts_name, (host,))
    if effective.qb_credentials() is not None:
        parsed = urlsplit(effective.qb_base_url_value() or "")
        host = (parsed.hostname or "").lower()
        scheme_allowed = parsed.scheme == "https" or (
            parsed.scheme == "http" and effective.qb_allow_insecure_http
        )
        if host and scheme_allowed and not parsed.username and not parsed.password:
            effective.qb_allowed_hosts = (host,)
    return effective


def get_settings() -> Settings:
    return apply_runtime_configuration(Settings())
