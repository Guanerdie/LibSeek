"""Add immutable approval, approval audit, and download plan tables.

Revision ID: 20260810_0003
Revises: 20260810_0002
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0003"
down_revision: str | None = "20260810_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def approval_status() -> sa.Enum:
    return sa.Enum(
        "PENDING",
        "APPROVED",
        "REJECTED",
        "EXPIRED",
        "REVOKED",
        "CONSUMED",
        name="approvalstatus",
        native_enum=False,
        length=20,
    )


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_item_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "torrent_candidate_id",
            sa.String(36),
            sa.ForeignKey("torrent_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", approval_status(), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("preflight_result", sa.JSON()),
        sa.Column("preflight_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_requests_media_item_id", "approval_requests", ["media_item_id"])
    op.create_index(
        "ix_approval_requests_torrent_candidate_id",
        "approval_requests",
        ["torrent_candidate_id"],
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_index("ix_approval_requests_expires_at", "approval_requests", ["expires_at"])
    op.create_index("ix_approval_requests_created_at", "approval_requests", ["created_at"])
    op.create_index(
        "uq_approval_active_candidate",
        "approval_requests",
        ["torrent_candidate_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED')"),
    )

    op.create_table(
        "approval_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "approval_request_id",
            sa.String(36),
            sa.ForeignKey("approval_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("sanitized_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_approval_events_approval_request_id", "approval_events", ["approval_request_id"]
    )
    op.create_index("ix_approval_events_event_type", "approval_events", ["event_type"])
    op.create_index("ix_approval_events_created_at", "approval_events", ["created_at"])

    op.create_table(
        "download_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "approval_id",
            sa.String(36),
            sa.ForeignKey("approval_requests.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("approval_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("preflight_policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("site_id", sa.String(50), nullable=False),
        sa.Column("torrent_ref", sa.String(180), nullable=False),
        sa.Column("expected_info_hash", sa.String(64)),
        sa.Column("release_title", sa.String(1000), nullable=False),
        sa.Column("save_path_ref", sa.String(180), nullable=False),
        sa.Column("category", sa.String(300), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("estimated_size_bytes", sa.BigInteger()),
        sa.Column("media_destination_plan", sa.JSON(), nullable=False),
        sa.Column("preflight_result", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_download_plans_approval_id", "download_plans", ["approval_id"])
    op.create_index("ix_download_plans_created_at", "download_plans", ["created_at"])

    op.execute(
        """
        CREATE FUNCTION unin_guard_approval_request_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval_requests are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.media_item_id IS DISTINCT FROM OLD.media_item_id
                OR NEW.torrent_candidate_id IS DISTINCT FROM OLD.torrent_candidate_id
                OR NEW.candidate_snapshot IS DISTINCT FROM OLD.candidate_snapshot
                OR NEW.snapshot_hash IS DISTINCT FROM OLD.snapshot_hash
                OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'approval immutable fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_request_immutable
        BEFORE UPDATE OR DELETE ON approval_requests
        FOR EACH ROW EXECUTE FUNCTION unin_guard_approval_request_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION unin_reject_immutable_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'immutable approval audit records cannot change'
                USING ERRCODE = '55000';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_event_immutable
        BEFORE UPDATE OR DELETE ON approval_events
        FOR EACH ROW EXECUTE FUNCTION unin_reject_immutable_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_download_plan_immutable
        BEFORE UPDATE OR DELETE ON download_plans
        FOR EACH ROW EXECUTE FUNCTION unin_reject_immutable_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_download_plan_immutable ON download_plans")
    op.execute("DROP TRIGGER IF EXISTS trg_approval_event_immutable ON approval_events")
    op.execute("DROP TRIGGER IF EXISTS trg_approval_request_immutable ON approval_requests")
    op.execute("DROP FUNCTION IF EXISTS unin_reject_immutable_audit_mutation()")
    op.execute("DROP FUNCTION IF EXISTS unin_guard_approval_request_immutable()")
    op.drop_table("download_plans")
    op.drop_table("approval_events")
    op.drop_table("approval_requests")
