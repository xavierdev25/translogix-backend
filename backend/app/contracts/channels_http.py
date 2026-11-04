"""HTTP models of the WhatsApp and PBX channel endpoints."""

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.common import ContractModel, DeliveryMode, Score
from app.contracts.ports import ChannelOutbound, DeliveryResult
from app.contracts.reply import ConversationReply

PBX_MAX_WORDS_PER_TURN = 40
_NOT_SPEAKABLE = re.compile(r"\d|[*#`_~\[\]]|https?://|www\.|[\U0001F300-\U0001FAFF☀-➿]")


class ProviderModel(BaseModel):
    """Payload owned by a provider: unknown fields are kept, never rejected."""

    model_config = ConfigDict(
        extra="allow", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )


class WaText(ProviderModel):
    body: str


class WaInteractiveReply(ProviderModel):
    id: str
    title: str
    description: str | None = None


class WaInteractive(ProviderModel):
    type: str = Field(examples=["button_reply", "list_reply"])
    button_reply: WaInteractiveReply | None = None
    list_reply: WaInteractiveReply | None = None


class WaMedia(ProviderModel):
    id: str
    mime_type: str | None = None
    sha256: str | None = None
    caption: str | None = None
    filename: str | None = None


class WaMessage(ProviderModel):
    from_: str = Field(alias="from", description="wa_id del cliente (PII).")
    id: str = Field(description="wamid: clave de idempotencia con el canal.")
    timestamp: str = Field(description="Segundos Unix, como string.")
    type: str = Field(examples=["text", "interactive", "image", "document", "audio"])
    text: WaText | None = None
    interactive: WaInteractive | None = None
    image: WaMedia | None = None
    document: WaMedia | None = None
    audio: WaMedia | None = None


class WaProfile(ProviderModel):
    name: str | None = Field(default=None, description="PII: no se registra en logs.")


class WaContact(ProviderModel):
    profile: WaProfile | None = None
    wa_id: str


class WaMetadata(ProviderModel):
    display_phone_number: str
    phone_number_id: str


class WaValue(ProviderModel):
    messaging_product: Literal["whatsapp"]
    metadata: WaMetadata
    contacts: list[WaContact] = Field(default_factory=list)
    messages: list[WaMessage] = Field(default_factory=list)
    statuses: list[dict[str, Any]] = Field(
        default_factory=list, description="Recibos de entrega; no son mensajes del cliente."
    )


class WaChange(ProviderModel):
    field: str = Field(examples=["messages"])
    value: WaValue


class WaEntry(ProviderModel):
    id: str
    changes: list[WaChange]


class WhatsAppWebhookPayload(ProviderModel):
    """Body of the Meta Cloud API webhook; the simulation endpoint accepts the same body.

    Structure taken from Meta's webhook format; **validate against Meta's current
    documentation** before going live. Unknown fields are preserved.
    """

    object: Literal["whatsapp_business_account"]
    entry: list[WaEntry]


class WebhookAck(ContractModel):
    """Fast acknowledgement for Meta; processing happens after parsing."""

    status: Literal["RECEIVED"] = "RECEIVED"
    messages: int = Field(ge=0, description="Mensajes del payload aceptados.")
    duplicates: int = Field(ge=0, description="Ignorados por idempotencia.")


class ChannelTurnResult(ContractModel):
    in_reply_to: str
    reply: ConversationReply
    outbound: ChannelOutbound
    delivery: DeliveryResult


class WhatsAppSimulationResult(ContractModel):
    delivery_mode: Literal[DeliveryMode.SIMULATED] = DeliveryMode.SIMULATED
    results: list[ChannelTurnResult]


class PbxTurnRequest(ContractModel):
    """One call turn. ``message_id`` is derived as ``<call_id>:<turn_index>`` (DEC-C4)."""

    call_id: str = Field(min_length=1, max_length=128)
    caller_id: str = Field(
        min_length=1, max_length=32, repr=False, description="ANI en claro; se hashea."
    )
    turn_index: int = Field(ge=0)
    event: Literal["CALL_STARTED", "TURN", "CALL_ENDED"] = "TURN"
    transcript: str | None = Field(default=None, max_length=2000)
    audio_ref: str | None = None
    transcript_confidence: Score | None = None
    dnis: str | None = None
    silence_ms: int | None = Field(default=None, ge=0)
    barge_in: bool = False

    @model_validator(mode="after")
    def _turn_has_one_input(self) -> Self:
        if self.event == "TURN" and (self.transcript is None) == (self.audio_ref is None):
            raise ValueError("a TURN needs exactly one of 'transcript' or 'audio_ref'")
        return self


class PbxSimulateRequest(ContractModel):
    call_id: str = Field(min_length=1, max_length=128)
    transcript: str = Field(min_length=1, max_length=2000)
    turn_index: int = Field(default=0, ge=0)
    caller_id: str = Field(default="simulated-caller", max_length=32, repr=False)


class PbxTurnResponse(ContractModel):
    reply: ConversationReply
    speech_text: str = Field(
        description=f"Texto hablable, sin Markdown, emojis ni URLs; ≤ ~{PBX_MAX_WORDS_PER_TURN} "
        "palabras."
    )
    transfer: bool = Field(description="True si el turno termina en transferencia a asesor.")
    delivery: DeliveryResult

    @field_validator("speech_text")
    @classmethod
    def _is_speakable(cls, value: str) -> str:
        """Digits are spelled out (guías, amounts, times); no Markdown, URLs or emoji."""
        if len(value.split()) > PBX_MAX_WORDS_PER_TURN:
            raise ValueError(f"a spoken turn has at most {PBX_MAX_WORDS_PER_TURN} words")
        if _NOT_SPEAKABLE.search(value):
            raise ValueError("speech_text must not contain digits, Markdown, URLs or emoji")
        return value
