from __future__ import annotations

import json
import os
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.core.config import Settings


class RuntimeConfigError(Exception):
    """Stable failure for unreadable or unsafe local runtime configuration."""


class RuntimeConfigAlreadyInitialized(RuntimeConfigError):
    """Raised when the one-time administrator setup has already completed."""


class RuntimeConfigValidationError(RuntimeConfigError):
    """Raised when a requested runtime configuration value is invalid."""


@dataclass(frozen=True, slots=True)
class AuthRecord:
    username: str
    password_digest: str = field(repr=False)
    session_signing_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeSetupStatus:
    admin_configured: bool
    configuration_complete: bool


@dataclass(frozen=True, slots=True)
class RuntimePtSite:
    architecture: Literal["avistaz", "nexusphp"]
    base_url: str
    site_id: str
    display_name: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    pid: str | None = field(default=None, repr=False)
    cookie: str | None = field(default=None, repr=False)
    passkey: str | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        if self.architecture == "avistaz":
            return bool(self.base_url and self.username and self.password and self.pid)
        return bool(self.site_id and self.display_name and self.base_url and self.cookie)

    @property
    def runtime_supported(self) -> bool:
        return self.architecture == "avistaz"


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    nextfind_base_url: str | None = None
    nextfind_username: str | None = None
    nextfind_password: str | None = field(default=None, repr=False)
    tmdb_token: str | None = field(default=None, repr=False)
    outbound_proxy_url: str | None = None
    outbound_proxy_username: str | None = None
    outbound_proxy_password: str | None = field(default=None, repr=False)
    pt_site_architecture: Literal["avistaz", "nexusphp"] | None = None
    avistaz_site: RuntimePtSite | None = None
    nexusphp_site: RuntimePtSite | None = None
    qb_url: str | None = None
    qb_username: str | None = None
    qb_password: str | None = field(default=None, repr=False)
    qb_save_path: str | None = None
    qb_category: str | None = None
    qb_allow_insecure_http: bool | None = None

    @property
    def pt_site(self) -> RuntimePtSite | None:
        if self.pt_site_architecture == "avistaz":
            return self.avistaz_site
        if self.pt_site_architecture == "nexusphp":
            return self.nexusphp_site
        return None

    @property
    def avistaz_username(self) -> str | None:
        return self.avistaz_site.username if self.avistaz_site else None

    @property
    def avistaz_password(self) -> str | None:
        return self.avistaz_site.password if self.avistaz_site else None

    @property
    def avistaz_pid(self) -> str | None:
        return self.avistaz_site.pid if self.avistaz_site else None

    def settings_overrides(self) -> dict[str, object]:
        values: dict[str, object] = {}
        has_runtime_nextfind_identity = (
            self.nextfind_base_url is not None or self.nextfind_username is not None
        )
        if self.nextfind_base_url is not None:
            values["nextfind_base_url"] = self.nextfind_base_url
            values["nextfind_allowed_hosts"] = (_url_host(self.nextfind_base_url),)
        if has_runtime_nextfind_identity:
            values["nextfind_username"] = self.nextfind_username
            values["nextfind_username_file"] = None
            values["nextfind_password"] = self.nextfind_password
            values["nextfind_password_file"] = None
        if self.tmdb_token is not None:
            values["tmdb_access_token"] = self.tmdb_token
            values["tmdb_access_token_file"] = None
        has_runtime_proxy_identity = (
            self.outbound_proxy_url is not None or self.outbound_proxy_username is not None
        )
        if has_runtime_proxy_identity:
            values["outbound_proxy_url"] = self.outbound_proxy_url or None
            values["outbound_proxy_username"] = self.outbound_proxy_username
            values["outbound_proxy_password"] = self.outbound_proxy_password
        active_site = self.pt_site
        if active_site is not None:
            values["pt_site_architecture"] = active_site.architecture
            values["pt_site_id"] = active_site.site_id
            values["pt_site_display_name"] = active_site.display_name
            values["pt_site_runtime_configured"] = active_site.configured
            values["pt_site_runtime_supported"] = active_site.runtime_supported
            if active_site.architecture == "avistaz":
                values["avistaz_base_url"] = active_site.base_url
                values["avistaz_allowed_hosts"] = (_url_host(active_site.base_url),)
                values["avistaz_username"] = active_site.username
                values["avistaz_password"] = active_site.password
                values["avistaz_pid"] = active_site.pid
                values["avistaz_username_file"] = None
                values["avistaz_password_file"] = None
                values["avistaz_pid_file"] = None
            else:
                # An unsupported architecture must never inherit legacy AvistaZ material.
                values["avistaz_username"] = None
                values["avistaz_password"] = None
                values["avistaz_pid"] = None
                values["avistaz_username_file"] = None
                values["avistaz_password_file"] = None
                values["avistaz_pid_file"] = None
        has_runtime_qb_identity = self.qb_url is not None or self.qb_username is not None
        if self.qb_url is not None:
            values["qb_base_url"] = self.qb_url
            values["qb_base_url_file"] = None
        if has_runtime_qb_identity:
            values["qb_username"] = self.qb_username
            values["qb_username_file"] = None
            values["qb_password"] = self.qb_password
            values["qb_password_file"] = None
        if self.qb_save_path is not None:
            values["qb_target_save_path"] = self.qb_save_path or None
        if self.qb_category is not None:
            values["qb_target_category"] = self.qb_category or None
        if self.qb_allow_insecure_http is not None:
            values["qb_allow_insecure_http"] = self.qb_allow_insecure_http
        return values


