"""Ports of the system (``typing.Protocol``) and the small models they exchange.

Every module programs against these ports, and ``app.contracts.fakes`` gives a
deterministic implementation of each one, so nobody waits for anybody.
Asynchronous where there is I/O; synchronous for pure calculations
(quotation, escalation rules) and the clock.

Errors travel as ``ContractError`` with a contract ``ErrorCode``; "not found"
lookups return ``None`` and the HTTP facade decides the error.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol, Self

from pydantic import UUID4, Field, model_validator

from app.contracts.common import (
    LOCALE,
    BogotaDatetime,
    ChannelType,
    ContractError,
    ContractModel,
    DeliveryMode,
    ErrorCode,
    Score,
)
from app.contracts.conversation import ConversationState, Intent
from app.contracts.escalation import (
    EscalationDecision,
    EscalationInput,
    HandoffPayload,
    HandoffStatus,
    HandoffSummary,
)
from app.contracts.inbound import InboundMessage
from app.contracts.pqrs import PqrsTicketRequest, PqrsTicketResponse
from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.rag import ChunkMetadata, RagAnswer
from app.contracts.reply import ConversationReply, ServiceName
from app.contracts.tms import TmsShipment
from app.contracts.tracking import TrackingResponse

# --- time ----------------------------------------------------------------------


class Clock(Protocol):
    """Source of the current time (rule 10): never call ``datetime.now()`` directly."""

    def now(self) -> datetime:
        """Timezone-aware current instant."""
        ...


# --- conversation and channels -------------------------------------------------


class ConversationService(Protocol):
    """Main port of the core; every adapter calls it."""

    async def handle(self, msg: InboundMessage) -> ConversationReply: ...


class ChannelOutbound(ContractModel):
    """What ``render_outbound`` produces: ready for ``deliver``, no network involved.

    The recipient is not included: the adapter resolves it from the value it
    holds in memory, so plain user ids never travel through the core.
    """

    channel: ChannelType
    session_id: str
    reply_id: str
    delivery_mode: DeliveryMode
    parts: list[dict[str, Any]] = Field(
        min_length=1, description="Cuerpos con la forma del proveedor, en orden de envío."
    )
    plain_text: str = Field(description="Versión en texto plano (PBX: texto hablable).")


class DeliveryResult(ContractModel):
    delivery_mode: DeliveryMode
    status: Literal["SENT", "QUEUED", "FAILED", "NOT_ATTEMPTED"]
    provider_message_id: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _failures_explain_themselves(self) -> Self:
        if self.status == "FAILED" and not self.error_code:
            raise ValueError("a FAILED delivery must carry error_code")
        return self


class ChannelAdapter(Protocol):
    channel: ChannelType

    def verify_request(self, raw_body: bytes, headers: Mapping[str, str]) -> None:
        """Raise ``ContractError(FIRMA_INVALIDA)`` when the request is not authentic."""

    def parse_inbound(self, raw: Any, headers: Mapping[str, str]) -> list[InboundMessage]: ...

    def render_outbound(self, reply: ConversationReply) -> ChannelOutbound:
        """Pure: no network, no clock, no randomness beyond the reply itself."""
        ...

    async def deliver(self, outbound: ChannelOutbound) -> DeliveryResult: ...


class Transcript(ContractModel):
    text: str
    confidence: Score | None = None
    language: str = LOCALE
    provider: str


class AudioRef(ContractModel):
    storage_ref: str
    mime_type: str
    duration_ms: int | None = Field(default=None, ge=0)


class AsrPort(Protocol):
    async def transcribe(self, audio_ref: str, language: str = LOCALE) -> Transcript: ...


class TtsPort(Protocol):
    async def synthesize(self, text: str, language: str = LOCALE) -> AudioRef: ...


class NullAsr:
    """Default ASR (mandatory by contract): real engines are P2 and need credentials."""

    async def transcribe(self, audio_ref: str, language: str = LOCALE) -> Transcript:
        raise ContractError(
            ErrorCode.NO_CONFIGURADO, "El reconocimiento de voz no está configurado."
        )


class NullTts:
    """Default TTS (mandatory by contract): real engines are P2 and need credentials."""

    async def synthesize(self, text: str, language: str = LOCALE) -> AudioRef:
        raise ContractError(ErrorCode.NO_CONFIGURADO, "La síntesis de voz no está configurada.")


# --- business services ---------------------------------------------------------


class TmsPort(Protocol):
    """TMS anti-corruption layer (P04). ``numero_guia`` is already canonical."""

    async def get_shipment(self, numero_guia: str) -> TmsShipment | None:
        """Full internal record, PII included: only for rules, never for a client."""
        ...

    async def get_tracking_view(self, numero_guia: str) -> TrackingResponse | None:
        """Client-safe view (DEC-S1)."""
        ...


class QuotationPort(Protocol):
    """Deterministic quotation (P03). Pure: the tariff matrix is loaded at startup.

    Raises ``ContractError`` with ``CIUDAD_NO_RECONOCIDA``, ``RUTA_SIN_TARIFA`` or
    ``SERVICIO_NO_DISPONIBLE``.
    """

    def quote(self, request: CotizacionRequest) -> CotizacionResponse: ...


class RagPort(Protocol):
    """Retrieval + grounded generation (P06).

    Without enough evidence it returns ``grounded=False`` (never an error).
    Raises ``ContractError(LLM_NO_DISPONIBLE)`` when generation is needed and the
    model is down.
    """

    async def answer(
        self, question: str, *, filters: Mapping[str, str] | None = None
    ) -> RagAnswer: ...


class SpecialCargoPort(Protocol):
    """Special cargo and prohibited items (P06), always answered from the corpus with citations."""

    async def check(self, question: str) -> RagAnswer: ...


class EscalationPort(Protocol):
    """Pure rules engine (P07): no I/O, no LLM."""

    def evaluate(self, data: EscalationInput) -> EscalationDecision: ...


class HandoffPort(Protocol):
    """Human handoff (P07)."""

    async def create(
        self, state: ConversationState, decision: EscalationDecision
    ) -> HandoffPayload: ...

    async def get(self, handoff_id: UUID4) -> HandoffPayload | None: ...

    async def list_by_status(
        self, status: HandoffStatus | None = None
    ) -> Sequence[HandoffSummary]: ...

    async def update_status(
        self, handoff_id: UUID4, status: HandoffStatus
    ) -> HandoffPayload | None: ...


class PqrsPort(Protocol):
    """PQRS tickets (P09). Consults escalation itself (CRIT-003) and fills ``escalamiento``."""

    async def create_ticket(self, request: PqrsTicketRequest) -> PqrsTicketResponse: ...


class AccountProfile(ContractModel):
    """What CRIT-004 needs about the shipper's account (fixture rotulado SIMULADO)."""

    tipo_cuenta: str = Field(examples=["ESTANDAR", "CORPORATIVO_GOLD"])
    envios_mensuales_cuenta: int = Field(ge=0)


