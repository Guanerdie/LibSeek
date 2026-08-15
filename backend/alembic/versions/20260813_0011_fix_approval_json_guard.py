"""Fix JSON comparison in the approval immutability guard.

Revision ID: 20260813_0011
Revises: 20260813_0010
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260813_0011"
down_revision: str | None = "20260813_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION unin_guard_approval_request_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval_requests are append-preserved'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.media_item_id IS DISTINCT FROM OLD.media_item_id
                OR NEW.torrent_candidate_id IS DISTINCT FROM OLD.torrent_candidate_id
                OR NEW.candidate_snapshot::jsonb IS DISTINCT FROM
                    OLD.candidate_snapshot::jsonb
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


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION unin_guard_approval_request_immutable()
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
