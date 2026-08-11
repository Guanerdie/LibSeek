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


def load_conservative_automation_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260811_0008_conservative_automation.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260811_0008", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_media_import_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260811_0009_media_import_plan_control.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260811_0009", path)
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


def test_conservative_automation_migration_is_seeded_guarded_and_reversible() -> None:
    migration = load_conservative_automation_migration()
    assert migration.revision == "20260811_0008"
    assert migration.down_revision == "20260811_0007"

    operation_names = (
        "add_column",
        "create_foreign_key",
        "create_index",
        "create_table",
        "create_check_constraint",
        "execute",
        "drop_constraint",
    )
    patches = [patch.object(migration.op, name) for name in operation_names]
    mocks = [item.start() for item in patches]
    try:
        migration.upgrade()
    finally:
        for item in reversed(patches):
            item.stop()

    created_tables = {call.args[0] for call in mocks[3].call_args_list}
    assert created_tables == {
        "automation_policy_revisions",
        "automation_policy_heads",
        "automation_decisions",
    }
    constraint_names = {call.args[0] for call in mocks[4].call_args_list}
    assert constraint_names == {
        "ck_download_executions_submission_metadata",
        "ck_execution_intents_automation_binding",
        "ck_download_executions_automation_binding",
    }
    created_indexes = {call.args[0] for call in mocks[2].call_args_list}
    assert "uq_torrent_search_active_media_site" in created_indexes
    sql = "\n".join(str(call.args[0]) for call in mocks[5].call_args_list)
    assert migration.DEFAULT_POLICY_HASH in sql
    assert "'MANUAL', 'MANUAL', 'MANUAL', 'MANUAL'" in sql
    assert "trg_automation_policy_revision_immutable" in sql
    assert "trg_automation_policy_head_guard" in sql
    assert "trg_download_execution_automation_binding_immutable" in sql

    downgrade_operations = (
        "execute",
        "drop_constraint",
        "drop_index",
        "drop_column",
        "drop_table",
        "create_check_constraint",
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
    assert "SELECT 1 FROM automation_decisions" in downgrade_sql
    assert "count(*) FROM automation_policy_revisions" in downgrade_sql
    assert migration.DEFAULT_POLICY_HASH in downgrade_sql
    assert "revision_no <> 1" in downgrade_sql
    assert "execution_intents WHERE origin = 'AUTOMATION'" in downgrade_sql
    assert "cannot downgrade with automation audit or custom policy records" in downgrade_sql


def test_media_import_migration_is_append_only_rerunnable_and_fail_closed() -> None:
    migration = load_media_import_migration()
    assert migration.revision == "20260811_0009"
    assert migration.down_revision == "20260811_0008"

    operation_names = ("create_table", "create_index", "execute")
    patches = [patch.object(migration.op, name) for name in operation_names]
    mocks = [item.start() for item in patches]
    try:
        migration.upgrade()
    finally:
        for item in reversed(patches):
            item.stop()

    created_tables = {call.args[0] for call in mocks[0].call_args_list}
    assert created_tables == {
        "media_import_requests",
        "media_import_plans",
        "media_import_preflights",
        "media_import_events",
    }
    for create_call in mocks[0].call_args_list:
        for value in create_call.args[1:]:
            if not isinstance(value, sa.Column):
                continue
            for foreign_key in value.foreign_keys:
                assert foreign_key.ondelete == "RESTRICT"
    plan_call = next(
        call
        for call in mocks[0].call_args_list
        if call.args[0] == "media_import_plans"
    )
    proposed_operation = next(
        value
        for value in plan_call.args[1:]
        if isinstance(value, sa.Column) and value.name == "proposed_operation"
    )
    assert isinstance(proposed_operation.type, sa.Enum)
    assert proposed_operation.type.create_constraint is True
    assert proposed_operation.type.name == "ck_media_import_plans_operation_closed"
    preflight_call = next(
        call
        for call in mocks[0].call_args_list
        if call.args[0] == "media_import_preflights"
    )
    overall_status = next(
        value
        for value in preflight_call.args[1:]
        if isinstance(value, sa.Column) and value.name == "overall_status"
    )
    assert isinstance(overall_status.type, sa.Enum)
    assert overall_status.type.create_constraint is True
    assert overall_status.type.name == "ck_media_import_preflights_status_closed"
    unique_constraint_names = {
        value.name
        for value in preflight_call.args[1:]
        if isinstance(value, sa.UniqueConstraint)
    }
    assert unique_constraint_names == {
        "uq_media_import_preflights_preflight_hash"
    }
    created_indexes = {call.args[0] for call in mocks[1].call_args_list}
    assert "ix_media_import_preflights_request_latest" in created_indexes
    active_index = next(
        call
        for call in mocks[1].call_args_list
        if call.args[0] == "uq_media_import_active_download_job"
    )
    assert active_index.kwargs["unique"] is True
    active_predicate = str(active_index.kwargs["postgresql_where"])
    assert "PREFLIGHT_REQUIRED" in active_predicate
    assert "REVIEW_REQUIRED" in active_predicate
    assert "APPROVED_PLAN_ONLY" in active_predicate
    sql = "\n".join(str(call.args[0]) for call in mocks[2].call_args_list)
    assert "trg_media_import_request_guard" in sql
    assert "trg_media_import_plan_immutable" in sql
    assert "trg_media_import_preflight_immutable" in sql
    assert "trg_media_import_event_immutable" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "OLD.status = 'REVIEW_REQUIRED'" in sql
    assert "'PREFLIGHT_REQUIRED', 'APPROVED_PLAN_ONLY', 'REJECTED'" in sql
    assert "terminal media import requests are immutable" in sql
    assert "NEW.decision_acknowledgements::jsonb IS DISTINCT FROM" in sql
    assert "OLD.decision_acknowledgements::jsonb" in sql

    downgrade_operations = ("execute", "drop_table")
    downgrade_patches = [
        patch.object(migration.op, name) for name in downgrade_operations
    ]
    downgrade_mocks = [item.start() for item in downgrade_patches]
    try:
        migration.downgrade()
    finally:
        for item in reversed(downgrade_patches):
            item.stop()
    downgrade_execute_sql = [
        str(call.args[0]) for call in downgrade_mocks[0].call_args_list
    ]
    expected_lock = (
        "LOCK TABLE media_import_requests, media_import_plans, "
        "media_import_preflights, media_import_events IN ACCESS EXCLUSIVE MODE"
    )
    assert " ".join(downgrade_execute_sql[0].split()) == expected_lock
    guard_index = next(
        index
        for index, statement in enumerate(downgrade_execute_sql)
        if "DO $$" in statement and "IF EXISTS" in statement
    )
    assert guard_index > 0
    downgrade_sql = "\n".join(downgrade_execute_sql)
    for table in (
        "media_import_requests",
        "media_import_plans",
        "media_import_preflights",
        "media_import_events",
    ):
        assert f"SELECT 1 FROM {table}" in downgrade_sql
    assert "cannot downgrade while append-preserved media import records exist" in downgrade_sql
