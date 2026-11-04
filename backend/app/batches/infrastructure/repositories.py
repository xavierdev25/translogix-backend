from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.batches.infrastructure.tables import ShipmentBatchItemRow, ShipmentBatchRow
from app.contracts.shipment_batches import BatchItemStatus, BatchStatus, ItemError
from app.shipments.infrastructure.tables import ShipmentRow

ResultRow = tuple[ShipmentBatchItemRow, str | None, int | None, str | None]


class BatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        client_id: UUID,
        referencia_lote: str,
        request_hash: str,
        total: int,
        at: datetime,
    ) -> UUID | None:
        """New batch id, or None if this client already has that reference (race-safe)."""
        statement = (
            insert(ShipmentBatchRow)
            .values(
                id=uuid4(),
                client_id=client_id,
                referencia_lote=referencia_lote,
                request_hash=request_hash,
                status=BatchStatus.RECEIVED.value,
                total_items=total,
                accepted_items=0,
                rejected_items=0,
                locked_until=None,
                created_at=at,
                started_at=None,
                completed_at=None,
            )
            .on_conflict_do_nothing(index_elements=["client_id", "referencia_lote"])
            .returning(ShipmentBatchRow.id)
        )
        batch_id: UUID | None = await self._session.scalar(statement)
        return batch_id

    async def add_items(
        self, batch_id: UUID, lines: Sequence[tuple[int, str | None]], at: datetime
    ) -> None:
        self._session.add_all(
            ShipmentBatchItemRow(
                id=uuid4(),
                batch_id=batch_id,
                line_number=line,
                referencia_cliente=reference,
                status=BatchItemStatus.PENDING.value,
                shipment_id=None,
                errors_json=[],
                created_at=at,
                processed_at=None,
            )
            for line, reference in lines
        )
        await self._session.flush()

    async def get(self, batch_id: UUID, client_id: UUID) -> ShipmentBatchRow | None:
        """Always scoped to the client: another tenant's batch does not exist for you."""
        row: ShipmentBatchRow | None = await self._session.scalar(
            select(ShipmentBatchRow).where(
                ShipmentBatchRow.id == batch_id, ShipmentBatchRow.client_id == client_id
            )
        )
        return row

    async def get_any(self, batch_id: UUID) -> ShipmentBatchRow | None:
        """Not scoped: only for an ADMIN's global read (CR-009). Clients always use ``get``."""
        row: ShipmentBatchRow | None = await self._session.get(ShipmentBatchRow, batch_id)
        return row

    async def get_by_reference(
        self, client_id: UUID, referencia_lote: str
    ) -> ShipmentBatchRow | None:
        row: ShipmentBatchRow | None = await self._session.scalar(
            select(ShipmentBatchRow).where(
                ShipmentBatchRow.client_id == client_id,
                ShipmentBatchRow.referencia_lote == referencia_lote,
            )
        )
        return row

    async def claim(self, batch_id: UUID, now: datetime, until: datetime) -> bool:
        """Takes the processing lease if nobody holds a live one."""
        claimed = await self._session.scalar(
            update(ShipmentBatchRow)
            .where(
                ShipmentBatchRow.id == batch_id,
                ShipmentBatchRow.status.in_(
                    [BatchStatus.RECEIVED.value, BatchStatus.PROCESSING.value]
                ),
                or_(ShipmentBatchRow.locked_until.is_(None), ShipmentBatchRow.locked_until < now),
            )
            .values(
                status=BatchStatus.PROCESSING.value,
                locked_until=until,
                started_at=func.coalesce(ShipmentBatchRow.started_at, now),
            )
            .returning(ShipmentBatchRow.id)
        )
        return claimed is not None

    async def renew(self, batch_id: UUID, until: datetime) -> None:
        await self._session.execute(
            update(ShipmentBatchRow)
            .where(ShipmentBatchRow.id == batch_id)
            .values(locked_until=until)
        )

    async def pending_items(self, batch_id: UUID) -> list[tuple[UUID, int]]:
        rows = await self._session.execute(
            select(ShipmentBatchItemRow.id, ShipmentBatchItemRow.line_number)
            .where(
                ShipmentBatchItemRow.batch_id == batch_id,
                ShipmentBatchItemRow.status == BatchItemStatus.PENDING.value,
            )
            .order_by(ShipmentBatchItemRow.line_number)
        )
        return [(item_id, line) for item_id, line in rows.tuples()]

    async def mark_created(self, item_id: UUID, shipment_id: UUID, at: datetime) -> None:
        await self._session.execute(
            update(ShipmentBatchItemRow)
            .where(ShipmentBatchItemRow.id == item_id)
            .values(status=BatchItemStatus.CREATED.value, shipment_id=shipment_id, processed_at=at)
        )

    async def mark_rejected(self, item_id: UUID, errors: Sequence[ItemError], at: datetime) -> None:
        await self._session.execute(
            update(ShipmentBatchItemRow)
            .where(ShipmentBatchItemRow.id == item_id)
            .values(
                status=BatchItemStatus.REJECTED.value,
                errors_json=[e.model_dump(mode="json") for e in errors],
                processed_at=at,
            )
        )

    async def counts(self, batch_id: UUID) -> dict[BatchItemStatus, int]:
        rows = await self._session.execute(
            select(ShipmentBatchItemRow.status, func.count())
            .where(ShipmentBatchItemRow.batch_id == batch_id)
            .group_by(ShipmentBatchItemRow.status)
        )
        found = {BatchItemStatus(status): count for status, count in rows.tuples()}
        return {status: found.get(status, 0) for status in BatchItemStatus}

    async def finish(self, batch_id: UUID, at: datetime) -> None:
        """Closes the batch when no item is pending; otherwise only releases the lease."""
        counts = await self.counts(batch_id)
        values: dict[str, Any] = {
            "accepted_items": counts[BatchItemStatus.CREATED],
            "rejected_items": counts[BatchItemStatus.REJECTED],
            "locked_until": None,
        }
        if counts[BatchItemStatus.PENDING] == 0:
            values["status"] = (
                BatchStatus.COMPLETED
                if counts[BatchItemStatus.REJECTED] == 0
                else BatchStatus.COMPLETED_WITH_ERRORS
            ).value
            values["completed_at"] = at
        await self._session.execute(
            update(ShipmentBatchRow).where(ShipmentBatchRow.id == batch_id).values(**values)
        )

    async def results(
        self,
        batch_id: UUID,
        *,
        after_line: int = 0,
        status: BatchItemStatus | None = None,
        limit: int | None = None,
    ) -> list[ResultRow]:
        """Items in line order, with the guía and price snapshot of created shipments."""
        query = (
            select(
                ShipmentBatchItemRow,
                ShipmentRow.numero_guia,
                ShipmentRow.flete_total_cop,
                ShipmentRow.formula_version,
            )
            .outerjoin(ShipmentRow, ShipmentRow.id == ShipmentBatchItemRow.shipment_id)
            .where(
                ShipmentBatchItemRow.batch_id == batch_id,
                ShipmentBatchItemRow.line_number > after_line,
            )
            .order_by(ShipmentBatchItemRow.line_number)
        )
        if status is not None:
            query = query.where(ShipmentBatchItemRow.status == status.value)
        if limit is not None:
            query = query.limit(limit)
        rows = await self._session.execute(query)
        return [(item, guia, flete, formula) for item, guia, flete, formula in rows.tuples()]
