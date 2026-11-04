"""Public HTTP surface of the MVP (§5), declared once.

``build_contract_app`` is a specification, not a server: every handler raises
``NotImplementedError``. Its only job is to let FastAPI derive the OpenAPI
document from the same Pydantic models the services import, so
``contracts/openapi.yaml`` cannot drift from the code. The real application
implements these routes with the same paths, operation ids and models.
"""

from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Body, FastAPI, File, Form, Header, Path, Query, Security, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime

from app.contracts.attachments import AttachmentUploadResponse
from app.contracts.channels_http import (
    PbxSimulateRequest,
    PbxTurnRequest,
    PbxTurnResponse,
    WebhookAck,
    WhatsAppSimulationResult,
    WhatsAppWebhookPayload,
)
from app.contracts.chat import ChatMessageRequest, ChatMessageResponse, SessionHistory
from app.contracts.common import CONTRACT_VERSION, ErrorCode
from app.contracts.escalation import HandoffPayload, HandoffStatus, HandoffStatusUpdate
from app.contracts.ops import (
    ADMIN_TOKEN_HEADER,
    ChannelMetricsResponse,
    HandoffListResponse,
    HealthResponse,
    ReadinessResponse,
    ReindexRequest,
    ReindexResponse,
)
from app.contracts.pqrs import DocumentoCodigo, PqrsTicketRequest, PqrsTicketResponse
from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.shipment_batches import (
    IDEMPOTENCY_KEY_HEADER,
    ITEMS_PAGE_MAX,
    BatchItemStatus,
    ShipmentBatchItemsPage,
    ShipmentBatchRequest,
    ShipmentBatchResponse,
    ShipmentBatchSummary,
)
from app.contracts.tariffs import CotizacionPerfilRequest, CotizacionPerfilResponse
from app.contracts.tracking import TrackingResponse

API_PREFIX = "/api/v1"
TITLE = "TransLogix AI — API pública del MVP"
DESCRIPTION = """\
Contrato de integración compartido (dueño: P01). Fuente de verdad junto con los
modelos Pydantic de `backend/app/contracts/`; este archivo se **genera**, no se edita.

Convenciones:

* Idioma `es-CO`; fechas ISO-8601 con offset, expresadas en `America/Bogota` (`-05:00`).
* Dinero: entero en COP. Cálculo interno con `Decimal`, redondeo `ROUND_HALF_UP` por línea.
* IDs: UUID v4 en string, salvo `numero_guia` (`^TLX-\\d{6}-CO$`).
* `X-Request-ID` opcional en la petición; la respuesta siempre lo devuelve.
* Todo error usa el sobre `ErrorEnvelope` con un `code` estable (`x-error-codes` por respuesta).
"""


def _spec_only() -> NoReturn:
    raise NotImplementedError("contract specification; the real application implements it")


def _route(
    operation_id: str,
    *,
    priority: str,
    errors: tuple[ErrorCode, ...] = (),
) -> dict[str, Any]:
    """Shared route options. ``errors`` become documented error responses."""
    return {
        "operation_id": operation_id,
        "openapi_extra": {
            "x-priority": priority,
            "x-error-codes": [code.value for code in errors],
        },
    }


admin_token = APIKeyHeader(
    name=ADMIN_TOKEN_HEADER,
    scheme_name="AdminToken",
    description="Token de administración definido en `ADMIN_TOKEN` (entorno).",
    auto_error=False,
)
AdminTokenDep = Annotated[str | None, Security(admin_token)]

api_key = HTTPBearer(
    scheme_name="ApiKey",
    description="API key de un cliente empresarial: `Authorization: Bearer <api_key>`.",
    auto_error=False,
)
ApiKeyDep = Annotated[HTTPAuthorizationCredentials | None, Security(api_key)]

# 1.4.0 (CR-002): personal token of internal staff, for the routes under /api/v1/internal/.
staff_token = HTTPBearer(
    scheme_name="StaffToken",
    description="Token personal de un usuario interno: `Authorization: Bearer <tlxs_…>`.",
    auto_error=False,
)
StaffTokenDep = Annotated[HTTPAuthorizationCredentials | None, Security(staff_token)]

