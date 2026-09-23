"""Reclaim disk space by deleting downloads whose files are already in the library.

Revision ID: 20260923_0025
Revises: 20260920_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260923_0025"
down_revision: str | None = "20260920_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.add_column(sa.Column("library_confirmed_at", sa.DateTime(timezone=True)))
        batch.create_index(
            "ix_library_media_library_confirmed_at", ["library_confirmed_at"]
        )

    with op.batch_alter_table("downloads") as batch:
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_downloads_completed_at", ["completed_at"])
        batch.add_column(
            sa.Column(
                "seeding_seconds", sa.BigInteger(), nullable=False, server_default="0"
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_state",
                sa.Enum(
                    "NONE",
                    "MARKED",
                    "DELETED",
                    "VANISHED",
                    "HELD",
                    name="downloadcleanupstate",
                    native_enum=False,
                    length=16,
                ),
                nullable=False,
                server_default="NONE",
            )
        )
        batch.create_index("ix_downloads_cleanup_state", ["cleanup_state"])
        batch.add_column(sa.Column("cleanup_marked_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cleanup_deleted_at", sa.DateTime(timezone=True)))

    # Everything downloaded before this revision belongs to the operator, who
    # sorts that backlog by hand.  Letting the cleanup loose on it could delete
    # files in the middle of being filed, so every existing download starts out
    # HELD -- the same state a manually rescued torrent ends up in -- and only
    # downloads submitted after the upgrade are ever cleaned up automatically.
    op.execute(sa.text("UPDATE downloads SET cleanup_state = 'HELD'"))

    with op.batch_alter_table("automation_policy") as batch:
        batch.add_column(
            sa.Column(
                "cleanup_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_dry_run", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_after_days", sa.Integer(), nullable=False, server_default="10"
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_min_seeding_days",
                sa.Integer(),
                nullable=False,
                server_default="10",
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_grace_days", sa.Integer(), nullable=False, server_default="2"
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_require_library_confirmed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "cleanup_daily_limit", sa.Integer(), nullable=False, server_default="20"
            )
        )
        batch.create_check_constraint(
            "ck_automation_cleanup_after_days", "cleanup_after_days >= 1"
        )
        batch.create_check_constraint(
            "ck_automation_cleanup_min_seeding_days", "cleanup_min_seeding_days >= 1"
        )
        batch.create_check_constraint(
            "ck_automation_cleanup_grace_days", "cleanup_grace_days >= 0"
        )
        batch.create_check_constraint(
            "ck_automation_cleanup_daily_limit", "cleanup_daily_limit >= 1"
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_policy") as batch:
        batch.drop_constraint("ck_automation_cleanup_daily_limit", type_="check")
        batch.drop_constraint("ck_automation_cleanup_grace_days", type_="check")
        batch.drop_constraint("ck_automation_cleanup_min_seeding_days", type_="check")
        batch.drop_constraint("ck_automation_cleanup_after_days", type_="check")
        batch.drop_column("cleanup_daily_limit")
        batch.drop_column("cleanup_require_library_confirmed")
        batch.drop_column("cleanup_grace_days")
        batch.drop_column("cleanup_min_seeding_days")
        batch.drop_column("cleanup_after_days")
        batch.drop_column("cleanup_dry_run")
        batch.drop_column("cleanup_enabled")

    with op.batch_alter_table("downloads") as batch:
        batch.drop_column("cleanup_deleted_at")
        batch.drop_column("cleanup_marked_at")
        batch.drop_index("ix_downloads_cleanup_state")
        batch.drop_column("cleanup_state")
        batch.drop_column("seeding_seconds")
        batch.drop_index("ix_downloads_completed_at")
        batch.drop_column("completed_at")

    with op.batch_alter_table("library_media") as batch:
        batch.drop_index("ix_library_media_library_confirmed_at")
        batch.drop_column("library_confirmed_at")
