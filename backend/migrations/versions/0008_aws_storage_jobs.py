"""Hybrid AWS (CR-011): attachment storage backend, migration log, document jobs and outbox.

Additive only:
- ``attachments.storage_backend``: ``local`` for every existing row, which keeps working as
  before;
- three new tables.

Downgrade drops them. It refuses to do so while an attachment lives in S3, because the local
row would point to a file that does not exist: migrate it back, or accept the loss explicitly.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("storage_backend", sa.String(length=8), nullable=False, server_default="local"),
    )
    op.create_table(
        "attachment_storage_migrations",
        sa.Column("attachment_id", sa.String(length=64), nullable=False),
        sa.Column("target_backend", sa.String(length=8), nullable=False),
        sa.Column("target_key", sa.String(length=256), nullable=False),
        sa.Column("source_ref", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["attachments.attachment_id"],
            name=op.f("fk_attachment_storage_migrations_attachment_id_attachments"),
        ),
        sa.PrimaryKeyConstraint("attachment_id", name=op.f("pk_attachment_storage_migrations")),
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params_json", JSON, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("result_json", JSON, nullable=True),
        sa.Column("requested_by_staff_user_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
    )
    op.create_index(op.f("ix_processing_jobs_status"), "processing_jobs", ["status"], unique=False)
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("payload_json", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
    )
    op.create_index(
        "ix_outbox_messages_pending",
        "outbox_messages",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    in_s3 = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM attachments WHERE storage_backend <> 'local'"))
        .scalar()
    )
    if in_s3:
        raise RuntimeError(
            f"{in_s3} adjuntos están en S3: el downgrade dejaría filas apuntando a archivos que no "
            "existen en disco. Devuélvelos a local antes (o asume la pérdida explícitamente)."
        )
    op.drop_index("ix_outbox_messages_pending", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index(op.f("ix_processing_jobs_status"), table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_table("attachment_storage_migrations")
    op.drop_column("attachments", "storage_backend")
