"""RBAC (CR-009): roles, ownership (IDOR/BOLA), 401 vs 403, deny by default, audit.

Every credential is a test value created inside the test, against ``translogix_test``.
The legacy admin token is the conftest's test value.
"""

import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import text

from app.auth.dependencies import RBAC_ROLES_ATTRIBUTE, SESSION_OWNER_ATTRIBUTE
from app.contracts.staff import Role
from app.staff.application.services import StaffAdminService
from tests.db import http, scalar
from tests.shipments.support import auth, envio, lote, new_client

ADMIN_TOKEN = "test-admin-token-0000"  # noqa: S105 - conftest test value, not a secret
BATCHES = "/api/v1/shipment-batches"
CONVERSATION_TABLES = "audit_events, handoffs, pqrs_tickets, messages, conversations"


@dataclass(frozen=True)
class Actors:
    admin: str
    admin_id: UUID
    reviewer: str
    reviewer_id: UUID
    reviewer2: str
    client_a: str
    client_b: str


async def staff(app: FastAPI, email: str, *roles: Role) -> tuple[UUID, str]:
    box = app.state.container
    async with box.sessions() as session:
        admin = StaffAdminService(session, box.clock)
        user = await admin.create_user(email, email.split("@")[0], roles)
        _, token = await admin.issue_token(user.id, expires_in_days=1)
        await session.commit()
    return user.id, token


@pytest.fixture
async def actors(app: FastAPI) -> Actors:
    async with app.state.container.sessions() as session:
        await session.execute(text(f"TRUNCATE {CONVERSATION_TABLES} CASCADE"))
        await session.commit()
    admin_id, admin = await staff(app, "admin@translogix.co", Role.ADMIN)
    reviewer_id, reviewer = await staff(app, "revisora@translogix.co", Role.REVIEWER)
    _, reviewer2 = await staff(app, "revisor2@translogix.co", Role.REVIEWER)
    _, _, client_a = await new_client(app, nit="900111222-3")
    _, _, client_b = await new_client(app, nit="800333444-5")
    return Actors(admin, admin_id, reviewer, reviewer_id, reviewer2, client_a, client_b)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with http(app) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return auth(token)


def legacy_admin() -> dict[str, str]:
    return {"X-Admin-Token": ADMIN_TOKEN}


# Every protected operation, with a body that passes validation (so 401/403 is what we see).
ANY_ID = "00000000-0000-4000-8000-000000000000"
PROTECTED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/auth/me", None),
    ("GET", "/api/v1/handoffs", None),
    ("GET", f"/api/v1/handoffs/{ANY_ID}", None),
    ("PATCH", f"/api/v1/handoffs/{ANY_ID}", {"status": "ASSIGNED"}),
    ("POST", "/api/v1/admin/rag/reindex", {}),
    ("POST", BATCHES, lote("L-1", envio("P-1"))),
    ("GET", f"{BATCHES}/{ANY_ID}", None),
    ("GET", f"{BATCHES}/{ANY_ID}/items", None),
    (
        "POST",
        "/api/v1/internal/staff-users",
        {"email": "x@translogix.co", "nombre": "X", "roles": ["REVIEWER"]},
    ),
    ("PATCH", f"/api/v1/internal/staff-users/{ANY_ID}/roles", {"roles": ["ADMIN"]}),
    ("GET", "/api/v1/internal/audit-events", None),
    ("GET", "/api/v1/internal/dangerous-goods", None),
    (
        "POST",
        f"/api/v1/internal/shipments/{ANY_ID}/dangerous-goods-review",
        {"decision": "APROBADA", "observaciones": "Todo en orden."},
    ),
]

# Public on purpose (customer channels and health); see docs/RBAC.md §4.
PUBLIC = {
    ("GET", "/api/v1/tracking/{guia}"),
    ("POST", "/api/v1/cotizar"),
    ("POST", "/api/v1/cotizaciones"),
    ("POST", "/api/v1/pqrs/crear-ticket"),
    ("POST", "/api/v1/chat/messages"),
    ("GET", "/api/v1/channels/whatsapp/webhook"),
    ("POST", "/api/v1/channels/whatsapp/webhook"),
    ("POST", "/api/v1/channels/whatsapp/simulate"),
    ("POST", "/api/v1/channels/pbx/turns"),
    ("POST", "/api/v1/channels/pbx/simulate"),
    ("GET", "/api/v1/metrics/channels"),
    ("GET", "/health"),
    ("GET", "/ready"),
}


