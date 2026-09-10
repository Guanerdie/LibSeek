"""Store each media item's NextFind regions for SQL filtering.

Filtering the library by region loaded every row into Python to derive the
regions from country codes and language, on every list request.  The derived
value now lives on the row as ``|欧美|日本|`` and the filter is a LIKE.

Revision ID: 20260910_0023
Revises: 20260909_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.simple.regions import region_tags

revision: str = "20260910_0023"
down_revision: str | None = "20260909_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.add_column(
            sa.Column("region_tags", sa.String(64), nullable=False, server_default="")
        )
    media = sa.table(
        "library_media",
        sa.column("id", sa.String),
        sa.column("country_codes", sa.JSON),
        sa.column("original_language", sa.String),
        sa.column("region_tags", sa.String),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(media.c.id, media.c.country_codes, media.c.original_language)
    ).all()
    for row in rows:
        tags = region_tags(row.country_codes, row.original_language)
        if tags:
            connection.execute(
                media.update().where(media.c.id == row.id).values(region_tags=tags)
            )


def downgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.drop_column("region_tags")
