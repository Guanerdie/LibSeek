"""Add read-only metadata identity and PT search workflow tables.

Revision ID: 20260810_0002
Revises: 20260810_0001
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0002"
down_revision: str | None = "20260810_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def workflow_status() -> sa.Enum:
    return sa.Enum(
        "DISCOVERED",
        "METADATA_PENDING",
        "IDENTITY_REVIEW",
        "IDENTITY_CONFIRMED",
        "PT_SEARCH_PENDING",
        "PT_SEARCHING",
        "TORRENT_REVIEW",
        "NO_CANDIDATE",
        "SEARCH_FAILED",
        name="workflowstatus",
        native_enum=False,
        length=30,
    )


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column(
            "workflow_status",
            workflow_status(),
            nullable=False,
            server_default="DISCOVERED",
        ),
    )

    op.create_table(
        "metadata_matches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("match_reasons", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_metadata_matches_media_id", "metadata_matches", ["media_id"])
    op.create_index("ix_metadata_matches_tmdb_id", "metadata_matches", ["tmdb_id"])
    op.create_index("ix_metadata_matches_created_at", "metadata_matches", ["created_at"])

    op.create_table(
        "identity_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "metadata_match_id",
            sa.String(36),
            sa.ForeignKey("metadata_matches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("confirmed_by", sa.String(120), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_identity_reviews_media_id", "identity_reviews", ["media_id"])
    op.create_index("ix_identity_reviews_created_at", "identity_reviews", ["created_at"])
    op.create_index(
        "uq_identity_reviews_confirmed_media",
        "identity_reviews",
        ["media_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CONFIRMED'"),
    )

    op.create_table(
        "torrent_search_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_id", sa.String(50), nullable=False),
        sa.Column("status", workflow_status(), nullable=False),
        sa.Column("strategy_log", sa.JSON(), nullable=False),
        sa.Column("sanitized_request", sa.JSON(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_torrent_search_runs_media_id", "torrent_search_runs", ["media_id"])
    op.create_index("ix_torrent_search_runs_status", "torrent_search_runs", ["status"])
    op.create_index("ix_torrent_search_runs_created_at", "torrent_search_runs", ["created_at"])

    op.create_table(
        "torrent_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "search_run_id",
            sa.String(36),
            sa.ForeignKey("torrent_search_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_id", sa.String(50), nullable=False),
        sa.Column("torrent_id", sa.String(180), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("match_reasons", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_torrent_candidates_search_run_id", "torrent_candidates", ["search_run_id"]
    )
    op.create_index("ix_torrent_candidates_created_at", "torrent_candidates", ["created_at"])
    op.create_index(
        "uq_torrent_candidate_search_site_torrent",
        "torrent_candidates",
        ["search_run_id", "site_id", "torrent_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("torrent_candidates")
    op.drop_table("torrent_search_runs")
    op.drop_table("identity_reviews")
    op.drop_table("metadata_matches")
    op.drop_column("media_items", "workflow_status")

