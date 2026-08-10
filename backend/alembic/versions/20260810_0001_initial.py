"""Create phase-1 discovery, media, job and audit tables.

Revision ID: 20260810_0001
Revises: none
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    job_status = sa.Enum(
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "RETRY_WAIT",
        "CANCELLED",
        name="jobstatus",
        native_enum=False,
        length=20,
    )
    media_type = sa.Enum("movie", "tv", name="mediatype", native_enum=False, length=10)
    identity_confidence = sa.Enum(
        "HIGH",
        "NEEDS_CONFIRMATION",
        name="identityconfidence",
        native_enum=False,
        length=30,
    )
    metadata_status = sa.Enum(
        "RESOLVED",
        "NEEDS_CONFIRMATION",
        "UNRESOLVED",
        name="metadatastatus",
        native_enum=False,
        length=30,
    )

    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discovery_runs_source", "discovery_runs", ["source"])

    op.create_table(
        "media_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_item_id", sa.String(180), nullable=False),
        sa.Column("media_type", media_type, nullable=False),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("original_title", sa.String(500)),
        sa.Column("year", sa.Integer()),
        sa.Column("poster_path", sa.Text()),
        sa.Column("raw_type", sa.String(80)),
        sa.Column("local_episodes", sa.Integer()),
        sa.Column("total_episodes", sa.Integer()),
        sa.Column("aired_episodes", sa.Integer()),
        sa.Column("missing_episodes", sa.JSON()),
        sa.Column("discovery_status", sa.String(40), nullable=False),
        sa.Column("identity_confidence", identity_confidence, nullable=False),
        sa.Column("metadata_status", metadata_status, nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_media_source_type_tmdb",
        "media_items",
        ["source", "media_type", "tmdb_id"],
        unique=True,
        postgresql_where=sa.text("tmdb_id IS NOT NULL"),
    )
    op.create_index(
        "uq_media_source_type_fallback",
        "media_items",
        ["source", "media_type", "source_item_id"],
        unique=True,
        postgresql_where=sa.text("tmdb_id IS NULL"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_type", sa.String(80), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("discovery_runs.id", ondelete="CASCADE"),
            unique=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(180)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_next_retry_at", "jobs", ["next_retry_at"])
    op.create_index(
        "uq_jobs_active_type",
        "jobs",
        ["job_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("sanitized_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_entity_type", "audit_events", ["entity_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(180), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_table("audit_events")
    op.drop_table("jobs")
    op.drop_table("media_items")
    op.drop_table("discovery_runs")

