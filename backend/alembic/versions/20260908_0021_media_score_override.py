"""Let one media item use its own minimum score.

The policy threshold has to serve the whole library at once: raising it to
protect quality on one show starves everything else, and lowering it for a
show you really want loosens the bar everywhere.

Revision ID: 20260908_0021
Revises: 20260908_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0021"
down_revision: str | None = "20260908_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.add_column(sa.Column("minimum_score_override", sa.Float()))


def downgrade() -> None:
    with op.batch_alter_table("library_media") as batch:
        batch.drop_column("minimum_score_override")
