"""Per-deployment weights for release quality.

The old score mixed identity with quality, so every candidate for one media
item scored within a hair of the others and the number could not rank
versions.  Quality is scored on its own now, and what "good" means is a
preference, so the weights belong to the operator.

Revision ID: 20260909_0022
Revises: 20260908_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260909_0022"
down_revision: str | None = "20260908_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("weight_resolution", "44"),
    ("weight_size", "24"),
    ("weight_source", "12"),
    ("weight_seeders", "13"),
    ("weight_promotion", "3"),
    ("seeder_floor", "3"),
)


def upgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        for name, default in _COLUMNS:
            batch.add_column(
                sa.Column(name, sa.Integer(), nullable=False, server_default=default)
            )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        for name, _ in reversed(_COLUMNS):
            batch.drop_column(name)
