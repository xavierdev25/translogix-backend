"""Web Chat HTTP models (``POST /api/v1/chat/messages`` and session history)."""

from enum import StrEnum
from typing import Self

from pydantic import UUID4, Field, model_validator

from app.contracts.common import LOCALE, BogotaDatetime, ChannelType, ContractModel, DeliveryMode
from app.contracts.conversation import ConversationStatus
from app.contracts.inbound import MessageType
from app.contracts.pqrs import DocumentoCodigo
from app.contracts.reply import Block, ConversationReply


class AttachmentRef(ContractModel):
    """Attachment already stored by the platform, referenced by id."""

    attachment_id: str = Field(min_length=1, max_length=64)
    declared_doc_code: DocumentoCodigo | None = None


class ChatMessageRequest(ContractModel):
    session_id: UUID4 | None = None
    client_id: UUID4 = Field(description="UUID estable del navegador (external_user_id).")
    message_id: UUID4 = Field(description="Idempotencia: reenviar el mismo id no reprocesa.")
    text: str | None = Field(default=None, max_length=4096)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    locale: str = LOCALE

    @model_validator(mode="after")
    def _has_content(self) -> Self:
        if not (self.text and self.text.strip()) and not self.attachments:
            raise ValueError("a message needs text or at least one attachment")
        return self


class ChatMessageResponse(ContractModel):
    reply: ConversationReply


class MessageDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class HistoryEntry(ContractModel):
    message_id: str
    direction: MessageDirection
    message_type: MessageType | None = Field(description="Solo mensajes entrantes.")
    text: str | None
    blocks: list[Block] | None = Field(description="Solo respuestas (OUTBOUND).")
    delivery_mode: DeliveryMode
    created_at: BogotaDatetime


class SessionHistory(ContractModel):
    session_id: UUID4
    channel: ChannelType
    status: ConversationStatus
    messages: list[HistoryEntry] = Field(description="En orden cronológico.")
