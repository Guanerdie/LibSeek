"""Persist IMDb identities used by automated candidate selection.

Revision ID: 20260823_0009
Revises: 20260823_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0009"
down_revision: str | None = "20260823_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_media",
        sa.Column("imdb_id", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("library_media", "imdb_id")
