"""Real application: implements the routes declared in ``app.contracts.api_spec``.

Same paths, operation ids and models as the contract; no extra endpoints.
Run: ``uv run uvicorn app.main:app --port 8000`` (from ``backend/``).
"""

import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Path,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import AwareDatetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.audit.application.service import ApiAuditService
from app.batches.api import routes as batch_routes
from app.batches.application.service import BatchService
from app.contracts.api_spec import (
    API_PREFIX,
    DESCRIPTION,
    TITLE,
    AdminTokenDep,
    build_contract_app,
)
from app.contracts.attachments import AttachmentUploadResponse
from app.contracts.channels_http import (
    ChannelTurnResult,
    PbxSimulateRequest,
    PbxTurnRequest,
    PbxTurnResponse,
    WebhookAck,
    WhatsAppSimulationResult,
    WhatsAppWebhookPayload,
)
from app.contracts.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    HistoryEntry,
    MessageDirection,
    SessionHistory,
)
from app.contracts.common import (
    CONTRACT_VERSION,
    ChannelType,
    ContractError,
    DeliveryMode,
    ErrorCode,
)
from app.contracts.escalation import HandoffPayload, HandoffStatus, HandoffStatusUpdate
from app.contracts.inbound import (
    Attachment,
    InboundMessage,
    MessageContent,
    MessageType,
    SystemEventCode,
    pbx_message_id,
)
from app.contracts.normalizers import normalize_numero_guia
from app.contracts.ops import (
    ChannelCounters,
    ChannelMetricsResponse,
    HandoffListResponse,
    HealthResponse,
    ReadinessChecks,
    ReadinessResponse,
    ReindexRequest,
    ReindexResponse,
)
from app.contracts.ports import ChannelOutbound, DeliveryResult
from app.contracts.pqrs import DocumentoCodigo, PqrsTicketRequest, PqrsTicketResponse
from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.reply import (
    ConversationReply,
    HandoffNoticeBlock,
    QuickRepliesBlock,
)
from app.contracts.shipment_batches import ShipmentBatchRequest
from app.contracts.tariffs import TipoPerfil
from app.contracts.tracking import TrackingResponse
from app.db.repositories import (
    AttachmentRepository,
    AuditRepository,
    ConversationRepository,
    HandoffRepository,
    MessageRepository,
    PqrsRepository,
    QuoteRepository,
    RagRepository,
)
from app.db.session import create_engine, create_session_factory
from app.modules.conversation.intents import IntentDetector
from app.modules.conversation.orchestrator import DialogueOrchestrator
from app.modules.conversation.service import ConversationService, hash_user, reply_text
from app.modules.escalation.accounts import FixtureAccountDirectory
from app.modules.escalation.engine import EscalationEngine, EscalationRules
from app.modules.escalation.handoff import HandoffService
from app.modules.escalation.sentiment import LexiconSentiment
from app.modules.pqrs.service import AttachmentService, PqrsService
from app.modules.quotation.data import TariffData
from app.modules.quotation.service import QuotationService
from app.modules.rag.corpus import load_zones
from app.modules.rag.service import IngestionService, RagService
from app.modules.tracking.service import DatasetTmsAdapter, StateMap
from app.shared.business_days import load_holidays
from app.shared.clock import build_clock
from app.shared.config import REPO_ROOT, Settings, get_settings
from app.shared.http import (
    RequestIdMiddleware,
    SlidingWindowRateLimiter,
    register_error_handlers,
    request_id_of,
    require_admin,
)
from app.shared.llm import OllamaChat, OllamaEmbedding
from app.shared.logging import configure_logging
from app.shipments.application.service import ShipmentService, tariff_version
from app.tariffs.api import routes as tariff_routes
from app.tariffs.application.service import TariffQuotationService

CONFIG_DIR = REPO_ROOT / "data" / "config"


