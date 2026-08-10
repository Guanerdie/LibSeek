"""Add a per-claim token for worker lease fencing.

Revision ID: 20260811_0004
Revises: 20260810_0003
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0004"
down_revision: str | None = "20260810_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lease_token", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "lease_token")
