from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "UNIN 影视缺失资源编排系统"
    app_env: str = "development"
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://unin:unin@postgres:5432/unin"
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
    avistaz_forbidden_qb_versions: tuple[str, ...] = ()
    approval_default_ttl_minutes: int = 60
    approval_max_ttl_minutes: int = 7 * 24 * 60
    approval_preflight_max_age_seconds: int = 300
    preferred_resolutions: tuple[str, ...] = ("2160p", "1080p")
    preferred_sources: tuple[str, ...] = ("BluRay", "WEB-DL")
    preferred_audio: tuple[str, ...] = ()
    preferred_subtitles: tuple[str, ...] = ("Chinese", "中文")
    max_candidate_size_bytes: int | None = None
    worker_poll_seconds: float = 2.0
    worker_heartbeat_stale_seconds: int = 30
    job_max_attempts: int = 3

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
        mode="before",
    )
    @classmethod
    def parse_string_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
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

    @property
    def nextfind_configured(self) -> bool:
        try:
            return self.nextfind_credentials() is not None
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