@dataclass
class Container:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    clock: Any
    tms: DatasetTmsAdapter
    tariffs: TariffData
    quotation: QuotationService
    escalation: EscalationEngine
    sentiment: LexiconSentiment
    accounts: FixtureAccountDirectory
    holidays: frozenset[Any]
    llm: OllamaChat | None
    embedding: OllamaEmbedding | None
    rag: RagService | None
    ingestion: IngestionService | None
    intents: IntentDetector
    limiter: SlidingWindowRateLimiter
    engine: AsyncEngine
    audit: ApiAuditService
    shipments: ShipmentService
    batches: BatchService
    batch_limiter: SlidingWindowRateLimiter
    tariff_quotes: TariffQuotationService

    def pqrs(self, session: AsyncSession) -> PqrsService:
        return PqrsService(
            tms=self.tms,
            escalation=self.escalation,
            tickets=PqrsRepository(session),
            attachments=AttachmentRepository(session),
            clock=self.clock,
            holidays=self.holidays,
            accounts=self.accounts,
        )

    def handoffs(self, session: AsyncSession) -> HandoffService:
        return HandoffService(
            HandoffRepository(session), self.clock, wait_seconds=self.settings.handoff_wait_seconds
        )

    def orchestrator(self, session: AsyncSession) -> DialogueOrchestrator:
        return DialogueOrchestrator(
            tms=self.tms,
            tariffs=self.tariffs,
            quotation=self.quotation,
            quotes=QuoteRepository(session),
            rag=self.rag,
            escalation=self.escalation,
            handoffs=self.handoffs(session),
            pqrs=self.pqrs(session),
            accounts=self.accounts,
            sentiment=self.sentiment,
            intents=self.intents,
            clock=self.clock,
            confidence_threshold=self.settings.confidence_threshold,
        )

    def conversation(self, session: AsyncSession) -> ConversationService:
        s = self.settings
        return ConversationService(
            session,
            self.orchestrator,
            clock=self.clock,
            salt=s.pii_hash_salt.get_secret_value(),
            ttl_minutes={
                ChannelType.WEB_CHAT: s.session_ttl_minutes_web_chat,
                ChannelType.WHATSAPP: s.session_ttl_minutes_whatsapp,
                ChannelType.PBX: s.session_ttl_minutes_pbx,
            },
            limiter=self.limiter,
        )


def build_container(settings: Settings) -> Container:
    clock = build_clock(settings.clock_override)
    source = settings.data_source_dir
    tariffs = TariffData(
        source / "matriz_tarifas_y_fletes.csv", source / "directorio_cobertura_y_tiempos.json"
    )
    escalation = EscalationEngine(
        EscalationRules.load(source / "reglas_escalacion_humana.json"),
        confidence_threshold=settings.confidence_threshold,
        crit003_mode=settings.crit003_mode,
        crit005_mode=settings.crit005_mode,
    )
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    quotation = QuotationService(tariffs, insurance_min_source=settings.insurance_min_source)
    shipments = ShipmentService(
        tariffs,
        quotation,
        tarifa_version=tariff_version(source / "matriz_tarifas_y_fletes.csv"),
        clock=clock,
    )
    llm = embedding = None
    rag = ingestion = None
    if settings.ollama_base_url is not None:
        base = str(settings.ollama_base_url)
        llm = OllamaChat(base, settings.ollama_chat_model or "", settings.llm_timeout_seconds)
        embedding = OllamaEmbedding(
            base,
            settings.ollama_embed_model or "",
            settings.embed_dim,
            settings.llm_timeout_seconds,
        )
        rag = RagService(sessions, embedding, llm, top_k=settings.rag_top_k)
        ingestion = IngestionService(
            sessions,
            embedding,
            source_dir=source,
            zones=load_zones(CONFIG_DIR / "departamento_zona.json"),
        )
    return Container(
        settings=settings,
        sessions=sessions,
        clock=clock,
        tms=DatasetTmsAdapter(
            source / "dataset_guias_simuladas_tms.json",
            StateMap(CONFIG_DIR / "tms_state_map.json"),
            clock,
        ),
        tariffs=tariffs,
        quotation=quotation,
        escalation=escalation,
        sentiment=LexiconSentiment(CONFIG_DIR / "sentiment_es.json"),
        accounts=FixtureAccountDirectory(
            settings.accounts_file or REPO_ROOT / "data" / "fixtures" / "cuentas_simuladas.json"
        ),
        holidays=load_holidays(settings.business_holidays_file),
        llm=llm,
        embedding=embedding,
        rag=rag,
        ingestion=ingestion,
        intents=IntentDetector(llm, tariffs),
        limiter=SlidingWindowRateLimiter(settings.chat_rate_limit_per_minute),
        engine=engine,
        audit=ApiAuditService(sessions),
        shipments=shipments,
        batches=BatchService(
            sessions, shipments, clock, lease_seconds=settings.batch_lease_seconds
        ),
        batch_limiter=SlidingWindowRateLimiter(settings.batch_rate_limit_per_minute),
        tariff_quotes=TariffQuotationService(
            sessions, tariffs, clock, environment=TipoPerfil(settings.tariff_environment)
        ),
    )


