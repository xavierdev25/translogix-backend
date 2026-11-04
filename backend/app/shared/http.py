"""HTTP plumbing: request id, the single error envelope, admin token, rate limit."""

import hmac
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.contracts.common import (
    REQUEST_ID_HEADER,
    REQUEST_ID_PATTERN,
    ContractError,
    ErrorCode,
)

logger = logging.getLogger("app.http")
_REQUEST_ID = re.compile(REQUEST_ID_PATTERN)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestIdMiddleware:
    """Accepts a well-formed ``X-Request-ID`` or generates one; always echoes it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                raw.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, send_with_id)


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or uuid.uuid4())


def error_response(request: Request, error: ContractError) -> JSONResponse:
    request_id = request_id_of(request)
    return JSONResponse(
        status_code=error.http_status,
        content=error.to_envelope(request_id).model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id},
    )


async def _contract_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(request, cast(ContractError, exc))


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    errors = cast(RequestValidationError, exc).errors()
    fields = [
        {
            "loc": [str(p) for p in e.get("loc", ())],
            "msg": str(e.get("msg")),
            "type": str(e.get("type")),
        }
        for e in errors
    ]
    return error_response(request, ContractError(ErrorCode.VALIDACION, details={"campos": fields}))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    status = cast(StarletteHTTPException, exc).status_code
    code = ErrorCode.NO_ENCONTRADO if status == 404 else ErrorCode.VALIDACION
    response = error_response(request, ContractError(code))
    response.status_code = status
    return response


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Type only: the message may carry user data or internals.
    logger.error(
        "unhandled error",
        extra={"request_id": request_id_of(request)},
        exc_info=(type(exc), exc, None),
    )
    return error_response(request, ContractError(ErrorCode.ERROR_INTERNO))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ContractError, _contract_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)


def require_admin(expected: str, provided: str | None) -> None:
    """Constant-time comparison; a missing or wrong token is NO_AUTORIZADO."""
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise ContractError(ErrorCode.NO_AUTORIZADO)


class SlidingWindowRateLimiter:
    """Per-key limit over a 60 s window. In-process only: one API instance (MVP)."""

    def __init__(self, per_minute: int, *, now: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        if self._limit <= 0:
            return
        now = self._now()
        hits = self._hits[key]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self._limit:
            raise ContractError(ErrorCode.LIMITE_DE_TASA, details={"reintentar_en_segundos": 60})
        hits.append(now)
