"""HTTP plumbing: request id, the single error envelope, admin token, rate limit."""

import hmac
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeout
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


class BodyTooLarge(StarletteHTTPException):
    """Raised while the body streams in; FastAPI re-raises HTTP errors from body parsing."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(status_code=413)
        self.max_bytes = max_bytes


class BodySizeLimitMiddleware:
    """413 ``CUERPO_DEMASIADO_GRANDE`` before the app buffers an oversized body (CR-008).

    A declared ``Content-Length`` is refused up front; a chunked body is counted while
    it streams. ``limits`` maps a path prefix to its own limit, and ``None`` leaves the
    route to enforce one (the batch endpoint answers ``LOTE_DEMASIADO_GRANDE``).
    """

    def __init__(self, app: ASGIApp, *, default: int, limits: Mapping[str, int | None]) -> None:
        self.app = app
        self._default = default
        self._limits = sorted(limits.items(), key=lambda item: -len(item[0]))

    def _limit_for(self, path: str) -> int | None:
        for prefix, limit in self._limits:
            if path.startswith(prefix):
                return limit
        return self._default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._limit_for(scope.get("path", "")) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length", b"").decode("latin-1")
        if declared.isdigit() and int(declared) > limit:
            request_id = str((scope.get("state") or {}).get("request_id") or uuid.uuid4())
            error = ContractError(ErrorCode.CUERPO_DEMASIADO_GRANDE, details={"max_bytes": limit})
            response = JSONResponse(
                status_code=error.http_status,
                content=error.to_envelope(request_id).model_dump(mode="json"),
                headers={"Connection": "close"},
            )
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        await self.app(scope, counted, send)


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
    if isinstance(exc, BodyTooLarge):
        details = {"max_bytes": exc.max_bytes}
        return error_response(
            request, ContractError(ErrorCode.CUERPO_DEMASIADO_GRANDE, details=details)
        )
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


async def _pool_exhausted(request: Request, exc: Exception) -> JSONResponse:
    """2.0.0 (CR-010, CAP-01): the DB pool did not free a connection in time. Saturation is
    temporary: 503 with Retry-After, not an unexpected 500."""
    logger.warning("db pool exhausted", extra={"request_id": request_id_of(request)})
    error = ContractError(ErrorCode.SERVICIO_SATURADO, details={"reintentar_en_segundos": 5})
    response = error_response(request, error)
    response.headers["Retry-After"] = "5"
    return response


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(SqlAlchemyTimeout, _pool_exhausted)
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
