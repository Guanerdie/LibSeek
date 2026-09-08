from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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
    search_columns = {column["name"]: column for column in inspector.get_columns("searches")}
    assert search_columns["cache_key"]["nullable"] is True
    assert search_columns["cache_expires_at"]["nullable"] is True
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
    search_columns = {column["name"] for column in inspector.get_columns("searches")}
    assert "cache_key" not in search_columns
    assert "cache_expires_at" not in search_columns
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
