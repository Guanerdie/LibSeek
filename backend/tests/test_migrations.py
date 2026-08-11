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


def load_download_executor_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260811_0007_download_executor_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260811_0007", path)
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


def test_download_executor_migration_has_named_state_constraints() -> None:
    migration = load_download_executor_migration()
    assert migration.revision == "20260811_0007"
    assert migration.down_revision == "20260811_0006"

    operation_names = (
        "drop_index",
        "create_index",
        "add_column",
        "create_check_constraint",
        "create_table",
        "execute",
    )
    patches = [patch.object(migration.op, name) for name in operation_names]
    mocks = [item.start() for item in patches]
    try:
        migration.upgrade()
    finally:
        for item in reversed(patches):
            item.stop()

    create_check_constraint = mocks[3]
    created_names = {call.args[0] for call in create_check_constraint.call_args_list}
    assert created_names == {
        *migration.EXECUTION_CHECKS,
        "ck_execution_intents_consumed_fields",
    }

    create_table = mocks[4]
    download_job_call = next(
        call for call in create_table.call_args_list if call.args[0] == "download_jobs"
    )
    table_check_names = {
        value.name
        for value in download_job_call.args[1:]
        if isinstance(value, sa.CheckConstraint)
    }
    assert table_check_names == {
        "ck_download_jobs_info_hash_required",
        "ck_download_jobs_info_hash_v1_shape",
        "ck_download_jobs_info_hash_v2_shape",
        "ck_download_jobs_content_bounds",
        "ck_download_jobs_progress",
        "ck_download_jobs_transfer_metrics",
        "ck_download_jobs_completed_at",
    }

    execute = mocks[5]
    emitted_sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "0007 cannot infer validated torrent metadata" in emitted_sql
    assert "inconsistent execution intent consumption fields" in emitted_sql
    assert "OLD.actual_info_hash_v1 IS NOT NULL" in emitted_sql
    assert "OLD.validated_at IS NOT NULL" in emitted_sql
    assert "OLD.verified_at IS NOT NULL" in emitted_sql

    downgrade_operations = (
        "execute",
        "drop_table",
        "drop_constraint",
        "drop_column",
        "drop_index",
        "create_index",
    )
    downgrade_patches = [
        patch.object(migration.op, name) for name in downgrade_operations
    ]
    downgrade_mocks = [item.start() for item in downgrade_patches]
    try:
        migration.downgrade()
    finally:
        for item in reversed(downgrade_patches):
            item.stop()
    downgrade_sql = "\n".join(
        str(call.args[0]) for call in downgrade_mocks[0].call_args_list
    )
    assert "SELECT 1 FROM download_jobs" in downgrade_sql
    assert "SELECT 1 FROM download_job_events" in downgrade_sql
    assert "append-preserved download jobs or events" in downgrade_sql
    assert "approval_requests WHERE status = 'EXECUTING'" in downgrade_sql
    assert "download_executions WHERE status = 'RETRY_WAIT'" in downgrade_sql
