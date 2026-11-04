"""Deterministic fakes of every port, built on the golden examples.

They let P08, P10-P12 and P15 work before the real services exist. They are
test doubles, not business logic: each one replays the golden payloads (and the
simulated TMS dataset) and labels anything outside them as simulated.

    from app.contracts import fakes
    reply = await fakes.FakeConversationService().handle(inbound_message)
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.contracts.chat import ChatMessageResponse
from app.contracts.common import ContractError, ErrorCode
from app.contracts.conversation import ConversationState, ConversationStatus, Intent
from app.contracts.escalation import (
    DEFAULT_ACTION_ROUTING,
    EscalationDecision,
    EscalationInput,
    HandoffPayload,
    HandoffStatus,
    HandoffSummary,
)
from app.contracts.examples import EXAMPLES, EXAMPLES_DIR
from app.contracts.inbound import InboundMessage
from app.contracts.normalizers import extract_numero_guia
from app.contracts.pii import hash_external_user_id
from app.contracts.ports import (
    AccountProfile,
    AuditEvent,
    IndexedChunk,
    LlmCompletion,
    LlmMessage,
    ScoredChunk,
)
from app.contracts.pqrs import PqrsTicketRequest, PqrsTicketResponse, PqrsTipo
from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.rag import RagAnswer
from app.contracts.reply import ConversationReply
from app.contracts.tms import TmsShipment
from app.contracts.tracking import TrackingResponse

DEMO_NOW = datetime.fromisoformat("2026-11-03T10:00:00-05:00")
TMS_DATASET = EXAMPLES_DIR.parents[1] / "data" / "source" / "dataset_guias_simuladas_tms.json"
FAKE_SALT = "fake-salt-not-a-secret-0001"
_CONFIDENCE_THRESHOLD = 0.75
_CRIT003_LIMIT = 5_000_000


def _golden(name: str) -> Any:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def deterministic_uuid4(seed: str) -> UUID:
    """A valid version-4 UUID derived from ``seed`` (same seed, same id)."""
    raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


class FixedClock:
    def __init__(self, now: datetime = DEMO_NOW) -> None:
        if now.utcoffset() is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeTms:
    """Internal records from the simulated dataset; client views from the golden examples."""

    def __init__(self, dataset: Path = TMS_DATASET) -> None:
        records = json.loads(dataset.read_text(encoding="utf-8"))
        self._shipments = {
            shipment.numero_guia: shipment
            for shipment in (TmsShipment.model_validate(record) for record in records)
        }
        self._views = {
            view.numero_guia: view
            for view in (
                TrackingResponse.model_validate(example.load())
                for example in EXAMPLES
                if example.model is TrackingResponse
            )
        }

    async def get_shipment(self, numero_guia: str) -> TmsShipment | None:
        return self._shipments.get(numero_guia)

    async def get_tracking_view(self, numero_guia: str) -> TrackingResponse | None:
        return self._views.get(numero_guia)


class FakeQuotation:
    """Knows only the golden quotes (S7, S7 con frío, S9); any other route has no tariff."""

    _GOLDEN = (
        ("cotizar.request", "cotizar.response"),
        ("cotizar.cadena-frio.request", "cotizar.cadena-frio.response"),
        ("cotizar.flete-minimo.request", "cotizar.flete-minimo.response"),
    )

    def __init__(self) -> None:
        self._quotes = [
            (
                CotizacionRequest.model_validate(_golden(request)),
                CotizacionResponse.model_validate(_golden(response)),
            )
            for request, response in self._GOLDEN
        ]

    def quote(self, request: CotizacionRequest) -> CotizacionResponse:
        for known, response in self._quotes:
            if known == request:
                return response
        raise ContractError(
            ErrorCode.RUTA_SIN_TARIFA,
            details={"origen": request.origen, "destino": request.destino, "simulado": True},
        )


class FakeRag:
    """Grounded answer for the S10 question; anything else has no evidence (S12)."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self._grounded = RagAnswer.model_validate(_golden("rag.grounded"))
        self._ungrounded = RagAnswer.model_validate(_golden("rag.ungrounded"))

    async def answer(self, question: str, *, filters: Mapping[str, str] | None = None) -> RagAnswer:
        if not self._available:
            raise ContractError(ErrorCode.LLM_NO_DISPONIBLE)
        lowered = question.lower()
        if "clorhídrico" in lowered or "corrosiv" in lowered:
            return self._grounded
        return self._ungrounded


class FakeSpecialCargo:
    def __init__(self, rag: FakeRag | None = None) -> None:
        self._rag = rag or FakeRag()

    async def check(self, question: str) -> RagAnswer:
        return await self._rag.answer(question)