_WRITE_LOCK = threading.Lock()
_MAX_CONFIG_BYTES = 64 * 1024
_AUTH_VERSION = 1
_INTEGRATIONS_VERSION = 2
_SUPPORTED_INTEGRATIONS_VERSIONS = frozenset({1, 2})


class RuntimeConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.root = Path(settings.runtime_config_dir)
        self.auth_dir = self.root / "auth"
        self.integrations_dir = self.root / "integrations"
        self.auth_path = self.auth_dir / "auth.json"
        self.integrations_path = self.integrations_dir / "config.json"

    def setup_status(self) -> RuntimeSetupStatus:
        return RuntimeSetupStatus(
            admin_configured=self.auth_record() is not None,
            configuration_complete=self.configuration_complete(),
        )

    def setup_admin(self, username: str, password_digest: str) -> AuthRecord:
        validated_username = _single_line(username, maximum=120)
        validated_digest = _single_line(password_digest, maximum=1024)
        record = AuthRecord(
            username=validated_username,
            password_digest=validated_digest,
            session_signing_key=secrets.token_urlsafe(48),
        )
        payload = {
            "version": _AUTH_VERSION,
            "username": record.username,
            "password_digest": record.password_digest,
            "session_signing_key": record.session_signing_key,
        }
        with _WRITE_LOCK:
            _ensure_private_directory(self.auth_dir)
            try:
                _atomic_create_json(self.auth_path, payload)
            except FileExistsError as exc:
                raise RuntimeConfigAlreadyInitialized(
                    "administrator is already initialized"
                ) from exc
            except OSError as exc:
                raise RuntimeConfigError(
                    "runtime authentication configuration is unavailable"
                ) from exc
        return record

    def auth_record(self) -> AuthRecord | None:
        if not self.auth_path.exists():
            return None
        try:
            payload = _read_json_object(self.auth_path)
            if payload.get("version") != _AUTH_VERSION:
                raise ValueError
            username = _single_line(payload.get("username"), maximum=120)
            password_digest = _single_line(payload.get("password_digest"), maximum=1024)
            signing_key = _single_line(payload.get("session_signing_key"), maximum=1024)
            if len(signing_key) < 32:
                raise ValueError
            return AuthRecord(username, password_digest, signing_key)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeConfigError("runtime authentication configuration is invalid") from exc

    def configuration(self) -> RuntimeConfiguration:
        if not self.integrations_path.exists():
            return RuntimeConfiguration()
        try:
            payload = _read_json_object(self.integrations_path)
            version = payload.get("version")
            if version not in _SUPPORTED_INTEGRATIONS_VERSIONS:
                raise ValueError
            return _configuration_from_payload(payload, version=version, settings=self._settings)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeConfigError("runtime integration configuration is invalid") from exc

    def save_configuration(
        self, updates: Mapping[str, Mapping[str, object]]
    ) -> RuntimeConfiguration:
        with _WRITE_LOCK:
            current = self.configuration()
            updated = _merge_configuration(current, updates)
            payload = _configuration_payload(updated)
            try:
                _ensure_private_directory(self.integrations_dir)
                _atomic_replace_json(self.integrations_path, payload)
            except OSError as exc:
                raise RuntimeConfigError(
                    "runtime integration configuration is unavailable"
                ) from exc
        return updated

    def configuration_complete(self) -> bool:
        runtime = self.configuration()
        effective = _effective_values(self._settings, runtime)
        return all(
            (
                effective["nextfind_base_url"],
                effective["nextfind_username"],
                effective["nextfind_password"],
                effective["tmdb_token"],
                effective["pt_site_configured"],
                effective["qb_url"],
                effective["qb_username"],
                effective["qb_password"],
            )
        )


