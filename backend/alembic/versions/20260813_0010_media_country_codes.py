"""Add normalized country codes to media items.

Revision ID: 20260813_0010
Revises: 20260811_0009
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0010"
down_revision: str | None = "20260811_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column("country_codes", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_items", "country_codes")
