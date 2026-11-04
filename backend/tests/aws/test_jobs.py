"""Phase 2 (CR-011): transactional outbox → SQS → worker → DLQ, on SQS simulated with moto."""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ops import JobStatus
from app.contracts.staff import Role
from app.db.models import OutboxMessageRow, ProcessingJobRow
from app.jobs.outbox import OutboxRelay
from app.jobs.publisher import PublishError, SqsPublisher
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.modules.rag.service import ReindexReport
from tests.auth.test_rbac import staff
from tests.aws.conftest import FakeAws
from tests.db import AppFactory, http, scalar
from tests.shipments.support import auth

REPORT = ReindexReport(
    documentos=1,
    chunks=21,
    omitidos_sin_cambios=3,
    duracion_ms=5,
    tokens_min=10,
    tokens_avg=200,
    tokens_max=512,
)


class FakeIngestion:
    """Stands in for IngestionService (Ollama is not needed for the delivery mechanics)."""

    def __init__(self, failures: int = 0, error: Exception | None = None) -> None:
        self.calls, self._failures = 0, failures
        self._error = error or ContractError(ErrorCode.LLM_NO_DISPONIBLE)

    async def reindex(self, **_: Any) -> ReindexReport:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return REPORT


@pytest.fixture
async def jobs_app(make_app: AppFactory, fake_aws: FakeAws) -> AsyncIterator[FastAPI]:
    async with make_app(
        jobs_backend="sqs", sqs_queue_url=fake_aws.queue_url, sqs_dlq_url=fake_aws.dlq_url
    ) as app:
        async with app.state.container.sessions() as session:
            await session.execute(text("TRUNCATE outbox_messages, processing_jobs"))
            await session.commit()
        yield app


def worker(app: FastAPI, aws: FakeAws, ingestion: Any, max_attempts: int = 3) -> JobWorker:
    box = app.state.container
    return JobWorker(
        box.sessions,
        aws.sqs,
        queue_url=aws.queue_url,
        dlq_url=aws.dlq_url,
        ingestion=ingestion,
        clock=box.clock,
        embed_model="qwen3-embedding:0.6b",
        embed_dim=1024,
        max_attempts=max_attempts,
        visibility_timeout=30,
        wait_seconds=0,
        retry_backoff_seconds=0,
    )


async def enqueue(app: FastAPI) -> uuid.UUID:
    job = await JobService(app.state.container.sessions).enqueue_reindex(
        doc_codes=None,
        force=False,
        staff_user_id=None,
        request_id="r-1",
        now=app.state.container.clock.now(),
    )
    return job.id


def relay(app: FastAPI, aws: FakeAws) -> OutboxRelay:
    box = app.state.container
    return OutboxRelay(box.sessions, SqsPublisher(aws.sqs, aws.queue_url), box.clock)


def queued(aws: FakeAws, url: str) -> int:
    attrs = aws.sqs.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attrs["Attributes"]["ApproximateNumberOfMessages"])


# --- outbox ------------------------------------------------------------------------------------


async def test_the_job_and_its_outbox_message_are_written_together_with_ids_only(
    jobs_app: FastAPI, fake_aws: FakeAws
) -> None:
    job_id = await enqueue(jobs_app)
    async with jobs_app.state.container.sessions() as session:
        outbox = (await session.scalars(select(OutboxMessageRow))).all()

    assert [o.payload_json for o in outbox] == [
        {"v": 1, "kind": "RAG_REINDEX", "job_id": str(job_id)}
    ]
    assert outbox[0].published_at is None
    published = await relay(jobs_app, fake_aws).publish_pending()
    message = fake_aws.sqs.receive_message(QueueUrl=fake_aws.queue_url)["Messages"][0]

    assert published == 1
    assert json.loads(message["Body"]) == {"v": 1, "kind": "RAG_REINDEX", "job_id": str(job_id)}
    assert (
        await scalar(jobs_app, "SELECT count(*) FROM outbox_messages WHERE published_at IS NULL")
        == 0
    )


async def test_a_failed_publish_stays_pending_and_is_retried(
    jobs_app: FastAPI, fake_aws: FakeAws
) -> None:
    class Down:
        async def publish(self, message_id: str, body: dict[str, Any]) -> None:
            raise PublishError("EndpointConnectionError")

    await enqueue(jobs_app)
    box = jobs_app.state.container
    failed = await OutboxRelay(box.sessions, Down(), box.clock).publish_pending()
    attempts = await scalar(jobs_app, "SELECT attempts FROM outbox_messages")
    recovered = await relay(jobs_app, fake_aws).publish_pending()

    assert (failed, attempts, recovered) == (0, 1, 1)
    assert queued(fake_aws, fake_aws.queue_url) == 1


