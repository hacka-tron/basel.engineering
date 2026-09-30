"""Allow mode='stopped' in the query log (DESIGN-002 §6.2, §9.3)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0004_query_mode_stopped"
down_revision = "0003_query_turn_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Appending an ENUM member keeps existing values and storage size unchanged, so
    # MySQL 8 applies it in place without rewriting the table.
    op.alter_column(
        "queries",
        "mode",
        existing_type=mysql.ENUM("full", "retrieval_only"),
        type_=mysql.ENUM("full", "retrieval_only", "stopped"),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Lossy: stopped answers are recorded as full answers before the member is removed.
    op.execute(sa.text("UPDATE queries SET mode = 'full' WHERE mode = 'stopped'"))
    op.alter_column(
        "queries",
        "mode",
        existing_type=mysql.ENUM("full", "retrieval_only", "stopped"),
        type_=mysql.ENUM("full", "retrieval_only"),
        existing_nullable=False,
    )
