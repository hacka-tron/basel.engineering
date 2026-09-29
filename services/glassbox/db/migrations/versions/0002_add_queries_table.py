"""Add the independent query log table."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002_add_queries_table"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "queries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", mysql.CHAR(length=26), nullable=False),
        sa.Column("corpus", mysql.ENUM("about_me", "about_system"), nullable=False),
        sa.Column("question", sa.String(length=1000), nullable=False),
        sa.Column("cache_status", mysql.ENUM("answer_hit", "miss"), nullable=False),
        sa.Column("mode", mysql.ENUM("full", "retrieval_only"), nullable=False),
        sa.Column("chunk_ids", mysql.JSON(), nullable=True),
        sa.Column("stage_timings_ms", mysql.JSON(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            mysql.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_created", "queries", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_created", table_name="queries")
    op.drop_table("queries")
