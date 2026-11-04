from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, Json


class EnterpriseClientRow(Base):
    __tablename__ = "enterprise_clients"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    nit: Mapped[str] = mapped_column(String(20), unique=True)
    razon_social: Mapped[str] = mapped_column(String(200))
    estado: Mapped[str] = mapped_column(String(16))
    tipo_cuenta: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ClientApiKeyRow(Base):
    __tablename__ = "client_api_keys"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    client_id: Mapped[UUID] = mapped_column(ForeignKey("enterprise_clients.id"), index=True)
    key_prefix: Mapped[str] = mapped_column(String(12), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(Json)
    created_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
