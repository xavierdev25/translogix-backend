"""Events pushed on ``WS /ws/chat/{session_id}``.

OpenAPI cannot describe WebSockets, so these schemas are published under
``components.schemas`` and referenced from the ``x-websockets`` extension.
"""

from typing import Annotated, Literal

from pydantic import UUID4, Field, TypeAdapter

from app.contracts.common import ContractModel
from app.contracts.escalation import ColaDestino, HandoffStatus, Prioridad
from app.contracts.reply import ConversationReply

WS_CHAT_PATH = "/ws/chat/{session_id}"


class WsMessageEvent(ContractModel):
    type: Literal["message"] = "message"
    session_id: UUID4
    reply: ConversationReply


class WsHandoffStatusEvent(ContractModel):
    type: Literal["handoff_status"] = "handoff_status"
    session_id: UUID4
    handoff_id: UUID4
    status: HandoffStatus
    prioridad: Prioridad
    cola: ColaDestino
    tiempo_espera_estimado_segundos: int | None = Field(default=None, ge=0)


WsEvent = Annotated[WsMessageEvent | WsHandoffStatusEvent, Field(discriminator="type")]
WS_EVENT_ADAPTER: TypeAdapter[WsMessageEvent | WsHandoffStatusEvent] = TypeAdapter(WsEvent)
