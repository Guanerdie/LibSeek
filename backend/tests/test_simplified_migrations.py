from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def sqlite_url(path: Path, *, async_driver: bool) -> str:
    driver = "sqlite+aiosqlite" if async_driver else "sqlite"
    return f"{driver}:///{path.as_posix()}"


def test_initial_migration_builds_and_drops_the_simplified_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).parents[1]
    database = backend_root / f".test-migration-{uuid4().hex}.db"
    monkeypatch.setenv("DATABASE_URL", sqlite_url(database, async_driver=True))
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))

    command.upgrade(config, "head")

    engine = create_engine(sqlite_url(database, async_driver=False))
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "activity_log",
        "alembic_version",
        "automation_jobs",
        "automation_policy",
        "automation_runs",
        "downloads",
        "episodes",
        "library_media",
        "media_seasons",
        "release_candidates",
        "searches",
    }
    assert {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            item["options"].get("ondelete"),
        )
        for item in inspector.get_foreign_keys("downloads")
    } == {
        (("candidate_id",), "release_candidates", "RESTRICT"),
        (("media_id",), "library_media", "RESTRICT"),
    }
    library_columns = {column["name"] for column in inspector.get_columns("library_media")}
    assert "country_codes" in library_columns
    assert "original_language" in library_columns
    assert "search_titles" in library_columns
    assert "imdb_id" in library_columns
    assert "region_tags" in library_columns
    assert "library_confirmed_at" in library_columns
    candidate_columns = {
        column["name"]: column for column in inspector.get_columns("release_candidates")
    }
    assert candidate_columns["download_factor"]["nullable"] is True
    assert candidate_columns["collection_type"]["nullable"] is True
    assert candidate_columns["file_count"]["nullable"] is True
    automation_job_columns = {
        column["name"]: column for column in inspector.get_columns("automation_jobs")
    }
    assert automation_job_columns["retry_of_job_id"]["nullable"] is True
    assert automation_job_columns["superseded_at"]["nullable"] is True
    assert automation_job_columns["error_code"]["nullable"] is True
    automation_policy_columns = {
        column["name"] for column in inspector.get_columns("automation_policy")
    }
    assert "years" in automation_policy_columns
    assert {
        "cleanup_enabled",
        "cleanup_dry_run",
        "cleanup_after_days",
        "cleanup_min_seeding_days",
        "cleanup_grace_days",
        "cleanup_require_library_confirmed",
        "cleanup_daily_limit",
    } <= automation_policy_columns
    download_columns = {column["name"] for column in inspector.get_columns("downloads")}
    assert {
        "completed_at",
        "seeding_seconds",
        "cleanup_state",
        "cleanup_marked_at",
        "cleanup_deleted_at",
    } <= download_columns
    # SQLite rebuilds the whole table for every batch_alter_table, so the
    # constraints that were already there have to survive the new ones.
    policy_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("automation_policy")
    }
    assert {
        "ck_automation_retry_delay",
        "ck_automation_attempts",
        "ck_automation_daily_limit",
        "ck_automation_cleanup_after_days",
        "ck_automation_cleanup_min_seeding_days",
        "ck_automation_cleanup_grace_days",
        "ck_automation_cleanup_daily_limit",
    } <= policy_checks
    search_columns = {column["name"]: column for column in inspector.get_columns("searches")}
    assert search_columns["cache_key"]["nullable"] is True
    assert search_columns["cache_expires_at"]["nullable"] is True
    season_columns = {column["name"] for column in inspector.get_columns("media_seasons")}
    assert {"season_number", "episode_count", "aired_episode_count", "is_complete"} <= (
        season_columns
    )
    assert (
        ("media_id",),
        "library_media",
        "CASCADE",
    ) in {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            item["options"].get("ondelete"),
        )
        for item in inspector.get_foreign_keys("media_seasons")
    }
    assert (
        ("retry_of_job_id",),
        "automation_jobs",
        "SET NULL",
    ) in {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            item["options"].get("ondelete"),
        )
        for item in inspector.get_foreign_keys("automation_jobs")
    }
    download_columns = {
        column["name"]: column for column in inspector.get_columns("downloads")
    }
    assert download_columns["content_size_bytes"]["nullable"] is True
    assert download_columns["submitted_at"]["nullable"] is True
    download_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("downloads")
    }
    assert "ck_download_content_size_positive" in download_checks
    engine.dispose()

    command.downgrade(config, "20260823_0005")
    engine = create_engine(sqlite_url(database, async_driver=False))
    inspector = inspect(engine)
    assert "automation_runs" not in inspector.get_table_names()
    download_columns = {
        column["name"]: column for column in inspector.get_columns("downloads")
    }
    assert "content_size_bytes" not in download_columns
    assert "submitted_at" not in download_columns
    library_columns = {column["name"] for column in inspector.get_columns("library_media")}
    assert "search_titles" not in library_columns
    assert "imdb_id" not in library_columns
    assert "library_confirmed_at" not in library_columns
    candidate_columns = {
        column["name"] for column in inspector.get_columns("release_candidates")
    }
    assert "download_factor" not in candidate_columns
    assert "collection_type" not in candidate_columns
    assert "file_count" not in candidate_columns
    automation_job_columns = {
        column["name"] for column in inspector.get_columns("automation_jobs")
    }
    assert "retry_of_job_id" not in automation_job_columns
    assert "superseded_at" not in automation_job_columns
    assert "error_code" not in automation_job_columns
    automation_policy_columns = {
        column["name"] for column in inspector.get_columns("automation_policy")
    }
    assert "cleanup_enabled" not in automation_policy_columns
    assert "cleanup_after_days" not in automation_policy_columns
    download_columns = {column["name"] for column in inspector.get_columns("downloads")}
    assert "cleanup_state" not in download_columns
    assert "completed_at" not in download_columns
    search_columns = {column["name"] for column in inspector.get_columns("searches")}
    assert "cache_key" not in search_columns
    assert "cache_expires_at" not in search_columns
    assert "media_seasons" not in set(inspector.get_table_names())
    download_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("downloads")
    }
    assert "ck_download_content_size_positive" not in download_checks
    engine.dispose()

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(sqlite_url(database, async_driver=False))
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
    database.unlink()


