"""Download a pasted list of urgently wanted titles in one go.

Filling one title by hand is search, wait, pick, download; a list of thirty is
that thirty times.  This takes the list as text -- titles, TMDB ids or TMDB
links, one per line -- matches each line against the missing list, and then
runs the same quick fill the single-title button runs, one title after
another in the background.

A line is matched against every title NextFind has ever reported, missing or
not.  A missing one is downloaded as a matter of course.  One already in the
library is downloaded only when the operator ticks it: a list tends to include
titles its author forgot they had, and a second copy is not something to make
by accident.  Space reclaim never removes such a copy on its own -- its
library confirmation predates the download, so nothing shows the new file was
filed -- and the operator is told so.

The release still has to pass the policy's quality rules; what is waived is
the daily budget, which exists to pace unattended automation, not an explicit
request.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.core import audit
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.errors import AppError
from app.models.enums import MediaType
from app.simple import automation
from app.simple.integrations import (
    build_pt_site,
    build_qb,
    build_qb_readonly,
    build_tmdb,
    close_adapter,
)
from app.simple.models import (
    ActivityLog,
    Download,
    DownloadCleanupState,
    DownloadState,
    LibraryMediaItem,
    MediaState,
)

_logger = logging.getLogger(__name__)

MAX_LINES = 200
#: Shown for a line that matched several titles, or none exactly.
MAX_CHOICES = 6

_TMDB_LINK = re.compile(r"themoviedb\.org/(movie|tv)/(\d+)", re.IGNORECASE)
_TMDB_TAGGED = re.compile(
    r"^(?:tmdb[:：\s]*)?(movie|tv|电影|剧集|电视剧)?[:：\s]*(\d{1,9})$", re.IGNORECASE
)
_TRAILING_YEAR = re.compile(r"^(.*?)[\s（(\[]*((?:19|20)\d{2})[)）\]]?$")
_ACTIVE_DOWNLOAD_STATES = (
    DownloadState.SUBMITTING,
    DownloadState.QUEUED,
    DownloadState.DOWNLOADING,
    DownloadState.PAUSED,
    DownloadState.SEEDING,
    DownloadState.OUTCOME_UNKNOWN,
)

LineStatus = Literal[
    "MATCHED",  # one missing title; ready to download
    "AMBIGUOUS",  # several candidates; the operator picks
    "NOT_MISSING",  # known, but NextFind no longer reports it missing
    "DOWNLOADING",  # already has a download under way
    "NOT_FOUND",  # nothing on the missing list resembles it
    "DUPLICATE",  # the same title as an earlier line
]


@dataclass(frozen=True)
class ParsedLine:
    raw: str
    tmdb_id: int | None = None
    media_type: MediaType | None = None
    title: str | None = None
    year: int | None = None


@dataclass(frozen=True)
class MediaChoice:
    media_id: str
    title: str
    original_title: str | None
    year: int | None
    media_type: MediaType
    tmdb_id: int | None
    # NextFind does not report it missing: downloading it is a deliberate
    # second copy, not filling a gap.
    in_library: bool = False


@dataclass(frozen=True)
class MatchedLine:
    raw: str
    status: LineStatus
    media: MediaChoice | None = None
    choices: tuple[MediaChoice, ...] = ()
    note: str | None = None


def normalize_title(value: str) -> str:
    """Fold a title down to what two spellings of it have in common.

    Full-width and half-width forms, case, spacing and punctuation are where
    a pasted title usually differs from the stored one.
    """

    folded = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in folded if char.isalnum())


def parse_lines(text: str) -> list[ParsedLine]:
    lines: list[ParsedLine] = []
    for raw_line in text.splitlines():
        raw = raw_line.strip()
        if not raw or raw.startswith("#"):
            continue
        link = _TMDB_LINK.search(raw)
        if link is not None:
            lines.append(
                ParsedLine(
                    raw=raw,
                    tmdb_id=int(link.group(2)),
                    media_type=MediaType.MOVIE
                    if link.group(1).lower() == "movie"
                    else MediaType.TV,
                )
            )
            continue
        tagged = _TMDB_TAGGED.match(raw)
        if tagged is not None:
            kind = (tagged.group(1) or "").lower()
            lines.append(
                ParsedLine(
                    raw=raw,
                    tmdb_id=int(tagged.group(2)),
                    media_type=(
                        MediaType.MOVIE
                        if kind in {"movie", "电影"}
                        else MediaType.TV
                        if kind
                        else None
                    ),
                )
            )
            continue
        title, year = raw, None
        with_year = _TRAILING_YEAR.match(raw)
        if with_year is not None and normalize_title(with_year.group(1)):
            title, year = with_year.group(1).strip(), int(with_year.group(2))
        lines.append(ParsedLine(raw=raw, title=title, year=year))
    return lines[:MAX_LINES]


def _choice(media: LibraryMediaItem) -> MediaChoice:
    return MediaChoice(
        media_id=media.id,
        title=media.title,
        original_title=media.original_title,
        year=media.year,
        media_type=media.media_type,
        tmdb_id=media.tmdb_id,
        in_library=not _is_missing(media),
    )


def _is_missing(media: LibraryMediaItem) -> bool:
    return media.state != MediaState.COMPLETE and media.library_confirmed_at is None


def _names(media: LibraryMediaItem) -> set[str]:
    values = [media.title, media.original_title, *(media.search_titles or [])]
    return {normalize_title(value) for value in values if value} - {""}


async def match_lines(session: AsyncSession, lines: list[ParsedLine]) -> list[MatchedLine]:
    """Find the missing-list title each line means; changes nothing."""

    if not lines:
        return []
    library = list(
        await session.scalars(select(LibraryMediaItem).where(LibraryMediaItem.source == "nextfind"))
    )
    by_tmdb: dict[int, list[LibraryMediaItem]] = {}
    by_name: dict[str, list[LibraryMediaItem]] = {}
    for media in library:
        if media.tmdb_id is not None:
            by_tmdb.setdefault(media.tmdb_id, []).append(media)
        for name in _names(media):
            by_name.setdefault(name, []).append(media)
    downloading = set(
        await session.scalars(
            select(Download.media_id).where(Download.state.in_(_ACTIVE_DOWNLOAD_STATES))
        )
    )

    results: list[MatchedLine] = []
    taken: set[str] = set()
    for line in lines:
        exact = True
        found: list[LibraryMediaItem] = []
        if line.tmdb_id is not None:
            found = [
                media
                for media in by_tmdb.get(line.tmdb_id, [])
                if line.media_type is None or media.media_type == line.media_type
            ]
        if not found and (line.tmdb_id is None or line.raw.isdigit()):
            # A bare number that is nobody's TMDB id may be a title: "2046".
            # And the whole line is tried before its year is split off, or
            # "Blade Runner 2049" would be looked up as "Blade Runner", 2049.
            wanted = normalize_title(line.title or line.raw)
            found = list(by_name.get(normalize_title(line.raw), []))
            if not found:
                found = list(by_name.get(wanted, []))
                if line.year is not None and any(media.year == line.year for media in found):
                    found = [media for media in found if media.year == line.year]
            if not found and len(wanted) >= 2 and not line.raw.isdigit():
                # Nothing is spelt exactly like this; offer what contains it
                # and let the operator say which, rather than guess.
                exact = False
                seen: dict[str, LibraryMediaItem] = {}
                for name, group in by_name.items():
                    if wanted in name:
                        for media in group:
                            seen.setdefault(media.id, media)
                found = sorted(seen.values(), key=lambda media: len(media.title))

        if not found:
            results.append(MatchedLine(raw=line.raw, status="NOT_FOUND"))
            continue
        missing = [media for media in found if _is_missing(media)]
        # A missing title is what the line most likely means; one already in
        # the library is offered only when nothing missing fits.
        pool = missing or found
        if len(pool) > 1 or not exact:
            results.append(
                MatchedLine(
                    raw=line.raw,
                    status="AMBIGUOUS",
                    choices=tuple(_choice(media) for media in pool[:MAX_CHOICES]),
                    note=(None if exact else "没有完全同名的影视，下面是名称相近的"),
                )
            )
            continue
        media = pool[0]
        if media.id in taken:
            results.append(MatchedLine(raw=line.raw, status="DUPLICATE", media=_choice(media)))
            continue
        taken.add(media.id)
        if media.id in downloading or media.state == MediaState.DOWNLOADING:
            results.append(
                MatchedLine(
                    raw=line.raw,
                    status="DOWNLOADING",
                    media=_choice(media),
                    note="已经有下载任务在进行",
                )
            )
            continue
        if not missing:
            # Downloadable, but only on the operator's say-so: a list often
            # contains titles they did not realise they already had.
            results.append(
                MatchedLine(
                    raw=line.raw,
                    status="NOT_MISSING",
                    media=_choice(media),
                    note="库中已有。仍可下载，但这份下载不会被自动清理",
                )
            )
            continue
        results.append(MatchedLine(raw=line.raw, status="MATCHED", media=_choice(media)))
    return results


# ---------------------------------------------------------------------------
# Running a batch
# ---------------------------------------------------------------------------

ItemOutcome = Literal[
    "PENDING",
    "RUNNING",
    "DOWNLOADED",
    "ALREADY_PRESENT",
    "NO_CANDIDATE",
    "FAILED",
    "CANCELLED",
]


@dataclass
class BatchItem:
    media_id: str
    title: str
    outcome: ItemOutcome = "PENDING"
    message: str | None = None
    selected_title: str | None = None
    download_id: str | None = None
    in_library: bool = False


@dataclass
class ImportBatch:
    """The most recent bulk import.  One runs at a time, and like the library
    sync its progress lives in memory: a restart forgets it, and whatever was
    already submitted is in the downloads list regardless."""

    id: str = ""
    state: Literal["IDLE", "RUNNING", "FINISHED", "CANCELLED"] = "IDLE"
    started_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    items: list[BatchItem] = field(default_factory=list)
    cancel_requested: bool = False


_batch = ImportBatch()
_tasks: set[asyncio.Task[None]] = set()


def current_batch() -> ImportBatch:
    return replace(_batch, items=[replace(item) for item in _batch.items])


def batch_running() -> bool:
    return _batch.state == "RUNNING"


async def start_batch(
    session: AsyncSession,
    media_ids: list[str],
    *,
    existing_ids: list[str] | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> ImportBatch:
    """Start downloading ``media_ids``.

    ``existing_ids`` names the ones the operator asked for although they are
    in the library already.  A title that is not missing and is not named
    there left the missing list after it was matched, and is refused rather
    than quietly downloaded a second time.
    """

    global _batch
    agreed = set(existing_ids or [])
    if batch_running():
        raise AppError(
            "IMPORT_IN_PROGRESS",
            "已有一批导入正在下载，请等它结束",
            status_code=409,
            retryable=True,
        )
    wanted = list(dict.fromkeys(media_ids))
    rows = {
        media.id: media
        for media in await session.scalars(
            select(LibraryMediaItem).where(LibraryMediaItem.id.in_(wanted))
        )
    }
    items: list[BatchItem] = []
    for media_id in wanted:
        media = rows.get(media_id)
        if media is None:
            raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
        in_library = not _is_missing(media)
        if in_library and media.id not in agreed:
            raise AppError(
                "MEDIA_NOT_MISSING",
                f"《{media.title}》已不在缺失列表里，请重新匹配",
                status_code=409,
            )
        items.append(BatchItem(media_id=media.id, title=media.title, in_library=in_library))
    now = utc_now()
    _batch = ImportBatch(
        id=now.strftime("%Y%m%d-%H%M%S"),
        state="RUNNING",
        started_by=audit.current().actor,
        started_at=now,
        items=items,
    )
    # Started inside the request, so the task inherits who asked for it and
    # every download below is recorded under their name.
    task = asyncio.create_task(_run(_batch, session_factory))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return current_batch()


def request_cancel() -> ImportBatch:
    if batch_running():
        _batch.cancel_requested = True
    return current_batch()


async def cancel_import_tasks() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _why_nothing(rejected: list[dict[str, object]]) -> str:
    if not rejected:
        return "站点上没有搜到这部影视的资源"
    counts: dict[str, int] = {}
    for entry in rejected:
        reasons = entry.get("reasons")
        for reason in reasons if isinstance(reasons, list) else []:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    top = sorted(counts.items(), key=lambda pair: -pair[1])[:2]
    summary = "；".join(f"{reason}（{count} 个）" for reason, count in top)
    return f"搜到 {len(rejected)} 个资源，都不符合选种标准：{summary}"


async def retire_vanished_downloads(
    session: AsyncSession,
    media_ids: list[str],
    *,
    qb_factory: Callable[[], QbittorrentAdapter] = build_qb_readonly,
) -> int:
    """Stop download records whose torrent is gone from standing in for a file.

    Submitting a release that was downloaded once before reuses the old record
    instead of adding the torrent again -- right while the torrent is still
    there, and wrong once it has been removed by hand, when the title would be
    reported as downloaded with nothing on disk.  Each finished record for
    these titles is checked against the client, and one whose torrent is no
    longer there is marked VANISHED, which the duplicate check ignores.
    """

    records = list(
        await session.scalars(
            select(Download).where(
                Download.media_id.in_(media_ids),
                Download.info_hash.is_not(None),
                Download.state.in_((DownloadState.COMPLETED, DownloadState.SEEDING)),
                Download.cleanup_state.in_((DownloadCleanupState.NONE, DownloadCleanupState.HELD)),
            )
        )
    )
    if not records:
        return 0
    qb = qb_factory()
    try:
        await qb.authenticate()
        torrents = await qb.list_torrents()
    finally:
        await close_adapter(qb)
    present = {identity for torrent in torrents for identity in torrent.identity_hashes}
    retired = 0
    for record in records:
        if (record.info_hash or "").casefold() in present:
            continue
        record.cleanup_state = DownloadCleanupState.VANISHED
        record.cleanup_hold_reason = None
        # No longer seeding either, or the title would still count as having
        # a download under way and refuse a new one.
        record.state = DownloadState.COMPLETED
        record.download_speed = 0
        record.upload_speed = 0
        retired += 1
        session.add(
            ActivityLog(
                media_id=record.media_id,
                event="DOWNLOAD_RECORD_RETIRED",
                message=f"{record.name} 已不在 qBittorrent 中，旧下载记录不再用于判重",
                reason="再次下载前核对：这个种子此前已被手动移除",
                details={"download_id": record.id, "info_hash": record.info_hash},
            )
        )
    if retired:
        await session.commit()
    return retired


def _aware(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _run(batch: ImportBatch, session_factory: async_sessionmaker[AsyncSession]) -> None:
    try:
        try:
            with audit.scope(details={"import_batch": batch.id}):
                async with session_factory() as session:
                    await retire_vanished_downloads(
                        session, [item.media_id for item in batch.items]
                    )
        except AppError as exc:
            # Without the client there is no telling which records are stale.
            # The batch still runs; a release that turns out to be a reused
            # record is reported as such below instead of as a new download.
            _logger.info("Skipped the stale download check: %s", exc.message)
        for item in batch.items:
            if batch.cancel_requested:
                item.outcome = "CANCELLED"
                continue
            item.outcome = "RUNNING"
            try:
                with audit.scope(details={"import_batch": batch.id}):
                    async with session_factory() as session:
                        _, selected, rejected, download = await automation.quick_fill_media(
                            session,
                            media_id=item.media_id,
                            adapter_factory=lambda site_id: build_pt_site(
                                site_id, allow_torrent_fetch=False
                            ),
                            pt_factory=lambda site_id: build_pt_site(
                                site_id, allow_torrent_fetch=True
                            ),
                            qb_factory=build_qb,
                            metadata_factory=build_tmdb,
                            origin=(
                                "批量导入（库中已有，按要求再次下载）"
                                if item.in_library
                                else "批量导入"
                            ),
                        )
            except AppError as exc:
                item.outcome = "FAILED"
                item.message = exc.message
                continue
            except Exception:
                # One title failing in an unexpected way must not strand the
                # rest of the list.
                _logger.exception("Bulk import item failed")
                item.outcome = "FAILED"
                item.message = "下载过程出现未预期的错误"
                continue
            if selected is None or download is None:
                item.outcome = "NO_CANDIDATE"
                item.message = _why_nothing(rejected)
                continue
            item.selected_title = selected.title
            item.download_id = download.id
            if download.state in (DownloadState.ERROR, DownloadState.OUTCOME_UNKNOWN):
                item.outcome = "FAILED"
                item.message = download.error_message or "提交到 qBittorrent 的结果未确认"
            elif batch.started_at is not None and _aware(download.created_at) < batch.started_at:
                # An older record for the very same torrent: nothing was
                # submitted, and saying "downloaded" would be untrue.
                item.outcome = "ALREADY_PRESENT"
                item.message = "选中的种子此前已经下载过，下载记录还在，没有重复提交"
            else:
                item.outcome = "DOWNLOADED"
    except asyncio.CancelledError:
        # Shutting down: whatever was submitted stays submitted.
        for item in batch.items:
            if item.outcome in ("PENDING", "RUNNING"):
                item.outcome = "CANCELLED"
        batch.state = "CANCELLED"
        batch.finished_at = utc_now()
        raise
    batch.state = "CANCELLED" if batch.cancel_requested else "FINISHED"
    batch.finished_at = utc_now()
