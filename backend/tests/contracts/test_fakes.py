"""The fakes honour their ports and replay the golden scenarios.

Typed assignments (``tms: ports.TmsPort = fakes.FakeTms()``) make mypy check
that every fake really implements its Protocol.
"""

import json
from datetime import datetime
from typing import Any

import pytest

from app.contracts import fakes, ports
from app.contracts.common import ChannelType, ContractError, ErrorCode
from app.contracts.conversation import ConversationState, Intent, Sentiment
from app.contracts.escalation import EscalationInput, HandoffStatus
from app.contracts.examples import EXAMPLES_DIR
from app.contracts.inbound import InboundMessage
from app.contracts.ports import IndexedChunk, LlmMessage
from app.contracts.pqrs import PqrsTicketRequest
from app.contracts.quotation import CotizacionRequest
from app.contracts.rag import ChunkMetadata
from app.contracts.reply import ConversationReply


def example(name: str) -> Any:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


INBOUND = ["inbound.web-chat", "inbound.whatsapp", "inbound.pbx"]


@pytest.mark.parametrize("name", INBOUND)
async def test_acceptance_fake_core_answers_every_channel(name: str) -> None:
    """P01 acceptance: ``FakeConversationService().handle(msg)`` for each channel."""
    message = InboundMessage.model_validate(example(name))
    core: ports.ConversationService = fakes.FakeConversationService()

    reply = await core.handle(message)

    assert isinstance(reply, ConversationReply)
    assert (reply.channel, reply.in_reply_to) == (message.channel, message.message_id)
    assert reply.meta.delivery_mode is message.delivery_mode
    # Same business answer on every channel; the PBX dictates the guía.
    assert reply.meta.evidence_ids == ["TLX-984021-CO"]


async def test_fake_core_falls_back_deterministically_for_unknown_messages() -> None:
    data = example("inbound.whatsapp")
    message = InboundMessage.model_validate(data | {"content": data["content"] | {"text": "hola"}})
    core = fakes.FakeConversationService()

    first, second = await core.handle(message), await core.handle(message)

    assert first == second
    assert first.meta.intent is Intent.DESCONOCIDA
    assert first.channel is ChannelType.WHATSAPP
    assert len(core.received) == 2


async def test_fake_tms_serves_the_five_guias_internally_and_safely() -> None:
    tms: ports.TmsPort = fakes.FakeTms()
    guias = ["TLX-984021-CO", "TLX-752190-CO", "TLX-310482-CO", "TLX-551904-CO", "TLX-118493-CO"]

    shipments = [await tms.get_shipment(guia) for guia in guias]
    views = [await tms.get_tracking_view(guia) for guia in guias]

    assert all(shipments)
    assert all(views)
    assert shipments[2] is not None
    assert shipments[2].especificaciones_carga.mercancia_peligrosa
    assert await tms.get_shipment("TLX-000000-CO") is None
    assert await tms.get_tracking_view("TLX-000000-CO") is None


def test_fake_quotation_knows_the_golden_routes_only() -> None:
    quotation: ports.QuotationPort = fakes.FakeQuotation()

    plain = quotation.quote(CotizacionRequest.model_validate(example("cotizar.request")))
    floor = quotation.quote(
        CotizacionRequest.model_validate(example("cotizar.flete-minimo.request"))
    )

    assert (plain.calculo.flete_total_cop, floor.calculo.flete_total_cop) == (190975, 22500)
    with pytest.raises(ContractError) as error:
        quotation.quote(
            CotizacionRequest.model_validate(example("cotizar.ruta-sin-tarifa.request"))
        )
    assert error.value.code is ErrorCode.RUTA_SIN_TARIFA


async def test_fake_rag_has_grounded_ungrounded_and_unavailable_modes() -> None:
    rag: ports.RagPort = fakes.FakeRag()
    cargo: ports.SpecialCargoPort = fakes.FakeSpecialCargo()

    grounded = await rag.answer("¿Puedo enviar ácido clorhídrico?")
    ungrounded = await rag.answer("¿Cuánto cuesta un contenedor a Hong Kong?")

    assert grounded.grounded
    assert grounded.citations
    assert not ungrounded.grounded
    assert ungrounded.answer_text is None
    assert (await cargo.check("ácido clorhídrico")).grounded
    with pytest.raises(ContractError) as error:
        await fakes.FakeRag(available=False).answer("lo que sea")
    assert error.value.code is ErrorCode.LLM_NO_DISPONIBLE


def _escalation(**changes: Any) -> EscalationInput:
    base: dict[str, Any] = {
        "intent": Intent.TRACKING,
        "user_text_normalized": "donde esta mi guia",
        "sentiment": Sentiment.NEUTRO,
        "incident_reported": False,
    }
    return EscalationInput.model_validate(base | changes)


@pytest.mark.parametrize(
    ("changes", "criterion"),
    [
        ({}, None),
        ({"valor_declarado_cop": 12_500_000, "requiere_refrigeracion": True}, None),  # S1
        ({"mercancia_peligrosa": True, "estado_guia": "RETENIDO_NOVEDAD"}, "CRIT-005"),  # S4
        ({"confidence_score": 0.31, "intent": Intent.CONSULTA_DOCUMENTAL}, "CRIT-001"),  # S12
        ({"user_text_normalized": "pesimo servicio voy a demandar"}, "CRIT-002"),  # S13
        ({"intent": Intent.PQRS, "valor_declarado_cop": 85_000_000}, "CRIT-003"),  # S14
    ],
)
def test_fake_escalation_replays_the_golden_decisions(
    changes: dict[str, Any], criterion: str | None
) -> None:
    engine: ports.EscalationPort = fakes.FakeEscalation()

    decision = engine.evaluate(_escalation(**changes))

    assert (decision.primary.id_criterio if decision.primary else None) == criterion


