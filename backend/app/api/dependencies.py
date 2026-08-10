from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.core.config import get_settings
from app.db.session import get_session
from app.errors import AppError

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_qb_adapter() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
    settings = get_settings()
    if not settings.enable_qb_read_only:
        raise AppError("QB_READ_ONLY_DISABLED", "qBittorrent 只读连接默认关闭", status_code=409)
    credentials = settings.qb_credentials()
    if credentials is None or not settings.qb_allowed_hosts:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时 Secret 未配置", status_code=409)
    base_url, username, password = credentials
    adapter = QbittorrentReadOnlyAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=settings.qb_allowed_hosts,
        allow_insecure_http=settings.qb_allow_insecure_http,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


QbAdapter = Annotated[ReadOnlyDownloaderAdapter, Depends(get_qb_adapter)]
