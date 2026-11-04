"""Ollama adapters for ``LlmPort`` and ``EmbeddingPort`` (local AI, swappable by port).

Every failure (connection, timeout, HTTP error, malformed body) becomes
``ContractError(LLM_NO_DISPONIBLE)`` so callers degrade instead of crashing.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any

import httpx

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import LlmCompletion, LlmMessage

logger = logging.getLogger("app.llm")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class _OllamaClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("ollama %s failed: %s", path, type(error).__name__)
            raise ContractError(ErrorCode.LLM_NO_DISPONIBLE) from error
        return data

    async def ping(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def aclose(self) -> None:
        await self._client.aclose()


class OllamaChat:
    """``LlmPort``. Deterministic settings (temperature 0); thinking disabled."""

    def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
        self._http = _OllamaClient(base_url, timeout_seconds)
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
        data = await self._http.post(
            "/api/chat",
            {
                "model": self.model,
                "messages": [m.model_dump() for m in messages],
                "stream": False,
                "think": False,
                "options": options,
            },
        )
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

    def __init__(self, base_url: str, model: str, dimension: int, timeout_seconds: float) -> None:
        self._http = _OllamaClient(base_url, timeout_seconds)
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
