"""Add automation policy and dry-run jobs.

Revision ID: 20260823_0004
Revises: 20260822_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0004"
down_revision: str | None = "20260822_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_policy",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("site_ids", sa.JSON(), nullable=False),
        sa.Column("media_types", sa.JSON(), nullable=False),
        sa.Column("minimum_score", sa.Float(), nullable=False),
        sa.Column("minimum_seeders", sa.Integer(), nullable=False),
        sa.Column("max_size_bytes", sa.BigInteger()),
        sa.Column("allow_warnings", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("minimum_score >= 0 AND minimum_score <= 1", name="ck_automation_score"),
        sa.CheckConstraint("minimum_seeders >= 0", name="ck_automation_seeders"),
        sa.CheckConstraint(
            "max_size_bytes IS NULL OR max_size_bytes > 0", name="ck_automation_max_size"
        ),
    )
    op.create_table(
        "automation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("library_media.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("search_id", sa.String(36), sa.ForeignKey("searches.id", ondelete="SET NULL")),
        sa.Column(
            "selected_candidate_id",
            sa.String(36),
            sa.ForeignKey("release_candidates.id", ondelete="SET NULL"),
        ),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_automation_jobs_run_id", "automation_jobs", ["run_id"])
    op.create_index("ix_automation_jobs_media_id", "automation_jobs", ["media_id"])
    op.create_index("ix_automation_jobs_state", "automation_jobs", ["state"])
    op.create_index("ix_automation_jobs_search_id", "automation_jobs", ["search_id"])
    op.create_index("ix_automation_jobs_created_at", "automation_jobs", ["created_at"])
    op.create_index(
        "uq_automation_job_run_media", "automation_jobs", ["run_id", "media_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("automation_jobs")
    op.drop_table("automation_policy")
