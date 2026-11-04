from enum import StrEnum
from typing import Any

from pydantic import UUID4, Field

from app.contracts.common import BogotaDatetime, ChannelType, ContractModel, NumeroGuia, Score


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    AWAITING_USER = "AWAITING_USER"
    IDLE = "IDLE"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class Intent(StrEnum):
    TRACKING = "TRACKING"
    COTIZACION = "COTIZACION"
    CONSULTA_DOCUMENTAL = "CONSULTA_DOCUMENTAL"
    CARGA_ESPECIAL = "CARGA_ESPECIAL"
    PQRS = "PQRS"
    SALUDO = "SALUDO"
    HABLAR_CON_HUMANO = "HABLAR_CON_HUMANO"
    DESCONOCIDA = "DESCONOCIDA"


class Sentiment(StrEnum):
    POSITIVO = "POSITIVO"
    NEUTRO = "NEUTRO"
    NEGATIVO = "NEGATIVO"
    MUY_NEGATIVO = "MUY_NEGATIVO"


class ConversationState(ContractModel):
    session_id: UUID4
    channel: ChannelType = Field(description="Canal de origen, persistido (DEC-CH1).")
    external_user_id_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ConversationStatus
    active_intent: Intent | None = None
    slots: dict[str, Any] = Field(
        default_factory=dict, examples=[{"cotizacion": {"origen": "Bogotá D.C."}}]
    )
    numero_guia_consultada: NumeroGuia | None = None
    confidence_scores: list[Score] = Field(
        default_factory=list, description="Solo turnos respondidos con RAG."
    )
    sentiment: Sentiment = Sentiment.NEUTRO
    fallback_count: int = Field(default=0, ge=0, description="= intentos_previos del handoff.")
    handoff_id: UUID4 | None = None
    created_at: BogotaDatetime
    updated_at: BogotaDatetime