def runtime_store(settings: Settings) -> RuntimeConfigStore:
    return RuntimeConfigStore(settings)


def _effective_values(settings: Settings, runtime: RuntimeConfiguration) -> dict[str, str | bool]:
    try:
        nextfind = settings.nextfind_credentials()
    except OSError:
        nextfind = None
    try:
        avistaz = settings.avistaz_credentials()
    except OSError:
        avistaz = None
    try:
        qb = settings.qb_credentials()
    except OSError:
        qb = None
    try:
        tmdb = settings.tmdb_token_value() or ""
    except OSError:
        tmdb = ""
    if runtime.pt_site is not None:
        pt_site_configured = runtime.pt_site.configured
    else:
        pt_site_configured = bool(avistaz)
    has_runtime_nextfind_identity = (
        runtime.nextfind_base_url is not None or runtime.nextfind_username is not None
    )
    has_runtime_qb_identity = runtime.qb_url is not None or runtime.qb_username is not None
    return {
        "nextfind_base_url": runtime.nextfind_base_url or settings.nextfind_base_url,
        "nextfind_username": (runtime.nextfind_username or "")
        if has_runtime_nextfind_identity
        else (nextfind[0] if nextfind else ""),
        "nextfind_password": runtime.nextfind_password or ""
        if has_runtime_nextfind_identity
        else (nextfind[1] if nextfind else ""),
        "tmdb_token": runtime.tmdb_token if runtime.tmdb_token is not None else tmdb,
        "pt_site_configured": pt_site_configured,
        "qb_url": runtime.qb_url or "" if has_runtime_qb_identity else (qb[0] if qb else ""),
        "qb_username": (runtime.qb_username or "")
        if has_runtime_qb_identity
        else (qb[1] if qb else ""),
        "qb_password": runtime.qb_password or ""
        if has_runtime_qb_identity
        else (qb[2] if qb else ""),
        "qb_save_path": runtime.qb_save_path
        if runtime.qb_save_path is not None
        else (settings.qb_target_save_path or ""),
        "qb_category": runtime.qb_category
        if runtime.qb_category is not None
        else (settings.qb_target_category or ""),
    }


