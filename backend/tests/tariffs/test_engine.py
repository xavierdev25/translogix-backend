"""Unit tests of ``excedente-v1`` (spec 1.1 §11, tariffs): no database."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts.examples import EXAMPLES
from app.contracts.tariffs import (
    CotizacionPerfilRequest,
    CotizacionPerfilResponse,
    TipoPerfil,
)
from app.contracts.timezone import BOGOTA
from app.tariffs.domain.engine import (
    ProfileStatus,
    TariffProfile,
    compute,
    is_effective,
    volumetric_weight,
)
from tests.tariffs.seed import SEED_RULES

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=BOGOTA)
PROFILE = TariffProfile(
    id=uuid4(),
    name="matriz-pruebas",
    version=1,
    environment_type=TipoPerfil.TEST_ONLY,
    status=ProfileStatus.ACTIVE,
    iva_pct=Decimal("19.00"),
    volumetric_divisor=4000,
    effective_from=NOW - timedelta(days=1),
    effective_until=None,
)
SMALL = {"alto_cm": Decimal(10), "ancho_cm": Decimal(10), "largo_cm": Decimal(10)}  # 0.25 kg


def freight(city: str, kg: str, **dims: Decimal) -> tuple[int, int, int]:
    result = compute(SEED_RULES[city], PROFILE, peso_real_kg=Decimal(kg), **(SMALL | dims))
    return result.flete_cop, result.iva_cop, result.total_cop


# Freight (COP) by destination for weights below, at and just above L, fractional and high.
EXPECTED = {
    "Barranquilla": {
        "3": 28000,
        "5.5": 28000,
        "5.51": 28050,
        "6.2": 31500,
        "7.25": 36750,
        "50": 250500,
    },
    "Bucaramanga": {
        "3": 19300,
        "5": 19300,
        "5.01": 19336,
        "6.2": 23620,
        "7.25": 27400,
        "50": 181300,
    },
    "Cali": {"3": 19800, "5": 19800, "5.01": 19837, "6.2": 24240, "7.25": 28125, "50": 186300},
    "Cartagena": {
        "3": 26900,
        "5.5": 26900,
        "5.51": 26948,
        "6.2": 30260,
        "7.25": 35300,
        "50": 240500,
    },
    "Cúcuta": {"3": 23800, "5": 23800, "5.01": 23844, "6.2": 29080, "7.25": 33700, "50": 221800},
}


@pytest.mark.parametrize(
    ("city", "kg", "flete"),
    [(city, kg, flete) for city, cases in EXPECTED.items() for kg, flete in cases.items()],
)
def test_freight_matches_the_approved_formula(city: str, kg: str, flete: int) -> None:
    assert freight(city, kg)[0] == flete


def test_spec_example_barranquilla_with_iva() -> None:
    assert freight("Barranquilla", "6.2") == (31500, 5985, 37485)


def test_excess_starts_right_after_the_included_weight_and_is_not_rounded_up() -> None:
    result = compute(SEED_RULES["Barranquilla"], PROFILE, peso_real_kg=Decimal("5.51"), **SMALL)

    assert result.peso_excedente_kg == Decimal("0.01")
    assert result.flete_cop == 28050  # 0.01 kg x 5000, not a whole extra kilogram


def test_iva_is_rounded_half_up_to_whole_pesos() -> None:
    flete, iva, total = freight("Bucaramanga", "5.01")  # 19336 x 19 % = 3673.84

    assert (flete, iva, total) == (19336, 3674, 23010)


def test_volumetric_weight_is_billed_when_it_exceeds_the_real_weight() -> None:
    result = compute(
        SEED_RULES["Barranquilla"],
        PROFILE,
        peso_real_kg=Decimal(2),
        alto_cm=Decimal(40),
        ancho_cm=Decimal(50),
        largo_cm=Decimal(60),
    )

    assert result.peso_volumetrico_kg == Decimal("30.00")
    assert result.peso_facturable_kg == Decimal("30.00")
    assert (result.flete_cop, result.iva_cop, result.total_cop) == (150500, 28595, 179095)


@pytest.mark.parametrize(
    ("dims", "expected"),
    [
        ((33, 47, 51), "19.78"),  # 19.77525
        ((10, 10, "0.2"), "0.01"),  # 0.005: half-up, not half-even
        ((40, 50, 60), "30.00"),
    ],
)
def test_volumetric_weight_keeps_two_decimals_half_up(dims: tuple[Any, ...], expected: str) -> None:
    alto, ancho, largo = (Decimal(str(d)) for d in dims)
    assert volumetric_weight(alto, ancho, largo, 4000) == Decimal(expected)


def test_only_an_active_profile_inside_its_dates_is_effective() -> None:
    assert is_effective(PROFILE, NOW)
    assert not is_effective(_replace(status=ProfileStatus.INACTIVE), NOW)
    assert not is_effective(_replace(effective_from=NOW + timedelta(seconds=1)), NOW)
    assert not is_effective(_replace(effective_until=NOW), NOW)


def test_request_accepts_two_decimals_at_most() -> None:
    base = {"origen": "Bogotá", "destino": "Cali", "alto_cm": 10, "ancho_cm": 10, "largo_cm": 10}
    CotizacionPerfilRequest.model_validate(base | {"peso_real_kg": 6.25})
    for invalid in (6.255, 0, -1):
        with pytest.raises(ValidationError):
            CotizacionPerfilRequest.model_validate(base | {"peso_real_kg": invalid})


def test_response_rejects_numbers_that_do_not_add_up(example_response: dict[str, Any]) -> None:
    CotizacionPerfilResponse.model_validate(example_response)
    for broken in (
        {"total_cop": 37486},
        {"iva_cop": 5984, "total_cop": 37484},
        {"flete_cop": 31000, "total_cop": 36985},
        {"precio_comercial": True},
    ):
        with pytest.raises(ValidationError):
            CotizacionPerfilResponse.model_validate(example_response | broken)


def _replace(**changes: Any) -> TariffProfile:
    return TariffProfile(**(PROFILE.__dict__ | changes))


@pytest.fixture
def example_response() -> dict[str, Any]:
    example = next(e for e in EXAMPLES if e.name == "cotizaciones.response")
    data: dict[str, Any] = example.load()
    return data
