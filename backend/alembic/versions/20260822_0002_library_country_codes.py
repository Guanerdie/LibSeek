"""Store the NextFind country codes used by library filters.

Revision ID: 20260822_0002
Revises: 20260822_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_0002"
down_revision: str | None = "20260822_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_media",
        sa.Column(
            "country_codes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("library_media", "country_codes")
