from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.simple.models import (
    ActivityLog,
    Download,
    DownloadState,
    Episode,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)


async def list_media(
    session: AsyncSession,
    *,
    state: MediaState | None,
    media_type: MediaType | None,
    country_code: str | None,
    year: int | None,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[LibraryMediaItem], int]:
    filters: list[ColumnElement[bool]] = []
    if state is not None:
        filters.append(LibraryMediaItem.state == state)
    if media_type is not None:
        filters.append(LibraryMediaItem.media_type == media_type)
    if year is not None:
        filters.append(LibraryMediaItem.year == year)
    if query and (term := query.strip()):
        filters.append(
            or_(
                LibraryMediaItem.title.icontains(term, autoescape=True),
                LibraryMediaItem.original_title.icontains(term, autoescape=True),
            )
        )

    statement = (
        select(LibraryMediaItem)
        .where(*filters)
        .order_by(LibraryMediaItem.updated_at.desc())
    )
    normalized_country = country_code.strip().upper() if country_code else None
    if normalized_country:
        all_rows = list(await session.scalars(statement))
        matching = [item for item in all_rows if normalized_country in item.country_codes]
        start = (page - 1) * page_size
        return matching[start : start + page_size], len(matching)

    total = await session.scalar(select(func.count()).select_from(LibraryMediaItem).where(*filters))
    page_rows = await session.scalars(
        statement.offset((page - 1) * page_size).limit(page_size)
    )
    return list(page_rows), int(total or 0)


async def media_filter_options(
    session: AsyncSession,
) -> tuple[list[MediaType], list[str], list[MediaState], list[int]]:
    rows = await session.execute(
        select(
            LibraryMediaItem.media_type,
            LibraryMediaItem.country_codes,
            LibraryMediaItem.state,
            LibraryMediaItem.year,
        )
    )
    media_types: set[MediaType] = set()
    country_codes: set[str] = set()
    states: set[MediaState] = set()
    years: set[int] = set()
    for media_type, item_country_codes, state, year in rows:
        media_types.add(media_type)
        country_codes.update(item_country_codes or [])
        states.add(state)
        if year is not None:
            years.add(year)
    return (
        sorted(media_types, key=lambda item: item.value),
        sorted(country_codes),
        sorted(states, key=lambda item: item.value),
        sorted(years, reverse=True),
    )


async def get_media(
    session: AsyncSession, media_id: str
) -> tuple[LibraryMediaItem, list[Episode], ReleaseSearch | None]:
    media = await session.get(LibraryMediaItem, media_id)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    episodes = await session.scalars(
        select(Episode)
        .where(Episode.media_id == media_id)
        .order_by(Episode.season_number, Episode.episode_number)
    )
    latest_search = await session.scalar(
        select(ReleaseSearch)
        .where(ReleaseSearch.media_id == media_id)
        .order_by(ReleaseSearch.created_at.desc(), ReleaseSearch.id.desc())
        .limit(1)
    )
    return media, list(episodes), latest_search


async def create_search(
    session: AsyncSession,
    *,
    media_id: str,
    site_ids: list[str],
) -> ReleaseSearch:
    media = await session.get(LibraryMediaItem, media_id, with_for_update=True)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    if media.tmdb_id is None:
        raise AppError("MEDIA_IDENTITY_REQUIRED", "请先确认 TMDB 影视信息", status_code=409)
    if media.state == MediaState.DOWNLOADING:
        raise AppError("MEDIA_ALREADY_DOWNLOADING", "该影视已有下载任务", status_code=409)

    search = ReleaseSearch(media_id=media.id, site_ids=site_ids)
    media.state = MediaState.SEARCHING
    media.attention_reason = None
    session.add_all(
        [
            search,
            ActivityLog(media_id=media.id, event="SEARCH_CREATED", message="已开始搜索 PT 资源"),
        ]
    )
    await session.commit()
    await session.refresh(search)
    return search


