"""Deterministic quotation, formula ``provisional-v1`` (DEC-Q1/Q2/Q3/Q4/Q6/Q8/T1).

Official (challenge §4.2):
    peso_volumetrico = alto * ancho * largo / 4000
    peso_facturable  = max(peso_real, peso_volumetrico)
    seguro           = max(1 % * valor_declarado, minimo)
    flete_total      = flete_base + seguro (+ surcharges, MVP extension)
Provisional (the PDF does not define them):
    tarifa      = real tariff if billed by real weight, volumetric tariff otherwise
    flete_base  = max(flete_minimo, peso_facturable * tarifa)
    recargo frío = route % over flete_base; mercancía peligrosa = fixed route value
"""

from decimal import Decimal
from typing import Literal
from uuid import uuid4

from app.contracts.common import ContractError, ErrorCode
from app.contracts.money import to_cop
from app.contracts.quotation import (
    FORMULA_VERSION,
    CalculoFlete,
    CotizacionRequest,
    CotizacionResponse,
    Recargo,
    RecargoCodigo,
    RutaCotizada,
    TarifaAplicada,
)
from app.modules.quotation.data import TariffData

PDF_INSURANCE_MINIMUM = 2500
COLD_CHAIN_SLOW_HOURS = 48
_TWO_DECIMALS = Decimal("0.01")


def _kg(value: Decimal) -> float:
    return float(value.quantize(_TWO_DECIMALS))


class QuotationService:
    """``QuotationPort``: pure, no I/O, no LLM. Raises ``ContractError`` for unquotable input."""

    def __init__(
        self, data: TariffData, *, insurance_min_source: Literal["pdf", "csv"] = "pdf"
    ) -> None:
        self._data = data
        self._insurance_source = insurance_min_source

    def quote(self, request: CotizacionRequest) -> CotizacionResponse:
        origen = self._city(request.origen)
        destino = self._city(request.destino)
        route = self._data.route(origen, destino)
        if route is None:
            raise ContractError(
                ErrorCode.RUTA_SIN_TARIFA, details={"origen": origen, "destino": destino}
            )
        coverage = self._data.coverage_for(destino)
        if request.requiere_cadena_frio and coverage is not None and not coverage.refrigerado:
            raise ContractError(
                ErrorCode.SERVICIO_NO_DISPONIBLE,
                f"No hay servicio refrigerado disponible hacia {destino}.",
                details={"servicio": "CADENA_FRIO", "destino": destino},
            )

        real = Decimal(str(request.peso_real_kg))
        volume_cm3 = (
            Decimal(str(request.alto_cm))
            * Decimal(str(request.ancho_cm))
            * Decimal(str(request.largo_cm))
        )
        volumetric = volume_cm3 / route.divisor_volumetrico
        billable = max(real, volumetric)
        by_real = real >= volumetric
        rate = route.tarifa_kg_real if by_real else route.tarifa_kg_volumetrico

        weighted = to_cop(billable * rate)
        base = max(route.flete_minimo, weighted)

        insurance_pct = route.porcentaje_seguro / 100
        pdf_minimum, csv_minimum = PDF_INSURANCE_MINIMUM, route.seguro_minimo
        minimum = pdf_minimum if self._insurance_source == "pdf" else csv_minimum
        insurance = max(to_cop(Decimal(request.valor_declarado_cop) * insurance_pct), minimum)

        surcharges = []
        if request.requiere_cadena_frio:
            surcharges.append(
                Recargo(
                    codigo=RecargoCodigo.CADENA_FRIO,
                    descripcion=f"Recargo cadena de frío {route.recargo_frio_pct.normalize()} %",
                    valor_cop=to_cop(Decimal(base) * route.recargo_frio_pct / 100),
                )
            )
        if request.mercancia_peligrosa:
            surcharges.append(
                Recargo(
                    codigo=RecargoCodigo.MERCANCIA_PELIGROSA,
                    descripcion="Recargo mercancía peligrosa (valor fijo de la ruta)",
                    valor_cop=route.recargo_peligrosa,
                )
            )

        other = ("csv", csv_minimum) if self._insurance_source == "pdf" else ("pdf", pdf_minimum)
        return CotizacionResponse(
            cotizacion_id=uuid4(),
            ruta=RutaCotizada(
                codigo_trayecto=route.codigo, origen=route.origen, destino=route.destino
            ),
            calculo=CalculoFlete(
                peso_volumetrico_kg=_kg(volumetric),
                peso_facturable_kg=_kg(billable),
                tarifa_aplicada=TarifaAplicada(
                    tipo="REAL" if by_real else "VOLUMETRICO", cop_por_kg=int(rate)
                ),
                flete_minimo_cop=route.flete_minimo,
                flete_minimo_aplicado=weighted < route.flete_minimo,
                flete_base_cop=base,
                seguro_cop=insurance,
                recargos=surcharges,
                flete_total_cop=base + insurance + sum(s.valor_cop for s in surcharges),
            ),
            tiempo_entrega_horas=route.tiempo_horas,
            advertencias=self._warnings(request, route.tiempo_horas, destino, real, volume_cm3),
            supuestos=[
                f"formula_version={FORMULA_VERSION}",
                f"seguro_minimo_fuente={self._insurance_source}({minimum})",
                f"seguro_minimo_alternativo={other[0]}({other[1]})",
            ],
            formula_version=FORMULA_VERSION,
        )

    def _city(self, name: str) -> str:
        canonical = self._data.canonical_city(name)
        if canonical is None:
            raise ContractError(ErrorCode.CIUDAD_NO_RECONOCIDA, details={"ciudad": name})
        return canonical

    def _warnings(
        self,
        request: CotizacionRequest,
        route_hours: int,
        destino: str,
        real_kg: Decimal,
        volume_cm3: Decimal,
    ) -> list[str]:
        warnings = []
        coverage = self._data.coverage_for(destino)
        if coverage is not None:
            if coverage.tiempo_horas != route_hours:  # DEC-T1
                warnings.append(
                    f"Tiempo de entrega en conflicto: la matriz indica {route_hours} h y el "
                    f"directorio de cobertura {coverage.tiempo_horas} h para {destino}; "
                    "se usa la matriz."
                )
            if real_kg > coverage.peso_maximo_kg:  # DEC-Q4: warn, do not block
                warnings.append(
                    f"El peso real supera el máximo por paquete en {destino} "
                    f"({coverage.peso_maximo_kg.normalize()} kg); requiere validación operativa."
                )
            if volume_cm3 / 1_000_000 > coverage.volumen_maximo_m3:
                warnings.append(
                    f"El volumen supera el máximo en {destino} "
                    f"({coverage.volumen_maximo_m3.normalize()} m³); requiere validación operativa."
                )
        if request.requiere_cadena_frio and route_hours > COLD_CHAIN_SLOW_HOURS:
            warnings.append(
                f"Cadena de frío con tránsito de {route_hours} h: confirmar con operaciones."
            )
        return warnings
