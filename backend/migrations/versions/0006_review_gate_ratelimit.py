"""Review gate for dangerous goods (BIZ-01) and shared rate limit (OPS-01), CR-010.

- ``shipments.review_status``: NO_REQUERIDA by default. Backfill:
  - ``mercancia_peligrosa`` shipments become PENDIENTE;
  - those that already have a decision in ``shipment_reviews`` get that decision.
- ``rate_limit_counters``: one-minute windows per key.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shipments",
        sa.Column(
            "review_status", sa.String(length=16), nullable=False, server_default="NO_REQUERIDA"
        ),
    )
    op.execute("UPDATE shipments SET review_status = 'PENDIENTE' WHERE mercancia_peligrosa")
    op.execute(
        "UPDATE shipments SET review_status = r.decision FROM shipment_reviews r"
        " WHERE r.shipment_id = shipments.id"
    )
    op.create_table(
        "rate_limit_counters",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window_start", name=op.f("pk_rate_limit_counters")),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_counters")
    op.drop_column("shipments", "review_status")
