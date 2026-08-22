from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.adapters.downloaders.qbittorrent_readonly import QbittorrentReadOnlyAdapter
from app.errors import AppError
from app.schemas.adapters import AdapterManifest
from app.services.torrent_validation import validate_torrent

QbAddOutcome = Literal["SUBMITTED", "ALREADY_PRESENT"]
QbAddStateField = Literal["paused", "stopped"]


@dataclass(frozen=True)
class QbAddResult:
    info_hash: str
    outcome: QbAddOutcome


class QbittorrentAdapter(QbittorrentReadOnlyAdapter):
    _WRITE_REQUESTS = frozenset(
        {
            ("POST", "/api/v2/torrents/add"),
            ("POST", "/api/v2/torrents/createCategory"),
        }
    )
    _ALLOWED_REQUESTS = QbittorrentReadOnlyAdapter._ALLOWED_REQUESTS | _WRITE_REQUESTS
    _WEB_API_VERSION = re.compile(
        r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
    )
    _STOPPED_ADD_PARAMETER_SINCE = (2, 11, 0)

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        allowed_hosts: tuple[str, ...],
        enable_write: bool = False,
        allow_insecure_http: bool = False,
        connect_timeout: float = 5,
        read_timeout: float = 30,
        max_response_bytes: int = 10 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
        before_request: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._add_state_field: QbAddStateField | None = None
        super().__init__(
            base_url=base_url,
            username=username,
            password=password,
            allowed_hosts=allowed_hosts,
            allow_insecure_http=allow_insecure_http,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
            transport=transport,
            before_request=before_request,
        )
        self.enable_write = enable_write

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="qbittorrent-executor",
            name="qBittorrent",
            adapter_type="downloader",
            version="1.0",
            enabled=True,
            mode="LIVE_WRITE" if self.enable_write else "WRITE_DISABLED",
            description="提交经过校验且已查重的 torrent 到 qBittorrent",
            capabilities={
                "login": True,
                "list_torrents": True,
                "add_torrent": self.enable_write,
                "create_category": self.enable_write,
                "write_operations": self.enable_write,
                "delete_torrent": False,
                "modify_torrent": False,
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        require_auth: bool = True,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> httpx.Response:
        if (method.upper(), path) in self._WRITE_REQUESTS and not self.enable_write:
            raise AppError(
                "QB_WRITE_DISABLED",
                "qBittorrent 写入能力默认关闭",
                status_code=403,
            )
        return await super()._request(
            method,
            path,
            require_auth=require_auth,
            params=params,
            data=data,
            files=files,
            before_send=before_send,
        )

    async def add_torrent(
        self,
        torrent: bytes,
        *,
        expected_info_hash: str,
        save_path: str | None,
        category: str,
        tags: tuple[str, ...] = (),
        start_immediately: bool = True,
        category_prepared: bool = False,
        category_write_guard: Callable[[], Awaitable[None]] | None = None,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> QbAddResult:
        if not self.enable_write:
            raise AppError(
                "QB_WRITE_DISABLED",
                "qBittorrent 写入能力默认关闭",
                status_code=403,
            )
        if not self._authenticated:
            raise AppError("QB_NOT_AUTHENTICATED", "qBittorrent SID 会话不存在", status_code=401)
        if not self._INFO_HASH.fullmatch(expected_info_hash):
            raise AppError(
                "QB_EXPECTED_INFO_HASH_REQUIRED",
                "提交前必须提供已校验并持久化的 info hash",
                status_code=409,
            )
        self._validate_target(save_path, category, tags)
        metadata = validate_torrent(torrent, expected_info_hash=expected_info_hash)
        selected_hash = expected_info_hash.casefold()

        existing = await self.find_torrents_by_hashes(metadata.identity_hashes)
        if any(
            not metadata.identity_hashes.isdisjoint(item.identity_hashes)
            for item in existing
        ):
            return QbAddResult(info_hash=selected_hash, outcome="ALREADY_PRESENT")

        add_state_field = await self._get_add_state_field()
        if not category_prepared:
            await self.ensure_category(
                category,
                save_path,
                write_guard=category_write_guard or write_guard,
            )
        form = {
            "category": category,
            "tags": ",".join(tags),
            add_state_field: "false" if start_immediately else "true",
        }
        if save_path:
            form["savepath"] = save_path
        try:
            response = await self._request(
                "POST",
                "/api/v2/torrents/add",
                data=form,
                files={
                    "torrents": (
                        "approved.torrent",
                        torrent,
                        "application/x-bittorrent",
                    )
                },
                before_send=write_guard,
            )
        except AppError as exc:
            if exc.details.get("external_request_performed") is False:
                raise
            raise self._unknown_outcome() from exc
        if self._looks_like_html(response) or response.text.strip() != "Ok.":
            raise self._unknown_outcome()
        return QbAddResult(info_hash=selected_hash, outcome="SUBMITTED")

    async def ensure_category(
        self,
        category: str,
        save_path: str | None,
        *,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not self.enable_write:
            raise AppError(
                "QB_WRITE_DISABLED",
                "qBittorrent 写入能力默认关闭",
                status_code=403,
            )
        if not self._authenticated:
            raise AppError("QB_NOT_AUTHENTICATED", "qBittorrent SID 会话不存在", status_code=401)
        self._validate_target(save_path, category, ())
        await self._ensure_category(category, save_path, write_guard=write_guard)

    async def _ensure_category(
        self,
        category: str,
        save_path: str | None,
        *,
        write_guard: Callable[[], Awaitable[None]] | None,
    ) -> None:
        categories = await self.get_categories()
        if category in categories:
            return

        create_error: AppError | None = None
        try:
            form = {"category": category}
            if save_path:
                form["savePath"] = save_path
            response = await self._request(
                "POST",
                "/api/v2/torrents/createCategory",
                data=form,
                before_send=write_guard,
            )
            if self._looks_like_html(response) or response.text.strip() != "Ok.":
                create_error = AppError(
                    "QB_RESPONSE_INVALID",
                    "qBittorrent 创建分类返回了无效响应",
                    status_code=502,
                )
        except AppError as exc:
            if (
                exc.details.get("external_request_performed") is False
                or exc.error_code == "QB_AUTH_FAILED"
            ):
                raise
            create_error = exc

        try:
            categories = await self.get_categories()
        except AppError as exc:
            raise AppError(
                "QB_CATEGORY_CREATE_OUTCOME_UNKNOWN",
                "无法确认 qBittorrent 目标分类是否创建成功",
                status_code=502,
                retryable=True,
                details={
                    "category": category,
                    "category_write_may_have_occurred": True,
                },
            ) from (create_error or exc)

        # A concurrent execution can create the same category while our request is in
        # flight. The authoritative read makes that race idempotent even if qB returns
        # an error for one of the create requests.
        if category in categories:
            return

        details: dict[str, Any] = {"category": category}
        if create_error is not None:
            details["cause_error_code"] = create_error.error_code
        raise AppError(
            "QB_CATEGORY_CREATE_FAILED",
            "qBittorrent 目标分类创建失败",
            status_code=502,
            retryable=True,
            details=details,
        )

    async def _get_add_state_field(self) -> QbAddStateField:
        if self._add_state_field is not None:
            return self._add_state_field

        raw_version = await self.get_web_api_version()
        match = self._WEB_API_VERSION.fullmatch(raw_version)
        if match is None:
            raise AppError(
                "QB_WEB_API_VERSION_INVALID",
                "qBittorrent Web API 版本格式无效，无法安全选择添加参数",
                status_code=502,
            )
        version = (int(match[1]), int(match[2]), int(match[3]))
        if version[0] != 2:
            raise AppError(
                "QB_WEB_API_VERSION_UNSUPPORTED",
                "qBittorrent Web API 版本不受支持，已拒绝添加请求",
                status_code=409,
            )

        # Web API 2.11.0 (qBittorrent 5.0) renamed the add flag from paused to stopped.
        self._add_state_field = (
            "stopped" if version >= self._STOPPED_ADD_PARAMETER_SINCE else "paused"
        )
        return self._add_state_field

    def _clear_session(self) -> None:
        self._add_state_field = None
        super()._clear_session()

    @staticmethod
    def _unknown_outcome() -> AppError:
        return AppError(
            "QB_ADD_OUTCOME_UNKNOWN",
            "qBittorrent 添加请求结果不确定，必须按 info hash 对账",
            status_code=502,
            retryable=False,
            details={"external_write_may_have_occurred": True},
        )

    @staticmethod
    def _validate_target(
        save_path: str | None, category: str, tags: tuple[str, ...]
    ) -> None:
        values = (category, *tags, *((save_path,) if save_path else ()))
        if not category.strip() or any(
            not value.strip() or any(marker in value for marker in ("\x00", "\r", "\n"))
            for value in values
        ):
            raise AppError("QB_TARGET_INVALID", "qBittorrent 下载目标配置无效", status_code=409)
        if len(tags) > 20 or any("," in tag or len(tag) > 100 for tag in tags):
            raise AppError("QB_TARGET_INVALID", "qBittorrent 标签配置无效", status_code=409)
