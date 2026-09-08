"""Reuse a recent identical search instead of hitting the site again.

A manual search followed minutes later by an automation run used to send the
same query to the PT site twice, burning quota and occasionally returning a
different candidate set to each caller.

Revision ID: 20260908_0015
Revises: 20260907_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0015"
down_revision: str | None = "20260907_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("searches") as batch:
        batch.add_column(sa.Column("cache_key", sa.String(length=64)))
        batch.add_column(sa.Column("cache_expires_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_searches_cache_key", ["cache_key"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("searches") as batch:
        batch.drop_index("ix_searches_cache_key")
        batch.drop_column("cache_expires_at")
        batch.drop_column("cache_key")
