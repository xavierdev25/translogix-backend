"""Initial schema: tables of contract §8 plus attachments; pgvector for rag_chunks.

Revision ID: 0001
Revises:
Create Date: 2026-11-03 16:53:13.582259+00:00
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The vector size is fixed when the table is created: changing EMBED_DIM needs a new
# migration and a reindex. No approximate index: the corpus is a few dozen chunks.
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "attachments",
        sa.Column("attachment_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("declared_doc_code", sa.String(length=32), nullable=True),
        sa.Column("storage_ref", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("attachment_id", name=op.f("pk_attachments")),
    )
    op.create_index(op.f("ix_attachments_session_id"), "attachments", ["session_id"], unique=False)
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column(
            "services_called",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "escalation_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "evidence_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        op.f("ix_audit_events_created_at"), "audit_events", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_audit_events_session_id"), "audit_events", ["session_id"], unique=False
    )
    op.create_index(op.f("ix_audit_events_trace_id"), "audit_events", ["trace_id"], unique=False)
    op.create_table(
        "conversations",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("external_user_id_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "state_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_conversations")),
    )
    op.create_index(
        op.f("ix_conversations_channel_external_user_id_hash_updated_at"),
        "conversations",
        ["channel", "external_user_id_hash", "updated_at"],
        unique=False,
    )
    op.create_index(op.f("ix_conversations_status"), "conversations", ["status"], unique=False)
    op.create_table(
        "handoffs",
        sa.Column("handoff_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column(
            "payload_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("handoff_id", name=op.f("pk_handoffs")),
    )
    op.create_index(op.f("ix_handoffs_session_id"), "handoffs", ["session_id"], unique=False)
    op.create_index(op.f("ix_handoffs_status"), "handoffs", ["status"], unique=False)
    op.create_table(
        "pqrs_tickets",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("tipo", sa.String(length=16), nullable=False),
        sa.Column("numero_guia", sa.String(length=16), nullable=True),
        sa.Column("estado", sa.String(length=16), nullable=False),
        sa.Column(
            "payload_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id", name=op.f("pk_pqrs_tickets")),
    )
    op.create_index(
        op.f("ix_pqrs_tickets_numero_guia"), "pqrs_tickets", ["numero_guia"], unique=False
    )
    op.create_index(
        op.f("ix_pqrs_tickets_session_id"), "pqrs_tickets", ["session_id"], unique=False
    )
    op.create_table(
        "quotes",
        sa.Column("cotizacion_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "request_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "response_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("cotizacion_id", name=op.f("pk_quotes")),
    )
    op.create_index(op.f("ix_quotes_session_id"), "quotes", ["session_id"], unique=False)
    op.create_table(
        "rag_documents",
        sa.Column("doc_code", sa.String(length=32), nullable=False),
        sa.Column("doc_title", sa.String(length=256), nullable=False),
        sa.Column("source_file", sa.String(length=256), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("doc_code", name=op.f("pk_rag_documents")),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("message_id", sa.String(length=256), nullable=False),
        sa.Column("in_reply_to", sa.String(length=256), nullable=True),
        sa.Column("message_type", sa.String(length=24), nullable=True),
        sa.Column(
            "content_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("delivery_mode", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversations.session_id"],
            name=op.f("fk_messages_session_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "channel",
            "message_id",
            "direction",
            name=op.f("uq_messages_channel_message_id_direction"),
        ),
    )
    op.create_index(op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False)
    op.create_index(op.f("ix_messages_in_reply_to"), "messages", ["in_reply_to"], unique=False)
    op.create_index(op.f("ix_messages_session_id"), "messages", ["session_id"], unique=False)
    op.create_table(
        "rag_chunks",
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("doc_code", sa.String(length=32), nullable=False),
        sa.Column("section_path", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False),
        sa.ForeignKeyConstraint(
            ["doc_code"],
            ["rag_documents.doc_code"],
            name=op.f("fk_rag_chunks_doc_code_rag_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", name=op.f("pk_rag_chunks")),
    )
    op.create_index(
        op.f("ix_rag_chunks_content_hash"), "rag_chunks", ["content_hash"], unique=False
    )
    op.create_index(op.f("ix_rag_chunks_doc_code"), "rag_chunks", ["doc_code"], unique=False)


def downgrade() -> None:
    # The vector extension is left installed: other databases objects may use it.
    op.drop_index(op.f("ix_rag_chunks_doc_code"), table_name="rag_chunks")
    op.drop_index(op.f("ix_rag_chunks_content_hash"), table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_index(op.f("ix_messages_session_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_in_reply_to"), table_name="messages")
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_table("messages")
    op.drop_table("rag_documents")
    op.drop_index(op.f("ix_quotes_session_id"), table_name="quotes")
    op.drop_table("quotes")
    op.drop_index(op.f("ix_pqrs_tickets_session_id"), table_name="pqrs_tickets")
    op.drop_index(op.f("ix_pqrs_tickets_numero_guia"), table_name="pqrs_tickets")
    op.drop_table("pqrs_tickets")
    op.drop_index(op.f("ix_handoffs_status"), table_name="handoffs")
    op.drop_index(op.f("ix_handoffs_session_id"), table_name="handoffs")
    op.drop_table("handoffs")
    op.drop_index(op.f("ix_conversations_status"), table_name="conversations")
    op.drop_index(
        op.f("ix_conversations_channel_external_user_id_hash_updated_at"),
        table_name="conversations",
    )
    op.drop_table("conversations")
    op.drop_index(op.f("ix_audit_events_trace_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_session_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_created_at"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(op.f("ix_attachments_session_id"), table_name="attachments")
    op.drop_table("attachments")
