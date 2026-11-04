"""The published OpenAPI document: current, valid, and faithful to the conventions."""

from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate

from app.contracts.artifacts import stale_artifacts
from app.contracts.common import ERROR_HTTP_STATUS, ErrorCode
from app.contracts.env import SECRET_ENV_VARS, EnvVar
from app.contracts.openapi import build_openapi, render_openapi_yaml

ROOT = Path(__file__).resolve().parents[3]
OPENAPI_PATH = ROOT / "contracts" / "openapi.yaml"

# §5 of the contract document: the public surface, nothing more, nothing less.
EXPECTED_SURFACE = {
    ("get", "/api/v1/tracking/{guia}"),
    ("post", "/api/v1/cotizar"),
    ("post", "/api/v1/cotizaciones"),  # 1.5.0: tariff profiles (CR-003, P1)
    ("post", "/api/v1/pqrs/crear-ticket"),
    ("post", "/api/v1/attachments"),  # added by the P01 prompt (P1)
    ("post", "/api/v1/chat/messages"),
    ("get", "/api/v1/chat/sessions/{session_id}/history"),
    ("get", "/api/v1/channels/whatsapp/webhook"),
    ("post", "/api/v1/channels/whatsapp/webhook"),
    ("post", "/api/v1/channels/whatsapp/simulate"),
    ("post", "/api/v1/channels/pbx/turns"),
    ("post", "/api/v1/channels/pbx/simulate"),
    ("get", "/api/v1/handoffs/{handoff_id}"),
    ("patch", "/api/v1/handoffs/{handoff_id}"),  # advisor inbox, needed by P07 (P1)
    ("get", "/api/v1/handoffs"),
    ("get", "/api/v1/metrics/channels"),
    ("post", "/api/v1/admin/rag/reindex"),
    ("post", "/api/v1/shipment-batches"),  # 1.3.0: bulk shipments (B2B, P0)
    ("get", "/api/v1/shipment-batches/{batch_id}"),
    ("get", "/api/v1/shipment-batches/{batch_id}/items"),
    ("get", "/health"),
    ("get", "/ready"),
}


def operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, operation)
        for path, item in document["paths"].items()
        for method, operation in item.items()
    ]


def test_generated_artifacts_are_up_to_date() -> None:
    # openapi.yaml, schemas/, mocks/manifest.json and the TS types' source hash.
    assert stale_artifacts() == [], (
        "regenerate: `uv run python -m app.contracts` and `contracts/types/generate.sh`"
    )


def test_document_is_valid_openapi_3_1() -> None:
    document = build_openapi()

    validate(document)
    assert document["openapi"].startswith("3.1")


def test_surface_matches_section_5_plus_additions_and_websocket() -> None:
    document = build_openapi()

    assert {(method, path) for method, path, _ in operations(document)} == EXPECTED_SURFACE
    assert list(document["x-websockets"]) == ["/ws/chat/{session_id}"]


def test_operation_ids_are_unique_and_prioritised() -> None:
    ops = operations(build_openapi())
    ids = [operation["operationId"] for _, _, operation in ops]

    assert len(ids) == len(set(ids))
    assert {operation["x-priority"] for _, _, operation in ops} <= {"P0", "P1"}


def test_every_error_response_uses_the_envelope_and_the_right_status() -> None:
    for method, path, operation in operations(build_openapi()):
        for status, response in operation["responses"].items():
            if path == "/ready" and status == "503":
                continue  # readiness reports its checks, not an error
            if not status.startswith(("4", "5")):
                continue
            schema = response["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}, (method, path)
            codes = response["x-error-codes"]
            assert codes, (method, path, status)
            assert all(str(ERROR_HTTP_STATUS[ErrorCode(code)]) == status for code in codes)
        assert "500" in operation["responses"], (method, path)


def test_fastapi_validation_body_is_not_published() -> None:
    schemas = build_openapi()["components"]["schemas"]

    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas


def test_every_operation_documents_the_request_id() -> None:
    for method, path, operation in operations(build_openapi()):
        params = operation["parameters"]
        assert {"$ref": "#/components/parameters/XRequestId"} in params, (method, path)
        for status, response in operation["responses"].items():
            assert "X-Request-ID" in response["headers"], (method, path, status)


def test_internal_pii_model_is_not_published() -> None:
    text = OPENAPI_PATH.read_text(encoding="utf-8")

    assert "TmsShipment" not in text
    assert "x-pii" not in text
    assert "documento_identidad" not in text
    assert "firma_digital_url" not in text


def test_protected_operations_declare_the_admin_token() -> None:
    by_id = {op["operationId"]: op for _, _, op in operations(build_openapi())}

    for operation_id in ("reindexRag", "getHandoff", "listHandoffs", "updateHandoffStatus"):
        assert by_id[operation_id]["security"] == [{"AdminToken": []}]


def test_bulk_shipment_operations_declare_the_client_api_key() -> None:
    by_id = {op["operationId"]: op for _, _, op in operations(build_openapi())}

    for operation_id in ("createShipmentBatch", "getShipmentBatch", "listShipmentBatchItems"):
        assert by_id[operation_id]["security"] == [{"ApiKey": []}]


def test_env_example_lists_exactly_the_canonical_names_without_secrets() -> None:
    entries = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )

    assert set(entries) == {var.value for var in EnvVar}
    for var in SECRET_ENV_VARS:
        assert entries[var] == "" or entries[var].startswith("<"), var


def _discriminated_blocks(document: dict[str, Any]) -> dict[str, Any]:
    blocks: dict[str, Any] = document["components"]["schemas"]["ConversationReply"]["properties"][
        "blocks"
    ]["items"]
    return blocks


def test_reply_blocks_are_a_discriminated_union() -> None:
    blocks = _discriminated_blocks(build_openapi())

    assert blocks["discriminator"]["propertyName"] == "type"
    assert set(blocks["discriminator"]["mapping"]) == {
        "TEXT",
        "QUICK_REPLIES",
        "TRACKING_CARD",
        "QUOTE_SUMMARY",
        "CITATIONS",
        "REQUEST_ATTACHMENTS",
        "PQRS_TICKET",
        "HANDOFF_NOTICE",
    }


def test_response_discriminators_are_required_for_typed_clients() -> None:
    schemas = build_openapi()["components"]["schemas"]

    for name in ("TextBlock", "TrackingCardBlock", "HandoffNoticeBlock", "WsHandoffStatusEvent"):
        assert "type" in schemas[name]["required"], name


def test_yaml_parses_back_to_the_built_document() -> None:
    assert yaml.safe_load(render_openapi_yaml()) == build_openapi()
