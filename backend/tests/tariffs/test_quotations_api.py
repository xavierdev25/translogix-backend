"""Profile quotations against PostgreSQL through the real app (CR-003, spec 1.1 §11)."""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.contracts.api_spec import build_contract_app
from app.contracts.common import ContractError, ErrorCode
from app.tariffs.application.service import TariffAdminService, TariffProfileDefinition
from tests.db import AppFactory, http, scalar
from tests.tariffs.seed import DANE, SEED_NAME, SEED_VERSION

URL = "/api/v1/cotizaciones"


def body(destino: str = "Barranquilla", kg: float = 6.2, **overrides: Any) -> dict[str, Any]:
    return {
        "origen": "Bogotá D.C.",
        "destino": destino,
        "peso_real_kg": kg,
        "alto_cm": 20,
        "ancho_cm": 30,
        "largo_cm": 40,
    } | overrides


_RESTORE = (
    "DELETE FROM tariff_quotations WHERE tariff_profile_id IN"
    " (SELECT id FROM tariff_profiles WHERE NOT (name = :n AND version = :v))",
    "DELETE FROM tariff_rules WHERE tariff_profile_id IN"
    " (SELECT id FROM tariff_profiles WHERE NOT (name = :n AND version = :v))",
    "DELETE FROM tariff_profiles WHERE NOT (name = :n AND version = :v)",
    "UPDATE tariff_profiles SET status = 'ACTIVE', effective_until = NULL WHERE name = :n",
)


async def _restore_seed(app: FastAPI) -> None:
    """Back to the migration state: only ``matriz-pruebas`` v1, active."""
    async with app.state.container.sessions() as session:
        for statement in _RESTORE:
            await session.execute(text(statement), {"n": SEED_NAME, "v": SEED_VERSION})
        await session.commit()


@pytest.fixture
async def app(make_app: AppFactory) -> AsyncIterator[FastAPI]:
    async with make_app() as instance:
        await _restore_seed(instance)
        yield instance
        await _restore_seed(instance)


async def admin(app: FastAPI, action: str, *args: Any) -> Any:
    box = app.state.container
    async with box.sessions() as session:
        result = await getattr(TariffAdminService(session, box.tariffs, box.clock), action)(*args)
        await session.commit()
    return result


def definition(version: int, base: int, **overrides: Any) -> TariffProfileDefinition:
    data = {
        "name": SEED_NAME,
        "version": version,
        "environment_type": "TEST_ONLY",
        "iva_pct": "19.00",
        "rules": [
            {
                "destination_city_code": DANE["Barranquilla"],
                "base_price_cop": base,
                "included_weight_kg": "5.50",
                "excess_price_per_kg_cop": 5000,
            }
        ],
    }
    return TariffProfileDefinition.model_validate(data | overrides)


async def seed_id(app: FastAPI) -> UUID:
    profile_id: UUID = await scalar(
        app, "SELECT id FROM tariff_profiles WHERE name = :n AND version = 1", n=SEED_NAME
    )
    return profile_id


# --- quotation -------------------------------------------------------------------------------


async def test_quote_uses_the_test_only_profile_and_keeps_a_snapshot(app: FastAPI) -> None:
    async with http(app) as client:
        response = await client.post(URL, json=body())

    data = response.json()
    assert response.status_code == 201
    assert (data["flete_cop"], data["iva_cop"], data["total_cop"]) == (31500, 5985, 37485)
    assert data["precio_comercial"] is False
    assert data["formula_version"] == "excedente-v1"
    assert data["tarifa"] | {"perfil_id": None} == {
        "perfil_id": None,
        "perfil_nombre": SEED_NAME,
        "perfil_version": 1,
        "tipo_perfil": "TEST_ONLY",
        "base_cop": 28000,
        "peso_incluido_kg": 5.5,
        "precio_kg_excedente_cop": 5000,
        "divisor_volumetrico": 4000,
        "iva_pct": 19.0,
    }
    assert data["destino"] == {"codigo_dane": "08001", "municipio": "Barranquilla"}
    async with app.state.container.sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT profile_type, profile_version, base_price_cop, included_weight_kg,"
                    " peso_excedente_kg, excess_price_per_kg_cop, flete_cop, iva_cop, total_cop,"
                    " peso_facturable_kg, precio_comercial FROM tariff_quotations WHERE id = :id"
                ),
                {"id": data["cotizacion_id"]},
            )
        ).one()
    assert tuple(row) == (
        "TEST_ONLY",
        1,
        28000,
        Decimal("5.50"),
        Decimal("0.70"),
        5000,
        31500,
        5985,
        37485,
        Decimal("6.20"),
        False,
    )


async def test_origin_is_recorded_but_does_not_change_the_price(app: FastAPI) -> None:
    async with http(app) as client:
        from_bogota = await client.post(URL, json=body(destino="Cali"))
        from_medellin = await client.post(URL, json=body(destino="Cali", origen="Medellín"))

    assert from_bogota.json()["total_cop"] == from_medellin.json()["total_cop"]
    assert from_medellin.json()["origen"]["codigo_dane"] == "05001"


