"""Builds ``contracts/openapi.yaml`` from the contract models.

FastAPI derives paths and schemas; this module then makes the document state
what the services really do:

* every error response uses ``ErrorEnvelope`` and lists its ``x-error-codes``
  (FastAPI's own 422 body is replaced, since the API never returns it);
* every operation documents ``X-Request-ID`` on the request and the response;
* internal contracts between services are published as ``x-internal`` schemas;
* WebSocket events are published under ``x-websockets``;
* golden examples are embedded.
"""

from collections import defaultdict
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from app.contracts.api_spec import build_contract_app
from app.contracts.common import (
    ERROR_HTTP_STATUS,
    REQUEST_ID_HEADER,
    REQUEST_ID_PATTERN,
    ErrorCode,
    ErrorEnvelope,
)
from app.contracts.conversation import ConversationState
from app.contracts.escalation import EscalationInput
from app.contracts.examples import ERROR_EXAMPLES, EXAMPLES
from app.contracts.inbound import InboundMessage
from app.contracts.ports import AuditEvent
from app.contracts.rag import ChunkMetadata, RagAnswer
from app.contracts.ws import WS_CHAT_PATH, WsHandoffStatusEvent, WsMessageEvent

REF = "#/components/schemas/{model}"
GENERATED_HEADER = (
    "# GENERADO desde backend/app/contracts — no editar a mano.\n"
    "# Regenerar: cd backend && uv run python -m app.contracts\n"
)

type JsonSchemaMode = Literal["validation", "serialization"]

# Contracts between services that no HTTP route exposes. Published so the YAML
# is the single reference; TmsShipment is deliberately absent (it carries PII).
INTERNAL_MODELS: tuple[tuple[type[BaseModel], JsonSchemaMode], ...] = (
    (InboundMessage, "validation"),
    (ConversationState, "serialization"),
    (EscalationInput, "validation"),
    (RagAnswer, "serialization"),
    (ChunkMetadata, "serialization"),
    (WsMessageEvent, "serialization"),
    (WsHandoffStatusEvent, "serialization"),
    (AuditEvent, "serialization"),
)


def _ref(name: str) -> dict[str, str]:
    return {"$ref": REF.format(model=name)}


def _add_models(
    schemas: dict[str, Any],
    models: tuple[tuple[type[BaseModel], JsonSchemaMode], ...],
    *,
    internal: bool,
) -> None:
    _, top = models_json_schema(list(models), ref_template=REF)
    for name, schema in top.get("$defs", {}).items():
        existing = schemas.get(name)
        if existing is None:
            schemas[name] = schema
        elif existing != schema:
            raise RuntimeError(f"schema '{name}' differs between routes and contract models")
    if internal:
        for model, _mode in models:
            schemas[model.__name__]["x-internal"] = True


def _error_example(code: ErrorCode) -> Any:
    return ERROR_EXAMPLES[code].load()


def _document_errors(operation: dict[str, Any]) -> None:
    codes = [ErrorCode(code) for code in operation.pop("x-error-codes", [])]
    responses: dict[str, Any] = operation["responses"]
    if "422" in responses:  # FastAPI adds it whenever there is something to validate
        codes.insert(0, ErrorCode.VALIDACION)
    # 1.6.0 (CR-008): every body is size-limited; the batch route answers its own 413.
    if "requestBody" in operation and ErrorCode.LOTE_DEMASIADO_GRANDE not in codes:
        codes.append(ErrorCode.CUERPO_DEMASIADO_GRANDE)
    codes.append(ErrorCode.ERROR_INTERNO)

    by_status: dict[str, list[ErrorCode]] = defaultdict(list)
    for code in codes:
        by_status[str(ERROR_HTTP_STATUS[code])].append(code)

    for status, group in by_status.items():
        responses[status] = {
            "description": "Códigos: " + ", ".join(group),
            "x-error-codes": [code.value for code in group],
            "content": {
                "application/json": {
                    "schema": _ref(ErrorEnvelope.__name__),
                    "examples": {code.value: {"value": _error_example(code)} for code in group},
                }
            },
        }
    operation["responses"] = dict(sorted(responses.items()))


def _add_request_id(operation: dict[str, Any]) -> None:
    operation.setdefault("parameters", []).insert(0, {"$ref": "#/components/parameters/XRequestId"})
    for response in operation["responses"].values():
        response.setdefault("headers", {})[REQUEST_ID_HEADER] = {
            "$ref": "#/components/headers/XRequestId"
        }


def _operations(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        operation["operationId"]: operation
        for path_item in schema["paths"].values()
        for operation in path_item.values()
    }


def _attach_examples(schema: dict[str, Any]) -> None:
    operations = _operations(schema)
    for example in EXAMPLES:
        if example.error_code:
            continue  # error examples are attached per code by _document_errors
        for use in example.uses:
            operation = operations[use.operation_id]
            if use.target == "request":
                media = operation["requestBody"]["content"]
            else:
                media = operation["responses"][str(use.status)]["content"]
            content = next(iter(media.values()))  # application/json, or text/plain
            summary = f"{use.scenario} · {example.summary}" if use.scenario else example.summary
            content.setdefault("examples", {})[example.name] = {
                "summary": summary,
                "value": example.load(),
            }


def _websockets() -> dict[str, Any]:
    events = [WsMessageEvent.__name__, WsHandoffStatusEvent.__name__]
    return {
        WS_CHAT_PATH: {
            "summary": "Eventos de la sesión de chat (servidor → cliente)",
            "x-priority": "P1",
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "events": _ref("WsEvent"),
            "eventTypes": events,
        }
    }


def build_openapi() -> dict[str, Any]:
    app = build_contract_app()
    schema = app.openapi()
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    _add_models(schemas, ((ErrorEnvelope, "serialization"),), internal=False)
    _add_models(schemas, INTERNAL_MODELS, internal=True)
    schemas["WsEvent"] = {
        "oneOf": [_ref(WsMessageEvent.__name__), _ref(WsHandoffStatusEvent.__name__)],
        "discriminator": {
            "propertyName": "type",
            "mapping": {
                "message": REF.format(model=WsMessageEvent.__name__),
                "handoff_status": REF.format(model=WsHandoffStatusEvent.__name__),
            },
        },
    }
    components["parameters"] = {
        "XRequestId": {
            "name": REQUEST_ID_HEADER,
            "in": "header",
            "required": False,
            "description": "Identificador de correlación; se genera si falta.",
            "schema": {"type": "string", "pattern": REQUEST_ID_PATTERN},
        }
    }
    components["headers"] = {
        "XRequestId": {
            "description": "El mismo X-Request-ID de la petición, o uno generado.",
            "schema": {"type": "string", "pattern": REQUEST_ID_PATTERN},
        }
    }

    for operation in _operations(schema).values():
        _document_errors(operation)
        _add_request_id(operation)
    _attach_examples(schema)
    schema["x-websockets"] = _websockets()
    return schema


def render_openapi_yaml() -> str:
    body = yaml.safe_dump(build_openapi(), sort_keys=False, allow_unicode=True, width=100)
    return GENERATED_HEADER + body
