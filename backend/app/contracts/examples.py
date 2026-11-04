"""Golden examples in ``contracts/examples/``.

Each file is validated against its Pydantic model and against the published
OpenAPI schema. HTTP examples are embedded in ``openapi.yaml`` and listed in
``contracts/mocks/manifest.json`` with a ``match`` hint, so the frontend mocks
(MSW) and the E2E tests replay the demo scenarios (S1-S18 of the master plan)
with exactly the same payloads.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

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
from app.contracts.common import ErrorCode, ErrorEnvelope
from app.contracts.conversation import ConversationState
from app.contracts.escalation import (
    EscalationDecision,
    EscalationInput,
    HandoffPayload,
    HandoffStatusUpdate,
)
from app.contracts.inbound import InboundMessage
from app.contracts.ops import (
    ChannelMetricsResponse,
    HandoffListResponse,
    HealthResponse,
    ReadinessResponse,
    ReindexRequest,
    ReindexResponse,
)
from app.contracts.ports import AuditEvent
from app.contracts.pqrs import PqrsTicketRequest, PqrsTicketResponse
from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.rag import ChunkMetadata, RagAnswer
from app.contracts.tracking import TrackingResponse
from app.contracts.ws import WsHandoffStatusEvent

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples"


@dataclass(frozen=True)
class Use:
    """Where an example appears in the HTTP surface."""

    operation_id: str
    target: Literal["request", "response"]
    status: int = 200
    scenario: str | None = None
    # Hint for mocks: {"path": {...}}, {"body": {...}} (subset) or {"text_contains": "..."}.
    match: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Example:
    name: str
    summary: str
    model: type[BaseModel] | None  # None: provider payload or plain text, no contract model
    uses: tuple[Use, ...] = ()
    scenario: str | None = None  # for internal (non-HTTP) examples
    # Values only the real service can produce (heuristic scores, counts): the
    # shape is contractual, these values are not. Listed in the mocks manifest.
    pending: str | None = None
    error_code: ErrorCode | None = None

    @property
    def path(self) -> Path:
        return EXAMPLES_DIR / f"{self.name}.json"

    @property
    def is_request(self) -> bool:
        return any(use.target == "request" for use in self.uses)

    @property
    def scenarios(self) -> set[str]:
        found = {use.scenario for use in self.uses if use.scenario}
        return found | ({self.scenario} if self.scenario else set())

    def load(self) -> Any:
        return json.loads(self.path.read_text(encoding="utf-8"))


def req(operation_id: str, scenario: str | None = None) -> Use:
    return Use(operation_id, "request", scenario=scenario)


def res(
    operation_id: str,
    status: int = 200,
    scenario: str | None = None,
    **match: Any,
) -> Use:
    return Use(operation_id, "response", status, scenario, match or None)


def _guia(numero: str) -> dict[str, str]:
    return {"guia": numero}


EXAMPLES: tuple[Example, ...] = (
    # --- tracking -------------------------------------------------------
    Example(
        "tracking.en-transito",
        "Cadena de frío en tránsito (TLX-984021-CO)",
        TrackingResponse,
        (res("getTracking", scenario="S1", path=_guia("TLX-984021-CO")),),
    ),
    Example(
        "tracking.en-reparto",
        "En reparto, sin datos del conductor (TLX-551904-CO)",
        TrackingResponse,
        (res("getTracking", scenario="S3", path=_guia("TLX-551904-CO")),),
    ),
    Example(
        "tracking.retenido",
        "Mercancía peligrosa retenida en control (TLX-310482-CO)",
        TrackingResponse,
        (res("getTracking", scenario="S4", path=_guia("TLX-310482-CO")),),
    ),
    Example(
        "tracking.entregado",
        "Entregado con prueba de entrega por iniciales (TLX-752190-CO)",
        TrackingResponse,
        (res("getTracking", scenario="S5", path=_guia("TLX-752190-CO")),),
    ),
    Example(
        "tracking.en-bodega-origen",
        "En bodega de origen, despacho semanal ZOMAC (TLX-118493-CO)",
        TrackingResponse,
        (res("getTracking", path=_guia("TLX-118493-CO")),),
    ),
    Example(
        "error.guia-no-encontrada",
        "Guía inexistente",
        ErrorEnvelope,
        (res("getTracking", 404, "S6", path=_guia("TLX-000000-CO")),),
        error_code=ErrorCode.GUIA_NO_ENCONTRADA,
    ),
    # --- quotation ------------------------------------------------------
    Example("cotizar.request", "Bogotá → Medellín", CotizacionRequest, (req("createQuote", "S7"),)),
    Example(
        "cotizar.response",
        "Bogotá → Medellín: total 190.975",
        CotizacionResponse,
        (
            res(
                "createQuote",
                scenario="S7",
                body={"destino": "Medellín", "requiere_cadena_frio": False},
            ),
        ),
    ),
    Example(
        "cotizar.cadena-frio.request",
        "Bogotá → Medellín con cadena de frío",
        CotizacionRequest,
        (req("createQuote", "S7"),),
    ),
    Example(
        "cotizar.cadena-frio.response",
        "Recargo de frío 25 % sobre el flete base: total 207.469",
        CotizacionResponse,
        (
            res(
                "createQuote",
                scenario="S7",
                body={"destino": "Medellín", "requiere_cadena_frio": True},
            ),
        ),
    ),
    Example(
        "cotizar.flete-minimo.request",
        "Bogotá → Pereira, paquete pequeño",
        CotizacionRequest,
        (req("createQuote", "S9"),),
    ),
    Example(
        "cotizar.flete-minimo.response",
        "Aplica el flete mínimo de la ruta: total 22.500",
        CotizacionResponse,
        (res("createQuote", scenario="S9", body={"destino": "Pereira"}),),
    ),
    Example(
        "cotizar.ruta-sin-tarifa.request",
        "Barranquilla → Bucaramanga (sin fila en la matriz)",
        CotizacionRequest,
        (req("createQuote", "S8"),),
    ),
    Example(
        "error.ruta-sin-tarifa",
        "Ruta sin tarifa: no se inventa un precio",
        ErrorEnvelope,
        (
            res(
                "createQuote",
                422,
                "S8",
                body={"origen": "Barranquilla", "destino": "Bucaramanga"},
            ),
        ),
        error_code=ErrorCode.RUTA_SIN_TARIFA,
    ),
    # --- pqrs -----------------------------------------------------------
    Example(
        "pqrs.siniestro.request",
        "Avería no aparente (TLX-752190-CO)",
        PqrsTicketRequest,
        (req("createPqrsTicket", "S14"),),
    ),
    Example(
        "pqrs.siniestro.response",
        "Escala a Jurídico/PQRS: valor declarado > 5.000.000 (CRIT-003)",
        PqrsTicketResponse,
        (res("createPqrsTicket", 201, "S14", body={"tipo": "SINIESTRO"}),),
    ),
    Example(
        "pqrs.peticion.request",
        "Petición de copia de prueba de entrega",
        PqrsTicketRequest,
        (req("createPqrsTicket"),),
    ),
    Example(
        "pqrs.peticion.response",
        "Petición radicada",
        PqrsTicketResponse,
        (res("createPqrsTicket", 201, body={"tipo": "PETICION"}),),
    ),
    Example(
        "attachment.upload.response",
        "Factura en PDF",
        AttachmentUploadResponse,
        (res("uploadAttachment", 201),),
    ),
    # --- web chat -------------------------------------------------------
    Example(
        "chat.tracking.request",
        "¿Dónde está mi guía?",
        ChatMessageRequest,
        (req("postChatMessage", "S1"),),
    ),
    Example(
        "chat.tracking.response",
        "Tarjeta de rastreo, sin escalamiento",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S1", text_contains="TLX-984021-CO"),),
    ),
    Example(
        "chat.retenida.request",
        "Guía con mercancía peligrosa",
        ChatMessageRequest,
        (req("postChatMessage", "S4"),),
    ),
    Example(
        "chat.retenida.response",
        "Rastreo + handoff URGENTE a Operaciones (CRIT-005)",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S4", text_contains="TLX-310482-CO"),),
    ),
    Example(
        "chat.guia-no-encontrada.request",
        "Guía inexistente",
        ChatMessageRequest,
        (req("postChatMessage", "S6"),),
    ),
    Example(
        "chat.guia-no-encontrada.response",
        "No inventa estado y ofrece asesor",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S6", text_contains="TLX-000000-CO"),),
    ),
    Example(
        "chat.cotizacion.request",
        "Último dato de la cotización iterativa",
        ChatMessageRequest,
        (req("postChatMessage", "S7"),),
    ),
    Example(
        "chat.cotizacion.response",
        "Resumen de cotización",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S7", text_contains="12.500.000"),),
    ),
    Example(
        "chat.mercancia-peligrosa.request",
        "Consulta de carga especial",
        ChatMessageRequest,
        (req("postChatMessage", "S10"),),
    ),
    Example(
        "chat.mercancia-peligrosa.response",
        "Respuesta RAG con citas, sin escalar",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S10", text_contains="ácido clorhídrico"),),
        pending="confidence_score: lo calcula heuristic_v1 en el servicio real",
    ),
    Example(
        "chat.fuera-de-corpus.request",
        "Pregunta fuera del corpus",
        ChatMessageRequest,
        (req("postChatMessage", "S12"),),
    ),
    Example(
        "chat.fuera-de-corpus.response",
        "Sin evidencia: escala por CRIT-001",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S12", text_contains="Hong Kong"),),
        pending="confidence_score: lo calcula heuristic_v1 en el servicio real",
    ),
    Example(
        "chat.amenaza-legal.request",
        "Amenaza de demanda",
        ChatMessageRequest,
        (req("postChatMessage", "S13"),),
    ),
    Example(
        "chat.amenaza-legal.response",
        "Escala a Supervisor por CRIT-002",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S13", text_contains="demandar"),),
    ),
    Example(
        "chat.en-reparto.request",
        "Guía en reparto",
        ChatMessageRequest,
        (req("postChatMessage", "S3"),),
    ),
    Example(
        "chat.en-reparto.response",
        "En reparto, sin datos del conductor",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S3", text_contains="TLX-551904-CO"),),
    ),
    Example(
        "chat.entregado.request",
        "Guía entregada",
        ChatMessageRequest,
        (req("postChatMessage", "S5"),),
    ),
    Example(
        "chat.entregado.response",
        "Entregado; recibió R. B.",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S5", text_contains="TLX-752190-CO"),),
    ),
    Example(
        "chat.ruta-sin-tarifa.request",
        "Cotización sin ruta en la matriz",
        ChatMessageRequest,
        (req("postChatMessage", "S8"),),
    ),
    Example(
        "chat.ruta-sin-tarifa.response",
        "No inventa tarifa y ofrece asesor",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S8", text_contains="Barranquilla a Bucaramanga"),),
    ),
    Example(
        "chat.flete-minimo.request",
        "Cotización completa en un mensaje",
        ChatMessageRequest,
        (req("postChatMessage", "S9"),),
    ),
    Example(
        "chat.flete-minimo.response",
        "Resumen con flete mínimo aplicado",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S9", text_contains="Pereira"),),
    ),
    Example(
        "chat.caja-rota.request",
        "Novedad tras la entrega",
        ChatMessageRequest,
        (req("postChatMessage", "S14"),),
    ),
    Example(
        "chat.caja-rota.response",
        "Checklist, ticket escalado y handoff a Jurídico/PQRS",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S14", text_contains="caja rota"),),
    ),
    Example(
        "chat.fuera-de-plazo.request",
        "Reclamo un mes después de la entrega",
        ChatMessageRequest,
        (req("postChatMessage", "S15"),),
    ),
    Example(
        "chat.fuera-de-plazo.response",
        "Plazo vencido según el protocolo, con citas",
        ChatMessageResponse,
        (res("postChatMessage", scenario="S15", text_contains="hace un mes"),),
        pending="texto generado y confidence_score: los produce el RAG real",
    ),
    Example(
        "session.history",
        "Historial de la sesión de S1",
        SessionHistory,
        (res("getSessionHistory", scenario="S1"),),
    ),
    # --- whatsapp -------------------------------------------------------
    Example(
        "whatsapp.verify.response",
        "Eco de hub.challenge",
        None,
        (res("verifyWhatsAppWebhook"),),
    ),
    Example(
        "whatsapp.webhook.request",
        "Mensaje de texto entrante (estructura Meta Cloud API)",
        WhatsAppWebhookPayload,
        (req("receiveWhatsAppWebhook", "S2"), req("simulateWhatsApp", "S2")),
    ),
    Example(
        "whatsapp.webhook.prohibido.request",
        "Pregunta por un artículo prohibido",
        WhatsAppWebhookPayload,
        (req("simulateWhatsApp", "S11"),),
    ),
    Example("whatsapp.webhook.ack", "Acuse rápido", WebhookAck, (res("receiveWhatsAppWebhook"),)),
    Example(
        "whatsapp.simulate.response",
        "Mismo resultado que S1, en texto plano y SIMULATED",
        WhatsAppSimulationResult,
        (res("simulateWhatsApp", scenario="S2", text_contains="TLX-984021-CO"),),
    ),
    Example(
        "whatsapp.simulate.prohibido.response",
        "Animales vivos: prohibido, con cita del manual §5",
        WhatsAppSimulationResult,
        (res("simulateWhatsApp", scenario="S11", text_contains="perro"),),
        pending="texto generado y confidence_score: los produce el RAG real",
    ),
    # --- pbx ------------------------------------------------------------
    Example(
        "pbx.turn.request",
        "Turno con guía dictada",
        PbxTurnRequest,
        (req("postPbxTurn", "S16"),),
    ),
    Example(
        "pbx.simulate.request",
        "Atajo de simulación",
        PbxSimulateRequest,
        (req("simulatePbx", "S16"),),
    ),
    Example(
        "pbx.turn.response",
        "Respuesta hablable con dígitos deletreados",
        PbxTurnResponse,
        (res("postPbxTurn", scenario="S16"), res("simulatePbx", scenario="S16")),
    ),
    # --- handoff and operations -----------------------------------------
    Example(
        "handoff.operaciones",
        "Mercancía peligrosa retenida → Operaciones (URGENTE)",
        HandoffPayload,
        (res("getHandoff", scenario="S4"),),
    ),
    Example(
        "handoff.status-update.request",
        "Asignar a un asesor",
        HandoffStatusUpdate,
        (req("updateHandoffStatus"),),
    ),
    Example(
        "handoff.asignado",
        "Handoff de S4 asignado",
        HandoffPayload,
        (res("updateHandoffStatus", scenario="S4"),),
    ),
    Example("handoff.list", "Bandeja de asesor", HandoffListResponse, (res("listHandoffs"),)),
    Example(
        "metrics.channels",
        "Conteos por canal (ilustrativos)",
        ChannelMetricsResponse,
        (res("getChannelMetrics", scenario="S18"),),
        pending="conteos: salen de la base de datos real",
    ),
    Example("reindex.request", "Reindexar todo", ReindexRequest, (req("reindexRag"),)),
    Example(
        "reindex.response",
        "Reindexación completa",
        ReindexResponse,
        (res("reindexRag"),),
        pending="conteos y duración: los mide la ingesta real",
    ),
    Example("health.ok", "Proceso vivo", HealthResponse, (res("getHealth"),)),
    Example("ready.ok", "Todo listo", ReadinessResponse, (res("getReadiness"),)),
    Example(
        "ready.sin-ollama",
        "Ollama caído: no listo",
        ReadinessResponse,
        (res("getReadiness", 503, "S17"),),
    ),
    # --- internal contracts (no HTTP route) -----------------------------
    Example("inbound.web-chat", "Desde Web Chat", InboundMessage, scenario="S1"),
    Example("inbound.whatsapp", "Desde WhatsApp (simulado)", InboundMessage, scenario="S2"),
    Example("inbound.pbx", "Desde PBX (transcripción ASR)", InboundMessage, scenario="S16"),
    Example("escalation.crit-005", "CRIT-005 primario sobre CRIT-002", EscalationDecision),
    Example("conversation-state.s04", "Estado tras escalar S4", ConversationState, scenario="S4"),
    Example("escalation-input.s04", "Entrada del motor en S4", EscalationInput, scenario="S4"),
    Example("chunk-metadata.clase-8", "Metadatos del chunk de Clase 8", ChunkMetadata),
    Example(
        "audit-event.s01",
        "Fila de auditoría de S1",
        AuditEvent,
        scenario="S1",
        pending="latency_ms: lo mide el servicio real",
    ),
    Example(
        "rag.grounded",
        "Con evidencia y citas",
        RagAnswer,
        scenario="S10",
        pending="confidence y scores: los calcula el retrieval real",
    ),
    Example(
        "rag.ungrounded",
        "Sin evidencia: no se genera texto",
        RagAnswer,
        scenario="S12",
        pending="confidence y scores: los calcula el retrieval real",
    ),
    Example("ws.handoff-status", "Estado del handoff de S4", WsHandoffStatusEvent, scenario="S4"),
)

# One example per error code; the two scenario errors above count for theirs.
_SCENARIO_ERRORS = {example.error_code for example in EXAMPLES if example.error_code}
EXAMPLES += tuple(
    Example(
        f"error.{code.value.lower().replace('_', '-')}",
        f"Error {code.value}",
        ErrorEnvelope,
        error_code=code,
    )
    for code in ErrorCode
    if code not in _SCENARIO_ERRORS
)
ERROR_EXAMPLES: dict[ErrorCode, Example] = {
    example.error_code: example for example in EXAMPLES if example.error_code
}
