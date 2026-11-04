"""Human handoff (P07): ``flujo_transferencia_pwa`` payload, persisted, with an advisor inbox."""

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

from app.contracts.common import ContractError, ErrorCode
from app.contracts.conversation import ConversationState, Intent
from app.contracts.escalation import (
    DEFAULT_ACTION_ROUTING,
    EscalationAction,
    EscalationDecision,
    HandoffPayload,
    HandoffRoute,
    HandoffStatus,
    HandoffSummary,
    HistorialItem,
)
from app.contracts.pii import redact_pii
from app.contracts.ports import Clock
from app.db.repositories import HandoffRepository

_TRANSITIONS = {
    HandoffStatus.PENDING: {HandoffStatus.ASSIGNED, HandoffStatus.CLOSED},
    HandoffStatus.ASSIGNED: {HandoffStatus.CLOSED},
    HandoffStatus.CLOSED: set(),
}


def summarize(
    state: ConversationState,
    decision: EscalationDecision,
    *,
    intent: Intent | None,
    estado_guia: str | None,
    history: Sequence[HistorialItem],
) -> str:
    """Deterministic, PII-free summary for the advisor (an LLM polish would be P1)."""
    primary = decision.primary
    parts = [
        f"Intención: {intent.value if intent else 'desconocida'}.",
        f"Canal: {state.channel.value}.",
        f"Guía: {state.numero_guia_consultada or 'no indicada'}"
        + (f" (estado TMS {estado_guia})." if estado_guia else "."),
    ]
    if primary:
        parts.append(f"Motivo: {primary.id_criterio} — {primary.evidencia}.")
    others = [t.id_criterio for t in decision.triggers if t is not primary]
    if others:
        parts.append(f"Otros criterios: {', '.join(others)}.")
    parts.append(f"Sentimiento: {state.sentiment.value}. Intentos previos: {state.fallback_count}.")
    last_user = next((h.texto for h in reversed(history) if h.rol == "USUARIO"), None)
    if last_user:
        parts.append(f"Último mensaje del cliente: «{redact_pii(last_user)[:200]}».")
    return " ".join(parts)


class HandoffService:
    """``HandoffPort`` backed by the ``handoffs`` table."""

    def __init__(
        self,
        repo: HandoffRepository,
        clock: Clock,
        *,
        wait_seconds: int,
        routing: Mapping[EscalationAction, HandoffRoute] = DEFAULT_ACTION_ROUTING,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._wait = wait_seconds
        self._routing = routing

    async def create(
        self,
        state: ConversationState,
        decision: EscalationDecision,
        *,
        history: Sequence[HistorialItem] = (),
        intent: Intent | None = None,
        estado_guia: str | None = None,
    ) -> HandoffPayload:
        if not decision.escalate or decision.primary is None:
            raise ContractError(
                ErrorCode.VALIDACION, "Solo se crea un handoff cuando hay escalamiento."
            )
        route = self._routing[decision.primary.accion]
        scores = state.confidence_scores
        payload = HandoffPayload(
            handoff_id=uuid4(),
            session_id=state.session_id,
            canal=state.channel,
            numero_guia_consultada=state.numero_guia_consultada,
            confidence_score_promedio=round(sum(scores) / len(scores), 4) if scores else None,
            sentimiento_detectado=state.sentiment,
            intentos_previos=state.fallback_count,
            resumen_contexto=summarize(
                state, decision, intent=intent, estado_guia=estado_guia, history=history
            ),
            historial=list(history),
            prioridad=route.prioridad,
            cola_destino=route.cola,
            triggers=[
                t for t in decision.triggers if t.accion is not EscalationAction.OFRECER_HANDOFF
            ],
            tiempo_espera_estimado_segundos=self._wait,
            status=HandoffStatus.PENDING,
            created_at=self._clock.now(),
        )
        await self._repo.add(payload)
        return payload

    async def get(self, handoff_id: UUID) -> HandoffPayload | None:
        return await self._repo.get(handoff_id)

    async def list_by_status(self, status: HandoffStatus | None = None) -> Sequence[HandoffSummary]:
        return await self._repo.list_by_status(status)

    async def update_status(self, handoff_id: UUID, status: HandoffStatus) -> HandoffPayload | None:
        current = await self._repo.get(handoff_id)
        if current is None:
            return None
        if status not in _TRANSITIONS[current.status]:
            raise ContractError(
                ErrorCode.VALIDACION,
                f"Transición no permitida: {current.status.value} → {status.value}.",
            )
        updated = current.model_copy(update={"status": status})
        await self._repo.update(updated)
        return updated
