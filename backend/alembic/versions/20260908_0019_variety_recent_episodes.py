"""How many of the newest episodes of a variety show to chase.

Revision ID: 20260908_0019
Revises: 20260908_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0019"
down_revision: str | None = "20260908_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.add_column(
            sa.Column(
                "variety_recent_episodes", sa.Integer(), nullable=False, server_default="5"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_column("variety_recent_episodes")