# --- worker ------------------------------------------------------------------------------------


async def test_the_worker_processes_once_and_ignores_duplicates(
    jobs_app: FastAPI, fake_aws: FakeAws
) -> None:
    job_id = await enqueue(jobs_app)
    await relay(jobs_app, fake_aws).publish_pending()
    body = json.dumps({"v": 1, "kind": "RAG_REINDEX", "job_id": str(job_id)})
    fake_aws.sqs.send_message(
        QueueUrl=fake_aws.queue_url, MessageBody=body
    )  # at-least-once: a duplicate
    ingestion = FakeIngestion()
    w = worker(jobs_app, fake_aws, ingestion)

    outcomes = [await w.handle(m) for m in _drain(fake_aws)]
    async with jobs_app.state.container.sessions() as session:
        job = await session.get_one(ProcessingJobRow, job_id)

    assert outcomes == ["done", "duplicate"]
    assert ingestion.calls == 1
    assert (job.status, job.attempts) == ("DONE", 1)
    assert job.result_json is not None
    assert job.result_json["chunks"] == 21
    assert queued(fake_aws, fake_aws.queue_url) == 0


def _drain(aws: FakeAws) -> list[dict[str, Any]]:
    """One receive, with a visibility window so the messages are not seen twice meanwhile."""
    response = aws.sqs.receive_message(
        QueueUrl=aws.queue_url,
        MaxNumberOfMessages=10,
        VisibilityTimeout=30,
        MessageSystemAttributeNames=["ApproximateReceiveCount"],
    )
    return list(response.get("Messages", []))


async def test_ollama_failures_are_retried_three_times_then_go_to_the_dlq(
    jobs_app: FastAPI, fake_aws: FakeAws, capsys: pytest.CaptureFixture[str]
) -> None:
    job_id = await enqueue(jobs_app)
    await relay(jobs_app, fake_aws).publish_pending()
    ingestion = FakeIngestion(failures=99)  # Ollama never comes back
    w = worker(jobs_app, fake_aws, ingestion)

    outcomes = []
    for _ in range(4):  # the 4th receive is beyond maxReceiveCount=3: SQS moves it to the DLQ
        outcomes += [await w.handle(m) for m in _drain(fake_aws)]
    in_dlq = queued(fake_aws, fake_aws.dlq_url)
    swept = await w.sweep_dlq()
    async with jobs_app.state.container.sessions() as session:
        job = await session.get_one(ProcessingJobRow, job_id)
    metrics_out = capsys.readouterr().out

    assert outcomes == ["retry", "retry", "dead_letter"]
    assert ingestion.calls == 3  # limited: never more than worker_max_attempts
    assert (job.status, job.attempts, job.last_error) == ("DEAD_LETTER", 3, "LLM_NO_DISPONIBLE")
    assert (in_dlq, swept, queued(fake_aws, fake_aws.dlq_url)) == (1, 1, 0)
    assert '"JobRetries"' in metrics_out
    assert '"DeadLetterMessages"' in metrics_out
    assert await JobService(jobs_app.state.container.sessions).requeue(job_id, job.updated_at)
    assert (
        await scalar(jobs_app, "SELECT status FROM processing_jobs WHERE id = :j", j=job_id)
        == "PENDING"
    )


async def test_a_job_recovers_after_a_transient_failure(
    jobs_app: FastAPI, fake_aws: FakeAws
) -> None:
    job_id = await enqueue(jobs_app)
    await relay(jobs_app, fake_aws).publish_pending()
    ingestion = FakeIngestion(failures=1)
    w = worker(jobs_app, fake_aws, ingestion)

    first = [await w.handle(m) for m in _drain(fake_aws)]
    second = [await w.handle(m) for m in _drain(fake_aws)]

    assert (first, second) == (["retry"], ["done"])
    assert (
        await scalar(jobs_app, "SELECT attempts FROM processing_jobs WHERE id = :j", j=job_id) == 2
    )


async def test_invalid_and_busy_messages(jobs_app: FastAPI, fake_aws: FakeAws) -> None:
    job_id = await enqueue(jobs_app)
    async with jobs_app.state.container.sessions() as session:
        job = await session.get_one(ProcessingJobRow, job_id)
        job.status, job.started_at = "PROCESSING", jobs_app.state.container.clock.now()
        await session.commit()
    w = worker(jobs_app, fake_aws, FakeIngestion())
    fake_aws.sqs.send_message(QueueUrl=fake_aws.queue_url, MessageBody="{no es json")
    fake_aws.sqs.send_message(
        QueueUrl=fake_aws.queue_url, MessageBody=json.dumps({"kind": "OTRO", "job_id": str(job_id)})
    )
    fake_aws.sqs.send_message(
        QueueUrl=fake_aws.queue_url,
        MessageBody=json.dumps({"v": 1, "kind": "RAG_REINDEX", "job_id": str(job_id)}),
    )

    outcomes = sorted([await w.handle(m) for m in _drain(fake_aws)])

    assert outcomes == ["busy", "invalid", "invalid"]


