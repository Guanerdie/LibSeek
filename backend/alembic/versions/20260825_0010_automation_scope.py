"""Add region and manual media scopes to automation policies.

Revision ID: 20260825_0010
Revises: 20260823_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0010"
down_revision: str | None = "20260823_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automation_policy",
        sa.Column("scope_mode", sa.String(length=16), nullable=False, server_default="filters"),
    )
    op.add_column(
        "automation_policy",
        sa.Column("regions", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "automation_policy",
        sa.Column("selected_media_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("automation_policy", "selected_media_ids")
    op.drop_column("automation_policy", "regions")
    op.drop_column("automation_policy", "scope_mode")
