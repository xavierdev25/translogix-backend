"""LLM admission bound and RAG pool (audit 2026-11-04 REL-01): overload degrades instead of
exhausting the database pool. No Ollama needed."""

import asyncio

import pytest
from pydantic import HttpUrl, SecretStr

from app.contracts.common import ContractError, ErrorCode
from app.main import build_container
from app.shared.config import Settings
from app.shared.llm import Admission
from tests.conftest import TEST_PII_KEY


async def hold(admission: Admission, entered: asyncio.Event, release: asyncio.Event) -> None:
    async with admission.slot():
        entered.set()
        await release.wait()


async def test_calls_beyond_the_queue_fail_at_once_and_the_queue_drains() -> None:
    admission = Admission(max_inflight=1, max_queue=1)
    release = asyncio.Event()
    first, second = asyncio.Event(), asyncio.Event()
    running = asyncio.create_task(hold(admission, first, release))
    await first.wait()
    queued = asyncio.create_task(hold(admission, second, release))
    await asyncio.sleep(0)  # the second call is now waiting for the slot

    with pytest.raises(ContractError) as refused:
        async with admission.slot():
            pass

    assert refused.value.code is ErrorCode.LLM_NO_DISPONIBLE
    assert refused.value.details == {"motivo": "saturado"}
    assert not second.is_set()
    release.set()
    await asyncio.gather(running, queued)
    assert second.is_set()
    async with admission.slot():  # everything released: a new call gets in
        pass


async def test_a_failed_call_releases_its_slot() -> None:
    admission = Admission(max_inflight=1, max_queue=0)

    with pytest.raises(RuntimeError):
        async with admission.slot():
            raise RuntimeError("ollama timed out")

    async with admission.slot():
        pass


async def test_rag_retrieval_does_not_draw_from_the_pool_its_chat_turn_holds() -> None:
    settings = Settings(
        admin_token=SecretStr("test-admin-token-0000"),
        pii_hash_salt=SecretStr("test-pii-salt-000000"),
        ollama_base_url=HttpUrl("http://127.0.0.1:9"),  # never called: only wired
        ollama_chat_model="m",
        ollama_embed_model="e",
        pii_encryption_keys=SecretStr(TEST_PII_KEY),
    )
    box = build_container(settings)
    try:
        assert box.rag_engine is not None
        assert box.rag_engine is not box.engine
    finally:
        for engine in (box.engine, box.rag_engine):
            if engine is not None:
                await engine.dispose()
        for client in (box.llm, box.embedding):
            if client is not None:
                await client.aclose()
