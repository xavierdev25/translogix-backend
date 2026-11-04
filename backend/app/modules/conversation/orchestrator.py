"""AI Dialogue Orchestrator (P08): one core for every channel, deterministic first.

A turn is: guard → intent → the service that owns the intent → escalation
policy → channel-agnostic blocks. Calculations, deadlines, tracking states and
escalation never go through the LLM; the LLM only classifies ambiguous
messages and phrases RAG answers from retrieved evidence.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.contracts.common import ContractError, ErrorCode
from app.contracts.conversation import ConversationState, ConversationStatus, Intent, Sentiment
from app.contracts.escalation import (
    EscalationAction,
    EscalationDecision,
    EscalationInput,
    HandoffStatus,
    HistorialItem,
)
from app.contracts.inbound import InboundMessage, MessageType, SystemEventCode
from app.contracts.normalizers import extract_numero_guia
from app.contracts.ports import AccountDirectoryPort, Clock, EscalationPort, TmsPort
from app.contracts.pqrs import (
    DocumentoAdjunto,
    DocumentoCodigo,
    PqrsTicketRequest,
    PqrsTipo,
    TipoNovedad,
)
from app.contracts.rag import DocCode, RagAnswer
from app.contracts.reply import (
    Block,
    ChecklistItemDetallado,
    CitationItem,
    CitationsBlock,
    HandoffNoticeBlock,
    PqrsTicketBlock,
    QuickRepliesBlock,
    QuickReplyOption,
    QuoteSummaryBlock,
    RequestAttachmentsBlock,
    ServiceName,
    TextBlock,
    TrackingCardBlock,
)
from app.contracts.tms import TmsShipment
from app.db.repositories import QuoteRepository
from app.modules.conversation.intents import IntentDetector, looks_like_injection
from app.modules.conversation.slots import QUESTIONS, QuoteSlots
from app.modules.escalation.handoff import HandoffService
from app.modules.escalation.sentiment import LexiconSentiment
from app.modules.pqrs.service import PqrsService
from app.modules.quotation.data import TariffData
from app.modules.quotation.service import QuotationService
from app.modules.rag.service import RagService
from app.modules.tracking.service import render_tracking_text
from app.shared.text import fold, format_cop, format_date_es

logger = logging.getLogger("app.orchestrator")

ADVISOR = QuickReplyOption(id="asesor", title="Hablar con un asesor")
MENU = [
    QuickReplyOption(id="rastrear", title="Rastrear una guía"),
    QuickReplyOption(id="cotizar", title="Cotizar un envío"),
    ADVISOR,
]
INTERACTIVE_TEXT = {
    "asesor": "quiero hablar con un asesor",
    "equipo_legal": "quiero hablar con un asesor",
    "cotizar": "quiero cotizar un envío",
    "otra_ruta": "quiero cotizar un envío",
    "rastrear": "quiero rastrear mi guía",
    "otra_guia": "quiero rastrear mi guía",
    "pqrs": "quiero radicar una pqrs",
    "novedad": "quiero reportar una novedad de mi envío",
    "cadena_frio": "agregar cadena de frío a la cotización",
}
DOC_DESCRIPTIONS = {
    DocumentoCodigo.GUIA: "Número de guía de transporte (TLX-XXXXXX-CO).",
    DocumentoCodigo.FACTURA_COMERCIAL: "Factura comercial de venta con IVA, o documento equivalente que certifique el costo del producto.",
    DocumentoCodigo.CARTA_RECLAMACION: "Carta de reclamación firmada, con el valor exacto reclamado y la descripción de lo ocurrido.",
    DocumentoCodigo.CERTIFICACION_BANCARIA: "Certificación bancaria de no más de 30 días para el reembolso.",
    DocumentoCodigo.FOTOS_EMPAQUE: "Fotos del empaque exterior con la etiqueta de despacho y de los materiales de amortiguación.",
    DocumentoCodigo.FOTOS_DANO: "Fotos del daño interno y del estado del producto.",
    DocumentoCodigo.CEDULA_O_RUT: "Copia de la cédula o del RUT.",
    DocumentoCodigo.ACTA_CON_SALVEDAD: "Acta de entrega con salvedad (solo si la avería era visible al recibir).",
}
NOVEDAD_LABELS = {
    TipoNovedad.AVERIA_NO_APARENTE: "avería no aparente",
    TipoNovedad.AVERIA_APARENTE: "avería aparente",
    TipoNovedad.SAQUEO: "saqueo (faltante de contenido)",
    TipoNovedad.PERDIDA_TOTAL: "pérdida total",
}
_INCIDENT_WORDS = (
    "retenid",
    "novedad",
    "accidente",
    "derrame",
    "fuga",
    "incendio",
    "rot",
    "danad",
    "averi",
)


@dataclass
class TurnOutcome:
    blocks: list[Block]
    intent: Intent
    services: list[ServiceName] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    llm_used: bool = False
    confidence: float | None = None
    escalation: EscalationDecision | None = None


@dataclass
class _Facts:
    """What the escalation policy may look at for this turn."""

    shipment: TmsShipment | None = None
    confidence: float | None = None
    claim_cop: int | None = None
    incident: bool = False
    hide_answer_if_escalated: bool = False


class DialogueOrchestrator:
    def __init__(
        self,
        *,
        tms: TmsPort,
        tariffs: TariffData,
        quotation: QuotationService,
        quotes: QuoteRepository,
        rag: RagService | None,
        escalation: EscalationPort,
        handoffs: HandoffService,
        pqrs: PqrsService,
        accounts: AccountDirectoryPort,
        sentiment: LexiconSentiment,
        intents: IntentDetector,
        clock: Clock,
        confidence_threshold: float,
    ) -> None:
        self._tms = tms
        self._tariffs = tariffs
        self._quotation = quotation
        self._quotes = quotes
        self._rag = rag
        self._escalation = escalation
        self._handoffs = handoffs
        self._pqrs = pqrs
        self._accounts = accounts
        self._sentiment = sentiment
        self._intents = intents
        self._clock = clock
        self._threshold = confidence_threshold

    async def respond(
        self, state: ConversationState, msg: InboundMessage, history: list[HistorialItem]
    ) -> TurnOutcome:
        if msg.message_type is MessageType.SYSTEM_EVENT:
            return self._system_event(state, msg.content.system_event)
        if state.status is ConversationStatus.ESCALATED and state.handoff_id:
            waiting = await self._still_waiting(state)
            if waiting:
                return waiting
        text = self._text_of(msg)
        kept = [
            {"codigo": a.declared_doc_code.value, "attachment_id": a.attachment_id}
            for a in msg.content.attachments
            if a.declared_doc_code is not None
        ]
        if kept:
            state.slots["adjuntos"] = [*state.slots.get("adjuntos", []), *kept]
        if not text.strip():
            return TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text="Recibimos tu archivo. Por ahora los documentos se adjuntan al ticket PQRS; cuéntame en qué te ayudo.",
                    ),
                    QuickRepliesBlock(type="QUICK_REPLIES", options=MENU),
                ],
                Intent.DESCONOCIDA,
            )
        if looks_like_injection(text):
            logger.info("prompt injection refused", extra={"channel": msg.channel.value})
            return TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text="No puedo ayudarte con eso. Puedo rastrear guías, cotizar envíos, resolver dudas de mercancías especiales o radicar una PQRS.",
                    ),
                    QuickRepliesBlock(type="QUICK_REPLIES", options=MENU),
                ],
                Intent.DESCONOCIDA,
                evidence=["guardia:prompt_injection"],
            )

        state.sentiment = self._sentiment.classify(text)
        slots = QuoteSlots.model_validate(state.slots.get("cotizacion") or {})
        quote_in_progress = bool(state.slots.get("cotizacion")) and bool(slots.missing())
        ruled = self._intents.by_rules(text, quote_in_progress=quote_in_progress)
        # Policy first: an explicit human request or a legal threat (CRIT-002) goes to a person
        # before any flow runs, so no automated answer is attempted (chat.amenaza-legal).
        early = self._escalation.evaluate(
            EscalationInput(
                intent=ruled or Intent.DESCONOCIDA,
                user_text_normalized=text,
                sentiment=state.sentiment,
                incident_reported=False,
            )
        )
        if early.escalate and early.primary and early.primary.id_criterio == "CRIT-002":
            state.active_intent = ruled or Intent.DESCONOCIDA
            outcome = TurnOutcome([], state.active_intent, escalation=early)
            await self._apply_escalation(state, outcome, text, _Facts(), history)
            return outcome
        if msg.content.interactive_reply_id == "cadena_frio" and state.slots.get(
            "ultima_cotizacion"
        ):
            intent, llm_used = Intent.COTIZACION, False
        elif ruled is not None:
            intent, llm_used = ruled, False
        else:
            intent, llm_used = await self._intents.detect(text, quote_in_progress=quote_in_progress)
        state.active_intent = intent

        facts = _Facts(incident=any(word in fold(text) for word in _INCIDENT_WORDS))
        handlers = {
            Intent.TRACKING: self._tracking,
            Intent.COTIZACION: self._quote,
            Intent.PQRS: self._claim,
            Intent.CARGA_ESPECIAL: self._special_cargo,
            Intent.CONSULTA_DOCUMENTAL: self._documentary,
        }
        handler = handlers.get(intent)
        if handler is not None:
            outcome = await handler(state, msg, text, facts)
        elif intent is Intent.SALUDO:
            outcome = TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text="¡Hola! Soy el asistente de TransLogix. Puedo rastrear una guía, cotizar un envío, resolver dudas sobre mercancías especiales o ayudarte a radicar una PQRS.",
                    ),
                    QuickRepliesBlock(type="QUICK_REPLIES", options=MENU),
                ],
                intent,
            )
        elif intent is Intent.HABLAR_CON_HUMANO:
            outcome = TurnOutcome([], intent)
        else:
            state.fallback_count += 1
            outcome = TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text="No estoy seguro de haber entendido. Puedo ayudarte con estas opciones o comunicarte con un asesor.",
                    ),
                    QuickRepliesBlock(type="QUICK_REPLIES", options=MENU),
                ],
                Intent.DESCONOCIDA,
            )
        outcome.llm_used = outcome.llm_used or llm_used
        await self._apply_escalation(state, outcome, text, facts, history)
        return outcome

    # --- flows ---------------------------------------------------------------

    async def _tracking(
        self, state: ConversationState, msg: InboundMessage, text: str, facts: _Facts
    ) -> TurnOutcome:
        guia = extract_numero_guia(text) or (
            state.numero_guia_consultada if "guia" in state.slots.get("pendiente", "") else None
        )
        if guia is None:
            state.slots["pendiente"] = "guia"
            return TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text="Con gusto. ¿Cuál es tu número de guía? Tiene la forma TLX-123456-CO.",
                    )
                ],
                Intent.TRACKING,
            )
        state.slots.pop("pendiente", None)
        try:
            view = await self._tms.get_tracking_view(guia)
            shipment = await self._tms.get_shipment(guia)
        except ContractError:
            return TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text="No pude consultar el sistema de rastreo en este momento. Intenta de nuevo en unos minutos o habla con un asesor.",
                    ),
                    QuickRepliesBlock(type="QUICK_REPLIES", options=[ADVISOR]),
                ],
                Intent.TRACKING,
                services=[ServiceName.TMS],
            )
        if view is None or shipment is None:
            return TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text=f"No encontramos la guía {guia}. Revisa que esté bien escrita; si quieres, te comunico con un asesor.",
                    ),
                    QuickRepliesBlock(
                        type="QUICK_REPLIES",
                        options=[
                            QuickReplyOption(id="otra_guia", title="Escribir otra guía"),
                            ADVISOR,
                        ],
                    ),
                ],
                Intent.TRACKING,
                services=[ServiceName.TMS],
            )
        state.numero_guia_consultada = guia
        facts.shipment = shipment
        follow_up = (
            QuickReplyOption(id="novedad", title="Reportar una novedad")
            if view.prueba_entrega
            else QuickReplyOption(id="cotizar", title="Cotizar un envío")
        )
        return TurnOutcome(
            [
                TextBlock(type="TEXT", text=render_tracking_text(view, self._clock)),
                TrackingCardBlock(
                    type="TRACKING_CARD",
                    numero_guia=view.numero_guia,
                    estado=view.estado_actual,
                    eta=view.fecha_estimada_entrega,
                    ubicacion_referencia=view.ubicacion_referencia,
                    eventos=view.eventos[-3:],
                    flags=view.flags,
                ),
                QuickRepliesBlock(type="QUICK_REPLIES", options=[follow_up, ADVISOR]),
            ],
            Intent.TRACKING,
            services=[ServiceName.TMS],
            evidence=[guia],
        )

    async def _quote(
        self, state: ConversationState, msg: InboundMessage, text: str, facts: _Facts
    ) -> TurnOutcome:
        if msg.content.interactive_reply_id == "cadena_frio" and state.slots.get(
            "ultima_cotizacion"
        ):
            slots = QuoteSlots.model_validate(
                state.slots["ultima_cotizacion"] | {"requiere_cadena_frio": True}
            )
        else:
            slots = QuoteSlots.model_validate(state.slots.get("cotizacion") or {})
            slots.update_from(text, self._tariffs)
        if (
            slots.origen
            and slots.destino
            and self._tariffs.route(slots.origen, slots.destino) is None
        ):
            state.slots.pop("cotizacion", None)
            return TurnOutcome(
                [
                    TextBlock(
                        type="TEXT",
                        text=f"No tengo tarifa para la ruta {slots.origen} → {slots.destino} en la matriz disponible, así que no puedo darte un valor. Si quieres, te comunico con un asesor.",
                    ),
                    QuickRepliesBlock(
                        type="QUICK_REPLIES",
                        options=[
                            QuickReplyOption(id="otra_ruta", title="Cotizar otra ruta"),
                            ADVISOR,
                        ],
                    ),
                ],
                Intent.COTIZACION,
                services=[ServiceName.QUOTATION],
            )
        missing = slots.missing()
        if missing:
            slots.pending = missing[0]
            state.slots["cotizacion"] = slots.dump()
            return TurnOutcome(
                [TextBlock(type="TEXT", text=QUESTIONS[missing[0]])], Intent.COTIZACION
            )
        request = slots.to_request()
        try:
            quote = self._quotation.quote(request)
        except ContractError as error:
            state.slots.pop("cotizacion", None)
            return TurnOutcome(
                [
                    TextBlock(type="TEXT", text=error.message),
                    QuickRepliesBlock(
                        type="QUICK_REPLIES",
                        options=[
                            QuickReplyOption(id="otra_ruta", title="Cotizar otra ruta"),
                            ADVISOR,
                        ],
                    ),
                ],
                Intent.COTIZACION,
                services=[ServiceName.QUOTATION],
            )
        await self._quotes.add(request, quote, state.session_id, self._clock.now())
        state.slots.pop("cotizacion", None)
        state.slots["ultima_cotizacion"] = slots.model_copy(update={"pending": None}).dump()
        c = quote.calculo
        minimum = (
            f" Se aplicó el flete mínimo de la ruta ({format_cop(c.flete_minimo_cop)})."
            if c.flete_minimo_aplicado
            else ""
        )
        summary = (
            f"Tu cotización de {quote.ruta.origen} a {quote.ruta.destino}: flete total "
            f"{format_cop(c.flete_total_cop)} (peso facturable {str(c.peso_facturable_kg).replace('.', ',')} kg; "
            f"seguro {format_cop(c.seguro_cop)}).{minimum} Entrega estimada en "
            f"{quote.tiempo_entrega_horas} horas. Valores calculados con la fórmula {quote.formula_version}."
        )
        options = (
            [ADVISOR]
            if request.requiere_cadena_frio
            else [QuickReplyOption(id="cadena_frio", title="Agregar cadena de frío"), ADVISOR]
        )
        return TurnOutcome(
            [
                TextBlock(type="TEXT", text=summary + "".join(f" {w}" for w in quote.advertencias)),
                QuoteSummaryBlock(
                    type="QUOTE_SUMMARY",
                    cotizacion_id=quote.cotizacion_id,
                    codigo_trayecto=quote.ruta.codigo_trayecto,
                    origen=quote.ruta.origen,
                    destino=quote.ruta.destino,
                    peso_facturable_kg=c.peso_facturable_kg,
                    flete_total_cop=c.flete_total_cop,
                    tiempo_entrega_horas=quote.tiempo_entrega_horas,
                    formula_version=quote.formula_version,
                    advertencias=quote.advertencias,
                ),
                QuickRepliesBlock(type="QUICK_REPLIES", options=options),
            ],
            Intent.COTIZACION,
            services=[ServiceName.QUOTATION],
            evidence=[str(quote.cotizacion_id)],
        )

    async def _claim(
        self, state: ConversationState, msg: InboundMessage, text: str, facts: _Facts
    ) -> TurnOutcome:
        folded = fold(text)
        guia = extract_numero_guia(text) or state.numero_guia_consultada
        novedad = self._novedad(folded)
        if guia is None or novedad is None:
            # No ticket possible yet: answer from the PQRS protocol, with citations (S15).
            return await self._rag_flow(state, text, [DocCode.DOC_PQRS_2026_04], Intent.PQRS, facts)
        state.numero_guia_consultada = guia
        facts.shipment = await self._tms.get_shipment(guia)
        try:
            ticket = await self._pqrs.create_ticket(
                PqrsTicketRequest(
                    session_id=state.session_id,
                    tipo=PqrsTipo.SINIESTRO,
                    numero_guia=guia,
                    tipo_novedad=novedad,
                    descripcion=text[:4000],
                    documentos=[
                        DocumentoAdjunto.model_validate(d) for d in state.slots.get("adjuntos", [])
                    ],
                ),
                sentiment=state.sentiment,
            )
        except ContractError as error:
            return TurnOutcome(
                [
                    TextBlock(type="TEXT", text=error.message),
                    QuickRepliesBlock(type="QUICK_REPLIES", options=[ADVISOR]),
                ],
                Intent.PQRS,
                services=[ServiceName.TMS, ServiceName.PQRS],
            )
        elig = ticket.elegibilidad
        parts = ["Lamentamos lo sucedido."]
        pod = facts.shipment.prueba_entrega if facts.shipment else None
        if pod and pod.observaciones_recibido:
            parts.append(f"La prueba de entrega de {guia} registra «{pod.observaciones_recibido}».")
        if elig:
            when = format_date_es(elig.fecha_limite) if elig.fecha_limite else "sin fecha"
            if elig.dentro_de_plazo:
                parts.append(
                    f"Tu caso se trata como {NOVEDAD_LABELS[novedad]}: tienes hasta el {when} para radicarlo."
                )
            else:
                parts.append(
                    f"Según el protocolo de PQRS (DOC-PQRS-2026-04 §3), el plazo para esta novedad venció el {when}."
                )
        blocks: list[Block] = [
            TextBlock(type="TEXT", text=" ".join(parts)),
            RequestAttachmentsBlock(
                type="REQUEST_ATTACHMENTS",
                checklist=[
                    ChecklistItemDetallado(
                        codigo=i.codigo,
                        obligatorio=i.obligatorio,
                        recibido=i.recibido,
                        descripcion=DOC_DESCRIPTIONS[i.codigo],
                    )
                    for i in ticket.checklist
                ],
            ),
            PqrsTicketBlock(
                type="PQRS_TICKET",
                ticket_id=ticket.ticket_id,
                estado=ticket.estado,
                faltantes=ticket.faltantes,
            ),
        ]
        if elig and not elig.dentro_de_plazo:
            blocks.append(
                QuickRepliesBlock(
                    type="QUICK_REPLIES",
                    options=[
                        QuickReplyOption(id="equipo_legal", title="Contactar al equipo legal"),
                        ADVISOR,
                    ],
                )
            )
        return TurnOutcome(
            blocks,
            Intent.PQRS,
            services=[ServiceName.TMS, ServiceName.PQRS, ServiceName.ESCALATION],
            evidence=[guia, str(ticket.ticket_id)],
            escalation=ticket.escalamiento,
        )

    async def _special_cargo(
        self, state: ConversationState, msg: InboundMessage, text: str, facts: _Facts
    ) -> TurnOutcome:
        return await self._rag_flow(
            state, text, [DocCode.DOC_OPS_2026_03], Intent.CARGA_ESPECIAL, facts
        )

    async def _documentary(
        self, state: ConversationState, msg: InboundMessage, text: str, facts: _Facts
    ) -> TurnOutcome:
        return await self._rag_flow(state, text, None, Intent.CONSULTA_DOCUMENTAL, facts)

    async def _rag_flow(
        self,
        state: ConversationState,
        text: str,
        docs: list[DocCode] | None,
        intent: Intent,
        facts: _Facts,
    ) -> TurnOutcome:
        if self._rag is None:
            return self._rag_unavailable(intent)
        try:
            answer = await self._rag.answer(text, doc_codes=docs)
        except ContractError as error:
            if error.code in (ErrorCode.LLM_NO_DISPONIBLE, ErrorCode.NO_CONFIGURADO):
                return self._rag_unavailable(intent)
            raise
        state.confidence_scores.append(answer.confidence.score)
        facts.confidence = answer.confidence.score
        facts.hide_answer_if_escalated = True
        blocks: list[Block] = []
        if answer.grounded and answer.answer_text:
            blocks = [TextBlock(type="TEXT", text=answer.answer_text), self._citations(answer)]
            if intent is Intent.PQRS:
                blocks.append(
                    QuickRepliesBlock(
                        type="QUICK_REPLIES",
                        options=[
                            QuickReplyOption(id="equipo_legal", title="Contactar al equipo legal"),
                            ADVISOR,
                        ],
                    )
                )
            else:
                blocks.append(
                    QuickRepliesBlock(
                        type="QUICK_REPLIES",
                        options=[QuickReplyOption(id="cotizar", title="Cotizar un envío"), ADVISOR],
                    )
                )
        return TurnOutcome(
            blocks,
            intent,
            services=[ServiceName.RAG],
            evidence=[c.chunk_id for c in answer.citations],
            # The LLM is called only when retrieval found usable context.
            llm_used=answer.retrieval.get("rechazo") != "sin_contexto",
            confidence=answer.confidence.score,
        )

    # --- escalation ------------------------------------------------------------

    async def _apply_escalation(
        self,
        state: ConversationState,
        outcome: TurnOutcome,
        text: str,
        facts: _Facts,
        history: list[HistorialItem],
    ) -> None:
        decision = outcome.escalation
        if decision is None:
            shipment = facts.shipment
            cargo = shipment.especificaciones_carga if shipment else None
            account = await self._accounts.get_account(shipment.remitente.nit) if shipment else None
            decision = self._escalation.evaluate(
                EscalationInput(
                    intent=outcome.intent,
                    user_text_normalized=text,
                    confidence_score=facts.confidence,
                    sentiment=state.sentiment,
                    valor_declarado_cop=cargo.declarado_cop if cargo else None,
                    valor_reclamacion_cop=facts.claim_cop,
                    envios_mensuales_cuenta=account.envios_mensuales_cuenta if account else None,
                    tipo_cuenta=account.tipo_cuenta if account else None,
                    mercancia_peligrosa=cargo.mercancia_peligrosa if cargo else None,
                    requiere_refrigeracion=cargo.requiere_refrigeracion if cargo else None,
                    estado_guia=shipment.estado_actual if shipment else None,
                    intentos_entrega_fallidos=shipment.intentos_entrega_fallidos
                    if shipment
                    else None,
                    incident_reported=facts.incident,
                )
            )
        if ServiceName.ESCALATION not in outcome.services:
            outcome.services.append(ServiceName.ESCALATION)
        outcome.escalation = decision
        offer = any(t.accion is EscalationAction.OFRECER_HANDOFF for t in decision.triggers)
        if not decision.escalate or decision.primary is None:
            if offer and not any(isinstance(b, QuickRepliesBlock) for b in outcome.blocks):
                outcome.blocks.append(QuickRepliesBlock(type="QUICK_REPLIES", options=[ADVISOR]))
            if not outcome.blocks:
                outcome.blocks.append(TextBlock(type="TEXT", text="¿En qué más puedo ayudarte?"))
            return
        if facts.hide_answer_if_escalated and any(
            t.id_criterio == "CRIT-001" for t in decision.triggers
        ):
            # Not enough evidence: nothing is affirmed (CRIT-001); the case goes to a person.
            outcome.blocks = []
        payload = await self._handoffs.create(
            state,
            decision,
            history=history,
            intent=outcome.intent,
            estado_guia=facts.shipment.estado_actual if facts.shipment else None,
        )
        state.status = ConversationStatus.ESCALATED
        state.handoff_id = payload.handoff_id
        outcome.blocks = [b for b in outcome.blocks if not isinstance(b, QuickRepliesBlock)]
        outcome.blocks.append(
            HandoffNoticeBlock(
                type="HANDOFF_NOTICE",
                handoff_id=payload.handoff_id,
                prioridad=payload.prioridad,
                cola=payload.cola_destino,
                mensaje_cliente=decision.mensaje_cliente or "Te comunicamos con un asesor.",
                tiempo_espera_estimado_segundos=payload.tiempo_espera_estimado_segundos,
            )
        )

    async def _still_waiting(self, state: ConversationState) -> TurnOutcome | None:
        handoff = await self._handoffs.get(state.handoff_id) if state.handoff_id else None
        if handoff is None or handoff.status is HandoffStatus.CLOSED:
            state.status = ConversationStatus.ACTIVE
            state.handoff_id = None
            return None
        return TurnOutcome(
            [
                TextBlock(
                    type="TEXT",
                    # Audit 2026-11-04 (R1): no advisor→customer channel exists yet (CR-007),
                    # so the text no longer promises a reply in this chat.
                    text="Tu caso ya quedó registrado y está en la cola de asesores de TransLogix.",
                )
            ],
            state.active_intent or Intent.HABLAR_CON_HUMANO,
            services=[ServiceName.ESCALATION],
            evidence=[str(handoff.handoff_id)],
        )

    # --- helpers -----------------------------------------------------------------

    @staticmethod
    def _text_of(msg: InboundMessage) -> str:
        reply_id = msg.content.interactive_reply_id
        if reply_id:
            return INTERACTIVE_TEXT.get(reply_id, msg.content.text or "")
        return msg.content.text or ""

    @staticmethod
    def _novedad(folded: str) -> TipoNovedad | None:
        if any(w in folded for w in ("saque", "robaron", "falta la", "faltan", "falta mercancia")):
            return TipoNovedad.SAQUEO
        if any(w in folded for w in ("perdid", "extravi", "no llego", "nunca llego")):
            return TipoNovedad.PERDIDA_TOTAL
        if any(
            w in folded
            for w in ("rot", "danad", "averi", "aplastad", "golpead", "mojad", "quebrad")
        ):
            return TipoNovedad.AVERIA_NO_APARENTE
        return None

    @staticmethod
    def _citations(answer: RagAnswer) -> CitationsBlock:
        return CitationsBlock(
            type="CITATIONS",
            items=[
                CitationItem(
                    doc_code=c.doc_code,
                    doc_title=c.doc_title,
                    section_path=c.section_path,
                    chunk_id=c.chunk_id,
                    excerpt=c.excerpt,
                )
                for c in answer.citations
            ],
        )

    @staticmethod
    def _rag_unavailable(intent: Intent) -> TurnOutcome:
        return TurnOutcome(
            [
                TextBlock(
                    type="TEXT",
                    text="En este momento no puedo consultar la base documental (servicio de IA no disponible). Si quieres, te comunico con un asesor.",
                ),
                QuickRepliesBlock(type="QUICK_REPLIES", options=[ADVISOR]),
            ],
            intent,
            services=[ServiceName.RAG],
            evidence=["error:LLM_NO_DISPONIBLE"],
        )

    @staticmethod
    def _system_event(state: ConversationState, event: SystemEventCode | None) -> TurnOutcome:
        if event is SystemEventCode.CALL_ENDED:
            state.status = ConversationStatus.CLOSED
            return TurnOutcome(
                [TextBlock(type="TEXT", text="Gracias por comunicarte con TransLogix.")],
                Intent.SALUDO,
            )
        if event is SystemEventCode.CALL_TRANSFERRED:
            return TurnOutcome(
                [TextBlock(type="TEXT", text="Transferencia a asesor registrada.")],
                Intent.HABLAR_CON_HUMANO,
            )
        return TurnOutcome(
            [
                TextBlock(
                    type="TEXT",
                    text="Bienvenido a TransLogix. Puedes decirme tu número de guía, pedir una cotización o hablar con un asesor.",
                ),
                QuickRepliesBlock(type="QUICK_REPLIES", options=MENU),
            ],
            Intent.SALUDO,
        )


def history_item(role: str, text: str, at: Any, channel: Any) -> HistorialItem:
    return HistorialItem(rol=role, texto=text, timestamp=at, canal=channel)  # type: ignore[arg-type]


__all__ = ["DialogueOrchestrator", "Sentiment", "TurnOutcome", "history_item"]