class FakeEscalation:
    """Replays the golden decisions of S4, S12, S13 and S14. Not the rules engine (P07)."""

    def __init__(self) -> None:
        reply = "chat.{}.response"
        self._decisions = {
            name: ChatMessageResponse.model_validate(
                _golden(reply.format(name))
            ).reply.meta.escalation
            for name in ("retenida", "fuera-de-corpus", "amenaza-legal", "tracking")
        }
        pqrs = PqrsTicketResponse.model_validate(_golden("pqrs.siniestro.response"))
        self._claim = pqrs.escalamiento

    def evaluate(self, data: EscalationInput) -> EscalationDecision:
        claimed = max(data.valor_reclamacion_cop or 0, data.valor_declarado_cop or 0)
        text = data.user_text_normalized.lower()
        if data.intent is Intent.PQRS and claimed > _CRIT003_LIMIT:
            decision = self._claim
        elif data.mercancia_peligrosa and (
            data.incident_reported or data.estado_guia == "RETENIDO_NOVEDAD"
        ):
            decision = self._decisions["retenida"]
        elif any(word in text for word in ("demandar", "pesimo servicio", "supertransporte")):
            decision = self._decisions["amenaza-legal"]
        elif data.confidence_score is not None and data.confidence_score < _CONFIDENCE_THRESHOLD:
            decision = self._decisions["fuera-de-corpus"]
        else:
            decision = self._decisions["tracking"]
        if decision is None:
            raise ContractError(ErrorCode.ERROR_INTERNO, "Golden reply without a decision.")
        return decision


class FakeHandoff:
    """In-memory handoffs, routed with ``DEFAULT_ACTION_ROUTING``."""

    def __init__(self, clock: FixedClock | None = None, wait_seconds: int = 45) -> None:
        self._clock = clock or FixedClock()
        self._wait = wait_seconds
        self._handoffs: dict[UUID, HandoffPayload] = {}

    async def create(
        self, state: ConversationState, decision: EscalationDecision
    ) -> HandoffPayload:
        if not decision.escalate or decision.primary is None:
            raise ContractError(ErrorCode.VALIDACION, "Solo se crea un handoff al escalar.")
        route = DEFAULT_ACTION_ROUTING[decision.primary.accion]
        scores = state.confidence_scores
        payload = HandoffPayload(
            handoff_id=deterministic_uuid4(f"handoff:{state.session_id}"),
            session_id=state.session_id,
            canal=state.channel,
            numero_guia_consultada=state.numero_guia_consultada,
            confidence_score_promedio=sum(scores) / len(scores) if scores else None,
            sentimiento_detectado=state.sentiment,
            intentos_previos=state.fallback_count,
            resumen_contexto="Resumen simulado por FakeHandoff.",
            historial=[],
            prioridad=route.prioridad,
            cola_destino=route.cola,
            triggers=decision.triggers,
            tiempo_espera_estimado_segundos=self._wait,
            status=HandoffStatus.PENDING,
            created_at=self._clock.now(),
        )
        self._handoffs[payload.handoff_id] = payload
        return payload

    async def get(self, handoff_id: UUID) -> HandoffPayload | None:
        return self._handoffs.get(handoff_id)

    async def list_by_status(self, status: HandoffStatus | None = None) -> list[HandoffSummary]:
        return [
            HandoffSummary(
                handoff_id=h.handoff_id,
                session_id=h.session_id,
                canal=h.canal,
                prioridad=h.prioridad,
                cola_destino=h.cola_destino,
                numero_guia_consultada=h.numero_guia_consultada,
                status=h.status,
                created_at=h.created_at,
            )
            for h in self._handoffs.values()
            if status is None or h.status is status
        ]

    async def update_status(self, handoff_id: UUID, status: HandoffStatus) -> HandoffPayload | None:
        current = self._handoffs.get(handoff_id)
        if current is None:
            return None
        updated = current.model_copy(update={"status": status})
        self._handoffs[handoff_id] = updated
        return updated


class FakePqrs:
    """Siniestro → golden S14 ticket (escalated); any other type → golden petición."""

    async def create_ticket(self, request: PqrsTicketRequest) -> PqrsTicketResponse:
        name = (
            "pqrs.siniestro.response"
            if request.tipo is PqrsTipo.SINIESTRO
            else "pqrs.peticion.response"
        )
        return PqrsTicketResponse.model_validate(_golden(name))


class FakeAccountDirectory:
    def __init__(self, accounts: Mapping[str, AccountProfile] | None = None) -> None:
        self._accounts = dict(accounts or {})

    async def get_account(self, nit: str) -> AccountProfile | None:
        return self._accounts.get(nit)


