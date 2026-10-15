from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.core import automation_runner
from app.db.base import Base
from app.db.session import enable_sqlite_foreign_keys, get_session
from app.errors import AppError
from app.main import app
from app.models.enums import MediaType
from app.simple import automation, integrations
from app.simple.models import (
    AutomationRun,
    AutomationRunState,
    LibraryMediaItem,
    MediaState,
    ReleaseSearch,
    SearchState,
)
from app.simple.service import create_search


@pytest_asyncio.fixture
async def file_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'recovery.db').as_posix()}",
        connect_args={"timeout": 0.1},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_failed_commit_releases_original_writer_before_fallback(
    file_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedCommitSession(AsyncSession):
        fail_commit = False

        async def commit(self) -> None:
            if self.fail_commit:
                self.fail_commit = False
                # Like a failed SQLite COMMIT, retain the write transaction.
                await self.flush()
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
            await super().commit()

    monkeypatch.setattr(automation_runner, "_sync_nextfind_before_automation", AsyncMock())
    monkeypatch.setattr(automation_runner, "SessionFactory", file_session_factory)
    monkeypatch.setattr(
        automation, "run_automation",
        AsyncMock(side_effect=AppError("SEARCH_FAILED", "搜索失败")),
    )
    factory = async_sessionmaker(
        file_session_factory.kw["bind"], class_=FailedCommitSession, expire_on_commit=False,
    )
    async with factory() as session:
        policy = await automation.get_policy(session)
        policy.enabled = True
        await session.commit()
        run = await automation.create_automation_run(session, trigger="manual")
        run_id = run.id
        session.fail_commit = True
        await asyncio.wait_for(
            automation_runner.execute_recorded_automation_run(session, run), timeout=2,
        )

    async with file_session_factory() as session:
        stored = await session.get(AutomationRun, run_id)
        assert stored is not None
        assert stored.state == AutomationRunState.FAILED
        assert stored.error_message == "搜索失败"
        assert stored.finished_at is not None


async def test_failure_persistence_stops_retrying_and_unwinds(
    file_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
    fail = AsyncMock(side_effect=error)
    sleep = AsyncMock()
    monkeypatch.setattr(automation_runner, "SessionFactory", file_session_factory)
    monkeypatch.setattr(automation_runner, "_fail_automation_run", fail)
    monkeypatch.setattr(automation_runner.asyncio, "sleep", sleep)

    with pytest.raises(OperationalError) as caught:
        await automation_runner._persist_automation_run_failure("run-id", "失败")

    assert caught.value is error
    assert fail.await_count == 5
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4, 8]


@pytest.mark.parametrize("failure_status_commit_fails", [False, True])
async def test_search_rolls_back_broken_transaction_and_preserves_initial_error(
    file_session_factory, monkeypatch: pytest.MonkeyPatch, failure_status_commit_fails: bool,
) -> None:
    async with file_session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="duplicate", media_type=MediaType.MOVIE,
            tmdb_id=9001, title="Recovery Movie", state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        media_id = media.id
        search = await create_search(session, media_id=media_id, site_ids=["avistaz"])
        search_id = search.id
        original_error: list[IntegrityError] = []
        fail_status_commit = False
        original_commit = session.commit

        async def fail_complete(*_args: object, **_kwargs: object) -> None:
            nonlocal fail_status_commit
            session.add(LibraryMediaItem(
                source_item_id="duplicate", media_type=MediaType.MOVIE, title="Duplicate",
            ))
            try:
                await session.flush()
            except IntegrityError as exc:
                original_error.append(exc)
                fail_status_commit = failure_status_commit_fails
                raise

        async def commit() -> None:
            if fail_status_commit:
                await session.flush()
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))
            await original_commit()

        monkeypatch.setattr(integrations, "complete_search", fail_complete)
        monkeypatch.setattr(session, "commit", commit)
        with pytest.raises(IntegrityError) as caught:
            await integrations.run_release_search(
                session, search_id, lambda _: AvistaZMockAdapter(),
            )
        assert caught.value is original_error[0]
        assert not session.in_transaction()

    async with file_session_factory() as session:
        stored_search = await session.get(ReleaseSearch, search_id)
        stored_media = await session.get(LibraryMediaItem, media_id)
        assert stored_search is not None and stored_media is not None
        if not failure_status_commit_fails:
            assert stored_search.state == SearchState.FAILED
            assert stored_media.state == MediaState.NEEDS_ATTENTION
        # Another writer can still commit even if saving the failure failed.
        stored_media.title = "Writer still works"
        await session.commit()


async def test_health_detects_a_real_database_lock_and_recovers(file_session_factory) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with file_session_factory() as session:
            yield session

    engine = file_session_factory.kw["bind"]
    blocker = sqlite3.connect(engine.url.database)
    app.dependency_overrides[get_session] = session_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        ) as client:
            assert (await client.get("/api/health")).status_code == 200
            blocker.execute("BEGIN EXCLUSIVE")
            response = await client.get("/api/health")
            assert response.status_code == 503
            assert "recovery.db" not in response.text
            blocker.rollback()
            response = await client.get("/api/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
    finally:
        blocker.close()
        app.dependency_overrides.clear()


async def test_wal_allows_background_commit_while_a_reader_holds_a_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "wal.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    event.listen(engine.sync_engine, "connect", enable_sqlite_foreign_keys)
    reader = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        reader = sqlite3.connect(database)
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM library_media").fetchone()[0] == 0
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(LibraryMediaItem(
                source_item_id="concurrent", media_type=MediaType.MOVIE, title="Concurrent",
            ))
            await asyncio.wait_for(session.commit(), timeout=2)
        assert reader.execute("SELECT count(*) FROM library_media").fetchone()[0] == 0
        reader.rollback()
        assert reader.execute("SELECT count(*) FROM library_media").fetchone()[0] == 1
    finally:
        if reader is not None:
            reader.close()
        await engine.dispose()
