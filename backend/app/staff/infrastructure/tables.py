from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, Json


class StaffUserRow(Base):
    __tablename__ = "staff_users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    nombre: Mapped[str] = mapped_column(String(120))
    roles: Mapped[list[str]] = mapped_column(Json)
    estado: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class StaffTokenRow(Base):
    __tablename__ = "staff_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    staff_user_id: Mapped[UUID] = mapped_column(ForeignKey("staff_users.id"), index=True)
    key_prefix: Mapped[str] = mapped_column(String(12), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
