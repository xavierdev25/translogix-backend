"""Bulk shipments against PostgreSQL through the real HTTP app (P0 spec §11)."""

import asyncio
import json
import logging
import re
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.batches.application.service import BatchService
from app.clients.application.services import ClientAdminService
from app.clients.domain.models import ClientContext, ClientStatus
from app.contracts.api_spec import build_contract_app
from app.contracts.normalizers import NUMERO_GUIA_PATTERN
from app.contracts.shipment_batches import ApiScope
from app.shipments.domain.models import GUIDE_MAX, GUIDE_MIN
from app.shipments.infrastructure import repositories as shipment_repos
from tests.db import AppFactory, http, scalar
from tests.shipments.support import auth, envio, lote, new_client

URL = "/api/v1/shipment-batches"
PII = ("Zuleima Parrado", "3157779911", "Carrera 99 # 88-77")


# --- authentication and isolation -------------------------------------------------------


async def test_api_key_authenticates_and_is_stored_only_as_a_hash(app: FastAPI) -> None:
    _, key_id, key = await new_client(app)
    async with http(app) as client:
        response = await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key))

    assert response.status_code == 201
    stored = await scalar(app, "SELECT key_hash FROM client_api_keys WHERE id = :id", id=key_id)
    assert stored != key
    assert len(stored) == 64
    assert await scalar(app, "SELECT last_used_at IS NOT NULL FROM client_api_keys")


async def test_missing_invalid_revoked_and_unauthorised_credentials(app: FastAPI) -> None:
    client_id, key_id, key = await new_client(app)
    _, _, read_only = await new_client(app, nit="800554321-9", scopes=[ApiScope.BATCHES_READ])
    body = lote("L-1", envio("P-1"))
    async with http(app) as client:
        missing = await client.post(URL, json=body)
        malformed = await client.post(URL, json=body, headers=auth("not-a-key"))
        forged = await client.post(URL, json=body, headers=auth(key[:-4] + "AAAA"))
        no_scope = await client.post(URL, json=body, headers=auth(read_only))
        box = app.state.container
        async with box.sessions() as session:
            await ClientAdminService(session, box.clock).set_status(
                client_id, ClientStatus.INACTIVE
            )
            await session.commit()
        inactive = await client.post(URL, json=body, headers=auth(key))
        async with box.sessions() as session:
            await ClientAdminService(session, box.clock).revoke_api_key(key_id)
            await session.commit()
        revoked = await client.post(URL, json=body, headers=auth(key))

    for response in (missing, malformed, forged, revoked):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "NO_AUTENTICADO"
    for response in (no_scope, inactive):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NO_AUTORIZADO"
    assert await scalar(app, "SELECT count(*) FROM shipment_batches") == 0
    # 1.7.0 (CR-009): 401 is audited as auth.failed and 403 as access.denied.
    failures = await scalar(
        app, "SELECT count(*) FROM api_audit_events WHERE action = 'auth.failed'"
    )
    denied = await scalar(
        app, "SELECT count(*) FROM api_audit_events WHERE action = 'access.denied'"
    )
    assert (failures, denied) == (4, 2)


async def test_clients_cannot_see_each_others_batches(app: FastAPI) -> None:
    _, _, key_a = await new_client(app)
    _, _, key_b = await new_client(app, nit="800554321-9")
    async with http(app) as client:
        a = await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key_a))
        # Same batch and shipment references for another tenant are independent.
        b = await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key_b))
        batch_a = a.json()["lote_id"]
        peek = await client.get(f"{URL}/{batch_a}", headers=auth(key_b))
        peek_items = await client.get(f"{URL}/{batch_a}/items", headers=auth(key_b))
        own = await client.get(f"{URL}/{batch_a}", headers=auth(key_a))

    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["lote_id"] != b.json()["lote_id"]
    assert b.json()["aceptados"] == 1
    for response in (peek, peek_items):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NO_ENCONTRADO"
    assert own.status_code == 200


# --- partial processing -------------------------------------------------------------------