async def test_a_stale_processing_lease_is_reclaimed(jobs_app: FastAPI, fake_aws: FakeAws) -> None:
    job_id = await enqueue(jobs_app)
    async with jobs_app.state.container.sessions() as session:
        job = await session.get_one(ProcessingJobRow, job_id)
        job.status = "PROCESSING"
        job.started_at = jobs_app.state.container.clock.now() - timedelta(
            minutes=10
        )  # crashed worker
        await session.commit()
    await relay(jobs_app, fake_aws).publish_pending()

    outcomes = [
        await worker(jobs_app, fake_aws, FakeIngestion()).handle(m) for m in _drain(fake_aws)
    ]

    assert outcomes == ["done"]


# --- API ---------------------------------------------------------------------------------------


async def test_async_reindex_through_the_api(jobs_app: FastAPI, fake_aws: FakeAws) -> None:
    _, admin = await staff(jobs_app, "adm.jobs@translogix.co", Role.ADMIN)
    _, reviewer = await staff(jobs_app, "rev.jobs@translogix.co", Role.REVIEWER)
    async with http(jobs_app) as c:
        queued_job = await c.post(
            "/api/v1/admin/rag/reindex", json={"asincrono": True}, headers=auth(admin)
        )
        job_id = queued_job.json()["job_id"]
        pending = await c.get(f"/api/v1/admin/rag/jobs/{job_id}", headers=auth(admin))
        forbidden = await c.get(f"/api/v1/admin/rag/jobs/{job_id}", headers=auth(reviewer))
        await relay(jobs_app, fake_aws).publish_pending()
        for m in _drain(fake_aws):
            await worker(jobs_app, fake_aws, FakeIngestion()).handle(m)
        done = await c.get(f"/api/v1/admin/rag/jobs/{job_id}", headers=auth(admin))
        missing = await c.get(f"/api/v1/admin/rag/jobs/{uuid.uuid4()}", headers=auth(admin))

    assert (queued_job.status_code, queued_job.json()["estado"]) == (202, "PENDING")
    assert pending.json()["estado"] == "PENDING"
    assert forbidden.status_code == 403
    assert done.json()["estado"] == JobStatus.DONE.value
    assert done.json()["resultado"]["chunks"] == 21
    assert missing.status_code == 404


async def test_async_reindex_needs_the_sqs_backend(app: FastAPI) -> None:
    _, admin = await staff(app, "adm.nojobs@translogix.co", Role.ADMIN)
    async with http(app) as c:
        response = await c.post(
            "/api/v1/admin/rag/reindex", json={"asincrono": True}, headers=auth(admin)
        )

    assert (response.status_code, response.json()["error"]["details"]) == (
        503,
        {"variable": "JOBS_BACKEND"},
    )


# --- RAG index across processes ----------------------------------------------------------------


async def test_the_api_notices_an_index_rebuilt_by_the_worker(app: FastAPI) -> None:
    from app.db.repositories import RagRepository
    from app.modules.rag.service import RagService

    rag = RagService(app.state.container.sessions, embedding=None, llm=None, top_k=5)  # type: ignore[arg-type]
    builds = 0
    original = rag._lexical.build

    def counting(rows: Any) -> None:
        nonlocal builds
        builds += 1
        original(rows)

    rag._lexical.build = counting  # type: ignore[method-assign]
    async with app.state.container.sessions() as session:
        repo = RagRepository(session)
        await rag._ensure_lexical(repo)
        await rag._ensure_lexical(repo)  # same version: cached
        await session.execute(
            text("UPDATE rag_documents SET ingested_at = ingested_at + interval '1 second'")
        )
        await session.commit()
        await rag._ensure_lexical(repo)  # another process re-indexed: rebuilt

    assert builds == 2


async def test_worker_logs_and_metrics_carry_no_personal_data(
    jobs_app: FastAPI,
    fake_aws: FakeAws,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    await enqueue(jobs_app)
    await relay(jobs_app, fake_aws).publish_pending()
    error = RuntimeError("Zuleima Parrado 3157779911 calle 99")  # an error message with PII
    for m in _drain(fake_aws):
        await worker(jobs_app, fake_aws, FakeIngestion(failures=1, error=error)).handle(m)
    stored_error = await scalar(jobs_app, "SELECT last_error FROM processing_jobs")
    output = caplog.text + capsys.readouterr().out

    assert stored_error == "RuntimeError"  # the type, never the message
    assert "Zuleima" not in output
    assert "3157779911" not in output
