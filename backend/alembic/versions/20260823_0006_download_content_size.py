"""Persist validated torrent metadata and submission time on downloads.

Revision ID: 20260823_0006
Revises: 20260823_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0006"
down_revision: str | None = "20260823_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.add_column(sa.Column("content_size_bytes", sa.BigInteger()))
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_download_content_size_positive",
            "content_size_bytes IS NULL OR content_size_bytes > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.drop_constraint("ck_download_content_size_positive", type_="check")
        batch.drop_column("submitted_at")
        batch.drop_column("content_size_bytes")
