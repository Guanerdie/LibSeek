from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import MediaSourceAdapter, MetadataProvider, PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.media_sources import NextFindAdapter
from app.adapters.metadata import TmdbProvider
from app.adapters.pt_sites import AvistaZAdapter
from app.core.config import Settings, get_settings
from app.core.http import shared_rate_limiter
from app.core.security import validate_external_url
from app.core.time import utc_now
from app.errors import AppError
from app.schemas.adapters import (
    MediaItemData,
    MetadataRecord,
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.services.matching import MatchPreferences, score_torrent_candidate
from app.services.torrent_validation import validate_torrent
from app.simple.automation_state import refresh_automation_run_summary
from app.simple.models import (
    ActivityLog,
    AutomationJob,
    AutomationJobState,
    AutomationRunState,
    Download,
    DownloadState,
    Episode,
    EpisodeState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
    SearchState,
)
from app.simple.service import complete_search, queue_download

_EPISODE_CODE = re.compile(r"^S(\d{2})E(\d{2,5})$")
# qBittorrent does not list a torrent the instant it is accepted, so a download
# is only declared missing once it has had time to show up.
_QB_MISSING_GRACE = timedelta(minutes=15)
_download_submission_lock = asyncio.Lock()
_nextfind_sync_lock = asyncio.Lock()


def build_nextfind(settings: Settings | None = None) -> NextFindAdapter:
    settings = settings or get_settings()
    base_url = validate_external_url(settings.nextfind_base_url, settings.nextfind_allowed_hosts)
    hostname = urlparse(base_url).hostname
    credentials = settings.nextfind_credentials()
    if hostname is None or credentials is None:
        raise AppError("NEXTFIND_NOT_CONFIGURED", "NextFind 尚未完成配置", status_code=409)
    username, password = credentials
    return NextFindAdapter(
        base_url=base_url,
        allowed_hosts=(hostname.casefold(),),
        username=username,
        password=password,
        max_response_bytes=settings.external_max_response_bytes,
        max_line_bytes=settings.external_max_ndjson_line_bytes,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        proxy=settings.outbound_proxy(),
    )


def build_tmdb(settings: Settings | None = None) -> TmdbProvider:
    settings = settings or get_settings()
    token = settings.tmdb_token_value()
    if token is None:
        raise AppError("TMDB_NOT_CONFIGURED", "TMDB 尚未完成配置", status_code=409)
    return TmdbProvider(
        access_token=token,
        base_url=settings.tmdb_base_url,
        allowed_hosts=("api.themoviedb.org",),
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
        min_interval_seconds=settings.tmdb_min_interval_seconds,
        cache_ttl_seconds=settings.tmdb_cache_ttl_seconds,
        cache_max_entries=settings.tmdb_cache_max_entries,
        allow_future_episodes=settings.tmdb_allow_future_episodes,
        proxy=settings.outbound_proxy(),
        limiter=shared_rate_limiter("tmdb", settings.tmdb_min_interval_seconds),
    )


def build_pt_site(
    site_id: str,
    settings: Settings | None = None,
    *,
    allow_torrent_fetch: bool = False,
) -> PtSiteAdapter:
    settings = settings or get_settings()
    if site_id != "avistaz" or settings.pt_site_architecture != "avistaz":
        raise AppError("PT_SITE_UNSUPPORTED", "当前只支持已配置的 AvistaZ 站点", status_code=409)
    credentials = settings.avistaz_credentials()
    if credentials is None:
        raise AppError("PT_SITE_NOT_CONFIGURED", "AvistaZ 尚未完成配置", status_code=409)
    base_url = validate_external_url(settings.avistaz_base_url, settings.avistaz_allowed_hosts)
    hostname = urlparse(base_url).hostname
    if hostname is None:
        raise AppError("PT_SITE_URL_INVALID", "AvistaZ 地址无效", status_code=409)
    username, password, pid = credentials
    return AvistaZAdapter(
        username=username,
        password=password,
        pid=pid,
        base_url=base_url,
        allowed_hosts=(hostname.casefold(),),
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
        min_interval_seconds=settings.avistaz_min_interval_seconds,
        enable_torrent_fetch=allow_torrent_fetch,
        proxy=settings.outbound_proxy(),
        limiter=shared_rate_limiter(
            f"pt:{site_id}", settings.avistaz_min_interval_seconds
        ),
    )


def candidate_details_url(
    site_id: str,
    torrent_id: str,
    settings: Settings | None = None,
) -> str | None:
    settings = settings or get_settings()
    if site_id != "avistaz":
        return None
    base_url = validate_external_url(
        settings.avistaz_base_url,
        settings.avistaz_allowed_hosts,
    )
    return f"{base_url.rstrip('/')}/torrents/{quote(torrent_id, safe='')}"


def build_qb(settings: Settings | None = None) -> QbittorrentAdapter:
    settings = settings or get_settings()
    if not settings.enable_qb_write:
        raise AppError("QB_WRITE_DISABLED", "qBittorrent 下载提交尚未启用", status_code=409)
    credentials = settings.qb_credentials()
    if credentials is None or not settings.qb_allowed_hosts:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 尚未完成配置", status_code=409)
    base_url, username, password = credentials
    return QbittorrentAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=settings.qb_allowed_hosts,
        enable_write=True,
        allow_insecure_http=settings.qb_allow_insecure_http,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
    )


