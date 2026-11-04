"""Add password_hash column to staff_users for email+password login (CR-007).

Nullable: existing users keep working with token-only auth until a password
is set via CLI (``python -m app.staff.cli set-password``).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("staff_users", sa.Column("password_hash", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("staff_users", "password_hash")
