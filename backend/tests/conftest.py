"""PostgreSQL fixtures shared by the integration tests (bulk shipments, staff identities).

They run against ``TEST_DATABASE_URL`` (default: the local ``translogix_test``), migrate
it to head once per session and empty the B2B and staff tables before each test. The
database name must end in ``_test``: these fixtures truncate tables. Contract tests do
not request them and never touch a database.
"""

import base64
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.main import create_app
from app.shared.config import Settings
from tests.db import AppFactory

BACKEND = Path(__file__).resolve().parents[1]
# Fernet key for the test database only: a fixed, public test value (like the test admin
# token), so rows encrypted by one run stay readable in the next. Never a deployment key.
TEST_PII_KEY = base64.urlsafe_b64encode(b"translogix-test-key-not-a-secret").decode()
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
    "staff_tokens",
    "staff_users",
    "tariff_quotations",  # profiles and rules keep the migration seed (see tests/tariffs)
    "shipment_reviews",  # 1.7.0 (CR-009)
    "rate_limit_counters",  # 2.0.0 (CR-010)
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
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PII_ENCRYPTION_KEYS": TEST_PII_KEY},
        check=True,
        capture_output=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture
def make_app(database_url: str) -> AppFactory:
    @asynccontextmanager
    async def factory(**overrides: Any) -> AsyncIterator[FastAPI]:
        defaults: dict[str, Any] = {
            "database_url": database_url,
            "admin_token": "test-admin-token-0000",  # test-only value
            "pii_hash_salt": "test-pii-salt-000000",
            "pii_encryption_keys": TEST_PII_KEY,
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