tracking = APIRouter(tags=["tracking"])
quotation = APIRouter(tags=["cotización"])
pqrs = APIRouter(tags=["pqrs"])
attachments = APIRouter(tags=["adjuntos"])
chat = APIRouter(tags=["web chat"])
whatsapp = APIRouter(prefix="/channels/whatsapp", tags=["whatsapp"])
pbx = APIRouter(prefix="/channels/pbx", tags=["pbx"])
handoffs = APIRouter(prefix="/handoffs", tags=["handoff"])
ops = APIRouter(tags=["operación"])
shipment_batches = APIRouter(prefix="/shipment-batches", tags=["envíos masivos"])


@tracking.get(
    "/tracking/{guia}",
    summary="Rastreo de guía (vista segura para cliente)",
    description="Oficial (PDF). Acepta formas normalizables (`tlx 984021 co`, `TLX984021CO`).",
    **_route(
        "getTracking",
        priority="P0",
        errors=(ErrorCode.GUIA_FORMATO_INVALIDO, ErrorCode.GUIA_NO_ENCONTRADA),
    ),
)
async def get_tracking(guia: Annotated[str, Path(max_length=64)]) -> TrackingResponse:
    _spec_only()


@quotation.post(
    "/cotizar",
    summary="Cotización de flete (fórmula provisional-v1)",
    description="Oficial (PDF). Los valores son salidas de `provisional-v1`, no tarifas oficiales.",
    **_route(
        "createQuote",
        priority="P0",
        errors=(
            ErrorCode.RUTA_SIN_TARIFA,
            ErrorCode.CIUDAD_NO_RECONOCIDA,
            ErrorCode.SERVICIO_NO_DISPONIBLE,
            ErrorCode.EXCEDE_LIMITE_COBERTURA,
        ),
    ),
)
async def create_quote(body: CotizacionRequest) -> CotizacionResponse:
    _spec_only()


@quotation.post(
    "/cotizaciones",
    status_code=201,
    summary="Cotización con perfil tarifario versionado (fórmula excedente-v1)",
    description=(
        "1.5.0 (CR-003). Peso facturable = máx(real, volumétrico); flete = base + excedente x "
        "precio/kg; IVA según el perfil. Persiste un snapshot inmutable. Con un perfil "
        "TEST_ONLY, `precio_comercial` es false. `/cotizar` no cambia."
    ),
    **_route(
        "createProfileQuotation",
        priority="P1",
        errors=(
            ErrorCode.CIUDAD_NO_RECONOCIDA,
            ErrorCode.CIUDAD_NO_CUBIERTA,
            ErrorCode.TARIFA_NO_DISPONIBLE,
            ErrorCode.NO_CONFIGURADO,
        ),
    ),
)
async def create_profile_quotation(body: CotizacionPerfilRequest) -> CotizacionPerfilResponse:
    _spec_only()


@pqrs.post(
    "/pqrs/crear-ticket",
    status_code=201,
    summary="Crea un ticket PQRS con elegibilidad, checklist y liquidación preliminar",
    description="Oficial (PDF). La liquidación es preliminar y no vinculante.",
    **_route(
        "createPqrsTicket",
        priority="P0",
        errors=(ErrorCode.GUIA_NO_ENCONTRADA, ErrorCode.NO_ENCONTRADO),
    ),
)
async def create_pqrs_ticket(body: PqrsTicketRequest) -> PqrsTicketResponse:
    _spec_only()


@attachments.post(
    "/attachments",
    status_code=201,
    summary="Sube un adjunto (PDF o JPG) y devuelve su attachment_id",
    description=(
        "Tipo validado por contenido; límite de tamaño configurable; el nombre de archivo del "
        "cliente no se conserva y el contenido no se registra en logs."
    ),
    **_route("uploadAttachment", priority="P1", errors=(ErrorCode.NO_ENCONTRADO,)),
)
async def upload_attachment(
    file: Annotated[UploadFile, File(description="PDF o JPG.")],
    declared_doc_code: Annotated[DocumentoCodigo | None, Form()] = None,
    session_id: Annotated[UUID | None, Form()] = None,
) -> AttachmentUploadResponse:
    _spec_only()


@chat.post(
    "/chat/messages",
    summary="Envía un mensaje del Web Chat y devuelve la respuesta del núcleo",
    description="Idempotente por `message_id`: un reenvío devuelve la respuesta ya generada.",
    **_route(
        "postChatMessage",
        priority="P0",
        errors=(ErrorCode.NO_ENCONTRADO, ErrorCode.LIMITE_DE_TASA),
    ),
)
async def post_chat_message(body: ChatMessageRequest) -> ChatMessageResponse:
    _spec_only()


