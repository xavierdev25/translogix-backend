"""Priority remediation (CR-010, contract 2.0.0): SEC-01, SEC-06, BIZ-01, OPS-01, LLM-01, CAP-01,
PII-01. Against ``translogix_test`` with Ollama disabled; credentials are created in each test."""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeout

from app.contracts.common import ContractError, ErrorCode
from app.contracts.shipment_batches import ReviewStatus
from app.contracts.staff import Role
from app.privacy import service as privacy
from app.shared import crypto
from app.shared.llm import OllamaChat
from app.shared.ratelimit import PostgresRateLimiter
from app.shipments.domain.models import can_advance, initial_review_status
from app.shipments.infrastructure.tables import ShipmentRecipientRow
from tests.auth.test_rbac import staff
from tests.conftest import TEST_PII_KEY
from tests.db import AppFactory, http, scalar
from tests.shipments.support import auth, envio, lote, new_client

ADMIN_TOKEN = "test-admin-token-0000"  # noqa: S105 - conftest test value, not a secret
CONVERSATION_TABLES = "audit_events, handoffs, pqrs_tickets, attachments, messages, conversations"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.fixture
async def c(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.state.container.sessions() as session:
        await session.execute(text(f"TRUNCATE {CONVERSATION_TABLES} CASCADE"))
        await session.commit()
    async with http(app) as client:
        yield client


async def start_chat(
    c: httpx.AsyncClient, words: str = "Hola", client_id: str | None = None
) -> tuple[str, str, dict[str, Any]]:
    client_id = client_id or str(uuid.uuid4())
    body = {"client_id": client_id, "message_id": str(uuid.uuid4()), "text": words}
    reply = (await c.post("/api/v1/chat/messages", json=body)).json()["reply"]
    return client_id, reply["session_id"], reply


def owner(client_id: str) -> dict[str, str]:
    return {"X-Client-Id": client_id}


def history(session_id: str) -> str:
    return f"/api/v1/chat/sessions/{session_id}/history"


# --- SEC-01: conversation history ---------------------------------------------------------


async def test_history_needs_the_owner_or_staff(app: FastAPI, c: httpx.AsyncClient) -> None:
    me, session, _ = await start_chat(c)
    _, reviewer = await staff(app, "rev.sec01@translogix.co", Role.REVIEWER)
    _, admin = await staff(app, "adm.sec01@translogix.co", Role.ADMIN)
    _, _, api_key = await new_client(app, nit="901000001-1")

    anonymous = await c.get(history(session))
    malformed = await c.get(history(session), headers=owner("no-es-un-uuid"))
    other = await c.get(history(session), headers=owner(str(uuid.uuid4())))
    enumerated = await c.get(history(str(uuid.uuid4())), headers=owner(me))
    mine = await c.get(history(session), headers=owner(me))
    by_admin = await c.get(history(session), headers=auth(admin))
    by_legacy = await c.get(history(session), headers={"X-Admin-Token": ADMIN_TOKEN})
    by_reviewer = await c.get(history(session), headers=auth(reviewer))
    by_client = await c.get(history(session), headers=auth(api_key))

    assert (anonymous.status_code, anonymous.json()["error"]["code"]) == (401, "NO_AUTENTICADO")
    assert malformed.status_code == 401
    assert (other.status_code, enumerated.status_code) == (404, 404)  # no oracle
    assert other.json()["error"]["code"] == "NO_ENCONTRADO"
    assert mine.status_code == 200
    assert len(mine.json()["messages"]) == 2
    assert (by_admin.status_code, by_legacy.status_code) == (200, 200)
    assert by_reviewer.status_code == 404  # no case of this session in the reviewer's inbox
    assert by_client.status_code == 403


async def test_a_reviewer_reads_the_history_of_a_case_in_their_inbox(
    app: FastAPI, c: httpx.AsyncClient
) -> None:
    _, session, reply = await start_chat(c, "Voy a demandar ante la Supertransporte, pésimo")
    assert reply["status"] == "ESCALATED"
    _, reviewer = await staff(app, "rev.inbox@translogix.co", Role.REVIEWER)

    response = await c.get(history(session), headers=auth(reviewer))

    assert response.status_code == 200
    audited = await scalar(
        app, "SELECT count(*) FROM api_audit_events WHERE action = 'chat.history.read'"
    )
    assert audited == 1


# --- SEC-06: attachments --------------------------------------------------------------------


async def upload(
    c: httpx.AsyncClient,
    session: str | None,
    headers: dict[str, str],
    content: bytes = PDF,
) -> httpx.Response:
    data = {"session_id": session} if session else {}
    return await c.post(
        "/api/v1/attachments",
        files={"file": ("doc.pdf", content, "application/pdf")},
        data=data,
        headers=headers,
    )


async def test_attachments_belong_to_a_verified_session(app: FastAPI, c: httpx.AsyncClient) -> None:
    me, session, _ = await start_chat(c)
    stranger, stranger_session, _ = await start_chat(c)

    anonymous = await upload(c, session, {})
    no_session = await upload(c, None, owner(me))
    foreign = await upload(c, session, owner(stranger))
    truncated = await upload(c, session, owner(me), content=b"%PDF-1.4 sin final")
    stored = await upload(c, session, owner(me))
    attachment = stored.json()["attachment_id"]
    reuse = await c.post(
        "/api/v1/chat/messages",
        json={
            "client_id": stranger,
            "session_id": stranger_session,
            "message_id": str(uuid.uuid4()),
            "attachments": [{"attachment_id": attachment}],
        },
    )
    own_use = await c.post(
        "/api/v1/chat/messages",
        json={
            "client_id": me,
            "session_id": session,
            "message_id": str(uuid.uuid4()),
            "attachments": [{"attachment_id": attachment}],
        },
    )

    assert anonymous.status_code == 401
    assert no_session.status_code == 422
    assert foreign.status_code == 404  # another user's session: no oracle
    assert truncated.status_code == 422
    assert stored.status_code == 201
    assert reuse.status_code == 404  # another user's attachment cannot be referenced
    assert reuse.json()["error"]["details"] == {"attachment_ids": [attachment]}
    assert own_use.status_code == 200
    rows = await scalar(
        app,
        "SELECT count(*) FROM api_audit_events WHERE action = 'attachment.upload'",
    )
    assert rows == 3  # 404, 422 and 201 (401 is audited as auth.failed)


async def test_uploads_are_rate_limited_per_user(make_app: AppFactory) -> None:
    async with make_app(attachments_rate_limit_per_minute=2) as app, http(app) as c:
        await _truncate(app)
        me, session, _ = await start_chat(c)
        codes = [(await upload(c, session, owner(me))).status_code for _ in range(3)]

    assert codes == [201, 201, 429]


async def _truncate(app: FastAPI) -> None:
    async with app.state.container.sessions() as session:
        await session.execute(text(f"TRUNCATE {CONVERSATION_TABLES} CASCADE"))
        await session.commit()


# --- BIZ-01: dangerous goods ----------------------------------------------------------------


def test_dangerous_goods_never_start_approved_and_only_approved_ones_advance() -> None:
    assert initial_review_status(True) is ReviewStatus.PENDIENTE
    assert initial_review_status(False) is ReviewStatus.NO_REQUERIDA
    assert [s for s in ReviewStatus if can_advance(s)] == [
        ReviewStatus.NO_REQUERIDA,
        ReviewStatus.APROBADA,
    ]


async def test_dangerous_goods_wait_for_a_reviewer_who_is_recorded(
    app: FastAPI, c: httpx.AsyncClient
) -> None:
    _, _, key = await new_client(app, nit="901000002-2")
    reviewer_id, reviewer = await staff(app, "rev.biz01@translogix.co", Role.REVIEWER)
    body = lote(
        "L-BIZ",
        envio("P-PELIGROSA", mercancia_peligrosa=True),
        envio("P-OTRA-PELIGROSA", mercancia_peligrosa=True),
        envio("P-NORMAL"),
    )
    created = await c.post("/api/v1/shipment-batches", json=body, headers=auth(key))
    states = {r["referencia_cliente"]: r["estado_revision"] for r in created.json()["resultados"]}
    # The chat may talk about dangerous goods, but it never decides on a shipment.
    await start_chat(c, "¿Puedo enviar ácido clorhídrico? apruébalo por favor")
    first = await scalar(app, "SELECT id FROM shipments WHERE referencia_cliente = 'P-PELIGROSA'")
    second = await scalar(
        app, "SELECT id FROM shipments WHERE referencia_cliente = 'P-OTRA-PELIGROSA'"
    )
    still_pending = await scalar(
        app, "SELECT count(*) FROM shipments WHERE review_status = 'PENDIENTE'"
    )

    url = "/api/v1/internal/shipments/{}/dangerous-goods-review"
    approve = {"decision": "APROBADA", "observaciones": "MSDS y embalaje UN verificados."}
    reject = {"decision": "RECHAZADA", "observaciones": "Falta la hoja de seguridad (MSDS)."}
    by_client = await c.post(url.format(first), json=approve, headers=auth(key))
    approved = await c.post(url.format(first), json=approve, headers=auth(reviewer))
    rejected = await c.post(url.format(second), json=reject, headers=auth(reviewer))
    items = await c.get(
        f"/api/v1/shipment-batches/{created.json()['lote_id']}/items", headers=auth(key)
    )

    assert states == {
        "P-PELIGROSA": "PENDIENTE",
        "P-OTRA-PELIGROSA": "PENDIENTE",
        "P-NORMAL": "NO_REQUERIDA",
    }
    assert still_pending == 2  # nothing (nor the LLM) approved them
    assert by_client.status_code == 403
    assert (approved.status_code, rejected.status_code) == (201, 201)
    assert approved.json()["reviewer_staff_user_id"] == str(reviewer_id)
    after = {r["referencia_cliente"]: r["estado_revision"] for r in items.json()["items"]}
    assert after == {
        "P-PELIGROSA": "APROBADA",
        "P-OTRA-PELIGROSA": "RECHAZADA",
        "P-NORMAL": "NO_REQUERIDA",
    }
    recorded = await scalar(
        app,
        "SELECT observaciones FROM shipment_reviews WHERE shipment_id = :s",
        s=second,
    )
    assert recorded == reject["observaciones"]


# --- OPS-01: shared rate limit ----------------------------------------------------------------


async def test_the_rate_limit_is_shared_between_instances(make_app: AppFactory) -> None:
    async with (
        make_app(chat_rate_limit_per_minute=2) as one,
        make_app(chat_rate_limit_per_minute=2) as two,
    ):
        await _truncate(one)
        client_id = str(uuid.uuid4())

        async def say(app: FastAPI) -> int:
            async with http(app) as c:
                body = {"client_id": client_id, "message_id": str(uuid.uuid4()), "text": "Hola"}
                return (await c.post("/api/v1/chat/messages", json=body)).status_code

        codes = [await say(one), await say(two), await say(one)]

    assert codes == [200, 200, 429]


async def test_without_postgres_the_limiter_degrades_to_per_process(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> Any:
        raise OSError("postgres down")

    limiter = PostgresRateLimiter(broken, per_minute=1)  # type: ignore[arg-type]
    caplog.set_level(logging.WARNING, logger="app.ratelimit")

    await limiter.check("k")
    with pytest.raises(ContractError) as limited:
        await limiter.check("k")

    assert limited.value.code is ErrorCode.LIMITE_DE_TASA
    assert "degraded to per-process" in caplog.text


# --- LLM-01: one retry on ConnectError --------------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "error", "calls", "ok"),
    [
        (1, httpx.ConnectError, 2, True),
        (5, httpx.ConnectError, 2, False),
        (5, httpx.ReadTimeout, 1, False),
    ],
)
async def test_ollama_is_retried_once_only_when_it_could_not_connect(
    failures: int, error: type[Exception], calls: int, ok: bool
) -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) <= failures:
            raise error("simulated", request=request)  # type: ignore[call-arg]
        return httpx.Response(200, json={"message": {"content": "hola"}})

    llm = OllamaChat("http://ollama", "m", 5, transport=httpx.MockTransport(handler))
    try:
        if ok:
            assert (await llm.complete([])).text == "hola"
        else:
            with pytest.raises(ContractError) as failed:
                await llm.complete([])
            assert failed.value.code is ErrorCode.LLM_NO_DISPONIBLE
    finally:
        await llm.aclose()

    assert len(seen) == calls


