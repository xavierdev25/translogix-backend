"""``/api/v1/shipment-batches``: same paths, operation ids and models as the contract.

The create route reads the raw body itself: it enforces the size limit before parsing
and validates each item separately (partial processing). Its OpenAPI request body is
declared with ``openapi_extra`` so the published schema stays the contract one.
"""

import json
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.audit.domain.models import ApiAuditEvent, AuditAction
from app.clients.api.dependencies import authenticated
from app.clients.domain.models import ClientContext
from app.contracts.api_spec import API_PREFIX
from app.contracts.common import ContractError, ErrorCode
from app.contracts.shipment_batches import (
    IDEMPOTENCY_KEY_HEADER,
    ITEMS_PAGE_MAX,
    REFERENCE_PATTERN,
    ApiScope,
    BatchItemStatus,
    ShipmentBatchItemsPage,
    ShipmentBatchResponse,
    ShipmentBatchSummary,
)
from app.shared.http import request_id_of

router = APIRouter(prefix=f"{API_PREFIX}/shipment-batches", tags=["envíos masivos"])

CanCreate = Annotated[ClientContext, Depends(authenticated(ApiScope.BATCHES_CREATE))]
CanRead = Annotated[ClientContext, Depends(authenticated(ApiScope.BATCHES_READ))]


class _Envelope(BaseModel):
    """Batch-level shape only; items are validated one by one by the service."""

    model_config = ConfigDict(extra="forbid")

    referencia_lote: str = Field(pattern=REFERENCE_PATTERN)
    envios: list[Any] = Field(min_length=1)


def _invalid(errors: list[Any]) -> RequestValidationError:
    return RequestValidationError(errors)


async def _read_body(request: Request, max_bytes: int) -> bytes:
    too_big = ContractError(ErrorCode.LOTE_DEMASIADO_GRANDE, details={"max_bytes": max_bytes})
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise too_big
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > max_bytes:
            raise too_big
    return bytes(body)


async def _audited(
    request: Request,
    context: ClientContext,
    action: AuditAction,
    resource_id: UUID | None,
    status: int,
) -> None:
    box = request.app.state.container
    await box.audit.record(
        ApiAuditEvent(
            action=action,
            http_status=status,
            request_id=request_id_of(request),
            created_at=box.clock.now(),
            client_id=context.client_id,
            api_key_id=context.api_key_id,
            resource_type="shipment_batch",
            resource_id=str(resource_id) if resource_id else None,
        )
    )


@router.post(
    "",
    status_code=201,
    operation_id="createShipmentBatch",
    responses={
        200: {"model": ShipmentBatchResponse, "description": "Repetición idempotente"},
        202: {"model": ShipmentBatchResponse, "description": "El lote sigue en proceso"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ShipmentBatchRequest"}
                }
            },
        }
    },
)
async def create_shipment_batch(
    request: Request,
    response: Response,
    context: CanCreate,
    idempotency_key: Annotated[
        str | None, Header(alias=IDEMPOTENCY_KEY_HEADER, max_length=64)
    ] = None,
) -> ShipmentBatchResponse:
    box = request.app.state.container
    status, batch_id = 500, None
    try:
        box.batch_limiter.check(f"batches:{context.client_id}")
        raw = await _read_body(request, box.settings.batch_max_body_bytes)
        try:
            envelope = _Envelope.model_validate(json.loads(raw))
        except json.JSONDecodeError:
            raise _invalid(
                [{"loc": ("body",), "msg": "JSON decode error", "type": "json_invalid"}]
            ) from None
        except ValidationError as error:
            raise _invalid(
                [
                    {**e, "loc": ("body", *e["loc"])}
                    for e in error.errors(include_input=False, include_url=False)
                ]
            ) from None
        if idempotency_key is not None and idempotency_key != envelope.referencia_lote:
            raise _invalid(
                [
                    {
                        "loc": ("header", IDEMPOTENCY_KEY_HEADER),
                        "msg": "debe ser igual a referencia_lote",
                        "type": "value_error",
                    }
                ]
            )
        if len(envelope.envios) > box.settings.batch_max_items:
            raise ContractError(
                ErrorCode.LOTE_DEMASIADO_GRANDE, details={"max_items": box.settings.batch_max_items}
            )
        result, status = await box.batches.submit(
            context, envelope.referencia_lote, envelope.envios
        )
        batch_id = result.lote_id
        response.status_code = status
        return cast(ShipmentBatchResponse, result)
    except ContractError as error:
        status = error.http_status
        raise
    except RequestValidationError:
        status = 422
        raise
    finally:
        await _audited(request, context, AuditAction.BATCH_CREATE, batch_id, status)


@router.get("/{batch_id}", operation_id="getShipmentBatch")
async def get_shipment_batch(
    batch_id: UUID, request: Request, context: CanRead
) -> ShipmentBatchSummary:
    status = 500
    try:
        summary = await request.app.state.container.batches.summary(context, batch_id)
        status = 200
        return cast(ShipmentBatchSummary, summary)
    except ContractError as error:
        status = error.http_status
        raise
    finally:
        await _audited(request, context, AuditAction.BATCH_READ, batch_id, status)


@router.get("/{batch_id}/items", operation_id="listShipmentBatchItems")
async def list_shipment_batch_items(
    batch_id: UUID,
    request: Request,
    context: CanRead,
    status: Annotated[BatchItemStatus | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=ITEMS_PAGE_MAX)] = 50,
) -> ShipmentBatchItemsPage:
    http_status = 500
    try:
        page = await request.app.state.container.batches.items(
            context, batch_id, status=status, cursor=cursor, limit=limit
        )
        http_status = 200
        return cast(ShipmentBatchItemsPage, page)
    except ContractError as error:
        http_status = error.http_status
        raise
    finally:
        await _audited(request, context, AuditAction.BATCH_ITEMS_READ, batch_id, http_status)
