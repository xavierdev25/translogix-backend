"""RBAC (CR-009): three roles, handoff assignee and dangerous-goods reviews.

- ``staff_users.roles``: the 1.4.0 roles (ASESOR, SUPERVISOR, OPERADOR_LOGISTICO,
  REVISOR_ESPECIALIZADO) become ``REVIEWER``. Least privilege: nobody gains ``ADMIN``
  through the migration; an ADMIN is granted explicitly afterwards.
- ``handoffs.assigned_staff_user_id``: nullable, so existing rows are unchanged.
- ``shipment_reviews``: new table.

Downgrade maps ``REVIEWER`` → ``ASESOR`` and ``ADMIN`` → ``SUPERVISOR``. The 1.4.0
roles cannot be recovered exactly: the upgrade merged four into one.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UP = {
    "ASESOR": "REVIEWER",
    "SUPERVISOR": "REVIEWER",
    "OPERADOR_LOGISTICO": "REVIEWER",
    "REVISOR_ESPECIALIZADO": "REVIEWER",
}
_DOWN = {"REVIEWER": "ASESOR", "ADMIN": "SUPERVISOR"}


def _remap(mapping: dict[str, str]) -> None:
    connection = op.get_bind()
    users = sa.table(
        "staff_users", sa.column("id", sa.Uuid()), sa.column("roles", postgresql.JSONB())
    )
    for user_id, roles in connection.execute(sa.select(users.c.id, users.c.roles)).all():
        new = sorted({mapping.get(role, role) for role in roles})
        if new != sorted(roles):
            connection.execute(users.update().where(users.c.id == user_id).values(roles=new))


def upgrade() -> None:
    _remap(_UP)
    op.add_column("handoffs", sa.Column("assigned_staff_user_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_handoffs_assigned_staff_user_id"),
        "handoffs",
        ["assigned_staff_user_id"],
        unique=False,
    )
    op.create_table(
        "shipment_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("observaciones", sa.String(length=2000), nullable=False),
        sa.Column("reviewer_staff_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_shipment_reviews_shipment_id_shipments"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_staff_user_id"],
            ["staff_users.id"],
            name=op.f("fk_shipment_reviews_reviewer_staff_user_id_staff_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_reviews")),
        sa.UniqueConstraint("shipment_id", name=op.f("uq_shipment_reviews_shipment_id")),
    )


def downgrade() -> None:
    op.drop_table("shipment_reviews")
    op.drop_index(op.f("ix_handoffs_assigned_staff_user_id"), table_name="handoffs")
    op.drop_column("handoffs", "assigned_staff_user_id")
    _remap(_DOWN)
