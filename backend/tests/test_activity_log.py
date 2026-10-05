"""The activity log answers: when, who, what, by hand or not, and why."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.adapters.base import PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.api.dependencies import get_viewer_principal
from app.core import audit
from app.core.auth import CSRF_HEADER_NAME, Principal
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.errors import AppError
from app.main import app
from app.models.enums import AuthRole, IdentityConfidence, MediaType, MetadataStatus
from app.schemas.adapters import DiscoveryWarning, MediaDiscoveryResult, MediaItemData
from app.simple import integrations
from app.simple.activity import category_of
from app.simple.integrations import (
    library_listing_is_fresh,
    retry_download,
    submit_download,
    sync_nextfind,
)
from app.simple.models import (
    ActivityLog,
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
)
from tests.test_simplified_daily_flow import (
    CapturingQb,
    FailingQb,
    FakeTorrentSource,
    add_candidate,
    torrent_fixture,
)
from tests.test_simplified_migrations import sqlite_url


async def _media(session: AsyncSession, source_item_id: str, title: str) -> LibraryMediaItem:
    media = LibraryMediaItem(
        source_item_id=source_item_id,
        media_type=MediaType.MOVIE,
        tmdb_id=int(uuid4().int % 1_000_000),
        title=title,
        state=MediaState.COMPLETE,
    )
    session.add(media)
    await session.flush()
    return media


async def _download_for(session: AsyncSession, media: LibraryMediaItem) -> Download:
    search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
    session.add(search)
    await session.flush()
    candidate = ReleaseCandidate(
        search_id=search.id,
        site_id="avistaz",
        torrent_id=f"t-{media.source_item_id}",
        title=f"{media.title}.1080p",
        score=0.9,
        reasons=[],
        warnings=[],
    )
    session.add(candidate)
    await session.flush()
    download = Download(
        media_id=media.id,
        candidate_id=candidate.id,
        name=f"{media.title}.mkv",
        state=DownloadState.SEEDING,
        progress=1,
        info_hash=uuid4().hex + uuid4().hex[:8],
    )
    session.add(download)
    await session.flush()
    return download


# ---------------------------------------------------------------------------
# Stamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_without_a_context_belong_to_the_system(session_factory) -> None:
    async with session_factory() as session:
        session.add(ActivityLog(event="NEXTFIND_SYNCED", message="sync"))
        await session.commit()
        row = (await session.scalars(select(ActivityLog))).one()

        assert row.actor == audit.SYSTEM
        assert row.trigger == "AUTO"
        assert row.reason is None


@pytest.mark.asyncio
async def test_rows_take_actor_trigger_reason_and_details_from_the_context(
    session_factory,
) -> None:
    async with session_factory() as session:
        with audit.scope(
            actor="owner",
            trigger="MANUAL",
            reason="because",
            details={"run_id": "r1", "site_id": "from-context"},
        ):
            session.add(
                ActivityLog(
                    event="DOWNLOAD_QUEUED", message="picked", details={"site_id": "avistaz"}
                )
            )
        # Created outside the block: the override must not leak.
        session.add(ActivityLog(event="SEARCH_CREATED", message="later"))
        await session.commit()
        rows = {row.event: row for row in await session.scalars(select(ActivityLog))}

        picked = rows["DOWNLOAD_QUEUED"]
        assert (picked.actor, picked.trigger, picked.reason) == ("owner", "MANUAL", "because")
        # The row's own details win over the context's.
        assert picked.details == {"run_id": "r1", "site_id": "avistaz"}
        assert rows["SEARCH_CREATED"].actor == audit.SYSTEM


def test_system_actors_are_never_mistaken_for_people() -> None:
    assert audit.is_person("owner")
    assert not audit.is_person(audit.SYSTEM)
    assert not audit.is_person(audit.CLEANUP)
    assert not audit.is_person(audit.SCHEDULER)


def test_events_fall_into_the_categories_the_page_filters_by() -> None:
    assert category_of("DOWNLOAD_SUBMITTED") == "download"
    assert category_of("DOWNLOAD_CLEANUP_DELETED") == "cleanup"
    assert category_of("SEARCH_COMPLETED") == "search"
    assert category_of("AUTOMATION_POLICY_UPDATED") == "settings"
    assert category_of("LIBRARY_CONFIRMED") == "library"
    assert category_of("NEXTFIND_SYNCED") == "library"


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


async def _viewer_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def viewer_override() -> Principal:
        return Principal(
            username="viewer",
            role=AuthRole.VIEWER,
            issued_at=0,
            expires_at=2_000_000_000,
            csrf_digest="test",
        )

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_viewer_principal] = viewer_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activity_api_lists_newest_first_with_every_field(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, "m1", "Example Movie")
        base = datetime(2026, 10, 1, tzinfo=UTC)
        session.add_all(
            [
                ActivityLog(
                    media_id=media.id,
                    event="DOWNLOAD_QUEUED",
                    message="已选择资源",
                    actor="owner",
                    trigger="MANUAL",
                    created_at=base,
                ),
                ActivityLog(
                    media_id=media.id,
                    event="DOWNLOAD_CLEANUP_DELETED",
                    message="已删除 Example.mkv 的种子和文件",
                    actor=audit.CLEANUP,
                    trigger="AUTO",
                    reason="观察期 2 天已过",
                    details={"size_bytes": 1234},
                    created_at=base + timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

    async for client in _viewer_client(session_factory):
        response = await client.get("/api/activity")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        newest, oldest = body["items"]
        assert newest["event"] == "DOWNLOAD_CLEANUP_DELETED"
        assert newest["category"] == "cleanup"
        assert newest["actor"] == audit.CLEANUP
        assert newest["trigger"] == "AUTO"
        assert newest["reason"] == "观察期 2 天已过"
        assert newest["details"] == {"size_bytes": 1234}
        assert newest["media_title"] == "Example Movie"
        assert oldest["category"] == "download"
        assert oldest["trigger"] == "MANUAL"


@pytest.mark.asyncio
async def test_activity_api_filters_by_category_trigger_and_text(session_factory) -> None:
    async with session_factory() as session:
        wanted = await _media(session, "m2", "Wanted Show")
        other = await _media(session, "m3", "Other Show")
        session.add_all(
            [
                ActivityLog(
                    media_id=wanted.id,
                    event="DOWNLOAD_SUBMITTED",
                    message="已提交到 qBittorrent",
                    actor="owner",
                    trigger="MANUAL",
                ),
                ActivityLog(
                    media_id=other.id,
                    event="DOWNLOAD_SUBMITTED",
                    message="已提交到 qBittorrent",
                    actor=audit.SCHEDULER,
                    trigger="AUTO",
                ),
                ActivityLog(
                    media_id=wanted.id,
                    event="DOWNLOAD_CLEANUP_MARKED",
                    message="已标记待清理",
                    actor=audit.CLEANUP,
                    trigger="AUTO",
                ),
                ActivityLog(event="NEXTFIND_SYNCED", message="同步完成"),
                ActivityLog(event="SEARCH_COMPLETED", message="搜索完成"),
            ]
        )
        await session.commit()

    async def events(client: httpx.AsyncClient, **params: str) -> list[str]:
        response = await client.get("/api/activity", params=params)
        assert response.status_code == 200, response.text
        return sorted(item["event"] for item in response.json()["items"])

    async for client in _viewer_client(session_factory):
        assert await events(client, category="download") == ["DOWNLOAD_SUBMITTED"] * 2
        assert await events(client, category="cleanup") == ["DOWNLOAD_CLEANUP_MARKED"]
        assert await events(client, category="library") == ["NEXTFIND_SYNCED"]
        assert await events(client, category="search") == ["SEARCH_COMPLETED"]
        assert await events(client, category="download", trigger="MANUAL") == [
            "DOWNLOAD_SUBMITTED"
        ]
        assert await events(client, query="Wanted") == [
            "DOWNLOAD_CLEANUP_MARKED",
            "DOWNLOAD_SUBMITTED",
        ]
        assert await events(client, media_id=other.id) == ["DOWNLOAD_SUBMITTED"]
        assert (await client.get("/api/activity", params={"category": "nope"})).status_code == 422


@pytest.mark.asyncio
async def test_a_signed_in_users_change_is_recorded_under_their_name(
    session_factory, tmp_path: Path
) -> None:
    """End to end: the real session cookie, not an overridden principal."""

    settings = Settings(
        _env_file=None,
        runtime_config_dir=tmp_path,
        auth_local_username=None,
        auth_local_password=None,
        auth_session_signing_key=None,
        auth_local_username_file=None,
        auth_local_password_file=None,
        auth_session_signing_key_file=None,
    )

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = session_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            setup = await client.post(
                "/api/auth/setup", json={"username": "owner", "password": "first-pass"}
            )
            assert setup.status_code == 200, setup.text
            csrf = str(setup.json()["csrf_token"])

            response = await client.put(
                "/api/automation/policy",
                json={"cleanup_grace_days": 0},
                headers={CSRF_HEADER_NAME: csrf},
            )
            assert response.status_code == 200, response.text
            assert response.json()["cleanup_grace_days"] == 0

            # Saving the same value again changes nothing and logs nothing.
            await client.put(
                "/api/automation/policy",
                json={"cleanup_grace_days": 0},
                headers={CSRF_HEADER_NAME: csrf},
            )
    finally:
        app.dependency_overrides.clear()

    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(ActivityLog).where(ActivityLog.event == "AUTOMATION_POLICY_UPDATED")
            )
        ).all()

    assert len(rows) == 1
    assert rows[0].actor == "owner"
    assert rows[0].trigger == "MANUAL"
    assert rows[0].details["changes"] == {"cleanup_grace_days": {"from": 2, "to": 0}}


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_download_leaves_a_row_for_each_step_including_the_failure(
    session_factory,
) -> None:
    payload, info_hash = torrent_fixture()
    async with session_factory() as session:
        _, candidate = await add_candidate(
            session, source_item_id="audited-download", info_hash=info_hash
        )

        with audit.scope(actor="owner", trigger="MANUAL"):
            with pytest.raises(AppError):
                await submit_download(
                    session,
                    candidate_id=candidate.id,
                    confirm_warnings=False,
                    pt_factory=lambda _site_id: cast(PtSiteAdapter, FakeTorrentSource(payload)),
                    qb_factory=lambda: cast(QbittorrentAdapter, FailingQb()),
                    settings=Settings(_env_file=None),
                )
            failed = await session.scalar(select(Download))
            assert failed is not None
            await retry_download(
                session,
                download_id=failed.id,
                pt_factory=lambda _site_id: cast(PtSiteAdapter, FakeTorrentSource(payload)),
                qb_factory=lambda: cast(QbittorrentAdapter, CapturingQb()),
                settings=Settings(_env_file=None),
            )
        rows = {
            row.event: row
            for row in await session.scalars(
                select(ActivityLog).where(ActivityLog.event.startswith("DOWNLOAD_"))
            )
        }

        assert set(rows) == {
            "DOWNLOAD_QUEUED",
            "DOWNLOAD_FAILED",
            "DOWNLOAD_RETRYING",
            "DOWNLOAD_SUBMITTED",
        }
        assert {(row.actor, row.trigger) for row in rows.values()} == {("owner", "MANUAL")}
        assert rows["DOWNLOAD_FAILED"].details["download_id"] == failed.id
        assert rows["DOWNLOAD_SUBMITTED"].details["download_id"] == failed.id
        assert rows["DOWNLOAD_SUBMITTED"].details["info_hash"] == info_hash


# ---------------------------------------------------------------------------
# Library confirmations
# ---------------------------------------------------------------------------


class _FakeNextFind:
    def __init__(self, missing: list[str], *, warnings: int = 0) -> None:
        self.missing = missing
        self.warnings = warnings

    async def authenticate(self) -> None:
        return None

    async def list_missing_media(self) -> MediaDiscoveryResult:
        return MediaDiscoveryResult(
            items=[
                MediaItemData(
                    source="nextfind",
                    source_item_id=item,
                    media_type=MediaType.MOVIE,
                    tmdb_id=1000 + index,
                    title=f"Missing {item}",
                    identity_confidence=IdentityConfidence.HIGH,
                    metadata_status=MetadataStatus.RESOLVED,
                    discovered_at=datetime(2026, 9, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 9, 1, tzinfo=UTC),
                )
                for index, item in enumerate(self.missing)
            ],
            warnings=[
                DiscoveryWarning(error_code="PAGE_LIMIT", message="short")
                for _ in range(self.warnings)
            ],
        )


@pytest.fixture(autouse=True)
def _fresh_sync_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrations, "_library_sync", integrations.LibrarySyncStatus())


@pytest.mark.asyncio
async def test_sync_records_confirmations_only_where_a_download_depends_on_them(
    session_factory,
) -> None:
    async with session_factory() as session:
        downloaded = await _media(session, "has-download", "Downloaded Title")
        await _download_for(session, downloaded)
        await _media(session, "no-download", "Plain Title")
        await session.commit()

        await sync_nextfind(session, _FakeNextFind(missing=["still-missing"]))  # type: ignore[arg-type]
        await session.refresh(downloaded)
        confirmed = (
            await session.scalars(
                select(ActivityLog).where(ActivityLog.event == "LIBRARY_CONFIRMED")
            )
        ).all()
        synced = (
            await session.scalars(
                select(ActivityLog).where(ActivityLog.event == "NEXTFIND_SYNCED")
            )
        ).one()

        assert downloaded.library_confirmed_at is not None
        assert [row.media_id for row in confirmed] == [downloaded.id]
        # Both titles were confirmed; only one was worth a row of its own.
        assert synced.details["newly_confirmed"] == 2
        assert library_listing_is_fresh(timedelta(minutes=15))


@pytest.mark.asyncio
async def test_a_title_reported_missing_again_loses_its_confirmation(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, "comes-back", "Returning Title")
        media.library_confirmed_at = datetime(2026, 9, 1, tzinfo=UTC)
        await _download_for(session, media)
        await session.commit()

        await sync_nextfind(session, _FakeNextFind(missing=["comes-back"]))  # type: ignore[arg-type]
        await session.refresh(media)
        revoked = (
            await session.scalars(
                select(ActivityLog).where(ActivityLog.event == "LIBRARY_CONFIRMATION_REVOKED")
            )
        ).all()

        assert media.library_confirmed_at is None
        assert [row.media_id for row in revoked] == [media.id]


@pytest.mark.asyncio
async def test_a_short_listing_confirms_nothing_and_is_not_fresh(session_factory) -> None:
    async with session_factory() as session:
        media = await _media(session, "unread", "Unread Title")
        await _download_for(session, media)
        await session.commit()

        await sync_nextfind(session, _FakeNextFind(missing=[], warnings=1))  # type: ignore[arg-type]
        await session.refresh(media)

        assert media.library_confirmed_at is None
        assert not library_listing_is_fresh(timedelta(minutes=15))


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_actor_columns_are_added_and_old_rows_stay_unattributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).parents[1]
    database = backend_root / f".test-migration-{uuid4().hex}.db"
    monkeypatch.setenv("DATABASE_URL", sqlite_url(database, async_driver=True))
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(config, "20260923_0025")

    engine = create_engine(sqlite_url(database, async_driver=False))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO activity_log (id, event, message, details, created_at)"
                " VALUES ('a1', 'NEXTFIND_SYNCED', 'old row', '{}', '2026-09-01 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sqlite_url(database, async_driver=False))
    columns = {column["name"] for column in inspect(engine).get_columns("activity_log")}
    with engine.connect() as connection:
        old = connection.execute(
            text("SELECT actor, trigger, reason FROM activity_log WHERE id = 'a1'")
        ).one()
    engine.dispose()
    assert {"actor", "trigger", "reason"} <= columns
    assert tuple(old) == (None, None, None)

    command.downgrade(config, "20260923_0025")
    engine = create_engine(sqlite_url(database, async_driver=False))
    columns = {column["name"] for column in inspect(engine).get_columns("activity_log")}
    engine.dispose()
    assert "actor" not in columns
    database.unlink()
