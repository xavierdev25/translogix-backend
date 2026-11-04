import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts.money import to_cop
from app.contracts.normalizers import extract_numero_guia, normalize_numero_guia
from app.contracts.pii import (
    PiiKind,
    canonical_external_user_id,
    find_pii,
    hash_external_user_id,
    mask_external_user_id,
)

SOURCES = Path(__file__).resolve().parents[3] / "data" / "source"
SALT = "test-salt-0123456789abcdef"


@pytest.mark.parametrize(
    "value",
    [
        "TLX-984021-CO",
        "tlx 984021 co",
        "TLX984021CO",
        "  tlx-984021-co ",
        "t l x nueve ocho cuatro cero dos uno c o",
        "te ele equis nueve ocho cuatro cero dos uno ce o",
        "T L X guion 9 8 4 0 2 1 guion C O",
        "tlx nueve ocho 4 0 dos 1 co",
    ],
)
def test_accepted_spellings_normalise_to_canonical(value: str) -> None:
    assert normalize_numero_guia(value) == "TLX-984021-CO"


@pytest.mark.parametrize(
    "value",
    ["", "TLX-98402-CO", "TLX-9840211-CO", "ABC-984021-CO", "TLX-984021-PE", "mi guía TLX984021CO"],
)
def test_other_inputs_are_not_guias(value: str) -> None:
    assert normalize_numero_guia(value) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Dónde está mi guía TLX-984021-CO?", "TLX-984021-CO"),
        ("mi guía es t l x tres uno cero cuatro ocho dos c o gracias", "TLX-310482-CO"),
        ("hola, quiero cotizar", None),
        ("la guía TLX-1234567-CO no existe", None),
    ],
)
def test_guia_is_extracted_from_free_text(text: str, expected: str | None) -> None:
    assert extract_numero_guia(text) == expected


def _raw_tms_descriptions() -> list[str]:
    dataset = json.loads((SOURCES / "dataset_guias_simuladas_tms.json").read_text("utf-8"))
    return [event["descripcion"] for guia in dataset for event in guia["historial_trazabilidad"]]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Asignado a conductor Juan Carlos Páez (Tel: 310-987-6543).", PiiKind.PHONE),
        ("Asignado a conductor Juan Carlos Páez.", PiiKind.PERSON_NAME),
        ("Consolidación en camión refrigerado Placa WKN-482.", PiiKind.PLATE),
        ("Documento C.C. 16.789.432", PiiKind.ID_DOCUMENT),
        ("NIT 900.123.456-7", PiiKind.NIT),
        ("Firma en https://cdn.translogix.com.co/pod/TLX-752190-CO_pod.png", PiiKind.URL),
        ("Posición 5.06889, -75.51738", PiiKind.GPS),
        ("Entregar en Carrera 15 # 4-22, Apt 502", PiiKind.ADDRESS),
        ("Recoger en Calle 26 # 96J-15, Bodega 4", PiiKind.ADDRESS),
        ("Mercancía recibida. Firmado por Ing. Roberto Bermúdez.", PiiKind.PERSON_NAME),
        ("Escribir a cliente@example.com", PiiKind.EMAIL),
    ],
)
def test_personal_data_is_detected(text: str, kind: PiiKind) -> None:
    assert kind in find_pii(text)


def test_raw_tms_dataset_contains_pii_that_must_be_sanitised() -> None:
    flagged = [text for text in _raw_tms_descriptions() if find_pii(text)]
    # Plate, driver name + phone, and full recipient name in the simulated TMS.
    assert len(flagged) == 3


@pytest.mark.parametrize(
    "text",
    [
        "Tu envío va en carretera hacia la ciudad de destino.",
        "Zona Franca Fontibón, Bogotá D.C.",
        "Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín",
        "En ruta con monitoreo activo; temperatura estable a 3.8 °C.",
        "Tu guía TLX-984021-CO va en camino.",
        "Precinto de seguridad #TLX-8841 aplicado.",
        "Mercancía peligrosa UN 1263, rotulado IMO Clase 3.",
        "Adjunta copia de la cédula o el RUT.",
        "Valor reclamado: $4.000.000 COP.",
        "Recibido por R. B.",
    ],
)
def test_ordinary_customer_text_is_not_flagged(text: str) -> None:
    assert find_pii(text) == set()


def test_same_person_hashes_identically_across_phone_formats() -> None:
    whatsapp = hash_external_user_id("573109876543", salt=SALT)
    pbx = hash_external_user_id("+57 310 987 6543", salt=SALT)

    assert whatsapp == pbx
    assert len(whatsapp) == 64
    assert whatsapp != hash_external_user_id("573109876543", salt=SALT + "x")


def test_web_client_ids_are_case_insensitive() -> None:
    upper = hash_external_user_id("5B9E2F4A-1C3D-4E8F-A7B6-2D1C0E9F8A7B", salt=SALT)

    assert upper == hash_external_user_id("5b9e2f4a-1c3d-4e8f-a7b6-2d1c0e9f8a7b", salt=SALT)


def test_short_salt_is_refused() -> None:
    with pytest.raises(ValueError, match="PII_HASH_SALT"):
        hash_external_user_id("573109876543", salt="short")


def test_mask_shows_only_last_four() -> None:
    assert mask_external_user_id("+57 310 987 4567") == "…4567"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("16493.75", 16494), ("65975", 65975), ("0.5", 1), ("2.4999", 2), ("190974.5", 190975)],
)
def test_cop_rounds_half_up(amount: str, expected: int) -> None:
    assert to_cop(Decimal(amount)) == expected


def test_cop_refuses_floats() -> None:
    with pytest.raises(TypeError):
        to_cop(16493.75)  # type: ignore[arg-type]


VECTORS = Path(__file__).resolve().parents[3] / "contracts" / "vectors"


def _vectors(name: str) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = json.loads((VECTORS / name).read_text("utf-8"))["casos"]
    return cases


@pytest.mark.parametrize(
    "case", _vectors("normalizacion_guia.json"), ids=lambda c: str(c["entrada"])
)
def test_reference_normaliser_matches_the_published_vectors(case: dict[str, object]) -> None:
    text = str(case["entrada"])

    assert normalize_numero_guia(text) == case["normalize"]
    assert extract_numero_guia(text) == case["extract"]


def test_reference_user_hash_matches_the_published_vectors() -> None:
    document = json.loads((VECTORS / "hash_usuario.json").read_text("utf-8"))
    salt = document["salt_de_prueba"]

    for case in document["casos"]:
        assert canonical_external_user_id(case["entrada"]) == case["canonico"]
        assert hash_external_user_id(case["entrada"], salt=salt) == case["hash"]
        assert mask_external_user_id(case["entrada"]) == case["mascara"]
    # One person on WhatsApp and PBX, one browser: two identities.
    assert len({case["hash"] for case in document["casos"]}) == 2


@pytest.mark.parametrize("case", _vectors("pii.json"), ids=lambda c: str(c["texto"])[:40])
def test_reference_pii_detector_matches_the_published_vectors(case: dict[str, object]) -> None:
    assert sorted(find_pii(str(case["texto"]))) == case["tipos"]
