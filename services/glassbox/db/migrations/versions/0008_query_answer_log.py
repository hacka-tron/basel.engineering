"""Answer log columns on queries (RAG plan phase 10, DESIGN-005 §5.6)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008_query_answer_log"
down_revision = "0007_portfolio_corpus"
branch_labels = None
depends_on = None

OLD_CACHE_STATUS = mysql.ENUM("answer_hit", "miss")
NEW_CACHE_STATUS = mysql.ENUM("answer_hit", "miss", "coalesced")
COLUMNS = ("answer", "abstained", "answer_route", "llm_model_id", "prompt_version")


def upgrade() -> None:
    # Nullable columns with no default appended at the end, and an ENUM member
    # appended at the end: MySQL 8 applies both as metadata-only changes
    # (ALGORITHM=INSTANT), and older rows read NULL. The api of the previous release
    # never names the new columns or writes 'coalesced', so it keeps working while
    # the rollout finishes.
    op.add_column("queries", sa.Column("answer", sa.Text(), nullable=True))
    op.add_column("queries", sa.Column("abstained", sa.Boolean(), nullable=True))
    op.add_column("queries", sa.Column("answer_route", sa.String(16), nullable=True))
    op.add_column("queries", sa.Column("llm_model_id", sa.String(128), nullable=True))
    op.add_column("queries", sa.Column("prompt_version", sa.String(16), nullable=True))
    op.alter_column(
        "queries",
        "cache_status",
        existing_type=OLD_CACHE_STATUS,
        type_=NEW_CACHE_STATUS,
        existing_nullable=False,
    )


def downgrade() -> None:
    # Keeps every row: a coalesced request replayed a cached answer, so it is
    # folded back into 'answer_hit' (what the previous release logged for it).
    op.execute(
        sa.text("UPDATE queries SET cache_status = 'answer_hit' WHERE cache_status = 'coalesced'")
    )
    op.alter_column(
        "queries",
        "cache_status",
        existing_type=NEW_CACHE_STATUS,
        type_=OLD_CACHE_STATUS,
        existing_nullable=False,
    )
    for column in reversed(COLUMNS):
        op.drop_column("queries", column)
