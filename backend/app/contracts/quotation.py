"""Quotation facade (``POST /api/v1/cotizar``).

Amounts are outputs of formula ``provisional-v1`` (DEC-Q2/Q3), not confirmed
official tariffs. The response validates its own arithmetic so that no
implementation can return a total that does not add up.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import UUID4, Field, model_validator

from app.contracts.common import ContractModel, Cop

FORMULA_VERSION = "provisional-v1"
VOLUMETRIC_DIVISOR = 4000


class CotizacionRequest(ContractModel):
    origen: str = Field(min_length=1, max_length=80, examples=["Bogotá D.C."])
    destino: str = Field(min_length=1, max_length=80, examples=["Medellín"])
    alto_cm: float = Field(gt=0)
    ancho_cm: float = Field(gt=0)
    largo_cm: float = Field(gt=0)
    peso_real_kg: float = Field(gt=0)
    valor_declarado_cop: Cop
    requiere_cadena_frio: bool = Field(default=False, description="Extensión del MVP.")
    mercancia_peligrosa: bool = Field(default=False, description="Extensión del MVP.")


class RutaCotizada(ContractModel):
    codigo_trayecto: str = Field(pattern=r"^TLX-[A-Z]{3}-[A-Z]{3}-\d{3}$")
    origen: str
    destino: str


class TarifaAplicada(ContractModel):
    tipo: Literal["REAL", "VOLUMETRICO"]
    cop_por_kg: Cop


class RecargoCodigo(StrEnum):
    CADENA_FRIO = "CADENA_FRIO"
    MERCANCIA_PELIGROSA = "MERCANCIA_PELIGROSA"


class Recargo(ContractModel):
    codigo: RecargoCodigo
    descripcion: str
    valor_cop: Cop


class CalculoFlete(ContractModel):
    peso_volumetrico_kg: float = Field(ge=0)
    peso_facturable_kg: float = Field(gt=0)
    tarifa_aplicada: TarifaAplicada
    flete_minimo_cop: Cop = Field(description="Piso de la ruta (`flete_minimo_cop` del CSV).")
    flete_minimo_aplicado: bool = Field(
        description="True si peso facturable x tarifa quedó por debajo del mínimo (DEC-Q2)."
    )
    flete_base_cop: Cop = Field(description="máx(flete mínimo, peso facturable x tarifa).")
    seguro_cop: Cop
    recargos: list[Recargo]
    flete_total_cop: Cop

    @model_validator(mode="after")
    def _totals_add_up(self) -> Self:
        if self.peso_facturable_kg < self.peso_volumetrico_kg:
            raise ValueError("peso_facturable_kg is max(real, volumetric)")
        if self.flete_base_cop < self.flete_minimo_cop:
            raise ValueError("flete_base_cop can never be below flete_minimo_cop")
        if self.flete_minimo_aplicado and self.flete_base_cop != self.flete_minimo_cop:
            raise ValueError("when the minimum applies, flete_base_cop equals flete_minimo_cop")
        codes = [recargo.codigo for recargo in self.recargos]
        if len(codes) != len(set(codes)):
            raise ValueError("each surcharge may appear once")
        expected = self.flete_base_cop + self.seguro_cop + sum(r.valor_cop for r in self.recargos)
        if self.flete_total_cop != expected:
            raise ValueError(f"flete_total_cop must be base + seguro + recargos = {expected}")
        return self


class CotizacionResponse(ContractModel):
    cotizacion_id: UUID4
    ruta: RutaCotizada
    calculo: CalculoFlete
    tiempo_entrega_horas: int = Field(gt=0)
    advertencias: list[str]
    supuestos: list[str] = Field(examples=[["formula_version=provisional-v1"]])
    formula_version: str = FORMULA_VERSION
