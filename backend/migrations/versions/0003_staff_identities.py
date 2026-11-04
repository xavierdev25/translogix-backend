"""Staff identities (CR-002): internal users, personal tokens, actor column in the B2B audit.

Additive only: two new tables and a nullable ``api_audit_events.staff_user_id``; no
existing row changes.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column(
            "roles",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("estado", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_users")),
        sa.UniqueConstraint("email", name=op.f("uq_staff_users_email")),
    )
    op.create_table(
        "staff_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("staff_user_id", sa.Uuid(), nullable=False),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["staff_user_id"],
            ["staff_users.id"],
            name=op.f("fk_staff_tokens_staff_user_id_staff_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_tokens")),
        sa.UniqueConstraint("key_prefix", name=op.f("uq_staff_tokens_key_prefix")),
    )
    op.create_index(
        op.f("ix_staff_tokens_staff_user_id"), "staff_tokens", ["staff_user_id"], unique=False
    )
    op.add_column("api_audit_events", sa.Column("staff_user_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_api_audit_events_staff_user_id"),
        "api_audit_events",
        ["staff_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_audit_events_staff_user_id"), table_name="api_audit_events")
    op.drop_column("api_audit_events", "staff_user_id")
    op.drop_index(op.f("ix_staff_tokens_staff_user_id"), table_name="staff_tokens")
    op.drop_table("staff_tokens")
    op.drop_table("staff_users")