# --- dependencies ----------------------------------------------------------------


def container_of(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


Box = Annotated[Container, Depends(container_of)]


async def db_session(box: Box) -> AsyncIterator[AsyncSession]:
    async with box.sessions() as session:
        yield session


Db = Annotated[AsyncSession, Depends(db_session)]


def admin(box: Container, token: str | None) -> None:
    require_admin(box.settings.admin_token.get_secret_value(), token)


# --- routers (paths and operation ids from the contract) -------------------------

api = APIRouter(prefix=API_PREFIX)
root = APIRouter(tags=["operación"])


@api.get("/tracking/{guia}", tags=["tracking"], operation_id="getTracking")
async def get_tracking(guia: Annotated[str, Path(max_length=64)], box: Box) -> TrackingResponse:
    normalized = normalize_numero_guia(guia)
    if normalized is None:
        raise ContractError(ErrorCode.GUIA_FORMATO_INVALIDO, details={"guia": guia[:64]})
    view = await box.tms.get_tracking_view(normalized)
    if view is None:
        raise ContractError(ErrorCode.GUIA_NO_ENCONTRADA, details={"numero_guia": normalized})
    return view


@api.post("/cotizar", tags=["cotización"], operation_id="createQuote")
async def create_quote(body: CotizacionRequest, box: Box, db: Db) -> CotizacionResponse:
    quote = box.quotation.quote(body)
    await QuoteRepository(db).add(body, quote, None, box.clock.now())
    await db.commit()
    return quote


@api.post("/pqrs/crear-ticket", status_code=201, tags=["pqrs"], operation_id="createPqrsTicket")
async def create_pqrs_ticket(body: PqrsTicketRequest, box: Box, db: Db) -> PqrsTicketResponse:
    ticket = await box.pqrs(db).create_ticket(body)
    await db.commit()
    return ticket


@api.post("/attachments", status_code=201, tags=["adjuntos"], operation_id="uploadAttachment")
async def upload_attachment(
    box: Box,
    db: Db,
    file: Annotated[UploadFile, File(description="PDF o JPG.")],
    declared_doc_code: Annotated[DocumentoCodigo | None, Form()] = None,
    session_id: Annotated[UUID | None, Form()] = None,
) -> AttachmentUploadResponse:
    limit = box.settings.attachments_max_bytes
    data = await file.read(limit + 1)
    service = AttachmentService(
        AttachmentRepository(db), box.settings.attachments_dir, limit, box.clock
    )
    stored = await service.store(data, declared=declared_doc_code, session_id=session_id)
    await db.commit()
    return stored


@api.post("/chat/messages", tags=["web chat"], operation_id="postChatMessage")
async def post_chat_message(
    body: ChatMessageRequest, request: Request, box: Box, db: Db
) -> ChatMessageResponse:
    message_type = MessageType.TEXT if body.text and body.text.strip() else MessageType.DOCUMENT
    known = await AttachmentRepository(db).existing([a.attachment_id for a in body.attachments])
    missing = [a.attachment_id for a in body.attachments if a.attachment_id not in known]
    if missing:
        raise ContractError(ErrorCode.NO_ENCONTRADO, details={"attachment_ids": missing})
    msg = InboundMessage(
        channel=ChannelType.WEB_CHAT,
        message_id=str(body.message_id),
        external_user_id=str(body.client_id),
        session_id=body.session_id,
        message_type=message_type,
        content=MessageContent(
            text=body.text,
            attachments=[
                Attachment(
                    attachment_id=a.attachment_id,
                    kind="DOCUMENT",
                    mime_type="application/pdf",
                    size_bytes=0,
                    declared_doc_code=a.declared_doc_code,
                )
                for a in body.attachments
            ],
        ),
        timestamp=box.clock.now(),
        metadata={"locale": body.locale},
        delivery_mode=DeliveryMode.SIMULATED,
    )
    reply = await box.conversation(db).handle(msg, trace_id=request_id_of(request))
    return ChatMessageResponse(reply=reply)


@api.get("/chat/sessions/{session_id}/history", tags=["web chat"], operation_id="getSessionHistory")
async def get_session_history(
    session_id: UUID,
    box: Box,
    db: Db,
    client_id: Annotated[UUID | None, Header(alias="X-Client-Id")] = None,
) -> SessionHistory:
    state = await ConversationRepository(db).get(session_id)
    if state is not None and client_id is not None:
        salt = box.settings.pii_hash_salt.get_secret_value()
        if hash_user(salt, state.channel, str(client_id)) != state.external_user_id_hash:
            state = None  # someone else's session: indistinguishable from a missing one
    if state is None:
        raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "session"})
    entries = []
    for row in await MessageRepository(db).history(session_id):
        if "reply" in row.content_json:
            reply = ConversationReply.model_validate(row.content_json["reply"])
            entries.append(
                HistoryEntry(
                    message_id=row.message_id,
                    direction=MessageDirection.OUTBOUND,
                    message_type=None,
                    text=reply_text(reply) or None,
                    blocks=reply.blocks,
                    delivery_mode=DeliveryMode(row.delivery_mode),
                    created_at=row.created_at,
                )
            )
        else:
            entries.append(
                HistoryEntry(
                    message_id=row.message_id,
                    direction=MessageDirection.INBOUND,
                    message_type=MessageType(row.message_type) if row.message_type else None,
                    text=row.content_json["content"].get("text"),
                    blocks=None,
                    delivery_mode=DeliveryMode(row.delivery_mode),
                    created_at=row.created_at,
                )
            )
    return SessionHistory(
        session_id=session_id, channel=state.channel, status=state.status, messages=entries
    )