def build_qb_readonly(settings: Settings | None = None) -> QbittorrentAdapter:
    settings = settings or get_settings()
    credentials = settings.qb_credentials()
    if credentials is None or not settings.qb_allowed_hosts:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 尚未完成配置", status_code=409)
    base_url, username, password = credentials
    return QbittorrentAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=settings.qb_allowed_hosts,
        enable_write=False,
        allow_insecure_http=settings.qb_allow_insecure_http,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
    )


async def close_adapter(adapter: object) -> None:
    close = getattr(adapter, "aclose", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def sync_nextfind(session: AsyncSession, adapter: MediaSourceAdapter) -> tuple[int, int]:
    if _nextfind_sync_lock.locked():
        raise AppError(
            "NEXTFIND_SYNC_IN_PROGRESS",
            "缺失影视资源同步正在进行，请稍后再试",
            status_code=409,
            retryable=True,
        )
    async with _nextfind_sync_lock:
        return await _sync_nextfind_locked(session, adapter)


async def _sync_nextfind_locked(
    session: AsyncSession, adapter: MediaSourceAdapter
) -> tuple[int, int]:
    await adapter.authenticate()
    result = await adapter.list_missing_media()
    stored_items = list(
        await session.scalars(
            select(LibraryMediaItem).where(LibraryMediaItem.source == "nextfind")
        )
    )
    stored_by_source_id = {
        (item.source, item.source_item_id): item for item in stored_items
    }
    stored_episodes = list(
        await session.scalars(
            select(Episode)
            .join(LibraryMediaItem, Episode.media_id == LibraryMediaItem.id)
            .where(LibraryMediaItem.source == "nextfind")
        )
    )
    episodes_by_media: dict[str, list[Episode]] = {}
    for episode in stored_episodes:
        episodes_by_media.setdefault(episode.media_id, []).append(episode)

    seen: set[str] = set()
    discovered: list[tuple[LibraryMediaItem, list[str]]] = []
    created = 0
    updated = 0
    for source_item in result.items:
        seen.add(source_item.source_item_id)
        existing = stored_by_source_id.get(
            (source_item.source, source_item.source_item_id)
        )
        if existing is None:
            existing = LibraryMediaItem(
                source=source_item.source,
                source_item_id=source_item.source_item_id,
                media_type=source_item.media_type,
                title=source_item.title,
                discovered_at=source_item.discovered_at,
            )
            session.add(existing)
            created += 1
        else:
            updated += 1
        _apply_discovery_item(existing, source_item)
        discovered.append((existing, source_item.missing_episodes or []))

    # Assign IDs for all newly discovered media in one database round trip, then
    # reconcile episodes from the preloaded collection without per-item SELECTs.
    await session.flush()
    for media, missing_episodes in discovered:
        _replace_missing_episodes_from_collection(
            session,
            media.id,
            missing_episodes,
            episodes_by_media.get(media.id, []),
        )

    for stored_item in stored_items:
        if (
            stored_item.source_item_id not in seen
            and stored_item.state != MediaState.DOWNLOADING
        ):
            stored_item.state = MediaState.COMPLETE
            stored_item.attention_reason = None

    session.add(
        ActivityLog(
            event="NEXTFIND_SYNCED",
            message=f"NextFind 同步完成：新增 {created}，更新 {updated}",
            details={"created": created, "updated": updated, "warnings": len(result.warnings)},
        )
    )
    await session.commit()
    return created, updated


def _apply_discovery_item(target: LibraryMediaItem, item: MediaItemData) -> None:
    identity_changed = target.tmdb_id != item.tmdb_id
    active_state = target.state in {
        MediaState.SEARCHING,
        MediaState.CANDIDATES,
        MediaState.DOWNLOADING,
    }
    target.media_type = item.media_type
    target.tmdb_id = item.tmdb_id
    target.title = item.title
    target.original_title = (
        item.original_title
        if identity_changed or item.original_title is not None
        else target.original_title
    )
    if identity_changed:
        target.search_titles = []
        target.imdb_id = None
    target.country_codes = item.country_codes or []
    target.original_language = item.original_language
    target.year = item.year
    target.poster_path = item.poster_path
    if not active_state:
        target.state = MediaState.READY if item.tmdb_id is not None else MediaState.NEEDS_ATTENTION
        target.attention_reason = None if item.tmdb_id is not None else "需要确认 TMDB 影视信息"
    # ``updated_at`` is the local scheduling/fairness timestamp.  Replacing it
    # with the timestamp of every full discovery response made the same first
    # page of media look oldest on every cycle.  SQLAlchemy updates it naturally
    # when any persisted field above really changes.


async def _replace_missing_episodes(
    session: AsyncSession, media_id: str, episode_codes: list[str]
) -> None:
    existing = list(
        await session.scalars(select(Episode).where(Episode.media_id == media_id))
    )
    _replace_missing_episodes_from_collection(session, media_id, episode_codes, existing)


def _replace_missing_episodes_from_collection(
    session: AsyncSession,
    media_id: str,
    episode_codes: list[str],
    existing: list[Episode],
) -> None:
    existing_map = {(item.season_number, item.episode_number): item for item in existing}
    wanted: set[tuple[int, int]] = set()
    for code in episode_codes:
        match = _EPISODE_CODE.fullmatch(code)
        if match is None:
            continue
        key = (int(match[1]), int(match[2]))
        wanted.add(key)
        episode = existing_map.get(key)
        if episode is None:
            session.add(
                Episode(
                    media_id=media_id,
                    season_number=key[0],
                    episode_number=key[1],
                    state=EpisodeState.MISSING,
                )
            )
        elif episode.state != EpisodeState.DOWNLOADING:
            episode.state = EpisodeState.MISSING
    for key, episode in existing_map.items():
        if key not in wanted and episode.state != EpisodeState.DOWNLOADING:
            episode.state = EpisodeState.AVAILABLE


async def identify_media(
    session: AsyncSession,
    media_id: str,
    provider: MetadataProvider,
    *,
    tmdb_id: int | None = None,
) -> LibraryMediaItem:
    media = await session.get(LibraryMediaItem, media_id, with_for_update=True)
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
    requested_id = tmdb_id or media.tmdb_id
    if requested_id is None:
        matches = await provider.search(media.media_type, media.title, media.year)
        if len(matches) != 1:
            media.state = MediaState.NEEDS_ATTENTION
            media.attention_reason = "TMDB 搜索需要人工选择"
            await session.commit()
            raise AppError(
                "TMDB_SELECTION_REQUIRED",
                "找到多个 TMDB 结果，请指定 TMDB ID",
                status_code=409,
                details={
                    "candidates": [
                        {"tmdb_id": item.tmdb_id, "title": item.title, "year": item.year}
                        for item in matches[:5]
                    ]
                },
            )
        requested_id = matches[0].tmdb_id
    record = await provider.get_by_tmdb_id(media.media_type, requested_id)
    media.tmdb_id = record.tmdb_id
    media.imdb_id = record.imdb_id
    media.title = record.chinese_title or record.title
    media.original_title = record.original_title
    media.search_titles = _unique_search_titles(
        record.english_title,
        record.original_title,
        *record.aliases,
    )
    media.country_codes = record.country_codes or media.country_codes
    media.original_language = record.original_language or media.original_language
    media.year = record.year
    media.poster_path = record.poster_path
    media.state = MediaState.READY
    media.attention_reason = None
    session.add(
        ActivityLog(media_id=media.id, event="TMDB_IDENTIFIED", message="TMDB 影视信息已确认")
    )
    await session.commit()
    await session.refresh(media)
    return media


def _unique_search_titles(*values: str | None) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        title = " ".join(value.split())[:200]
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        titles.append(title)
    return titles


async def _enrich_media_search_titles(
    session: AsyncSession,
    media: LibraryMediaItem,
    metadata_factory: Callable[[], MetadataProvider] | None,
) -> None:
    if (
        (media.search_titles and media.imdb_id is not None)
        or media.tmdb_id is None
        or metadata_factory is None
    ):
        return

    provider: MetadataProvider | None = None
    try:
        provider = metadata_factory()
        record = await provider.get_by_tmdb_id(media.media_type, media.tmdb_id)
    except AppError:
        return
    finally:
        if provider is not None:
            await close_adapter(provider)

    media.search_titles = _unique_search_titles(
        record.english_title,
        record.original_title,
        *record.aliases,
    )
    media.imdb_id = record.imdb_id
    if media.original_title is None:
        media.original_title = record.original_title
    if media.original_language is None:
        media.original_language = record.original_language
    if not media.country_codes and record.country_codes:
        media.country_codes = record.country_codes
    if media.year is None:
        media.year = record.year
    if media.poster_path is None:
        media.poster_path = record.poster_path
    await session.commit()


async def _search_site_candidates(
    adapter: PtSiteAdapter,
    media: LibraryMediaItem,
    target_torrent_id: str | None = None,
) -> list[TorrentCandidate]:
    merged: dict[str, TorrentCandidate] = {}
    external_id_requests = [
        TorrentSearchRequest(
            tmdb=media.tmdb_id,
            type=media.media_type,
            limit=100,
        )
    ]
    if media.imdb_id:
        external_id_requests.append(
            TorrentSearchRequest(
                imdb=media.imdb_id,
                type=media.media_type,
                limit=100,
            )
        )
    for request in external_id_requests:
        by_external_id = await adapter.search(request)
        for candidate in by_external_id:
            merged.setdefault(candidate.torrent_id, candidate)
        if target_torrent_id is not None and target_torrent_id in merged:
            return list(merged.values())

    text_titles = _unique_search_titles(
        *media.search_titles,
        media.original_title,
        media.title,
    )
    for title in text_titles[:3]:
        by_title = await adapter.search(
            TorrentSearchRequest(
                search=title,
                type=media.media_type,
                limit=100,
            )
        )
        for candidate in by_title:
            merged.setdefault(candidate.torrent_id, candidate)
        if target_torrent_id is not None:
            if target_torrent_id in merged:
                break
        elif by_title or merged:
            break
    return list(merged.values())


async def run_release_search(
    session: AsyncSession,
    search_id: str,
    adapter_factory: Callable[[str], PtSiteAdapter],
    settings: Settings | None = None,
    metadata_factory: Callable[[], MetadataProvider] | None = None,
) -> ReleaseSearch:
    settings = settings or get_settings()
    search = await session.get(ReleaseSearch, search_id)
    if search is None:
        raise AppError("SEARCH_NOT_FOUND", "搜索记录不存在", status_code=404)
    media = await session.get(LibraryMediaItem, search.media_id)
    if media is None or media.tmdb_id is None:
        raise AppError("MEDIA_IDENTITY_REQUIRED", "请先确认 TMDB 影视信息", status_code=409)
    await _enrich_media_search_titles(session, media, metadata_factory)
    search.state = SearchState.RUNNING
    await session.commit()
    metadata = MetadataRecord(
        tmdb_id=media.tmdb_id,
        imdb_id=media.imdb_id,
        media_type=media.media_type,
        title=media.title,
        original_title=media.original_title,
        aliases=media.search_titles,
        year=media.year,
    )
    stored: list[ReleaseCandidate] = []
    try:
        for site_id in search.site_ids:
            adapter = adapter_factory(site_id)
            try:
                results = await _search_site_candidates(adapter, media)
            finally:
                await close_adapter(adapter)
            for raw in results:
                scored = score_torrent_candidate(
                    metadata,
                    raw,
                    missing_episodes=None,
                    preferences=MatchPreferences(
                        resolutions=settings.preferred_resolutions,
                        sources=settings.preferred_sources,
                        audio=settings.preferred_audio,
                        subtitles=settings.preferred_subtitles,
                        max_size_bytes=settings.max_candidate_size_bytes,
                    ),
                )
                stored.append(_candidate_from_adapter(search.id, scored))
        return await complete_search(session, search_id=search.id, candidates=stored)
    except Exception as exc:
        search.state = SearchState.FAILED
        search.error_message = exc.message if isinstance(exc, AppError) else "PT 搜索失败"
        search.finished_at = utc_now()
        media.state = MediaState.NEEDS_ATTENTION
        media.attention_reason = search.error_message
        await session.commit()
        raise


def _candidate_from_adapter(
    search_id: str, candidate: TorrentCandidate
) -> ReleaseCandidate:
    episode_coverage = (
        [f"S{candidate.season:02d}E{number:02d}" for number in candidate.episodes]
        if candidate.season is not None and candidate.episodes
        else []
    )
    season_coverage = [candidate.season] if candidate.season is not None else []
    return ReleaseCandidate(
        search_id=search_id,
        site_id=candidate.site_id,
        torrent_id=candidate.torrent_id,
        title=candidate.release_title,
        size_bytes=candidate.size_bytes,
        seeders=candidate.seeders,
        resolution=candidate.resolution,
        source=candidate.source,
        codec=candidate.codec,
        download_factor=candidate.download_factor,
        collection_type=candidate.collection_type,
        file_count=candidate.file_count,
        season_coverage=season_coverage,
        episode_coverage=episode_coverage,
        score=candidate.match_score or 0,
        reasons=candidate.match_reasons,
        warnings=candidate.warnings,
        info_hash=candidate.info_hash.casefold() if candidate.info_hash else None,
    )


async def _fetch_torrent_with_reference_refresh(
    pt: PtSiteAdapter,
    candidate: ReleaseCandidate,
    media: LibraryMediaItem,
) -> bytes:
    try:
        return await pt.fetch_torrent(candidate.torrent_id)
    except AppError as exc:
        if exc.error_code != "TORRENT_REFERENCE_NOT_IN_SESSION":
            raise

    refreshed = await _search_site_candidates(
        pt,
        media,
        target_torrent_id=candidate.torrent_id,
    )
    if any(item.torrent_id == candidate.torrent_id for item in refreshed):
        return await pt.fetch_torrent(candidate.torrent_id)

    raise AppError(
        "TORRENT_NO_LONGER_AVAILABLE",
        "站点中已找不到该候选资源，请重新搜索后选择其他资源",
        status_code=409,
    )


def _qb_download_tags(configured: tuple[str, ...], media_title: str) -> tuple[str, ...]:
    title_tag = " ".join(media_title.replace(",", "，").split())[:100]
    tags = list(configured)
    if not title_tag or any(tag.casefold() == title_tag.casefold() for tag in tags):
        return tuple(tags)
    if len(tags) >= 20:
        tags = tags[:19]
    tags.append(title_tag)
    return tuple(tags)


async def _submit_download_unlocked(
    session: AsyncSession,
    *,
    candidate_id: str,
    confirm_warnings: bool,
    pt_factory: Callable[[str], PtSiteAdapter],
    qb_factory: Callable[[], QbittorrentAdapter],
    on_download: Callable[[Download], Awaitable[None]] | None = None,
    write_guard: Callable[[Download], Awaitable[None]] | None = None,
    settings: Settings | None = None,
) -> Download:
    settings = settings or get_settings()
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
    candidate_search = await session.get(ReleaseSearch, candidate.search_id)
    if candidate_search is None:
        raise AppError("SEARCH_NOT_FOUND", "搜索记录不存在", status_code=404)
    target_media = await session.get(LibraryMediaItem, candidate_search.media_id)
    if target_media is None:
        raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)

    async def reuse_download(existing: Download) -> Download:
        if existing.media_id != candidate_search.media_id:
            message = "相同种子已关联其他影视条目，当前数据模型不能跨影视复用下载"
            if target_media is not None:
                target_media.state = MediaState.NEEDS_ATTENTION
                target_media.attention_reason = message
            await session.commit()
            raise AppError(
                "DUPLICATE_DOWNLOAD_OTHER_MEDIA",
                message,
                status_code=409,
                details={"existing_download_id": existing.id},
            )
        if target_media is not None:
            if existing.state in {DownloadState.COMPLETED, DownloadState.SEEDING}:
                target_media.state = MediaState.COMPLETE
                target_media.attention_reason = None
            elif existing.state in {DownloadState.ERROR, DownloadState.SUBMITTING}:
                target_media.state = MediaState.NEEDS_ATTENTION
            else:
                target_media.state = MediaState.DOWNLOADING
                target_media.attention_reason = None
        if on_download is not None:
            await on_download(existing)
        else:
            await session.commit()
        if existing.state == DownloadState.ERROR:
            if target_media is not None:
                target_media.attention_reason = "相同种子的已有下载记录处于错误状态"
                await session.commit()
            raise AppError(
                "DUPLICATE_DOWNLOAD_FAILED",
                "相同种子的已有下载记录处于错误状态，请先处理原任务",
                status_code=409,
            )
        if existing.state == DownloadState.SUBMITTING:
            if target_media is not None:
                target_media.attention_reason = "相同种子的已有提交尚未完成"
                await session.commit()
            raise AppError(
                "DUPLICATE_DOWNLOAD_SUBMITTING",
                "相同种子的已有提交尚未完成，请先同步或处理原任务",
                status_code=409,
            )
        return existing

    if candidate.info_hash is not None:
        duplicate = await session.scalar(
            select(Download).where(
                Download.info_hash == candidate.info_hash,
                Download.candidate_id != candidate.id,
            )
        )
        if duplicate is not None:
            return await reuse_download(duplicate)

    category = settings.qb_target_category or candidate.site_id
    save_path = settings.qb_target_save_path or None
    pt = pt_factory(candidate.site_id)
    try:
        qb = qb_factory()
        download: Download | None = None
        try:
            download = await queue_download(
                session, candidate_id=candidate_id, confirm_warnings=confirm_warnings
            )
            if download.state != DownloadState.SUBMITTING:
                if on_download is not None:
                    await on_download(download)
                return download
            payload = await _fetch_torrent_with_reference_refresh(pt, candidate, target_media)
            validated = validate_torrent(
                payload,
                expected_info_hash=candidate.info_hash,
                max_torrent_bytes=settings.torrent_max_bytes,
                max_files=settings.torrent_max_files,
            )
            info_hash = validated.info_hash_v1 or validated.info_hash_v2
            if info_hash is None:
                raise AppError(
                    "TORRENT_INFO_HASH_MISSING",
                    "种子文件缺少可用 info hash",
                    status_code=409,
                )
            duplicate = await session.scalar(
                select(Download).where(Download.info_hash == info_hash, Download.id != download.id)
            )
            if duplicate is not None:
                await session.delete(download)
                await session.commit()
                download = None
                return await reuse_download(duplicate)
            download.info_hash = info_hash
            download.name = validated.name
            download.content_size_bytes = validated.total_size_bytes
            await session.commit()

            await qb.authenticate()
            if on_download is not None:
                await on_download(download)

            async def guard_category_write() -> None:
                if write_guard is not None:
                    await write_guard(download)

            async def guard_torrent_add() -> None:
                await guard_category_write()
                download.submitted_at = utc_now()
                await session.commit()

            result = await qb.add_torrent(
                payload,
                expected_info_hash=info_hash,
                save_path=save_path,
                category=category,
                tags=_qb_download_tags(settings.qb_plan_tags, target_media.title),
                start_immediately=True,
                category_write_guard=guard_category_write,
                write_guard=guard_torrent_add,
            )
            download.state = DownloadState.QUEUED
            download.info_hash = result.info_hash
            await session.commit()
            await session.refresh(download)
            return download
        except AppError as exc:
            if download is None:
                raise
            download.state = (
                DownloadState.OUTCOME_UNKNOWN
                if exc.error_code == "QB_ADD_OUTCOME_UNKNOWN"
                else DownloadState.ERROR
            )
            download.error_message = exc.message
            if download.state == DownloadState.ERROR:
                media = await session.get(LibraryMediaItem, download.media_id)
                if media is not None:
                    media.state = MediaState.NEEDS_ATTENTION
                    media.attention_reason = exc.message
            await session.commit()
            raise
        finally:
            await close_adapter(qb)
    finally:
        await close_adapter(pt)


