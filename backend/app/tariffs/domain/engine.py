"""Tariff engine ``excedente-v1`` (CR-003): pure, ``Decimal`` only, no I/O.

See ``app.contracts.tariffs`` for the formula. Weights keep two decimals: the
volumetric weight is quantised half-up to 0.01 kg (DEC-TAR2) and nothing is
rounded up to the next kilogram. Money is whole COP, half-up per line (DEC-Q8).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from app.contracts.money import to_cop
from app.contracts.tariffs import TipoPerfil

CENTI = Decimal("0.01")


class ProfileStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class TariffProfile:
    id: UUID
    name: str
    version: int
    environment_type: TipoPerfil
    status: ProfileStatus
    iva_pct: Decimal
    volumetric_divisor: int
    effective_from: datetime
    effective_until: datetime | None


@dataclass(frozen=True)
class TariffRule:
    destination_city_code: str
    base_price_cop: int
    included_weight_kg: Decimal
    excess_price_per_kg_cop: int


@dataclass(frozen=True)
class Breakdown:
    peso_volumetrico_kg: Decimal
    peso_facturable_kg: Decimal
    peso_excedente_kg: Decimal
    flete_cop: int
    iva_cop: int
    total_cop: int


def volumetric_weight(alto: Decimal, ancho: Decimal, largo: Decimal, divisor: int) -> Decimal:
    return (alto * ancho * largo / Decimal(divisor)).quantize(CENTI, rounding=ROUND_HALF_UP)


def compute(
    rule: TariffRule,
    profile: TariffProfile,
    *,
    peso_real_kg: Decimal,
    alto_cm: Decimal,
    ancho_cm: Decimal,
    largo_cm: Decimal,
) -> Breakdown:
    volumetric = volumetric_weight(alto_cm, ancho_cm, largo_cm, profile.volumetric_divisor)
    billable = max(peso_real_kg, volumetric).quantize(CENTI)
    excess = max(Decimal(0), billable - rule.included_weight_kg).quantize(CENTI)
    flete = rule.base_price_cop + to_cop(excess * rule.excess_price_per_kg_cop)
    iva = to_cop(Decimal(flete) * profile.iva_pct / 100)
    return Breakdown(volumetric, billable, excess, flete, iva, flete + iva)


def is_effective(profile: TariffProfile, now: datetime) -> bool:
    return (
        profile.status is ProfileStatus.ACTIVE
        and profile.effective_from <= now
        and (profile.effective_until is None or now < profile.effective_until)
    )
