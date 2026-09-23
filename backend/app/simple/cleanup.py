"""Reclaim disk space by deleting downloads whose files already reached the library.

The download directory and the media library hold two real copies of every
file, so once a title is in the library its downloaded copy is redundant and
can go -- but only after it has seeded long enough to satisfy the site, and
only once we are sure it really is in the library.

Three things make that safe:

- **The library signal is the NextFind missing list, not the media state.**
  ``MediaState.COMPLETE`` is set the moment a download reaches 100%, long
  before the file is imported; deleting on it would erase files the operator
  has not filed yet.  ``LibraryMediaItem.library_confirmed_at`` is written when
  NextFind stops reporting the title as missing, which is the real signal.
- **Seeding time is checked against the site's own hit-and-run rule.**  The
  policy floor and the site requirement are both applied, whichever is longer,
  and a site whose rule is unknown is never cleaned up at all.
- **Deletion happens in two steps.**  A due torrent is first tagged in
  qBittorrent and left seeding for a grace period; only then are the files
  removed.  Removing the tag by hand inside qBittorrent rescues a torrent for
  good, which gives the operator an override that needs no UNIN login.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.core.config import Settings, get_settings
from app.core.pt_site_rules import effective_hnr_rule
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.errors import AppError
from app.schemas.qbittorrent import QbTorrent
from app.simple.models import (
    ActivityLog,
    AutomationPolicy,
    Download,
    DownloadCleanupState,
    DownloadState,
    LibraryMediaItem,
    ReleaseCandidate,
)

_logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")

#: Tag written into qBittorrent while a torrent waits out its grace period.
#: Removing it by hand takes the torrent out of automatic cleanup for good.
CLEANUP_TAG = "unin-cleanup"

_FIRST_RUN_DELAY = timedelta(minutes=10)
_RUN_INTERVAL = timedelta(hours=6)
#: Downloads inspected per cycle.  The loop runs every few hours, so there is
#: no value in walking a huge library in one pass.
_SCAN_LIMIT = 500
_DAY_SECONDS = 86_400

_CLEANABLE_STATES = (DownloadState.COMPLETED, DownloadState.SEEDING)
_ACTIVE_CLEANUP_STATES = (DownloadCleanupState.NONE, DownloadCleanupState.MARKED)


def cross_seeded_with(torrent: QbTorrent, torrents: Sequence[QbTorrent]) -> bool:
    """Whether another torrent in the client points at the same content.

    Cross-seeding the same files to several trackers is normal on private
    sites.  Deleting the files under one of them breaks every other, and the
    other tracker's hit-and-run rule is exactly the one we may know nothing
    about -- so a shared payload is left alone entirely.
    """

    own = torrent.identity_hashes
    return any(
        other.identity_hashes.isdisjoint(own)
        and other.save_path == torrent.save_path
        and other.name == torrent.name
        for other in torrents
    )


@dataclass(frozen=True)
class CleanupCandidate:
    """One download paired with what the decision needs to know about it."""

    download: Download
    media: LibraryMediaItem
    site_id: str
    torrent: QbTorrent | None


@dataclass(frozen=True)
class CleanupDecision:
    eligible: bool
    reason: str | None = None
    required_seeding_days: int = 0
    cross_seeded: bool = False


@dataclass
class CleanupResult:
    dry_run: bool = False
    marked: int = 0
    deleted: int = 0
    held: int = 0
    unmarked: int = 0
    vanished: int = 0
    would_reclaim_bytes: int = 0
    reclaimed_bytes: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.marked + self.deleted + self.held + self.unmarked + self.vanished


@dataclass(frozen=True)
class CleanupPreviewItem:
    download_id: str
    media_title: str
    name: str
    size_bytes: int
    completed_at: datetime | None
    seeding_days: float
    required_seeding_days: int
    cleanup_state: DownloadCleanupState
    deletes_at: datetime | None
    blocked_reason: str | None


def _as_utc(value: datetime) -> datetime:
    # SQLite gives naive datetimes back even for timezone-aware columns.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _day_start(now: datetime) -> datetime:
    local = _as_utc(now).astimezone(_SHANGHAI)
    return datetime.combine(local.date(), datetime.min.time(), _SHANGHAI).astimezone(
        now.tzinfo
    )


def required_seeding_days(policy: AutomationPolicy, site_id: str) -> int | None:
    """Days a torrent must seed before deletion, or None when the site is unknown.

    The site's own hit-and-run rule wins whenever it is stricter than the
    policy floor.  An unknown rule returns None and the caller must refuse to
    clean up: guessing here is how an account gets banned.
    """

    rule = effective_hnr_rule(site_id, None)
    if not rule.known:
        return None
    site_days = rule.minimum_seeding_days or 0
    return max(policy.cleanup_min_seeding_days, site_days)


def evaluate(
    candidate: CleanupCandidate,
    policy: AutomationPolicy,
    *,
    now: datetime,
    torrents: Sequence[QbTorrent] = (),
) -> CleanupDecision:
    download = candidate.download
    torrent = candidate.torrent
    if torrent is None:
        return CleanupDecision(False, "qBittorrent 中已不存在该种子")
    if download.state not in _CLEANABLE_STATES:
        return CleanupDecision(False, "下载尚未完成")
    if download.completed_at is None:
        return CleanupDecision(False, "完成时间未知")

    retention = timedelta(days=policy.cleanup_after_days)
    if _as_utc(download.completed_at) + retention > now:
        return CleanupDecision(False, "尚未达到保留期")

    if policy.cleanup_require_library_confirmed:
        confirmed = candidate.media.library_confirmed_at
        if confirmed is None:
            return CleanupDecision(False, "尚未确认入库")
        # An older confirmation belongs to an earlier copy.  Re-downloading a
        # title in better quality leaves the media confirmed the whole time, so
        # "the media is in the library" would happily delete a fresh 4K file
        # that was never filed.  Only a confirmation observed after this
        # download finished says anything about this download.
        if _as_utc(confirmed) < _as_utc(download.completed_at):
            return CleanupDecision(False, "入库确认早于本次下载完成")

    needed = required_seeding_days(policy, candidate.site_id)
    if needed is None:
        return CleanupDecision(False, "站点 H&R 规则未知，拒绝清理")
    if torrent.seeding_time < needed * _DAY_SECONDS:
        return CleanupDecision(False, f"做种时长不足 {needed} 天", needed)

    if cross_seeded_with(torrent, torrents):
        return CleanupDecision(False, "同一份文件还在其它种子上辅种", needed, True)

    return CleanupDecision(True, None, needed)


def refresh_from_torrent(download: Download, torrent: QbTorrent) -> None:
    """Keep the mirrored counters current for downloads the status sync drops.

    ``sync_download_statuses`` skips anything already COMPLETED, and a seeding
    torrent that qBittorrent reports as ``stalledUP`` maps to exactly that, so
    the seeding counter would freeze near zero and the preview would show "0.0
    days seeded" for a torrent that has been seeding for weeks.  A completion
    time missed by the sync (the app was down when the torrent finished) is
    filled in from the client as well.  Downloads that predate this feature
    never get here: the migration marks them HELD.
    """

    download.seeding_seconds = max(0, torrent.seeding_time)
    if torrent.progress >= 1 and download.completed_at is None and torrent.completion_on > 0:
        download.completed_at = datetime.fromtimestamp(torrent.completion_on, tz=UTC)


def _torrent_tags(torrent: QbTorrent) -> set[str]:
    return {tag.strip() for tag in torrent.tags.split(",") if tag.strip()}


async def _load_candidates(
    session: AsyncSession, torrents: dict[str, QbTorrent]
) -> list[CleanupCandidate]:
    rows = await session.execute(
        select(Download, LibraryMediaItem, ReleaseCandidate.site_id)
        .join(LibraryMediaItem, LibraryMediaItem.id == Download.media_id)
        .join(ReleaseCandidate, ReleaseCandidate.id == Download.candidate_id)
        .where(
            Download.info_hash.is_not(None),
            Download.state.in_(_CLEANABLE_STATES),
            Download.cleanup_state.in_(_ACTIVE_CLEANUP_STATES),
        )
        .order_by(Download.completed_at.asc())
        .limit(_SCAN_LIMIT)
    )
    candidates: list[CleanupCandidate] = []
    for download, media, site_id in rows.tuples():
        info_hash = (download.info_hash or "").casefold()
        candidates.append(
            CleanupCandidate(
                download=download,
                media=media,
                site_id=site_id,
                torrent=torrents.get(info_hash),
            )
        )
    return candidates


async def _deleted_today(session: AsyncSession, now: datetime) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Download)
        .where(
            Download.cleanup_state == DownloadCleanupState.DELETED,
            Download.cleanup_deleted_at >= _day_start(now),
        )
    )
    return int(count or 0)


def _log(
    session: AsyncSession,
    candidate: CleanupCandidate,
    *,
    event: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        ActivityLog(
            media_id=candidate.media.id,
            event=event,
            message=message,
            details={
                "download_id": candidate.download.id,
                "info_hash": candidate.download.info_hash,
                **(details or {}),
            },
        )
    )


async def preview_cleanup(
    session: AsyncSession, torrents: Sequence[QbTorrent], *, now: datetime | None = None
) -> list[CleanupPreviewItem]:
    """What the next cycles would touch, so the operator can look before enabling."""

    now = now or utc_now()
    policy = await session.get(AutomationPolicy, "default")
    if policy is None:
        return []
    by_hash = {
        identity: torrent for torrent in torrents for identity in torrent.identity_hashes
    }
    items: list[CleanupPreviewItem] = []
    for candidate in await _load_candidates(session, by_hash):
        if candidate.torrent is not None:
            refresh_from_torrent(candidate.download, candidate.torrent)
        decision = evaluate(candidate, policy, now=now, torrents=torrents)
        download = candidate.download
        marked_at = download.cleanup_marked_at
        deletes_at = (
            _as_utc(marked_at) + timedelta(days=policy.cleanup_grace_days)
            if marked_at is not None
            else None
        )
        items.append(
            CleanupPreviewItem(
                download_id=download.id,
                media_title=candidate.media.title,
                name=download.name,
                size_bytes=(
                    candidate.torrent.size
                    if candidate.torrent is not None
                    else download.content_size_bytes or 0
                ),
                completed_at=download.completed_at,
                seeding_days=round(download.seeding_seconds / _DAY_SECONDS, 2),
                required_seeding_days=decision.required_seeding_days,
                cleanup_state=download.cleanup_state,
                deletes_at=deletes_at,
                blocked_reason=decision.reason,
            )
        )
    return items


async def run_cleanup_cycle(
    session: AsyncSession,
    *,
    readonly_factory: Callable[[], QbittorrentAdapter],
    write_factory: Callable[[], QbittorrentAdapter],
    settings: Settings | None = None,
    now: datetime | None = None,
) -> CleanupResult:
    settings = settings or get_settings()
    now = now or utc_now()
    policy = await session.get(AutomationPolicy, "default")
    if policy is None or not policy.cleanup_enabled:
        return CleanupResult(dry_run=True)

    # Without the separate delete authorisation -- or without ordinary write
    # access, which tagging needs -- the cycle still reports what it would do
    # but never touches qBittorrent.
    dry_run = (
        policy.cleanup_dry_run
        or not settings.enable_qb_delete
        or not settings.enable_qb_write
    )
    result = CleanupResult(dry_run=dry_run)

    readonly = readonly_factory()
    try:
        await readonly.authenticate()
        torrents = await readonly.list_torrents()
    finally:
        await _close(readonly)

    by_hash = {
        identity: torrent for torrent in torrents for identity in torrent.identity_hashes
    }
    candidates = await _load_candidates(session, by_hash)
    if not candidates:
        return result

    writer: QbittorrentAdapter | None = None

    async def ensure_writer() -> QbittorrentAdapter:
        nonlocal writer
        if writer is None:
            writer = write_factory()
            await writer.authenticate()
        return writer

    try:
        deleted_today = await _deleted_today(session, now)
        for candidate in candidates:
            download = candidate.download
            torrent = candidate.torrent

            # Gone from the client already: nothing left to delete.  The status
            # sync decides whether that was an error; here it only ends the
            # cleanup lifecycle so the row stops being rescanned.
            if torrent is None:
                if download.cleanup_state == DownloadCleanupState.MARKED:
                    # Not our doing, so it neither counts as reclaimed space nor
                    # spends today's delete budget.
                    download.cleanup_state = DownloadCleanupState.VANISHED
                    result.vanished += 1
                continue

            refresh_from_torrent(download, torrent)

            # The operator pulled the tag off inside qBittorrent.  That is the
            # manual override, and it is permanent for this download.
            if (
                download.cleanup_state == DownloadCleanupState.MARKED
                and CLEANUP_TAG not in _torrent_tags(torrent)
            ):
                download.cleanup_state = DownloadCleanupState.HELD
                download.cleanup_marked_at = None
                result.held += 1
                _log(
                    session,
                    candidate,
                    event="DOWNLOAD_CLEANUP_HELD",
                    message=f"{download.name} 的清理标记已被手动移除，不再自动清理",
                )
                continue

            decision = evaluate(candidate, policy, now=now, torrents=torrents)

            if not decision.eligible:
                # A marked torrent that no longer qualifies (the policy moved,
                # or it was re-reported as missing) goes back to untouched.
                if download.cleanup_state == DownloadCleanupState.MARKED:
                    if dry_run:
                        # Clearing the row while the tag stays on in qBittorrent
                        # would break the manual override: the operator removes
                        # a tag the next cycle no longer associates with
                        # anything, and the torrent quietly gets re-marked.
                        result.skipped.append(f"{download.name}: 演练，保持已标记状态")
                        continue
                    await (await ensure_writer()).remove_tags(
                        [download.info_hash or ""], [CLEANUP_TAG]
                    )
                    download.cleanup_state = DownloadCleanupState.NONE
                    download.cleanup_marked_at = None
                    result.unmarked += 1
                    _log(
                        session,
                        candidate,
                        event="DOWNLOAD_CLEANUP_UNMARKED",
                        message=f"{download.name} 已不符合清理条件：{decision.reason}",
                    )
                elif decision.reason:
                    result.skipped.append(f"{download.name}: {decision.reason}")
                continue

            if download.cleanup_state == DownloadCleanupState.NONE:
                if dry_run:
                    # Every eligible download would otherwise write a row every
                    # six hours; one summary per cycle is logged at the end.
                    result.would_reclaim_bytes += torrent.size
                else:
                    await (await ensure_writer()).add_tags(
                        [download.info_hash or ""], [CLEANUP_TAG]
                    )
                    download.cleanup_state = DownloadCleanupState.MARKED
                    download.cleanup_marked_at = now
                    _log(
                        session,
                        candidate,
                        event="DOWNLOAD_CLEANUP_MARKED",
                        message=(
                            f"{download.name} 已标记待清理，"
                            f"{policy.cleanup_grace_days} 天后删除"
                        ),
                        details={"size_bytes": torrent.size},
                    )
                result.marked += 1
                continue

            marked_at = download.cleanup_marked_at
            if marked_at is None:
                download.cleanup_marked_at = now
                continue
            if _as_utc(marked_at) + timedelta(days=policy.cleanup_grace_days) > now:
                continue

            if deleted_today >= policy.cleanup_daily_limit:
                result.skipped.append(f"{download.name}: 已达今日清理上限")
                continue

            if dry_run:
                result.would_reclaim_bytes += torrent.size
            else:
                # The listing was taken at the top of the cycle and a long run
                # can spend minutes below it.  Re-read this one torrent so a tag
                # pulled off in the meantime still rescues it.
                client = await ensure_writer()
                fresh = await client.find_torrents_by_hashes([download.info_hash or ""])
                current = next(iter(fresh), None)
                if current is None:
                    download.cleanup_state = DownloadCleanupState.VANISHED
                    result.vanished += 1
                    continue
                if CLEANUP_TAG not in _torrent_tags(current):
                    download.cleanup_state = DownloadCleanupState.HELD
                    download.cleanup_marked_at = None
                    result.held += 1
                    _log(
                        session,
                        candidate,
                        event="DOWNLOAD_CLEANUP_HELD",
                        message=f"{download.name} 的清理标记已被手动移除，不再自动清理",
                    )
                    continue
                await client.delete_torrents([download.info_hash or ""], delete_files=True)
                download.cleanup_state = DownloadCleanupState.DELETED
                download.cleanup_deleted_at = now
                # The torrent is gone from the client, so stop presenting it as
                # seeding.  The status sync would settle on the same state on
                # its next pass; doing it here avoids showing a stale one.
                download.state = DownloadState.COMPLETED
                download.download_speed = 0
                download.upload_speed = 0
                deleted_today += 1
                _log(
                    session,
                    candidate,
                    event="DOWNLOAD_CLEANUP_DELETED",
                    message=f"已删除 {download.name} 的种子和文件",
                    details={"size_bytes": torrent.size},
                )
                # Only a real delete reclaims anything; the dry run counts its
                # bytes in would_reclaim_bytes instead.
                result.reclaimed_bytes += torrent.size
            result.deleted += 1
        if dry_run and (result.marked or result.deleted):
            session.add(
                ActivityLog(
                    event="DOWNLOAD_CLEANUP_PREVIEW",
                    message=(
                        f"演练：本轮有 {result.marked + result.deleted} 项可清理，"
                        f"预计释放 {result.would_reclaim_bytes} 字节"
                    ),
                    details={
                        "markable": result.marked,
                        "deletable": result.deleted,
                        "would_reclaim_bytes": result.would_reclaim_bytes,
                    },
                )
            )
    finally:
        if writer is not None:
            await _close(writer)
        await session.commit()

    return result


async def _close(adapter: object) -> None:
    close = getattr(adapter, "aclose", None)
    if close is not None:
        await close()


async def _stopped_within(stop: asyncio.Event, delay: timedelta) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay.total_seconds())
    except TimeoutError:
        return False
    return True


async def download_cleanup_loop(
    stop: asyncio.Event,
    *,
    readonly_factory: Callable[[], QbittorrentAdapter],
    write_factory: Callable[[], QbittorrentAdapter],
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    delay = _FIRST_RUN_DELAY
    while not await _stopped_within(stop, delay):
        delay = _RUN_INTERVAL
        try:
            async with session_factory() as session:
                result = await run_cleanup_cycle(
                    session,
                    readonly_factory=readonly_factory,
                    write_factory=write_factory,
                )
        except AppError as exc:
            # A downloader that is offline or unconfigured is not worth a stack
            # trace; the next cycle tries again.
            _logger.info("Download cleanup skipped: %s", exc.message)
            continue
        except Exception:
            _logger.exception("Download cleanup cycle failed")
            continue
        if result.total:
            _logger.info("Download cleanup: %s", result)