class AccountDirectoryPort(Protocol):
    async def get_account(self, nit: str) -> AccountProfile | None:
        """``nit`` comes from the internal TMS record; it is never shown to a client."""
        ...


# --- AI infrastructure ---------------------------------------------------------


class LlmMessage(ContractModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LlmCompletion(ContractModel):
    text: str
    model: str


class LlmPort(Protocol):
    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LlmCompletion:
        """Raise ``ContractError(LLM_NO_DISPONIBLE)`` when the model is unreachable."""
        ...


class EmbeddingPort(Protocol):
    @property
    def dimension(self) -> int:
        """Must equal ``EMBED_DIM``."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Raises ``ContractError(LLM_NO_DISPONIBLE)``."""
        ...


class IndexedChunk(ContractModel):
    chunk_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: ChunkMetadata
    embedding: list[float] = Field(min_length=1)


class ScoredChunk(ContractModel):
    chunk_id: str
    content: str
    metadata: ChunkMetadata
    score: Score = Field(description="Similitud coseno recortada a [0, 1].")


class VectorStorePort(Protocol):
    async def upsert(self, chunks: Sequence[IndexedChunk]) -> int:
        """Return how many were inserted; chunks whose content_hash is stored are skipped."""
        ...

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        filters: Mapping[str, str] | None = None,
    ) -> list[ScoredChunk]:
        """Best ``top_k`` chunks, highest score first; ``filters`` match metadata fields."""
        ...


# --- audit -----------------------------------------------------------------------


class AuditEvent(ContractModel):
    """One row of ``audit_events`` (§8). Never carries PII or message text."""

    id: UUID4
    trace_id: str = Field(min_length=1)
    session_id: UUID4 | None
    channel: ChannelType | None
    intent: Intent | None
    services_called: list[ServiceName]
    escalation: EscalationDecision | None
    evidence_ids: list[str]
    llm_used: bool
    latency_ms: int = Field(ge=0)
    created_at: BogotaDatetime


class AuditPort(Protocol):
    async def record(self, event: AuditEvent) -> None: ...