# --- WhatsApp (SIMULATED; REAL delivery needs Meta credentials) -------------------

_MAX_WA_BUTTONS = 3


def render_whatsapp(reply: ConversationReply, max_chars: int) -> ChannelOutbound:
    parts: list[dict[str, Any]] = []
    body = reply_text(reply) or "TransLogix"
    buttons = [o for b in reply.blocks if isinstance(b, QuickRepliesBlock) for o in b.options]
    for block in reply.blocks:
        if isinstance(block, HandoffNoticeBlock):
            body = f"{body} {block.mensaje_cliente}".strip()
    body = body[:max_chars]
    if buttons:
        parts.append(
            {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": o.id, "title": o.title[:20]}}
                            for o in buttons[:_MAX_WA_BUTTONS]
                        ]
                    },
                },
            }
        )
    else:
        parts.append({"type": "text", "text": {"body": body}})
    return ChannelOutbound(
        channel=ChannelType.WHATSAPP,
        session_id=str(reply.session_id),
        reply_id=str(reply.reply_id),
        delivery_mode=DeliveryMode.SIMULATED,
        parts=parts,
        plain_text=body,
    )


def whatsapp_inbound(payload: WhatsAppWebhookPayload, box: Container) -> list[InboundMessage]:
    messages = []
    for entry in payload.entry:
        for change in entry.changes:
            for m in change.value.messages:
                interactive = m.interactive and (
                    m.interactive.button_reply or m.interactive.list_reply
                )
                if interactive:
                    kind, content = (
                        MessageType.INTERACTIVE_REPLY,
                        MessageContent(interactive_reply_id=interactive.id, text=interactive.title),
                    )
                elif m.text:
                    kind, content = MessageType.TEXT, MessageContent(text=m.text.body[:4096])
                else:
                    continue  # media download needs Meta credentials: not processed here
                messages.append(
                    InboundMessage(
                        channel=ChannelType.WHATSAPP,
                        message_id=m.id,
                        external_user_id=m.from_,
                        message_type=kind,
                        content=content,
                        timestamp=box.clock.now(),
                        metadata={
                            "wa_message_type": m.type,
                            "phone_number_id": change.value.metadata.phone_number_id,
                        },
                        delivery_mode=DeliveryMode.SIMULATED,
                    )
                )
    return messages