class FakeLlm:
    def __init__(
        self, text: str = "Respuesta simulada por FakeLlm.", *, available: bool = True
    ) -> None:
        self._text = text
        self._available = available

    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LlmCompletion:
        if not self._available:
            raise ContractError(ErrorCode.LLM_NO_DISPONIBLE)
        return LlmCompletion(text=self._text, model="fake-llm")


class FakeEmbedding:
    """Hash-based unit vectors: identical texts get identical vectors, offline."""

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        vector = values[: self.dimension]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class FakeVectorStore:
    def __init__(self) -> None:
        self._chunks: dict[str, IndexedChunk] = {}

    async def upsert(self, chunks: Sequence[IndexedChunk]) -> int:
        known = {chunk.metadata.content_hash for chunk in self._chunks.values()}
        inserted = 0
        for chunk in chunks:
            if chunk.metadata.content_hash not in known:
                self._chunks[chunk.chunk_id] = chunk
                known.add(chunk.metadata.content_hash)
                inserted += 1
        return inserted

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int,
        filters: Mapping[str, str] | None = None,
    ) -> list[ScoredChunk]:
        def matches(chunk: IndexedChunk) -> bool:
            metadata = chunk.metadata.model_dump(mode="json")
            return all(metadata.get(key) == value for key, value in (filters or {}).items())

        scored = [
            ScoredChunk(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                metadata=chunk.metadata,
                score=max(0.0, min(1.0, _cosine(embedding, chunk.embedding))),
            )
            for chunk in self._chunks.values()
            if matches(chunk)
        ]
        return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class FakeConversationService:
    """The core, replayed: golden chat replies matched with the mocks' ``text_contains`` hints.

    The reply is re-addressed to the inbound message (channel, ``in_reply_to``,
    ``delivery_mode``). A message outside the golden set gets a deterministic
    fallback reply labelled as simulated, never an error.
    """

    def __init__(self, clock: FixedClock | None = None) -> None:
        self._clock = clock or FixedClock()
        self._replies = {
            str(use.match["text_contains"]).lower(): ChatMessageResponse.model_validate(
                example.load()
            ).reply
            for example in EXAMPLES
            for use in example.uses
            if use.operation_id == "postChatMessage" and use.match and "text_contains" in use.match
        }
        self.received: list[InboundMessage] = []

    async def handle(self, msg: InboundMessage) -> ConversationReply:
        self.received.append(msg)
        text = msg.content.text or ""
        # Dictated guías ("t l x nueve ocho…") match through their canonical form.
        haystack = f"{text} {extract_numero_guia(text) or ''}".lower()
        golden = next(
            (reply for needle, reply in self._replies.items() if needle in haystack), None
        )
        data = golden.model_dump(mode="json") if golden else self._fallback(msg)
        data["in_reply_to"] = msg.message_id
        data["channel"] = msg.channel
        data["session_id"] = str(msg.session_id or self._session_for(msg))
        data["meta"]["delivery_mode"] = msg.delivery_mode
        return ConversationReply.model_validate(data)

    @staticmethod
    def _session_for(msg: InboundMessage) -> UUID:
        user = hash_external_user_id(msg.external_user_id, salt=FAKE_SALT)
        return deterministic_uuid4(f"session:{msg.channel}:{user}")

    def _fallback(self, msg: InboundMessage) -> dict[str, Any]:
        return {
            "session_id": str(self._session_for(msg)),
            "in_reply_to": msg.message_id,
            "reply_id": str(deterministic_uuid4(f"reply:{msg.channel}:{msg.message_id}")),
            "channel": msg.channel,
            "status": ConversationStatus.AWAITING_USER,
            "blocks": [
                {
                    "type": "TEXT",
                    "text": "Respuesta simulada: no hay un ejemplo golden para este mensaje.",
                },
                {
                    "type": "QUICK_REPLIES",
                    "options": [{"id": "asesor", "title": "Hablar con un asesor"}],
                },
            ],
            "meta": {
                "trace_id": f"fake-{hashlib.sha256(msg.message_id.encode()).hexdigest()[:12]}",
                "intent": Intent.DESCONOCIDA,
                "confidence_score": None,
                "confidence_method": None,
                "escalation": None,
                "services_called": [],
                "evidence_ids": [],
                "llm_used": False,
                "delivery_mode": msg.delivery_mode,
            },
            "created_at": self._clock.now().isoformat(),
        }


__all__ = [
    "DEMO_NOW",
    "FakeAccountDirectory",
    "FakeAudit",
    "FakeConversationService",
    "FakeEmbedding",
    "FakeEscalation",
    "FakeHandoff",
    "FakeLlm",
    "FakePqrs",
    "FakeQuotation",
    "FakeRag",
    "FakeSpecialCargo",
    "FakeTms",
    "FakeVectorStore",
    "FixedClock",
    "deterministic_uuid4",
]
