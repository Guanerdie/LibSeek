"""Match a site's recent-torrents feed against the library.

Optimisation 9 in ``NEXT_OPTIMIZATION_PLAN.md``.  Searching costs one request
per missing item; reading the feed costs one request for everything the site
published recently, and the matching happens locally.  For a library with
dozens of missing items that is the difference between dozens of requests per
cycle and one.

An RSS entry is a *lead*, not a candidate.  The feed carries a release title
and little else, so a match here only says "this looks like something we want,
go ask the site properly" -- identity is still settled by the normal search and
scoring path, against TMDB, before anything is downloaded.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import RssEntry
from app.simple.models import LibraryMediaItem, MediaState

# States whose media are still looking for a release.
_OPEN_STATES = (MediaState.READY, MediaState.NEEDS_ATTENTION, MediaState.CANDIDATES)

_YEAR = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_SEPARATORS = re.compile(r"[_\W]+")
# A feed is public-ish XML from a third party; an entity-expansion payload
# ("billion laughs") is the realistic attack, and ElementTree has no option to
# disable it, so a DOCTYPE is refused outright instead.
_DOCTYPE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_MAX_ENTRIES = 500


@dataclass(frozen=True)
class RssMatch:
    media_id: str
    media_title: str
    entry: RssEntry


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return _SEPARATORS.sub(" ", folded).strip()


def _tokens(value: str | None) -> list[str]:
    return _normalize(value).split() if value else []


def _torrent_id_from_link(link: str, parameter: str) -> str | None:
    query = parse_qs(urlsplit(link).query)
    values = query.get(parameter) or []
    return values[0] if values and values[0] else None


def _parsed_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_rss(content: bytes, *, torrent_id_parameter: str = "id") -> list[RssEntry]:
    """Turn an RSS 2.0 body into entries, skipping anything unusable."""

    if _DOCTYPE.search(content):
        raise AppError(
            "RSS_DOCTYPE_REJECTED",
            "RSS 响应包含 DOCTYPE 声明，已拒绝解析",
            status_code=502,
        )
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise AppError("RSS_INVALID_XML", "RSS 响应不是有效的 XML", status_code=502) from exc

    entries: list[RssEntry] = []
    for item in root.iter("item"):
        if len(entries) >= _MAX_ENTRIES:
            break
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        torrent_id = _torrent_id_from_link(link, torrent_id_parameter)
        if torrent_id is None:
            continue
        size_bytes: int | None = None
        enclosure = item.find("enclosure")
        if enclosure is not None:
            raw_length = enclosure.get("length")
            if raw_length and raw_length.isdigit():
                size_bytes = int(raw_length)
        entries.append(
            RssEntry(
                torrent_id=torrent_id[:128],
                title=title[:500],
                published_at=_parsed_date(item.findtext("pubDate")),
                size_bytes=size_bytes,
                details_ref=link[:1000],
            )
        )
    return entries


def entry_matches_media(entry: RssEntry, media: LibraryMediaItem) -> bool:
    """Does this release title plausibly belong to this library item?

    Deliberately conservative on titles and strict on the year: a false
    positive here only costs one wasted search, but a false *negative* is
    invisible, so the check leans on evidence the feed actually carries.
    """

    release = _normalize(entry.title)
    if not release:
        return False
    release_tokens = release.split()

    candidates = [media.title, media.original_title, *(media.search_titles or [])]
    matched_title = False
    for candidate in candidates:
        tokens = _tokens(candidate)
        if not tokens:
            continue
        # Every word of the library title must appear, in order, at the front
        # of the release name -- release names lead with the title.
        if release_tokens[: len(tokens)] == tokens:
            matched_title = True
            break
    if not matched_title:
        return False

    if media.year is not None:
        years = {int(found) for found in _YEAR.findall(entry.title)}
        # A release that names a different year is a different work; one that
        # names no year at all stays eligible, because plenty do not.
        if years and media.year not in years:
            return False
    return True


async def match_rss_to_library(
    session: AsyncSession,
    entries: list[RssEntry],
    *,
    media_types: list[MediaType] | None = None,
) -> list[RssMatch]:
    """Pair feed entries with library items that are still missing a release."""

    if not entries:
        return []
    statement = select(LibraryMediaItem).where(
        LibraryMediaItem.state.in_(_OPEN_STATES),
        LibraryMediaItem.tmdb_id.is_not(None),
    )
    if media_types:
        statement = statement.where(LibraryMediaItem.media_type.in_(media_types))
    wanted = list(await session.scalars(statement))
    if not wanted:
        return []

    matches: list[RssMatch] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        for media in wanted:
            if not entry_matches_media(entry, media):
                continue
            key = (media.id, entry.torrent_id)
            if key in seen:
                continue
            seen.add(key)
            matches.append(RssMatch(media_id=media.id, media_title=media.title, entry=entry))
    return matches
