from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.simple.models import (
    ActivityLog,
    AutomationJob,
    Download,
    DownloadState,
    Episode,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.regions import NEXTFIND_REGION_ORDER, NextFindRegion, nextfind_regions


async def list_media(
    session: AsyncSession,
    *,
    state: MediaState | None,
    media_type: MediaType | None,
    region: NextFindRegion | None,
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
    if region is not None:
        all_rows = list(await session.scalars(statement))
        matching = [
            item
            for item in all_rows
            if region in nextfind_regions(item.country_codes, item.original_language)
        ]
        start = (page - 1) * page_size
        return matching[start : start + page_size], len(matching)

    total = await session.scalar(select(func.count()).select_from(LibraryMediaItem).where(*filters))
    page_rows = await session.scalars(
        statement.offset((page - 1) * page_size).limit(page_size)
    )
    return list(page_rows), int(total or 0)


async def media_filter_options(
    session: AsyncSession,
) -> tuple[list[MediaType], list[NextFindRegion], list[MediaState], list[int]]:
    rows = await session.execute(
        select(
            LibraryMediaItem.media_type,
            LibraryMediaItem.state,
            LibraryMediaItem.year,
        )
    )
    media_types: set[MediaType] = set()
    states: set[MediaState] = set()
    years: set[int] = set()
    for media_type, state, year in rows:
        media_types.add(media_type)
        states.add(state)
        if year is not None:
            years.add(year)
    return (
        sorted(media_types, key=lambda item: item.value),
        list(NEXTFIND_REGION_ORDER),
        sorted(states, key=lambda item: item.value),
        sorted(years, reverse=True),
    )


async def latest_automation_job(session: AsyncSession, media_id: str) -> AutomationJob | None:
    """The most recent automation attempt for this media item.

    Its ``decision`` is the only record of *why* nothing was downloaded, and
    until now it was reachable only from the automation pages -- so a media
    item sitting at READY looked identical whether the site had nothing, the
    score threshold rejected everything, or every candidate was a dead torrent.
    """

    job: AutomationJob | None = await session.scalar(
        select(AutomationJob)
        .where(AutomationJob.media_id == media_id)
        # created_at can tie when two jobs land in the same clock tick, and a
        # random UUID is a meaningless tie-break; a job that has finished is
        # the later one in every case that matters.
        .order_by(
            AutomationJob.created_at.desc(),
            AutomationJob.finished_at.desc(),
            AutomationJob.id.desc(),
        )
        .limit(1)
    )
    return job


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


# How long a completed search stands in for the next identical one.  Long
# enough to absorb a manual search immediately followed by an automation run,
# short enough that a user pressing search twice on purpose still sees the
# site's current state.
SEARCH_CACHE_TTL = timedelta(minutes=5)


async def get_or_create_search(
    session: AsyncSession,
    *,
    media_id: str,
    site_ids: list[str],
    force: bool = False,
) -> tuple[ReleaseSearch, bool]:
    """Return a search for this media/site pair and whether it must still run.

    A user who searches by hand and an automation cycle that starts a minute
    later used to send the PT site the same query twice, spending quota to
    learn the same thing -- and occasionally disagreeing, because the two
    result sets were fetched seconds apart.  Reusing a recent completed search
    removes both problems.  ``force`` is the escape hatch behind the UI's
    refresh button, for when the user knows the site has changed.
    """

    media = await session.get(LibraryMediaItem, media_id, with_for_update=True)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    if media.tmdb_id is None:
        raise AppError("MEDIA_IDENTITY_REQUIRED", "请先确认 TMDB 影视信息", status_code=409)
    if media.state == MediaState.DOWNLOADING:
        raise AppError("MEDIA_ALREADY_DOWNLOADING", "该影视已有下载任务", status_code=409)

    cache_key = ReleaseSearch.make_cache_key(media.id, site_ids)
    if not force:
        cached = await session.scalar(
            select(ReleaseSearch)
            .where(
                ReleaseSearch.cache_key == cache_key,
                ReleaseSearch.cache_expires_at.is_not(None),
                ReleaseSearch.cache_expires_at > utc_now(),
                # Only a finished, successful search stands for a result.  A
                # PENDING or FAILED one carries no candidates, and handing it
                # back would look like an instant empty search.
                ReleaseSearch.state == SearchState.SUCCEEDED,
            )
            .order_by(ReleaseSearch.created_at.desc(), ReleaseSearch.id.desc())
            .limit(1)
        )
        if cached is not None:
            return cached, False

    search = ReleaseSearch(
        media_id=media.id,
        site_ids=site_ids,
        cache_key=cache_key,
        cache_expires_at=utc_now() + SEARCH_CACHE_TTL,
    )
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
    return search, True


async def create_search(
    session: AsyncSession,
    *,
    media_id: str,
    site_ids: list[str],
) -> ReleaseSearch:
    """Always start a new search, bypassing the cache."""

    search, _ = await get_or_create_search(
        session, media_id=media_id, site_ids=site_ids, force=True
    )
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
            media.attention_reason = None
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

    active_for_media = await session.scalar(
        select(Download)
        .where(
            Download.media_id == media.id,
            Download.state.in_(
                (
                    DownloadState.SUBMITTING,
                    DownloadState.QUEUED,
                    DownloadState.DOWNLOADING,
                    DownloadState.PAUSED,
                    DownloadState.SEEDING,
                    DownloadState.OUTCOME_UNKNOWN,
                )
            ),
        )
        .order_by(Download.created_at.desc())
    )
    if active_for_media is not None:
        raise AppError(
            "MEDIA_DOWNLOAD_ACTIVE",
            "该影视已有进行中的下载，请先同步或处理现有任务",
            status_code=409,
            details={"existing_download_id": active_for_media.id},
        )

    download = Download(
        media_id=media.id,
        candidate_id=candidate.id,
        info_hash=candidate.info_hash,
        name=candidate.title,
    )
    media.state = MediaState.DOWNLOADING
    media.attention_reason = None
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
