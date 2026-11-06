"""Add user MFA columns.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-22 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add mfa_enabled, mfa_secret, and mfa_recovery_codes columns to users table."""
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("mfa_secret", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("mfa_recovery_codes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop MFA columns from users table."""
    op.drop_column("users", "mfa_recovery_codes")
    op.drop_column("users", "mfa_secret")
    op.drop_column("users", "mfa_enabled")
