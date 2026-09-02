"""Track per-media search cooldown so empty searches back off over time.

Revision ID: 20260902_0013
Revises: 20260901_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0013"
down_revision: str | None = "20260901_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.add_column(sa.Column("last_searched_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "search_miss_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("next_search_at", sa.DateTime(timezone=True)))
        batch.create_index(
            "ix_library_media_next_search_at", ["next_search_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.drop_index("ix_library_media_next_search_at")
        batch.drop_column("next_search_at")
        batch.drop_column("search_miss_count")
        batch.drop_column("last_searched_at")
