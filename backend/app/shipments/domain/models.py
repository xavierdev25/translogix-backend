"""Internal shipments created through the B2B API (system of record: TransLogix DB).

Guías come from a PostgreSQL sequence and keep the canonical contract format
``TLX-dddddd-CO`` so tracking and chat normalisers accept them. The reserved
range 200000-299999 does not overlap the guías of the simulated TMS dataset
(DEC-SHP1); gaps are allowed (a rolled-back item consumes a number).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.shipment_batches import Destinatario

GUIDE_SEQUENCE_NAME = "shipment_guide_seq"
GUIDE_MIN = 200_000
GUIDE_MAX = 299_999


class ShipmentStatus(StrEnum):
    # Provisional (DEC-SHP5): the business still has to define "confirmed for dispatch".
    REGISTRADO = "REGISTRADO"


def format_guia(number: int) -> str:
    if not GUIDE_MIN <= number <= GUIDE_MAX:
        raise ValueError("guía fuera del rango reservado")
    return f"TLX-{number:06d}-CO"


@dataclass(frozen=True)
class ShipmentDraft:
    """A validated and quoted item, ready to persist. Built without touching the DB."""

    client_id: UUID
    referencia_cliente: str
    origen_codigo_dane: str
    destino_codigo_dane: str
    request: CotizacionRequest
    quote: CotizacionResponse
    tarifa_version: str
    cotizado_en: datetime
    recipient: Destinatario

    @property
    def codigo_trayecto(self) -> str:
        return self.quote.ruta.codigo_trayecto

    @staticmethod
    def decimal(value: float) -> Decimal:
        return Decimal(str(value))


@dataclass(frozen=True)
class CreatedShipment:
    shipment_id: UUID
    numero_guia: str
    flete_total_cop: int
    formula_version: str
