"""Add guarded download executor metadata and monitored download jobs.

Revision ID: 20260811_0007
Revises: 20260811_0006
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0007"
down_revision: str | None = "20260811_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXECUTION_CHECKS = {
    "ck_download_executions_attempts": (
        "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts"
    ),
    "ck_download_executions_actual_info_hash_shape": (
        "actual_info_hash IS NULL OR "
        "actual_info_hash ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'"
    ),
    "ck_download_executions_actual_info_hash_v1_shape": (
        "actual_info_hash_v1 IS NULL OR actual_info_hash_v1 ~ '^[0-9a-f]{40}$'"
    ),
    "ck_download_executions_actual_info_hash_v2_shape": (
        "actual_info_hash_v2 IS NULL OR actual_info_hash_v2 ~ '^[0-9a-f]{64}$'"
    ),
    "ck_download_executions_actual_info_hash_binding": (
        "actual_info_hash IS NULL "
        "OR (actual_info_hash_v1 IS NOT NULL "
        "AND actual_info_hash = actual_info_hash_v1) "
        "OR (actual_info_hash_v2 IS NOT NULL "
        "AND (actual_info_hash = actual_info_hash_v2 "
        "OR actual_info_hash = substr(actual_info_hash_v2, 1, 40)))"
    ),
    "ck_download_executions_lease_fields": (
        "((locked_at IS NULL AND locked_by IS NULL AND lease_token IS NULL) OR "
        "(locked_at IS NOT NULL AND locked_by IS NOT NULL AND lease_token IS NOT NULL))"
    ),
    "ck_download_executions_lease_status": (
        "((status IN ('VALIDATING', 'SUBMITTING') AND locked_at IS NOT NULL) OR "
        "(status NOT IN ('VALIDATING', 'SUBMITTING') AND locked_at IS NULL))"
    ),
    "ck_download_executions_validation_bundle": (
        "((actual_info_hash IS NULL AND actual_info_hash_v1 IS NULL "
        "AND actual_info_hash_v2 IS NULL AND actual_size_bytes IS NULL "
        "AND actual_file_count IS NULL AND validated_at IS NULL "
        "AND submitted_at IS NULL) OR "
        "(actual_info_hash IS NOT NULL "
        "AND (actual_info_hash_v1 IS NOT NULL OR actual_info_hash_v2 IS NOT NULL) "
        "AND actual_size_bytes > 0 AND actual_file_count > 0 "
        "AND validated_at IS NOT NULL AND submitted_at IS NOT NULL))"
    ),
    "ck_download_executions_submission_metadata": (
        "((status IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
        "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
        "AND actual_info_hash IS NOT NULL) OR "
        "(status NOT IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
        "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
        "AND actual_info_hash IS NULL))"
    ),
    "ck_download_executions_verified_status": (
        "((status IN ('SUBMITTED', 'ALREADY_PRESENT') AND verified_at IS NOT NULL) OR "
        "(status NOT IN ('SUBMITTED', 'ALREADY_PRESENT') AND verified_at IS NULL))"
    ),
    "ck_download_executions_reconciliation_fields": (
        "((reconciliation_requested_by IS NULL AND reconciliation_requested_at IS NULL) "
        "OR (reconciliation_requested_by IS NOT NULL "
        "AND reconciliation_requested_at IS NOT NULL))"
    ),
    "ck_download_executions_reconciliation_status": (
        "status <> 'RECONCILIATION_PENDING' OR "
        "(reconciliation_requested_by IS NOT NULL "
        "AND reconciliation_requested_at IS NOT NULL)"
    ),
    "ck_download_executions_reconciliation_reason": (
        "reconciliation_reason IS NULL OR "
        "(reconciliation_requested_by IS NOT NULL "
        "AND reconciliation_requested_at IS NOT NULL)"
    ),
}


def download_job_status() -> sa.Enum:
    return sa.Enum(
        "QUEUED",
        "DOWNLOADING",
        "PAUSED",
        "CHECKING",
        "SEEDING",
        "COMPLETED",
        "MISSING",
        "ERROR",
        name="downloadjobstatus",
        native_enum=False,
        length=20,
    )


def hnr_status() -> sa.Enum:
    return sa.Enum(
        "UNKNOWN",
        "AT_RISK",
        "SATISFIED",
        name="hnrstatus",
        native_enum=False,
        length=20,
    )


def upgrade() -> None:
    op.drop_index("uq_approval_active_candidate", table_name="approval_requests")
    op.create_index(
        "uq_approval_active_candidate",
        "approval_requests",
        ["torrent_candidate_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED', 'EXECUTING')"),
        sqlite_where=sa.text("status IN ('PENDING', 'APPROVED', 'EXECUTING')"),
    )

    op.add_column(
        "download_executions",
        sa.Column("actual_info_hash_v1", sa.String(40), nullable=True),
    )
    op.add_column(
        "download_executions",
        sa.Column("actual_info_hash_v2", sa.String(64), nullable=True),
    )
    op.add_column(
        "download_executions",
        sa.Column("actual_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "download_executions",
        sa.Column("actual_file_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "download_executions",
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "download_executions",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "download_executions",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM download_executions
                WHERE status IN (
                    'VALIDATING', 'SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT',
                    'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED',
                    'RECONCILIATION_PENDING'
                )
                    OR actual_info_hash IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    '0007 cannot infer validated torrent metadata for an advanced '
                    'download execution; reconcile or remove the phase-4 test record first'
                    USING ERRCODE = '55000';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM execution_intents
                WHERE (status = 'CONSUMED') IS DISTINCT FROM
                    (consumed_at IS NOT NULL AND consumed_by IS NOT NULL)
            ) THEN
                RAISE EXCEPTION
                    '0007 found inconsistent execution intent consumption fields'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )

    for name, condition in EXECUTION_CHECKS.items():
        op.create_check_constraint(name, "download_executions", condition)

    op.create_check_constraint(
        "ck_execution_intents_consumed_fields",
        "execution_intents",
        "((status = 'CONSUMED' AND consumed_at IS NOT NULL AND consumed_by IS NOT NULL) "
        "OR (status <> 'CONSUMED' AND consumed_at IS NULL AND consumed_by IS NULL))",
    )

    op.create_table(
        "download_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("download_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            sa.String(36),
            sa.ForeignKey("approval_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "media_item_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", download_job_status(), nullable=False),
        sa.Column("release_title", sa.String(1000), nullable=False),
        sa.Column("info_hash_v1", sa.String(40)),
        sa.Column("info_hash_v2", sa.String(64)),
        sa.Column("save_path_ref", sa.String(180), nullable=False),
        sa.Column("category", sa.String(300), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("download_speed_bps", sa.BigInteger(), nullable=False),
        sa.Column("upload_speed_bps", sa.BigInteger(), nullable=False),
        sa.Column("downloaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("hnr_status", hnr_status(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "info_hash_v1 IS NOT NULL OR info_hash_v2 IS NOT NULL",
            name="ck_download_jobs_info_hash_required",
        ),
        sa.CheckConstraint(
            "info_hash_v1 IS NULL OR info_hash_v1 ~ '^[0-9a-f]{40}$'",
            name="ck_download_jobs_info_hash_v1_shape",
        ),
        sa.CheckConstraint(
            "info_hash_v2 IS NULL OR info_hash_v2 ~ '^[0-9a-f]{64}$'",
            name="ck_download_jobs_info_hash_v2_shape",
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND file_count > 0",
            name="ck_download_jobs_content_bounds",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="ck_download_jobs_progress",
        ),
        sa.CheckConstraint(
            "download_speed_bps >= 0 AND upload_speed_bps >= 0 "
            "AND downloaded_bytes >= 0 AND uploaded_bytes >= 0 AND ratio >= -1",
            name="ck_download_jobs_transfer_metrics",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR completed_at IS NOT NULL",
            name="ck_download_jobs_completed_at",
        ),
        sa.UniqueConstraint("execution_id", name="uq_download_jobs_execution_id"),
    )
    op.create_index("ix_download_jobs_execution_id", "download_jobs", ["execution_id"])
    op.create_index("ix_download_jobs_approval_id", "download_jobs", ["approval_id"])
    op.create_index("ix_download_jobs_media_item_id", "download_jobs", ["media_item_id"])
    op.create_index("ix_download_jobs_status", "download_jobs", ["status"])
    op.create_index("ix_download_jobs_last_seen_at", "download_jobs", ["last_seen_at"])
    op.create_index("ix_download_jobs_created_at", "download_jobs", ["created_at"])

    op.create_table(
        "download_job_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "download_job_id",
            sa.String(36),
            sa.ForeignKey("download_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("sanitized_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_download_job_events_download_job_id",
        "download_job_events",
        ["download_job_id"],
    )
    op.create_index(
        "ix_download_job_events_event_type",
        "download_job_events",
        ["event_type"],
    )
    op.create_index(
        "ix_download_job_events_created_at",
        "download_job_events",
        ["created_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION unin_guard_download_execution_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'download_executions are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.approval_id IS DISTINCT FROM OLD.approval_id
                OR NEW.intent_id IS DISTINCT FROM OLD.intent_id
                OR NEW.idempotency_key_sha256 IS DISTINCT FROM OLD.idempotency_key_sha256
                OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
                OR NEW.approval_snapshot_hash IS DISTINCT FROM OLD.approval_snapshot_hash
                OR NEW.plan_hash IS DISTINCT FROM OLD.plan_hash
                OR NEW.qb_target_fingerprint IS DISTINCT FROM OLD.qb_target_fingerprint
                OR NEW.launch_mode IS DISTINCT FROM OLD.launch_mode
                OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR (
                    OLD.actual_info_hash IS NOT NULL
                    AND NEW.actual_info_hash IS DISTINCT FROM OLD.actual_info_hash
                )
                OR (
                    OLD.actual_info_hash_v1 IS NOT NULL
                    AND NEW.actual_info_hash_v1 IS DISTINCT FROM OLD.actual_info_hash_v1
                )
                OR (
                    OLD.actual_info_hash_v2 IS NOT NULL
                    AND NEW.actual_info_hash_v2 IS DISTINCT FROM OLD.actual_info_hash_v2
                )
                OR (
                    OLD.actual_size_bytes IS NOT NULL
                    AND NEW.actual_size_bytes IS DISTINCT FROM OLD.actual_size_bytes
                )
                OR (
                    OLD.actual_file_count IS NOT NULL
                    AND NEW.actual_file_count IS DISTINCT FROM OLD.actual_file_count
                )
                OR (
                    OLD.validated_at IS NOT NULL
                    AND NEW.validated_at IS DISTINCT FROM OLD.validated_at
                )
                OR (
                    OLD.submitted_at IS NOT NULL
                    AND NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
                )
                OR (
                    OLD.verified_at IS NOT NULL
                    AND NEW.verified_at IS DISTINCT FROM OLD.verified_at
                )
            THEN
                RAISE EXCEPTION 'download execution immutable fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION unin_guard_download_job_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'download_jobs are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.execution_id IS DISTINCT FROM OLD.execution_id
                OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
                OR NEW.media_item_id IS DISTINCT FROM OLD.media_item_id
                OR NEW.release_title IS DISTINCT FROM OLD.release_title
                OR NEW.info_hash_v1 IS DISTINCT FROM OLD.info_hash_v1
                OR NEW.info_hash_v2 IS DISTINCT FROM OLD.info_hash_v2
                OR NEW.save_path_ref IS DISTINCT FROM OLD.save_path_ref
                OR NEW.category IS DISTINCT FROM OLD.category
                OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
                OR NEW.file_count IS DISTINCT FROM OLD.file_count
                OR NEW.started_at IS DISTINCT FROM OLD.started_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'download job immutable fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_download_job_immutable
        BEFORE UPDATE OR DELETE ON download_jobs
        FOR EACH ROW EXECUTE FUNCTION unin_guard_download_job_immutable()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_download_job_event_immutable
        BEFORE UPDATE OR DELETE ON download_job_events
        FOR EACH ROW EXECUTE FUNCTION unin_reject_immutable_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM download_jobs
            ) OR EXISTS (
                SELECT 1 FROM download_job_events
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while append-preserved download jobs or events exist'
                    USING ERRCODE = '55000';
            END IF;
            IF EXISTS (
                SELECT 1 FROM approval_requests WHERE status = 'EXECUTING'
            ) OR EXISTS (
                SELECT 1 FROM download_executions WHERE status = 'RETRY_WAIT'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while EXECUTING approvals or RETRY_WAIT executions exist'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_download_job_event_immutable ON download_job_events")
    op.execute("DROP TRIGGER IF EXISTS trg_download_job_immutable ON download_jobs")
    op.execute("DROP FUNCTION IF EXISTS unin_guard_download_job_immutable()")
    op.drop_table("download_job_events")
    op.drop_table("download_jobs")

    op.drop_constraint(
        "ck_execution_intents_consumed_fields",
        "execution_intents",
        type_="check",
    )
    for constraint_name in reversed(EXECUTION_CHECKS):
        op.drop_constraint(
            constraint_name,
            "download_executions",
            type_="check",
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION unin_guard_download_execution_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'download_executions are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.approval_id IS DISTINCT FROM OLD.approval_id
                OR NEW.intent_id IS DISTINCT FROM OLD.intent_id
                OR NEW.idempotency_key_sha256 IS DISTINCT FROM OLD.idempotency_key_sha256
                OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
                OR NEW.approval_snapshot_hash IS DISTINCT FROM OLD.approval_snapshot_hash
                OR NEW.plan_hash IS DISTINCT FROM OLD.plan_hash
                OR NEW.qb_target_fingerprint IS DISTINCT FROM OLD.qb_target_fingerprint
                OR NEW.launch_mode IS DISTINCT FROM OLD.launch_mode
                OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR (
                    OLD.actual_info_hash IS NOT NULL
                    AND NEW.actual_info_hash IS DISTINCT FROM OLD.actual_info_hash
                )
            THEN
                RAISE EXCEPTION 'download execution immutable fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for column_name in (
        "verified_at",
        "submitted_at",
        "validated_at",
        "actual_file_count",
        "actual_size_bytes",
        "actual_info_hash_v2",
        "actual_info_hash_v1",
    ):
        op.drop_column("download_executions", column_name)

    op.drop_index("uq_approval_active_candidate", table_name="approval_requests")
    op.create_index(
        "uq_approval_active_candidate",
        "approval_requests",
        ["torrent_candidate_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED')"),
        sqlite_where=sa.text("status IN ('PENDING', 'APPROVED')"),
    )
