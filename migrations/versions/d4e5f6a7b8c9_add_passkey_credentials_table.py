"""Add passkey_credentials table for WebAuthn authentication.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-23 16:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create passkey_credentials table."""
    op.create_table(
        "passkey_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(length=255), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "device_name",
            sa.String(length=128),
            server_default="Varsayılan Güvenlik Anahtarı",
            nullable=False,
        ),
        sa.Column("aaguid", sa.String(length=64), nullable=True),
        sa.Column("transports", sa.String(length=128), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id", name="uq_passkey_credentials_credential_id"),
    )
    op.create_index(
        "ix_passkey_credentials_credential_id",
        "passkey_credentials",
        ["credential_id"],
        unique=True,
    )
    op.create_index(
        "ix_passkey_credentials_user_id",
        "passkey_credentials",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop passkey_credentials table."""
    op.drop_index("ix_passkey_credentials_user_id", table_name="passkey_credentials")
    op.drop_index("ix_passkey_credentials_credential_id", table_name="passkey_credentials")
    op.drop_table("passkey_credentials")
