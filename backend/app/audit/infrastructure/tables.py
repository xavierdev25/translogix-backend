from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ApiAuditEventRow(Base):
    """Append-only. ``client_id`` is null when the caller could not be authenticated."""

    __tablename__ = "api_audit_events"
    __table_args__ = (Index(None, "client_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    client_id: Mapped[UUID | None]
    api_key_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(32))
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    http_status: Mapped[int]
    created_at: Mapped[datetime]