def _merge_configuration(
    current: RuntimeConfiguration, updates: Mapping[str, Mapping[str, object]]
) -> RuntimeConfiguration:
    allowed_groups = {"nextfind", "tmdb", "outbound_proxy", "pt_site", "qbittorrent"}
    if set(updates) - allowed_groups:
        raise RuntimeConfigError("runtime integration update is invalid")
    nextfind = updates.get("nextfind", {})
    tmdb = updates.get("tmdb", {})
    proxy = updates.get("outbound_proxy", {})
    qb = updates.get("qbittorrent", {})
    try:
        nextfind_base_url = _optional_public_update(
            nextfind, "base_url", current.nextfind_base_url, maximum=2048
        )
        if nextfind_base_url is not None:
            nextfind_base_url = _validate_https_origin(nextfind_base_url)
        nextfind_username = _optional_public_update(
            nextfind, "username", current.nextfind_username, maximum=120
        )
        same_nextfind_identity = (
            nextfind_base_url == current.nextfind_base_url
            and nextfind_username == current.nextfind_username
        )
        nextfind_password = _optional_secret_update(
            nextfind,
            "password",
            current.nextfind_password if same_nextfind_identity else None,
        )
        tmdb_token = _optional_secret_update(tmdb, "token", current.tmdb_token)
        proxy_url = _optional_public_update(
            proxy, "url", current.outbound_proxy_url, maximum=2048
        )
        if proxy_url:
            proxy_url = _validate_proxy_origin(proxy_url)
        proxy_username = _optional_public_update(
            proxy, "username", current.outbound_proxy_username, maximum=120
        )
        same_proxy_identity = (
            proxy_url == current.outbound_proxy_url
            and proxy_username == current.outbound_proxy_username
        )
        proxy_password = _optional_secret_update(
            proxy,
            "password",
            current.outbound_proxy_password if same_proxy_identity else None,
        )
        if not proxy_url:
            proxy_username = None
            proxy_password = None
        elif bool(proxy_username) != bool(proxy_password):
            raise ValueError
        pt_site_architecture, avistaz_site, nexusphp_site = _merge_pt_sites(
            current, updates.get("pt_site")
        )
        qb_url = _optional_public_update(qb, "url", current.qb_url, maximum=2048)
        qb_username = _optional_public_update(qb, "username", current.qb_username, maximum=120)
        same_qb_identity = qb_url == current.qb_url and qb_username == current.qb_username
        qb_password = _optional_secret_update(
            qb, "password", current.qb_password if same_qb_identity else None
        )
        qb_save_path = _optional_public_update(qb, "save_path", current.qb_save_path, maximum=4096)
        qb_category = _optional_public_update(qb, "category", current.qb_category, maximum=300)
        allow = qb.get("allow_insecure_http", current.qb_allow_insecure_http)
        if allow is not None and type(allow) is not bool:
            raise ValueError
        _validate_qb_url(qb_url, allow_insecure_http=bool(allow))
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigValidationError("runtime integration update is invalid") from exc
    return RuntimeConfiguration(
        nextfind_base_url=nextfind_base_url,
        nextfind_username=nextfind_username,
        nextfind_password=nextfind_password,
        tmdb_token=tmdb_token,
        outbound_proxy_url=proxy_url,
        outbound_proxy_username=proxy_username,
        outbound_proxy_password=proxy_password,
        pt_site_architecture=pt_site_architecture,
        avistaz_site=avistaz_site,
        nexusphp_site=nexusphp_site,
        qb_url=qb_url,
        qb_username=qb_username,
        qb_password=qb_password,
        qb_save_path=qb_save_path,
        qb_category=qb_category,
        qb_allow_insecure_http=allow,
    )


def _merge_pt_sites(
    current: RuntimeConfiguration, update: Mapping[str, object] | None
) -> tuple[
    Literal["avistaz", "nexusphp"] | None,
    RuntimePtSite | None,
    RuntimePtSite | None,
]:
    if update is None:
        return current.pt_site_architecture, current.avistaz_site, current.nexusphp_site
    architecture = update.get("architecture")
    if architecture not in {"avistaz", "nexusphp"}:
        raise ValueError
    if architecture == "avistaz":
        base_url = _validate_https_origin(_required_public(update, "base_url", maximum=2048))
        username = _required_public(update, "username", maximum=120)
        current_site = current.avistaz_site
        same_identity = bool(
            current_site
            and current_site.base_url == base_url
            and current_site.username == username
        )
        site = RuntimePtSite(
            architecture="avistaz",
            base_url=base_url,
            site_id="avistaz",
            display_name="AvistaZ",
            username=username,
            password=_optional_secret_update(
                update,
                "password",
                current_site.password if same_identity and current_site else None,
            ),
            pid=_optional_secret_update(
                update, "pid", current_site.pid if same_identity and current_site else None
            ),
        )
        return "avistaz", site, current.nexusphp_site
    site_id = _required_public(update, "site_id", maximum=24)
    _validate_site_id(site_id)
    display_name = _required_public(update, "display_name", maximum=100)
    base_url = _validate_https_origin(_required_public(update, "base_url", maximum=2048))
    current_site = current.nexusphp_site
    same_identity = bool(
        current_site and current_site.site_id == site_id and current_site.base_url == base_url
    )
    site = RuntimePtSite(
        architecture="nexusphp",
        base_url=base_url,
        site_id=site_id,
        display_name=display_name,
        cookie=_optional_secret_update(
            update, "cookie", current_site.cookie if same_identity and current_site else None
        ),
        passkey=_optional_secret_update(
            update, "passkey", current_site.passkey if same_identity and current_site else None
        ),
    )
    return "nexusphp", current.avistaz_site, site


def _validate_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError
    return value.rstrip("/")


