"""Let each deployment choose its own empty-search backoff.

The 1/3/7 day ladder was hard-coded.  Sites differ in how aggressively they
police repeated searches, so the tiers move into the policy.

Revision ID: 20260908_0016
Revises: 20260908_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0016"
down_revision: str | None = "20260908_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        # Defaults reproduce the previous hard-coded ladder exactly, so an
        # existing deployment keeps the pace it already had.
        batch.add_column(
            sa.Column("cooldown_tier_1_hours", sa.Integer(), nullable=False, server_default="24")
        )
        batch.add_column(
            sa.Column("cooldown_tier_2_hours", sa.Integer(), nullable=False, server_default="72")
        )
        batch.add_column(
            sa.Column("cooldown_tier_3_hours", sa.Integer(), nullable=False, server_default="168")
        )
        batch.create_check_constraint(
            "ck_automation_cooldown_tiers",
            "cooldown_tier_1_hours >= 1 AND cooldown_tier_2_hours >= 1 "
            "AND cooldown_tier_3_hours >= 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_constraint("ck_automation_cooldown_tiers", type_="check")
        batch.drop_column("cooldown_tier_3_hours")
        batch.drop_column("cooldown_tier_2_hours")
        batch.drop_column("cooldown_tier_1_hours")
