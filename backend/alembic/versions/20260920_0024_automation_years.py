"""Add release years to the automation scope.

An empty list keeps the existing behaviour and allows every year.

Revision ID: 20260920_0024
Revises: 20260910_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260920_0024"
down_revision: str | None = "20260910_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.add_column(
            sa.Column("years", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_column("years")
