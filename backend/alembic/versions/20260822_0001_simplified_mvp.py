"""Create the simplified daily-use schema.

Revision ID: 20260822_0001
Revises: none
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_media",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_item_id", sa.String(180), nullable=False),
        sa.Column("media_type", sa.String(10), nullable=False),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("original_title", sa.String(500)),
        sa.Column("year", sa.Integer()),
        sa.Column("poster_path", sa.Text()),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attention_reason", sa.Text()),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_library_media_state", "library_media", ["state"])
    op.create_index(
        "uq_library_media_source_item",
        "library_media",
        ["source", "source_item_id"],
        unique=True,
    )
    op.create_index(
        "uq_library_media_tmdb_type",
        "library_media",
        ["tmdb_id", "media_type"],
        unique=True,
    )

    op.create_table(
        "episodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("library_media.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("air_date", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(20), nullable=False),
        sa.CheckConstraint("season_number >= 0", name="ck_media_episode_season_nonnegative"),
        sa.CheckConstraint("episode_number > 0", name="ck_media_episode_number_positive"),
    )
    op.create_index("ix_episodes_media_id", "episodes", ["media_id"])
    op.create_index("ix_episodes_state", "episodes", ["state"])
    op.create_index(
        "uq_media_episode_number",
        "episodes",
        ["media_id", "season_number", "episode_number"],
        unique=True,
    )

    op.create_table(
        "searches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("library_media.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_searches_media_id", "searches", ["media_id"])
    op.create_index("ix_searches_state", "searches", ["state"])
    op.create_index("ix_searches_created_at", "searches", ["created_at"])

    op.create_table(
        "release_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "search_id",
            sa.String(36),
            sa.ForeignKey("searches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_id", sa.String(50), nullable=False),
        sa.Column("torrent_id", sa.String(180), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("seeders", sa.Integer()),
        sa.Column("resolution", sa.String(40)),
        sa.Column("source", sa.String(80)),
        sa.Column("codec", sa.String(80)),
        sa.Column("season_coverage", sa.JSON(), nullable=False),
        sa.Column("episode_coverage", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("info_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_release_candidate_score_range"),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0", name="ck_release_candidate_size_positive"
        ),
        sa.CheckConstraint(
            "seeders IS NULL OR seeders >= 0", name="ck_release_candidate_seeders_nonnegative"
        ),
    )
    op.create_index(
        "ix_release_candidates_search_id", "release_candidates", ["search_id"]
    )
    op.create_index(
        "ix_release_candidates_info_hash", "release_candidates", ["info_hash"]
    )
    op.create_index(
        "uq_release_candidate_site_torrent",
        "release_candidates",
        ["search_id", "site_id", "torrent_id"],
        unique=True,
    )

    op.create_table(
        "downloads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("library_media.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("release_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("info_hash", sa.String(64)),
        sa.Column("name", sa.String(1000), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("download_speed", sa.BigInteger(), nullable=False),
        sa.Column("upload_speed", sa.BigInteger(), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name="ck_download_progress_range"),
        sa.CheckConstraint("download_speed >= 0", name="ck_download_speed_nonnegative"),
        sa.CheckConstraint("upload_speed >= 0", name="ck_upload_speed_nonnegative"),
        sa.CheckConstraint("ratio >= 0", name="ck_download_ratio_nonnegative"),
    )
    op.create_index("ix_downloads_media_id", "downloads", ["media_id"])
    op.create_index("ix_downloads_state", "downloads", ["state"])
    op.create_index("ix_downloads_created_at", "downloads", ["created_at"])
    op.create_index("uq_download_candidate", "downloads", ["candidate_id"], unique=True)
    op.create_index("uq_download_info_hash", "downloads", ["info_hash"], unique=True)

    op.create_table(
        "activity_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(36),
            sa.ForeignKey("library_media.id", ondelete="CASCADE"),
        ),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_activity_log_media_id", "activity_log", ["media_id"])
    op.create_index("ix_activity_log_event", "activity_log", ["event"])
    op.create_index("ix_activity_log_created_at", "activity_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("activity_log")
    op.drop_table("downloads")
    op.drop_table("release_candidates")
    op.drop_table("searches")
    op.drop_table("episodes")
    op.drop_table("library_media")
