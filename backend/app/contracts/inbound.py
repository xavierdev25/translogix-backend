"""``InboundMessage``: the only input the conversational core accepts.

Adapters translate their provider's payload into this model. The core never
sees Meta, the PBX or the browser: channel ``metadata`` is limited to the keys
allowed per channel and is for traceability and rendering only.
"""

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import UUID4, Field, field_validator, model_validator

from app.contracts.common import (
    LOCALE,
    BogotaDatetime,
    ChannelType,
    ContractModel,
    DeliveryMode,
    Score,
)
from app.contracts.pqrs import DocumentoCodigo


class MessageType(StrEnum):
    TEXT = "TEXT"
    AUDIO_TRANSCRIPT = "AUDIO_TRANSCRIPT"
    IMAGE = "IMAGE"
    DOCUMENT = "DOCUMENT"
    INTERACTIVE_REPLY = "INTERACTIVE_REPLY"
    SYSTEM_EVENT = "SYSTEM_EVENT"


class SystemEventCode(StrEnum):
    SESSION_OPENED = "SESSION_OPENED"
    CALL_STARTED = "CALL_STARTED"
    CALL_ENDED = "CALL_ENDED"
    CALL_TRANSFERRED = "CALL_TRANSFERRED"


class Attachment(ContractModel):
    attachment_id: str = Field(min_length=1, max_length=64)
    kind: Literal["IMAGE", "DOCUMENT", "AUDIO"]
    mime_type: str = Field(pattern=r"^[a-z]+/[a-z0-9.+-]+$", examples=["application/pdf"])
    size_bytes: int = Field(ge=0)
    storage_ref: str | None = Field(
        default=None, description="Referencia a almacenamiento local; nunca la URL del proveedor."
    )
    declared_doc_code: DocumentoCodigo | None = None

    @field_validator("storage_ref")
    @classmethod
    def _not_a_provider_url(cls, value: str | None) -> str | None:
        if value is not None and value.lower().startswith(("http://", "https://")):
            raise ValueError("storage_ref must be a local reference, not a provider URL")
        return value


class AsrInfo(ContractModel):
    transcript_confidence: Score | None = None
    language: str = LOCALE
    provider: str = Field(examples=["simulated"])


class MessageContent(ContractModel):
    text: str | None = Field(default=None, max_length=4096)
    attachments: list[Attachment] = Field(default_factory=list)
    interactive_reply_id: str | None = None
    asr: AsrInfo | None = None
    system_event: SystemEventCode | None = Field(
        default=None, description="Solo con message_type=SYSTEM_EVENT."
    )


class WhatsAppMetadata(ContractModel):
    provider: Literal["meta_cloud_api"] = "meta_cloud_api"
    wa_message_type: str | None = None
    phone_number_id: str | None = None
    profile_name: str | None = Field(default=None, description="PII: no se registra en logs.")


class WebChatMetadata(ContractModel):
    user_agent: str | None = None
    locale: str | None = None
    client_version: str | None = None


class PbxMetadata(ContractModel):
    call_id: str | None = None
    ani_hash: str | None = None
    dnis: str | None = None
    turn_index: int | None = Field(default=None, ge=0)
    silence_ms: int | None = Field(default=None, ge=0)
    barge_in: bool | None = None


CHANNEL_METADATA_MODELS: dict[ChannelType, type[ContractModel]] = {
    ChannelType.WHATSAPP: WhatsAppMetadata,
    ChannelType.WEB_CHAT: WebChatMetadata,
    ChannelType.PBX: PbxMetadata,
}


def pbx_message_id(call_id: str, turn_index: int) -> str:
    """Idempotency key of a PBX turn (DEC-C4): the PBX sends no message ids."""
    return f"{call_id}:{turn_index}"


class InboundMessage(ContractModel):
    channel: ChannelType
    message_id: str = Field(
        min_length=1, max_length=256, description="Idempotencia: UNIQUE(channel, message_id)."
    )
    external_user_id: str = Field(
        min_length=1,
        max_length=256,
        repr=False,
        description="En claro solo en memoria del adaptador; se persiste hasheado.",
    )
    session_id: UUID4 | None = None
    message_type: MessageType
    content: MessageContent
    timestamp: BogotaDatetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    delivery_mode: DeliveryMode

    @property
    def idempotency_key(self) -> tuple[ChannelType, str]:
        return (self.channel, self.message_id)

    @model_validator(mode="after")
    def _metadata_allowed_for_channel(self) -> Self:
        CHANNEL_METADATA_MODELS[self.channel].model_validate(self.metadata)
        return self

    @model_validator(mode="after")
    def _content_matches_type(self) -> Self:
        content, kind = self.content, self.message_type
        if kind in (MessageType.TEXT, MessageType.AUDIO_TRANSCRIPT) and not content.text:
            raise ValueError(f"{kind} requires content.text")
        if kind is MessageType.AUDIO_TRANSCRIPT and content.asr is None:
            raise ValueError("AUDIO_TRANSCRIPT requires content.asr")
        if kind in (MessageType.IMAGE, MessageType.DOCUMENT) and not any(
            attachment.kind == kind.value for attachment in content.attachments
        ):
            raise ValueError(f"{kind} requires at least one {kind} attachment")
        if kind is MessageType.INTERACTIVE_REPLY and not content.interactive_reply_id:
            raise ValueError("INTERACTIVE_REPLY requires content.interactive_reply_id")
        if (kind is MessageType.SYSTEM_EVENT) != (content.system_event is not None):
            raise ValueError("content.system_event is required for, and only for, SYSTEM_EVENT")
        return self