async def test_valid_items_are_created_and_invalid_ones_rejected_individually(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    body = lote(
        "L-1",
        envio("P-1"),
        envio("P-2", destino="Envigado"),
        envio("P-3", destinatario={"nombre": "X", "telefono": "12", "direccion": "Y"}),
        envio("P-4", origen="Gotham"),
        envio("P-1"),
        envio("P-5", destino="Cali"),
    )
    async with http(app) as client:
        response = await client.post(URL, json=body, headers=auth(key))

    data = response.json()
    assert response.status_code == 201
    assert (data["status"], data["total"], data["aceptados"], data["rechazados"]) == (
        "COMPLETED_WITH_ERRORS",
        6,
        2,
        4,
    )
    by_line = {r["linea"]: r for r in data["resultados"]}
    assert [by_line[n]["status"] for n in range(1, 7)] == [
        "CREATED",
        "REJECTED",
        "REJECTED",
        "REJECTED",
        "REJECTED",
        "CREATED",
    ]
    assert by_line[2]["errores"] == [{"code": "TARIFA_NO_DISPONIBLE", "field": "destino"}]
    assert by_line[3]["errores"] == [{"code": "VALIDACION", "field": "destinatario.telefono"}]
    assert by_line[4]["errores"] == [{"code": "CIUDAD_NO_RECONOCIDA", "field": "origen"}]
    assert by_line[5]["errores"] == [
        {"code": "REFERENCIA_DUPLICADA", "field": "referencia_cliente"}
    ]
    for line in (1, 6):
        guia = by_line[line]["numero_guia"]
        assert re.match(NUMERO_GUIA_PATTERN, guia)
        assert GUIDE_MIN <= int(guia[4:10]) <= GUIDE_MAX
        assert by_line[line]["formula_version"] == "provisional-v1"
    assert by_line[1]["flete_total_cop"] == 21000
    assert await scalar(app, "SELECT count(*) FROM shipments") == 2
    assert await scalar(app, "SELECT count(*) FROM shipment_recipients") == 2


async def test_price_snapshot_keeps_formula_tariff_version_and_quote(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    async with http(app) as client:
        await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key))

    async with app.state.container.sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT s.formula_version, s.tarifa_version, s.cotizado_en IS NOT NULL,"
                    " s.flete_total_cop, s.peso_volumetrico_kg, s.peso_facturable_kg,"
                    " s.origen_codigo_dane, s.destino_codigo_dane, q.formula_version, s.estado"
                    " FROM shipments s JOIN quotes q ON q.cotizacion_id = s.cotizacion_id"
                )
            )
        ).one()
    assert row[0] == row[8] == "provisional-v1"
    assert row[1].startswith("matriz_tarifas_y_fletes.csv@sha256:")
    assert row[2] is True
    assert (row[3], float(row[4]), float(row[5])) == (21000, 6.0, 6.0)
    assert (row[6], row[7], row[9]) == ("11001", "05001", "REGISTRADO")