async def get_search(
    session: AsyncSession, search_id: str
) -> tuple[ReleaseSearch, list[ReleaseCandidate]]:
    search = await session.get(ReleaseSearch, search_id)
    if search is None:
        raise AppError("SEARCH_NOT_FOUND", "搜索记录不存在", status_code=404)
    candidates = await session.scalars(
        select(ReleaseCandidate)
        .where(ReleaseCandidate.search_id == search.id)
        .order_by(ReleaseCandidate.score.desc(), ReleaseCandidate.seeders.desc())
    )
    return search, list(candidates)


async def complete_search(
    session: AsyncSession,
    *,
    search_id: str,
    candidates: Sequence[ReleaseCandidate],
) -> ReleaseSearch:
    search = await session.get(ReleaseSearch, search_id, with_for_update=True)
    if search is None:
        raise AppError("SEARCH_NOT_FOUND", "搜索记录不存在", status_code=404)
    if search.state not in {SearchState.PENDING, SearchState.RUNNING}:
        raise AppError("SEARCH_ALREADY_FINISHED", "搜索已经结束", status_code=409)
    media = await session.get(LibraryMediaItem, search.media_id, with_for_update=True)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)

    for candidate in candidates:
        candidate.search_id = search.id
        session.add(candidate)
    search.state = SearchState.SUCCEEDED
    search.finished_at = utc_now()
    media.state = MediaState.CANDIDATES if candidates else MediaState.NEEDS_ATTENTION
    media.attention_reason = None if candidates else "没有找到可用资源"
    session.add(
        ActivityLog(
            media_id=media.id,
            event="SEARCH_COMPLETED",
            message=f"搜索完成，找到 {len(candidates)} 个候选资源",
            details={"candidate_count": len(candidates)},
        )
    )
    await session.commit()
    await session.refresh(search)
    return search


async def queue_download(
    session: AsyncSession,
    *,
    candidate_id: str,
    confirm_warnings: bool,
) -> Download:
    candidate = await session.get(ReleaseCandidate, candidate_id)
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "候选资源不存在", status_code=404)
    if candidate.warnings and not confirm_warnings:
        raise AppError(
            "CANDIDATE_CONFIRMATION_REQUIRED",
            "该资源有风险提示，请确认后下载",
            status_code=409,
            details={"warnings": candidate.warnings},
        )
    search = await session.get(ReleaseSearch, candidate.search_id)
    if search is None:
        raise AppError("SEARCH_NOT_FOUND", "搜索记录不存在", status_code=404)
    media = await session.get(LibraryMediaItem, search.media_id, with_for_update=True)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)

    existing = await session.scalar(select(Download).where(Download.candidate_id == candidate.id))
    if existing is not None:
        if existing.state == DownloadState.ERROR:
            existing.state = DownloadState.SUBMITTING
            existing.error_message = None
            media.state = MediaState.DOWNLOADING
            session.add(
                ActivityLog(
                    media_id=media.id,
                    event="DOWNLOAD_RETRYING",
                    message=f"重新提交资源：{candidate.title}",
                    details={"site_id": candidate.site_id},
                )
            )
            await session.commit()
            await session.refresh(existing)
        return existing

    download = Download(
        media_id=media.id,
        candidate_id=candidate.id,
        info_hash=candidate.info_hash,
        name=candidate.title,
    )
    media.state = MediaState.DOWNLOADING
    session.add_all(
        [
            download,
            ActivityLog(
                media_id=media.id,
                event="DOWNLOAD_QUEUED",
                message=f"已选择资源：{candidate.title}",
                details={"site_id": candidate.site_id},
            ),
        ]
    )
    await session.commit()
    await session.refresh(download)
    return download


async def list_downloads(
    session: AsyncSession,
    *,
    state: DownloadState | None,
    page: int,
    page_size: int,
) -> tuple[list[Download], int]:
    filters = [Download.state == state] if state is not None else []
    total = await session.scalar(select(func.count()).select_from(Download).where(*filters))
    rows = await session.scalars(
        select(Download)
        .where(*filters)
        .order_by(Download.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows), int(total or 0)