async def test_fake_handoff_routes_and_tracks_status() -> None:
    handoffs: ports.HandoffPort = fakes.FakeHandoff()
    decision = fakes.FakeEscalation().evaluate(
        _escalation(mercancia_peligrosa=True, estado_guia="RETENIDO_NOVEDAD")
    )
    state = ConversationState(
        session_id=fakes.deterministic_uuid4("s4"),
        channel=ChannelType.WEB_CHAT,
        external_user_id_hash="0" * 64,
        status="ESCALATED",  # type: ignore[arg-type]
        numero_guia_consultada="TLX-310482-CO",
        created_at=fakes.DEMO_NOW,
        updated_at=fakes.DEMO_NOW,
    )

    created = await handoffs.create(state, decision)
    assigned = await handoffs.update_status(created.handoff_id, HandoffStatus.ASSIGNED)

    assert (created.cola_destino, created.prioridad) == ("OPERACIONES", "URGENTE")
    assert await handoffs.get(created.handoff_id) == assigned
    assert [h.status for h in await handoffs.list_by_status()] == [HandoffStatus.ASSIGNED]
    assert await handoffs.list_by_status(HandoffStatus.PENDING) == []
    with pytest.raises(ContractError):
        await handoffs.create(state, fakes.FakeEscalation().evaluate(_escalation()))


async def test_fake_pqrs_returns_the_golden_tickets() -> None:
    pqrs: ports.PqrsPort = fakes.FakePqrs()

    claim = await pqrs.create_ticket(
        PqrsTicketRequest.model_validate(example("pqrs.siniestro.request"))
    )
    petition = await pqrs.create_ticket(
        PqrsTicketRequest.model_validate(example("pqrs.peticion.request"))
    )

    assert (claim.estado, petition.estado) == ("ESCALADO", "RADICADO")


async def test_fake_accounts_llm_and_audit() -> None:
    gold = ports.AccountProfile(tipo_cuenta="CORPORATIVO_GOLD", envios_mensuales_cuenta=900)
    accounts: ports.AccountDirectoryPort = fakes.FakeAccountDirectory({"900.123.456-7": gold})
    llm: ports.LlmPort = fakes.FakeLlm()
    audit: ports.AuditPort = fakes.FakeAudit()

    assert await accounts.get_account("900.123.456-7") == gold
    assert await accounts.get_account("otro") is None
    completion = await llm.complete([LlmMessage(role="user", content="hola")])
    assert completion.model == "fake-llm"
    with pytest.raises(ContractError) as error:
        await fakes.FakeLlm(available=False).complete([])
    assert error.value.code is ErrorCode.LLM_NO_DISPONIBLE
    event = ports.AuditEvent(
        id=fakes.deterministic_uuid4("audit"),
        trace_id="trc-1",
        session_id=None,
        channel=ChannelType.PBX,
        intent=Intent.TRACKING,
        services_called=[],
        escalation=None,
        evidence_ids=["TLX-984021-CO"],
        llm_used=False,
        latency_ms=12,
        created_at=fakes.DEMO_NOW,
    )
    await audit.record(event)
    assert isinstance(audit, fakes.FakeAudit)
    assert audit.events == [event]


async def test_fake_embedding_is_deterministic_unit_length_and_sized() -> None:
    embedding: ports.EmbeddingPort = fakes.FakeEmbedding(dimension=64)

    first, again, other = await embedding.embed(["Clase 8", "Clase 8", "Clase 3"])

    assert embedding.dimension == 64 == len(first)
    assert first == again != other
    assert abs(sum(v * v for v in first) - 1) < 1e-9


def _chunk(chunk_id: str, content: str, vector: list[float], doc: str) -> IndexedChunk:
    metadata = ChunkMetadata.model_validate(
        {
            "doc_code": doc,
            "doc_title": "t",
            "source_file": "f.md",
            "section_path": "s",
            "tipo": "normativa_carga",
            "chunk_index": 0,
            "content_hash": f"{hash(content) & (2**256 - 1):064x}",
            "ingested_at": "2026-11-03T10:00:00-05:00",
        }
    )
    return IndexedChunk(chunk_id=chunk_id, content=content, metadata=metadata, embedding=vector)


async def test_fake_vector_store_is_idempotent_and_ranks_by_similarity() -> None:
    store: ports.VectorStorePort = fakes.FakeVectorStore()
    near = _chunk("a", "corrosivos", [1.0, 0.0], "DOC-OPS-2026-03")
    far = _chunk("b", "plazos", [0.0, 1.0], "DOC-PQRS-2026-04")

    assert await store.upsert([near, far]) == 2
    assert await store.upsert([near]) == 0  # same content_hash: skipped

    ranked = await store.search([0.9, 0.1], top_k=2)
    filtered = await store.search([0.9, 0.1], top_k=2, filters={"doc_code": "DOC-PQRS-2026-04"})

    assert [chunk.chunk_id for chunk in ranked] == ["a", "b"]
    assert [chunk.chunk_id for chunk in filtered] == ["b"]


def test_fixed_clock_is_aware_and_stable() -> None:
    clock: ports.Clock = fakes.FixedClock()

    assert clock.now() == clock.now() == fakes.DEMO_NOW
    with pytest.raises(ValueError, match="timezone-aware"):
        fakes.FixedClock(datetime(2026, 11, 3, 10, 0))  # noqa: DTZ001 - the point of the test


def test_deterministic_uuid4_is_a_valid_v4() -> None:
    value = fakes.deterministic_uuid4("seed")

    assert value == fakes.deterministic_uuid4("seed") != fakes.deterministic_uuid4("other")
    assert value.version == 4
