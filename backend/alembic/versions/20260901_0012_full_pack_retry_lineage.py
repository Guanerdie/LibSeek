"""Store TV pack metadata and immutable automation retry lineage.

Revision ID: 20260901_0012
Revises: 20260825_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_0012"
down_revision: str | None = "20260825_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release_candidates") as batch:
        batch.add_column(sa.Column("collection_type", sa.String(length=32)))
        batch.add_column(sa.Column("file_count", sa.Integer()))

    with op.batch_alter_table("automation_jobs") as batch:
        batch.add_column(sa.Column("retry_of_job_id", sa.String(length=36)))
        batch.add_column(sa.Column("superseded_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key(
            "fk_automation_jobs_retry_of_job_id",
            "automation_jobs",
            ["retry_of_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_automation_jobs_retry_of_job_id", ["retry_of_job_id"], unique=False
        )
        batch.create_index(
            "ix_automation_jobs_superseded_at", ["superseded_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_jobs") as batch:
        batch.drop_index("ix_automation_jobs_superseded_at")
        batch.drop_index("ix_automation_jobs_retry_of_job_id")
        batch.drop_constraint(
            "fk_automation_jobs_retry_of_job_id", type_="foreignkey"
        )
        batch.drop_column("superseded_at")
        batch.drop_column("retry_of_job_id")

    with op.batch_alter_table("release_candidates") as batch:
        batch.drop_column("file_count")
        batch.drop_column("collection_type")
