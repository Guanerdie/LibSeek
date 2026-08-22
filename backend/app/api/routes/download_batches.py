from fastapi import APIRouter, Query

from app.api.dependencies import DbSession, OperatorPrincipal, SettingsDep, ViewerPrincipal
from app.errors import AppError
from app.models.entities import DownloadBatchItem
from app.schemas.common import Page
from app.schemas.download_batches import (
    DownloadBatchCreateRequest,
    DownloadBatchResponse,
    DownloadBatchSummaryResponse,
)
from app.services.download_batches import (
    create_download_batch,
    download_batch_response,
    get_download_batch,
    list_download_batches,
    retry_download_batch_item,
)

router = APIRouter(prefix="/download-batches", tags=["download-batches"])


@router.post("", response_model=DownloadBatchResponse, status_code=201)
async def create_batch(
    request: DownloadBatchCreateRequest,
    session: DbSession,
    settings: SettingsDep,
    principal: OperatorPrincipal,
) -> DownloadBatchResponse:
    batch = await create_download_batch(session, request, settings, actor=principal.username)
    await session.commit()
    return await download_batch_response(session, batch)


@router.get("", response_model=Page[DownloadBatchSummaryResponse])
async def get_batches(
    session: DbSession,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[DownloadBatchSummaryResponse]:
    items, total = await list_download_batches(session, page=page, page_size=page_size)
    await session.commit()
    return Page(items=items, page=page, page_size=page_size, total=total)


@router.get("/{batch_id}", response_model=DownloadBatchResponse)
async def get_batch(
    batch_id: str, session: DbSession, _principal: ViewerPrincipal
) -> DownloadBatchResponse:
    batch = await get_download_batch(session, batch_id)
    await session.commit()
    return await download_batch_response(session, batch)


@router.post("/{batch_id}/items/{item_id}/retry", response_model=DownloadBatchResponse)
async def retry_batch_item(
    batch_id: str,
    item_id: str,
    session: DbSession,
    settings: SettingsDep,
    _principal: OperatorPrincipal,
) -> DownloadBatchResponse:
    batch = await get_download_batch(session, batch_id)
    item = await session.get(DownloadBatchItem, item_id)
    if item is None or item.batch_id != batch.id:
        raise AppError("DOWNLOAD_BATCH_ITEM_NOT_FOUND", "下载批次条目不存在", status_code=404)
    await retry_download_batch_item(session, batch, item, settings)
    await session.commit()
    return await download_batch_response(session, batch)
