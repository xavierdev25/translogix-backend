"""Operational endpoints: health, readiness, channel metrics, RAG reindex, handoff inbox."""

from typing import Literal

from pydantic import Field

from app.contracts.common import BogotaDatetime, ChannelType, ContractModel
from app.contracts.escalation import HandoffSummary
from app.contracts.rag import DocCode

ADMIN_TOKEN_HEADER = "X-Admin-Token"  # noqa: S105 - header name, not a secret


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"


class ReadinessChecks(ContractModel):
    database: Literal["ok", "fail"]
    ollama: Literal["ok", "fail", "disabled"]
    rag_index: Literal["ok", "empty", "fail"]


class ReadinessResponse(ContractModel):
    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks


class ChannelCounters(ContractModel):
    channel: ChannelType
    conversaciones: int = Field(ge=0)
    mensajes_entrantes: int = Field(ge=0)
    mensajes_salientes: int = Field(ge=0)
    handoffs: int = Field(ge=0)


class ChannelMetricsResponse(ContractModel):
    generado_en: BogotaDatetime
    desde: BogotaDatetime | None
    hasta: BogotaDatetime | None
    canales: list[ChannelCounters] = Field(description="Una fila por canal, incluso en cero.")


class ReindexRequest(ContractModel):
    doc_codes: list[DocCode] | None = Field(default=None, description="null = todos.")
    force: bool = Field(default=False, description="Reindexar aunque el contenido no cambie.")


class ReindexResponse(ContractModel):
    status: Literal["COMPLETED"] = "COMPLETED"
    documentos: int = Field(ge=0)
    chunks: int = Field(ge=0)
    omitidos_sin_cambios: int = Field(ge=0)
    duracion_ms: int = Field(ge=0)
    embed_model: str
    embed_dim: int = Field(gt=0)


class HandoffListResponse(ContractModel):
    items: list[HandoffSummary]
