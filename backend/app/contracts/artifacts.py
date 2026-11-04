"""Every generated file under ``contracts/``, rendered from the models.

* ``openapi.yaml`` — the public HTTP contract;
* ``schemas/*.schema.json`` — standalone JSON Schemas (2020-12) for the
  contracts exchanged between services and for WebSocket events;
* ``mocks/manifest.json`` — operation → example map with match hints, the input
  of the frontend MSW handlers and of the E2E tests.

``types/api.d.ts`` is generated from the YAML by ``openapi-typescript`` (Node);
``types/openapi.sha256`` records which YAML it was generated from.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from app.contracts.channels_http import WhatsAppWebhookPayload
from app.contracts.common import CONTRACT_VERSION, ErrorCode, ErrorEnvelope
from app.contracts.conversation import ConversationState
from app.contracts.escalation import EscalationDecision, EscalationInput, HandoffPayload
from app.contracts.examples import ERROR_EXAMPLES, EXAMPLES
from app.contracts.inbound import InboundMessage
from app.contracts.openapi import build_openapi, render_openapi_yaml
from app.contracts.ports import AuditEvent, ChannelOutbound, DeliveryResult
from app.contracts.rag import ChunkMetadata, RagAnswer
from app.contracts.reply import ConversationReply
from app.contracts.ws import WsEvent

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
OPENAPI_PATH = CONTRACTS_DIR / "openapi.yaml"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"
MANIFEST_PATH = CONTRACTS_DIR / "mocks" / "manifest.json"
TS_TYPES_PATH = CONTRACTS_DIR / "types" / "api.d.ts"
TS_SOURCE_HASH_PATH = CONTRACTS_DIR / "types" / "openapi.sha256"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

type Mode = Literal["validation", "serialization"]

# Inputs use the validation schema (what a producer may send); outputs the
# serialisation schema (what a consumer always receives).
STANDALONE_SCHEMAS: tuple[tuple[str, Any, Mode], ...] = (
    ("InboundMessage", InboundMessage, "validation"),
    ("ConversationReply", ConversationReply, "serialization"),
    ("ConversationState", ConversationState, "serialization"),
    ("EscalationInput", EscalationInput, "validation"),
    ("EscalationDecision", EscalationDecision, "serialization"),
    ("HandoffPayload", HandoffPayload, "serialization"),
    ("RagAnswer", RagAnswer, "serialization"),
    ("ChunkMetadata", ChunkMetadata, "serialization"),
    ("ChannelOutbound", ChannelOutbound, "serialization"),
    ("DeliveryResult", DeliveryResult, "serialization"),
    ("ErrorEnvelope", ErrorEnvelope, "serialization"),
    ("WsEvent", WsEvent, "serialization"),
    ("AuditEvent", AuditEvent, "serialization"),
    ("WhatsAppWebhookPayload", WhatsAppWebhookPayload, "validation"),
)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def standalone_schema(name: str, target: Any, mode: Mode) -> dict[str, Any]:
    schema = TypeAdapter(target).json_schema(mode=mode)
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"{name}.schema.json",
        "title": schema.pop("title", name),
        "x-contract-version": CONTRACT_VERSION,
        **schema,
    }


def build_mock_manifest(openapi: dict[str, Any]) -> dict[str, Any]:
    operations = []
    for path, item in openapi["paths"].items():
        for method, operation in item.items():
            operation_id = operation["operationId"]
            entries: dict[str, list[dict[str, Any]]] = {"requests": [], "responses": []}
            for example in EXAMPLES:
                for use in example.uses:
                    if use.operation_id != operation_id:
                        continue
                    entry: dict[str, Any] = {"example": f"examples/{example.name}.json"}
                    if use.target == "response":
                        entry = {"status": use.status, **entry}
                    entry["scenario"] = use.scenario
                    if use.target == "response":
                        entry["match"] = dict(use.match) if use.match else None
                    entry["pendiente_de_servicio_real"] = example.pending
                    entries[f"{use.target}s"].append(entry)
            errors = [
                {
                    "status": int(status),
                    "code": code,
                    "example": f"examples/{ERROR_EXAMPLES[ErrorCode(code)].name}.json",
                }
                for status, response in operation["responses"].items()
                for code in response.get("x-error-codes", [])
            ]
            operations.append(
                {
                    "operationId": operation_id,
                    "method": method.upper(),
                    "path": path,
                    **entries,
                    "errors": errors,
                }
            )
    return {
        "contract_version": CONTRACT_VERSION,
        "match_semantics": {
            "path": "valores exactos de parámetros de ruta",
            "body": "subconjunto exacto del cuerpo JSON de la petición",
            "text_contains": (
                "el texto del mensaje (`text`; `transcript` en PBX; `messages[].text.body` en "
                "WhatsApp) contiene la cadena, sin distinguir mayúsculas"
            ),
            "null": "respuesta por defecto de la operación",
        },
        "pendiente_de_servicio_real": (
            "si no es null, la forma es contractual pero ese valor lo produce el servicio real"
        ),
        "operations": operations,
    }


def render_artifacts() -> dict[Path, str]:
    files = {OPENAPI_PATH: render_openapi_yaml()}
    for name, target, mode in STANDALONE_SCHEMAS:
        files[SCHEMAS_DIR / f"{name}.schema.json"] = _json(standalone_schema(name, target, mode))
    files[MANIFEST_PATH] = _json(build_mock_manifest(build_openapi()))
    return files


def openapi_sha256() -> str:
    return hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest()


def stale_artifacts() -> list[str]:
    """Human-readable reasons why the committed artifacts differ from the models."""
    problems = []
    rendered = render_artifacts()
    for path, content in rendered.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            problems.append(f"{path.relative_to(CONTRACTS_DIR)} is out of date")
    problems.extend(
        f"{path.relative_to(CONTRACTS_DIR)} no longer corresponds to a model"
        for path in SCHEMAS_DIR.glob("*.json")
        if path not in rendered
    )
    recorded = TS_SOURCE_HASH_PATH.read_text().strip() if TS_SOURCE_HASH_PATH.exists() else None
    if not TS_TYPES_PATH.exists() or recorded != openapi_sha256():
        problems.append("types/api.d.ts was not generated from the current openapi.yaml")
    return problems