async def call(
    c: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await c.request(method, path, json=body, headers=headers or {})


# --- authentication: 401 ------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
async def test_protected_operations_without_credentials_are_401(
    app: FastAPI, client: httpx.AsyncClient, method: str, path: str, body: Any
) -> None:
    response = await call(client, method, path, body)

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "NO_AUTENTICADO"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer basura"},
        {"Authorization": "Bearer tlxs_000000000000_" + "A" * 43},
        {"Authorization": "Bearer tlx_000000000000_" + "A" * 43},
        {"X-Admin-Token": "otro-token-cualquiera"},
    ],
)
async def test_invalid_credentials_are_401(
    app: FastAPI, client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/internal/audit-events", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NO_AUTENTICADO"


# --- who am I ------------------------------------------------------------------------


async def test_the_role_comes_from_the_credential(
    actors: Actors, client: httpx.AsyncClient
) -> None:
    seen = {}
    for name, headers in {
        "admin": bearer(actors.admin),
        "reviewer": bearer(actors.reviewer),
        "client": bearer(actors.client_a),
        "legacy": legacy_admin(),
    }.items():
        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
        seen[name] = (response.json()["roles"], response.json()["metodo"])

    assert seen == {
        "admin": (["ADMIN"], "STAFF_TOKEN"),
        "reviewer": (["REVIEWER"], "STAFF_TOKEN"),
        "client": (["CLIENT"], "API_KEY"),
        "legacy": (["ADMIN"], "ADMIN_TOKEN"),
    }


async def test_a_role_sent_in_headers_or_body_is_ignored(
    actors: Actors, client: httpx.AsyncClient
) -> None:
    forged = {**bearer(actors.client_a), "X-Role": "ADMIN", "X-User-Role": "ADMIN"}
    me = await client.get("/api/v1/auth/me", headers=forged)
    audit = await client.get("/api/v1/internal/audit-events?role=ADMIN", headers=forged)
    smuggled = await client.post(
        "/api/v1/internal/staff-users",
        json={"email": "y@translogix.co", "nombre": "Y", "roles": ["ADMIN"], "role": "ADMIN"},
        headers=bearer(actors.reviewer),
    )

    assert me.json()["roles"] == ["CLIENT"]
    assert audit.status_code == 403
    assert smuggled.status_code == 403


# --- authorization: 403 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/admin/rag/reindex", {}),
        (
            "POST",
            "/api/v1/internal/staff-users",
            {"email": "z@translogix.co", "nombre": "Z", "roles": ["ADMIN"]},
        ),
        ("PATCH", f"/api/v1/internal/staff-users/{ANY_ID}/roles", {"roles": ["ADMIN"]}),
        ("GET", "/api/v1/internal/audit-events", None),
        ("GET", f"{BATCHES}/{ANY_ID}", None),
    ],
)
async def test_a_reviewer_cannot_run_admin_actions(
    actors: Actors, client: httpx.AsyncClient, method: str, path: str, body: Any
) -> None:
    response = await call(client, method, path, body, bearer(actors.reviewer))

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "NO_AUTORIZADO"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/handoffs", None),
        ("GET", f"/api/v1/handoffs/{ANY_ID}", None),
        ("PATCH", f"/api/v1/handoffs/{ANY_ID}", {"status": "CLOSED"}),
        ("POST", "/api/v1/admin/rag/reindex", {}),
        (
            "POST",
            "/api/v1/internal/staff-users",
            {"email": "w@translogix.co", "nombre": "W", "roles": ["REVIEWER"]},
        ),
        ("GET", "/api/v1/internal/audit-events", None),
        ("GET", "/api/v1/internal/dangerous-goods", None),
        (
            "POST",
            f"/api/v1/internal/shipments/{ANY_ID}/dangerous-goods-review",
            {"decision": "APROBADA", "observaciones": "Lo apruebo yo mismo."},
        ),
    ],
)
async def test_a_client_cannot_use_internal_or_admin_operations(
    actors: Actors, client: httpx.AsyncClient, method: str, path: str, body: Any
) -> None:
    response = await call(client, method, path, body, bearer(actors.client_a))

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "NO_AUTORIZADO"


# --- users and roles -------------------------------------------------------------------


async def test_nobody_changes_their_own_roles_and_changes_apply_on_the_next_request(
    actors: Actors, client: httpx.AsyncClient
) -> None:
    own = await client.patch(
        f"/api/v1/internal/staff-users/{actors.admin_id}/roles",
        json={"roles": ["REVIEWER"]},
        headers=bearer(actors.admin),
    )
    before = await client.get("/api/v1/internal/audit-events", headers=bearer(actors.reviewer))
    promoted = await client.patch(
        f"/api/v1/internal/staff-users/{actors.reviewer_id}/roles",
        json={"roles": ["ADMIN", "REVIEWER"]},
        headers=bearer(actors.admin),
    )
    after = await client.get("/api/v1/internal/audit-events", headers=bearer(actors.reviewer))
    me = await client.get("/api/v1/auth/me", headers=bearer(actors.admin))

    assert own.status_code == 403
    assert own.json()["error"]["details"] == {"motivo": "propio_rol"}
    assert me.json()["roles"] == ["ADMIN"]  # unchanged
    assert before.status_code == 403
    assert promoted.status_code == 200
    assert promoted.json()["roles"] == ["ADMIN", "REVIEWER"]
    assert after.status_code == 200