async def test_a_failing_item_rolls_back_alone(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shipment and recipient are atomic, and no guía is confirmed for the failed item."""
    original = shipment_repos.ShipmentRepository.add

    async def add_then_fail(self: Any, draft: Any, *args: Any) -> UUID:
        shipment_id = await original(self, draft, *args)
        if draft.referencia_cliente == "P-FAIL":
            raise RuntimeError("simulated failure after the inserts")
        return shipment_id

    monkeypatch.setattr(shipment_repos.ShipmentRepository, "add", add_then_fail)
    _, _, key = await new_client(app)
    async with http(app) as client:
        response = await client.post(
            URL, json=lote("L-1", envio("P-1"), envio("P-FAIL"), envio("P-3")), headers=auth(key)
        )

    results = response.json()["resultados"]
    assert [r["status"] for r in results] == ["CREATED", "REJECTED", "CREATED"]
    assert results[1]["errores"] == [{"code": "ERROR_INTERNO", "field": None}]
    assert results[1]["numero_guia"] is None
    assert (
        await scalar(app, "SELECT count(*) FROM shipments WHERE referencia_cliente = 'P-FAIL'") == 0
    )
    assert await scalar(app, "SELECT count(*) FROM shipments") == 2
    assert await scalar(app, "SELECT count(*) FROM shipment_recipients") == 2


async def test_references_already_used_in_earlier_batches_are_rejected(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    async with http(app) as client:
        await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key))
        second = await client.post(
            URL, json=lote("L-2", envio("P-1"), envio("P-2")), headers=auth(key)
        )

    results = second.json()["resultados"]
    assert [r["status"] for r in results] == ["REJECTED", "CREATED"]
    assert results[0]["errores"] == [
        {"code": "REFERENCIA_DUPLICADA", "field": "referencia_cliente"}
    ]


# --- idempotency and concurrency ----------------------------------------------------------


async def test_identical_replay_returns_the_same_batch_without_new_shipments(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    body = lote("L-1", envio("P-1"), envio("P-2", destino="Envigado"))
    async with http(app) as client:
        first = await client.post(URL, json=body, headers=auth(key))
        replay = await client.post(URL, json=body, headers=auth(key) | {"Idempotency-Key": "L-1"})

    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json()["lote_id"] == first.json()["lote_id"]
    assert replay.json()["resultados"] == first.json()["resultados"]
    assert await scalar(app, "SELECT count(*) FROM shipments") == 1


async def test_same_reference_with_different_content_is_a_conflict(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    async with http(app) as client:
        await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key))
        conflict = await client.post(
            URL, json=lote("L-1", envio("P-1", peso_real_kg=9)), headers=auth(key)
        )
        mismatch = await client.post(
            URL, json=lote("L-2", envio("P-9")), headers=auth(key) | {"Idempotency-Key": "OTHER"}
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCIA_CONFLICTO"
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["details"]["campos"][0]["loc"] == ["header", "Idempotency-Key"]
    assert await scalar(app, "SELECT count(*) FROM shipment_batches") == 1


async def test_concurrent_identical_submissions_create_each_shipment_once(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    body = lote("L-1", *(envio(f"P-{n}") for n in range(8)))
    async with http(app) as client:
        responses = await asyncio.gather(
            *(client.post(URL, json=body, headers=auth(key)) for _ in range(3))
        )

    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(201) == 1
    assert set(statuses) <= {200, 201, 202}
    assert await scalar(app, "SELECT count(*) FROM shipment_batches") == 1
    assert await scalar(app, "SELECT count(*) FROM shipments") == 8


async def test_guias_stay_unique_under_concurrent_batches(app: FastAPI) -> None:
    keys = [(await new_client(app, nit=f"90012345{n}-1"))[2] for n in range(3)]
    async with http(app) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    URL, json=lote("L-1", *(envio(f"P-{n}") for n in range(10))), headers=auth(k)
                )
                for k in keys
            )
        )

    guias = [r["numero_guia"] for resp in responses for r in resp.json()["resultados"]]
    assert all(resp.status_code == 201 for resp in responses)
    assert len(guias) == len(set(guias)) == 30
    assert all(GUIDE_MIN <= int(g[4:10]) <= GUIDE_MAX for g in guias)


async def test_an_interrupted_batch_resumes_its_pending_items_on_replay(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_id, key_id, key = await new_client(app)
    context = ClientContext(client_id, key_id, frozenset(ApiScope))
    body = lote("L-1", envio("P-1"), envio("P-2"), envio("P-3"))
    service: BatchService = app.state.container.batches
    original = BatchService._process_item
    calls = 0

    async def crash_on_second(self: BatchService, *args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError  # the process dies mid-batch
        await original(self, *args)

    monkeypatch.setattr(BatchService, "_process_item", crash_on_second)
    with pytest.raises(asyncio.CancelledError):
        await service.submit(context, body["referencia_lote"], body["envios"])
    monkeypatch.setattr(BatchService, "_process_item", original)

    async with http(app) as client:
        while_leased = await client.post(URL, json=body, headers=auth(key))
        async with app.state.container.sessions() as session:  # the lease expires
            await session.execute(
                text("UPDATE shipment_batches SET locked_until = now() - interval '1 s'")
            )
            await session.commit()
        resumed = await client.post(URL, json=body, headers=auth(key))

    assert while_leased.status_code == 202
    assert while_leased.json()["status"] == "PROCESSING"
    assert while_leased.json()["pendientes"] == 2
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "COMPLETED"
    assert [r["status"] for r in resumed.json()["resultados"]] == ["CREATED"] * 3
    assert await scalar(app, "SELECT count(*) FROM shipments") == 3


# --- limits and validation ----------------------------------------------------------------


async def test_size_limits_and_malformed_requests(make_app: AppFactory) -> None:
    async with make_app(batch_max_items=2, batch_max_body_bytes=4000) as app:
        _, _, key = await new_client(app)
        async with http(app) as client:
            too_many = await client.post(
                URL, json=lote("L-1", envio("P-1"), envio("P-2"), envio("P-3")), headers=auth(key)
            )
            too_big = await client.post(
                URL,
                content=json.dumps({"referencia_lote": "L-2", "envios": ["x" * 5000]}),
                headers=auth(key) | {"Content-Type": "application/json"},
            )
            not_json = await client.post(URL, content=b"{oops", headers=auth(key))
            no_reference = await client.post(
                URL, json={"envios": [envio("P-1")]}, headers=auth(key)
            )
            empty = await client.post(URL, json=lote("L-3"), headers=auth(key))

    for response in (too_many, too_big):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "LOTE_DEMASIADO_GRANDE"
    for response in (not_json, no_reference, empty):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDACION"


async def test_batch_creation_is_rate_limited_per_client(make_app: AppFactory) -> None:
    async with make_app(batch_rate_limit_per_minute=1) as app:
        _, _, key = await new_client(app)
        async with http(app) as client:
            first = await client.post(URL, json=lote("L-1", envio("P-1")), headers=auth(key))
            second = await client.post(URL, json=lote("L-2", envio("P-2")), headers=auth(key))

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "LIMITE_DE_TASA"


# --- reading ---------------------------------------------------------------------------------


async def test_status_and_paginated_results(app: FastAPI) -> None:
    _, _, key = await new_client(app)
    body = lote(
        "L-1",
        envio("P-1"),
        envio("P-2", destino="Envigado"),
        envio("P-3"),
        envio("P-4", origen="X"),
    )
    async with http(app) as client:
        created = await client.post(URL, json=body, headers=auth(key))
        batch = created.json()["lote_id"]
        summary = await client.get(f"{URL}/{batch}", headers=auth(key))
        page1 = await client.get(f"{URL}/{batch}/items", params={"limit": 3}, headers=auth(key))
        page2 = await client.get(
            f"{URL}/{batch}/items",
            params={"limit": 3, "cursor": page1.json()["next_cursor"]},
            headers=auth(key),
        )
        rejected = await client.get(
            f"{URL}/{batch}/items", params={"status": "REJECTED"}, headers=auth(key)
        )
        bad_cursor = await client.get(
            f"{URL}/{batch}/items", params={"cursor": "zzz"}, headers=auth(key)
        )
        unknown = await client.get(f"{URL}/00000000-0000-4000-8000-000000000000", headers=auth(key))

    assert summary.json() | {"created_at": None, "started_at": None, "completed_at": None} == {
        "lote_id": batch,
        "referencia_lote": "L-1",
        "status": "COMPLETED_WITH_ERRORS",
        "total": 4,
        "aceptados": 2,
        "rechazados": 2,
        "pendientes": 0,
        "created_at": None,
        "started_at": None,
        "completed_at": None,
    }
    assert [i["linea"] for i in page1.json()["items"]] == [1, 2, 3]
    assert [i["linea"] for i in page2.json()["items"]] == [4]
    assert page2.json()["next_cursor"] is None
    assert [i["linea"] for i in rejected.json()["items"]] == [2, 4]
    assert bad_cursor.status_code == 422
    assert unknown.status_code == 404


# --- PII ----------------------------------------------------------------------------------------


async def test_personal_data_never_leaves_the_recipients_table(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    _, _, key = await new_client(app)
    body = lote(
        "L-1",
        envio("P-1"),
        envio("P-2", destinatario={"nombre": PII[0], "telefono": "12", "direccion": PII[2]}),
    )
    caplog.set_level(logging.DEBUG)
    async with http(app) as client:
        response = await client.post(URL, json=body, headers=auth(key))
        items = await client.get(f"{URL}/{response.json()['lote_id']}/items", headers=auth(key))

    exposed: list[tuple[str, str]] = [
        ("response", response.text),
        ("items", items.text),
        ("logs", caplog.text),
    ]
    async with app.state.container.sessions() as session:
        for table in ("shipment_batches", "shipment_batch_items", "shipments", "api_audit_events"):
            rows = (await session.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
            exposed.append((table, json.dumps([list(r) for r in rows], default=str)))
        recipients = (
            await session.execute(text("SELECT nombre, telefono FROM shipment_recipients"))
        ).all()
    for where, content in exposed:
        for value in PII:
            assert value not in content, (where, value)
    assert [tuple(r) for r in recipients] == [(PII[0], PII[1])]


# --- HTTP contract --------------------------------------------------------------------------


def test_real_openapi_matches_the_contract_for_bulk_shipments(app: FastAPI) -> None:
    real, contract = app.openapi(), build_contract_app().openapi()

    for path in (URL, f"{URL}/{{batch_id}}", f"{URL}/{{batch_id}}/items"):
        for method, operation in contract["paths"][path].items():
            mine = real["paths"][path][method]
            assert mine["operationId"] == operation["operationId"]
            assert mine["security"] == operation["security"]
            assert sorted(p["name"] for p in mine.get("parameters", [])) == sorted(
                p["name"] for p in operation.get("parameters", [])
            )
            assert mine.get("requestBody") == operation.get("requestBody")
            for status in ("200", "201", "202"):
                assert mine["responses"].get(status, {}).get("content") == operation[
                    "responses"
                ].get(status, {}).get("content")
    for name in ("ShipmentBatchRequest", "EnvioLoteItem", "Destinatario"):
        assert real["components"]["schemas"][name] == contract["components"]["schemas"][name]
