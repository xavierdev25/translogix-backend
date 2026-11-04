"""Helpers shared by the bulk-shipment integration tests."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import FastAPI

from app.clients.application.services import ClientAdminService
from app.contracts.shipment_batches import ApiScope

ALL_SCOPES = (ApiScope.BATCHES_CREATE, ApiScope.BATCHES_READ)


async def new_client(
    app: FastAPI, nit: str = "900123456-7", scopes: Iterable[ApiScope] = ALL_SCOPES
) -> tuple[UUID, UUID, str]:
    """(client_id, api_key_id, full key) created through the admin service."""
    box = app.state.container
    async with box.sessions() as session:
        admin = ClientAdminService(session, box.clock)
        client = await admin.create_client(nit, f"Cliente {nit}")
        record, key = await admin.create_api_key(client.id, scopes)
        await session.commit()
    return client.id, record.id, key


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def envio(referencia: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "referencia_cliente": referencia,
        "origen": "BOGOTA",
        "destino": "MEDELLIN",
        "alto_cm": 20,
        "ancho_cm": 30,
        "largo_cm": 40,
        "peso_real_kg": 5.5,
        "valor_declarado_cop": 250000,
        "destinatario": {
            "nombre": "Zuleima Parrado",
            "telefono": "3157779911",
            "direccion": "Carrera 99 # 88-77",
        },
    }
    return base | overrides


def lote(referencia: str, *envios: dict[str, Any]) -> dict[str, Any]:
    return {"referencia_lote": referencia, "envios": list(envios)}
