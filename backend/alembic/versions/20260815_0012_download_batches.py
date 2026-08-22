"""Add download batch orchestration.

Revision ID: 20260815_0012
Revises: 20260813_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0012"
down_revision: str | None = "20260813_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("site_id", sa.String(50), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("max_total_size_bytes", sa.BigInteger()),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_download_batches_status", "download_batches", ["status"])
    op.create_index("ix_download_batches_created_at", "download_batches", ["created_at"])
    op.create_table(
        "download_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("download_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_item_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "media_item_id", name="uq_download_batch_media"),
    )
    op.create_index("ix_download_batch_items_batch_id", "download_batch_items", ["batch_id"])
    op.create_index(
        "ix_download_batch_items_media_item_id", "download_batch_items", ["media_item_id"]
    )
    op.create_index("ix_download_batch_items_status", "download_batch_items", ["status"])


def downgrade() -> None:
    op.drop_table("download_batch_items")
    op.drop_table("download_batches")
