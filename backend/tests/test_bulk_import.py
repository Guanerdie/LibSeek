"""Pasting a list of wanted titles and downloading them in one go."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.errors import AppError
from app.models.enums import MediaType
from app.simple import bulk_import
from app.simple.bulk_import import match_lines, normalize_title, parse_lines
from app.simple.models import (
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
)


async def _media(
    session: AsyncSession,
    title: str,
    *,
    tmdb_id: int,
    media_type: MediaType = MediaType.TV,
    year: int | None = 2020,
    original_title: str | None = None,
    search_titles: list[str] | None = None,
    state: MediaState = MediaState.READY,
    confirmed: bool = False,
) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=f"nextfind:{media_type.value}:{tmdb_id}",
        media_type=media_type,
        tmdb_id=tmdb_id,
        title=title,
        original_title=original_title,
        search_titles=search_titles or [],
        year=year,
        state=state,
        library_confirmed_at=datetime(2026, 10, 1, tzinfo=UTC) if confirmed else None,
    )
    session.add(media)
    await session.flush()
    return media


# ---------------------------------------------------------------------------
# Reading the pasted text
# ---------------------------------------------------------------------------


def test_lines_are_read_as_links_ids_or_titles() -> None:
    lines = parse_lines(
        "\n".join(
            [
                "https://www.themoviedb.org/tv/12345-some-show?language=zh-CN",
                "themoviedb.org/movie/678",
                "tmdb:999",
                "tv 4242",
                "   ",
                "# 这一行是备注",
                "红宝石戒指 (2013)",
                "Blade Runner 2049",
                "少年星球",
            ]
        )
    )

    assert [(line.tmdb_id, line.media_type) for line in lines[:4]] == [
        (12345, MediaType.TV),
        (678, MediaType.MOVIE),
        (999, None),
        (4242, MediaType.TV),
    ]
    assert (lines[4].title, lines[4].year) == ("红宝石戒指", 2013)
    assert (lines[5].title, lines[5].year) == ("Blade Runner", 2049)
    assert (lines[6].title, lines[6].year) == ("少年星球", None)
    assert len(lines) == 7


def test_titles_are_compared_without_case_spacing_or_punctuation() -> None:
    assert normalize_title("WHY R U：偏偏是你？") == normalize_title("why r u: 偏偏是你?")
    assert normalize_title("Ｒｕｂｙ　Ｒｉｎｇ") == "rubyring"


def test_no_more_than_the_line_limit_is_read() -> None:
    assert (
        len(parse_lines("\n".join(f"片名{index}" for index in range(500)))) == bulk_import.MAX_LINES
    )


# ---------------------------------------------------------------------------
# Matching against the missing list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_line_is_matched_to_the_missing_title_it_means(session_factory) -> None:
    async with session_factory() as session:
        ruby = await _media(session, "红宝石戒指", tmdb_id=1, year=2013, original_title="루비 반지")
        planet = await _media(session, "少年星球", tmdb_id=2, search_titles=["Boys Planet"])
        film = await _media(session, "同名", tmdb_id=3, media_type=MediaType.MOVIE, year=2001)
        show = await _media(session, "同名", tmdb_id=4, year=2019)
        await _media(session, "已入库的剧", tmdb_id=5, state=MediaState.COMPLETE, confirmed=True)
        blade = await _media(
            session, "Blade Runner 2049", tmdb_id=6, media_type=MediaType.MOVIE, year=2017
        )
        number = await _media(session, "2046", tmdb_id=7, media_type=MediaType.MOVIE, year=2004)
        await session.commit()

        results = await match_lines(
            session,
            parse_lines(
                "\n".join(
                    [
                        "红宝石戒指 (2013)",  # title with year
                        "루비 반지",  # original title
                        "boys planet",  # alias, different case
                        "https://www.themoviedb.org/tv/4",  # link picks the series
                        "同名",  # two titles share the name
                        "同名 2001",  # the year settles it
                        "已入库的剧",  # no longer missing
                        "Blade Runner 2049",  # the number is part of the title
                        "2046",  # a bare number that is a title, not an id
                        "完全不存在的片子",
                        "戒指",  # only part of a name
                    ]
                )
            ),
        )
        by_raw = {line.raw: line for line in results}

        assert by_raw["红宝石戒指 (2013)"].status == "MATCHED"
        assert by_raw["红宝石戒指 (2013)"].media.media_id == ruby.id
        # The same title again, spelt another way.
        assert by_raw["루비 반지"].status == "DUPLICATE"
        assert by_raw["boys planet"].media.media_id == planet.id
        assert by_raw["https://www.themoviedb.org/tv/4"].media.media_id == show.id
        assert by_raw["同名"].status == "AMBIGUOUS"
        assert {choice.media_id for choice in by_raw["同名"].choices} == {film.id, show.id}
        assert by_raw["同名 2001"].media.media_id == film.id
        assert by_raw["已入库的剧"].status == "NOT_MISSING"
        assert by_raw["Blade Runner 2049"].media.media_id == blade.id
        assert by_raw["2046"].media.media_id == number.id
        assert by_raw["完全不存在的片子"].status == "NOT_FOUND"
        # Close, but not the same: offered for the operator to confirm.
        assert by_raw["戒指"].status == "AMBIGUOUS"
        assert [choice.media_id for choice in by_raw["戒指"].choices] == [ruby.id]
        assert by_raw["戒指"].note is not None


@pytest.mark.asyncio
async def test_a_title_already_downloading_is_not_offered_again(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, "正在下的剧", tmdb_id=10)
        search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(search)
        await session.flush()
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="t1",
            title="Show.S01",
            score=0.9,
            reasons=[],
            warnings=[],
        )
        session.add(candidate)
        await session.flush()
        session.add(
            Download(
                media_id=media.id,
                candidate_id=candidate.id,
                name="Show.S01",
                state=DownloadState.DOWNLOADING,
            )
        )
        await session.commit()

        (line,) = await match_lines(session, parse_lines("正在下的剧"))

        assert line.status == "DOWNLOADING"


# ---------------------------------------------------------------------------
# Running the batch
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bulk_import, "_batch", bulk_import.ImportBatch())


async def _finish() -> None:
    await asyncio.gather(*list(bulk_import._tasks))


class _Selected:
    def __init__(self, title: str) -> None:
        self.title = title


class _Submitted:
    def __init__(self, download_id: str, state: DownloadState = DownloadState.QUEUED) -> None:
        self.id = download_id
        self.state = state
        self.error_message = "qBittorrent 拒绝了这个种子" if state == DownloadState.ERROR else None


@pytest.mark.asyncio
async def test_each_title_is_filled_in_turn_and_its_outcome_recorded(
    monkeypatch: pytest.MonkeyPatch, session_factory
) -> None:
    async with session_factory() as session:
        good = await _media(session, "能下到的", tmdb_id=21)
        none = await _media(session, "没有合格资源的", tmdb_id=22)
        broken = await _media(session, "会出错的", tmdb_id=23)
        refused = await _media(session, "被下载器拒绝的", tmdb_id=24)
        await session.commit()

        calls: list[tuple[str, str, str, dict[str, object]]] = []

        async def fake_quick_fill(_session: AsyncSession, **kwargs: Any) -> tuple[Any, ...]:
            context = audit.current()
            calls.append(
                (kwargs["media_id"], kwargs["origin"], context.actor, dict(context.details))
            )
            if kwargs["media_id"] == good.id:
                return None, _Selected("Good.S01.1080p"), [], _Submitted("d-good")
            if kwargs["media_id"] == none.id:
                rejected = [
                    {"title": "a", "reasons": ["做种数不足"]},
                    {"title": "b", "reasons": ["做种数不足", "资源体积超过策略上限"]},
                ]
                return None, None, rejected, None
            if kwargs["media_id"] == refused.id:
                return (
                    None,
                    _Selected("Refused.S01"),
                    [],
                    _Submitted("d-refused", DownloadState.ERROR),
                )
            raise AppError("QB_WRITE_DISABLED", "qBittorrent 下载提交尚未启用", status_code=409)

        monkeypatch.setattr(bulk_import.automation, "quick_fill_media", fake_quick_fill)
        with audit.scope(actor="owner", trigger="MANUAL"):
            started = await bulk_import.start_batch(
                session,
                [good.id, none.id, broken.id, refused.id, good.id],
                session_factory=session_factory,
            )
            assert started.state == "RUNNING" and started.started_by == "owner"
            assert len(started.items) == 4, "a repeated id is one download, not two"
            await _finish()

    batch = bulk_import.current_batch()
    outcomes = {item.title: item for item in batch.items}
    assert batch.state == "FINISHED" and batch.finished_at is not None
    assert outcomes["能下到的"].outcome == "DOWNLOADED"
    assert outcomes["能下到的"].selected_title == "Good.S01.1080p"
    assert outcomes["没有合格资源的"].outcome == "NO_CANDIDATE"
    assert "做种数不足（2 个）" in (outcomes["没有合格资源的"].message or "")
    assert outcomes["会出错的"].outcome == "FAILED"
    assert outcomes["会出错的"].message == "qBittorrent 下载提交尚未启用"
    assert outcomes["被下载器拒绝的"].outcome == "FAILED"
    # One failure does not stop the rest, and every download is the operator's.
    assert [call[0] for call in calls] == [good.id, none.id, broken.id, refused.id]
    assert {call[1] for call in calls} == {"批量导入"}
    assert {call[2] for call in calls} == {"owner"}
    assert all(call[3]["import_batch"] == batch.id for call in calls)


@pytest.mark.asyncio
async def test_a_second_batch_waits_for_the_first(
    monkeypatch: pytest.MonkeyPatch, session_factory
) -> None:
    gate = asyncio.Event()

    async def slow_quick_fill(_session: AsyncSession, **kwargs: Any) -> tuple[Any, ...]:
        await gate.wait()
        return None, _Selected("x"), [], _Submitted("d")

    monkeypatch.setattr(bulk_import.automation, "quick_fill_media", slow_quick_fill)
    async with session_factory() as session:
        first = await _media(session, "第一部", tmdb_id=31)
        second = await _media(session, "第二部", tmdb_id=32)
        await session.commit()

        await bulk_import.start_batch(
            session, [first.id, second.id], session_factory=session_factory
        )
        with pytest.raises(AppError) as raised:
            await bulk_import.start_batch(session, [second.id], session_factory=session_factory)
        assert raised.value.error_code == "IMPORT_IN_PROGRESS"

        # Let the first title get under way, then cancel: it stops before the
        # next title, and the one in hand completes.
        for _ in range(5):
            await asyncio.sleep(0)
        bulk_import.request_cancel()
        gate.set()
        await _finish()

    batch = bulk_import.current_batch()
    assert batch.state == "CANCELLED"
    assert [item.outcome for item in batch.items] == ["DOWNLOADED", "CANCELLED"]


@pytest.mark.asyncio
async def test_a_title_that_left_the_missing_list_is_refused(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(
            session, "刚入库的", tmdb_id=41, state=MediaState.COMPLETE, confirmed=True
        )
        await session.commit()

        with pytest.raises(AppError) as raised:
            await bulk_import.start_batch(session, [media.id], session_factory=session_factory)

        assert raised.value.error_code == "MEDIA_NOT_MISSING"
        assert not bulk_import.batch_running()
        assert (await session.scalars(select(Download))).all() == []
