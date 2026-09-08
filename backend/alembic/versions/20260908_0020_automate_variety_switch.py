"""Whether variety shows take part in automation at all.

They publish one episode at a time, number their seasons differently from
TMDB, and their newest episodes are frequently unseeded -- so they burn search
quota for little return until an operator deliberately takes them on.

Revision ID: 20260908_0020
Revises: 20260908_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0020"
down_revision: str | None = "20260908_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.add_column(
            sa.Column(
                "automate_variety", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_column("automate_variety")