@api.get(
    "/channels/whatsapp/webhook",
    response_class=PlainTextResponse,
    tags=["whatsapp"],
    operation_id="verifyWhatsAppWebhook",
)
async def verify_whatsapp_webhook(
    box: Box,
    mode: Annotated[str, Query(alias="hub.mode")],
    verify_token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
) -> str:
    expected = box.settings.whatsapp_verify_token
    if expected is None:
        raise ContractError(ErrorCode.NO_CONFIGURADO, details={"variable": "WHATSAPP_VERIFY_TOKEN"})
    if mode != "subscribe" or not hmac.compare_digest(expected.get_secret_value(), verify_token):
        raise ContractError(ErrorCode.NO_AUTORIZADO)
    return challenge


@api.post("/channels/whatsapp/webhook", tags=["whatsapp"], operation_id="receiveWhatsAppWebhook")
async def receive_whatsapp_webhook(
    request: Request,
    box: Box,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256")],
) -> WebhookAck:
    secret = box.settings.whatsapp_app_secret
    if secret is None:
        raise ContractError(ErrorCode.NO_CONFIGURADO, details={"variable": "WHATSAPP_APP_SECRET"})
    raw = await request.body()
    digest = hmac.new(secret.get_secret_value().encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"sha256={digest}", signature):
        raise ContractError(ErrorCode.FIRMA_INVALIDA)
    payload = WhatsAppWebhookPayload.model_validate_json(raw)
    accepted = duplicates = 0
    for msg in whatsapp_inbound(payload, box):
        async with box.sessions() as db:
            if await MessageRepository(db).find_reply(msg.channel, msg.message_id):
                duplicates += 1
                continue
            await box.conversation(db).handle(msg, trace_id=request_id_of(request))
            accepted += 1
    return WebhookAck(messages=accepted, duplicates=duplicates)


@api.post("/channels/whatsapp/simulate", tags=["whatsapp"], operation_id="simulateWhatsApp")
async def simulate_whatsapp(
    payload: WhatsAppWebhookPayload, request: Request, box: Box
) -> WhatsAppSimulationResult:
    results = []
    for msg in whatsapp_inbound(payload, box):
        async with box.sessions() as db:
            reply = await box.conversation(db).handle(msg, trace_id=request_id_of(request))
        results.append(
            ChannelTurnResult(
                in_reply_to=msg.message_id,
                reply=reply,
                outbound=render_whatsapp(reply, box.settings.wa_max_body_chars),
                delivery=DeliveryResult(
                    delivery_mode=DeliveryMode.SIMULATED, status="NOT_ATTEMPTED"
                ),
            )
        )
    return WhatsAppSimulationResult(results=results)


# --- PBX (SIMULATED: transcript in, speakable text out) ----------------------------

_DIGITS = ("cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve")
_SYMBOLS = {"$": " pesos ", "%": " por ciento ", "°": " grados ", "→": " a ", "#": " "}


def speakable(text: str, max_words: int = 40) -> str:
    """Digits spelled one by one (guías, amounts); symbols read aloud; no Markdown."""
    for symbol, spoken in _SYMBOLS.items():
        text = text.replace(symbol, spoken)
    text = re.sub(r"(?<=\d)[.,](?=\d)", "", text)
    text = re.sub(r"\d", lambda m: f" {_DIGITS[int(m.group())]} ", text)
    text = re.sub(r"[*_`~\[\]«»]", "", text)
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    words = text.split()
    return " ".join(words[:max_words]) or "Un momento, por favor."


