"""Record which seasons of a series have finished airing.

Without it automation cannot tell a season that is safe to replace with a
season pack from the one still going out week by week, so an airing series
could not be automated at all.

Revision ID: 20260908_0017
Revises: 20260908_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0017"
down_revision: str | None = "20260908_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_seasons",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "media_id",
            sa.String(length=36),
            sa.ForeignKey("library_media.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("episode_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aired_episode_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_air_date", sa.DateTime(timezone=True)),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("season_number >= 0", name="ck_media_season_nonnegative"),
        sa.CheckConstraint("episode_count >= 0", name="ck_media_season_episode_count"),
        sa.CheckConstraint("aired_episode_count >= 0", name="ck_media_season_aired_count"),
    )
    op.create_index("ix_media_seasons_media_id", "media_seasons", ["media_id"])
    op.create_index("ix_media_seasons_is_complete", "media_seasons", ["is_complete"])
    op.create_index(
        "uq_media_season_number", "media_seasons", ["media_id", "season_number"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_media_season_number", table_name="media_seasons")
    op.drop_index("ix_media_seasons_is_complete", table_name="media_seasons")
    op.drop_index("ix_media_seasons_media_id", table_name="media_seasons")
    op.drop_table("media_seasons")
