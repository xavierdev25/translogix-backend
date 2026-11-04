"""Batch use cases: submit (idempotent, partial), status and paginated results.

Transactions:
1. Header and every line are committed as PENDING before any processing.
2. Each item runs in its own transaction: guía, quote snapshot, shipment, recipient and
   the item status commit together or not at all.
3. The lease (``locked_until``) is renewed per item. If the process stops, the PENDING
   items stay pending; an identical replay after the lease expires resumes them and
   never recreates CREATED or REJECTED lines. P0 has no worker (spec §6.5).
"""

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.batches.domain.models import (
    FINAL_STATUSES,
    ParsedItem,
    canonical_hash,
    decode_cursor,
    encode_cursor,
    flag_duplicate_references,
    parse_items,
)
from app.batches.infrastructure.repositories import BatchRepository, ResultRow
from app.batches.infrastructure.tables import ShipmentBatchRow
from app.clients.domain.models import ClientContext
from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import Clock
from app.contracts.shipment_batches import (
    BatchItemStatus,
    BatchStatus,
    ItemError,
    ShipmentBatchItemResult,
    ShipmentBatchItemsPage,
    ShipmentBatchResponse,
    ShipmentBatchSummary,
)
from app.db.repositories import UNIQUE_VIOLATION
from app.shipments.application.service import ShipmentService

logger = logging.getLogger("app.batches")

DUPLICATE = ItemError(code=ErrorCode.REFERENCIA_DUPLICADA, field="referencia_cliente")
INTERNAL = ItemError(code=ErrorCode.ERROR_INTERNO, field=None)


