"""Core chat flows through the real app and the test database, with Ollama disabled.

Regression net added by the audit of 2026-11-04: tracking, quotation (official formula),
human escalation (CRIT-002/CRIT-005), degradation without the LLM, the waiting-for-an-
advisor text (R1). The lock between turns (CON-01) is in ``test_conversation_lock.py``.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from tests.db import http

CONVERSATION_TABLES = ("audit_events", "handoffs", "pqrs_tickets", "messages", "conversations")


@pytest.fixture
async def chat(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.state.container.sessions() as session:
        await session.execute(text(f"TRUNCATE {', '.join(CONVERSATION_TABLES)} CASCADE"))
        await session.commit()
    async with http(app) as client:
        yield client


async def say(
    c: httpx.AsyncClient, words: str, *, client: str, session: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"client_id": client, "message_id": str(uuid.uuid4()), "text": words}
    if session:
        body["session_id"] = session
    response = await c.post("/api/v1/chat/messages", json=body)
    assert response.status_code == 200, response.text
    reply: dict[str, Any] = response.json()["reply"]
    return reply


def texts(reply: dict[str, Any]) -> str:
    return " ".join(b["text"] for b in reply["blocks"] if b["type"] == "TEXT")


def block(reply: dict[str, Any], kind: str) -> dict[str, Any]:
    found = [b for b in reply["blocks"] if b["type"] == kind]
    assert found, f"no {kind} block in {[b['type'] for b in reply['blocks']]}"
    first: dict[str, Any] = found[0]
    return first


def criterion(reply: dict[str, Any]) -> str | None:
    escalation = reply["meta"]["escalation"] or {}
    primary: dict[str, Any] = escalation.get("primary") or {}
    return primary.get("id_criterio")


async def test_tracking_is_answered_from_data_without_the_llm(chat: httpx.AsyncClient) -> None:
    reply = await say(chat, "¿Dónde está mi guía TLX-984021-CO?", client=str(uuid.uuid4()))

    assert reply["meta"]["intent"] == "TRACKING"
    assert reply["meta"]["llm_used"] is False
    assert "En tránsito" in texts(reply)


async def test_quotation_uses_the_official_formula(chat: httpx.AsyncClient) -> None:
    client = str(uuid.uuid4())
    first = await say(chat, "Quiero cotizar un envío de Bogotá a Medellín", client=client)
    session = first["session_id"]
    for words in ("40x50x60", "45,5 kg"):
        await say(chat, words, client=client, session=session)
    reply = await say(chat, "El valor declarado es 12.500.000", client=client, session=session)

    summary = block(reply, "QUOTE_SUMMARY")
    # vol = 40·50·60 / 4000 = 30 kg < 45,5 real; base 45,5 * 1.450 = 65.975;
    # seguro = máx(1 % de 12.500.000, 2.500) = 125.000; total = base + seguro.
    assert summary["peso_facturable_kg"] == 45.5
    assert summary["flete_total_cop"] == 65_975 + 125_000


async def test_legal_threat_escalates_with_crit002(chat: httpx.AsyncClient) -> None:
    reply = await say(
        chat, "Voy a demandar ante la Supertransporte, pésimo servicio", client=str(uuid.uuid4())
    )

    assert (reply["status"], criterion(reply)) == ("ESCALATED", "CRIT-002")
    block(reply, "HANDOFF_NOTICE")


async def test_dangerous_goods_incident_escalates_with_crit005(chat: httpx.AsyncClient) -> None:
    reply = await say(chat, "TLX-310482-CO", client=str(uuid.uuid4()))

    assert (reply["status"], criterion(reply)) == ("ESCALATED", "CRIT-005")


async def test_documentary_question_without_llm_degrades_and_offers_a_human(
    chat: httpx.AsyncClient,
) -> None:
    reply = await say(chat, "¿Puedo enviar baterías de litio?", client=str(uuid.uuid4()))

    assert not [b for b in reply["blocks"] if b["type"] == "CITATIONS"]
    assert "no puedo consultar la base documental" in texts(reply)
    assert reply["meta"]["llm_used"] is False


async def test_waiting_for_an_advisor_does_not_promise_a_reply_in_this_chat(
    chat: httpx.AsyncClient,
) -> None:
    client = str(uuid.uuid4())
    escalated = await say(chat, "Son unos ladrones, voy a demandar", client=client)
    reply = await say(chat, "¿Hola?", client=client, session=escalated["session_id"])

    assert "cola de asesores" in texts(reply)
    assert "este mismo chat" not in texts(reply)