@chat.get(
    "/chat/sessions/{session_id}/history",
    summary="Historial de una sesión",
    **_route("getSessionHistory", priority="P0", errors=(ErrorCode.NO_ENCONTRADO,)),
)
async def get_session_history(session_id: UUID) -> SessionHistory:
    _spec_only()


@whatsapp.get(
    "/webhook",
    response_class=PlainTextResponse,
    summary="Verificación del webhook (reto de suscripción de Meta)",
    description="Responde `hub.challenge` en texto plano si `hub.verify_token` coincide.",
    **_route("verifyWhatsAppWebhook", priority="P1", errors=(ErrorCode.NO_AUTORIZADO,)),
)
async def verify_whatsapp_webhook(
    mode: Annotated[str, Query(alias="hub.mode")],
    verify_token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
) -> str:
    _spec_only()


@whatsapp.post(
    "/webhook",
    summary="Recepción real de mensajes de WhatsApp (Meta Cloud API)",
    description=(
        "Exige `X-Hub-Signature-256` (HMAC-SHA256 del cuerpo con `WHATSAPP_APP_SECRET`). "
        "El cuerpo es el de Meta; **validar contra la documentación vigente de Meta**."
    ),
    **_route("receiveWhatsAppWebhook", priority="P1", errors=(ErrorCode.FIRMA_INVALIDA,)),
)
async def receive_whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256")],
) -> WebhookAck:
    _spec_only()


@whatsapp.post(
    "/simulate",
    summary="Simulación de WhatsApp: mismo cuerpo que el webhook real, marcado SIMULATED",
    **_route("simulateWhatsApp", priority="P1"),
)
async def simulate_whatsapp(payload: WhatsAppWebhookPayload) -> WhatsAppSimulationResult:
    _spec_only()


@pbx.post(
    "/turns",
    summary="Turno de llamada (transcripción o referencia de audio)",
    description="`message_id` interno = `<call_id>:<turn_index>`.",
    **_route("postPbxTurn", priority="P1", errors=(ErrorCode.NO_CONFIGURADO,)),
)
async def post_pbx_turn(body: PbxTurnRequest) -> PbxTurnResponse:
    _spec_only()


@pbx.post(
    "/simulate",
    summary="Atajo de simulación de PBX (transcripción + call_id)",
    **_route("simulatePbx", priority="P1"),
)
async def simulate_pbx(body: PbxSimulateRequest) -> PbxTurnResponse:
    _spec_only()


@handoffs.get(
    "/{handoff_id}",
    summary="Detalle y payload de un handoff",
    description="Contiene historial de la conversación: requiere token (DEC-C13).",
    **_route(
        "getHandoff", priority="P0", errors=(ErrorCode.NO_AUTORIZADO, ErrorCode.NO_ENCONTRADO)
    ),
)
async def get_handoff(handoff_id: UUID, token: AdminTokenDep) -> HandoffPayload:
    _spec_only()


@handoffs.patch(
    "/{handoff_id}",
    summary="Asigna o cierra un handoff (bandeja de asesor)",
    description="PENDING → ASSIGNED → CLOSED; emite `handoff_status` por WebSocket.",
    **_route(
        "updateHandoffStatus",
        priority="P1",
        errors=(ErrorCode.NO_AUTORIZADO, ErrorCode.NO_ENCONTRADO),
    ),
)
async def update_handoff_status(
    handoff_id: UUID, body: HandoffStatusUpdate, token: AdminTokenDep
) -> HandoffPayload:
    _spec_only()


@handoffs.get(
    "",
    summary="Bandeja mínima de asesor",
    **_route("listHandoffs", priority="P1", errors=(ErrorCode.NO_AUTORIZADO,)),
)
async def list_handoffs(
    token: AdminTokenDep, status: Annotated[HandoffStatus | None, Query()] = None
) -> HandoffListResponse:
    _spec_only()


_BATCH_AUTH_ERRORS = (ErrorCode.NO_AUTENTICADO, ErrorCode.NO_AUTORIZADO)


