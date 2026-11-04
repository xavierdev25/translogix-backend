from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base

_KG = Numeric(10, 2)


class TariffProfileRow(Base):
    """Versioned profile. Its rules never change once loaded: a change is a new version."""

    __tablename__ = "tariff_profiles"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        # At most one ACTIVE profile per type: the active tariff is never ambiguous.
        Index(
            "uq_tariff_profiles_one_active_per_type",
            "environment_type",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    environment_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    iva_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    volumetric_divisor: Mapped[int]
    created_at: Mapped[datetime]
    effective_from: Mapped[datetime]
    effective_until: Mapped[datetime | None]


class TariffRuleRow(Base):
    __tablename__ = "tariff_rules"
    __table_args__ = (UniqueConstraint("tariff_profile_id", "destination_city_code"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tariff_profile_id: Mapped[UUID] = mapped_column(ForeignKey("tariff_profiles.id"))
    destination_city_code: Mapped[str] = mapped_column(String(8))
    base_price_cop: Mapped[int] = mapped_column(BigInteger)
    included_weight_kg: Mapped[Decimal] = mapped_column(_KG)
    excess_price_per_kg_cop: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime]


class TariffQuotationRow(Base):
    """Immutable snapshot (spec 1.1 §6.5): every value used is copied, not referenced."""

    __tablename__ = "tariff_quotations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    origen_codigo_dane: Mapped[str] = mapped_column(String(8))
    destino_codigo_dane: Mapped[str] = mapped_column(String(8))
    peso_real_kg: Mapped[Decimal] = mapped_column(_KG)
    alto_cm: Mapped[Decimal] = mapped_column(_KG)
    ancho_cm: Mapped[Decimal] = mapped_column(_KG)
    largo_cm: Mapped[Decimal] = mapped_column(_KG)
    peso_volumetrico_kg: Mapped[Decimal] = mapped_column(_KG)
    peso_facturable_kg: Mapped[Decimal] = mapped_column(_KG)
    peso_excedente_kg: Mapped[Decimal] = mapped_column(_KG)
    tariff_profile_id: Mapped[UUID] = mapped_column(ForeignKey("tariff_profiles.id"), index=True)
    profile_name: Mapped[str] = mapped_column(String(64))
    profile_version: Mapped[int]
    profile_type: Mapped[str] = mapped_column(String(16))
    base_price_cop: Mapped[int] = mapped_column(BigInteger)
    included_weight_kg: Mapped[Decimal] = mapped_column(_KG)
    excess_price_per_kg_cop: Mapped[int] = mapped_column(BigInteger)
    volumetric_divisor: Mapped[int]
    iva_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    flete_cop: Mapped[int] = mapped_column(BigInteger)
    iva_cop: Mapped[int] = mapped_column(BigInteger)
    total_cop: Mapped[int] = mapped_column(BigInteger)
    formula_version: Mapped[str] = mapped_column(String(32))
    precio_comercial: Mapped[bool]
    calculated_at: Mapped[datetime]
