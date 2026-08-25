"""Persist asynchronous automation run progress.

Revision ID: 20260825_0011
Revises: 20260825_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0011"
down_revision: str | None = "20260825_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("deferred_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_automation_runs_state", "automation_runs", ["state"], unique=False
    )
    op.create_index(
        "ix_automation_runs_created_at",
        "automation_runs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_automation_runs_created_at", table_name="automation_runs")
    op.drop_index("ix_automation_runs_state", table_name="automation_runs")
    op.drop_table("automation_runs")
