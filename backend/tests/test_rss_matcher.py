"""One feed read instead of one search per missing item.

Optimisation 9 in ``NEXT_OPTIMIZATION_PLAN.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.automation_runner import run_rss_match_cycle
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import RssEntry
from app.services.rss_matcher import (
    entry_matches_media,
    match_rss_to_library,
    parse_rss,
)
from app.simple.models import LibraryMediaItem, MediaState

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>Example.Movie.2026.1080p.WEB-DL.x264</title>
      <link>https://example.invalid/details.php?id=4242</link>
      <pubDate>Mon, 08 Sep 2026 03:00:00 +0000</pubDate>
      <enclosure url="https://example.invalid/download.php?id=4242" length="2147483648" />
    </item>
    <item>
      <title>Other.Show.S02.COMPLETE.1080p</title>
      <link>https://example.invalid/details.php?id=99</link>
      <pubDate>Mon, 08 Sep 2026 02:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_a_feed_becomes_entries() -> None:
    entries = parse_rss(FEED)

    assert [entry.torrent_id for entry in entries] == ["4242", "99"]
    assert entries[0].title == "Example.Movie.2026.1080p.WEB-DL.x264"
    assert entries[0].size_bytes == 2147483648
    assert entries[0].published_at == datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
    assert entries[1].size_bytes is None


def test_an_item_without_a_usable_link_is_skipped() -> None:
    feed = b"""<rss><channel>
      <item><title>No link</title></item>
      <item><title>No id</title><link>https://example.invalid/details.php</link></item>
      <item><title>Good</title><link>https://example.invalid/details.php?id=7</link></item>
    </channel></rss>"""

    assert [entry.torrent_id for entry in parse_rss(feed)] == ["7"]


def test_a_doctype_is_refused_rather_than_expanded() -> None:
    """Entity expansion is the realistic attack on a third-party feed."""

    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss></rss>'

    with pytest.raises(AppError) as excinfo:
        parse_rss(bomb)

    assert excinfo.value.error_code == "RSS_DOCTYPE_REJECTED"


def test_malformed_xml_is_reported_not_swallowed() -> None:
    with pytest.raises(AppError) as excinfo:
        parse_rss(b"<rss><channel>")

    assert excinfo.value.error_code == "RSS_INVALID_XML"


def test_an_unparseable_date_leaves_the_entry_undated() -> None:
    feed = b"""<rss><channel><item>
      <title>Thing</title>
      <link>https://example.invalid/details.php?id=5</link>
      <pubDate>whenever</pubDate>
    </item></channel></rss>"""

    assert parse_rss(feed)[0].published_at is None


def test_the_torrent_id_parameter_is_configurable() -> None:
    feed = b"""<rss><channel><item>
      <title>Thing</title>
      <link>https://example.invalid/t.php?tid=88</link>
    </item></channel></rss>"""

    assert parse_rss(feed, torrent_id_parameter="tid")[0].torrent_id == "88"


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _media(
    *,
    title: str,
    year: int | None = 2026,
    original_title: str | None = None,
    search_titles: list[str] | None = None,
) -> LibraryMediaItem:
    return LibraryMediaItem(
        source_item_id=f"rss-{title}",
        media_type=MediaType.MOVIE,
        tmdb_id=1,
        title=title,
        original_title=original_title,
        search_titles=search_titles or [],
        year=year,
        state=MediaState.READY,
    )


def _entry(title: str) -> RssEntry:
    return RssEntry(torrent_id="1", title=title)


def test_a_release_leading_with_the_title_matches() -> None:
    assert entry_matches_media(
        _entry("Example.Movie.2026.1080p.WEB-DL"), _media(title="Example Movie")
    )


def test_a_different_film_does_not_match() -> None:
    assert not entry_matches_media(
        _entry("Something.Else.2026.1080p"), _media(title="Example Movie")
    )


def test_a_title_appearing_only_mid_string_does_not_match() -> None:
    """Release names lead with the title; a mid-string hit is a coincidence."""

    assert not entry_matches_media(
        _entry("Making.Of.Example.Movie.2026.1080p"), _media(title="Example Movie")
    )


def test_a_wrong_year_rejects_an_otherwise_matching_title() -> None:
    assert not entry_matches_media(
        _entry("Example.Movie.1999.1080p"), _media(title="Example Movie", year=2026)
    )


def test_a_release_naming_no_year_stays_eligible() -> None:
    """Plenty of releases omit the year; that is not evidence against."""

    assert entry_matches_media(
        _entry("Example.Movie.1080p.WEB-DL"), _media(title="Example Movie", year=2026)
    )


def test_an_alias_title_also_matches() -> None:
    assert entry_matches_media(
        _entry("Alternate.Name.2026.1080p"),
        _media(title="中文名", search_titles=["Alternate Name"]),
    )


def test_the_original_title_also_matches() -> None:
    assert entry_matches_media(
        _entry("Original.Name.2026.1080p"),
        _media(title="中文名", original_title="Original Name"),
    )


@pytest.mark.asyncio
async def test_only_media_still_looking_for_a_release_are_matched(session_factory) -> None:
    async with session_factory() as session:
        wanted = _media(title="Example Movie")
        already = _media(title="Example Movie")
        already.source_item_id = "rss-done"
        already.tmdb_id = 2
        already.state = MediaState.COMPLETE
        session.add_all([wanted, already])
        await session.commit()

        matches = await match_rss_to_library(session, parse_rss(FEED))

        assert [match.media_id for match in matches] == [wanted.id]


@pytest.mark.asyncio
async def test_an_empty_feed_matches_nothing(session_factory) -> None:
    async with session_factory() as session:
        session.add(_media(title="Example Movie"))
        await session.commit()

        assert await match_rss_to_library(session, []) == []


@pytest.mark.asyncio
async def test_media_without_a_tmdb_id_are_skipped(session_factory) -> None:
    """An unidentified item cannot be verified, so a lead is worthless."""

    async with session_factory() as session:
        media = _media(title="Example Movie")
        media.tmdb_id = None
        session.add(media)
        await session.commit()

        assert await match_rss_to_library(session, parse_rss(FEED)) == []


# ---------------------------------------------------------------------------
# The cycle
# ---------------------------------------------------------------------------


class FeedAdapter:
    def __init__(self, feed: bytes = FEED) -> None:
        self.feed = feed
        self.calls = 0

    async def fetch_rss(self, *, hours: int = 1) -> list[RssEntry]:
        self.calls += 1
        self.hours = hours
        return parse_rss(self.feed)


@pytest.mark.asyncio
async def test_a_match_clears_the_search_cooldown(session_factory) -> None:
    """The cooldown was a guess that nothing would appear; the feed disproved it."""

    async with session_factory() as session:
        media = _media(title="Example Movie")
        media.search_miss_count = 3
        media.next_search_at = utc_now() + timedelta(days=7)
        session.add(media)
        await session.commit()
        adapter = FeedAdapter()

        matches = await run_rss_match_cycle(session, adapter, window_hours=1)  # type: ignore[arg-type]

        assert len(matches) == 1
        assert adapter.calls == 1
        await session.refresh(media)
        assert media.next_search_at is None
        assert media.search_miss_count == 0


@pytest.mark.asyncio
async def test_one_feed_read_covers_the_whole_library(session_factory) -> None:
    """The point of the optimisation: N missing items, one request."""

    async with session_factory() as session:
        for index in range(5):
            media = _media(title="Example Movie")
            media.source_item_id = f"rss-many-{index}"
            media.tmdb_id = 100 + index
            session.add(media)
        await session.commit()
        adapter = FeedAdapter()

        matches = await run_rss_match_cycle(session, adapter, window_hours=1)  # type: ignore[arg-type]

        assert len(matches) == 5
        assert adapter.calls == 1


@pytest.mark.asyncio
async def test_an_adapter_without_rss_support_is_reported(session_factory) -> None:
    class NoRss:
        pass

    async with session_factory() as session:
        with pytest.raises(AppError) as excinfo:
            await run_rss_match_cycle(session, NoRss(), window_hours=1)  # type: ignore[arg-type]

        assert excinfo.value.error_code == "PT_SITE_RSS_UNSUPPORTED"


@pytest.mark.asyncio
async def test_a_media_not_in_cooldown_is_left_alone(session_factory) -> None:
    async with session_factory() as session:
        media = _media(title="Example Movie")
        session.add(media)
        await session.commit()

        await run_rss_match_cycle(session, FeedAdapter(), window_hours=1)  # type: ignore[arg-type]

        await session.refresh(media)
        assert media.next_search_at is None
        assert media.search_miss_count == 0
