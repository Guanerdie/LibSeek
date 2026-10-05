"""Record who did each action and whether it was manual or automatic.

Revision ID: 20261005_0026
Revises: 20260923_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_0026"
down_revision: str | None = "20260923_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows keep NULL in all three: nobody recorded who did them, and
    # guessing would put invented facts into an audit trail.
    with op.batch_alter_table("activity_log") as batch:
        batch.add_column(sa.Column("actor", sa.String(length=120)))
        batch.add_column(sa.Column("trigger", sa.String(length=16)))
        batch.add_column(sa.Column("reason", sa.Text()))
        batch.create_index("ix_activity_log_trigger", ["trigger"])


def downgrade() -> None:
    with op.batch_alter_table("activity_log") as batch:
        batch.drop_index("ix_activity_log_trigger")
        batch.drop_column("reason")
        batch.drop_column("trigger")
        batch.drop_column("actor")
