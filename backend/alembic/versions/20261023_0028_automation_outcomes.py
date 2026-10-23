"""Keep pre-search download skips separate from executed automation jobs.

Revision ID: 20261023_0028
Revises: 20261007_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261023_0028"
down_revision: str | None = "20261007_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("skipped_existing_count", "awaiting_library_count"):
        op.add_column(
            "automation_runs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_runs") as batch:
        batch.drop_column("awaiting_library_count")
        batch.drop_column("skipped_existing_count")
