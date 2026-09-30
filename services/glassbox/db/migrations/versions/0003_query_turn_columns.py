"""Add conversational follow-up columns to the query log (DESIGN-002 §9.3)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0003_query_turn_columns"
down_revision = "0002_add_queries_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "queries",
        sa.Column(
            "turn_index",
            mysql.TINYINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("queries", sa.Column("rewritten_query", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("queries", "rewritten_query")
    op.drop_column("queries", "turn_index")
