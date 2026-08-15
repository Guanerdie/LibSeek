from fastapi import APIRouter, Query

from app.api.dependencies import DbSession, ViewerPrincipal
from app.core.media_regions import MediaRegion
from app.errors import AppError
from app.models.entities import MediaItem
from app.models.enums import MediaType
from app.schemas.common import Page
from app.schemas.entities import MediaItemResponse
from app.services.media import list_media_items

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=Page[MediaItemResponse])
async def get_media(
    session: DbSession,
    _principal: ViewerPrincipal,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
    media_type: MediaType | None = None,
    identity_confidence: str | None = Query(default=None, max_length=30),
    region: MediaRegion | None = None,
    query: str | None = Query(default=None, max_length=200),
) -> Page[MediaItemResponse]:
    items, total = await list_media_items(
        session,
        page=page,
        page_size=page_size,
        media_type=media_type,
        identity_confidence=identity_confidence,
        region=region,
        query=query,
    )
    return Page[MediaItemResponse](items=items, page=page, page_size=page_size, total=total)


@router.get("/{media_id}", response_model=MediaItemResponse)
async def get_media_item(
    media_id: str, session: DbSession, _principal: ViewerPrincipal
) -> MediaItemResponse:
    item = await session.get(MediaItem, media_id)
    if item is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    return MediaItemResponse.model_validate(item)
