"""Add execution intents and the download execution control plane.

Revision ID: 20260811_0006
Revises: 20260811_0005
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0006"
down_revision: str | None = "20260811_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def execution_intent_status() -> sa.Enum:
    return sa.Enum(
        "ACTIVE",
        "CONSUMED",
        "EXPIRED",
        "CANCELLED",
        name="executionintentstatus",
        native_enum=False,
        length=20,
    )


def download_launch_mode() -> sa.Enum:
    return sa.Enum(
        "ADD_PAUSED",
        "START_IMMEDIATELY",
        name="downloadlaunchmode",
        native_enum=False,
        length=30,
    )


def download_execution_status() -> sa.Enum:
    return sa.Enum(
        "PENDING",
        "VALIDATING",
        "SUBMITTING",
        "SUBMITTED",
        "ALREADY_PRESENT",
        "OUTCOME_UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "RECONCILIATION_PENDING",
        "FAILED",
        "CANCELLED",
        name="downloadexecutionstatus",
        native_enum=False,
        length=40,
    )


def upgrade() -> None:
    op.create_table(
        "execution_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "approval_id",
            sa.String(36),
            sa.ForeignKey("approval_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("nonce_sha256", sa.String(64), nullable=False),
        sa.Column("approval_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("qb_target_fingerprint", sa.String(64), nullable=False),
        sa.Column("launch_mode", download_launch_mode(), nullable=False),
        sa.Column("status", execution_intent_status(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_by", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "nonce_sha256", name="uq_execution_intents_nonce_sha256"
        ),
    )
    op.create_index("ix_execution_intents_approval_id", "execution_intents", ["approval_id"])
    op.create_index("ix_execution_intents_status", "execution_intents", ["status"])
    op.create_index("ix_execution_intents_expires_at", "execution_intents", ["expires_at"])
    op.create_index("ix_execution_intents_created_at", "execution_intents", ["created_at"])
    op.create_index(
        "uq_execution_intent_active_approval",
        "execution_intents",
        ["approval_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "download_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "approval_id",
            sa.String(36),
            sa.ForeignKey("approval_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "intent_id",
            sa.String(36),
            sa.ForeignKey("execution_intents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key_sha256", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("approval_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("qb_target_fingerprint", sa.String(64), nullable=False),
        sa.Column("launch_mode", download_launch_mode(), nullable=False),
        sa.Column("status", download_execution_status(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(180)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("actual_info_hash", sa.String(64)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciliation_requested_by", sa.String(120)),
        sa.Column("reconciliation_requested_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("approval_id", name="uq_download_executions_approval_id"),
        sa.UniqueConstraint("intent_id", name="uq_download_executions_intent_id"),
        sa.UniqueConstraint(
            "idempotency_key_sha256",
            name="uq_download_executions_idempotency_key_sha256",
        ),
    )
    op.create_index("ix_download_executions_approval_id", "download_executions", ["approval_id"])
    op.create_index("ix_download_executions_intent_id", "download_executions", ["intent_id"])
    op.create_index("ix_download_executions_status", "download_executions", ["status"])
    op.create_index(
        "ix_download_executions_next_retry_at", "download_executions", ["next_retry_at"]
    )
    op.create_index("ix_download_executions_created_at", "download_executions", ["created_at"])

    op.create_table(
        "download_execution_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "download_execution_id",
            sa.String(36),
            sa.ForeignKey("download_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("from_status", sa.String(40)),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("sanitized_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_download_execution_events_download_execution_id",
        "download_execution_events",
        ["download_execution_id"],
    )
    op.create_index(
        "ix_download_execution_events_event_type",
        "download_execution_events",
        ["event_type"],
    )
    op.create_index(
        "ix_download_execution_events_created_at",
        "download_execution_events",
        ["created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION unin_guard_execution_intent_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'execution_intents are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.approval_id IS DISTINCT FROM OLD.approval_id
                OR NEW.nonce_sha256 IS DISTINCT FROM OLD.nonce_sha256
                OR NEW.approval_snapshot_hash IS DISTINCT FROM OLD.approval_snapshot_hash
                OR NEW.plan_hash IS DISTINCT FROM OLD.plan_hash
                OR NEW.qb_target_fingerprint IS DISTINCT FROM OLD.qb_target_fingerprint
                OR NEW.launch_mode IS DISTINCT FROM OLD.launch_mode
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'execution intent immutable fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_execution_intent_immutable
        BEFORE UPDATE OR DELETE ON execution_intents
        FOR EACH ROW EXECUTE FUNCTION unin_guard_execution_intent_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION unin_guard_download_execution_immutable()
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
    op.execute(
        """
        CREATE TRIGGER trg_download_execution_immutable
        BEFORE UPDATE OR DELETE ON download_executions
        FOR EACH ROW EXECUTE FUNCTION unin_guard_download_execution_immutable()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_download_execution_event_immutable
        BEFORE UPDATE OR DELETE ON download_execution_events
        FOR EACH ROW EXECUTE FUNCTION unin_reject_immutable_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_download_execution_event_immutable "
        "ON download_execution_events"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_download_execution_immutable ON download_executions")
    op.execute("DROP TRIGGER IF EXISTS trg_execution_intent_immutable ON execution_intents")
    op.execute("DROP FUNCTION IF EXISTS unin_guard_download_execution_immutable()")
    op.execute("DROP FUNCTION IF EXISTS unin_guard_execution_intent_immutable()")
    op.drop_table("download_execution_events")
    op.drop_table("download_executions")
    op.drop_table("execution_intents")
