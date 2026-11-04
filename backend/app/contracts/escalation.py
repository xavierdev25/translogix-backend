"""Escalation rules I/O and the human handoff payload.

The rules engine itself is owned by another prompt; this module fixes what it
receives (``EscalationInput``), what it returns (``EscalationDecision``) and the
default routing of each action to a queue and priority.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, NamedTuple, Self

from pydantic import UUID4, Field, model_validator

from app.contracts.common import (
    BogotaDatetime,
    ChannelType,
    ClientSafeText,
    ContractModel,
    Cop,
    NumeroGuia,
    Score,
)
from app.contracts.conversation import Intent, Sentiment

RULES_VERSION = "1.0.2026"


class EscalationAction(StrEnum):
    ESCALAR_A_HUMANO = "ESCALAR_A_HUMANO"
    ESCALAR_PRIORIDAD_ALTA = "ESCALAR_PRIORIDAD_ALTA"
    ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS = "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS"
    ESCALAR_A_EJECUTIVO_CUENTA_DEDICADO = "ESCALAR_A_EJECUTIVO_CUENTA_DEDICADO"
    ESCALAR_URGENCIA_OPERACIONES = "ESCALAR_URGENCIA_OPERACIONES"
    OFRECER_HANDOFF = "OFRECER_HANDOFF"  # non-binding (DEC-E4)


def parse_action(raw: str) -> EscalationAction:
    """Canonical action for a value read from the rules JSON.

    The source file spells one action ``ESCALAR_Urgencia_OPERACIONES``; the raw
    spelling is kept in ``EscalationTrigger.accion_raw``.
    """
    return EscalationAction(raw.strip().upper())


class Prioridad(StrEnum):
    NORMAL = "NORMAL"
    ALTA = "ALTA"
    URGENTE = "URGENTE"


class ColaDestino(StrEnum):
    SERVICIO_CLIENTE = "SERVICIO_CLIENTE"
    SUPERVISOR = "SUPERVISOR"
    JURIDICO_PQRS = "JURIDICO_PQRS"
    EJECUTIVO_CUENTA = "EJECUTIVO_CUENTA"
    OPERACIONES = "OPERACIONES"


class HandoffRoute(NamedTuple):
    cola: ColaDestino
    prioridad: Prioridad


# Default, overridable by configuration. OFRECER_HANDOFF creates no handoff.
DEFAULT_ACTION_ROUTING: Mapping[EscalationAction, HandoffRoute] = MappingProxyType(
    {
        EscalationAction.ESCALAR_A_HUMANO: HandoffRoute(
            ColaDestino.SERVICIO_CLIENTE, Prioridad.NORMAL
        ),
        EscalationAction.ESCALAR_PRIORIDAD_ALTA: HandoffRoute(
            ColaDestino.SUPERVISOR, Prioridad.ALTA
        ),
        EscalationAction.ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS: HandoffRoute(
            ColaDestino.JURIDICO_PQRS, Prioridad.ALTA
        ),
        EscalationAction.ESCALAR_A_EJECUTIVO_CUENTA_DEDICADO: HandoffRoute(
            ColaDestino.EJECUTIVO_CUENTA, Prioridad.ALTA
        ),
        EscalationAction.ESCALAR_URGENCIA_OPERACIONES: HandoffRoute(
            ColaDestino.OPERACIONES, Prioridad.URGENTE
        ),
    }
)

CriterionId = Literal["CRIT-001", "CRIT-002", "CRIT-003", "CRIT-004", "CRIT-005", "INFO-INTENTOS"]

# Which trigger becomes primary when several fire (DEC-E7, provisional): most
# severe queue first. Recorded in EscalationDecision.config_snapshot.
DEFAULT_PRIORITY_ORDER: tuple[CriterionId, ...] = (
    "CRIT-005",
    "CRIT-003",
    "CRIT-002",
    "CRIT-004",
    "CRIT-001",
    "INFO-INTENTOS",
)


class EscalationInput(ContractModel):
    """Pure input of the rules engine: everything a rule may look at, nothing else."""

    intent: Intent
    user_text_normalized: str
    confidence_score: Score | None = Field(
        default=None, description="Solo si hubo respuesta RAG en el turno."
    )
    sentiment: Sentiment
    valor_declarado_cop: Cop | None = Field(default=None, description="De la guía asociada.")
    valor_reclamacion_cop: Cop | None = None
    envios_mensuales_cuenta: int | None = Field(default=None, ge=0)
    tipo_cuenta: str | None = None
    mercancia_peligrosa: bool | None = None
    requiere_refrigeracion: bool | None = None
    estado_guia: str | None = None
    intentos_entrega_fallidos: int | None = Field(default=None, ge=0)
    incident_reported: bool = Field(
        description=(
            "El usuario reporta un incidente. En CRIT005_MODE=incident_context también cuenta "
            "como incidente un estado de novedad de la guía, p. ej. RETENIDO_NOVEDAD (DEC-E3)."
        )
    )


class EscalationTrigger(ContractModel):
    id_criterio: CriterionId
    accion: EscalationAction
    accion_raw: str = Field(description="Acción tal como aparece en el JSON de reglas.")
    evidencia: ClientSafeText = Field(description="Qué dato disparó la regla, sin PII.")
    modo: str = Field(examples=["claim_context", "incident_context", "literal"])


class EscalationDecision(ContractModel):
    escalate: bool
    primary: EscalationTrigger | None
    triggers: list[EscalationTrigger]
    mensaje_cliente: str | None = Field(description="Texto del JSON para el criterio primario.")
    rules_version: str = RULES_VERSION
    config_snapshot: dict[str, str] = Field(
        examples=[{"CRIT003_MODE": "claim_context", "CRIT005_MODE": "incident_context"}]
    )

    @model_validator(mode="after")
    def _primary_matches_decision(self) -> Self:
        if self.escalate:
            if self.primary is None or self.primary not in self.triggers:
                raise ValueError("an escalation needs a primary trigger taken from 'triggers'")
            if self.mensaje_cliente is None:
                raise ValueError("an escalation must carry the customer message")
        elif self.primary is not None:
            raise ValueError("'primary' must be null when escalate is false")
        return self


class HandoffStatus(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    CLOSED = "CLOSED"


class HistorialItem(ContractModel):
    rol: Literal["USUARIO", "ASISTENTE", "ASESOR", "SISTEMA"]
    texto: str
    timestamp: BogotaDatetime
    canal: ChannelType


class HandoffPayload(ContractModel):
    """``flujo_transferencia_pwa`` of the rules JSON plus routing extensions."""

    handoff_id: UUID4
    session_id: UUID4
    canal: ChannelType
    numero_guia_consultada: NumeroGuia | None
    confidence_score_promedio: Score | None
    sentimiento_detectado: Sentiment
    intentos_previos: int = Field(ge=0, description="= ConversationState.fallback_count.")
    resumen_contexto: str = Field(
        min_length=1, description="Generado por IA, con revisión determinística de campos."
    )
    historial: list[HistorialItem]
    prioridad: Prioridad
    cola_destino: ColaDestino
    triggers: list[EscalationTrigger] = Field(min_length=1)
    tiempo_espera_estimado_segundos: int = Field(
        ge=0, description="Configurable (HANDOFF_WAIT_SECONDS); valor del JSON, no es un SLA."
    )
    status: HandoffStatus
    created_at: BogotaDatetime


class HandoffStatusUpdate(ContractModel):
    """Advisor inbox action (``PATCH /api/v1/handoffs/{id}``, P1): PENDING → ASSIGNED → CLOSED."""

    status: Literal[HandoffStatus.ASSIGNED, HandoffStatus.CLOSED]


class HandoffSummary(ContractModel):
    """Row of the advisor inbox (``GET /api/v1/handoffs``)."""

    handoff_id: UUID4
    session_id: UUID4
    canal: ChannelType
    prioridad: Prioridad
    cola_destino: ColaDestino
    numero_guia_consultada: NumeroGuia | None
    status: HandoffStatus
    created_at: BogotaDatetime
