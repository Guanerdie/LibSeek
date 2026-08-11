"""Add plan-only optional media import control records.

Revision ID: 20260811_0009
Revises: 20260811_0008
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0009"
down_revision: str | None = "20260811_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def media_import_status() -> sa.Enum:
    return sa.Enum(
        "PREFLIGHT_REQUIRED",
        "REVIEW_REQUIRED",
        "APPROVED_PLAN_ONLY",
        "REJECTED",
        "REVOKED",
        name="mediaimportstatus",
        native_enum=False,
        length=30,
    )


def media_import_operation() -> sa.Enum:
    return sa.Enum(
        "HARDLINK",
        "COPY",
        name="ck_media_import_plans_operation_closed",
        native_enum=False,
        create_constraint=True,
        length=20,
    )


def preflight_status() -> sa.Enum:
    return sa.Enum(
        "PASS",
        "WARNING",
        "BLOCKED",
        "UNKNOWN",
        name="ck_media_import_preflights_status_closed",
        native_enum=False,
        create_constraint=True,
        length=20,
    )


def upgrade() -> None:
    op.create_table(
        "media_import_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "download_job_id",
            sa.String(36),
            sa.ForeignKey("download_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "media_item_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("download_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", media_import_status(), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(120)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_by", sa.String(120)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.String(500)),
        sa.Column("revoked_by", sa.String(120)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.String(500)),
        sa.Column("decision_acknowledgements", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "((status IN ('PREFLIGHT_REQUIRED', 'REVIEW_REQUIRED') "
            "AND approved_by IS NULL AND approved_at IS NULL "
            "AND rejected_by IS NULL AND rejected_at IS NULL "
            "AND rejection_reason IS NULL AND revoked_by IS NULL "
            "AND revoked_at IS NULL AND revocation_reason IS NULL "
            "AND decision_acknowledgements IS NULL) OR "
            "(status = 'APPROVED_PLAN_ONLY' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL AND rejected_by IS NULL "
            "AND rejected_at IS NULL AND rejection_reason IS NULL "
            "AND revoked_by IS NULL AND revoked_at IS NULL "
            "AND revocation_reason IS NULL AND decision_acknowledgements IS NOT NULL) OR "
            "(status = 'REJECTED' AND approved_by IS NULL AND approved_at IS NULL "
            "AND rejected_by IS NOT NULL AND rejected_at IS NOT NULL "
            "AND revoked_by IS NULL AND revoked_at IS NULL "
            "AND revocation_reason IS NULL AND decision_acknowledgements IS NULL) OR "
            "(status = 'REVOKED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND rejected_by IS NULL AND rejected_at IS NULL "
            "AND rejection_reason IS NULL AND revoked_by IS NOT NULL "
            "AND revoked_at IS NOT NULL AND decision_acknowledgements IS NOT NULL))",
            name="ck_media_import_requests_decision_shape",
        ),
    )
    for column in (
        "download_job_id",
        "media_item_id",
        "execution_id",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_media_import_requests_{column}",
            "media_import_requests",
            [column],
        )
    op.create_index(
        "uq_media_import_active_download_job",
        "media_import_requests",
        ["download_job_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PREFLIGHT_REQUIRED', 'REVIEW_REQUIRED', "
            "'APPROVED_PLAN_ONLY')"
        ),
        sqlite_where=sa.text(
            "status IN ('PREFLIGHT_REQUIRED', 'REVIEW_REQUIRED', "
            "'APPROVED_PLAN_ONLY')"
        ),
    )

    op.create_table(
        "media_import_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(36),
            sa.ForeignKey("media_import_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "download_job_id",
            sa.String(36),
            sa.ForeignKey("download_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "media_item_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("download_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("proposed_operation", media_import_operation(), nullable=False),
        sa.Column("source_manifest", sa.JSON(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("target_mapping", sa.JSON(), nullable=False),
        sa.Column("target_mapping_hash", sa.String(64), nullable=False),
        sa.Column("job_summary_snapshot", sa.JSON(), nullable=False),
        sa.Column("summary_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("source_retention", sa.Boolean(), nullable=False),
        sa.Column("overwrite_allowed", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode = 'PLAN_ONLY_NO_FILE_OPERATION'",
            name="ck_media_import_plans_mode",
        ),
        sa.CheckConstraint(
            "source_retention = true AND overwrite_allowed = false",
            name="ck_media_import_plans_non_destructive",
        ),
        sa.CheckConstraint(
            "source_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_plans_manifest_hash",
        ),
        sa.CheckConstraint(
            "target_mapping_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_plans_target_hash",
        ),
        sa.CheckConstraint(
            "summary_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_plans_summary_hash",
        ),
        sa.CheckConstraint(
            "config_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_plans_config_hash",
        ),
        sa.CheckConstraint(
            "plan_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_plans_plan_hash",
        ),
        sa.UniqueConstraint("request_id", name="uq_media_import_plans_request_id"),
        sa.UniqueConstraint("plan_hash", name="uq_media_import_plans_plan_hash"),
    )
    for column in (
        "request_id",
        "download_job_id",
        "media_item_id",
        "execution_id",
        "created_at",
    ):
        op.create_index(
            f"ix_media_import_plans_{column}", "media_import_plans", [column]
        )

    op.create_table(
        "media_import_preflights",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(36),
            sa.ForeignKey("media_import_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("media_import_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("overall_status", preflight_status(), nullable=False),
        sa.Column("inspection_snapshot", sa.JSON(), nullable=False),
        sa.Column("inspection_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("preflight_hash", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("checked_by", sa.String(120), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "inspection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_preflights_inspection_hash",
        ),
        sa.CheckConstraint(
            "result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_preflights_result_hash",
        ),
        sa.CheckConstraint(
            "preflight_hash ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_preflights_preflight_hash",
        ),
        sa.CheckConstraint(
            "config_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_media_import_preflights_config_hash",
        ),
        sa.UniqueConstraint(
            "preflight_hash", name="uq_media_import_preflights_preflight_hash"
        ),
    )
    for column in (
        "request_id",
        "plan_id",
        "overall_status",
        "created_at",
    ):
        op.create_index(
            f"ix_media_import_preflights_{column}",
            "media_import_preflights",
            [column],
        )
    op.create_index(
        "ix_media_import_preflights_request_latest",
        "media_import_preflights",
        ["request_id", "created_at", "id"],
    )

    op.create_table(
        "media_import_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(36),
            sa.ForeignKey("media_import_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("from_status", sa.String(30)),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("sanitized_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("request_id", "event_type", "created_at"):
        op.create_index(
            f"ix_media_import_events_{column}", "media_import_events", [column]
        )

    op.execute(
        """
        CREATE FUNCTION unin_guard_media_import_request()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'media import requests are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.status IN ('REJECTED', 'REVOKED')
                OR (OLD.status = 'APPROVED_PLAN_ONLY'
                    AND NEW.status = 'APPROVED_PLAN_ONLY')
            THEN
                RAISE EXCEPTION 'terminal media import requests are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.download_job_id IS DISTINCT FROM OLD.download_job_id
                OR NEW.media_item_id IS DISTINCT FROM OLD.media_item_id
                OR NEW.execution_id IS DISTINCT FROM OLD.execution_id
                OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR (OLD.approved_by IS NOT NULL
                    AND NEW.approved_by IS DISTINCT FROM OLD.approved_by)
                OR (OLD.approved_at IS NOT NULL
                    AND NEW.approved_at IS DISTINCT FROM OLD.approved_at)
                OR (OLD.rejected_by IS NOT NULL
                    AND NEW.rejected_by IS DISTINCT FROM OLD.rejected_by)
                OR (OLD.rejected_at IS NOT NULL
                    AND NEW.rejected_at IS DISTINCT FROM OLD.rejected_at)
                OR (OLD.rejection_reason IS NOT NULL
                    AND NEW.rejection_reason IS DISTINCT FROM OLD.rejection_reason)
                OR (OLD.revoked_by IS NOT NULL
                    AND NEW.revoked_by IS DISTINCT FROM OLD.revoked_by)
                OR (OLD.revoked_at IS NOT NULL
                    AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
                OR (OLD.revocation_reason IS NOT NULL
                    AND NEW.revocation_reason IS DISTINCT FROM OLD.revocation_reason)
                OR (OLD.decision_acknowledgements IS NOT NULL
                    AND NEW.decision_acknowledgements::jsonb IS DISTINCT FROM
                        OLD.decision_acknowledgements::jsonb)
            THEN
                RAISE EXCEPTION 'media import immutable fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'PREFLIGHT_REQUIRED'
                    AND NEW.status IN ('REVIEW_REQUIRED', 'REJECTED'))
                OR (OLD.status = 'REVIEW_REQUIRED'
                    AND NEW.status IN (
                        'PREFLIGHT_REQUIRED', 'APPROVED_PLAN_ONLY', 'REJECTED'
                    ))
                OR (OLD.status = 'APPROVED_PLAN_ONLY' AND NEW.status = 'REVOKED')
            ) THEN
                RAISE EXCEPTION 'invalid media import request transition'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION unin_reject_media_import_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'media import plan, preflight, and events are immutable'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_media_import_request_guard "
        "BEFORE UPDATE OR DELETE ON media_import_requests FOR EACH ROW "
        "EXECUTE FUNCTION unin_guard_media_import_request()"
    )
    for table, trigger in (
        ("media_import_plans", "trg_media_import_plan_immutable"),
        ("media_import_preflights", "trg_media_import_preflight_immutable"),
        ("media_import_events", "trg_media_import_event_immutable"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION unin_reject_media_import_audit_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE media_import_requests, media_import_plans, "
        "media_import_preflights, media_import_events IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM media_import_requests)
                OR EXISTS (SELECT 1 FROM media_import_plans)
                OR EXISTS (SELECT 1 FROM media_import_preflights)
                OR EXISTS (SELECT 1 FROM media_import_events)
            THEN
                RAISE EXCEPTION
                    'cannot downgrade while append-preserved media import records exist'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_media_import_event_immutable ON media_import_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_media_import_preflight_immutable "
        "ON media_import_preflights"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_media_import_plan_immutable ON media_import_plans"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_media_import_request_guard ON media_import_requests"
    )
    op.execute("DROP FUNCTION IF EXISTS unin_reject_media_import_audit_mutation()")
    op.execute("DROP FUNCTION IF EXISTS unin_guard_media_import_request()")
    op.drop_table("media_import_events")
    op.drop_table("media_import_preflights")
    op.drop_table("media_import_plans")
    op.drop_table("media_import_requests")
