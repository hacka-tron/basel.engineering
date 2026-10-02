"""Add a notes column to ingestion_runs for the stale sweep's outcome (DESIGN-003)."""

import sqlalchemy as sa
from alembic import op

revision = "0006_ingestion_run_notes"
down_revision = "0005_query_ttft_ms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A nullable JSON column with no default appended at the end: MySQL 8 adds it with
    # ALGORITHM=INSTANT (metadata only, no table rebuild), and older rows read NULL.
    # Only the ingest Job reads or writes ingestion_runs, and it runs after migrate.
    op.add_column("ingestion_runs", sa.Column("notes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_runs", "notes")