async def test_billable_weight_includes_the_volumetric_weight(app: FastAPI) -> None:
    async with http(app) as client:
        response = await client.post(URL, json=body(kg=2, alto_cm=40, ancho_cm=50, largo_cm=60))

    data = response.json()
    assert (data["peso_volumetrico_kg"], data["peso_facturable_kg"]) == (30.0, 30.0)
    assert (data["flete_cop"], data["total_cop"]) == (150500, 179095)


async def test_errors_for_cities_rules_and_weights(app: FastAPI) -> None:
    async with http(app) as client:
        no_rule = await client.post(URL, json=body(destino="Medellín"))
        unknown = await client.post(URL, json=body(destino="Gotham"))
        too_precise = await client.post(URL, json=body(kg=6.255))
        oversized = await client.post(URL, json=body(alto_cm=900, ancho_cm=900, largo_cm=900))

    assert (no_rule.status_code, no_rule.json()["error"]["code"]) == (422, "TARIFA_NO_DISPONIBLE")
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (422, "CIUDAD_NO_RECONOCIDA")
    assert (too_precise.status_code, too_precise.json()["error"]["code"]) == (422, "VALIDACION")
    assert oversized.status_code == 201
    assert oversized.json()["advertencias"]
    assert await scalar(app, "SELECT count(*) FROM tariff_quotations") == 1


# --- profiles ------------------------------------------------------------------------------------


async def test_inactive_or_expired_profiles_are_not_used(app: FastAPI) -> None:
    profile_id = await seed_id(app)
    async with http(app) as client:
        await admin(app, "deactivate", profile_id)
        inactive = await client.post(URL, json=body())
        await admin(app, "activate", profile_id)
        async with app.state.container.sessions() as session:
            await session.execute(
                text(
                    "UPDATE tariff_profiles SET effective_until = now() - interval '1 s'"
                    " WHERE id = :id"
                ),
                {"id": profile_id},
            )
            await session.commit()
        expired = await client.post(URL, json=body())

    for response in (inactive, expired):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "NO_CONFIGURADO"


async def test_commercial_profiles_are_ignored_in_test_only_mode(make_app: AppFactory) -> None:
    async with make_app() as test_only:
        await _restore_seed(test_only)
        commercial = await admin(
            test_only, "load", definition(1, 99000, name="comercial", environment_type="COMMERCIAL")
        )
        await admin(test_only, "activate", commercial.id)
        async with http(test_only) as client:
            quote = await client.post(URL, json=body())
    async with make_app(tariff_environment="COMMERCIAL") as commercial_mode:
        async with http(commercial_mode) as client:
            commercial_quote = await client.post(URL, json=body())
        await _restore_seed(commercial_mode)

    assert quote.json()["tarifa"]["tipo_perfil"] == "TEST_ONLY"
    assert quote.json()["precio_comercial"] is False
    assert commercial_quote.json()["tarifa"]["tipo_perfil"] == "COMMERCIAL"
    assert commercial_quote.json()["precio_comercial"] is True
    assert commercial_quote.json()["flete_cop"] == 99000 + 3500


async def test_snapshots_survive_a_new_profile_version(app: FastAPI) -> None:
    async with http(app) as client:
        before = (await client.post(URL, json=body())).json()
        v2 = await admin(app, "load", definition(2, 30000))
        await admin(app, "activate", v2.id)
        after = (await client.post(URL, json=body())).json()

    assert (before["tarifa"]["perfil_version"], before["flete_cop"]) == (1, 31500)
    assert (after["tarifa"]["perfil_version"], after["flete_cop"]) == (2, 33500)
    stored = await scalar(
        app, "SELECT flete_cop FROM tariff_quotations WHERE id = :id", id=before["cotizacion_id"]
    )
    assert stored == 31500
    assert await scalar(app, "SELECT count(*) FROM tariff_profiles WHERE status = 'ACTIVE'") == 1


@pytest.mark.parametrize(
    ("version", "overrides", "message"),
    [
        (1, {}, "ya existe"),
        (
            3,
            {
                "rules": [
                    {
                        "destination_city_code": "99999",
                        "base_price_cop": 1,
                        "included_weight_kg": "1",
                        "excess_price_per_kg_cop": 1,
                    }
                ]
            },
            "DANE",
        ),
        (
            3,
            {
                "rules": 2
                * [
                    {
                        "destination_city_code": "08001",
                        "base_price_cop": 1,
                        "included_weight_kg": "1",
                        "excess_price_per_kg_cop": 1,
                    }
                ]
            },
            "repetidos",
        ),
    ],
)
async def test_profile_loading_is_validated(
    app: FastAPI, version: int, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ContractError) as error:
        await admin(app, "load", definition(version, 1000, **overrides))

    assert error.value.code is ErrorCode.VALIDACION
    assert message in error.value.message


# --- HTTP contract ---------------------------------------------------------------------------


def test_real_openapi_matches_the_contract_for_profile_quotations(app: FastAPI) -> None:
    real = app.openapi()["paths"][URL]["post"]
    contract = build_contract_app().openapi()["paths"][URL]["post"]

    assert real["operationId"] == contract["operationId"] == "createProfileQuotation"
    assert real["requestBody"] == contract["requestBody"]
    assert real["responses"]["201"]["content"] == contract["responses"]["201"]["content"]
