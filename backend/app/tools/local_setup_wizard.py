# ruff: noqa: E501
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import stat
import sys
import time
import webbrowser
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import BinaryIO, Final, NoReturn, cast
from urllib.parse import urlsplit

# The embedded, nonce-protected HTML is intentionally kept as a single auditable document.

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


DEFAULT_TIMEOUT_SECONDS: Final = 15 * 60
MAX_REQUEST_BODY_BYTES: Final = 64 * 1024
MAX_DOTENV_BYTES: Final = 1024 * 1024
MAX_TRANSACTION_MANIFEST_BYTES: Final = 64 * 1024
TOKEN_HEADER: Final = "X-UNIN-Setup-Token"
WINDOWS_ACL_NONCE_ENV: Final = "UNIN_SETUP_ACL_NONCE"
WINDOWS_ACL_MARKER_NAME: Final = "setup.acl-marker"
TRANSACTION_MANIFEST_NAME: Final = "transaction.json"
TRANSACTION_COMMITTED_NAME: Final = "COMMITTED"
TRANSACTION_COMMITTED_TEMP_NAME: Final = "COMMITTED.tmp"
TRANSACTION_PREPARING_NAME: Final = "PREPARING"
TRANSACTION_PREPARING_TEMP_NAME: Final = "PREPARING.tmp"
_GROUP_ORDER: Final = ("discovery", "avistaz", "qb")
_STRICT_DOTENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_DOTENV_INTERPOLATION = re.compile(r"\$(?:\{)?[A-Za-z_]")
_RUN_DIRECTORY_NAME = re.compile(r"^[0-9a-f]{32}$")
_STAGED_ARTIFACT_NAME = re.compile(r"^[0-9a-f]{32}\.(?:tmp|rollback)$")

_GROUP_SECRET_FIELDS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "discovery": (
        ("auth_local_username", "auth_local_username.txt"),
        ("auth_local_password", "auth_local_password.txt"),
        ("auth_session_signing_key", "auth_session_signing_key.txt"),
        ("nextfind_username", "nextfind_username.txt"),
        ("nextfind_password", "nextfind_password.txt"),
        ("tmdb_access_token", "tmdb_access_token.txt"),
    ),
    "avistaz": (
        ("avistaz_username", "avistaz_username.txt"),
        ("avistaz_password", "avistaz_password.txt"),
        ("avistaz_pid", "avistaz_pid.txt"),
    ),
    "qb": (
        ("qb_base_url", "qb_base_url.txt"),
        ("qb_username", "qb_username.txt"),
        ("qb_password", "qb_password.txt"),
    ),
}

