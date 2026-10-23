"""Allow removed downloads to retain history when the same torrent is re-added.

Revision ID: 20261023_0029
Revises: 20261023_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261023_0029"
down_revision: str | None = "20261023_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (("uq_download_candidate", "candidate_id"), ("uq_download_info_hash", "info_hash"))


def upgrade() -> None:
    for name, column in _INDEXES:
        op.drop_index(name, table_name="downloads")
        op.create_index(
            name,
            "downloads",
            [column],
            unique=True,
            sqlite_where=sa.text("cleanup_state NOT IN ('DELETED', 'VANISHED')"),
            postgresql_where=sa.text("cleanup_state NOT IN ('DELETED', 'VANISHED')"),
        )


def downgrade() -> None:
    # Do not erase audit history just to fit the old global uniqueness rule.
    # Check both indexes before any DDL (SQLite DDL may not be transactional).
    connection = op.get_bind()
    for _, column in _INDEXES:
        duplicate = connection.execute(
            sa.text(
                f"SELECT 1 FROM downloads WHERE {column} IS NOT NULL "
                f"GROUP BY {column} HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                "Cannot restore global download uniqueness while re-download history exists; "
                "restore the pre-upgrade data backup before downgrading."
            )
    for name, column in _INDEXES:
        op.drop_index(name, table_name="downloads")
        op.create_index(name, "downloads", [column], unique=True)
