"""Space reclaim: only delete what is genuinely safe to delete."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.core import audit
from app.core.config import Settings
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.qbittorrent import QbTorrent
from app.simple import cleanup
from app.simple.automation import get_policy
from app.simple.cleanup import CLEANUP_TAG, run_cleanup_cycle
from app.simple.models import (
    ActivityLog,
    AutomationPolicy,
    Download,
    DownloadCleanupState,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
)

_DAY = 86_400
_tmdb_ids = itertools.count(9000)


class RecordingQb:
    """Stands in for both the read-only and the write adapter."""

    def __init__(
        self, torrents: list[QbTorrent], *, on_recheck: list[QbTorrent] | None = None
    ) -> None:
        self.torrents = torrents
        # What the pre-delete re-read sees, when it differs from the snapshot.
        self.on_recheck = on_recheck
        self.tagged: list[tuple[list[str], list[str]]] = []
        self.untagged: list[tuple[list[str], list[str]]] = []
        self.deleted: list[tuple[list[str], bool]] = []
        self.closed = False

    async def authenticate(self) -> None:
        return None

    async def list_torrents(self) -> list[QbTorrent]:
        return self.torrents

    async def find_torrents_by_hashes(self, hashes: Sequence[str]) -> list[QbTorrent]:
        pool = self.torrents if self.on_recheck is None else self.on_recheck
        wanted = {value.casefold() for value in hashes}
        return [t for t in pool if t.identity_hashes & wanted]

    async def add_tags(self, hashes: Sequence[str], tags: Sequence[str]) -> None:
        self.tagged.append((list(hashes), list(tags)))

    async def remove_tags(self, hashes: Sequence[str], tags: Sequence[str]) -> None:
        self.untagged.append((list(hashes), list(tags)))

    async def delete_torrents(self, hashes: Sequence[str], *, delete_files: bool) -> None:
        self.deleted.append((list(hashes), delete_files))

    async def aclose(self) -> None:
        self.closed = True


def _settings(*, enable_delete: bool = True, enable_write: bool = True) -> Settings:
    return Settings(
        _env_file=None, enable_qb_write=enable_write, enable_qb_delete=enable_delete
    )


def _torrent(
    info_hash: str,
    *,
    seeding_days: float,
    tags: str = "",
    size: int = 20_000,
) -> QbTorrent:
    # Distinct names per hash: two torrents sharing name and save path are what
    # cross-seed detection looks for, and would silently disqualify each other.
    return QbTorrent(
        hash=info_hash,
        name=f"Example-{info_hash[:8]}.mkv",
        size=size,
        progress=1,
        ratio=1.5,
        state="uploading",
        seeding_time=int(seeding_days * _DAY),
        tags=tags,
    )


async def _seed(
    session: AsyncSession,
    *,
    source_item_id: str,
    info_hash: str,
    completed_days_ago: float = 30,
    seeding_seconds: int = 30 * _DAY,
    library_confirmed: bool = True,
    site_id: str = "avistaz",
    cleanup_state: DownloadCleanupState = DownloadCleanupState.NONE,
    marked_days_ago: float | None = None,
) -> Download:
    now = utc_now()
    media = LibraryMediaItem(
        source_item_id=source_item_id,
        media_type=MediaType.MOVIE,
        tmdb_id=next(_tmdb_ids),
        title="Example Movie",
        state=MediaState.COMPLETE,
        library_confirmed_at=now - timedelta(days=20) if library_confirmed else None,
    )
    session.add(media)
    await session.flush()
    search = ReleaseSearch(media_id=media.id, site_ids=[site_id])
    session.add(search)
    await session.flush()
    candidate = ReleaseCandidate(
        search_id=search.id,
        site_id=site_id,
        torrent_id=f"t-{source_item_id}",
        title="Example.Movie.1080p.WEB-DL",
        score=0.9,
        reasons=[],
        warnings=[],
        info_hash=info_hash,
    )
    session.add(candidate)
    await session.flush()
    download = Download(
        media_id=media.id,
        candidate_id=candidate.id,
        name="Example.mkv",
        state=DownloadState.SEEDING,
        progress=1,
        info_hash=info_hash,
        submitted_at=now - timedelta(days=40),
        completed_at=now - timedelta(days=completed_days_ago),
        seeding_seconds=seeding_seconds,
        cleanup_state=cleanup_state,
        cleanup_marked_at=(
            now - timedelta(days=marked_days_ago) if marked_days_ago is not None else None
        ),
    )
    session.add(download)
    await session.commit()
    return download


async def _enable(session: AsyncSession, **overrides: object) -> AutomationPolicy:
    policy = await get_policy(session)
    policy.cleanup_enabled = True
    policy.cleanup_dry_run = False
    for key, value in overrides.items():
        setattr(policy, key, value)
    await session.commit()
    return policy


async def _run(
    session: AsyncSession,
    qb: RecordingQb,
    *,
    enable_delete: bool = True,
    enable_write: bool = True,
) -> cleanup.CleanupResult:
    return await run_cleanup_cycle(
        session,
        readonly_factory=lambda: cast(QbittorrentAdapter, qb),
        write_factory=lambda: cast(QbittorrentAdapter, qb),
        settings=_settings(enable_delete=enable_delete, enable_write=enable_write),
    )


@pytest.mark.asyncio
async def test_defaults_are_ten_days_and_off(session_factory) -> None:
    async with session_factory() as session:
        policy = await get_policy(session)
        assert policy.cleanup_enabled is False
        assert policy.cleanup_dry_run is True
        assert policy.cleanup_after_days == 10
        assert policy.cleanup_min_seeding_days == 10


@pytest.mark.asyncio
async def test_not_confirmed_in_library_is_left_alone(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="unconfirmed", info_hash="a" * 40, library_confirmed=False
        )
        await _enable(session)
        qb = RecordingQb([_torrent("a" * 40, seeding_days=30)])

        result = await _run(session, qb)
        await session.refresh(download)

        assert qb.tagged == []
        assert qb.deleted == []
        assert result.marked == 0
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_seeding_below_the_site_rule_is_left_alone(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(session, source_item_id="short-seed", info_hash="b" * 40)
        # A policy floor lower than AvistaZ's seven days must not win.
        await _enable(session, cleanup_min_seeding_days=1)
        qb = RecordingQb([_torrent("b" * 40, seeding_days=3)])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.tagged == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_unknown_site_rule_refuses_cleanup(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="unknown-site", info_hash="c" * 40, site_id="somept"
        )
        await _enable(session)
        qb = RecordingQb([_torrent("c" * 40, seeding_days=365)])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.tagged == []
        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_retention_period_not_reached(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="too-fresh", info_hash="d" * 40, completed_days_ago=4
        )
        await _enable(session)
        qb = RecordingQb([_torrent("d" * 40, seeding_days=30)])

        await _run(session, qb)
        await session.refresh(download)

        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_dry_run_touches_nothing(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(session, source_item_id="dry", info_hash="e" * 40)
        await _enable(session, cleanup_dry_run=True)
        qb = RecordingQb([_torrent("e" * 40, seeding_days=30)])

        result = await _run(session, qb)
        await session.refresh(download)

        assert result.dry_run is True
        assert result.marked == 1
        assert qb.tagged == []
        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_delete_authorisation_missing_blocks_a_due_deletion(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="unauthorised",
            info_hash="f" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb([_torrent("f" * 40, seeding_days=30, tags=CLEANUP_TAG)])

        result = await _run(session, qb, enable_delete=False)
        await session.refresh(download)

        assert result.dry_run is True
        assert qb.deleted == [], "ENABLE_QB_DELETE=false must stop a due deletion"
        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_write_authorisation_missing_blocks_a_due_deletion(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="no-write",
            info_hash="fe" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb([_torrent("fe" * 20, seeding_days=30, tags=CLEANUP_TAG)])

        result = await _run(session, qb, enable_write=False)
        await session.refresh(download)

        assert result.dry_run is True
        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_eligible_download_is_tagged_first(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(session, source_item_id="markable", info_hash="1" * 40)
        await _enable(session)
        qb = RecordingQb([_torrent("1" * 40, seeding_days=30)])

        result = await _run(session, qb)
        await session.refresh(download)

        assert result.marked == 1
        assert qb.tagged == [(["1" * 40], [CLEANUP_TAG])]
        assert qb.deleted == [], "the grace period must pass before anything is deleted"
        assert download.cleanup_state == DownloadCleanupState.MARKED
        assert download.cleanup_marked_at is not None


@pytest.mark.asyncio
async def test_grace_period_over_deletes_with_files(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="deletable",
            info_hash="2" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb([_torrent("2" * 40, seeding_days=30, tags=CLEANUP_TAG, size=50_000)])

        result = await _run(session, qb)
        await session.refresh(download)

        assert qb.deleted == [(["2" * 40], True)]
        assert download.cleanup_state == DownloadCleanupState.DELETED
        assert download.cleanup_deleted_at is not None
        assert result.reclaimed_bytes == 50_000


@pytest.mark.asyncio
async def test_grace_period_still_running(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="still-waiting",
            info_hash="3" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=0.5,
        )
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb([_torrent("3" * 40, seeding_days=30, tags=CLEANUP_TAG)])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_tag_removed_by_hand_holds_the_download_forever(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="rescued",
            info_hash="4" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb([_torrent("4" * 40, seeding_days=30, tags="other")])

        result = await _run(session, qb)
        await session.refresh(download)

        assert result.held == 1
        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.HELD

        # A later cycle must keep its hands off it.
        qb2 = RecordingQb([_torrent("4" * 40, seeding_days=60, tags="other")])
        await _run(session, qb2)
        await session.refresh(download)
        assert qb2.tagged == []
        assert download.cleanup_state == DownloadCleanupState.HELD


@pytest.mark.asyncio
async def test_policy_change_unmarks_a_pending_download(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="no-longer-due",
            info_hash="5" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_min_seeding_days=90)
        qb = RecordingQb([_torrent("5" * 40, seeding_days=30, tags=CLEANUP_TAG)])

        result = await _run(session, qb)
        await session.refresh(download)

        assert result.unmarked == 1
        assert qb.untagged == [(["5" * 40], [CLEANUP_TAG])]
        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_daily_limit_defers_the_rest(session_factory) -> None:
    async with session_factory() as session:
        first = await _seed(
            session,
            source_item_id="limit-a",
            info_hash="6" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        second = await _seed(
            session,
            source_item_id="limit-b",
            info_hash="7" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_grace_days=2, cleanup_daily_limit=1)
        qb = RecordingQb(
            [
                _torrent("6" * 40, seeding_days=30, tags=CLEANUP_TAG),
                _torrent("7" * 40, seeding_days=30, tags=CLEANUP_TAG),
            ]
        )

        await _run(session, qb)
        await session.refresh(first)
        await session.refresh(second)

        states = {first.cleanup_state, second.cleanup_state}
        assert states == {DownloadCleanupState.DELETED, DownloadCleanupState.MARKED}
        assert len(qb.deleted) == 1


@pytest.mark.asyncio
async def test_torrent_already_gone_closes_the_lifecycle(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="already-gone",
            info_hash="8" * 40,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session)
        qb = RecordingQb([])

        result = await _run(session, qb)
        await session.refresh(download)

        assert qb.deleted == []
        assert download.cleanup_state == DownloadCleanupState.VANISHED
        assert download.cleanup_deleted_at is None, "no reclaim was performed"
        assert result.deleted == 0
        assert result.vanished == 1


@pytest.mark.asyncio
async def test_disabled_policy_does_nothing(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(session, source_item_id="off", info_hash="9" * 40)
        # The policy row must exist and be eligible in every way except the
        # switch, otherwise this passes for the wrong reason.
        policy = await get_policy(session)
        policy.cleanup_enabled = False
        policy.cleanup_dry_run = False
        await session.commit()
        qb = RecordingQb([_torrent("9" * 40, seeding_days=30)])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.tagged == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_preview_explains_why_something_is_blocked(session_factory) -> None:
    async with session_factory() as session:
        await _seed(
            session, source_item_id="blocked", info_hash="0" * 40, library_confirmed=False
        )
        await _enable(session)
        torrents = [_torrent("0" * 40, seeding_days=30)]

        items = await cleanup.preview_cleanup(session, torrents)

        assert len(items) == 1
        assert items[0].blocked_reason == "尚未确认入库"
        assert items[0].size_bytes == 20_000


@pytest.mark.asyncio
async def test_required_days_takes_the_stricter_of_policy_and_site(session_factory) -> None:
    async with session_factory() as session:
        policy = await get_policy(session)
        policy.cleanup_min_seeding_days = 3
        assert cleanup.required_seeding_days(policy, "avistaz") == 7
        policy.cleanup_min_seeding_days = 14
        assert cleanup.required_seeding_days(policy, "avistaz") == 14
        assert cleanup.required_seeding_days(policy, "somept") is None


@pytest.mark.asyncio
async def test_naive_timestamps_from_sqlite_are_handled(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(session, source_item_id="naive", info_hash="ab" * 20)
        download.completed_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
        await session.commit()
        await _enable(session)
        qb = RecordingQb([_torrent("ab" * 20, seeding_days=30)])

        await _run(session, qb)
        await session.refresh(download)

        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_confirmation_older_than_this_download_blocks_cleanup(session_factory) -> None:
    """A re-download of an already-filed title is not itself filed."""

    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="upgraded", info_hash="1a" * 20, completed_days_ago=15
        )
        # The library was confirmed 20 days ago, before this newer copy landed.
        await _enable(session)
        qb = RecordingQb([_torrent("1a" * 20, seeding_days=30)])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.tagged == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_confirmation_after_completion_allows_cleanup(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="filed-after", info_hash="2a" * 20, completed_days_ago=30
        )
        await _enable(session)
        qb = RecordingQb([_torrent("2a" * 20, seeding_days=30)])

        await _run(session, qb)
        await session.refresh(download)

        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_cross_seeded_files_are_never_deleted(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(session, source_item_id="cross", info_hash="3a" * 20)
        await _enable(session)
        mine = _torrent("3a" * 20, seeding_days=30)
        sibling = QbTorrent(
            hash="4a" * 20,
            name=mine.name,
            size=mine.size,
            progress=1,
            ratio=1,
            state="uploading",
            seeding_time=mine.seeding_time,
            save_path=mine.save_path,
        )
        qb = RecordingQb([mine, sibling])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.tagged == []
        assert download.cleanup_state == DownloadCleanupState.NONE


@pytest.mark.asyncio
async def test_dry_run_keeps_a_marked_download_marked(session_factory) -> None:
    """Clearing the row while the tag stays on would break the manual override."""

    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="dry-unmark",
            info_hash="5a" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_dry_run=True, cleanup_min_seeding_days=90)
        qb = RecordingQb([_torrent("5a" * 20, seeding_days=30, tags=CLEANUP_TAG)])

        await _run(session, qb)
        await session.refresh(download)

        assert qb.untagged == []
        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_tag_pulled_after_the_snapshot_still_rescues_the_torrent(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="late-rescue",
            info_hash="6a" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb(
            [_torrent("6a" * 20, seeding_days=30, tags=CLEANUP_TAG)],
            on_recheck=[_torrent("6a" * 20, seeding_days=30, tags="")],
        )

        result = await _run(session, qb)
        await session.refresh(download)

        assert qb.deleted == []
        assert result.held == 1
        assert download.cleanup_state == DownloadCleanupState.HELD


@pytest.mark.asyncio
async def test_seeding_counter_is_refreshed_for_completed_downloads(session_factory) -> None:
    """The status sync skips COMPLETED rows, so the preview would show 0 days."""

    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="stalled", info_hash="7a" * 20, seeding_seconds=0
        )
        download.state = DownloadState.COMPLETED
        await session.commit()
        await _enable(session)

        items = await cleanup.preview_cleanup(
            session, [_torrent("7a" * 20, seeding_days=25)]
        )

        assert items[0].seeding_days == 25.0
        assert items[0].blocked_reason is None


@pytest.mark.asyncio
async def test_missing_completion_time_is_taken_from_the_client(session_factory) -> None:
    """The app was down when the torrent finished, so the sync never saw it."""

    async with session_factory() as session:
        download = await _seed(session, source_item_id="missed-finish", info_hash="8a" * 20)
        download.completed_at = None
        await session.commit()
        await _enable(session)
        torrent = _torrent("8a" * 20, seeding_days=30)
        torrent = torrent.model_copy(
            update={
                "completion_on": int(
                    (utc_now() - timedelta(days=30)).timestamp()
                )
            }
        )
        qb = RecordingQb([torrent])

        await _run(session, qb)
        await session.refresh(download)

        assert download.completed_at is not None
        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_naive_marked_at_is_handled(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="naive-marked",
            info_hash="9a" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
        )
        download.cleanup_marked_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5)
        await session.commit()
        await _enable(session, cleanup_grace_days=2)
        qb = RecordingQb([_torrent("9a" * 20, seeding_days=30, tags=CLEANUP_TAG)])

        await _run(session, qb)
        await session.refresh(download)

        assert download.cleanup_state == DownloadCleanupState.DELETED


@pytest.mark.asyncio
async def test_held_download_is_neither_cleaned_nor_previewed(session_factory) -> None:
    """Every download that predates the upgrade starts out HELD."""

    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="backlog",
            info_hash="bb" * 20,
            cleanup_state=DownloadCleanupState.HELD,
        )
        await _enable(session)
        torrent = _torrent("bb" * 20, seeding_days=60, tags=CLEANUP_TAG)
        qb = RecordingQb([torrent])

        result = await _run(session, qb)
        items = await cleanup.preview_cleanup(session, [torrent])
        await session.refresh(download)

        assert qb.tagged == [] and qb.deleted == []
        assert result.total == 0
        assert items == []
        assert download.cleanup_state == DownloadCleanupState.HELD


# ---------------------------------------------------------------------------
# Library confirmation is refreshed before anything is decided
# ---------------------------------------------------------------------------


async def _run_with_refresh(
    session: AsyncSession,
    qb: RecordingQb,
    refresher: Callable[[AsyncSession], Awaitable[None]],
) -> cleanup.CleanupResult:
    return await run_cleanup_cycle(
        session,
        readonly_factory=lambda: cast(QbittorrentAdapter, qb),
        write_factory=lambda: cast(QbittorrentAdapter, qb),
        library_refresher=refresher,
        settings=_settings(),
    )


@pytest.mark.asyncio
async def test_failed_library_refresh_stops_the_whole_cycle(session_factory) -> None:
    """Stale confirmations must never be acted on."""

    async with session_factory() as session:
        due = await _seed(
            session,
            source_item_id="due",
            info_hash="c1" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        fresh = await _seed(session, source_item_id="fresh", info_hash="c2" * 20)
        await _enable(session)
        qb = RecordingQb(
            [
                _torrent("c1" * 20, seeding_days=30, tags=CLEANUP_TAG),
                _torrent("c2" * 20, seeding_days=30),
            ]
        )

        async def refresher(_session: AsyncSession) -> None:
            raise AppError("NEXTFIND_UNAVAILABLE", "NextFind 无法连接", status_code=502)

        result = await _run_with_refresh(session, qb, refresher)
        await session.refresh(due)
        await session.refresh(fresh)
        skipped = (
            await session.scalars(
                select(ActivityLog).where(ActivityLog.event == "DOWNLOAD_CLEANUP_SKIPPED")
            )
        ).all()

        assert qb.tagged == [] and qb.deleted == []
        assert result.total == 0
        assert due.cleanup_state == DownloadCleanupState.MARKED
        assert fresh.cleanup_state == DownloadCleanupState.NONE
        assert len(skipped) == 1
        assert "NextFind 无法连接" in skipped[0].message


@pytest.mark.asyncio
async def test_library_is_refreshed_before_deciding(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session, source_item_id="late-confirm", info_hash="c3" * 20, library_confirmed=False
        )
        await _enable(session)
        qb = RecordingQb([_torrent("c3" * 20, seeding_days=30)])
        calls = 0

        async def refresher(refresh_session: AsyncSession) -> None:
            # What a NextFind sync does when the title has left the missing list.
            nonlocal calls
            calls += 1
            media = await refresh_session.get(LibraryMediaItem, download.media_id)
            assert media is not None
            media.library_confirmed_at = utc_now()
            await refresh_session.commit()

        result = await _run_with_refresh(session, qb, refresher)
        await session.refresh(download)

        assert calls == 1
        assert result.marked == 1
        assert download.cleanup_state == DownloadCleanupState.MARKED


@pytest.mark.asyncio
async def test_idle_cycle_does_not_refresh_the_library(session_factory) -> None:
    """Held, deleted and unfinished downloads give the cycle nothing to decide."""

    async with session_factory() as session:
        await _seed(
            session,
            source_item_id="held",
            info_hash="c4" * 20,
            cleanup_state=DownloadCleanupState.HELD,
        )
        await _enable(session)
        qb = RecordingQb([_torrent("c4" * 20, seeding_days=30)])
        calls = 0

        async def refresher(_session: AsyncSession) -> None:
            nonlocal calls
            calls += 1

        await _run_with_refresh(session, qb, refresher)

        assert calls == 0


# ---------------------------------------------------------------------------
# Every step leaves a record of who did it and why
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marking_and_deleting_record_actor_and_reason(session_factory) -> None:
    async with session_factory() as session:
        marked = await _seed(session, source_item_id="audit-mark", info_hash="d1" * 20)
        deleted = await _seed(
            session,
            source_item_id="audit-delete",
            info_hash="d2" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=5,
        )
        await _enable(session)
        qb = RecordingQb(
            [
                _torrent("d1" * 20, seeding_days=30, size=1_000),
                _torrent("d2" * 20, seeding_days=30, tags=CLEANUP_TAG, size=2_000),
            ]
        )

        with audit.scope(actor=audit.CLEANUP, trigger="AUTO"):
            await _run(session, qb)
        rows = {
            row.event: row
            for row in await session.scalars(
                select(ActivityLog).where(ActivityLog.event.startswith("DOWNLOAD_CLEANUP_"))
            )
        }

        mark_row = rows["DOWNLOAD_CLEANUP_MARKED"]
        assert mark_row.actor == audit.CLEANUP
        assert mark_row.trigger == "AUTO"
        assert mark_row.details["download_id"] == marked.id
        assert mark_row.details["size_bytes"] == 1_000
        assert mark_row.details["required_seeding_days"] == 10
        assert "已确认入库" in (mark_row.reason or "")
        assert "已做种 30.0 天" in (mark_row.reason or "")

        delete_row = rows["DOWNLOAD_CLEANUP_DELETED"]
        assert delete_row.actor == audit.CLEANUP
        assert delete_row.details["download_id"] == deleted.id
        assert delete_row.details["files_deleted"] is True
        assert "观察期 2 天已过" in (delete_row.reason or "")
        assert "删除前复核清理标签仍在" in (delete_row.reason or "")


# ---------------------------------------------------------------------------
# Holding and releasing by hand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_held_download_can_be_released_and_held_again(session_factory) -> None:
    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="release",
            info_hash="e1" * 20,
            cleanup_state=DownloadCleanupState.HELD,
        )

        with audit.scope(actor="owner", trigger="MANUAL"):
            released = await cleanup.set_cleanup_hold(
                session, download_id=download.id, held=False
            )
            assert released.cleanup_state == DownloadCleanupState.NONE
            held = await cleanup.set_cleanup_hold(session, download_id=download.id, held=True)
            assert held.cleanup_state == DownloadCleanupState.HELD
        events = [
            (row.event, row.actor, row.trigger)
            for row in await session.scalars(
                select(ActivityLog).order_by(ActivityLog.created_at, ActivityLog.event.desc())
            )
        ]

        assert ("DOWNLOAD_CLEANUP_RELEASED", "owner", "MANUAL") in events
        assert ("DOWNLOAD_CLEANUP_HELD", "owner", "MANUAL") in events


@pytest.mark.asyncio
async def test_marked_download_cannot_be_held_from_the_app(session_factory) -> None:
    """The tag in qBittorrent is the override; two switches could disagree."""

    async with session_factory() as session:
        download = await _seed(
            session,
            source_item_id="marked-hold",
            info_hash="e2" * 20,
            cleanup_state=DownloadCleanupState.MARKED,
            marked_days_ago=1,
        )

        with pytest.raises(AppError) as raised:
            await cleanup.set_cleanup_hold(session, download_id=download.id, held=True)

        assert raised.value.error_code == "DOWNLOAD_CLEANUP_MARKED"


# ---------------------------------------------------------------------------
# The interval is a setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(360, [600.0, 21_600.0]), (20, [600.0, 1_200.0]), (5, [300.0, 300.0])],
)
@pytest.mark.asyncio
async def test_loop_waits_the_configured_interval(
    monkeypatch: pytest.MonkeyPatch, session_factory, minutes: int, expected: list[float]
) -> None:
    waits: list[float] = []

    async def fake_wait(_stop: asyncio.Event, delay: timedelta) -> bool:
        waits.append(delay.total_seconds())
        return len(waits) > 1

    async def fake_cycle(*_args: object, **_kwargs: object) -> cleanup.CleanupResult:
        return cleanup.CleanupResult()

    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: Settings(_env_file=None, cleanup_interval_minutes=minutes),
    )
    monkeypatch.setattr(cleanup, "_stopped_within", fake_wait)
    monkeypatch.setattr(cleanup, "run_cleanup_cycle", fake_cycle)
    qb = RecordingQb([])

    await cleanup.download_cleanup_loop(
        asyncio.Event(),
        readonly_factory=lambda: cast(QbittorrentAdapter, qb),
        write_factory=lambda: cast(QbittorrentAdapter, qb),
        session_factory=session_factory,
    )

    assert waits == expected
