"""Tables of contract §8 (plus ``attachments``). JSON payloads are contract models dumped."""

import os
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Index, MetaData, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.crypto import EncryptedJson

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
# Fixed at migration time; changing it needs a new migration and a reindex.
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))

Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index(None, "channel", "external_user_id_hash", "updated_at"),)

    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    external_user_id_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("channel", "message_id", "direction"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.session_id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    message_id: Mapped[str] = mapped_column(String(256))
    # Outbound rows point to the inbound message they answer (idempotent replays).
    in_reply_to: Mapped[str | None] = mapped_column(String(256), index=True)
    message_type: Mapped[str | None] = mapped_column(String(24))
    # 2.0.0 (PII-01): encrypted at rest; the code still reads a plain dict.
    content_json: Mapped[dict[str, Any]] = mapped_column(EncryptedJson)
    delivery_mode: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(index=True)


class HandoffRow(Base):
    __tablename__ = "handoffs"

    handoff_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(index=True)
    channel: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime]
    # 1.7.0 (CR-009): the REVIEWER who took the case; null while PENDING or if an
    # ADMIN assigned it with the shared token.
    assigned_staff_user_id: Mapped[UUID | None] = mapped_column(index=True)


class QuoteRow(Base):
    __tablename__ = "quotes"

    cotizacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(Json)
    response_json: Mapped[dict[str, Any]] = mapped_column(Json)
    formula_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime]


class PqrsTicketRow(Base):
    __tablename__ = "pqrs_tickets"

    ticket_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(index=True)
    tipo: Mapped[str] = mapped_column(String(16))
    numero_guia: Mapped[str | None] = mapped_column(String(16), index=True)
    estado: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime]


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[UUID | None] = mapped_column(index=True)
    channel: Mapped[str | None] = mapped_column(String(16))
    intent: Mapped[str | None] = mapped_column(String(32))
    services_called: Mapped[list[str]] = mapped_column(Json)
    escalation_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    evidence_ids: Mapped[list[str]] = mapped_column(Json)
    llm_used: Mapped[bool]
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(index=True)


class AttachmentRow(Base):
    __tablename__ = "attachments"

    attachment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    declared_doc_code: Mapped[str | None] = mapped_column(String(32))
    storage_ref: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime]
    # 2.1.0 (CR-011): where storage_ref lives: "local" (var/attachments) or "s3" (object key).
    storage_backend: Mapped[str] = mapped_column(String(8), default="local", server_default="local")


class RagDocumentRow(Base):
    __tablename__ = "rag_documents"

    doc_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    doc_title: Mapped[str] = mapped_column(String(256))
    source_file: Mapped[str] = mapped_column(String(256))
    content_hash: Mapped[str] = mapped_column(String(64))
    ingested_at: Mapped[datetime]


class RagChunkRow(Base):
    __tablename__ = "rag_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_code: Mapped[str] = mapped_column(
        ForeignKey("rag_documents.doc_code", ondelete="CASCADE"), index=True
    )
    section_path: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(Json)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))


class RateLimitCounterRow(Base):
    """One-minute window per key, shared by every instance (2.0.0, OPS-01)."""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    hits: Mapped[int]


class AttachmentStorageMigrationRow(Base):
    """Result of copying an attachment from local disk to S3 (2.1.0). Lets a re-run resume
    without uploading twice; the local original is kept until the copy is VERIFIED."""

    __tablename__ = "attachment_storage_migrations"

    attachment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("attachments.attachment_id"), primary_key=True
    )
    target_backend: Mapped[str] = mapped_column(String(8))
    target_key: Mapped[str] = mapped_column(String(256))
    source_ref: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16))  # COPIED | VERIFIED | FAILED
    attempts: Mapped[int]
    last_error: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class ProcessingJobRow(Base):
    """Asynchronous document job (2.1.0): only RAG_REINDEX for now. Holds the state the API
    reports; the SQS message only carries the id."""

    __tablename__ = "processing_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    params_json: Mapped[dict[str, Any]] = mapped_column(Json)
    attempts: Mapped[int]
    last_error: Mapped[str | None] = mapped_column(String(300))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    requested_by_staff_user_id: Mapped[UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class OutboxMessageRow(Base):
    """Transactional outbox (2.1.0): written in the same transaction as the job, published to
    SQS by the relay. Ids only, never PII nor documents."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        Index("ix_outbox_messages_pending", "created_at", postgresql_where="published_at IS NULL"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime]
    published_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