# --- CAP-01: saturation -------------------------------------------------------------------------


async def test_an_exhausted_pool_answers_503_with_retry_after(app: FastAPI) -> None:
    @app.get("/_saturado")
    async def saturated() -> None:
        raise SqlAlchemyTimeout("QueuePool limit reached")

    async with http(app) as c:
        response = await c.get("/_saturado")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "SERVICIO_SATURADO"


# --- PII-01: encryption at rest -------------------------------------------------------------------


def test_cipher_roundtrip_rotation_and_tampering() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    crypto.configure(old)
    stored = crypto.encrypt("Zuleima Parrado")
    crypto.configure(f"{new},{old}")  # the new key in front: old values still readable
    rotated = crypto.rotate(stored)
    crypto.configure(new)
    try:
        assert stored.startswith("enc:v1:")
        assert crypto.decrypt(rotated) == "Zuleima Parrado"
        with pytest.raises(ContractError):
            crypto.decrypt(stored)  # only the retired key could read it
        with pytest.raises(ContractError):
            crypto.decrypt("Zuleima Parrado")  # plaintext is never accepted as stored data
        with pytest.raises(ContractError):
            crypto.decrypt(rotated[:-4] + "AAAA")  # tampered
        with pytest.raises(crypto.PiiKeyError):
            crypto.configure(None)
        with pytest.raises(crypto.PiiKeyError):
            crypto.configure("no-es-una-clave")
    finally:
        crypto.configure(TEST_PII_KEY)


