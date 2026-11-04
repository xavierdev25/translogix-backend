"""Quotation by versioned tariff profiles (CR-003, added in 1.5.0).

Formula ``excedente-v1`` (spec 1.1 §6, decisions DEC-TAR1 to DEC-TAR6):

    peso_volumetrico = alto * ancho * largo / divisor        (2 decimals, half-up)
    P                = max(peso_real, peso_volumetrico)      (billable weight, every destination)
    flete            = B + max(0, P - L) * A                 (excess right after L; whole COP)
    iva              = flete * iva_pct / 100                 (whole COP, half-up)
    total            = flete + iva

B, L, A, the IVA rate and the divisor come from the active profile, never from code.
Weights are exact to 0.01 kg and are not rounded up to the next kilogram. A
``TEST_ONLY`` profile never produces a commercial price (``precio_comercial=false``).
``POST /cotizar`` (``provisional-v1``) is unchanged.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import UUID4, Field, PlainSerializer, model_validator

from app.contracts.common import BogotaDatetime, ContractModel, Cop
from app.contracts.money import to_cop

FORMULA_EXCEDENTE = "excedente-v1"

_AS_NUMBER = PlainSerializer(float, return_type=float, when_used="json")
# Weights and dimensions: exact Decimal with two decimals, published as JSON numbers.
Medida = Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2), _AS_NUMBER]
Peso = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2), _AS_NUMBER]


class TipoPerfil(StrEnum):
    TEST_ONLY = "TEST_ONLY"
    COMMERCIAL = "COMMERCIAL"


class CotizacionPerfilRequest(ContractModel):
    origen: str = Field(
        min_length=1, max_length=80, description="Se registra; no cambia el precio (DEC-TAR5)."
    )
    destino: str = Field(min_length=1, max_length=80, examples=["Barranquilla"])
    peso_real_kg: Medida
    alto_cm: Medida
    ancho_cm: Medida
    largo_cm: Medida


class CiudadRef(ContractModel):
    codigo_dane: str = Field(pattern=r"^\d{5}$")
    municipio: str


class TarifaPerfilAplicada(ContractModel):
    """Copy of the rule used: the quote never depends on the profile changing later."""

    perfil_id: UUID4
    perfil_nombre: str
    perfil_version: int = Field(ge=1)
    tipo_perfil: TipoPerfil
    base_cop: Cop
    peso_incluido_kg: Peso
    precio_kg_excedente_cop: Cop
    divisor_volumetrico: int = Field(gt=0)
    iva_pct: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2), _AS_NUMBER]


class CotizacionPerfilResponse(ContractModel):
    cotizacion_id: UUID4
    formula_version: str = Field(examples=[FORMULA_EXCEDENTE])
    precio_comercial: bool = Field(description="Siempre false con un perfil TEST_ONLY.")
    origen: CiudadRef
    destino: CiudadRef
    peso_real_kg: Peso
    peso_volumetrico_kg: Peso
    peso_facturable_kg: Peso
    peso_excedente_kg: Peso
    tarifa: TarifaPerfilAplicada
    flete_cop: Cop
    iva_cop: Cop
    total_cop: Cop
    advertencias: list[str]
    calculado_en: BogotaDatetime

    @model_validator(mode="after")
    def _arithmetic_adds_up(self) -> Self:
        rate = self.tarifa
        billable = max(self.peso_real_kg, self.peso_volumetrico_kg)
        excess = max(Decimal(0), billable - rate.peso_incluido_kg)
        if self.peso_facturable_kg != billable or self.peso_excedente_kg != excess:
            raise ValueError("billable and excess weights do not follow excedente-v1")
        if self.flete_cop != rate.base_cop + to_cop(excess * rate.precio_kg_excedente_cop):
            raise ValueError("flete_cop does not follow excedente-v1")
        if self.iva_cop != to_cop(Decimal(self.flete_cop) * rate.iva_pct / 100):
            raise ValueError("iva_cop does not match the profile rate")
        if self.total_cop != self.flete_cop + self.iva_cop:
            raise ValueError("total_cop must be flete_cop + iva_cop")
        if self.precio_comercial and rate.tipo_perfil is TipoPerfil.TEST_ONLY:
            raise ValueError("a TEST_ONLY profile never yields a commercial price")
        return self