def spoken_to_text(transcript: str) -> str:
    """ASR output to text the core understands: "T L X nueve ocho..." -> "TLX98..."."""
    words = {w: str(i) for i, w in enumerate(_DIGITS)}
    text = re.sub(
        r"\b(" + "|".join(_DIGITS) + r")\b",
        lambda m: words[m.group(1).lower()],
        transcript,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = re.sub(r"\bT\s*L\s*X\b", "TLX", text, flags=re.IGNORECASE)
    return re.sub(r"\bC\s*O\b", "CO", text)


async def _pbx_turn(body: PbxTurnRequest, request: Request, box: Container) -> PbxTurnResponse:
    events = {
        "CALL_STARTED": SystemEventCode.CALL_STARTED,
        "CALL_ENDED": SystemEventCode.CALL_ENDED,
    }
    if body.event != "TURN":
        kind, content = (
            MessageType.SYSTEM_EVENT,
            MessageContent(system_event=events[body.event]),
        )
    elif body.transcript is None:
        raise ContractError(
            ErrorCode.NO_CONFIGURADO, "La transcripción de audio (ASR) no está integrada."
        )
    else:
        kind, content = MessageType.TEXT, MessageContent(text=spoken_to_text(body.transcript))
    salt = box.settings.pii_hash_salt.get_secret_value().encode()
    msg = InboundMessage(
        channel=ChannelType.PBX,
        message_id=pbx_message_id(body.call_id, body.turn_index),
        external_user_id=body.caller_id,
        message_type=kind,
        content=content,
        timestamp=box.clock.now(),
        metadata={
            "call_id": body.call_id,
            "ani_hash": hmac.new(salt, body.caller_id.encode(), hashlib.sha256).hexdigest(),
            "turn_index": body.turn_index,
            "barge_in": body.barge_in,
        },
        delivery_mode=DeliveryMode.SIMULATED,
    )
    async with box.sessions() as db:
        reply = await box.conversation(db).handle(msg, trace_id=request_id_of(request))
    notice = next((b for b in reply.blocks if isinstance(b, HandoffNoticeBlock)), None)
    spoken = reply_text(reply) or (notice.mensaje_cliente if notice else "")
    return PbxTurnResponse(
        reply=reply,
        speech_text=speakable(spoken),
        transfer=notice is not None,
        delivery=DeliveryResult(delivery_mode=DeliveryMode.SIMULATED, status="NOT_ATTEMPTED"),
    )


@api.post("/channels/pbx/turns", tags=["pbx"], operation_id="postPbxTurn")
async def post_pbx_turn(body: PbxTurnRequest, request: Request, box: Box) -> PbxTurnResponse:
    return await _pbx_turn(body, request, box)


@api.post("/channels/pbx/simulate", tags=["pbx"], operation_id="simulatePbx")
async def simulate_pbx(body: PbxSimulateRequest, request: Request, box: Box) -> PbxTurnResponse:
    turn = PbxTurnRequest(
        call_id=body.call_id,
        caller_id=body.caller_id,
        turn_index=body.turn_index,
        transcript=body.transcript,
    )
    return await _pbx_turn(turn, request, box)


# --- handoffs (advisor inbox, admin token) -----------------------------------------


@api.get("/handoffs/{handoff_id}", tags=["handoff"], operation_id="getHandoff")
async def get_handoff(handoff_id: UUID, token: AdminTokenDep, box: Box, db: Db) -> HandoffPayload:
    admin(box, token)
    payload = await box.handoffs(db).get(handoff_id)
    if payload is None:
        raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "handoff"})
    return payload


@api.patch("/handoffs/{handoff_id}", tags=["handoff"], operation_id="updateHandoffStatus")
async def update_handoff_status(
    handoff_id: UUID, body: HandoffStatusUpdate, token: AdminTokenDep, box: Box, db: Db
) -> HandoffPayload:
    admin(box, token)
    updated = await box.handoffs(db).update_status(handoff_id, body.status)
    if updated is None:
        raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "handoff"})
    await db.commit()
    return updated


