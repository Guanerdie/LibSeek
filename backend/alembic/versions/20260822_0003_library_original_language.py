"""Store the NextFind original language used by its region rules.

Revision ID: 20260822_0003
Revises: 20260822_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_0003"
down_revision: str | None = "20260822_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_media",
        sa.Column("original_language", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("library_media", "original_language")
