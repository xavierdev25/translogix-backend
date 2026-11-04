"""Repositories: rows in, contract models out. One ``AsyncSession`` per unit of work."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.attachments import AttachmentUploadResponse
from app.contracts.common import ChannelType
from app.contracts.conversation import ConversationState, ConversationStatus
from app.contracts.escalation import HandoffPayload, HandoffStatus, HandoffSummary
from app.contracts.inbound import InboundMessage
from app.contracts.ports import AuditEvent, ScoredChunk
from app.contracts.pqrs import PqrsTicketRequest, PqrsTicketResponse
from app.contracts.quotation import CotizacionRequest, CotizacionResponse
from app.contracts.rag import ChunkMetadata
from app.contracts.reply import ConversationReply
from app.db.models import (
    AttachmentRow,
    AuditEventRow,
    ConversationRow,
    HandoffRow,
    MessageRow,
    PqrsTicketRow,
    QuoteRow,
    RagChunkRow,
    RagDocumentRow,
)

INBOUND, OUTBOUND = "INBOUND", "OUTBOUND"


UNIQUE_VIOLATION = "23505"


class DuplicateMessageError(Exception):
    """The (channel, message_id) pair was already received."""


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: UUID, *, for_update: bool = False) -> ConversationState | None:
        """``for_update`` locks the row until commit: two turns of one conversation run one
        after the other, so neither overwrites the other's state (audit 2026-11-04, CON-01)."""
        row = await self._session.get(
            ConversationRow, session_id, with_for_update=for_update, populate_existing=for_update
        )
        return ConversationState.model_validate(row.state_json) if row else None

    async def latest_open(
        self, channel: ChannelType, user_hash: str, since: datetime, *, for_update: bool = False
    ) -> ConversationState | None:
        query = (
            select(ConversationRow)
            .where(
                ConversationRow.channel == channel.value,
                ConversationRow.external_user_id_hash == user_hash,
                ConversationRow.status != ConversationStatus.CLOSED.value,
                ConversationRow.updated_at >= since,
            )
            .order_by(ConversationRow.updated_at.desc())
            .limit(1)
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await self._session.scalar(query)
        return ConversationState.model_validate(row.state_json) if row else None

    async def save(self, state: ConversationState) -> None:
        data = state.model_dump(mode="json")
        row = await self._session.get(ConversationRow, state.session_id)
        if row is None:
            self._session.add(
                ConversationRow(
                    session_id=state.session_id,
                    channel=state.channel.value,
                    external_user_id_hash=state.external_user_id_hash,
                    status=state.status.value,
                    state_json=data,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                )
            )
        else:
            row.status = state.status.value
            row.state_json = data
            row.updated_at = state.updated_at
        await self._session.flush()


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_reply(self, channel: ChannelType, in_reply_to: str) -> ConversationReply | None:
        row = await self._session.scalar(
            select(MessageRow).where(
                MessageRow.channel == channel.value,
                MessageRow.direction == OUTBOUND,
                MessageRow.in_reply_to == in_reply_to,
            )
        )
        return ConversationReply.model_validate(row.content_json["reply"]) if row else None

    async def add_inbound(self, msg: InboundMessage, session_id: UUID, at: datetime) -> None:
        row = MessageRow(
            id=uuid4(),
            session_id=session_id,
            channel=msg.channel.value,
            direction=INBOUND,
            message_id=msg.message_id,
            in_reply_to=None,
            message_type=msg.message_type.value,
            # Never the external user id or provider metadata: content only.
            content_json={"content": msg.content.model_dump(mode="json")},
            delivery_mode=msg.delivery_mode.value,
            created_at=at,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == UNIQUE_VIOLATION:
                raise DuplicateMessageError(msg.message_id) from error
            raise

    async def add_outbound(self, reply: ConversationReply) -> None:
        self._session.add(
            MessageRow(
                id=uuid4(),
                session_id=reply.session_id,
                channel=reply.channel.value,
                direction=OUTBOUND,
                message_id=str(reply.reply_id),
                in_reply_to=reply.in_reply_to,
                message_type=None,
                content_json={"reply": reply.model_dump(mode="json")},
                delivery_mode=reply.meta.delivery_mode.value,
                created_at=reply.created_at,
            )
        )
        await self._session.flush()

    async def history(self, session_id: UUID) -> Sequence[MessageRow]:
        rows = await self._session.scalars(
            select(MessageRow)
            .where(MessageRow.session_id == session_id)
            .order_by(MessageRow.created_at, MessageRow.direction)
        )
        return list(rows)


class HandoffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payload: HandoffPayload) -> None:
        self._session.add(
            HandoffRow(
                handoff_id=payload.handoff_id,
                session_id=payload.session_id,
                channel=payload.canal.value,
                payload_json=payload.model_dump(mode="json"),
                status=payload.status.value,
                created_at=payload.created_at,
            )
        )
        await self._session.flush()

    async def get(self, handoff_id: UUID) -> HandoffPayload | None:
        row = await self._session.get(HandoffRow, handoff_id)
        return HandoffPayload.model_validate(row.payload_json) if row else None

    async def get_with_assignee(
        self, handoff_id: UUID
    ) -> tuple[HandoffPayload, UUID | None] | None:
        row = await self._session.get(HandoffRow, handoff_id)
        if row is None:
            return None
        return HandoffPayload.model_validate(row.payload_json), row.assigned_staff_user_id

    async def update(self, payload: HandoffPayload, *, assignee: UUID | None = None) -> None:
        """``assignee`` is only written when given: closing keeps who took the case."""
        row = await self._session.get_one(HandoffRow, payload.handoff_id)
        row.payload_json = payload.model_dump(mode="json")
        row.status = payload.status.value
        if assignee is not None:
            row.assigned_staff_user_id = assignee
        await self._session.flush()

    async def list_by_status(
        self, status: HandoffStatus | None, *, reviewer: UUID | None = None
    ) -> list[HandoffSummary]:
        """``reviewer`` set: only the PENDING queue and the cases assigned to that REVIEWER."""
        query = select(HandoffRow).order_by(HandoffRow.created_at.desc())
        if status is not None:
            query = query.where(HandoffRow.status == status.value)
        if reviewer is not None:
            query = query.where(
                or_(
                    HandoffRow.status == HandoffStatus.PENDING.value,
                    HandoffRow.assigned_staff_user_id == reviewer,
                )
            )
        payloads = [
            HandoffPayload.model_validate(r.payload_json)
            for r in await self._session.scalars(query)
        ]
        return [
            HandoffSummary(
                handoff_id=p.handoff_id,
                session_id=p.session_id,
                canal=p.canal,
                prioridad=p.prioridad,
                cola_destino=p.cola_destino,
                numero_guia_consultada=p.numero_guia_consultada,
                status=p.status,
                created_at=p.created_at,
            )
            for p in payloads
        ]


class QuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        request: CotizacionRequest,
        response: CotizacionResponse,
        session_id: UUID | None,
        at: datetime,
    ) -> None:
        self._session.add(
            QuoteRow(
                cotizacion_id=response.cotizacion_id,
                session_id=session_id,
                request_json=request.model_dump(mode="json"),
                response_json=response.model_dump(mode="json"),
                formula_version=response.formula_version,
                created_at=at,
            )
        )
        await self._session.flush()


class PqrsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, request: PqrsTicketRequest, ticket: PqrsTicketResponse, at: datetime
    ) -> None:
        self._session.add(
            PqrsTicketRow(
                ticket_id=ticket.ticket_id,
                session_id=request.session_id,
                tipo=request.tipo.value,
                numero_guia=request.numero_guia,
                estado=ticket.estado.value,
                payload_json={
                    "request": request.model_dump(mode="json"),
                    "response": ticket.model_dump(mode="json"),
                },
                created_at=at,
            )
        )
        await self._session.flush()


class AttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        attachment: AttachmentUploadResponse,
        *,
        session_id: UUID | None,
        storage_ref: str,
        at: datetime,
    ) -> None:
        self._session.add(
            AttachmentRow(
                attachment_id=attachment.attachment_id,
                session_id=session_id,
                kind=attachment.kind,
                mime_type=attachment.mime_type,
                size_bytes=attachment.size_bytes,
                sha256=attachment.sha256,
                declared_doc_code=attachment.declared_doc_code.value
                if attachment.declared_doc_code
                else None,
                storage_ref=storage_ref,
                created_at=at,
            )
        )
        await self._session.flush()

    async def existing(self, attachment_ids: Sequence[str]) -> set[str]:
        if not attachment_ids:
            return set()
        rows = await self._session.scalars(
            select(AttachmentRow.attachment_id).where(
                AttachmentRow.attachment_id.in_(attachment_ids)
            )
        )
        return set(rows)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, event: AuditEvent) -> None:
        self._session.add(
            AuditEventRow(
                id=event.id,
                trace_id=event.trace_id,
                session_id=event.session_id,
                channel=event.channel.value if event.channel else None,
                intent=event.intent.value if event.intent else None,
                services_called=[s.value for s in event.services_called],
                escalation_json=event.escalation.model_dump(mode="json")
                if event.escalation
                else None,
                evidence_ids=event.evidence_ids,
                llm_used=event.llm_used,
                latency_ms=event.latency_ms,
                created_at=event.created_at,
            )
        )
        await self._session.flush()

    async def channel_counters(
        self, since: datetime | None, until: datetime | None
    ) -> dict[str, dict[str, int]]:
        def window(column: Any) -> list[Any]:
            conditions = []
            if since is not None:
                conditions.append(column >= since)
            if until is not None:
                conditions.append(column <= until)
            return conditions

        counters: dict[str, dict[str, int]] = {
            c.value: {
                "conversaciones": 0,
                "mensajes_entrantes": 0,
                "mensajes_salientes": 0,
                "handoffs": 0,
            }
            for c in ChannelType
        }
        for channel, total in await self._session.execute(
            select(ConversationRow.channel, func.count())
            .where(*window(ConversationRow.created_at))
            .group_by(ConversationRow.channel)
        ):
            counters[channel]["conversaciones"] = total
        for channel, direction, total in await self._session.execute(
            select(MessageRow.channel, MessageRow.direction, func.count())
            .where(*window(MessageRow.created_at))
            .group_by(MessageRow.channel, MessageRow.direction)
        ):
            key = "mensajes_entrantes" if direction == INBOUND else "mensajes_salientes"
            counters[channel][key] = total
        for channel, total in await self._session.execute(
            select(HandoffRow.channel, func.count())
            .where(*window(HandoffRow.created_at))
            .group_by(HandoffRow.channel)
        ):
            counters[channel]["handoffs"] = total
        return counters


class RagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def document_hashes(self) -> dict[str, str]:
        rows = await self._session.execute(
            select(RagDocumentRow.doc_code, RagDocumentRow.content_hash)
        )
        return dict(rows.tuples().all())

    async def replace_document(
        self,
        *,
        doc_code: str,
        doc_title: str,
        source_file: str,
        content_hash: str,
        ingested_at: datetime,
        chunks: Sequence[tuple[str, str, ChunkMetadata, list[float]]],
    ) -> None:
        existing = await self._session.get(RagDocumentRow, doc_code)
        if existing is not None:
            await self._session.delete(existing)  # cascades to its chunks
            await self._session.flush()
        self._session.add(
            RagDocumentRow(
                doc_code=doc_code,
                doc_title=doc_title,
                source_file=source_file,
                content_hash=content_hash,
                ingested_at=ingested_at,
            )
        )
        # No ORM relationship orders the inserts: the parent row must exist before its chunks.
        await self._session.flush()
        for chunk_id, content, metadata, embedding in chunks:
            self._session.add(
                RagChunkRow(
                    chunk_id=chunk_id,
                    doc_code=doc_code,
                    section_path=metadata.section_path,
                    content=content,
                    metadata_json=metadata.model_dump(mode="json"),
                    content_hash=metadata.content_hash,
                    embedding=embedding,
                )
            )
        await self._session.flush()

    async def count_chunks(self) -> int:
        return int(await self._session.scalar(select(func.count()).select_from(RagChunkRow)) or 0)

    async def all_chunks(self) -> list[tuple[str, str, ChunkMetadata]]:
        rows = await self._session.scalars(select(RagChunkRow).order_by(RagChunkRow.chunk_id))
        return [
            (r.chunk_id, r.content, ChunkMetadata.model_validate(r.metadata_json)) for r in rows
        ]

    async def search(
        self, embedding: Sequence[float], *, top_k: int, doc_codes: Sequence[str] | None = None
    ) -> list[ScoredChunk]:
        distance = RagChunkRow.embedding.cosine_distance(list(embedding))
        query = select(RagChunkRow, distance.label("distance")).order_by(distance).limit(top_k)
        if doc_codes:
            query = query.where(RagChunkRow.doc_code.in_(doc_codes))
        result = await self._session.execute(query)
        return [
            ScoredChunk(
                chunk_id=row.chunk_id,
                content=row.content,
                metadata=ChunkMetadata.model_validate(row.metadata_json),
                score=max(0.0, min(1.0, 1.0 - float(dist))),
            )
            for row, dist in result
        ]