@api.get("/handoffs", tags=["handoff"], operation_id="listHandoffs")
async def list_handoffs(
    token: AdminTokenDep, box: Box, db: Db, status: Annotated[HandoffStatus | None, Query()] = None
) -> HandoffListResponse:
    admin(box, token)
    return HandoffListResponse(items=list(await box.handoffs(db).list_by_status(status)))


# --- operation ----------------------------------------------------------------------


@api.get("/metrics/channels", tags=["operación"], operation_id="getChannelMetrics")
async def get_channel_metrics(
    box: Box,
    db: Db,
    desde: Annotated[AwareDatetime | None, Query()] = None,
    hasta: Annotated[AwareDatetime | None, Query()] = None,
) -> ChannelMetricsResponse:
    counters = await AuditRepository(db).channel_counters(desde, hasta)
    zero = {"conversaciones": 0, "mensajes_entrantes": 0, "mensajes_salientes": 0, "handoffs": 0}
    return ChannelMetricsResponse(
        generado_en=box.clock.now(),
        desde=desde,
        hasta=hasta,
        canales=[
            ChannelCounters(channel=c, **(zero | counters.get(c.value, {}))) for c in ChannelType
        ],
    )


@api.post("/admin/rag/reindex", tags=["operación"], operation_id="reindexRag")
async def reindex_rag(
    token: AdminTokenDep, box: Box, body: Annotated[ReindexRequest | None, Body()] = None
) -> ReindexResponse:
    admin(box, token)
    if box.ingestion is None or box.rag is None:
        raise ContractError(ErrorCode.NO_CONFIGURADO, details={"variable": "OLLAMA_BASE_URL"})
    request = body or ReindexRequest()
    report = await box.ingestion.reindex(
        now=box.clock.now(), doc_codes=request.doc_codes, force=request.force
    )
    box.rag.invalidate()
    return ReindexResponse(
        documentos=report.documentos,
        chunks=report.chunks,
        omitidos_sin_cambios=report.omitidos_sin_cambios,
        duracion_ms=report.duracion_ms,
        embed_model=box.settings.ollama_embed_model or "",
        embed_dim=box.settings.embed_dim,
    )


@root.get("/health", operation_id="getHealth")
async def get_health() -> HealthResponse:
    return HealthResponse()


@root.get(
    "/ready",
    operation_id="getReadiness",
    responses={503: {"model": ReadinessResponse}},
)
async def get_readiness(box: Box) -> JSONResponse:
    database = rag_index = "fail"
    try:
        async with box.sessions() as db:
            await db.execute(text("SELECT 1"))
            database = "ok"
            rag_index = "ok" if await RagRepository(db).count_chunks() else "empty"
    except Exception:
        database = rag_index = "fail"
    ollama = "disabled"
    if box.llm is not None:
        ollama = "ok" if await box.llm.ping() else "fail"
    checks = ReadinessChecks(database=database, ollama=ollama, rag_index=rag_index)  # type: ignore[arg-type]
    ready = database == "ok" and ollama != "fail" and rag_index == "ok"
    body = ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if ready else 503)


def _publish_raw_body_schemas(app: FastAPI) -> None:
    """Routes that read their raw body publish the contract request schema, copied as is."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate()
            published = schema["components"]["schemas"]
            contract = build_contract_app().openapi()["components"]["schemas"]
            pending = [ShipmentBatchRequest.__name__]
            while pending:
                name = pending.pop()
                if name not in published:
                    published[name] = contract[name]
                    pending += re.findall(r"#/components/schemas/(\w+)", json.dumps(contract[name]))
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = build_container(settings)
        yield
        box: Container = app.state.container
        for client in (box.llm, box.embedding):
            if client is not None:
                await client.aclose()
        await box.engine.dispose()

    app = FastAPI(title=TITLE, version=CONTRACT_VERSION, description=DESCRIPTION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST", "PATCH"],
        # Authorization carries the B2B API key and the staff token (1.3.0/1.4.0).
        allow_headers=[
            "Content-Type",
            "Authorization",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Admin-Token",
            "X-Client-Id",
        ],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(api)
    app.include_router(batch_routes.router)
    app.include_router(tariff_routes.router)
    app.include_router(root)
    _publish_raw_body_schemas(app)
    return app


app = create_app()