_PLAINTEXT_CREDENTIAL_ENV_NAMES: Final = (
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

_GROUP_PLAINTEXT_CREDENTIAL_ENV_NAMES: Final = {
    "discovery": (
        "AUTH_LOCAL_USERNAME",
        "AUTH_LOCAL_PASSWORD",
        "AUTH_SESSION_SIGNING_KEY",
        "NEXTFIND_USERNAME",
        "NEXTFIND_PASSWORD",
        "TMDB_ACCESS_TOKEN",
    ),
    "avistaz": ("AVISTAZ_USERNAME", "AVISTAZ_PASSWORD", "AVISTAZ_PID"),
    "qb": ("QB_BASE_URL", "QB_USERNAME", "QB_PASSWORD"),
}

_NEW_ENV_DISABLED_FLAGS: Final = (
    "ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE",
    "ENABLE_DOWNLOAD_EXECUTOR",
    "ENABLE_AVISTAZ_TORRENT_FETCH",
    "ENABLE_QB_WRITE",
    "ENABLE_DOWNLOAD_MONITOR",
    "ENABLE_AUTOMATION_ENGINE",
    "ENABLE_MEDIA_IMPORT_CONTROL_PLANE",
)

_GROUP_LIVE_FLAGS: Final = {
    "discovery": "ENABLE_TMDB_LIVE",
    "avistaz": "ENABLE_AVISTAZ_LIVE_SEARCH",
    "qb": "ENABLE_QB_READ_ONLY",
}

_PUBLIC_ERROR_MESSAGES: Final = {
    "BAD_REQUEST": "提交格式不正确，请重新录入。",
    "DOTENV_INVALID": ".env 格式不符合严格 KEY=VALUE 约束。",
    "EXISTING_SECRET": "所选分组已有 Secret；默认禁止覆盖，请重新启动并显式允许替换。",
    "FILESYSTEM_UNSAFE": "配置目录或文件类型不安全，已拒绝写入。",
    "INVALID_DISCOVERY": "Discovery 配置不完整或格式无效。",
    "INVALID_AVISTAZ": "AvistaZ 配置不完整或格式无效。",
    "INVALID_QB": "qBittorrent 配置不完整或格式无效。",
    "SETUP_BUSY": "另一个配置向导正在写入，请稍后重试。",
    "UNSELECTED_PLAINTEXT_SECRET": (".env 含未选择分组的明文凭据；为避免静默删除，已拒绝保存。"),
    "ROLLBACK_FAILED": "配置事务回滚未能完整完成；已停止后续写入，请重新运行向导恢复。",
    "WRITE_FAILED": "配置未能完整保存；未执行任何外部连接。",
}


class SetupError(Exception):
    def __init__(self, code: str, *, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.public_message = _PUBLIC_ERROR_MESSAGES.get(
            code, _PUBLIC_ERROR_MESSAGES["BAD_REQUEST"]
        )


@dataclass(frozen=True)
class StagedFile:
    destination: Path
    temporary: Path
    rollback: Path | None
    existed: bool
    replace: bool
    original_identity: tuple[int, int] | None
    staged_identity: tuple[int, int]


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    if os.name == "nt":
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError:
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)
    return False


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _regular_file_stat(path: Path, *, single_link: bool) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as exc:
        raise SetupError("FILESYSTEM_UNSAFE") from exc
    if (
        _is_link_like(path)
        or not stat.S_ISREG(result.st_mode)
        or (single_link and result.st_nlink != 1)
    ):
        raise SetupError("FILESYSTEM_UNSAFE")
    return result


def _secure_directory(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)


def _secure_file(path: Path) -> None:
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


def _single_line_secret(value: object, *, minimum: int = 1, maximum: int = 8192) -> str:
    if not isinstance(value, str):
        raise ValueError
    if value != value.strip() or not minimum <= len(value) <= maximum:
        raise ValueError
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError
    return value


def _dotenv_value(value: object, *, required: bool, maximum: int, comma_safe: bool) -> str:
    if not isinstance(value, str):
        raise ValueError
    if value != value.strip() or len(value) > maximum:
        raise ValueError
    if required and not value:
        raise ValueError
    forbidden = {"\r", "\n", "\x00", "$", "#"}
    if not comma_safe:
        forbidden.add(",")
    if any(character in value for character in forbidden):
        raise ValueError
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError
    return value


def _object_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError
    return cast(Mapping[str, object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError


def _validate_qb_url(value: str, *, allow_insecure_http: bool) -> tuple[str, str, bool]:
    if len(value) > 2048 or any(character.isspace() for character in value):
        raise ValueError
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError from exc
    scheme = parsed.scheme
    host = parsed.hostname
    if (
        scheme not in {"https", "http"}
        or not parsed.netloc
        or not host
        or not host.isascii()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(character) < 32 or ord(character) == 127 for character in parsed.path)
    ):
        raise ValueError
    if port is not None and not 1 <= port <= 65535:
        raise ValueError
    insecure = scheme == "http"
    if insecure != allow_insecure_http:
        raise ValueError
    return value, host.casefold(), insecure


def _rewrite_dotenv(text: str, updates: Mapping[str, str]) -> bytes:
    normalized = text.lstrip("\ufeff")
    output: list[str] = []
    seen: set[str] = set()
    for line in normalized.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            output.append(line)
            continue
        match = _STRICT_DOTENV_LINE.fullmatch(line)
        if match is None:
            raise SetupError("DOTENV_INVALID") from None
        name = match.group(1)
        current_value = match.group(2)
        if _DOTENV_INTERPOLATION.search(current_value):
            raise SetupError("DOTENV_INVALID")
        if name.startswith("COMPOSE_") and current_value.strip():
            raise SetupError("DOTENV_INVALID")
        if name in updates:
            output.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            output.append(line)
    for name, value in updates.items():
        if name not in seen:
            output.append(f"{name}={value}")
    return ("\n".join(output) + "\n").encode("utf-8")


def _nonempty_plaintext_credentials(text: str) -> set[str]:
    names: set[str] = set()
    normalized = text.lstrip("\ufeff")
    for line in normalized.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _STRICT_DOTENV_LINE.fullmatch(line)
        if match is None:
            raise SetupError("DOTENV_INVALID")
        name, current_value = match.groups()
        if _DOTENV_INTERPOLATION.search(current_value):
            raise SetupError("DOTENV_INVALID")
        if name.startswith("COMPOSE_") and current_value.strip():
            raise SetupError("DOTENV_INVALID")
        if name in _PLAINTEXT_CREDENTIAL_ENV_NAMES and current_value.strip():
            names.add(name)
    return names


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    handle: BinaryIO | None = None
    descriptor: int | None = None
    try:
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                path,
                flags | no_follow | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            _regular_file_stat(path, single_link=True)
            try:
                descriptor = os.open(path, flags | no_follow)
            except OSError as exc:
                raise SetupError("FILESYSTEM_UNSAFE") from exc
        except OSError as exc:
            raise SetupError("FILESYSTEM_UNSAFE") from exc

        opened = os.fstat(descriptor)
        current = _regular_file_stat(path, single_link=True)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise SetupError("FILESYSTEM_UNSAFE")
        if os.name != "nt":
            getattr(os, "fchmod")(descriptor, 0o600)  # noqa: B009 - Windows typing
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        descriptor = None
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SetupError("SETUP_BUSY", status=HTTPStatus.CONFLICT) from exc
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class LocalSetupWriter:
    def __init__(self, project_root: Path, *, replace_existing: bool = False) -> None:
        if _is_link_like(project_root):
            raise SetupError("FILESYSTEM_UNSAFE")
        resolved_root = project_root.resolve(strict=True)
        if not resolved_root.is_dir() or _is_link_like(resolved_root):
            raise SetupError("FILESYSTEM_UNSAFE")
        self.project_root = resolved_root
        self.replace_existing = replace_existing
        self.secrets_dir = self.project_root / "secrets"
        self.staging_dir = self.secrets_dir / ".setup-staging"
        self.lock_path = self.staging_dir / "setup.lock"
        self.env_path = self.project_root / ".env"
        self.example_path = self.project_root / ".env.example"
        self._prepare_directories()
        with _exclusive_lock(self.lock_path):
            self._recover_staging()
            self._validate_configuration_paths(single_link=True)
            for path in (self.env_path, *self._fixed_secret_paths()):
                if _path_lexists(path):
                    _secure_file(path)

    def _prepare_directories(self) -> None:
        for path in (self.secrets_dir, self.staging_dir):
            if path.exists() and (not path.is_dir() or _is_link_like(path)):
                raise SetupError("FILESYSTEM_UNSAFE")
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
            _secure_directory(path)
        for filename in self.fixed_secret_names():
            target = self.secrets_dir / filename
            if _is_link_like(target) or (target.exists() and not target.is_file()):
                raise SetupError("FILESYSTEM_UNSAFE")
        if _is_link_like(self.env_path) or (self.env_path.exists() and not self.env_path.is_file()):
            raise SetupError("FILESYSTEM_UNSAFE")

    def _validate_configuration_paths(self, *, single_link: bool) -> None:
        for path in (self.env_path, *self._fixed_secret_paths()):
            if _path_lexists(path):
                _regular_file_stat(path, single_link=single_link)

    def state(self) -> dict[str, object]:
        groups: dict[str, object] = {}
        recommended_group: str | None = None
        all_configured = True
        for group in _GROUP_ORDER:
            states: list[bool] = []
            for _, filename in _GROUP_SECRET_FIELDS[group]:
                target = self.secrets_dir / filename
                states.append(target.exists() or _is_link_like(target))
            configured = all(states)
            incomplete = any(states) and not configured
            all_configured = all_configured and configured
            if recommended_group is None and not configured and not incomplete:
                recommended_group = group
            groups[group] = {
                "configured": configured,
                "incomplete": incomplete,
            }
        return {
            "ok": True,
            "replace_existing": self.replace_existing,
            "env_exists": self.env_path.exists() or _is_link_like(self.env_path),
            "recommended_group": recommended_group,
            "all_configured": all_configured,
            "groups": groups,
        }

    def save(self, payload: object) -> dict[str, object]:
        try:
            request = _object_mapping(payload)
            raw_groups = request.get("groups")
            if not isinstance(raw_groups, list) or not raw_groups:
                raise ValueError
            if not all(isinstance(group, str) for group in raw_groups):
                raise ValueError
            selected = tuple(cast(list[str], raw_groups))
            if len(set(selected)) != len(selected) or any(
                group not in _GROUP_ORDER for group in selected
            ):
                raise ValueError
            canonical = tuple(group for group in _GROUP_ORDER if group in selected)
            if selected != canonical:
                raise ValueError
            _exact_keys(request, {"groups", *selected})
        except ValueError as exc:
            raise SetupError("BAD_REQUEST") from exc

        secret_values: dict[Path, str] = {}
        env_updates: dict[str, str] = {name: "false" for name in _NEW_ENV_DISABLED_FLAGS}
        for group in selected:
            group_payload = request[group]
            if group == "discovery":
                values = self._validate_discovery(group_payload)
            elif group == "avistaz":
                values = self._validate_avistaz(group_payload)
            else:
                values, qb_updates = self._validate_qb(group_payload)
                env_updates.update(qb_updates)
            env_updates.update({name: "" for name in _GROUP_PLAINTEXT_CREDENTIAL_ENV_NAMES[group]})
            env_updates[_GROUP_LIVE_FLAGS[group]] = "true"
            for field_name, filename in _GROUP_SECRET_FIELDS[group]:
                secret_values[self.secrets_dir / filename] = values[field_name]

        with _exclusive_lock(self.lock_path):
            self._recover_staging()
            self._validate_destinations(secret_values)
            env_bytes, env_was_missing = self._build_env(selected, env_updates)
            staged = self._stage_all(secret_values, env_bytes, env_was_missing=env_was_missing)
            attempted: list[StagedFile] = []
            try:
                for item in staged:
                    attempted.append(item)
                    self._commit(item)
                _fsync_directory(self.secrets_dir)
                _fsync_directory(self.project_root)
            except OSError:
                self._rollback_and_raise(attempted, staged)

            try:
                self._mark_committed(staged)
            except OSError:
                run_dir = staged[0].temporary.parent
                if not self._committed_marker_is_valid(run_dir):
                    self._rollback_and_raise(attempted, staged)
            self._cleanup_staging(staged, committed=True)

        return {
            "ok": True,
            "groups": list(selected),
            "files": [path.name for path in secret_values],
            "env_created": env_was_missing,
            "external_connections_tested": False,
        }

    def _rollback_and_raise(
        self, attempted: Sequence[StagedFile], staged: Sequence[StagedFile]
    ) -> NoReturn:
        if not self._transaction_items_are_valid(staged) or self._rollback(attempted):
            raise SetupError("ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR) from None
        self._cleanup_staging(staged, committed=False)
        raise SetupError("WRITE_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR) from None

    @staticmethod
    def _validate_discovery(payload: object) -> dict[str, str]:
        expected = {
            field
            for field, _ in _GROUP_SECRET_FIELDS["discovery"]
            if field != "auth_session_signing_key"
        }
        try:
            values = _object_mapping(payload)
            _exact_keys(values, expected)
            result = {name: _single_line_secret(values[name]) for name in expected}
            if len(result["auth_local_username"]) > 120:
                raise ValueError
            result["auth_session_signing_key"] = secrets.token_urlsafe(48)
            return result
        except (KeyError, ValueError) as exc:
            raise SetupError("INVALID_DISCOVERY") from exc

    @staticmethod
    def _validate_avistaz(payload: object) -> dict[str, str]:
        expected = {field for field, _ in _GROUP_SECRET_FIELDS["avistaz"]}
        try:
            values = _object_mapping(payload)
            _exact_keys(values, expected)
            return {name: _single_line_secret(values[name]) for name in expected}
        except (KeyError, ValueError) as exc:
            raise SetupError("INVALID_AVISTAZ") from exc

    @staticmethod
    def _validate_qb(payload: object) -> tuple[dict[str, str], dict[str, str]]:
        secret_fields = {field for field, _ in _GROUP_SECRET_FIELDS["qb"]}
        expected = {
            *secret_fields,
            "qb_target_save_path",
            "qb_target_category",
            "qb_allow_insecure_http",
        }
        try:
            values = _object_mapping(payload)
            _exact_keys(values, expected)
            allow_http = values["qb_allow_insecure_http"]
            if not isinstance(allow_http, bool):
                raise ValueError
            base_url = _single_line_secret(values["qb_base_url"], maximum=2048)
            base_url, host, insecure = _validate_qb_url(base_url, allow_insecure_http=allow_http)
            target_path = _dotenv_value(
                values["qb_target_save_path"], required=True, maximum=2048, comma_safe=False
            )
            category = _dotenv_value(
                values["qb_target_category"], required=False, maximum=180, comma_safe=True
            )
            secrets_result = {
                "qb_base_url": base_url,
                "qb_username": _single_line_secret(values["qb_username"]),
                "qb_password": _single_line_secret(values["qb_password"]),
            }
            env_updates = {
                "QB_ALLOWED_HOSTS": host,
                "QB_ALLOW_INSECURE_HTTP": "true" if insecure else "false",
                "QB_TARGET_SAVE_PATH": target_path,
                "QB_ALLOWED_SAVE_PATHS": target_path,
                "QB_TARGET_CATEGORY": category,
            }
            return secrets_result, env_updates
        except (KeyError, ValueError) as exc:
            raise SetupError("INVALID_QB") from exc

    def _validate_destinations(self, values: Mapping[Path, str]) -> None:
        for target in values:
            if target.parent != self.secrets_dir or target.name not in self.fixed_secret_names():
                raise SetupError("FILESYSTEM_UNSAFE")
            if _path_lexists(target):
                _regular_file_stat(target, single_link=True)
            if _path_lexists(target) and not self.replace_existing:
                raise SetupError("EXISTING_SECRET", status=HTTPStatus.CONFLICT)
        for path in (self.env_path, self.example_path):
            if _path_lexists(path):
                _regular_file_stat(path, single_link=True)

    def _build_env(self, selected: tuple[str, ...], updates: dict[str, str]) -> tuple[bytes, bool]:
        env_was_missing = not self.env_path.exists()
        source = self.example_path if env_was_missing else self.env_path
        try:
            if not source.is_file() or source.stat().st_size > MAX_DOTENV_BYTES:
                raise SetupError("FILESYSTEM_UNSAFE")
            text = source.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise SetupError("DOTENV_INVALID") from exc
        except OSError as exc:
            raise SetupError("FILESYSTEM_UNSAFE") from exc

        if env_was_missing:
            postgres_password = secrets.token_hex(32)
            updates.update(
                {
                    **{name: "" for name in _PLAINTEXT_CREDENTIAL_ENV_NAMES},
                    "POSTGRES_PASSWORD": postgres_password,
                    "DATABASE_URL": (
                        f"postgresql+psycopg://unin:{postgres_password}@postgres:5432/unin"
                    ),
                    **{name: "false" for name in _NEW_ENV_DISABLED_FLAGS},
                    **{
                        flag: "true" if group in selected else "false"
                        for group, flag in _GROUP_LIVE_FLAGS.items()
                    },
                }
            )
        else:
            selected_plaintext_names = {
                name for group in selected for name in _GROUP_PLAINTEXT_CREDENTIAL_ENV_NAMES[group]
            }
            unexpected_plaintext_names = (
                _nonempty_plaintext_credentials(text) - selected_plaintext_names
            )
            if unexpected_plaintext_names:
                raise SetupError("UNSELECTED_PLAINTEXT_SECRET", status=HTTPStatus.CONFLICT)
        return _rewrite_dotenv(text, updates), env_was_missing

    def _stage_all(
        self,
        secret_values: Mapping[Path, str],
        env_bytes: bytes,
        *,
        env_was_missing: bool,
    ) -> list[StagedFile]:
        run_dir = self.staging_dir / secrets.token_hex(16)
        run_dir.mkdir(mode=0o700)
        _secure_directory(run_dir)
        staged: list[StagedFile] = []
        try:
            self._write_preparing_marker(run_dir)
            for destination, value in secret_values.items():
                staged.append(
                    self._stage_file(
                        run_dir,
                        destination,
                        value.encode("utf-8"),
                        expected_existed=_path_lexists(destination),
                    )
                )
            staged.append(
                self._stage_file(
                    run_dir,
                    self.env_path,
                    env_bytes,
                    expected_existed=not env_was_missing,
                )
            )
            self._write_manifest(run_dir, staged)
            return staged
        except SetupError:
            self._cleanup_staging(staged, run_dir=run_dir)
            raise
        except OSError:
            self._cleanup_staging(staged, run_dir=run_dir)
            raise SetupError("WRITE_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR) from None

    @staticmethod
    def _preparing_marker_content(run_dir: Path) -> bytes:
        return f"UNIN-SETUP-PREPARING-v1:{run_dir.name}\n".encode("ascii")

    @classmethod
    def _write_preparing_marker(cls, run_dir: Path) -> None:
        temporary = run_dir / TRANSACTION_PREPARING_TEMP_NAME
        marker = run_dir / TRANSACTION_PREPARING_NAME
        try:
            with temporary.open("xb") as handle:
                _secure_file(temporary)
                handle.write(cls._preparing_marker_content(run_dir))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, marker)
            _secure_file(marker)
            _fsync_directory(run_dir)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise

    @classmethod
    def _preparing_marker_is_valid(cls, run_dir: Path) -> bool:
        marker = run_dir / TRANSACTION_PREPARING_NAME
        try:
            marker_stat = _regular_file_stat(marker, single_link=True)
            expected = cls._preparing_marker_content(run_dir)
            return marker_stat.st_size == len(expected) and hmac.compare_digest(
                marker.read_bytes(), expected
            )
        except (OSError, SetupError):
            return False

    @staticmethod
    def _stage_file(
        run_dir: Path,
        destination: Path,
        content: bytes,
        *,
        expected_existed: bool,
    ) -> StagedFile:
        temporary = run_dir / f"{secrets.token_hex(16)}.tmp"
        rollback: Path | None = None
        original_identity: tuple[int, int] | None = None
        staged_identity: tuple[int, int] | None = None
        try:
            if expected_existed:
                before = _regular_file_stat(destination, single_link=True)
                original_identity = (before.st_dev, before.st_ino)
                rollback = run_dir / f"{secrets.token_hex(16)}.rollback"
                os.link(destination, rollback, follow_symlinks=False)
                source_after = _regular_file_stat(destination, single_link=False)
                rollback_stat = _regular_file_stat(rollback, single_link=False)
                if (
                    (source_after.st_dev, source_after.st_ino) != original_identity
                    or (rollback_stat.st_dev, rollback_stat.st_ino) != original_identity
                    or source_after.st_nlink != 2
                    or rollback_stat.st_nlink != 2
                ):
                    raise SetupError("FILESYSTEM_UNSAFE")
            elif _path_lexists(destination):
                raise SetupError("FILESYSTEM_UNSAFE")

            with temporary.open("xb") as handle:
                _secure_file(temporary)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_stat = _regular_file_stat(temporary, single_link=True)
            staged_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
        except (OSError, SetupError):
            with contextlib.suppress(OSError):
                temporary.unlink()
            if rollback is not None:
                with contextlib.suppress(OSError):
                    rollback.unlink()
            raise
        if staged_identity is None:
            raise SetupError("FILESYSTEM_UNSAFE")
        return StagedFile(
            destination=destination,
            temporary=temporary,
            rollback=rollback,
            existed=expected_existed,
            replace=expected_existed,
            original_identity=original_identity,
            staged_identity=staged_identity,
        )

    def _write_manifest(self, run_dir: Path, staged: Sequence[StagedFile]) -> None:
        items: list[dict[str, object]] = []
        for item in staged:
            if item.destination == self.env_path:
                destination_kind = "env"
                destination_name = ".env"
            elif (
                item.destination.parent == self.secrets_dir
                and item.destination.name in self.fixed_secret_names()
            ):
                destination_kind = "secret"
                destination_name = item.destination.name
            else:
                raise SetupError("FILESYSTEM_UNSAFE")
            items.append(
                {
                    "destination_kind": destination_kind,
                    "destination_name": destination_name,
                    "temporary": item.temporary.name,
                    "rollback": item.rollback.name if item.rollback is not None else None,
                    "existed": item.existed,
                    "original_device": (
                        item.original_identity[0] if item.original_identity is not None else None
                    ),
                    "original_inode": (
                        item.original_identity[1] if item.original_identity is not None else None
                    ),
                    "staged_device": item.staged_identity[0],
                    "staged_inode": item.staged_identity[1],
                }
            )
        content = json.dumps(
            {"version": 1, "items": items},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        temporary = run_dir / f"{TRANSACTION_MANIFEST_NAME}.tmp"
        manifest = run_dir / TRANSACTION_MANIFEST_NAME
        try:
            with temporary.open("xb") as handle:
                _secure_file(temporary)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest)
            _secure_file(manifest)
            _fsync_directory(run_dir)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise

    @staticmethod
    def _commit(item: StagedFile) -> None:
        if item.replace:
            os.replace(item.temporary, item.destination)
        else:
            os.link(item.temporary, item.destination, follow_symlinks=False)
            item.temporary.unlink()
        _secure_file(item.destination)

    @classmethod
    def _mark_committed(cls, staged: Sequence[StagedFile]) -> None:
        if not staged:
            raise OSError
        run_dir = staged[0].temporary.parent
        temporary = run_dir / TRANSACTION_COMMITTED_TEMP_NAME
        marker = run_dir / TRANSACTION_COMMITTED_NAME
        digest = cls._manifest_digest(run_dir)
        try:
            with temporary.open("xb") as handle:
                _secure_file(temporary)
                handle.write(digest)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(run_dir)
            os.replace(temporary, marker)
        except OSError:
            if cls._committed_marker_is_valid(run_dir):
                return
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
        # Once the atomic rename makes COMMITTED visible, no later failure may
        # send the transaction down the rollback path.
        with contextlib.suppress(OSError):
            _fsync_directory(run_dir)

    @staticmethod
    def _manifest_digest(run_dir: Path) -> bytes:
        manifest = run_dir / TRANSACTION_MANIFEST_NAME
        manifest_stat = _regular_file_stat(manifest, single_link=True)
        if not 1 <= manifest_stat.st_size <= MAX_TRANSACTION_MANIFEST_BYTES:
            raise OSError
        try:
            content = manifest.read_bytes()
        except OSError:
            raise
        return hashlib.sha256(content).hexdigest().encode("ascii")

    @classmethod
    def _committed_marker_is_valid(cls, run_dir: Path) -> bool:
        marker = run_dir / TRANSACTION_COMMITTED_NAME
        try:
            marker_stat = _regular_file_stat(marker, single_link=True)
            if marker_stat.st_size != 64:
                return False
            return hmac.compare_digest(marker.read_bytes(), cls._manifest_digest(run_dir))
        except (OSError, SetupError):
            return False

    @staticmethod
    def _rollback(staged: Sequence[StagedFile]) -> bool:
        if not LocalSetupWriter._transaction_items_are_valid(staged):
            return True
        failed = False
        for item in reversed(staged):
            try:
                if item.existed:
                    if item.original_identity is None:
                        raise OSError
                    destination_stat = _regular_file_stat(item.destination, single_link=False)
                    destination_identity = (
                        destination_stat.st_dev,
                        destination_stat.st_ino,
                    )
                    if destination_identity == item.staged_identity:
                        if item.rollback is None or not _path_lexists(item.rollback):
                            raise OSError
                        os.replace(item.rollback, item.destination)
                        _secure_file(item.destination)
                    elif destination_identity != item.original_identity:
                        raise OSError
                elif _path_lexists(item.destination):
                    destination_stat = _regular_file_stat(item.destination, single_link=False)
                    if (
                        destination_stat.st_dev,
                        destination_stat.st_ino,
                    ) != item.staged_identity:
                        raise OSError
                    item.destination.unlink()
            except (OSError, SetupError):
                failed = True
        try:
            if staged:
                _fsync_directory(staged[0].temporary.parent)
                secret_parent = next(
                    (item.destination.parent for item in staged if item.destination.name != ".env"),
                    None,
                )
                if secret_parent is not None:
                    _fsync_directory(secret_parent)
                env_item = next(
                    (item for item in staged if item.destination.name == ".env"),
                    None,
                )
                if env_item is not None:
                    _fsync_directory(env_item.destination.parent)
        except OSError:
            failed = True
        return failed

    @staticmethod
    def _transaction_items_are_valid(staged: Sequence[StagedFile]) -> bool:
        for item in staged:
            try:
                temporary_stat: os.stat_result | None = None
                if _path_lexists(item.temporary):
                    temporary_stat = _regular_file_stat(item.temporary, single_link=False)
                    if (
                        temporary_stat.st_dev,
                        temporary_stat.st_ino,
                    ) != item.staged_identity:
                        return False

                rollback_stat: os.stat_result | None = None
                if item.rollback is not None and _path_lexists(item.rollback):
                    rollback_stat = _regular_file_stat(item.rollback, single_link=False)
                    if (
                        item.original_identity is None
                        or (
                            rollback_stat.st_dev,
                            rollback_stat.st_ino,
                        )
                        != item.original_identity
                    ):
                        return False

                destination_stat: os.stat_result | None = None
                if _path_lexists(item.destination):
                    destination_stat = _regular_file_stat(item.destination, single_link=False)
                    destination_identity = (
                        destination_stat.st_dev,
                        destination_stat.st_ino,
                    )
                    allowed = {item.staged_identity}
                    if item.original_identity is not None:
                        allowed.add(item.original_identity)
                    if destination_identity not in allowed:
                        return False
                elif item.existed:
                    return False

                staged_links = int(temporary_stat is not None) + int(
                    destination_stat is not None
                    and (destination_stat.st_dev, destination_stat.st_ino) == item.staged_identity
                )
                if temporary_stat is not None and temporary_stat.st_nlink != staged_links:
                    return False
                if (
                    destination_stat is not None
                    and (destination_stat.st_dev, destination_stat.st_ino) == item.staged_identity
                    and destination_stat.st_nlink != staged_links
                ):
                    return False

                if item.original_identity is not None:
                    original_links = int(rollback_stat is not None) + int(
                        destination_stat is not None
                        and (destination_stat.st_dev, destination_stat.st_ino)
                        == item.original_identity
                    )
                    if rollback_stat is not None and rollback_stat.st_nlink != original_links:
                        return False
                    if (
                        destination_stat is not None
                        and (destination_stat.st_dev, destination_stat.st_ino)
                        == item.original_identity
                        and destination_stat.st_nlink != original_links
                    ):
                        return False
            except (OSError, SetupError):
                return False
        return True

    @staticmethod
    def _artifact_matches(path: Path, identity: tuple[int, int]) -> bool:
        try:
            result = _regular_file_stat(path, single_link=False)
        except SetupError:
            return False
        return (result.st_dev, result.st_ino) == identity

    @classmethod
    def _cleanup_staging(
        cls,
        staged: Sequence[StagedFile],
        *,
        run_dir: Path | None = None,
        committed: bool = False,
    ) -> bool:
        directory = run_dir
        if directory is None and staged:
            directory = staged[0].temporary.parent
        if directory is None:
            return True
        if not cls._transaction_items_are_valid(staged):
            return False
        if not cls._run_directory_entries_are_known(directory, staged, committed=committed):
            return False

        for item in staged:
            if _path_lexists(item.temporary):
                if not cls._artifact_matches(item.temporary, item.staged_identity):
                    return False
                try:
                    item.temporary.unlink()
                except OSError:
                    return False
            if item.rollback is not None and _path_lexists(item.rollback):
                if item.original_identity is None or not cls._artifact_matches(
                    item.rollback, item.original_identity
                ):
                    return False
                try:
                    item.rollback.unlink()
                except OSError:
                    return False

        for name in (
            f"{TRANSACTION_MANIFEST_NAME}.tmp",
            TRANSACTION_COMMITTED_TEMP_NAME,
        ):
            path = directory / name
            if _path_lexists(path):
                try:
                    _regular_file_stat(path, single_link=True)
                    path.unlink()
                except (OSError, SetupError):
                    return False

        manifest = directory / TRANSACTION_MANIFEST_NAME
        if _path_lexists(manifest):
            try:
                _regular_file_stat(manifest, single_link=True)
                manifest.unlink()
            except (OSError, SetupError):
                return False

        # COMMITTED is deliberately the final artifact removed. Any earlier
        # failure returns while the marker and manifest are still available.
        marker = directory / TRANSACTION_COMMITTED_NAME
        if _path_lexists(marker):
            try:
                _regular_file_stat(marker, single_link=True)
                marker.unlink()
            except (OSError, SetupError):
                return False
        for name in (TRANSACTION_PREPARING_TEMP_NAME, TRANSACTION_PREPARING_NAME):
            path = directory / name
            if _path_lexists(path):
                try:
                    _regular_file_stat(path, single_link=True)
                    path.unlink()
                except (OSError, SetupError):
                    return False
        try:
            directory.rmdir()
            _fsync_directory(directory.parent)
        except OSError:
            return False
        return True

    @classmethod
    def _run_directory_entries_are_known(
        cls,
        run_dir: Path,
        staged: Sequence[StagedFile],
        *,
        committed: bool,
    ) -> bool:
        allowed = {
            TRANSACTION_MANIFEST_NAME,
            f"{TRANSACTION_MANIFEST_NAME}.tmp",
            TRANSACTION_COMMITTED_TEMP_NAME,
            TRANSACTION_PREPARING_NAME,
            TRANSACTION_PREPARING_TEMP_NAME,
            *(item.temporary.name for item in staged),
            *(item.rollback.name for item in staged if item.rollback is not None),
        }
        if not cls._preparing_marker_is_valid(run_dir):
            return False
        if committed:
            allowed.add(TRANSACTION_COMMITTED_NAME)
            if not cls._committed_marker_is_valid(
                run_dir
            ) or not cls._destinations_match_staged_identity(staged):
                return False
        elif _path_lexists(run_dir / TRANSACTION_COMMITTED_NAME):
            return False
        try:
            for child in tuple(run_dir.iterdir()):
                if child.name not in allowed or _is_link_like(child) or child.is_dir():
                    return False
                _regular_file_stat(child, single_link=False)
        except (OSError, SetupError):
            return False
        return True

    @classmethod
    def _recover_run_without_manifest(cls, run_dir: Path) -> bool:
        """Recover only runs carrying our durable PREPARING marker.

        Unknown non-empty directories are preserved untouched and ignored. An
        empty directory is safe to remove. A valid PREPARING marker proves that
        patterned staging artifacts belong to an interrupted setup run.
        """
        try:
            children = tuple(run_dir.iterdir())
        except OSError:
            return False
        if not children:
            try:
                run_dir.rmdir()
                _fsync_directory(run_dir.parent)
            except OSError:
                return False
            return True
        if not cls._preparing_marker_is_valid(run_dir):
            return False

        committed = run_dir / TRANSACTION_COMMITTED_NAME
        has_committed = _path_lexists(committed)
        allowed_fixed = {
            TRANSACTION_PREPARING_NAME,
            TRANSACTION_PREPARING_TEMP_NAME,
            f"{TRANSACTION_MANIFEST_NAME}.tmp",
            TRANSACTION_COMMITTED_TEMP_NAME,
        }
        if has_committed:
            allowed_fixed.add(TRANSACTION_COMMITTED_NAME)
        try:
            for child in children:
                if _is_link_like(child) or child.is_dir():
                    return False
                result = _regular_file_stat(child, single_link=False)
                if child.name in allowed_fixed:
                    if child.name == TRANSACTION_COMMITTED_NAME:
                        content = child.read_bytes()
                        if result.st_nlink != 1 or re.fullmatch(rb"[0-9a-f]{64}", content) is None:
                            return False
                    elif (
                        child.name
                        in {
                            TRANSACTION_PREPARING_NAME,
                            TRANSACTION_PREPARING_TEMP_NAME,
                            f"{TRANSACTION_MANIFEST_NAME}.tmp",
                            TRANSACTION_COMMITTED_TEMP_NAME,
                        }
                        and result.st_nlink != 1
                    ):
                        return False
                    continue
                if has_committed or _STAGED_ARTIFACT_NAME.fullmatch(child.name) is None:
                    return False
                if child.name.endswith(".tmp") and result.st_nlink != 1:
                    return False

            for child in children:
                child.unlink()
            run_dir.rmdir()
            _fsync_directory(run_dir.parent)
        except (OSError, SetupError):
            return False
        return True

    @staticmethod
    def _destinations_match_staged_identity(staged: Sequence[StagedFile]) -> bool:
        try:
            for item in staged:
                result = _regular_file_stat(item.destination, single_link=False)
                if (result.st_dev, result.st_ino) != item.staged_identity:
                    return False
        except (OSError, SetupError):
            return False
        return True

    def _fixed_secret_paths(self) -> tuple[Path, ...]:
        return tuple(self.secrets_dir / name for name in self.fixed_secret_names())

    def _recover_staging(self) -> None:
        try:
            entries = tuple(self.staging_dir.iterdir())
        except OSError as exc:
            raise SetupError("FILESYSTEM_UNSAFE") from exc
        for entry in entries:
            if entry.name == self.lock_path.name:
                continue
            if entry.name == WINDOWS_ACL_MARKER_NAME:
                _regular_file_stat(entry, single_link=True)
                continue
            if (
                _RUN_DIRECTORY_NAME.fullmatch(entry.name) is None
                or _is_link_like(entry)
                or not entry.is_dir()
            ):
                raise SetupError("FILESYSTEM_UNSAFE")

            committed = entry / TRANSACTION_COMMITTED_NAME
            manifest = entry / TRANSACTION_MANIFEST_NAME
            if not _path_lexists(manifest):
                if not self._recover_run_without_manifest(entry):
                    raise SetupError("ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR)
                continue
            try:
                staged = self._load_manifest(entry)
            except (OSError, SetupError, UnicodeError, ValueError, json.JSONDecodeError):
                raise SetupError(
                    "ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR
                ) from None

            if _path_lexists(committed):
                if not self._run_directory_entries_are_known(
                    entry, staged, committed=True
                ) or not self._cleanup_staging(staged, run_dir=entry, committed=True):
                    raise SetupError("ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR)
                continue
            if not self._run_directory_entries_are_known(entry, staged, committed=False):
                raise SetupError("ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            if self._rollback(staged):
                raise SetupError("ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            if not self._cleanup_staging(staged, run_dir=entry, committed=False):
                raise SetupError("ROLLBACK_FAILED", status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _load_manifest(self, run_dir: Path) -> list[StagedFile]:
        manifest = run_dir / TRANSACTION_MANIFEST_NAME
        manifest_stat = _regular_file_stat(manifest, single_link=True)
        if manifest_stat.st_size > MAX_TRANSACTION_MANIFEST_BYTES:
            raise SetupError("FILESYSTEM_UNSAFE")
        raw = json.loads(
            manifest.read_text(encoding="ascii"),
            object_pairs_hook=_unique_json_object,
        )
        data = _object_mapping(raw)
        _exact_keys(data, {"version", "items"})
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError
        raw_items = data["items"]
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 13:
            raise ValueError

        staged: list[StagedFile] = []
        destinations: set[Path] = set()
        artifact_names: set[str] = set()
        expected_keys = {
            "destination_kind",
            "destination_name",
            "temporary",
            "rollback",
            "existed",
            "original_device",
            "original_inode",
            "staged_device",
            "staged_inode",
        }
        for raw_item in raw_items:
            item = _object_mapping(raw_item)
            _exact_keys(item, expected_keys)
            kind = item["destination_kind"]
            destination_name = item["destination_name"]
            temporary_name = item["temporary"]
            rollback_name = item["rollback"]
            existed = item["existed"]
            original_device = item["original_device"]
            original_inode = item["original_inode"]
            staged_device = item["staged_device"]
            staged_inode = item["staged_inode"]
            if (
                not isinstance(kind, str)
                or not isinstance(destination_name, str)
                or not isinstance(temporary_name, str)
                or type(existed) is not bool
                or _STAGED_ARTIFACT_NAME.fullmatch(temporary_name) is None
                or not temporary_name.endswith(".tmp")
                or type(staged_device) is not int
                or type(staged_inode) is not int
                or staged_device < 0
                or staged_inode < 0
            ):
                raise ValueError
            if kind == "env" and destination_name == ".env":
                destination = self.env_path
            elif kind == "secret" and destination_name in self.fixed_secret_names():
                destination = self.secrets_dir / destination_name
            else:
                raise ValueError
            if destination in destinations or temporary_name in artifact_names:
                raise ValueError
            destinations.add(destination)
            artifact_names.add(temporary_name)

            if existed:
                if (
                    not isinstance(rollback_name, str)
                    or _STAGED_ARTIFACT_NAME.fullmatch(rollback_name) is None
                    or not rollback_name.endswith(".rollback")
                    or type(original_device) is not int
                    or type(original_inode) is not int
                    or original_device < 0
                    or original_inode < 0
                    or rollback_name in artifact_names
                ):
                    raise ValueError
                artifact_names.add(rollback_name)
                rollback = run_dir / rollback_name
                original_identity = (original_device, original_inode)
            else:
                if (
                    rollback_name is not None
                    or original_device is not None
                    or original_inode is not None
                ):
                    raise ValueError
                rollback = None
                original_identity = None
            staged.append(
                StagedFile(
                    destination=destination,
                    temporary=run_dir / temporary_name,
                    rollback=rollback,
                    existed=existed,
                    replace=existed,
                    original_identity=original_identity,
                    staged_identity=(staged_device, staged_inode),
                )
            )
        return staged

    @staticmethod
    def fixed_secret_names() -> frozenset[str]:
        return frozenset(
            filename for group in _GROUP_ORDER for _, filename in _GROUP_SECRET_FIELDS[group]
        )


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UNIN 本机配置向导</title>
  <style nonce="__NONCE__">
    :root { color-scheme: light; font-family: system-ui, sans-serif; background: #f3f5f4; color: #20251f; }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 20px; }
    main { max-width: 760px; margin: 0 auto; }
    h1 { margin-bottom: 8px; }
    .notice, fieldset { border: 1px solid #d5d1c8; border-radius: 8px; background: #fff; padding: 16px; margin: 16px 0; min-width: 0; }
    .notice { color: #485046; border-left: 4px solid #338553; }
    fieldset[disabled] { opacity: .55; }
    label { display: block; margin: 12px 0 5px; }
    input[type=text], input[type=password], input[type=url] { width: 100%; max-width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #aaa99f; background: #fff; color: #20251f; }
    .selector { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; font-weight: 650; }
    .status { color: #61685f; font-size: .9rem; margin-top: 8px; }
    .danger { color: #9a4f00; background: #fff5e5; border: 1px solid #efb35f; border-radius: 6px; padding: 10px; }
    button { max-width: 100%; padding: 11px 18px; border: 0; border-radius: 6px; background: #237a45; color: white; font-weight: 650; cursor: pointer; }
    button:disabled { opacity: .5; cursor: wait; }
    #message { min-height: 1.5em; margin-top: 14px; white-space: pre-wrap; }
    @media (max-width: 360px) { body { padding: 12px; } .notice, fieldset { padding: 12px; } button { width: 100%; } }
  </style>
</head>
<body>
<main>
  <h1>UNIN 本机配置向导</h1>
  <div class="notice">只保存本机配置，不测试或连接 NextFind、TMDB、AvistaZ、qBittorrent，也不启动 Docker。Secret 不会从已有文件读取或预填。</div>
  <form id="setup-form" autocomplete="off">
    <fieldset id="discovery-fields">
      <legend><label class="selector"><input id="select-discovery" type="checkbox" checked>步骤 1：本地账号与影视来源</label></legend>
      <div class="status" id="status-discovery">正在读取存在状态…</div>
      <label for="auth-local-username">UNIN 本地用户名</label>
      <input id="auth-local-username" type="text" autocomplete="off" maxlength="120">
      <label for="auth-local-password">UNIN 本地密码</label>
      <input id="auth-local-password" class="secret-input" type="password" autocomplete="new-password" maxlength="8192">
      <label for="nextfind-username">NextFind 用户名</label>
      <input id="nextfind-username" type="text" autocomplete="off" maxlength="8192">
      <label for="nextfind-password">NextFind 密码</label>
      <input id="nextfind-password" class="secret-input" type="password" autocomplete="new-password" maxlength="8192">
      <label for="tmdb-token">TMDB 访问令牌</label>
      <input id="tmdb-token" class="secret-input" type="password" autocomplete="new-password" maxlength="8192">
    </fieldset>

    <fieldset id="avistaz-fields" disabled>
      <legend><label class="selector"><input id="select-avistaz" type="checkbox">步骤 2：AvistaZ</label></legend>
      <div class="status" id="status-avistaz">未选择，不会创建文件。</div>
      <label for="avistaz-username">AvistaZ 用户名</label>
      <input id="avistaz-username" type="text" autocomplete="off" maxlength="8192">
      <label for="avistaz-password">AvistaZ 密码</label>
      <input id="avistaz-password" class="secret-input" type="password" autocomplete="new-password" maxlength="8192">
      <label for="avistaz-pid">AvistaZ PID</label>
      <input id="avistaz-pid" class="secret-input" type="password" autocomplete="new-password" maxlength="8192">
    </fieldset>

    <fieldset id="qb-fields" disabled>
      <legend><label class="selector"><input id="select-qb" type="checkbox">步骤 3：qBittorrent 只读连接</label></legend>
      <div class="status" id="status-qb">未选择，不会创建文件。</div>
      <label for="qb-url">qBittorrent Web 地址</label>
      <input id="qb-url" type="url" autocomplete="off" maxlength="2048" placeholder="https://qb.example.internal">
      <label for="qb-username">qBittorrent 用户名</label>
      <input id="qb-username" type="text" autocomplete="off" maxlength="8192">
      <label for="qb-password">qBittorrent 密码</label>
      <input id="qb-password" class="secret-input" type="password" autocomplete="new-password" maxlength="8192">
      <label for="qb-save-path">qBittorrent 下载保存路径</label>
      <input id="qb-save-path" type="text" autocomplete="off" maxlength="2048">
      <label for="qb-category">qBittorrent 分类（可选）</label>
      <input id="qb-category" type="text" autocomplete="off" maxlength="180">
      <label class="selector danger"><input id="qb-allow-http" type="checkbox">我明确接受受信内网 HTTP 明文传输风险</label>
    </fieldset>

    <button id="save-button" type="submit">保存本机配置</button>
    <div id="message" role="status" aria-live="polite"></div>
  </form>
</main>
<script nonce="__NONCE__">
(() => {
  'use strict';
  const token = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '';
  history.replaceState(null, '', '/');
  const message = document.getElementById('message');
  const form = document.getElementById('setup-form');
  const saveButton = document.getElementById('save-button');
  const groupNames = ['discovery', 'avistaz', 'qb'];
  const selected = name => document.getElementById(`select-${name}`).checked;
  const value = id => document.getElementById(id).value;
  const setEnabled = name => {
    const box = document.getElementById(`select-${name}`);
    const fieldset = document.getElementById(`${name}-fields`);
    for (const control of fieldset.querySelectorAll('input')) {
      if (control !== box) control.disabled = !box.checked;
    }
    fieldset.disabled = false;
    fieldset.classList.toggle('inactive', !box.checked);
  };
  const api = async (path, body) => {
    const response = await fetch(path, {
      method: 'POST',
      cache: 'no-store',
      credentials: 'omit',
      headers: {'Content-Type': 'application/json', 'X-UNIN-Setup-Token': token},
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || '请求被拒绝');
    return data;
  };
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(token)) {
    form.replaceChildren(message);
    message.textContent = '启动 token 无效，请关闭页面并重新运行 setup.ps1。';
    return;
  }
  for (const name of groupNames) {
    document.getElementById(`select-${name}`).addEventListener('change', () => setEnabled(name));
    setEnabled(name);
  }
  api('/api/state', {}).then(state => {
    for (const name of groupNames) {
      const group = state.groups[name];
      const status = group.configured ? '已录入' : (group.incomplete ? '录入不完整' : '未录入');
      const suffix = state.replace_existing ? '本次已显式允许替换。' : '默认禁止覆盖。';
      document.getElementById(`status-${name}`).textContent = `${status}；${suffix}`;
      const selector = document.getElementById(`select-${name}`);
      if (!state.replace_existing && (group.configured || group.incomplete)) {
        selector.checked = false;
        selector.disabled = true;
      } else if (!state.replace_existing) {
        selector.checked = name === state.recommended_group;
      }
      setEnabled(name);
    }
    if (!state.replace_existing && state.all_configured) {
      message.textContent = '三个分组均已录入；如需替换，请关闭页面并使用 setup.cmd -ReplaceExisting。';
      saveButton.disabled = true;
    } else if (!state.replace_existing && !state.recommended_group) {
      message.textContent = '存在录入不完整的分组；请关闭页面并使用 setup.cmd -ReplaceExisting。';
      saveButton.disabled = true;
    }
  }).catch(error => { message.textContent = error.message; });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const groups = groupNames.filter(selected);
    if (!groups.length) { message.textContent = '请至少选择一个配置分组。'; return; }
    const payload = {groups};
    if (selected('discovery')) payload.discovery = {
      auth_local_username: value('auth-local-username'),
      auth_local_password: value('auth-local-password'),
      nextfind_username: value('nextfind-username'),
      nextfind_password: value('nextfind-password'),
      tmdb_access_token: value('tmdb-token'),
    };
    if (selected('avistaz')) payload.avistaz = {
      avistaz_username: value('avistaz-username'),
      avistaz_password: value('avistaz-password'),
      avistaz_pid: value('avistaz-pid'),
    };
    if (selected('qb')) payload.qb = {
      qb_base_url: value('qb-url'),
      qb_username: value('qb-username'),
      qb_password: value('qb-password'),
      qb_target_save_path: value('qb-save-path'),
      qb_target_category: value('qb-category'),
      qb_allow_insecure_http: document.getElementById('qb-allow-http').checked,
    };
    for (const input of document.querySelectorAll('.secret-input')) input.value = '';
    saveButton.disabled = true;
    message.textContent = '正在原子保存；不会测试外部连接…';
    try {
      const result = await api('/api/save', payload);
      form.replaceChildren(message);
      message.textContent = `已保存 ${result.groups.join('、')}；未测试任何外部连接。可以关闭此页面。`;
    } catch (error) {
      message.textContent = `${error.message} 密码字段已清空，请重新输入。`;
      saveButton.disabled = false;
    }
  });
})();
</script>
</body>
</html>
"""


class WizardRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "UNINLocalSetup"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def log_message(self, format: str, *args: object) -> None:
        return

    @property
    def wizard_server(self) -> WizardHTTPServer:
        return cast(WizardHTTPServer, self.server)

    def _single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name, [])
        if len(values) != 1:
            return None
        return values[0]

    def _host_is_valid(self) -> bool:
        return self._single_header("Host") == self.wizard_server.expected_host

    def _api_headers_are_valid(self) -> bool:
        if not self._host_is_valid():
            return False
        if self._single_header("Origin") != self.wizard_server.origin:
            return False
        supplied = self._single_header(TOKEN_HEADER)
        return supplied is not None and hmac.compare_digest(
            supplied, self.wizard_server.setup_token
        )

    def _security_headers(self, *, csp_nonce: str | None = None) -> None:
        if csp_nonce is None:
            csp = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        else:
            csp = (
                "default-src 'none'; "
                f"script-src 'nonce-{csp_nonce}'; style-src 'nonce-{csp_nonce}'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'none'; img-src 'none'"
            )
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        self.send_header("Connection", "close")

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        csp_nonce: str | None = None,
    ) -> None:
        self.send_response(status)
        self._security_headers(csp_nonce=csp_nonce)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _reject(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"ok": False, "error_code": code, "message": message})

    def _discard_bounded_request_body(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            return
        length = min(int(lengths[0]), MAX_REQUEST_BODY_BYTES + 1)
        if length > 0:
            self.rfile.read(length)

    def _read_json(self) -> object:
        if self._single_header("Content-Type") != "application/json":
            self._discard_bounded_request_body()
            raise SetupError("BAD_REQUEST", status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        if self.headers.get_all("Transfer-Encoding", []):
            raise SetupError("BAD_REQUEST", status=HTTPStatus.BAD_REQUEST)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise SetupError("BAD_REQUEST", status=HTTPStatus.LENGTH_REQUIRED)
        length = int(lengths[0])
        if length <= 0 or length > MAX_REQUEST_BODY_BYTES:
            status = (
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if length > MAX_REQUEST_BODY_BYTES
                else HTTPStatus.BAD_REQUEST
            )
            if length > MAX_REQUEST_BODY_BYTES:
                self.rfile.read(MAX_REQUEST_BODY_BYTES + 1)
            raise SetupError("BAD_REQUEST", status=status)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise SetupError("BAD_REQUEST")
        try:
            return json.loads(
                raw,
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SetupError("BAD_REQUEST") from exc

    def do_GET(self) -> None:
        if not self._host_is_valid():
            self._reject(HTTPStatus.BAD_REQUEST, "HOST_REJECTED", "Host 校验失败。")
            return
        if self.path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, b"", "text/plain; charset=utf-8")
            return
        if self.path != "/":
            self._reject(HTTPStatus.NOT_FOUND, "NOT_FOUND", "页面不存在。")
            return
        nonce = secrets.token_urlsafe(24)
        page = _PAGE_TEMPLATE.replace("__NONCE__", html.escape(nonce, quote=True)).encode("utf-8")
        self._send_bytes(
            HTTPStatus.OK,
            page,
            "text/html; charset=utf-8",
            csp_nonce=nonce,
        )

    def do_POST(self) -> None:
        if not self._host_is_valid():
            self._discard_bounded_request_body()
            self._reject(HTTPStatus.BAD_REQUEST, "HOST_REJECTED", "Host 校验失败。")
            return
        if not self._api_headers_are_valid():
            self._discard_bounded_request_body()
            self._reject(HTTPStatus.FORBIDDEN, "REQUEST_REJECTED", "请求来源或 token 无效。")
            return
        try:
            payload = self._read_json()
            if self.path == "/api/state":
                if payload != {}:
                    raise SetupError("BAD_REQUEST")
                self._send_json(HTTPStatus.OK, self.wizard_server.writer.state())
                return
            if self.path == "/api/save":
                result = self.wizard_server.writer.save(payload)
                self.wizard_server.completed = True
                self.wizard_server.setup_token = secrets.token_urlsafe(48)
                self._send_json(HTTPStatus.OK, result)
                return
            self._reject(HTTPStatus.NOT_FOUND, "NOT_FOUND", "接口不存在。")
        except SetupError as exc:
            self._reject(exc.status, exc.code, exc.public_message)
        except Exception:
            self._reject(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "本机配置保存失败；未执行任何外部连接。",
            )

    def _method_not_allowed(self) -> None:
        self._reject(HTTPStatus.METHOD_NOT_ALLOWED, "METHOD_NOT_ALLOWED", "请求方法不允许。")

    do_DELETE = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_PUT = _method_not_allowed


class WizardHTTPServer(HTTPServer):
    allow_reuse_address = False

    def __init__(self, writer: LocalSetupWriter, setup_token: str) -> None:
        super().__init__(("127.0.0.1", 0), WizardRequestHandler)
        host, port = cast(tuple[str, int], self.server_address)
        if host != "127.0.0.1":
            self.server_close()
            raise RuntimeError("local setup server did not bind to IPv4 loopback")
        self.writer = writer
        self.setup_token = setup_token
        self.expected_host = f"127.0.0.1:{port}"
        self.origin = f"http://{self.expected_host}"
        self.completed = False
        self.timeout = 0.5


def run_local_setup(
    writer: LocalSetupWriter,
    *,
    open_browser: bool = True,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    setup_token = secrets.token_urlsafe(32)
    server = WizardHTTPServer(writer, setup_token)
    launch_url = f"{server.origin}/#{setup_token}"
    try:
        try:
            opened = webbrowser.open(launch_url, new=1, autoraise=True) if open_browser else False
        except Exception:
            opened = False
        if not opened:
            print("未能安全打开默认浏览器，配置向导已关闭；请重新运行 scripts/setup.ps1。")
            return False
        print(f"UNIN 本机配置向导已打开：{server.origin}/")
        print("向导仅监听 127.0.0.1，将在 15 分钟后自动关闭；保存不会测试外部连接。")
        deadline = time.monotonic() + timeout_seconds
        while not server.completed and time.monotonic() < deadline:
            server.handle_request()
        return server.completed
    finally:
        server.setup_token = secrets.token_urlsafe(48)
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UNIN 一次性本机配置向导")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--recover-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    return parser


def _consume_windows_acl_marker(project_root: Path) -> bool:
    nonce = os.environ.pop(WINDOWS_ACL_NONCE_ENV, None)
    marker = project_root / "secrets" / ".setup-staging" / WINDOWS_ACL_MARKER_NAME
    if (
        not isinstance(nonce, str)
        or not 43 <= len(nonce) <= 128
        or not nonce.isascii()
        or any(character.isspace() for character in nonce)
    ):
        return False
    try:
        if _is_link_like(project_root) or not project_root.is_dir():
            return False
        for directory in (marker.parent.parent, marker.parent):
            if _is_link_like(directory) or not directory.is_dir():
                return False
        marker_stat = _regular_file_stat(marker, single_link=True)
        if not 43 <= marker_stat.st_size <= 128:
            return False
        marker_nonce = marker.read_text(encoding="ascii")
        if not hmac.compare_digest(marker_nonce, nonce):
            return False
        marker.unlink()
        return True
    except (OSError, SetupError, UnicodeError):
        return False


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 12):  # noqa: UP036 - standalone launcher guard
        print("本机配置向导需要 Python 3.12 或更高版本。", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    if (
        os.name == "nt"
        and not args.recover_only
        and not _consume_windows_acl_marker(args.project_root)
    ):
        print("Windows 必须通过 scripts/setup.ps1 启动，以便先收紧当前 SID ACL。", file=sys.stderr)
        return 2
    try:
        writer = LocalSetupWriter(args.project_root, replace_existing=args.replace_existing)
        if args.recover_only:
            print("本机配置向导 staging 恢复检查已完成。")
            return 0
        completed = run_local_setup(writer, open_browser=not args.no_browser)
    except SetupError as exc:
        print(f"配置向导未启动：{exc.public_message}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError):
        print("配置向导未能绑定本机端口或访问配置目录。", file=sys.stderr)
        return 2
    if not completed:
        print("配置向导已超时关闭，未完成保存。", file=sys.stderr)
        return 3
    print("配置已原子保存；未测试任何外部连接，也未启动 Docker。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
