from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.adapters.downloaders.qbittorrent_readonly import QbittorrentReadOnlyAdapter
from app.errors import AppError
from app.schemas.adapters import AdapterManifest
from app.services.torrent_validation import validate_torrent

QbAddOutcome = Literal["SUBMITTED", "ALREADY_PRESENT"]


@dataclass(frozen=True)
class QbAddResult:
    info_hash: str
    outcome: QbAddOutcome


class QbittorrentAdapter(QbittorrentReadOnlyAdapter):
    _ALLOWED_REQUESTS = QbittorrentReadOnlyAdapter._ALLOWED_REQUESTS | {
        ("POST", "/api/v2/torrents/add")
    }

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
    ) -> None:
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
        )
        self.enable_write = enable_write

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="qbittorrent-executor",
            name="qBittorrent",
            adapter_type="downloader",
            version="1.0",
            enabled=True,
            mode="LIVE_WRITE_GATED" if self.enable_write else "WRITE_DISABLED",
            description="受执行审批和幂等检查保护的 qBittorrent 添加能力",
            capabilities={
                "login": True,
                "list_torrents": True,
                "add_torrent": self.enable_write,
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
    ) -> httpx.Response:
        if method.upper() == "POST" and path == "/api/v2/torrents/add" and not self.enable_write:
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
        )

    async def add_torrent(
        self,
        torrent: bytes,
        *,
        expected_info_hash: str,
        save_path: str,
        category: str,
        tags: tuple[str, ...] = (),
        start_immediately: bool = True,
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

        known_hashes = {
            identity
            for item in await self.list_torrents()
            for identity in item.identity_hashes
        }
        if metadata.identity_hashes & known_hashes:
            return QbAddResult(info_hash=selected_hash, outcome="ALREADY_PRESENT")

        form = {
            "savepath": save_path,
            "category": category,
            "tags": ",".join(tags),
            "paused": "false" if start_immediately else "true",
        }
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
            )
        except AppError as exc:
            raise self._unknown_outcome() from exc
        if self._looks_like_html(response) or response.text.strip() != "Ok.":
            raise self._unknown_outcome()
        return QbAddResult(info_hash=selected_hash, outcome="SUBMITTED")

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
    def _validate_target(save_path: str, category: str, tags: tuple[str, ...]) -> None:
        values = (save_path, category, *tags)
        if not save_path.strip() or not category.strip() or any(
            not value.strip() or any(marker in value for marker in ("\x00", "\r", "\n"))
            for value in values
        ):
            raise AppError("QB_TARGET_INVALID", "qBittorrent 下载目标配置无效", status_code=409)
        if len(tags) > 20 or any("," in tag or len(tag) > 100 for tag in tags):
            raise AppError("QB_TARGET_INVALID", "qBittorrent 标签配置无效", status_code=409)
