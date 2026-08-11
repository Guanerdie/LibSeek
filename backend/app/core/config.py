from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
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
    database_url: str = "postgresql+psycopg://unin:unin@postgres:5432/unin"
    auth_local_username: SecretStr | None = None
    auth_local_password: SecretStr | None = None
    auth_session_signing_key: SecretStr | None = None
    auth_local_username_file: Path | None = None
    auth_local_password_file: Path | None = None
    auth_session_signing_key_file: Path | None = None
    auth_local_role: AuthRole = AuthRole.ADMIN
    auth_session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=300, le=7 * 24 * 60 * 60)
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
    nextfind_base_url: str = "https://nextfind.example"
    nextfind_username: SecretStr | None = None
    nextfind_password: SecretStr | None = None
    nextfind_username_file: Path | None = None
    nextfind_password_file: Path | None = None
    enable_tmdb_live: bool = False
    tmdb_base_url: str = "https://api.themoviedb.org"
    tmdb_access_token: SecretStr | None = None
    tmdb_access_token_file: Path | None = None
    tmdb_min_interval_seconds: float = 0.25
    tmdb_cache_ttl_seconds: int = 24 * 60 * 60
    tmdb_cache_max_entries: int = 512
    tmdb_allow_future_episodes: bool = False
    enable_avistaz_live_search: bool = False
    avistaz_base_url: str = "https://avistaz.to"
    avistaz_username: SecretStr | None = None
    avistaz_password: SecretStr | None = None
    avistaz_pid: SecretStr | None = None
    avistaz_username_file: Path | None = None
    avistaz_password_file: Path | None = None
    avistaz_pid_file: Path | None = None
    avistaz_min_interval_seconds: float = 6.0
    enable_qb_read_only: bool = False
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
    qb_save_path_ref: str = "qb-default-save-path"
    qb_allowed_save_paths: tuple[str, ...] = ()
    qb_plan_tags: tuple[str, ...] = ()
    qb_target_instance_ref: str = "qb-primary"
    avistaz_forbidden_qb_versions: tuple[str, ...] = ()
    enable_download_execution_control_plane: bool = False
    enable_download_executor: bool = False
    enable_avistaz_torrent_fetch: bool = False
    enable_qb_write: bool = False
    enable_download_monitor: bool = False
    enable_automation_engine: bool = False
    enable_media_import_control_plane: bool = False
    media_import_target_root_refs: tuple[str, ...] = ()
    media_import_preflight_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    execution_intent_default_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    execution_intent_max_ttl_seconds: int = Field(default=900, ge=30, le=3600)
    download_execution_lease_seconds: int = Field(default=300, ge=30, le=3600)
    download_execution_lease_renew_interval_seconds: float = Field(
        default=60, ge=1, le=1800
    )
    download_execution_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    download_execution_retry_max_seconds: int = Field(default=900, ge=1, le=86_400)
    download_executor_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    download_executor_ready_ttl_seconds: int = Field(default=90, ge=15, le=300)
    automation_preflight_ready_ttl_seconds: int = Field(default=90, ge=15, le=300)
    download_monitor_interval_seconds: float = Field(default=15, ge=1, le=3600)
    download_monitor_batch_size: int = Field(default=100, ge=1, le=500)
    torrent_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    torrent_max_files: int = Field(default=20_000, ge=1, le=100_000)
    approval_default_ttl_minutes: int = 60
    approval_max_ttl_minutes: int = 7 * 24 * 60
    approval_preflight_max_age_seconds: int = 300
    preferred_resolutions: tuple[str, ...] = ("2160p", "1080p")
    preferred_sources: tuple[str, ...] = ("BluRay", "WEB-DL")
    preferred_audio: tuple[str, ...] = ()
    preferred_subtitles: tuple[str, ...] = ("Chinese", "中文")
    max_candidate_size_bytes: int | None = None
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_heartbeat_stale_seconds: int = Field(default=30, ge=5, le=3600)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    job_lease_renew_interval_seconds: float = Field(default=60, ge=1, le=1800)

    @model_validator(mode="after")
    def validate_job_lease(self) -> Self:
        if self.job_lease_renew_interval_seconds >= self.job_lease_seconds:
            raise ValueError("job lease renewal interval must be shorter than the lease")
        return self

    @model_validator(mode="after")
    def validate_execution_intent_ttl(self) -> Self:
        if self.execution_intent_default_ttl_seconds > self.execution_intent_max_ttl_seconds:
            raise ValueError("execution intent default TTL cannot exceed the maximum TTL")
        if not self.qb_target_instance_ref.strip() or len(self.qb_target_instance_ref) > 180:
            raise ValueError("qb target instance ref must be between 1 and 180 characters")
        if (
            self.download_execution_lease_renew_interval_seconds
            >= self.download_execution_lease_seconds
        ):
            raise ValueError("download execution lease renewal must be shorter than the lease")
        if self.download_execution_retry_base_seconds > self.download_execution_retry_max_seconds:
            raise ValueError("download execution retry base cannot exceed retry maximum")
        return self

    @property
    def download_executor_enabled(self) -> bool:
        return all(
            (
                self.enable_download_executor,
                self.enable_avistaz_torrent_fetch,
                self.enable_qb_write,
            )
        )

    @field_validator(
        "allowed_external_hosts",
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
        "qb_allowed_save_paths",
        "qb_plan_tags",
        "avistaz_forbidden_qb_versions",
        "media_import_target_root_refs",
        mode="before",
    )
    @classmethod
    def parse_string_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("max_candidate_size_bytes", mode="before")
    @classmethod
    def parse_optional_integer(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
            return self.tmdb_token_value() is not None
        except OSError:
            return False

    @property
    def avistaz_configured(self) -> bool:
        try:
            return self.avistaz_credentials() is not None
        except OSError:
            return False

    @property
    def qb_configured(self) -> bool:
        try:
            return self.qb_credentials() is not None and bool(self.qb_allowed_hosts)
        except OSError:
            return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
