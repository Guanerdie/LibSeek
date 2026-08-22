"""Add scheduled starts to download batches.

Revision ID: 20260816_0013
Revises: 20260815_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0013"
down_revision: str | None = "20260815_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_batches",
        sa.Column(
            "launch_mode",
            sa.String(30),
            nullable=False,
            server_default="ADD_PAUSED",
        ),
    )
    for table_name in ("execution_intents", "download_executions"):
        op.drop_constraint(f"ck_{table_name}_automation_binding", table_name, type_="check")
        op.create_check_constraint(
            f"ck_{table_name}_automation_binding",
            table_name,
            "((origin = 'MANUAL' AND automation_policy_revision_id IS NULL "
            "AND automation_decision_id IS NULL) OR "
            "(origin = 'AUTOMATION' AND automation_policy_revision_id IS NOT NULL "
            "AND automation_decision_id IS NOT NULL "
            "AND launch_mode IN ('ADD_PAUSED', 'SCHEDULED_START')))",
        )


def downgrade() -> None:
    for table_name in ("execution_intents", "download_executions"):
        op.drop_constraint(f"ck_{table_name}_automation_binding", table_name, type_="check")
        op.create_check_constraint(
            f"ck_{table_name}_automation_binding",
            table_name,
            "((origin = 'MANUAL' AND automation_policy_revision_id IS NULL "
            "AND automation_decision_id IS NULL) OR "
            "(origin = 'AUTOMATION' AND automation_policy_revision_id IS NOT NULL "
            "AND automation_decision_id IS NOT NULL AND launch_mode = 'ADD_PAUSED'))",
        )
    op.drop_column("download_batches", "launch_mode")
