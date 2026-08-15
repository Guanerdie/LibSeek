from __future__ import annotations

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.media_regions import MediaRegion, country_codes_for_region
from app.models.entities import MediaItem
from app.models.enums import MediaType


async def list_media_items(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    media_type: MediaType | None = None,
    identity_confidence: str | None = None,
    region: MediaRegion | None = None,
    query: str | None = None,
) -> tuple[list[MediaItem], int]:
    filters = []
    if media_type is not None:
        filters.append(MediaItem.media_type == media_type)
    if identity_confidence:
        filters.append(MediaItem.identity_confidence == identity_confidence)
    if region is not None:
        serialized_country_codes = func.upper(cast(MediaItem.country_codes, String))
        filters.append(
            or_(
                *(
                    serialized_country_codes.like(f'%"{code}"%')
                    for code in country_codes_for_region(region)
                )
            )
        )
    if query:
        filters.append(MediaItem.title.ilike(f"%{query.strip()}%"))
    total = (
        await session.scalar(select(func.count()).select_from(MediaItem).where(*filters)) or 0
    )
    statement = (
        select(MediaItem)
        .where(*filters)
        .order_by(MediaItem.updated_at.desc(), MediaItem.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await session.scalars(statement)).all()), total
