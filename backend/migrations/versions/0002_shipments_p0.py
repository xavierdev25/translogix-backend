"""Bulk shipments P0: enterprise clients, API keys, batches, shipments, recipients, audit.

Adds the guía sequence ``shipment_guide_seq`` (range 200000-299999, DEC-SHP1; gaps
allowed). ``shipment_batch_items.shipment_id`` and ``shipments.batch_item_id`` point
at each other, so the first FK is added after both tables exist. Existing tables are
not touched.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04 06:57:26.455315+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUIDE_SEQUENCE = sa.Sequence(
    "shipment_guide_seq", start=200_000, minvalue=200_000, maxvalue=299_999
)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
KG = sa.Numeric(precision=10, scale=2)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(GUIDE_SEQUENCE))

    op.create_table(
        "enterprise_clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("nit", sa.String(length=20), nullable=False),
        sa.Column("razon_social", sa.String(length=200), nullable=False),
        sa.Column("estado", sa.String(length=16), nullable=False),
        sa.Column("tipo_cuenta", sa.String(length=32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enterprise_clients")),
        sa.UniqueConstraint("nit", name=op.f("uq_enterprise_clients_nit")),
    )
    op.create_table(
        "client_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["enterprise_clients.id"],
            name=op.f("fk_client_api_keys_client_id_enterprise_clients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_api_keys")),
        sa.UniqueConstraint("key_prefix", name=op.f("uq_client_api_keys_key_prefix")),
    )
    op.create_index(
        op.f("ix_client_api_keys_client_id"), "client_api_keys", ["client_id"], unique=False
    )
    op.create_table(
        "shipment_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("referencia_lote", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("accepted_items", sa.Integer(), nullable=False),
        sa.Column("rejected_items", sa.Integer(), nullable=False),
        sa.Column("locked_until", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("completed_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["enterprise_clients.id"],
            name=op.f("fk_shipment_batches_client_id_enterprise_clients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_batches")),
        sa.UniqueConstraint(
            "client_id",
            "referencia_lote",
            name=op.f("uq_shipment_batches_client_id_referencia_lote"),
        ),
    )
    op.create_table(
        "shipment_batch_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("referencia_cliente", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=True),
        sa.Column("errors_json", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("processed_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["shipment_batches.id"],
            name=op.f("fk_shipment_batch_items_batch_id_shipment_batches"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_batch_items")),
        sa.UniqueConstraint(
            "batch_id", "line_number", name=op.f("uq_shipment_batch_items_batch_id_line_number")
        ),
    )
    op.create_index(
        op.f("ix_shipment_batch_items_shipment_id"),
        "shipment_batch_items",
        ["shipment_id"],
        unique=False,
    )
    op.create_table(
        "shipments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("numero_guia", sa.String(length=13), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("referencia_cliente", sa.String(length=64), nullable=False),
        sa.Column("batch_item_id", sa.Uuid(), nullable=True),
        sa.Column("origen_codigo_dane", sa.String(length=8), nullable=False),
        sa.Column("destino_codigo_dane", sa.String(length=8), nullable=False),
        sa.Column("codigo_trayecto", sa.String(length=20), nullable=False),
        sa.Column("alto_cm", KG, nullable=False),
        sa.Column("ancho_cm", KG, nullable=False),
        sa.Column("largo_cm", KG, nullable=False),
        sa.Column("peso_real_kg", KG, nullable=False),
        sa.Column("peso_volumetrico_kg", KG, nullable=False),
        sa.Column("peso_facturable_kg", KG, nullable=False),
        sa.Column("valor_declarado_cop", sa.BigInteger(), nullable=False),
        sa.Column("requiere_cadena_frio", sa.Boolean(), nullable=False),
        sa.Column("mercancia_peligrosa", sa.Boolean(), nullable=False),
        sa.Column("cotizacion_id", sa.Uuid(), nullable=True),
        sa.Column("flete_total_cop", sa.BigInteger(), nullable=True),
        sa.Column("formula_version", sa.String(length=32), nullable=True),
        sa.Column("tarifa_version", sa.String(length=80), nullable=True),
        sa.Column("cotizado_en", TS, nullable=True),
        sa.Column("estado", sa.String(length=16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_item_id"],
            ["shipment_batch_items.id"],
            name=op.f("fk_shipments_batch_item_id_shipment_batch_items"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["enterprise_clients.id"],
            name=op.f("fk_shipments_client_id_enterprise_clients"),
        ),
        sa.ForeignKeyConstraint(
            ["cotizacion_id"],
            ["quotes.cotizacion_id"],
            name=op.f("fk_shipments_cotizacion_id_quotes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint(
            "client_id",
            "referencia_cliente",
            name=op.f("uq_shipments_client_id_referencia_cliente"),
        ),
        sa.UniqueConstraint("numero_guia", name=op.f("uq_shipments_numero_guia")),
    )
    op.create_index(
        op.f("ix_shipments_batch_item_id"), "shipments", ["batch_item_id"], unique=False
    )
    # The cycle closes once both tables exist.
    op.create_foreign_key(
        op.f("fk_shipment_batch_items_shipment_id_shipments"),
        "shipment_batch_items",
        "shipments",
        ["shipment_id"],
        ["id"],
    )
    op.create_table(
        "shipment_recipients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("tipo_documento", sa.String(length=10), nullable=True),
        sa.Column("numero_documento", sa.String(length=20), nullable=True),
        sa.Column("telefono", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("direccion", sa.String(length=200), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_shipment_recipients_shipment_id_shipments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_recipients")),
        sa.UniqueConstraint("shipment_id", name=op.f("uq_shipment_recipients_shipment_id")),
    )
    op.create_table(
        "api_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_audit_events")),
    )
    op.create_index(
        op.f("ix_api_audit_events_client_id_created_at"),
        "api_audit_events",
        ["client_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_audit_events_client_id_created_at"), table_name="api_audit_events")
    op.drop_table("api_audit_events")
    op.drop_table("shipment_recipients")
    op.drop_constraint(
        op.f("fk_shipment_batch_items_shipment_id_shipments"),
        "shipment_batch_items",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_shipments_batch_item_id"), table_name="shipments")
    op.drop_table("shipments")
    op.drop_index(op.f("ix_shipment_batch_items_shipment_id"), table_name="shipment_batch_items")
    op.drop_table("shipment_batch_items")
    op.drop_table("shipment_batches")
    op.drop_index(op.f("ix_client_api_keys_client_id"), table_name="client_api_keys")
    op.drop_table("client_api_keys")
    op.drop_table("enterprise_clients")
    op.execute(sa.schema.DropSequence(GUIDE_SEQUENCE))
