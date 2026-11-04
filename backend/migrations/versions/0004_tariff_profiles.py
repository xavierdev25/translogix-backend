"""Tariff profiles (CR-003): versioned profiles, rules per destination, quotation snapshots.

Seeds the TEST_ONLY profile ``matriz-pruebas`` v1 with the five rules of spec 1.1 §6.2 and
IVA 19 % (DEC-TAR4). Additive only; existing tables are not touched.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_PROFILE_ID = uuid.UUID("7c3b2a10-5d4e-4f6a-9b8c-0d1e2f3a4b5c")
SEED_FROM = datetime(2026, 1, 1, tzinfo=ZoneInfo("America/Bogota"))
# Spec 1.1 §6.2: destination (DANE), base COP, included kg, excess COP/kg. TEST_ONLY prices.
SEED_RULES = (
    ("08001", 28_000, "5.50", 5_000),  # Barranquilla
    ("68001", 19_300, "5.00", 3_600),  # Bucaramanga
    ("76001", 19_800, "5.00", 3_700),  # Cali
    ("13001", 26_900, "5.50", 4_800),  # Cartagena
    ("54001", 23_800, "5.00", 4_400),  # Cúcuta
)


def _seed() -> None:
    profiles = sa.table(
        "tariff_profiles",
        *(sa.column(c) for c in ("id", "name", "version", "environment_type", "status")),
        *(sa.column(c) for c in ("iva_pct", "volumetric_divisor", "created_at")),
        *(sa.column(c) for c in ("effective_from", "effective_until")),
    )
    rules = sa.table(
        "tariff_rules",
        *(sa.column(c) for c in ("id", "tariff_profile_id", "destination_city_code")),
        *(sa.column(c) for c in ("base_price_cop", "included_weight_kg")),
        *(sa.column(c) for c in ("excess_price_per_kg_cop", "created_at")),
    )
    op.bulk_insert(
        profiles,
        [
            {
                "id": SEED_PROFILE_ID,
                "name": "matriz-pruebas",
                "version": 1,
                "environment_type": "TEST_ONLY",
                "status": "ACTIVE",
                "iva_pct": Decimal("19.00"),
                "volumetric_divisor": 4000,
                "created_at": SEED_FROM,
                "effective_from": SEED_FROM,
                "effective_until": None,
            }
        ],
    )
    op.bulk_insert(
        rules,
        [
            {
                "id": uuid.uuid5(SEED_PROFILE_ID, code),
                "tariff_profile_id": SEED_PROFILE_ID,
                "destination_city_code": code,
                "base_price_cop": base,
                "included_weight_kg": Decimal(included),
                "excess_price_per_kg_cop": excess,
                "created_at": SEED_FROM,
            }
            for code, base, included, excess in SEED_RULES
        ],
    )


def upgrade() -> None:
    op.create_table(
        "tariff_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("environment_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("iva_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("volumetric_divisor", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariff_profiles")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_tariff_profiles_name_version")),
    )
    op.create_index(
        "uq_tariff_profiles_one_active_per_type",
        "tariff_profiles",
        ["environment_type"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "tariff_quotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("origen_codigo_dane", sa.String(length=8), nullable=False),
        sa.Column("destino_codigo_dane", sa.String(length=8), nullable=False),
        sa.Column("peso_real_kg", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("alto_cm", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("ancho_cm", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("largo_cm", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("peso_volumetrico_kg", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("peso_facturable_kg", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("peso_excedente_kg", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("tariff_profile_id", sa.Uuid(), nullable=False),
        sa.Column("profile_name", sa.String(length=64), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("profile_type", sa.String(length=16), nullable=False),
        sa.Column("base_price_cop", sa.BigInteger(), nullable=False),
        sa.Column("included_weight_kg", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("excess_price_per_kg_cop", sa.BigInteger(), nullable=False),
        sa.Column("volumetric_divisor", sa.Integer(), nullable=False),
        sa.Column("iva_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("flete_cop", sa.BigInteger(), nullable=False),
        sa.Column("iva_cop", sa.BigInteger(), nullable=False),
        sa.Column("total_cop", sa.BigInteger(), nullable=False),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("precio_comercial", sa.Boolean(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tariff_profile_id"],
            ["tariff_profiles.id"],
            name=op.f("fk_tariff_quotations_tariff_profile_id_tariff_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariff_quotations")),
    )
    op.create_index(
        op.f("ix_tariff_quotations_tariff_profile_id"),
        "tariff_quotations",
        ["tariff_profile_id"],
        unique=False,
    )
    op.create_table(
        "tariff_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tariff_profile_id", sa.Uuid(), nullable=False),
        sa.Column("destination_city_code", sa.String(length=8), nullable=False),
        sa.Column("base_price_cop", sa.BigInteger(), nullable=False),
        sa.Column("included_weight_kg", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("excess_price_per_kg_cop", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tariff_profile_id"],
            ["tariff_profiles.id"],
            name=op.f("fk_tariff_rules_tariff_profile_id_tariff_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariff_rules")),
        sa.UniqueConstraint(
            "tariff_profile_id",
            "destination_city_code",
            name=op.f("uq_tariff_rules_tariff_profile_id_destination_city_code"),
        ),
    )
    _seed()


def downgrade() -> None:
    op.drop_table("tariff_rules")
    op.drop_index(op.f("ix_tariff_quotations_tariff_profile_id"), table_name="tariff_quotations")
    op.drop_table("tariff_quotations")
    op.drop_index(
        "uq_tariff_profiles_one_active_per_type",
        table_name="tariff_profiles",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_table("tariff_profiles")
