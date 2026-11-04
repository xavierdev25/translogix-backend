"""Unit tests of the bulk-shipment domain: no database, real tariff and coverage data."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.batches.domain.models import (
    canonical_hash,
    decode_cursor,
    encode_cursor,
    flag_duplicate_references,
    parse_items,
)
from app.clients.domain.models import (
    KEY_PATTERN,
    hash_api_key,
    key_prefix_of,
    new_api_key,
    normalize_nit,
)
from app.contracts.common import ErrorCode
from app.contracts.normalizers import NUMERO_GUIA_PATTERN
from app.contracts.shipment_batches import EnvioLoteItem
from app.contracts.timezone import BOGOTA
from app.modules.quotation.data import Coverage, TariffData
from app.modules.quotation.service import QuotationService
from app.shared.clock import build_clock
from app.shipments.application.service import ShipmentService, tariff_version
from app.shipments.domain.models import GUIDE_MAX, GUIDE_MIN, ShipmentDraft, format_guia

SOURCE = Path(__file__).resolve().parents[3] / "data" / "source"
TARIFFS = SOURCE / "matriz_tarifas_y_fletes.csv"
COVERAGE = SOURCE / "directorio_cobertura_y_tiempos.json"
PHONE = "3157779911"


def item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "referencia_cliente": "PEDIDO-1001",
        "origen": "BOGOTA",
        "destino": "MEDELLIN",
        "alto_cm": 20,
        "ancho_cm": 30,
        "largo_cm": 40,
        "peso_real_kg": 5.5,
        "valor_declarado_cop": 250000,
        "destinatario": {
            "nombre": "Zuleima Parrado",
            "telefono": PHONE,
            "direccion": "Cra 99 # 88-77",
        },
    }
    return base | overrides


@pytest.fixture(scope="module")
def tariffs() -> TariffData:
    return TariffData(TARIFFS, COVERAGE)


@pytest.fixture
def service(tariffs: TariffData) -> ShipmentService:
    clock = build_clock(datetime(2026, 11, 4, 9, 0, tzinfo=BOGOTA))
    return ShipmentService(
        tariffs, QuotationService(tariffs), tarifa_version=tariff_version(TARIFFS), clock=clock
    )


# --- clients -----------------------------------------------------------------------


def test_api_keys_are_random_prefixed_and_stored_only_as_hash() -> None:
    prefix, key = new_api_key()
    other_prefix, other = new_api_key()

    assert KEY_PATTERN.match(key)
    assert key_prefix_of(key) == prefix
    assert prefix != other_prefix
    assert key != other
    assert hash_api_key(key) == hash_api_key(key) != key
    assert len(hash_api_key(key)) == 64


@pytest.mark.parametrize("raw", ["", "Bearer x", "tlx_short_secret", f"tlx_{'g' * 12}_{'a' * 43}"])
def test_malformed_keys_have_no_prefix(raw: str) -> None:
    assert key_prefix_of(raw) is None


def test_nit_is_normalised_and_validated() -> None:
    assert normalize_nit("900.123.456-7") == "900123456-7"
    assert normalize_nit(" 800 554 321 ") == "800554321"
    with pytest.raises(ValueError, match="NIT"):
        normalize_nit("ABC-123")


# --- guías ---------------------------------------------------------------------------


def test_guias_use_the_canonical_format_inside_the_reserved_range() -> None:
    assert format_guia(GUIDE_MIN) == "TLX-200000-CO"
    assert re.match(NUMERO_GUIA_PATTERN, format_guia(GUIDE_MAX))
    for outside in (GUIDE_MIN - 1, GUIDE_MAX + 1):
        with pytest.raises(ValueError, match="rango"):
            format_guia(outside)


def test_reserved_range_does_not_collide_with_the_simulated_dataset() -> None:
    dataset = json.loads((SOURCE / "dataset_guias_simuladas_tms.json").read_text())
    numbers = {int(s["numero_guia"][4:10]) for s in dataset}

    assert numbers
    assert not any(GUIDE_MIN <= n <= GUIDE_MAX for n in numbers)


# --- batch parsing ---------------------------------------------------------------------


def test_invalid_items_report_field_paths_never_values() -> None:
    parsed = parse_items(
        [item(), item(destinatario={"nombre": "X", "telefono": "12", "direccion": "Y"}), 7]
    )

    assert parsed[0].item is not None
    assert parsed[0].errors == []
    assert [e.field for e in parsed[1].errors] == ["destinatario.telefono"]
    assert parsed[1].errors[0].code is ErrorCode.VALIDACION
    assert parsed[1].referencia_cliente == "PEDIDO-1001"  # still usable for duplicate checks
    assert parsed[2].referencia_cliente is None
    assert parsed[2].errors[0].field is None
    dumped = json.dumps([e.model_dump(mode="json") for p in parsed for e in p.errors])
    assert "12" not in dumped
    assert PHONE not in dumped


def test_only_repetitions_after_the_first_line_are_duplicates() -> None:
    parsed = parse_items(
        [item(), item(referencia_cliente="PEDIDO-2"), item(), item(referencia_cliente="PEDIDO-2")]
    )
    flag_duplicate_references(parsed)

    assert [p.item is not None for p in parsed] == [True, True, False, False]
    assert {e.code for p in parsed[2:] for e in p.errors} == {ErrorCode.REFERENCIA_DUPLICADA}


def test_canonical_hash_ignores_key_order_and_number_spelling() -> None:
    first = [item()]
    reordered = [dict(reversed(list(item(alto_cm=20.0).items())))]
    changed = [item(peso_real_kg=6)]

    def digest(raw: list[Any]) -> str:
        return canonical_hash("LOTE-1", parse_items(raw), raw)

    assert digest(first) == digest(reordered)
    assert digest(first) != digest(changed)
    assert canonical_hash("LOTE-2", parse_items(first), first) != digest(first)


def test_cursor_round_trip_and_rejection() -> None:
    assert decode_cursor(encode_cursor(42)) == 42
    for bad in ("not-a-cursor", "AAAA", "%%%%", "nw"):
        with pytest.raises(ValueError, match="cursor"):
            decode_cursor(bad)


# --- shipment validation and quotation -------------------------------------------------


def test_valid_item_is_quoted_with_the_existing_engine(service: ShipmentService) -> None:
    draft = service.prepare(uuid4(), EnvioLoteItem.model_validate(item()))

    assert isinstance(draft, ShipmentDraft)
    assert (draft.origen_codigo_dane, draft.destino_codigo_dane) == ("11001", "05001")
    assert draft.request.origen == "Bogotá D.C."
    assert draft.codigo_trayecto == "TLX-BOG-MED-001"
    calc = draft.quote.calculo
    assert (calc.peso_volumetrico_kg, calc.peso_facturable_kg, calc.flete_total_cop) == (
        6.0,
        6.0,
        21000,
    )
    assert draft.quote.formula_version == "provisional-v1"
    assert draft.tarifa_version.startswith("matriz_tarifas_y_fletes.csv@sha256:")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"destino": "Envigado"}, (ErrorCode.TARIFA_NO_DISPONIBLE, "destino")),
        ({"origen": "Gotham"}, (ErrorCode.CIUDAD_NO_RECONOCIDA, "origen")),
        (
            {"destino": "Quibdó", "requiere_cadena_frio": True},
            (ErrorCode.SERVICIO_NO_DISPONIBLE, "requiere_cadena_frio"),
        ),
    ],
)
def test_business_rejections_carry_code_and_field(
    service: ShipmentService, overrides: dict[str, Any], expected: tuple[ErrorCode, str]
) -> None:
    errors = service.prepare(uuid4(), EnvioLoteItem.model_validate(item(**overrides)))

    assert isinstance(errors, list)
    assert [(e.code, e.field) for e in errors] == [expected]


def test_recognised_city_without_coverage_is_not_covered(tariffs: TariffData) -> None:
    class WithoutMedellin(TariffData):
        def __init__(self) -> None:
            self.__dict__ = tariffs.__dict__

        def coverage_for(self, city: str) -> Coverage | None:
            return None if city == "Medellín" else super().coverage_for(city)

    data = WithoutMedellin()
    service = ShipmentService(
        data, QuotationService(data), tarifa_version="t", clock=build_clock(datetime.now(BOGOTA))
    )
    errors = service.prepare(uuid4(), EnvioLoteItem.model_validate(item()))

    assert isinstance(errors, list)
    assert [(e.code, e.field) for e in errors] == [(ErrorCode.CIUDAD_NO_CUBIERTA, "destino")]
