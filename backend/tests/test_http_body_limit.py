"""Request body size limit (CR-008, audit 2026-11-04 SEC-04/05). No database needed."""

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import FastAPI, File, Request, UploadFile
from pydantic import SecretStr

from app.main import create_app
from app.shared.config import Settings
from app.shared.http import BodySizeLimitMiddleware, RequestIdMiddleware, register_error_handlers

FRONT = "http://localhost:5173"


def tiny_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, default=1000, limits={"/big": 5000, "/own": None})
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    async def echo(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    for path in ("/echo", "/big", "/own"):
        app.add_api_route(path, echo, methods=["POST"])

    @app.post("/form")
    async def form(file: Annotated[UploadFile, File()]) -> dict[str, int]:
        return {"bytes": len(await file.read())}

    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def chunks(total: int, size: int = 400) -> AsyncIterator[bytes]:
    for start in range(0, total, size):
        yield b"x" * min(size, total - start)


def assert_too_large(response: httpx.Response, limit: int) -> None:
    assert response.status_code == 413
    error = response.json()["error"]
    assert (error["code"], error["details"]) == ("CUERPO_DEMASIADO_GRANDE", {"max_bytes": limit})
    assert error["request_id"] == response.headers["x-request-id"]


async def test_a_body_within_the_limit_reaches_the_route() -> None:
    async with client(tiny_app()) as c:
        response = await c.post("/echo", content=b"x" * 1000)

    assert response.json() == {"bytes": 1000}


async def test_a_declared_length_over_the_limit_is_refused_before_reading() -> None:
    async with client(tiny_app()) as c:
        response = await c.post("/echo", content=b"x" * 1001)

    assert_too_large(response, 1000)


async def test_a_chunked_body_is_cut_while_it_streams() -> None:
    async with client(tiny_app()) as c:
        response = await c.post("/echo", content=chunks(5000))

    assert "content-length" not in response.request.headers
    assert_too_large(response, 1000)


async def test_a_multipart_upload_is_cut_while_it_streams() -> None:
    async with client(tiny_app()) as c:
        response = await c.post("/form", files={"file": ("a.pdf", b"x" * 3000, "application/pdf")})

    assert_too_large(response, 1000)


@pytest.mark.parametrize(("path", "size", "status"), [("/big", 4000, 200), ("/big", 6000, 413)])
async def test_a_prefix_may_have_its_own_limit(path: str, size: int, status: int) -> None:
    async with client(tiny_app()) as c:
        response = await c.post(path, content=b"x" * size)

    assert response.status_code == status


async def test_a_route_that_enforces_its_own_limit_is_left_alone() -> None:
    async with client(tiny_app()) as c:
        response = await c.post("/own", content=b"x" * 20_000)

    assert response.json() == {"bytes": 20_000}


async def test_the_real_app_cuts_an_oversized_chat_message_with_cors_headers() -> None:
    settings = Settings(
        admin_token=SecretStr("test-admin-token-0000"),
        pii_hash_salt=SecretStr("test-pii-salt-000000"),
        ollama_base_url=None,
        cors_allow_origins=[FRONT],
        max_request_body_bytes=64 * 1024,
    )
    body = b'{"text": "' + b"x" * (65 * 1024) + b'"}'
    async with client(create_app(settings)) as c:
        response = await c.post(
            "/api/v1/chat/messages",
            content=body,
            headers={"Content-Type": "application/json", "Origin": FRONT},
        )

    assert_too_large(response, 64 * 1024)
    assert response.headers["access-control-allow-origin"] == FRONT
