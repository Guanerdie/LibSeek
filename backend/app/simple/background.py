"""Work the API hands off instead of doing inside the request.

A PT search walks several query strategies at the site's rate limit, and a
NextFind sync pages through the whole missing list; either can outlast a
reverse proxy's read timeout, and a phone gives up long before that.  The
routes start them here and return at once, and clients follow the search or
the sync status by polling.  The app is one process with one event loop, so
the tasks live in memory; whatever a restart cuts off is marked failed on the
next start.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import MediaSourceAdapter
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.errors import AppError
from app.simple.integrations import (
    build_pt_site,
    build_tmdb,
    close_adapter,
    mark_library_sync_started,
    run_release_search,
    settle_library_sync,
    sync_nextfind,
)
from app.simple.models import LibraryMediaItem, MediaState, ReleaseSearch, SearchState

_logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task[None]] = set()


def _track(task: asyncio.Task[None]) -> None:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def cancel_background_tasks() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _mark_search_failed(search: ReleaseSearch, message: str) -> None:
    search.state = SearchState.FAILED
    search.error_message = message
    search.finished_at = utc_now()


async def _release_searching_media(
    session: AsyncSession, search: ReleaseSearch, message: str
) -> None:
    media = await session.get(LibraryMediaItem, search.media_id)
    if media is not None and media.state == MediaState.SEARCHING:
        media.state = MediaState.NEEDS_ATTENTION
        media.attention_reason = message


def queue_release_search(
    search_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    _track(asyncio.create_task(run_release_search_in_background(search_id, session_factory)))


async def run_release_search_in_background(
    search_id: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    try:
        async with session_factory() as session:
            await run_release_search(
                session,
                search_id,
                lambda site_id: build_pt_site(site_id, allow_torrent_fetch=False),
                metadata_factory=build_tmdb,
            )
    except asyncio.CancelledError:
        # Shutting down; the next start marks the search as interrupted.
        raise
    except Exception as exc:
        if not isinstance(exc, AppError):
            _logger.exception("Background release search failed")
        # run_release_search records failures once the search is running, but
        # not the ones raised before that; it must never stay pending.
        await _fail_unfinished_search(
            session_factory,
            search_id,
            exc.message if isinstance(exc, AppError) else "PT 搜索失败",
        )


async def _fail_unfinished_search(
    session_factory: async_sessionmaker[AsyncSession], search_id: str, message: str
) -> None:
    try:
        async with session_factory() as session:
            search = await session.get(ReleaseSearch, search_id)
            if search is None or search.state not in {SearchState.PENDING, SearchState.RUNNING}:
                return
            _mark_search_failed(search, message)
            await _release_searching_media(session, search, message)
            await session.commit()
    except Exception:
        _logger.exception("Unable to record a failed background search")


async def recover_interrupted_searches(session: AsyncSession) -> int:
    """Fail the searches a restart cut off; nothing is left running them."""

    searches = list(
        await session.scalars(
            select(ReleaseSearch).where(
                ReleaseSearch.state.in_((SearchState.PENDING, SearchState.RUNNING))
            )
        )
    )
    message = "应用重启中断了这次搜索，请重新搜索"
    for search in searches:
        _mark_search_failed(search, message)
        await _release_searching_media(session, search, message)
    if searches:
        await session.commit()
    return len(searches)


def queue_library_sync(
    adapter: MediaSourceAdapter,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    """Start a NextFind sync in the background; the caller checked none is running."""

    # Reported as running from the moment it is queued, so a client polling
    # right away never mistakes the previous result for this one.
    mark_library_sync_started()
    _track(asyncio.create_task(run_library_sync_in_background(adapter, session_factory)))


async def run_library_sync_in_background(
    adapter: MediaSourceAdapter, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    try:
        async with session_factory() as session:
            await sync_nextfind(session, adapter)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if not isinstance(exc, AppError):
            _logger.exception("Background NextFind sync failed")
        # sync_nextfind reports its own outcome; this covers a failure before
        # it could, which would otherwise leave the status stuck on RUNNING.
        settle_library_sync(exc.message if isinstance(exc, AppError) else "缺失影视同步失败")
    finally:
        await close_adapter(adapter)