async def submit_download(
    session: AsyncSession,
    *,
    candidate_id: str,
    confirm_warnings: bool,
    pt_factory: Callable[[str], PtSiteAdapter],
    qb_factory: Callable[[], QbittorrentAdapter],
    on_download: Callable[[Download], Awaitable[None]] | None = None,
    write_guard: Callable[[Download], Awaitable[None]] | None = None,
    settings: Settings | None = None,
) -> Download:
    async with _download_submission_lock:
        return await _submit_download_unlocked(
            session,
            candidate_id=candidate_id,
            confirm_warnings=confirm_warnings,
            pt_factory=pt_factory,
            qb_factory=qb_factory,
            on_download=on_download,
            write_guard=write_guard,
            settings=settings,
        )


async def retry_download(
    session: AsyncSession,
    *,
    download_id: str,
    pt_factory: Callable[[str], PtSiteAdapter],
    qb_factory: Callable[[], QbittorrentAdapter],
    settings: Settings | None = None,
) -> Download:
    async with _download_submission_lock:
        existing = await session.get(Download, download_id)
        if existing is None:
            raise AppError("DOWNLOAD_NOT_FOUND", "下载记录不存在", status_code=404)
        if existing.state in {DownloadState.SUBMITTING, DownloadState.OUTCOME_UNKNOWN}:
            raise AppError(
                "DOWNLOAD_RETRY_UNSAFE",
                "提交结果尚未确认，请先同步下载状态后再处理",
                status_code=409,
            )
        if existing.state != DownloadState.ERROR:
            return existing
        return await _submit_download_unlocked(
            session,
            candidate_id=existing.candidate_id,
            confirm_warnings=True,
            pt_factory=pt_factory,
            qb_factory=qb_factory,
            settings=settings,
        )


