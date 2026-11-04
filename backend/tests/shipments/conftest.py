"""PostgreSQL fixtures for the bulk-shipment tests.

They run against ``TEST_DATABASE_URL`` (default: the local ``translogix_test``), migrate
it to head once per session and empty the new tables before each test. The database
name must end in ``_test``: these fixtures truncate tables.
"""

import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.main import create_app
from app.shared.config import Settings

BACKEND = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:54329/translogix_test"
)
NEW_TABLES = (
    "api_audit_events",
    "shipment_recipients",
    "shipment_batch_items",
    "shipments",
    "shipment_batches",
    "client_api_keys",
    "enterprise_clients",
    "quotes",
)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_url(TEST_DATABASE_URL)
    if not (url.database or "").endswith("_test"):
        pytest.fail(f"TEST_DATABASE_URL must point to a *_test database, got {url.database!r}")
    try:
        socket.create_connection((url.host or "localhost", url.port or 5432), timeout=2).close()
    except OSError:
        pytest.skip(f"PostgreSQL de pruebas no disponible en {url.host}:{url.port}")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        check=True,
        capture_output=True,
    )
    return TEST_DATABASE_URL


AppFactory = Callable[..., AbstractAsyncContextManager[FastAPI]]


@pytest.fixture
def make_app(database_url: str) -> AppFactory:
    @asynccontextmanager
    async def factory(**overrides: Any) -> AsyncIterator[FastAPI]:
        defaults: dict[str, Any] = {
            "database_url": database_url,
            "admin_token": "test-admin-token-0000",  # test-only value
            "pii_hash_salt": "test-pii-salt-000000",
            "ollama_base_url": None,
            "batch_rate_limit_per_minute": 0,
        }
        settings = Settings(**(defaults | overrides))
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with app.state.container.sessions() as session:
                await session.execute(text(f"TRUNCATE {', '.join(NEW_TABLES)} CASCADE"))
                await session.commit()
            yield app

    return factory


@pytest.fixture
async def app(make_app: AppFactory) -> AsyncIterator[FastAPI]:
    async with make_app() as instance:
        yield instance
