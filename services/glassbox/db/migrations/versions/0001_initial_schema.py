"""Create the Phase 1a ingestion tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("corpus", mysql.ENUM("about_me", "about_system"), nullable=False),
        sa.Column("source_path", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("content_hash", mysql.CHAR(length=64), nullable=False),
        sa.Column("commit_sha", mysql.CHAR(length=40), nullable=True),
        sa.Column(
            "updated_at",
            mysql.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("corpus", "source_path", name="uq_doc"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", mysql.BLOB(), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunk"),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("commit_sha", mysql.CHAR(length=40), nullable=True),
        sa.Column("started_at", mysql.TIMESTAMP(), nullable=False),
        sa.Column("finished_at", mysql.TIMESTAMP(), nullable=True),
        sa.Column("docs_changed", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("chunks_written", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("status", mysql.ENUM("running", "succeeded", "failed"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_table("chunks")
    op.drop_table("documents")
