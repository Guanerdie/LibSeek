"""Persist PT candidate download accounting factors.

Revision ID: 20260823_0008
Revises: 20260823_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0008"
down_revision: str | None = "20260823_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "release_candidates",
        sa.Column("download_factor", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("release_candidates", "download_factor")
