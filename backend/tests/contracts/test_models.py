"""Behaviour of the contract models: invariants any implementation must satisfy."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.attachments import AttachmentUploadResponse
from app.contracts.channels_http import PbxTurnRequest, PbxTurnResponse, WhatsAppWebhookPayload
from app.contracts.chat import ChatMessageRequest
from app.contracts.common import (
    DEFAULT_ERROR_MESSAGES,
    ERROR_HTTP_STATUS,
    ContractError,
    ErrorCode,
)
from app.contracts.conversation import ConversationState
from app.contracts.escalation import (
    DEFAULT_ACTION_ROUTING,
    DEFAULT_PRIORITY_ORDER,
    EscalationAction,
    EscalationDecision,
    HandoffStatusUpdate,
    parse_action,
)
from app.contracts.examples import EXAMPLES_DIR
from app.contracts.inbound import InboundMessage, pbx_message_id
from app.contracts.pii import find_pii
from app.contracts.ports import DeliveryResult, NullAsr, NullTts
from app.contracts.pqrs import LiquidacionPreliminar, PqrsTicketRequest, PqrsTicketResponse
from app.contracts.quotation import CalculoFlete, CotizacionRequest, CotizacionResponse
from app.contracts.rag import RagAnswer
from app.contracts.reply import ConversationReply
from app.contracts.tms import TmsShipment
from app.contracts.tracking import EventoVista, TrackingResponse
from app.contracts.ws import WS_EVENT_ADAPTER, WsHandoffStatusEvent

SOURCES = Path(__file__).resolve().parents[3] / "data" / "source"


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES_DIR / f"{name}.json").read_text("utf-8"))
    return data


# --- conventions -----------------------------------------------------------


def test_every_error_code_has_a_status_and_a_spanish_message() -> None:
    assert set(ERROR_HTTP_STATUS) == set(ErrorCode)
    assert set(DEFAULT_ERROR_MESSAGES) == set(ErrorCode)


def test_contract_error_builds_the_envelope() -> None:
    error = ContractError(ErrorCode.GUIA_NO_ENCONTRADA, details={"numero_guia": "TLX-000000-CO"})

    envelope = error.to_envelope("req-1").model_dump(mode="json")

    assert error.http_status == 404
    assert envelope["error"]["message"] == DEFAULT_ERROR_MESSAGES[ErrorCode.GUIA_NO_ENCONTRADA]
    assert envelope["error"]["details"] == {"numero_guia": "TLX-000000-CO"}


def test_datetimes_need_an_offset_and_are_served_in_bogota_time() -> None:
    event = example("tracking.en-transito")["eventos"][0]

    naive = event | {"timestamp": "2026-11-02T16:15:00"}
    utc = EventoVista.model_validate(event | {"timestamp": "2026-11-02T16:15:00Z"})

    with pytest.raises(ValidationError):
        EventoVista.model_validate(naive)
    assert utc.model_dump(mode="json")["timestamp"] == "2026-11-02T11:15:00-05:00"


# --- tracking (DEC-S1) -----------------------------------------------------


def test_tracking_view_cannot_carry_raw_tms_descriptions() -> None:
    dataset = json.loads((SOURCES / "dataset_guias_simuladas_tms.json").read_text("utf-8"))
    raw = [
        event["descripcion"]
        for guia in dataset
        for event in guia["historial_trazabilidad"]
        if find_pii(event["descripcion"])
    ]
    base = example("tracking.en-transito")["eventos"][0]

    for text in raw:
        with pytest.raises(ValidationError, match="personal data"):
            EventoVista.model_validate(base | {"descripcion_cliente": text})


def test_proof_of_delivery_accepts_initials_only() -> None:
    data = example("tracking.entregado")
    full_name = data | {
        "prueba_entrega": data["prueba_entrega"] | {"recibido_por_iniciales": "Roberto Bermúdez"}
    }

    with pytest.raises(ValidationError):
        TrackingResponse.model_validate(full_name)


def test_tracking_rejects_extra_fields_such_as_gps() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        TrackingResponse.model_validate(
            example("tracking.en-transito") | {"ubicacion_gps_actual": {"latitud": 5.07}}
        )


def test_tracking_events_must_be_chronological() -> None:
    data = example("tracking.en-transito")

    with pytest.raises(ValidationError, match="chronological"):
        TrackingResponse.model_validate(data | {"eventos": list(reversed(data["eventos"]))})


def test_internal_tms_record_parses_the_dataset_and_hides_pii_in_repr() -> None:
    dataset = json.loads((SOURCES / "dataset_guias_simuladas_tms.json").read_text("utf-8"))

    shipments = [TmsShipment.model_validate(item) for item in dataset]

    assert [s.numero_guia for s in shipments][:2] == ["TLX-984021-CO", "TLX-752190-CO"]
    rendered = repr(shipments[1])
    assert "16.789.432" not in rendered
    assert "firma_digital_url" not in rendered
    assert "890.987.654-3" not in rendered


# --- quotation -------------------------------------------------------------


def test_quote_total_must_add_up() -> None:
    calculo = example("cotizar.cadena-frio.response")["calculo"]

    CalculoFlete.model_validate(calculo)
    with pytest.raises(ValidationError, match="base \\+ seguro \\+ recargos = 207469"):
        CalculoFlete.model_validate(calculo | {"flete_total_cop": 207470})


def test_minimum_freight_is_a_floor() -> None:
    calculo = example("cotizar.flete-minimo.response")["calculo"]

    CalculoFlete.model_validate(calculo)
    with pytest.raises(ValidationError, match="below flete_minimo_cop"):
        CalculoFlete.model_validate(
            calculo
            | {"flete_base_cop": 9480, "flete_minimo_aplicado": False, "flete_total_cop": 13980}
        )
    with pytest.raises(ValidationError, match="equals flete_minimo_cop"):
        CalculoFlete.model_validate(
            example("cotizar.response")["calculo"] | {"flete_minimo_aplicado": True}
        )


def test_billable_weight_is_never_below_volumetric() -> None:
    calculo = example("cotizar.response")["calculo"]

    with pytest.raises(ValidationError, match="max"):
        CalculoFlete.model_validate(calculo | {"peso_facturable_kg": 29.0})


@pytest.mark.parametrize(
    "change", [{"alto_cm": 0}, {"peso_real_kg": -1}, {"valor_declarado_cop": -5}, {"x": 1}]
)
def test_quote_request_validation(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CotizacionRequest.model_validate(example("cotizar.request") | change)


def test_quote_amounts_are_integers() -> None:
    data = example("cotizar.response")

    with pytest.raises(ValidationError):
        CotizacionResponse.model_validate(
            data | {"calculo": data["calculo"] | {"seguro_cop": 125000.5}}
        )


# --- pqrs ------------------------------------------------------------------


def test_siniestro_requires_guia_novedad_and_amount() -> None:
    request = example("pqrs.siniestro.request")

    for field in ("numero_guia", "tipo_novedad"):
        with pytest.raises(ValidationError, match=field):
            PqrsTicketRequest.model_validate(request | {field: None})
    # The amount is optional: the chat opens the ticket before the customer states it.
    PqrsTicketRequest.model_validate(request | {"valor_reclamado_cop": None})


def test_novedad_only_applies_to_siniestro() -> None:
    with pytest.raises(ValidationError, match="only applies"):
        PqrsTicketRequest.model_validate(
            {"tipo": "QUEJA", "descripcion": "Mal trato", "tipo_novedad": "OTRO"}
        )


def test_missing_documents_are_derived_from_the_checklist() -> None:
    data = example("pqrs.siniestro.response")

    with pytest.raises(ValidationError, match="faltantes"):
        PqrsTicketResponse.model_validate(data | {"faltantes": ["CARTA_RECLAMACION"]})
    with pytest.raises(ValidationError, match="RADICADO"):
        PqrsTicketResponse.model_validate(data | {"estado": "RADICADO"})
    # The S14 claim escalates (CRIT-003); claiming BORRADOR would hide that.
    with pytest.raises(ValidationError, match="ESCALADO"):
        PqrsTicketResponse.model_validate(data | {"estado": "BORRADOR"})
    with pytest.raises(ValidationError, match="ESCALADO"):
        PqrsTicketResponse.model_validate(data | {"escalamiento": None})


def test_deductible_matches_its_percentage() -> None:
    liquidacion = example("pqrs.siniestro.response")["liquidacion_preliminar"]

    with pytest.raises(ValidationError, match="200000"):
        LiquidacionPreliminar.model_validate(liquidacion | {"deducible_cop": 199999})


# --- rag -------------------------------------------------------------------


def test_ungrounded_answers_carry_no_text() -> None:
    data = example("rag.ungrounded")

    with pytest.raises(ValidationError, match="ungrounded"):
        RagAnswer.model_validate(data | {"answer_text": "Sí, se puede."})


def test_grounded_answers_need_a_citation() -> None:
    with pytest.raises(ValidationError, match="citation"):
        RagAnswer.model_validate(example("rag.grounded") | {"citations": []})


def test_confidence_is_declared_not_a_probability() -> None:
    confidence = example("rag.grounded")["confidence"]

    with pytest.raises(ValidationError):
        RagAnswer.model_validate(
            example("rag.grounded") | {"confidence": confidence | {"is_probability": True}}
        )
    with pytest.raises(ValidationError, match="components"):
        RagAnswer.model_validate(
            example("rag.grounded") | {"confidence": confidence | {"components": {"top_sim": 1}}}
        )


# --- escalation ------------------------------------------------------------


def test_escalation_primary_must_be_one_of_the_triggers() -> None:
    data = example("escalation.crit-005")

    with pytest.raises(ValidationError, match="primary"):
        EscalationDecision.model_validate(data | {"triggers": data["triggers"][:1]})
    with pytest.raises(ValidationError, match="null"):
        EscalationDecision.model_validate(data | {"escalate": False})


def test_raw_action_spelling_from_rules_json_is_accepted() -> None:
    rules = json.loads((SOURCES / "reglas_escalacion_humana.json").read_text("utf-8"))

    actions = {parse_action(rule["accion"]) for rule in rules["criterios_escalacion"]}

    assert EscalationAction.ESCALAR_URGENCIA_OPERACIONES in actions
    assert actions <= set(DEFAULT_ACTION_ROUTING)


def test_every_binding_action_has_a_route_and_criteria_have_a_priority() -> None:
    binding = set(EscalationAction) - {EscalationAction.OFRECER_HANDOFF}
    rules = json.loads((SOURCES / "reglas_escalacion_humana.json").read_text("utf-8"))

    assert set(DEFAULT_ACTION_ROUTING) == binding
    assert {rule["id_criterio"] for rule in rules["criterios_escalacion"]} <= set(
        DEFAULT_PRIORITY_ORDER
    )


# --- inbound / reply -------------------------------------------------------


def test_metadata_is_limited_to_the_channel_allowlist() -> None:
    data = example("inbound.whatsapp")

    with pytest.raises(ValidationError, match="Extra inputs"):
        InboundMessage.model_validate(data | {"metadata": data["metadata"] | {"call_id": "x"}})


@pytest.mark.parametrize(
    ("message_type", "content_change", "error"),
    [
        ("TEXT", {"text": None}, "content.text"),
        ("AUDIO_TRANSCRIPT", {"asr": None}, "content.asr"),
        ("IMAGE", {}, "attachment"),
        ("INTERACTIVE_REPLY", {}, "interactive_reply_id"),
        ("SYSTEM_EVENT", {}, "system_event"),
        ("TEXT", {"system_event": "CALL_STARTED"}, "system_event"),
    ],
)
def test_content_must_match_message_type(
    message_type: str, content_change: dict[str, Any], error: str
) -> None:
    data = example("inbound.pbx")
    broken = data | {"message_type": message_type, "content": data["content"] | content_change}

    with pytest.raises(ValidationError, match=error):
        InboundMessage.model_validate(broken)


def test_attachments_reference_local_storage_only() -> None:
    attachment = {
        "attachment_id": "a1",
        "kind": "IMAGE",
        "mime_type": "image/jpeg",
        "size_bytes": 10,
        "storage_ref": "https://lookaside.fbsbx.com/whatsapp_business/attachments/1",
    }
    data = example("inbound.whatsapp")

    with pytest.raises(ValidationError, match="provider URL"):
        InboundMessage.model_validate(
            data | {"content": data["content"] | {"attachments": [attachment]}}
        )


def test_plain_user_id_never_appears_in_repr() -> None:
    message = InboundMessage.model_validate(example("inbound.whatsapp"))

    assert "573000000000" not in repr(message)
    assert message.idempotency_key == (message.channel, "wamid.SIMULATED-0001")


def test_pbx_message_id_is_derived_from_call_and_turn() -> None:
    message = InboundMessage.model_validate(example("inbound.pbx"))

    assert message.message_id == pbx_message_id(
        message.metadata["call_id"], message.metadata["turn_index"]
    )


def test_quick_replies_are_capped_at_three() -> None:
    data = example("chat.tracking.response")["reply"]
    blocks = [*data["blocks"]]
    blocks[2] = blocks[2] | {"options": [*blocks[2]["options"], {"id": "x", "title": "Otra"}]}

    with pytest.raises(ValidationError):
        ConversationReply.model_validate(data | {"blocks": blocks})


def test_handoff_notice_requires_escalated_status() -> None:
    data = example("chat.tracking.response")["reply"]
    notice = {
        "type": "HANDOFF_NOTICE",
        "handoff_id": "7a6b5c4d-3e2f-4a1b-9c8d-7e6f5a4b3c2d",
        "prioridad": "ALTA",
        "cola": "SUPERVISOR",
        "mensaje_cliente": "Te paso con un supervisor.",
        "tiempo_espera_estimado_segundos": 45,
    }

    with pytest.raises(ValidationError, match="ESCALATED"):
        ConversationReply.model_validate(data | {"blocks": [notice]})
    ConversationReply.model_validate(data | {"blocks": [notice], "status": "ESCALATED"})


def test_confidence_score_only_with_rag() -> None:
    data = example("chat.tracking.response")["reply"]

    with pytest.raises(ValidationError, match="RAG"):
        ConversationReply.model_validate(
            data
            | {
                "meta": data["meta"]
                | {"confidence_score": 0.9, "confidence_method": "heuristic_v1"}
            }
        )


def test_unknown_block_type_is_rejected() -> None:
    data = example("chat.tracking.response")["reply"]

    with pytest.raises(ValidationError):
        ConversationReply.model_validate(data | {"blocks": [{"type": "HTML", "html": "<b>x</b>"}]})


# --- channel HTTP models ---------------------------------------------------


def test_pbx_turn_needs_exactly_one_input() -> None:
    data = example("pbx.turn.request")

    with pytest.raises(ValidationError, match="exactly one"):
        PbxTurnRequest.model_validate(data | {"audio_ref": "audio/call-7d1e-2.wav"})
    with pytest.raises(ValidationError, match="exactly one"):
        PbxTurnRequest.model_validate(data | {"transcript": None})
    PbxTurnRequest.model_validate(data | {"event": "CALL_ENDED", "transcript": None})


@pytest.mark.parametrize(
    "speech",
    [
        "Tu guía TLX-984021-CO está en tránsito.",  # digits must be spelled out
        "Tu envío **llega hoy**.",
        "Consulta https://translogix.example para más detalles.",
        "Tu envío llega hoy 🚚",
        " ".join(["palabra"] * 41),
    ],
)
def test_pbx_speech_must_be_speakable(speech: str) -> None:
    data = example("pbx.turn.response")

    with pytest.raises(ValidationError, match=r"speech_text|spoken turn"):
        PbxTurnResponse.model_validate(data | {"speech_text": speech})


def test_chat_message_needs_text_or_attachments() -> None:
    with pytest.raises(ValidationError, match="text or at least one attachment"):
        ChatMessageRequest.model_validate(example("chat.tracking.request") | {"text": "   "})


def test_failed_delivery_must_explain_itself() -> None:
    with pytest.raises(ValidationError, match="error_code"):
        DeliveryResult(delivery_mode="REAL", status="FAILED")  # type: ignore[arg-type]


def test_ws_events_are_discriminated_by_type() -> None:
    event = WS_EVENT_ADAPTER.validate_python(example("ws.handoff-status"))

    assert isinstance(event, WsHandoffStatusEvent)


async def test_null_speech_ports_report_not_configured() -> None:
    with pytest.raises(ContractError) as asr:
        await NullAsr().transcribe("audio/1.wav")
    with pytest.raises(ContractError) as tts:
        await NullTts().synthesize("Hola")

    assert asr.value.code is tts.value.code is ErrorCode.NO_CONFIGURADO
    assert asr.value.http_status == 503


def test_timestamps_are_parsed_as_aware_datetimes() -> None:
    message = InboundMessage.model_validate(example("inbound.web-chat"))

    assert isinstance(message.timestamp, datetime)
    assert message.timestamp.utcoffset() is not None


# --- 1.2.0: money, guías, deadlines, uploads, handoff inbox, WhatsApp body --------


@pytest.mark.parametrize("amount", [12500000.0, 12500000.5, "12500000", True])
def test_money_must_be_a_plain_integer(amount: object) -> None:
    with pytest.raises(ValidationError):
        CotizacionRequest.model_validate(
            example("cotizar.request") | {"valor_declarado_cop": amount}
        )


@pytest.mark.parametrize(
    "guia", ["TLX-98402-CO", "tlx-984021-co", "TLX-984021-PE", "TLX 984021 CO"]
)
def test_contract_fields_only_accept_canonical_guias(guia: str) -> None:
    state = {
        "session_id": "c1a2b3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        "channel": "WEB_CHAT",
        "external_user_id_hash": "0" * 64,
        "status": "ACTIVE",
        "created_at": "2026-11-03T10:00:00-05:00",
        "updated_at": "2026-11-03T10:00:00-05:00",
    }

    with pytest.raises(ValidationError):
        TrackingResponse.model_validate(example("tracking.en-transito") | {"numero_guia": guia})
    with pytest.raises(ValidationError):
        ConversationState.model_validate(state | {"numero_guia_consultada": guia})
    ConversationState.model_validate(state | {"numero_guia_consultada": "TLX-984021-CO"})


def _late_ticket(**changes: Any) -> dict[str, Any]:
    data = example("pqrs.siniestro.response")
    late = data["elegibilidad"] | {"dentro_de_plazo": False, "fecha_limite": "2026-11-05"}
    return data | {"elegibilidad": late, "escalamiento": None, "estado": "FUERA_DE_PLAZO"} | changes


def test_expired_deadline_is_always_visible() -> None:
    PqrsTicketResponse.model_validate(_late_ticket())

    with pytest.raises(ValidationError, match="FUERA_DE_PLAZO"):
        PqrsTicketResponse.model_validate(_late_ticket(estado="BORRADOR"))
    with pytest.raises(ValidationError, match="FUERA_DE_PLAZO"):
        PqrsTicketResponse.model_validate(
            _late_ticket(elegibilidad=example("pqrs.siniestro.response")["elegibilidad"])
        )


def test_escalation_takes_precedence_over_an_expired_deadline() -> None:
    escalated = _late_ticket(
        estado="ESCALADO", escalamiento=example("pqrs.siniestro.response")["escalamiento"]
    )

    assert PqrsTicketResponse.model_validate(escalated).estado == "ESCALADO"


def test_uploaded_attachment_kind_matches_its_type() -> None:
    data = example("attachment.upload.response")

    with pytest.raises(ValidationError, match="kind"):
        AttachmentUploadResponse.model_validate(data | {"kind": "IMAGE"})
    with pytest.raises(ValidationError):
        AttachmentUploadResponse.model_validate(data | {"mime_type": "image/png", "kind": "IMAGE"})


def test_advisor_can_only_assign_or_close() -> None:
    HandoffStatusUpdate.model_validate({"status": "CLOSED"})

    with pytest.raises(ValidationError):
        HandoffStatusUpdate.model_validate({"status": "PENDING"})


def test_whatsapp_body_keeps_unknown_provider_fields_and_the_from_alias() -> None:
    data = example("whatsapp.webhook.request")
    value = data["entry"][0]["changes"][0]["value"]
    value["messages"][0]["context"] = {"forwarded": True}  # a field this contract does not model

    payload = WhatsAppWebhookPayload.model_validate(data)
    message = payload.entry[0].changes[0].value.messages[0]

    assert message.from_ == "573000000000"
    dumped = payload.model_dump(mode="json")
    assert dumped["entry"][0]["changes"][0]["value"]["messages"][0]["from"] == "573000000000"
    assert dumped["entry"][0]["changes"][0]["value"]["messages"][0]["context"] == {
        "forwarded": True
    }


def test_whatsapp_body_rejects_other_webhook_objects() -> None:
    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload.model_validate(
            example("whatsapp.webhook.request") | {"object": "page"}
        )
