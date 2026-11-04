"""Helpers for tests that run the real app against the test database (see ``conftest.py``)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import text

AppFactory = Callable[..., AbstractAsyncContextManager[FastAPI]]


def http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def scalar(app: FastAPI, sql: str, **params: Any) -> Any:
    async with app.state.container.sessions() as session:
        return await session.scalar(text(sql), params)