async def test_staff_users_get_only_staff_roles(actors: Actors, client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/v1/internal/staff-users",
        json={"email": "nueva@translogix.co", "nombre": "Nueva", "roles": ["REVIEWER"]},
        headers=bearer(actors.admin),
    )
    as_client = await client.post(
        "/api/v1/internal/staff-users",
        json={"email": "cliente@translogix.co", "nombre": "C", "roles": ["CLIENT"]},
        headers=legacy_admin(),
    )

    assert created.status_code == 201
    assert created.json()["roles"] == ["REVIEWER"]
    assert as_client.status_code == 422


# --- client isolation (IDOR / BOLA) ---------------------------------------------------


async def test_a_client_never_reads_another_clients_batch(
    actors: Actors, client: httpx.AsyncClient
) -> None:
    created = await client.post(
        BATCHES, json=lote("L-A", envio("P-A")), headers=bearer(actors.client_a)
    )
    batch_id = created.json()["lote_id"]
    own = await client.get(f"{BATCHES}/{batch_id}", headers=bearer(actors.client_a))
    other = await client.get(f"{BATCHES}/{batch_id}", headers=bearer(actors.client_b))
    other_items = await client.get(f"{BATCHES}/{batch_id}/items", headers=bearer(actors.client_b))
    admin = await client.get(f"{BATCHES}/{batch_id}", headers=bearer(actors.admin))
    legacy = await client.get(f"{BATCHES}/{batch_id}/items", headers=legacy_admin())
    reviewer = await client.get(f"{BATCHES}/{batch_id}", headers=bearer(actors.reviewer))
    staff_creates = await client.post(
        BATCHES, json=lote("L-S", envio("P-S")), headers=bearer(actors.admin)
    )

    assert created.status_code == 201
    assert own.status_code == 200
    assert (other.status_code, other_items.status_code) == (404, 404)  # no oracle
    assert other.json()["error"]["code"] == "NO_ENCONTRADO"
    assert admin.status_code == 200
    assert legacy.status_code == 200
    assert reviewer.status_code == 403
    assert staff_creates.status_code == 403  # only a CLIENT creates batches


# --- dangerous goods ---------------------------------------------------------------


async def test_only_a_reviewer_decides_on_dangerous_goods(
    app: FastAPI, actors: Actors, client: httpx.AsyncClient
) -> None:
    batch = lote("L-DG", envio("P-DG", mercancia_peligrosa=True), envio("P-OK"))
    created = await client.post(BATCHES, json=batch, headers=bearer(actors.client_a))
    assert created.status_code == 201, created.text
    dangerous = await scalar(app, "SELECT id FROM shipments WHERE mercancia_peligrosa")
    normal = await scalar(app, "SELECT id FROM shipments WHERE NOT mercancia_peligrosa")
    url = f"/api/v1/internal/shipments/{dangerous}/dangerous-goods-review"
    decision = {"decision": "APROBADA", "observaciones": "MSDS y embalaje UN verificados."}

    pending = await client.get(
        "/api/v1/internal/dangerous-goods?estado=PENDIENTE", headers=bearer(actors.reviewer)
    )
    by_client = await client.post(url, json=decision, headers=bearer(actors.client_a))
    by_admin = await client.post(url, json=decision, headers=bearer(actors.admin))
    by_legacy_admin = await client.post(url, json=decision, headers=legacy_admin())
    approved = await client.post(url, json=decision, headers=bearer(actors.reviewer))
    again = await client.post(url, json=decision, headers=bearer(actors.reviewer2))
    not_dangerous = await client.post(
        f"/api/v1/internal/shipments/{normal}/dangerous-goods-review",
        json=decision,
        headers=bearer(actors.reviewer),
    )
    missing = await client.post(
        f"/api/v1/internal/shipments/{uuid.uuid4()}/dangerous-goods-review",
        json=decision,
        headers=bearer(actors.reviewer),
    )
    reviewed = await client.get(
        "/api/v1/internal/dangerous-goods?estado=REVISADO", headers=bearer(actors.admin)
    )

    assert [i["shipment_id"] for i in pending.json()["items"]] == [str(dangerous)]
    assert "destinatario" not in pending.text  # no PII
    assert "Zuleima" not in pending.text
    assert (by_client.status_code, by_admin.status_code) == (403, 403)
    assert by_legacy_admin.status_code == 401  # the shared admin token is not accepted here
    assert approved.status_code == 201
    assert approved.json()["reviewer_staff_user_id"] == str(actors.reviewer_id)
    assert again.status_code == 422
    assert not_dangerous.status_code == 422
    assert missing.status_code == 404
    assert reviewed.json()["items"][0]["revision"]["decision"] == "APROBADA"