async def sync_download_statuses(
    session: AsyncSession, qb: QbittorrentAdapter
) -> int:
    await qb.authenticate()
    torrents = await qb.list_torrents()
    by_hash = {
        identity: torrent
        for torrent in torrents
        for identity in torrent.identity_hashes
    }
    downloads = list(
        await session.scalars(
            select(Download).where(
                Download.state.not_in((DownloadState.COMPLETED, DownloadState.ERROR))
            )
        )
    )
    updated = 0
    now = utc_now()
    for download in downloads:
        if download.info_hash is None:
            continue
        torrent = by_hash.get(download.info_hash.casefold())
        if torrent is None:
            if await _release_vanished_download(session, download, now=now):
                updated += 1
            continue
        outcome_was_unknown = download.state == DownloadState.OUTCOME_UNKNOWN
        download.progress = torrent.progress
        download.download_speed = torrent.dlspeed
        download.upload_speed = torrent.upspeed
        download.ratio = max(0, torrent.ratio)
        download.state = _download_state(torrent.state, torrent.progress)
        download.error_message = None
        media = await session.get(LibraryMediaItem, download.media_id)
        if media is not None:
            if torrent.progress >= 1:
                media.state = MediaState.COMPLETE
                media.attention_reason = None
            elif download.state == DownloadState.ERROR:
                media.state = MediaState.NEEDS_ATTENTION
                media.attention_reason = "qBittorrent 下载任务异常"
            else:
                media.state = MediaState.DOWNLOADING
                media.attention_reason = None
        if outcome_was_unknown:
            jobs = list(
                await session.scalars(
                    select(AutomationJob).where(
                        AutomationJob.download_id == download.id,
                        AutomationJob.state == AutomationJobState.FAILED,
                        AutomationJob.superseded_at.is_(None),
                    )
                )
            )
            affected_run_ids: set[str] = set()
            for job in jobs:
                job.state = AutomationJobState.SUCCEEDED
                job.error_code = None
                job.error_message = None
                job.next_attempt_at = None
                job.finished_at = utc_now()
                affected_run_ids.add(job.run_id)
            for run_id in affected_run_ids:
                run = await refresh_automation_run_summary(session, run_id)
                if run is None or run.state in {
                    AutomationRunState.PENDING,
                    AutomationRunState.RUNNING,
                }:
                    continue
                run.state = (
                    AutomationRunState.FAILED
                    if run.failed_count
                    else AutomationRunState.SUCCEEDED
                )
                run.error_message = (
                    f"{run.failed_count} 个任务执行失败" if run.failed_count else None
                )
                run.finished_at = utc_now()
        updated += 1
    await session.commit()
    return updated


