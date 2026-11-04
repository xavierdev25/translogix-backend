from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, Json


class ShipmentBatchRow(Base):
    """``locked_until`` is the processing lease: a replay may resume the batch once it expires."""

    __tablename__ = "shipment_batches"
    __table_args__ = (UniqueConstraint("client_id", "referencia_lote"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    client_id: Mapped[UUID] = mapped_column(ForeignKey("enterprise_clients.id"))
    referencia_lote: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    total_items: Mapped[int]
    accepted_items: Mapped[int]
    rejected_items: Mapped[int]
    locked_until: Mapped[datetime | None]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class ShipmentBatchItemRow(Base):
    """One line of a batch. No personal data: recipients live in ``shipment_recipients``."""

    __tablename__ = "shipment_batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "line_number"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("shipment_batches.id"))
    line_number: Mapped[int]
    referencia_cliente: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    # shipments.batch_item_id points back here: the cycle is created with ALTER (use_alter).
    shipment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("shipments.id", use_alter=True), index=True
    )
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(Json)
    created_at: Mapped[datetime]
    processed_at: Mapped[datetime | None]
