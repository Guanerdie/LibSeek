from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import MediaSourceAdapter, MetadataProvider, PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.adapters.media_sources import NextFindAdapter
from app.adapters.metadata import TmdbProvider
from app.adapters.pt_sites import AvistaZAdapter
from app.core.config import Settings, get_settings
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
from app.simple.models import (
    ActivityLog,
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

_EPISODE_CODE = re.compile(r"^S(\d{2})E(\d{2,3})$")


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
    )


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
    await adapter.authenticate()
    result = await adapter.list_missing_media()
    seen: set[str] = set()
    created = 0
    updated = 0
    for source_item in result.items:
        seen.add(source_item.source_item_id)
        existing = await session.scalar(
            select(LibraryMediaItem).where(
                LibraryMediaItem.source == source_item.source,
                LibraryMediaItem.source_item_id == source_item.source_item_id,
            )
        )
        if existing is None:
            existing = LibraryMediaItem(
                source=source_item.source,
                source_item_id=source_item.source_item_id,
                media_type=source_item.media_type,
                title=source_item.title,
            )
            session.add(existing)
            created += 1
        else:
            updated += 1
        _apply_discovery_item(existing, source_item)
        await session.flush()
        await _replace_missing_episodes(
            session, existing.id, source_item.missing_episodes or []
        )

    old_items = await session.scalars(
        select(LibraryMediaItem).where(LibraryMediaItem.source == "nextfind")
    )
    for stored_item in old_items:
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
    target.media_type = item.media_type
    target.tmdb_id = item.tmdb_id
    target.title = item.title
    target.original_title = item.original_title
    target.country_codes = item.country_codes or []
    target.year = item.year
    target.poster_path = item.poster_path
    target.state = MediaState.READY if item.tmdb_id is not None else MediaState.NEEDS_ATTENTION
    target.attention_reason = None if item.tmdb_id is not None else "需要确认 TMDB 影视信息"
    target.discovered_at = item.discovered_at
    target.updated_at = item.updated_at


async def _replace_missing_episodes(
    session: AsyncSession, media_id: str, episode_codes: list[str]
) -> None:
    existing = list(
        await session.scalars(select(Episode).where(Episode.media_id == media_id))
    )
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
    media.title = record.chinese_title or record.title
    media.original_title = record.original_title
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


async def run_release_search(
    session: AsyncSession,
    search_id: str,
    adapter_factory: Callable[[str], PtSiteAdapter],
    settings: Settings | None = None,
) -> ReleaseSearch:
    settings = settings or get_settings()
    search = await session.get(ReleaseSearch, search_id)
    if search is None:
        raise AppError("SEARCH_NOT_FOUND", "搜索记录不存在", status_code=404)
    media = await session.get(LibraryMediaItem, search.media_id)
    if media is None or media.tmdb_id is None:
        raise AppError("MEDIA_IDENTITY_REQUIRED", "请先确认 TMDB 影视信息", status_code=409)
    search.state = SearchState.RUNNING
    await session.commit()
    episodes = list(await session.scalars(select(Episode).where(Episode.media_id == media.id)))
    missing = [
        f"S{item.season_number:02d}E{item.episode_number:02d}"
        for item in episodes
        if item.state == EpisodeState.MISSING
    ]
    metadata = MetadataRecord(
        tmdb_id=media.tmdb_id,
        media_type=media.media_type,
        title=media.title,
        original_title=media.original_title,
        year=media.year,
    )
    stored: list[ReleaseCandidate] = []
    try:
        for site_id in search.site_ids:
            adapter = adapter_factory(site_id)
            try:
                results = await adapter.search(
                    TorrentSearchRequest(
                        tmdb=media.tmdb_id,
                        type=media.media_type,
                        limit=100,
                    )
                )
                if not results:
                    results = await adapter.search(
                        TorrentSearchRequest(
                            search=media.original_title or media.title,
                            type=media.media_type,
                            limit=100,
                        )
                    )
            finally:
                await close_adapter(adapter)
            for raw in results:
                scored = score_torrent_candidate(
                    metadata,
                    raw,
                    missing_episodes=missing,
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
        season_coverage=season_coverage,
        episode_coverage=episode_coverage,
        score=candidate.match_score or 0,
        reasons=candidate.match_reasons,
        warnings=candidate.warnings,
        info_hash=candidate.info_hash.casefold() if candidate.info_hash else None,
    )


async def submit_download(
    session: AsyncSession,
    *,
    candidate_id: str,
    confirm_warnings: bool,
    pt_factory: Callable[[str], PtSiteAdapter],
    qb_factory: Callable[[], QbittorrentAdapter],
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
    category = settings.qb_target_category or candidate.site_id
    save_path = settings.qb_target_save_path or None
    qb = qb_factory()
    try:
        download = await queue_download(
            session, candidate_id=candidate_id, confirm_warnings=confirm_warnings
        )
        if download.state != DownloadState.SUBMITTING:
            return download
        pt = pt_factory(candidate.site_id)
        try:
            payload = await pt.fetch_torrent(candidate.torrent_id)
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
                return duplicate
            download.info_hash = info_hash
            download.name = validated.name
            await session.commit()

            await qb.authenticate()
            result = await qb.add_torrent(
                payload,
                expected_info_hash=info_hash,
                save_path=save_path,
                category=category,
                tags=settings.qb_plan_tags,
                start_immediately=True,
            )
            download.state = DownloadState.QUEUED
            download.info_hash = result.info_hash
            await session.commit()
            await session.refresh(download)
            return download
        except AppError as exc:
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
            await close_adapter(pt)
    finally:
        await close_adapter(qb)


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
    for download in downloads:
        if download.info_hash is None:
            continue
        torrent = by_hash.get(download.info_hash.casefold())
        if torrent is None:
            continue
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
        updated += 1
    await session.commit()
    return updated


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
