"""SQS document worker: RAG reindexing, reusing the same ``IngestionService`` as the API.

**Delivery and retries:**
- Long polling (``WaitTimeSeconds``) and one message at a time. Embeddings go to the same local
  Ollama, so processing more in parallel would not add inference capacity.
- **Idempotency:** a job is "claimed" with a conditional UPDATE (PENDING, FAILED, or PROCESSING
  whose lease expired). A duplicate of a DONE or DEAD_LETTER job is deleted without
  reprocessing.
- **Limited retries:** on failure the job stays FAILED and the message is not deleted, so SQS
  delivers it again after ``ChangeMessageVisibility`` (growing backoff). When
  ``worker_max_attempts`` is reached, the job is marked DEAD_LETTER: the queue's redrive policy
  (``maxReceiveCount`` equal to that value) moves the message to the DLQ.
- ``sweep_dlq`` records what reached the DLQ (metric and state) and removes it from there. The
  job keeps the record, and ``retry`` re-queues it.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.common import ContractError
from app.contracts.ops import JobStatus, ReindexResponse
from app.contracts.rag import DocCode
from app.db.models import ProcessingJobRow
from app.jobs.service import RAG_REINDEX
from app.shared import metrics

logger = logging.getLogger("app.worker")


class JobWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        sqs: Any,
        *,
        queue_url: str,
        dlq_url: str | None,
        ingestion: Any,
        clock: Any,
        embed_model: str,
        embed_dim: int,
        max_attempts: int,
        visibility_timeout: int,
        wait_seconds: int,
        retry_backoff_seconds: int = 30,
    ) -> None:
        self._sessions, self._sqs, self._url, self._dlq = sessions, sqs, queue_url, dlq_url
        self._ingestion, self._clock = ingestion, clock
        self._embed_model, self._embed_dim = embed_model, embed_dim
        self._max, self._visibility, self._wait = max_attempts, visibility_timeout, wait_seconds
        self._backoff = retry_backoff_seconds

    async def _sqs_call(self, operation: str, fn: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, **kwargs)
        except Exception:
            metrics.emit("InfrastructureErrors", 1, component="sqs", operation=operation)
            raise

    async def poll_once(self) -> int:
        response = await self._sqs_call(
            "receive",
            self._sqs.receive_message,
            QueueUrl=self._url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=self._wait,
            VisibilityTimeout=self._visibility,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        for message in messages:
            await self.handle(message)
        return len(messages)

    async def _delete(self, message: dict[str, Any]) -> None:
        await self._sqs_call(
            "delete",
            self._sqs.delete_message,
            QueueUrl=self._url,
            ReceiptHandle=message["ReceiptHandle"],
        )

    async def _claim(self, job_id: UUID, now: datetime) -> ProcessingJobRow | None:
        stale = now - timedelta(seconds=self._visibility)
        async with self._sessions() as session:
            claimed = await session.scalar(
                update(ProcessingJobRow)
                .where(
                    ProcessingJobRow.id == job_id,
                    or_(
                        ProcessingJobRow.status.in_(
                            [JobStatus.PENDING.value, JobStatus.FAILED.value]
                        ),
                        (ProcessingJobRow.status == JobStatus.PROCESSING.value)
                        & (ProcessingJobRow.started_at < stale),
                    ),
                )
                .values(
                    status=JobStatus.PROCESSING.value,
                    attempts=ProcessingJobRow.attempts + 1,
                    started_at=now,
                    updated_at=now,
                )
                .returning(ProcessingJobRow)
            )
            await session.commit()
            return claimed

    async def handle(self, message: dict[str, Any]) -> str:
        """Outcome: done | duplicate | retry | dead_letter | invalid | busy."""
        try:
            body = json.loads(message["Body"])
            job_id = UUID(body["job_id"])
            if body.get("kind") != RAG_REINDEX:
                raise ValueError("kind")
        except (ValueError, KeyError, TypeError):
            # A malformed message will never succeed: it is not retried.
            metrics.emit("JobMessages", 1, outcome="invalid")
            await self._delete(message)
            return "invalid"
        now = self._clock.now()
        job = await self._claim(job_id, now)
        if job is None:
            async with self._sessions() as session:
                current = await session.get(ProcessingJobRow, job_id)
            if current is None or current.status in (
                JobStatus.DONE.value,
                JobStatus.DEAD_LETTER.value,
            ):
                metrics.emit("JobMessages", 1, outcome="duplicate")
                await self._delete(message)
                return "duplicate"
            metrics.emit("JobMessages", 1, outcome="busy")  # another worker holds it
            return "busy"
        if job.attempts > 1:
            metrics.emit("JobRetries", 1, kind=RAG_REINDEX)
        params = job.params_json
        try:
            with metrics.timer("JobDuration", kind=RAG_REINDEX):
                report = await self._ingestion.reindex(
                    now=now,
                    doc_codes=[DocCode(c) for c in params["doc_codes"]]
                    if params.get("doc_codes")
                    else None,
                    force=bool(params.get("force")),
                )
        except Exception as error:  # Ollama down, DB, a corrupt document…
            return await self._failed(message, job, error)
        result = ReindexResponse(
            documentos=report.documentos,
            chunks=report.chunks,
            omitidos_sin_cambios=report.omitidos_sin_cambios,
            duracion_ms=report.duracion_ms,
            embed_model=self._embed_model,
            embed_dim=self._embed_dim,
        )
        await self._finish(job.id, JobStatus.DONE, result=result.model_dump(mode="json"))
        await self._delete(message)
        metrics.emit("JobMessages", 1, outcome="done")
        return "done"

    async def _failed(
        self, message: dict[str, Any], job: ProcessingJobRow, error: Exception
    ) -> str:
        code = error.code.value if isinstance(error, ContractError) else type(error).__name__
        final = job.attempts >= self._max
        status = JobStatus.DEAD_LETTER if final else JobStatus.FAILED
        await self._finish(job.id, status, error=code[:300])
        logger.warning(
            "job failed",
            extra={"job_kind": RAG_REINDEX, "attempt": job.attempts, "error_type": code},
        )
        # Backoff: the message comes back after the visibility and SQS counts the receive. On
        # the last attempt it becomes visible at once, so the next receive (any worker) makes
        # SQS move it to the DLQ (redrive) without waiting out the whole visibility timeout.
        await self._sqs_call(
            "visibility",
            self._sqs.change_message_visibility,
            QueueUrl=self._url,
            ReceiptHandle=message["ReceiptHandle"],
            VisibilityTimeout=0 if final else min(self._backoff * job.attempts, self._visibility),
        )
        metrics.emit("JobMessages", 1, outcome="dead_letter" if final else "retry")
        return "dead_letter" if final else "retry"

    async def _finish(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = self._clock.now()
        async with self._sessions() as session:
            job = await session.get_one(ProcessingJobRow, job_id)
            job.status, job.updated_at, job.last_error = status.value, now, error
            if status in (JobStatus.DONE, JobStatus.DEAD_LETTER):
                job.finished_at = now
            if result is not None:
                job.result_json = result
            await session.commit()

    async def sweep_dlq(self, max_messages: int = 10) -> int:
        """Records the messages in the DLQ (state and metric) and removes them from there."""
        if not self._dlq:
            return 0
        response = await self._sqs_call(
            "receive",
            self._sqs.receive_message,
            QueueUrl=self._dlq,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=0,
        )
        count = 0
        for message in response.get("Messages", []):
            try:
                job_id = UUID(json.loads(message["Body"])["job_id"])
            except (ValueError, KeyError, TypeError):
                job_id = None
            if job_id is not None:
                async with self._sessions() as session:
                    job = await session.get(ProcessingJobRow, job_id)
                    if job is not None and job.status != JobStatus.DONE.value:
                        job.status, job.updated_at = JobStatus.DEAD_LETTER.value, self._clock.now()
                        job.finished_at = job.finished_at or self._clock.now()
                        await session.commit()
            await self._sqs_call(
                "delete",
                self._sqs.delete_message,
                QueueUrl=self._dlq,
                ReceiptHandle=message["ReceiptHandle"],
            )
            count += 1
        if count:
            metrics.emit("DeadLetterMessages", count, kind=RAG_REINDEX)
        return count