# --- escalated cases (handoffs) -------------------------------------------------------


async def escalate(c: httpx.AsyncClient) -> str:
    body = {
        "client_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "text": "Voy a demandar ante la Supertransporte, pésimo servicio",
    }
    reply = (await c.post("/api/v1/chat/messages", json=body)).json()["reply"]
    handoff = next(b for b in reply["blocks"] if b["type"] == "HANDOFF_NOTICE")
    return str(handoff["handoff_id"])


async def test_a_reviewer_sees_the_queue_and_their_own_cases_only(
    actors: Actors, client: httpx.AsyncClient
) -> None:
    first, second = await escalate(client), await escalate(client)
    r1, r2 = bearer(actors.reviewer), bearer(actors.reviewer2)

    taken = await client.patch(f"/api/v1/handoffs/{first}", json={"status": "ASSIGNED"}, headers=r1)
    queue_r2 = await client.get("/api/v1/handoffs", headers=r2)
    queue_r1 = await client.get("/api/v1/handoffs", headers=r1)
    peek = await client.get(f"/api/v1/handoffs/{first}", headers=r2)
    steal = await client.patch(f"/api/v1/handoffs/{first}", json={"status": "CLOSED"}, headers=r2)
    closed = await client.patch(f"/api/v1/handoffs/{first}", json={"status": "CLOSED"}, headers=r1)
    admin_all = await client.get("/api/v1/handoffs", headers=bearer(actors.admin))
    legacy_all = await client.get("/api/v1/handoffs", headers=legacy_admin())

    def ids(response: httpx.Response) -> set[str]:
        return {item["handoff_id"] for item in response.json()["items"]}

    assert taken.status_code == 200
    assert ids(queue_r2) == {second}
    assert ids(queue_r1) == {first, second}
    assert (peek.status_code, steal.status_code) == (404, 404)
    assert closed.status_code == 200
    assert ids(admin_all) == ids(legacy_all) == {first, second}


# --- audit and deny by default ---------------------------------------------------------


async def test_sensitive_actions_and_denials_are_audited_without_credentials(
    app: FastAPI, actors: Actors, client: httpx.AsyncClient
) -> None:
    await client.get("/api/v1/internal/audit-events", headers=bearer(actors.reviewer))
    await client.post(
        "/api/v1/internal/staff-users",
        json={"email": "audit@translogix.co", "nombre": "A", "roles": ["REVIEWER"]},
        headers=bearer(actors.admin),
    )
    events = await client.get("/api/v1/internal/audit-events", headers=bearer(actors.admin))

    actions = {(e["action"], e["http_status"]) for e in events.json()["items"]}
    assert ("access.denied", 403) in actions
    assert ("staff.create", 201) in actions
    denied_by = await scalar(
        app, "SELECT staff_user_id FROM api_audit_events WHERE action = 'access.denied'"
    )
    assert denied_by == actors.reviewer_id
    for credential in (actors.admin, actors.reviewer, actors.client_a, ADMIN_TOKEN):
        leaked = await scalar(
            app,
            "SELECT count(*) FROM api_audit_events WHERE api_audit_events::text LIKE :t",
            t=f"%{credential[-20:]}%",
        )
        assert leaked == 0


def is_protected(route: APIRoute) -> bool:
    """``require(...)`` (roles) or, 2.0.0, a web-chat session-owner check (X-Client-Id)."""
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if getattr(dependency.call, RBAC_ROLES_ATTRIBUTE, None) or getattr(
            dependency.call, SESSION_OWNER_ATTRIBUTE, False
        ):
            return True
        pending.extend(dependency.dependencies)
    return False


def api_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    """FastAPI 0.16x wraps included routers; their routes keep their full paths."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router.routes)


def test_every_route_is_protected_or_explicitly_public(app: FastAPI) -> None:
    """Deny by default: a new route without ``require(...)`` fails here until it is listed."""
    routes = list(api_routes(app.routes))
    unprotected = {
        (method, route.path)
        for route in routes
        if not is_protected(route)
        for method in route.methods or ()
    }

    assert len(routes) == 31  # 18 protected + 13 public: a new route must land in one set
    assert unprotected == PUBLIC