@shipment_batches.post(
    "",
    status_code=201,
    summary="Crea un lote de envíos (procesamiento parcial e idempotente)",
    description=(
        "Scope `batches:create`. Cada envío se procesa en su propia transacción: los válidos "
        "obtienen guía interna, los inválidos se rechazan con errores por ítem. Misma "
        "`referencia_lote` y mismo contenido → 200 con el lote existente; contenido distinto "
        "→ 409. 202 si el mismo lote se está procesando en otra solicitud."
    ),
    responses={
        200: {"model": ShipmentBatchResponse, "description": "Repetición idempotente"},
        202: {"model": ShipmentBatchResponse, "description": "El lote sigue en proceso"},
    },
    **_route(
        "createShipmentBatch",
        priority="P0",
        errors=(
            *_BATCH_AUTH_ERRORS,
            ErrorCode.IDEMPOTENCIA_CONFLICTO,
            ErrorCode.LOTE_DEMASIADO_GRANDE,
            ErrorCode.LIMITE_DE_TASA,
        ),
    ),
)
async def create_shipment_batch(
    body: ShipmentBatchRequest,
    credentials: ApiKeyDep,
    idempotency_key: Annotated[
        str | None, Header(alias=IDEMPOTENCY_KEY_HEADER, max_length=64)
    ] = None,
) -> ShipmentBatchResponse:
    _spec_only()


@shipment_batches.get(
    "/{batch_id}",
    summary="Estado y contadores de un lote (solo el cliente propietario)",
    description="Scope `batches:read`. Un lote de otro cliente responde 404.",
    **_route(
        "getShipmentBatch", priority="P0", errors=(*_BATCH_AUTH_ERRORS, ErrorCode.NO_ENCONTRADO)
    ),
)
async def get_shipment_batch(batch_id: UUID, credentials: ApiKeyDep) -> ShipmentBatchSummary:
    _spec_only()


@shipment_batches.get(
    "/{batch_id}/items",
    summary="Resultados por ítem de un lote, paginados por línea",
    description="Scope `batches:read`. Orden estable por `linea`; `cursor` es opaco.",
    **_route(
        "listShipmentBatchItems",
        priority="P0",
        errors=(*_BATCH_AUTH_ERRORS, ErrorCode.NO_ENCONTRADO),
    ),
)
async def list_shipment_batch_items(
    batch_id: UUID,
    credentials: ApiKeyDep,
    status: Annotated[BatchItemStatus | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=ITEMS_PAGE_MAX)] = 50,
) -> ShipmentBatchItemsPage:
    _spec_only()


@ops.get(
    "/metrics/channels",
    summary="Conversaciones y mensajes por canal",
    **_route("getChannelMetrics", priority="P1"),
)
async def get_channel_metrics(
    desde: Annotated[AwareDatetime | None, Query()] = None,
    hasta: Annotated[AwareDatetime | None, Query()] = None,
) -> ChannelMetricsResponse:
    _spec_only()


@ops.post(
    "/admin/rag/reindex",
    summary="Reindexación del corpus RAG",
    **_route(
        "reindexRag",
        priority="P0",
        errors=(ErrorCode.NO_AUTORIZADO, ErrorCode.LLM_NO_DISPONIBLE),
    ),
)
async def reindex_rag(
    token: AdminTokenDep, body: Annotated[ReindexRequest | None, Body()] = None
) -> ReindexResponse:
    _spec_only()


root = APIRouter(tags=["operación"])


@root.get("/health", summary="Estado del proceso", **_route("getHealth", priority="P0"))
async def get_health() -> HealthResponse:
    _spec_only()


@root.get(
    "/ready",
    summary="Dependencias listas (BD, Ollama, índice)",
    responses={
        503: {"model": ReadinessResponse, "description": "Alguna dependencia no está lista"}
    },
    **_route("getReadiness", priority="P0"),
)
async def get_readiness() -> ReadinessResponse:
    _spec_only()


def build_contract_app() -> FastAPI:
    app = FastAPI(title=TITLE, version=CONTRACT_VERSION, description=DESCRIPTION)
    api = APIRouter(prefix=API_PREFIX)
    routers = (tracking, quotation, pqrs, attachments, chat, whatsapp, pbx, handoffs)
    for router in (*routers, shipment_batches, ops):
        api.include_router(router)
    app.include_router(api)
    app.include_router(root)
    return app
