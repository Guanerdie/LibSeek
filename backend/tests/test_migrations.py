from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import sqlalchemy as sa


def load_local_episode_matrix_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260811_0005_local_episode_matrix.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260811_0005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_episode_matrix_migration_adds_and_drops_json_column() -> None:
    migration = load_local_episode_matrix_migration()
    assert migration.revision == "20260811_0005"
    assert migration.down_revision == "20260811_0004"

    with patch.object(migration.op, "add_column") as add_column:
        migration.upgrade()
    table_name, column = add_column.call_args.args
    assert table_name == "media_items"
    assert column.name == "local_episode_matrix"
    assert isinstance(column.type, sa.JSON)
    assert column.nullable is True

    with patch.object(migration.op, "drop_column") as drop_column:
        migration.downgrade()
    drop_column.assert_called_once_with("media_items", "local_episode_matrix")