class BatchService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        shipments: ShipmentService,
        clock: Clock,
        *,
        lease_seconds: int,
    ) -> None:
        self._sessions = sessions
        self._shipments = shipments
        self._clock = clock
        self._lease = timedelta(seconds=lease_seconds)

    async def submit(
        self, context: ClientContext, referencia_lote: str, raw_items: list[Any]
    ) -> tuple[ShipmentBatchResponse, int]:
        """(final state, HTTP status): 201 new · 200 idempotent replay · 202 still processing."""
        parsed = parse_items(raw_items)
        flag_duplicate_references(parsed)
        digest = canonical_hash(referencia_lote, parsed, raw_items)
        now = self._clock.now()

        async with self._sessions() as session:
            repo = BatchRepository(session)
            batch_id = await repo.insert(
                context.client_id, referencia_lote, digest, len(parsed), now
            )
            created = batch_id is not None
            if batch_id is not None:
                await repo.add_items(
                    batch_id, [(p.linea, p.referencia_cliente) for p in parsed], now
                )
            else:
                # The INSERT waited for any concurrent writer of the same key, so the row exists.
                existing = await repo.get_by_reference(context.client_id, referencia_lote)
                if existing is None:
                    raise ContractError(ErrorCode.ERROR_INTERNO)
                if existing.request_hash != digest:
                    raise ContractError(
                        ErrorCode.IDEMPOTENCIA_CONFLICTO,
                        details={"referencia_lote": referencia_lote},
                    )
                batch_id = existing.id
                if BatchStatus(existing.status) in FINAL_STATUSES:
                    return await self._response(context, batch_id), 200
            claimed = await repo.claim(batch_id, now, now + self._lease)
            await session.commit()
        if not claimed:
            return await self._response(context, batch_id), 202

        await self._process(context, batch_id, parsed)
        return await self._response(context, batch_id), 201 if created else 200

    async def summary(self, context: ClientContext, batch_id: UUID) -> ShipmentBatchSummary:
        async with self._sessions() as session:
            repo = BatchRepository(session)
            row = await self._owned(repo, context, batch_id)
            counts = await repo.counts(batch_id)
        return _summary(row, counts)

    async def items(
        self,
        context: ClientContext,
        batch_id: UUID,
        *,
        status: BatchItemStatus | None,
        cursor: str | None,
        limit: int,
    ) -> ShipmentBatchItemsPage:
        try:
            after = decode_cursor(cursor) if cursor else 0
        except ValueError:
            raise ContractError(
                ErrorCode.VALIDACION,
                details={
                    "campos": [
                        {
                            "loc": ["query", "cursor"],
                            "msg": "cursor inválido",
                            "type": "value_error",
                        }
                    ]
                },
            ) from None
        async with self._sessions() as session:
            repo = BatchRepository(session)
            await self._owned(repo, context, batch_id)
            rows = await repo.results(batch_id, after_line=after, status=status, limit=limit + 1)
        page = [_result(row) for row in rows[:limit]]
        next_cursor = encode_cursor(page[-1].linea) if len(rows) > limit else None
        return ShipmentBatchItemsPage(items=page, next_cursor=next_cursor)

    # --- processing ------------------------------------------------------------------

    async def _process(
        self, context: ClientContext, batch_id: UUID, parsed: list[ParsedItem]
    ) -> None:
        async with self._sessions() as session:
            pending = await BatchRepository(session).pending_items(batch_id)
        for item_id, line in pending:
            await self._process_item(context, batch_id, item_id, parsed[line - 1])
        async with self._sessions() as session:
            await BatchRepository(session).finish(batch_id, self._clock.now())
            await session.commit()

    async def _process_item(
        self, context: ClientContext, batch_id: UUID, item_id: UUID, parsed: ParsedItem
    ) -> None:
        if parsed.item is None:
            await self._reject(batch_id, item_id, parsed.errors)
            return
        draft = self._shipments.prepare(context.client_id, parsed.item)
        if isinstance(draft, list):
            await self._reject(batch_id, item_id, draft)
            return
        try:
            async with self._sessions() as session:
                created = await self._shipments.create(session, draft, batch_item_id=item_id)
                repo = BatchRepository(session)
                now = self._clock.now()
                await repo.mark_created(item_id, created.shipment_id, now)
                await repo.renew(batch_id, now + self._lease)
                await session.commit()
        except ContractError as error:
            await self._reject(
                batch_id,
                item_id,
                [
                    DUPLICATE
                    if error.code is ErrorCode.REFERENCIA_DUPLICADA
                    else ItemError(code=error.code, field=None)
                ],
            )
        except IntegrityError as error:
            # A concurrent batch took the same reference first: the UNIQUE constraint decides.
            unique = getattr(error.orig, "sqlstate", None) == UNIQUE_VIOLATION
            await self._reject(batch_id, item_id, [DUPLICATE if unique else INTERNAL])
        except Exception as error:  # one broken item must not stop the batch (spec §6.4)
            logger.error("batch item failed", extra={"error_type": type(error).__name__})
            await self._reject(batch_id, item_id, [INTERNAL])

    async def _reject(self, batch_id: UUID, item_id: UUID, errors: list[ItemError]) -> None:
        async with self._sessions() as session:
            repo = BatchRepository(session)
            now = self._clock.now()
            await repo.mark_rejected(item_id, errors, now)
            await repo.renew(batch_id, now + self._lease)
            await session.commit()

    # --- reading -----------------------------------------------------------------------

    @staticmethod
    async def _owned(
        repo: BatchRepository, context: ClientContext, batch_id: UUID
    ) -> ShipmentBatchRow:
        row = await repo.get(batch_id, context.client_id)
        if row is None:
            raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "shipment_batch"})
        return row

    async def _response(self, context: ClientContext, batch_id: UUID) -> ShipmentBatchResponse:
        async with self._sessions() as session:
            repo = BatchRepository(session)
            row = await self._owned(repo, context, batch_id)
            counts = await repo.counts(batch_id)
            rows = await repo.results(batch_id)
        return ShipmentBatchResponse(
            **_summary(row, counts).model_dump(), resultados=[_result(r) for r in rows]
        )


def _summary(row: ShipmentBatchRow, counts: dict[BatchItemStatus, int]) -> ShipmentBatchSummary:
    return ShipmentBatchSummary(
        lote_id=row.id,
        referencia_lote=row.referencia_lote,
        status=BatchStatus(row.status),
        total=row.total_items,
        aceptados=counts[BatchItemStatus.CREATED],
        rechazados=counts[BatchItemStatus.REJECTED],
        pendientes=counts[BatchItemStatus.PENDING],
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _result(row: ResultRow) -> ShipmentBatchItemResult:
    item, numero_guia, flete, formula = row
    return ShipmentBatchItemResult(
        linea=item.line_number,
        referencia_cliente=item.referencia_cliente,
        status=BatchItemStatus(item.status),
        numero_guia=numero_guia,
        flete_total_cop=flete,
        formula_version=formula,
        errores=[ItemError.model_validate(e) for e in item.errors_json],
    )