def _validate_proxy_origin(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError from exc
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
        raise ValueError
    return value.rstrip("/")


def _url_host(value: str) -> str:
    validated = _validate_https_origin(value)
    host = urlsplit(validated).hostname
    if not host:
        raise ValueError
    return host.casefold()


def _validate_site_id(value: str) -> None:
    import re

    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?", value) is None:
        raise ValueError


def _validate_qb_url(value: str | None, *, allow_insecure_http: bool) -> None:
    if value in {None, ""}:
        return
    parsed = urlsplit(value)
    allowed_schemes = {"https", "http"} if allow_insecure_http else {"https"}
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError


def _required_public(values: Mapping[str, object], key: str, *, maximum: int) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError
    validated = _optional_public_update(values, key, None, maximum=maximum)
    if not validated:
        raise ValueError
    return validated


def _optional_public_update(
    values: Mapping[str, object], key: str, current: str | None, *, maximum: int
) -> str | None:
    if key not in values:
        return current
    value = values[key]
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum:
        raise ValueError
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError
    return value


def _optional_secret_update(
    values: Mapping[str, object], key: str, current: str | None
) -> str | None:
    if key not in values or values[key] in {None, ""}:
        return current
    return _single_line(values[key], maximum=8192)


def _configuration_payload(configuration: RuntimeConfiguration) -> dict[str, object]:
    return {
        "version": _INTEGRATIONS_VERSION,
        "nextfind": {
            "base_url": configuration.nextfind_base_url,
            "username": configuration.nextfind_username,
            "password": configuration.nextfind_password,
        },
        "tmdb": {"token": configuration.tmdb_token},
        "outbound_proxy": {
            "url": configuration.outbound_proxy_url,
            "username": configuration.outbound_proxy_username,
            "password": configuration.outbound_proxy_password,
        },
        "pt_sites": {
            "active_architecture": configuration.pt_site_architecture,
            "avistaz": _pt_site_payload(configuration.avistaz_site),
            "nexusphp": _pt_site_payload(configuration.nexusphp_site),
        },
        "qbittorrent": {
            "url": configuration.qb_url,
            "username": configuration.qb_username,
            "password": configuration.qb_password,
            "save_path": configuration.qb_save_path,
            "category": configuration.qb_category,
            "allow_insecure_http": configuration.qb_allow_insecure_http,
        },
    }


def _pt_site_payload(site: RuntimePtSite | None) -> dict[str, object] | None:
    if site is None:
        return None
    return {
        "architecture": site.architecture,
        "base_url": site.base_url,
        "site_id": site.site_id,
        "display_name": site.display_name,
        "username": site.username,
        "password": site.password,
        "pid": site.pid,
        "cookie": site.cookie,
        "passkey": site.passkey,
    }


def _configuration_from_payload(
    payload: Mapping[str, Any], *, version: object, settings: Settings
) -> RuntimeConfiguration:
    nextfind = _mapping(payload.get("nextfind"))
    tmdb = _mapping(payload.get("tmdb"))
    proxy = _mapping(payload.get("outbound_proxy", {}))
    qb = _mapping(payload.get("qbittorrent"))
    allow = qb.get("allow_insecure_http")
    if allow is not None and type(allow) is not bool:
        raise ValueError
    nextfind_base_url = _stored_optional_string(nextfind.get("base_url"), 2048)
    if version == 1 and nextfind_base_url is None:
        nextfind_base_url = settings.nextfind_base_url
    if nextfind_base_url is not None:
        nextfind_base_url = _validate_https_origin(nextfind_base_url)
    qb_url = _stored_optional_string(qb.get("url"), 2048)
    _validate_qb_url(qb_url, allow_insecure_http=bool(allow))
    proxy_url = _stored_optional_string(proxy.get("url"), 2048)
    if proxy_url:
        proxy_url = _validate_proxy_origin(proxy_url)
    proxy_username = _stored_optional_string(proxy.get("username"), 120)
    proxy_password = _stored_optional_string(proxy.get("password"), 8192)
    if not proxy_url and (proxy_username is not None or proxy_password is not None):
        raise ValueError
    if proxy_url and bool(proxy_username) != bool(proxy_password):
        raise ValueError
    pt_site_architecture: Literal["avistaz", "nexusphp"] | None
    if version == 1:
        avistaz_site = _v1_avistaz_site(payload, settings)
        nexusphp_site = None
        if avistaz_site is not None:
            pt_site_architecture = "avistaz"
        else:
            pt_site_architecture = None
    else:
        pt_sites = _mapping(payload.get("pt_sites"))
        active = pt_sites.get("active_architecture")
        if active not in {None, "avistaz", "nexusphp"}:
            raise ValueError
        if active == "avistaz":
            pt_site_architecture = "avistaz"
        elif active == "nexusphp":
            pt_site_architecture = "nexusphp"
        else:
            pt_site_architecture = None
        avistaz_site = _stored_pt_site(pt_sites.get("avistaz"), architecture="avistaz")
        nexusphp_site = _stored_pt_site(pt_sites.get("nexusphp"), architecture="nexusphp")
        if pt_site_architecture == "avistaz" and avistaz_site is None:
            raise ValueError
        if pt_site_architecture == "nexusphp" and nexusphp_site is None:
            raise ValueError
    return RuntimeConfiguration(
        nextfind_base_url=nextfind_base_url,
        nextfind_username=_stored_optional_string(nextfind.get("username"), 120),
        nextfind_password=_stored_optional_string(nextfind.get("password"), 8192),
        tmdb_token=_stored_optional_string(tmdb.get("token"), 8192),
        outbound_proxy_url=proxy_url,
        outbound_proxy_username=proxy_username,
        outbound_proxy_password=proxy_password,
        pt_site_architecture=pt_site_architecture,
        avistaz_site=avistaz_site,
        nexusphp_site=nexusphp_site,
        qb_url=qb_url,
        qb_username=_stored_optional_string(qb.get("username"), 120),
        qb_password=_stored_optional_string(qb.get("password"), 8192),
        qb_save_path=_stored_optional_string(qb.get("save_path"), 4096),
        qb_category=_stored_optional_string(qb.get("category"), 300),
        qb_allow_insecure_http=allow,
    )


def _v1_avistaz_site(payload: Mapping[str, Any], settings: Settings) -> RuntimePtSite | None:
    avistaz = _mapping(payload.get("avistaz"))
    username = _stored_optional_string(avistaz.get("username"), 120)
    password = _stored_optional_string(avistaz.get("password"), 8192)
    pid = _stored_optional_string(avistaz.get("pid"), 8192)
    if username is None and password is None and pid is None:
        return None
    return RuntimePtSite(
        architecture="avistaz",
        base_url=_validate_https_origin(settings.avistaz_base_url),
        site_id="avistaz",
        display_name="AvistaZ",
        username=username,
        password=password,
        pid=pid,
    )


def _stored_pt_site(
    value: object, *, architecture: Literal["avistaz", "nexusphp"]
) -> RuntimePtSite | None:
    if value is None:
        return None
    site = _mapping(value)
    if site.get("architecture") != architecture:
        raise ValueError
    base_url = _validate_https_origin(_stored_required_string(site.get("base_url"), 2048))
    site_id = _stored_required_string(site.get("site_id"), 24)
    _validate_site_id(site_id)
    display_name = _stored_required_string(site.get("display_name"), 100)
    return RuntimePtSite(
        architecture=architecture,
        base_url=base_url,
        site_id=site_id,
        display_name=display_name,
        username=_stored_optional_string(site.get("username"), 120),
        password=_stored_optional_string(site.get("password"), 8192),
        pid=_stored_optional_string(site.get("pid"), 8192),
        cookie=_stored_optional_string(site.get("cookie"), 8192),
        passkey=_stored_optional_string(site.get("passkey"), 8192),
    )


def _stored_required_string(value: object, maximum: int) -> str:
    stored = _stored_optional_string(value, maximum)
    if not stored:
        raise ValueError
    return stored


def _stored_optional_string(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum:
        raise ValueError
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError
    return value


def _single_line(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    stat_result = path.stat()
    if not path.is_file() or stat_result.st_size > _MAX_CONFIG_BYTES:
        raise RuntimeConfigError("runtime configuration file is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError
    return payload


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise OSError("runtime configuration directory is unsafe")
    if os.name != "nt":
        path.chmod(0o700)


def _atomic_create_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    try:
        _write_new_json(temporary, payload)
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_replace_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    try:
        _write_new_json(temporary, payload)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(body) > _MAX_CONFIG_BYTES:
        raise OSError("runtime configuration is too large")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    if os.name != "nt":
        path.chmod(0o600)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
