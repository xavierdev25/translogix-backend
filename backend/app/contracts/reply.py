"""``ConversationReply``: the channel-agnostic answer of the core.

It never contains HTML, channel-specific Markdown or provider payloads; each
adapter's ``render_outbound`` turns the blocks into its own format.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import UUID4, Field, model_validator

from app.contracts.common import (
    BogotaDatetime,
    ChannelType,
    ClientSafeText,
    ContractModel,
    Cop,
    DeliveryMode,
    NumeroGuia,
    Score,
)
from app.contracts.conversation import ConversationStatus, Intent
from app.contracts.escalation import ColaDestino, EscalationDecision, Prioridad
from app.contracts.pqrs import ChecklistItem, DocumentoCodigo, EstadoTicket
from app.contracts.rag import DocCode
from app.contracts.tracking import EstadoVista, EventoVista, TrackingFlags

MAX_QUICK_REPLIES = 3


class TextBlock(ContractModel):
    type: Literal["TEXT"] = "TEXT"
    text: str = Field(min_length=1)


class QuickReplyOption(ContractModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)


class QuickRepliesBlock(ContractModel):
    type: Literal["QUICK_REPLIES"] = "QUICK_REPLIES"
    options: list[QuickReplyOption] = Field(min_length=1, max_length=MAX_QUICK_REPLIES)


class TrackingCardBlock(ContractModel):
    type: Literal["TRACKING_CARD"] = "TRACKING_CARD"
    numero_guia: NumeroGuia
    estado: EstadoVista
    eta: BogotaDatetime | None
    ubicacion_referencia: ClientSafeText | None
    eventos: list[EventoVista]
    flags: TrackingFlags


class QuoteSummaryBlock(ContractModel):
    type: Literal["QUOTE_SUMMARY"] = "QUOTE_SUMMARY"
    cotizacion_id: UUID4
    codigo_trayecto: str
    origen: str
    destino: str
    peso_facturable_kg: float = Field(gt=0)
    flete_total_cop: Cop
    tiempo_entrega_horas: int = Field(gt=0)
    formula_version: str
    advertencias: list[str]


class CitationItem(ContractModel):
    doc_code: DocCode
    doc_title: str
    section_path: str
    chunk_id: str
    excerpt: str


class CitationsBlock(ContractModel):
    type: Literal["CITATIONS"] = "CITATIONS"
    items: list[CitationItem] = Field(min_length=1)


class ChecklistItemDetallado(ChecklistItem):
    descripcion: str


class RequestAttachmentsBlock(ContractModel):
    type: Literal["REQUEST_ATTACHMENTS"] = "REQUEST_ATTACHMENTS"
    checklist: list[ChecklistItemDetallado] = Field(min_length=1)


class PqrsTicketBlock(ContractModel):
    type: Literal["PQRS_TICKET"] = "PQRS_TICKET"
    ticket_id: UUID4
    estado: EstadoTicket
    faltantes: list[DocumentoCodigo]


class HandoffNoticeBlock(ContractModel):
    type: Literal["HANDOFF_NOTICE"] = "HANDOFF_NOTICE"
    handoff_id: UUID4
    prioridad: Prioridad
    cola: ColaDestino
    mensaje_cliente: str
    tiempo_espera_estimado_segundos: int = Field(ge=0)


Block = Annotated[
    TextBlock
    | QuickRepliesBlock
    | TrackingCardBlock
    | QuoteSummaryBlock
    | CitationsBlock
    | RequestAttachmentsBlock
    | PqrsTicketBlock
    | HandoffNoticeBlock,
    Field(discriminator="type"),
]


class ServiceName(StrEnum):
    TMS = "tms"
    QUOTATION = "quotation"
    RAG = "rag"
    ESCALATION = "escalation"
    PQRS = "pqrs"


class ReplyMeta(ContractModel):
    trace_id: str = Field(min_length=1)
    intent: Intent
    confidence_score: Score | None = Field(description="Solo RAG; null en tracking/cotización.")
    confidence_method: Literal["heuristic_v1"] | None
    escalation: EscalationDecision | None
    services_called: list[ServiceName]
    evidence_ids: list[str] = Field(description="chunk_id / guía / cotizacion_id usados.")
    llm_used: bool
    delivery_mode: DeliveryMode

    @model_validator(mode="after")
    def _confidence_only_with_rag(self) -> Self:
        if (self.confidence_score is None) != (self.confidence_method is None):
            raise ValueError("confidence_score and confidence_method go together")
        if self.confidence_score is not None and ServiceName.RAG not in self.services_called:
            raise ValueError("a confidence score only exists when RAG was called")
        return self


class ConversationReply(ContractModel):
    session_id: UUID4
    in_reply_to: str = Field(description="message_id del InboundMessage.")
    reply_id: UUID4
    channel: ChannelType
    status: ConversationStatus
    blocks: list[Block] = Field(min_length=1)
    meta: ReplyMeta
    created_at: BogotaDatetime

    @model_validator(mode="after")
    def _handoff_means_escalated(self) -> Self:
        has_notice = any(isinstance(block, HandoffNoticeBlock) for block in self.blocks)
        if has_notice and self.status is not ConversationStatus.ESCALATED:
            raise ValueError("a HANDOFF_NOTICE block requires status ESCALATED")
        return self
