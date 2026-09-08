"""Record TMDB genres and airing status on a library item.

Needed to tell a weekly variety show from a drama series.  Two automation
rules that are correct for drama are wrong for variety -- "the release year
must equal the show's year" (TMDB stores the year the show started, releases
are named for the current year) and "only accept a complete season pack" (a
show that never ends has none) -- and without the genre there is no way to
know which rules apply.

Revision ID: 20260908_0018
Revises: 20260908_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0018"
down_revision: str | None = "20260908_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.add_column(
            sa.Column("genre_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("tmdb_status", sa.String(length=40)))


def downgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.drop_column("tmdb_status")
        batch.drop_column("genre_ids")
