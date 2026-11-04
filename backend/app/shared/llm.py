"""Ollama adapters for ``LlmPort`` and ``EmbeddingPort`` (local AI, swappable by port).

Every failure (connection, timeout, HTTP error, malformed body) becomes
``ContractError(LLM_NO_DISPONIBLE)`` so callers degrade instead of crashing.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import LlmCompletion, LlmMessage
from app.shared import metrics

logger = logging.getLogger("app.llm")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


CONNECT_RETRY_DELAY_SECONDS = 0.25


class _OllamaClient:
    """2.0.0 (LLM-01): exactly one retry, only when the connection could not be opened
    (``ConnectError``: Ollama restarting, socket refused). Never on a timeout or an HTTP error:
    that request may still be running in Ollama, and repeating it would double the load. The
    caller already holds its admission slot, so the retry adds no concurrent request."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport
        )

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        operation = path.rsplit("/", 1)[-1]  # chat | embed
        for attempt in (1, 2):
            try:
                with metrics.timer("OllamaLatency", operation=operation):
                    response = await self._client.post(path, json=body)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
            except httpx.ConnectError as error:
                if attempt == 1:
                    metrics.emit("OllamaRetries", 1, operation=operation)
                    await asyncio.sleep(CONNECT_RETRY_DELAY_SECONDS)
                    continue
                metrics.emit("InfrastructureErrors", 1, component="ollama", operation=operation)
                logger.warning("ollama %s failed after retry: %s", path, type(error).__name__)
                raise ContractError(ErrorCode.LLM_NO_DISPONIBLE) from error
            except (httpx.HTTPError, ValueError) as error:
                metrics.emit("InfrastructureErrors", 1, component="ollama", operation=operation)
                logger.warning("ollama %s failed: %s", path, type(error).__name__)
                raise ContractError(ErrorCode.LLM_NO_DISPONIBLE) from error
            return data
        raise AssertionError("unreachable")  # pragma: no cover

    async def ping(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def aclose(self) -> None:
        await self._client.aclose()


class Admission:
    """At most ``max_inflight`` calls to the model and ``max_queue`` waiting for one.

    A chat turn keeps its database connection while it waits for the model, so an
    unbounded queue exhausts the pool and the API answers 500 (audit 2026-11-04,
    REL-01). A call beyond the queue fails at once as ``LLM_NO_DISPONIBLE``, the same
    path as an unreachable Ollama: the turn degrades and offers a human.
    """

    def __init__(self, max_inflight: int, max_queue: int) -> None:
        self._slots = asyncio.Semaphore(max_inflight)
        self._max_queue = max_queue
        self._waiting = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self._slots.locked() and self._waiting >= self._max_queue:
            logger.warning("llm saturated: %d waiting", self._waiting)
            raise ContractError(ErrorCode.LLM_NO_DISPONIBLE, details={"motivo": "saturado"})
        self._waiting += 1
        try:
            await self._slots.acquire()
        finally:
            self._waiting -= 1
        try:
            yield
        finally:
            self._slots.release()


class OllamaChat:
    """``LlmPort``. Deterministic settings (temperature 0); thinking disabled."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        *,
        max_inflight: int = 4,
        max_queue: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = _OllamaClient(base_url, timeout_seconds, transport=transport)
        self._admission = Admission(max_inflight, max_queue)
        self.model = model

    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LlmCompletion:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = max_tokens
        body = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            "think": False,
            "options": options,
        }
        async with self._admission.slot():
            data = await self._http.post("/api/chat", body)
        content = data.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ContractError(
                ErrorCode.LLM_NO_DISPONIBLE, details={"motivo": "respuesta sin contenido"}
            )
        return LlmCompletion(text=_THINK.sub("", content).strip(), model=self.model)

    async def ping(self) -> bool:
        return await self._http.ping()

    async def aclose(self) -> None:
        await self._http.aclose()


class OllamaEmbedding:
    """``EmbeddingPort``. Refuses vectors whose size differs from ``EMBED_DIM``."""

    def __init__(
        self,
        base_url: str,
        model: str,
        dimension: int,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = _OllamaClient(base_url, timeout_seconds, transport=transport)
        self.model = model
        self.dimension = dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        data = await self._http.post("/api/embed", {"model": self.model, "input": list(texts)})
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ContractError(
                ErrorCode.LLM_NO_DISPONIBLE, details={"motivo": "embeddings inválidos"}
            )
        if any(len(v) != self.dimension for v in vectors):
            raise ContractError(
                ErrorCode.NO_CONFIGURADO,
                "La dimensión de los embeddings no coincide con EMBED_DIM.",
                details={"esperado": self.dimension, "recibido": len(vectors[0])},
            )
        return [[float(x) for x in v] for v in vectors]

    async def aclose(self) -> None:
        await self._http.aclose()
