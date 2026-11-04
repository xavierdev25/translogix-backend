"""Bulk shipments for enterprise clients (B2B API, added in 1.3.0).

``POST /api/v1/shipment-batches`` creates many shipments in one request. Each
item is processed on its own: valid items become shipments with an internal
guía; invalid items are rejected with structured, PII-free errors. The batch is
idempotent per client and ``referencia_lote``.

Guías created here are internal TransLogix identifiers: they do not confirm a
carrier guía nor any synchronisation with an external TMS (DEC-SHP1).
"""

from enum import StrEnum

from pydantic import UUID4, Field

from app.contracts.common import BogotaDatetime, ContractModel, Cop, ErrorCode, NumeroGuia
from app.contracts.quotation import CotizacionRequest

REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,63}$"
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
ITEMS_PAGE_MAX = 200


class ApiScope(StrEnum):
    SHIPMENTS_CREATE = "shipments:create"
    SHIPMENTS_READ = "shipments:read"
    BATCHES_CREATE = "batches:create"
    BATCHES_READ = "batches:read"


class BatchStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"


class BatchItemStatus(StrEnum):
    PENDING = "PENDING"
    CREATED = "CREATED"
    REJECTED = "REJECTED"


class Destinatario(ContractModel):
    """Datos personales del destinatario (PII): se guardan aparte y nunca se devuelven."""

    nombre: str = Field(min_length=1, max_length=120)
    telefono: str = Field(pattern=r"^\+?\d{7,15}$")
    direccion: str = Field(min_length=1, max_length=200)
    tipo_documento: str | None = Field(default=None, max_length=10, examples=["CC"])
    numero_documento: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EnvioLoteItem(CotizacionRequest):
    """Un envío del lote: los campos de cotización canónicos más referencia y destinatario."""

    referencia_cliente: str = Field(
        pattern=REFERENCE_PATTERN,
        description="Única por cliente empresarial, de forma permanente (DEC-SHP3).",
    )
    destinatario: Destinatario


class ShipmentBatchRequest(ContractModel):
    referencia_lote: str = Field(
        pattern=REFERENCE_PATTERN,
        description=(
            "Clave de idempotencia del lote por cliente. Si se envía el header "
            "`Idempotency-Key`, debe ser idéntico (DEC-SHP2)."
        ),
    )
    envios: list[EnvioLoteItem] = Field(
        min_length=1,
        description=(
            "Cada envío se valida por separado: uno inválido se rechaza sin afectar a los "
            "demás. El máximo por lote lo fija `BATCH_MAX_ITEMS`."
        ),
    )


class ItemError(ContractModel):
    """Error de un ítem: código y campo, nunca el valor enviado."""

    code: ErrorCode
    field: str | None = Field(examples=["destino"])


class ShipmentBatchItemResult(ContractModel):
    linea: int = Field(ge=1, description="Posición del envío en `envios`, desde 1.")
    referencia_cliente: str | None = Field(description="null si el ítem no la traía válida.")
    status: BatchItemStatus
    numero_guia: NumeroGuia | None = Field(description="Guía interna; solo si status=CREATED.")
    flete_total_cop: Cop | None = Field(description="Snapshot de la cotización aplicada.")
    formula_version: str | None = Field(
        description="`provisional-v1`: no es una tarifa comercial oficial."
    )
    errores: list[ItemError]


class ShipmentBatchSummary(ContractModel):
    lote_id: UUID4
    referencia_lote: str
    status: BatchStatus
    total: int = Field(ge=0)
    aceptados: int = Field(ge=0)
    rechazados: int = Field(ge=0)
    pendientes: int = Field(ge=0)
    created_at: BogotaDatetime
    started_at: BogotaDatetime | None
    completed_at: BogotaDatetime | None


class ShipmentBatchResponse(ShipmentBatchSummary):
    resultados: list[ShipmentBatchItemResult] = Field(description="Ordenados por `linea`.")


class ShipmentBatchItemsPage(ContractModel):
    items: list[ShipmentBatchItemResult]
    next_cursor: str | None = Field(description="Opaco; null cuando no hay más resultados.")
