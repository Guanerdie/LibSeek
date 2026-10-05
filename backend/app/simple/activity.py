"""Read the activity log back: what was done, when, by whom and why."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import ColumnElement, and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.simple.models import ActivityLog, LibraryMediaItem

ActivityCategory = Literal["download", "cleanup", "library", "search", "settings"]

_CLEANUP_PREFIX = "DOWNLOAD_CLEANUP_"
_DOWNLOAD_PREFIX = "DOWNLOAD_"
_SEARCH_PREFIX = "SEARCH_"
_SETTINGS_EVENTS = ("AUTOMATION_POLICY_UPDATED",)


def category_of(event: str) -> ActivityCategory:
    if event.startswith(_CLEANUP_PREFIX):
        return "cleanup"
    if event.startswith(_DOWNLOAD_PREFIX):
        return "download"
    if event.startswith(_SEARCH_PREFIX):
        return "search"
    if event in _SETTINGS_EVENTS:
        return "settings"
    # NextFind syncs, library confirmations and TMDB identification.
    return "library"


def _category_filter(category: ActivityCategory) -> ColumnElement[bool]:
    event = ActivityLog.event
    is_cleanup = event.startswith(_CLEANUP_PREFIX, autoescape=True)
    is_download = and_(event.startswith(_DOWNLOAD_PREFIX, autoescape=True), not_(is_cleanup))
    is_search = event.startswith(_SEARCH_PREFIX, autoescape=True)
    is_settings = event.in_(_SETTINGS_EVENTS)
    if category == "cleanup":
        return is_cleanup
    if category == "download":
        return is_download
    if category == "search":
        return is_search
    if category == "settings":
        return is_settings
    return not_(or_(event.startswith(_DOWNLOAD_PREFIX, autoescape=True), is_search, is_settings))


async def list_activity(
    session: AsyncSession,
    *,
    category: ActivityCategory | None,
    trigger: Literal["MANUAL", "AUTO"] | None,
    query: str | None,
    media_id: str | None,
    page: int,
    page_size: int,
) -> tuple[list[tuple[ActivityLog, str | None]], int]:
    filters: list[ColumnElement[bool]] = []
    if category is not None:
        filters.append(_category_filter(category))
    if trigger is not None:
        filters.append(ActivityLog.trigger == trigger)
    if media_id is not None:
        filters.append(ActivityLog.media_id == media_id)
    if query:
        filters.append(
            or_(
                ActivityLog.message.contains(query, autoescape=True),
                ActivityLog.actor.contains(query, autoescape=True),
                LibraryMediaItem.title.contains(query, autoescape=True),
            )
        )

    joined = select(ActivityLog, LibraryMediaItem.title).outerjoin(
        LibraryMediaItem, LibraryMediaItem.id == ActivityLog.media_id
    )
    total = await session.scalar(
        select(func.count())
        .select_from(ActivityLog)
        .outerjoin(LibraryMediaItem, LibraryMediaItem.id == ActivityLog.media_id)
        .where(*filters)
    )
    rows = await session.execute(
        joined.where(*filters)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [(entry, title) for entry, title in rows.tuples()], int(total or 0)
