from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import QbAdapter, ViewerPrincipal
from app.schemas.qbittorrent import QbStatus, QbTorrentList

router = APIRouter(prefix="/downloaders/qbittorrent", tags=["qbittorrent-read-only"])


@router.get("/status", response_model=QbStatus)
async def get_qbittorrent_status(
    _principal: ViewerPrincipal, adapter: QbAdapter
) -> QbStatus:
    await adapter.authenticate()
    application_version = await adapter.get_version()
    web_api_version = await adapter.get_web_api_version()
    torrents = await adapter.list_torrents()
    categories = await adapter.get_categories()
    active = sum(
        item.progress == 1
        and (item.upspeed > 0 or item.state.casefold() in {"uploading", "forcedup"})
        for item in torrents
    )
    return QbStatus(
        connected=True,
        application_version=application_version,
        web_api_version=web_api_version,
        torrent_count=len(torrents),
        category_count=len(categories),
        active_seeding_count=active,
    )


@router.get("/torrents", response_model=QbTorrentList)
async def get_qbittorrent_torrents(
    _principal: ViewerPrincipal, adapter: QbAdapter
) -> QbTorrentList:
    await adapter.authenticate()
    torrents = await adapter.list_torrents()
    return QbTorrentList(items=torrents, total=len(torrents))
