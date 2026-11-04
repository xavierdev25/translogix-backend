"""CORS for browser fronts: Bearer credentials must pass the preflight (no database needed)."""

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.shared.config import Settings

FRONT = "http://localhost:5173"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        admin_token=SecretStr("test-admin-token-0000"),
        pii_hash_salt=SecretStr("test-pii-salt-000000"),
        ollama_base_url=None,
        cors_allow_origins=[FRONT],
    )


async def preflight(settings: Settings, origin: str, headers: str) -> httpx.Response:
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.options(
            "/api/v1/shipment-batches",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": headers,
            },
        )


async def test_allowed_front_may_send_bearer_credentials(settings: Settings) -> None:
    response = await preflight(settings, FRONT, "authorization,content-type,idempotency-key")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONT
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


async def test_unknown_origins_are_still_refused(settings: Settings) -> None:
    response = await preflight(settings, "https://evil.example", "authorization")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_browsers_can_read_the_request_id(settings: Settings) -> None:
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        response = await c.get("/health", headers={"Origin": FRONT})

    assert response.headers["access-control-expose-headers"] == "X-Request-ID"