def test_region_tags_are_backfilled_for_media_already_in_the_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).parents[1]
    database = backend_root / f".test-migration-{uuid4().hex}.db"
    monkeypatch.setenv("DATABASE_URL", sqlite_url(database, async_driver=True))
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(config, "20260909_0022")

    engine = create_engine(sqlite_url(database, async_driver=False))
    with engine.begin() as connection:
        for media_id, countries, language in (
            ("tagged", '["JP", "US"]', "ja"),
            ("untagged", "[]", None),
        ):
            connection.execute(
                text(
                    "INSERT INTO library_media (id, source, source_item_id, media_type, title,"
                    " state, discovered_at, updated_at, country_codes, original_language)"
                    " VALUES (:id, 'nextfind', :id, 'tv', :id, 'READY',"
                    " '2026-09-01 00:00:00', '2026-09-01 00:00:00', :countries, :language)"
                ),
                {"id": media_id, "countries": countries, "language": language},
            )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sqlite_url(database, async_driver=False))
    with engine.connect() as connection:
        tags = dict(connection.execute(text("SELECT id, region_tags FROM library_media")).all())
    engine.dispose()
    assert tags == {"tagged": "|欧美|日本|", "untagged": ""}

    command.downgrade(config, "20260909_0022")
    engine = create_engine(sqlite_url(database, async_driver=False))
    library_columns = {column["name"] for column in inspect(engine).get_columns("library_media")}
    engine.dispose()
    assert "region_tags" not in library_columns
    database.unlink()


def test_existing_downloads_are_held_back_from_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backlog is the operator's to sort; only new downloads get cleaned up."""

    backend_root = Path(__file__).parents[1]
    database = backend_root / f".test-migration-{uuid4().hex}.db"
    monkeypatch.setenv("DATABASE_URL", sqlite_url(database, async_driver=True))
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(config, "20260920_0024")

    stamp = "2026-09-01 00:00:00"
    engine = create_engine(sqlite_url(database, async_driver=False))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO library_media (id, source, source_item_id, media_type, title,"
                " state, discovered_at, updated_at, country_codes)"
                " VALUES ('m1', 'nextfind', 'm1', 'movie', 'm1', 'COMPLETE',"
                " :stamp, :stamp, '[]')"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO searches (id, media_id, site_ids, state, created_at)"
                " VALUES ('s1', 'm1', '[\"avistaz\"]', 'SUCCEEDED', :stamp)"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO release_candidates (id, search_id, site_id, torrent_id, title,"
                " season_coverage, episode_coverage, score, reasons, warnings, created_at)"
                " VALUES ('c1', 's1', 'avistaz', 't1', 'Example', '[]', '[]', 0.9,"
                " '[]', '[]', :stamp)"
            ),
            {"stamp": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO downloads (id, media_id, candidate_id, name, state, progress,"
                " download_speed, upload_speed, ratio, info_hash, created_at, updated_at)"
                " VALUES ('d1', 'm1', 'c1', 'Example.mkv', 'SEEDING', 1, 0, 0, 1.5,"
                " :hash, :stamp, :stamp)"
            ),
            {"stamp": stamp, "hash": "a" * 40},
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sqlite_url(database, async_driver=False))
    with engine.connect() as connection:
        state = connection.execute(
            text("SELECT cleanup_state FROM downloads WHERE id = 'd1'")
        ).scalar_one()
    engine.dispose()
    assert state == "HELD"

    command.downgrade(config, "20260920_0024")
    database.unlink()
