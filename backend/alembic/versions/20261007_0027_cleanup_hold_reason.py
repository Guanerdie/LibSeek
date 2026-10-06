"""Tell held downloads apart, and allow the backlog to be released automatically.

Revision ID: 20261007_0027
Revises: 20261005_0026
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261007_0027"
down_revision: str | None = "20261005_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.add_column(sa.Column("cleanup_hold_reason", sa.String(length=16)))
    with op.batch_alter_table("automation_policy") as batch:
        batch.add_column(
            sa.Column(
                "cleanup_release_backlog",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    connection = op.get_bind()
    # Until now HELD meant three things at once.  A download the operator held
    # on purpose left a DOWNLOAD_CLEANUP_HELD row behind; everything else that
    # is held got that way in the 0025 migration.  When in doubt a hold counts
    # as deliberate, because only the backlog may be released automatically.
    deliberate: set[str] = set()
    for (details,) in connection.execute(
        sa.text("SELECT details FROM activity_log WHERE event = 'DOWNLOAD_CLEANUP_HELD'")
    ):
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except ValueError:
                continue
        if isinstance(details, dict) and isinstance(details.get("download_id"), str):
            deliberate.add(details["download_id"])

    connection.execute(
        sa.text(
            "UPDATE downloads SET cleanup_hold_reason = 'BACKLOG' WHERE cleanup_state = 'HELD'"
        )
    )
    for download_id in sorted(deliberate):
        connection.execute(
            sa.text(
                "UPDATE downloads SET cleanup_hold_reason = 'MANUAL'"
                " WHERE id = :id AND cleanup_state = 'HELD'"
            ),
            {"id": download_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_column("cleanup_release_backlog")
    with op.batch_alter_table("downloads") as batch:
        batch.drop_column("cleanup_hold_reason")