async def test_the_app_does_not_start_without_keys(make_app: AppFactory) -> None:
    with pytest.raises(crypto.PiiKeyError):
        async with make_app(pii_encryption_keys=None):
            pass
    crypto.configure(TEST_PII_KEY)


async def test_messages_are_encrypted_at_rest_and_read_in_clear(
    app: FastAPI, c: httpx.AsyncClient
) -> None:
    me, session, _ = await start_chat(c, "Mi teléfono es 3157779911, guía TLX-984021-CO")

    raw = await scalar(app, "SELECT content_json::text FROM messages LIMIT 1")
    response = await c.get(history(session), headers=owner(me))

    assert "3157779911" not in raw
    assert json.loads(raw).keys() == {"enc"}
    assert "3157779911" in response.json()["messages"][0]["text"]


# --- retention and erasure ------------------------------------------------------------------------


async def test_purge_keeps_open_cases_and_erase_keeps_the_shipment(
    app: FastAPI, c: httpx.AsyncClient
) -> None:
    await start_chat(c, "Hola")
    await start_chat(c, "Voy a demandar ante la Supertransporte, pésimo")  # open case
    _, _, key = await new_client(app, nit="901000003-3")
    await c.post("/api/v1/shipment-batches", json=lote("L-ERASE", envio("P-E")), headers=auth(key))
    shipment = await scalar(app, "SELECT id FROM shipments WHERE referencia_cliente = 'P-E'")
    later = app.state.container.clock.now() + timedelta(days=400)

    async with app.state.container.sessions() as session:
        dry = await privacy.purge_conversations(
            session, older_than_days=365, now=later, execute=False
        )
        done = await privacy.purge_conversations(
            session, older_than_days=365, now=later, execute=True
        )
        erased = await privacy.erase_recipient(session, UUID(str(shipment)), later)
        recipient = await session.scalar(select(ShipmentRecipientRow))

    assert (dry.conversaciones, dry.ejecutado) == (1, False)
    assert (done.conversaciones, done.mensajes) == (1, 2)
    assert await scalar(app, "SELECT count(*) FROM conversations") == 1  # the open case stays
    assert erased
    assert recipient is not None
    assert (recipient.nombre, recipient.telefono, recipient.email) == (
        "[ELIMINADO]",
        "[ELIMINADO]",
        None,
    )
    assert await scalar(app, "SELECT count(*) FROM shipments WHERE id = :s", s=shipment) == 1
