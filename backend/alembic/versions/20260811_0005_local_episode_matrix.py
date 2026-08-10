"""Persist the exact local TV episode matrix.

Revision ID: 20260811_0005
Revises: 20260811_0004
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0005"
down_revision: str | None = "20260811_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_items", sa.Column("local_episode_matrix", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_items", "local_episode_matrix")