async def _release_vanished_download(
    session: AsyncSession, download: Download, *, now: datetime
) -> bool:
    """Close out a download that qBittorrent no longer knows about.

    Deleting the torrent in qB used to leave the download in ``DOWNLOADING``
    forever, and the media with it -- a state both ``create_search`` and
    ``run_automation`` refuse to act on, so the item could only be revived by
    editing the database.  A grace period keeps a torrent that was just handed
    to qB from being failed before qB has listed it.
    """

    reference = download.submitted_at or download.created_at
    if reference is not None:
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        if now - reference < _QB_MISSING_GRACE:
            return False

    finished = download.progress >= 1
    download.download_speed = 0
    download.upload_speed = 0
    if finished:
        # It completed and was then removed from the client; that is not an error.
        download.state = DownloadState.COMPLETED
        download.error_message = None
    else:
        download.state = DownloadState.ERROR
        download.error_message = "下载器中已不存在该任务"

    media = await session.get(LibraryMediaItem, download.media_id)
    if media is not None and media.state == MediaState.DOWNLOADING:
        if finished:
            media.state = MediaState.COMPLETE
            media.attention_reason = None
        else:
            media.state = MediaState.NEEDS_ATTENTION
            media.attention_reason = "qBittorrent 中已不存在该下载任务"

    if not finished:
        await _supersede_jobs_for_download(session, download)
    return True


async def _supersede_jobs_for_download(session: AsyncSession, download: Download) -> None:
    """Free the media from a failed job that points at a now-dead download.

    Releasing the media alone is not enough: ``run_automation`` also skips any
    media that still owns a live FAILED job, so the item would stay invisible.
    Superseding leaves the original run's recorded outcome untouched.
    """

    jobs = list(
        await session.scalars(
            select(AutomationJob).where(
                AutomationJob.download_id == download.id,
                AutomationJob.state == AutomationJobState.FAILED,
                AutomationJob.superseded_at.is_(None),
            )
        )
    )
    stamp = utc_now()
    for job in jobs:
        job.superseded_at = stamp


def _download_state(qb_state: str, progress: float) -> DownloadState:
    state = qb_state.casefold()
    if progress >= 1:
        return (
            DownloadState.SEEDING
            if state in {"uploading", "forcedup"}
            else DownloadState.COMPLETED
        )
    if "pause" in state or "stop" in state:
        return DownloadState.PAUSED
    if "error" in state or "missing" in state:
        return DownloadState.ERROR
    return DownloadState.DOWNLOADING
