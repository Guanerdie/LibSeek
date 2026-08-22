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
    assert "country_codes" in {
        column["name"] for column in inspector.get_columns("library_media")
    }
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(sqlite_url(database, async_driver=False))
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
    database.unlink()
