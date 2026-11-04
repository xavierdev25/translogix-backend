"""Creating and reading jobs. The job row and its outbox message are written in **one**
transaction: either both exist or neither does (Transactional Outbox). There is no
"saved but not published" window, nor the reverse."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.ops import JobStatus, ReindexJob, ReindexResponse
from app.contracts.rag import DocCode
from app.db.models import OutboxMessageRow, ProcessingJobRow

RAG_REINDEX = "RAG_REINDEX"
TOPIC = "documents.reindex"
MESSAGE_VERSION = 1


def message_for(job_id: UUID) -> dict[str, Any]:
    """What travels in SQS: identifiers only, never PII, documents or histories."""
    return {"v": MESSAGE_VERSION, "kind": RAG_REINDEX, "job_id": str(job_id)}


class JobService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def enqueue_reindex(
        self,
        *,
        doc_codes: Sequence[DocCode] | None,
        force: bool,
        staff_user_id: UUID | None,
        request_id: str | None,
        now: datetime,
    ) -> ProcessingJobRow:
        job = ProcessingJobRow(
            id=uuid4(),
            kind=RAG_REINDEX,
            status=JobStatus.PENDING.value,
            params_json={
                "doc_codes": [d.value for d in doc_codes] if doc_codes else None,
                "force": force,
            },
            attempts=0,
            requested_by_staff_user_id=staff_user_id,
            request_id=request_id,
            created_at=now,
            updated_at=now,
        )
        async with self._sessions() as session:
            session.add(job)
            session.add(
                OutboxMessageRow(
                    id=uuid4(),
                    topic=TOPIC,
                    payload_json=message_for(job.id),
                    created_at=now,
                    attempts=0,
                )
            )
            await session.commit()
        return job

    async def get(self, job_id: UUID) -> ProcessingJobRow | None:
        async with self._sessions() as session:
            return await session.get(ProcessingJobRow, job_id)

    async def requeue(self, job_id: UUID, now: datetime) -> bool:
        """Manual retry after the DLQ: back to PENDING with a new outbox message."""
        async with self._sessions() as session:
            job = await session.get(ProcessingJobRow, job_id, with_for_update=True)
            if job is None or job.status not in (
                JobStatus.DEAD_LETTER.value,
                JobStatus.FAILED.value,
            ):
                return False
            job.status, job.attempts, job.last_error, job.updated_at = (
                JobStatus.PENDING.value,
                0,
                None,
                now,
            )
            session.add(
                OutboxMessageRow(
                    id=uuid4(),
                    topic=TOPIC,
                    payload_json=message_for(job.id),
                    created_at=now,
                    attempts=0,
                )
            )
            await session.commit()
        return True


def to_contract(job: ProcessingJobRow) -> ReindexJob:
    result = ReindexResponse.model_validate(job.result_json) if job.result_json else None
    return ReindexJob(
        job_id=job.id,
        estado=JobStatus(job.status),
        intentos=job.attempts,
        creado_en=job.created_at,
        actualizado_en=job.updated_at,
        iniciado_en=job.started_at,
        terminado_en=job.finished_at,
        resultado=result,
        error=job.last_error,
    )
