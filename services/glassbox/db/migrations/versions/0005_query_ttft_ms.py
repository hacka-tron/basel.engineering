"""Add time to first token to the query log (DESIGN-002 §7.5, §9.3)."""

import sqlalchemy as sa
from alembic import op

revision = "0005_query_ttft_ms"
down_revision = "0004_query_mode_stopped"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A nullable column with no default appended at the end: MySQL 8 adds it with
    # ALGORITHM=INSTANT (metadata only, no table rebuild), and older rows read NULL.
    # The api of the previous release never names the column, so it keeps working.
    op.add_column("queries", sa.Column("ttft_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("queries", "ttft_ms")
