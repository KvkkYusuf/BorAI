"""Add query pattern composite indexes.

Revision ID: a1b2c3d4e5f6
Revises: 02dceff5c3b5
Create Date: 2026-10-22 20:17:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "02dceff5c3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create composite indexes for high-frequency query patterns."""
    op.create_index(
        "ix_market_bars_inst_adj_ts",
        "market_bars",
        ["instrument_id", "is_adjusted", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_alert_rules_inst_active",
        "alert_rules",
        ["instrument_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    """Drop composite indexes."""
    op.drop_index("ix_alert_rules_inst_active", table_name="alert_rules")
    op.drop_index("ix_market_bars_inst_adj_ts", table_name="market_bars")
