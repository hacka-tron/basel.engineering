"""Add 'portfolio' to both corpus ENUM columns (portfolio spec §6.2)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0007_portfolio_corpus"
down_revision = "0006_ingestion_run_notes"
branch_labels = None
depends_on = None

OLD = mysql.ENUM("about_me", "about_system")
NEW = mysql.ENUM("about_me", "about_system", "portfolio")
TABLES = ("documents", "queries")


def upgrade() -> None:
    # Appending a member at the end keeps every existing value's index and the 1-byte
    # storage size, so MySQL 8.0 changes only the table metadata (ALGORITHM=INSTANT,
    # concurrent DML allowed), also on documents, where corpus leads the uq_doc key.
    # Both tables are small (hundreds of documents, at most thousands of query rows),
    # so even a fallback table copy would take well under a second. Pods still on the
    # previous image keep working during the rollout: they never write 'portfolio'.
    for table in TABLES:
        op.alter_column(table, "corpus", existing_type=OLD, type_=NEW, existing_nullable=False)


def downgrade() -> None:
    # Lossy: the old ENUM can't hold portfolio rows, so they are deleted first (chunks
    # follow their documents through ON DELETE CASCADE). Run
    # `python -m services.glassbox.ingest.run --clear --corpus portfolio --yes` before
    # downgrading so the Redis chunk keys go too; otherwise the previous image's
    # reconcile removes them as keys of an unknown corpus.
    op.execute(sa.text("DELETE FROM queries WHERE corpus = 'portfolio'"))
    op.execute(sa.text("DELETE FROM documents WHERE corpus = 'portfolio'"))
    for table in TABLES:
        op.alter_column(table, "corpus", existing_type=NEW, type_=OLD, existing_nullable=False)
