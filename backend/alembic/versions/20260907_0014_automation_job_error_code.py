"""Record the error code of a failed automation job.

Without it the runner cannot tell a transient failure (site 5xx, rate limit,
qBittorrent briefly unreachable) from one that needs a human decision, so every
FAILED job kept its media out of the queue forever.

Revision ID: 20260907_0014
Revises: 20260902_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260907_0014"
down_revision: str | None = "20260902_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_jobs") as batch:
        batch.add_column(sa.Column("error_code", sa.String(length=64)))
        batch.create_index("ix_automation_jobs_error_code", ["error_code"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("automation_jobs") as batch:
        batch.drop_index("ix_automation_jobs_error_code")
        batch.drop_column("error_code")
