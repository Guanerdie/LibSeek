"""Add scheduling, retry and download budgets to automation.

Revision ID: 20260823_0005
Revises: 20260823_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0005"
down_revision: str | None = "20260823_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.add_column(
            sa.Column("auto_identify", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(
            sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60")
        )
        batch.add_column(
            sa.Column("retry_delay_minutes", sa.Integer(), nullable=False, server_default="30")
        )
        batch.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
        )
        batch.add_column(
            sa.Column("daily_download_limit", sa.Integer(), nullable=False, server_default="3")
        )
        batch.add_column(sa.Column("daily_download_bytes", sa.BigInteger()))
        batch.add_column(sa.Column("last_run_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint("ck_automation_interval", "interval_minutes >= 5")
        batch.create_check_constraint("ck_automation_retry_delay", "retry_delay_minutes >= 1")
        batch.create_check_constraint("ck_automation_attempts", "max_attempts >= 1")
        batch.create_check_constraint("ck_automation_daily_limit", "daily_download_limit >= 1")
        batch.create_check_constraint(
            "ck_automation_daily_bytes",
            "daily_download_bytes IS NULL OR daily_download_bytes > 0",
        )

    with op.batch_alter_table("automation_jobs") as batch:
        batch.add_column(sa.Column("download_id", sa.String(36)))
        batch.add_column(
            sa.Column("trigger", sa.String(20), nullable=False, server_default="manual")
        )
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key(
            "fk_automation_jobs_download_id",
            "downloads",
            ["download_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_automation_jobs_download_id", "automation_jobs", ["download_id"])
    op.create_index("ix_automation_jobs_next_attempt_at", "automation_jobs", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_automation_jobs_next_attempt_at", table_name="automation_jobs")
    op.drop_index("ix_automation_jobs_download_id", table_name="automation_jobs")
    with op.batch_alter_table("automation_jobs") as batch:
        batch.drop_constraint("fk_automation_jobs_download_id", type_="foreignkey")
        batch.drop_column("next_attempt_at")
        batch.drop_column("attempt_count")
        batch.drop_column("trigger")
        batch.drop_column("download_id")
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_constraint("ck_automation_daily_bytes", type_="check")
        batch.drop_constraint("ck_automation_daily_limit", type_="check")
        batch.drop_constraint("ck_automation_attempts", type_="check")
        batch.drop_constraint("ck_automation_retry_delay", type_="check")
        batch.drop_constraint("ck_automation_interval", type_="check")
        batch.drop_column("last_run_at")
        batch.drop_column("daily_download_bytes")
        batch.drop_column("daily_download_limit")
        batch.drop_column("max_attempts")
        batch.drop_column("retry_delay_minutes")
        batch.drop_column("interval_minutes")
        batch.drop_column("auto_identify")
