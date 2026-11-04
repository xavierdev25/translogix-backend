"""Retention and erasure of personal data (Ley 1581 de 2012).

**The retention period is a legal decision that is still pending.** Nothing runs on a schedule;
these operations run on demand (``python -m app.privacy.cli``) once the period is approved:
- ``purge_conversations``: deletes web/WhatsApp/PBX conversations older than N days, with their
  messages and conversational audit. A conversation with an open case (handoff PENDING or
  ASSIGNED) is kept.
- ``erase_recipient``: the data subject's erasure request. It replaces the recipient's data with
  a tombstone and keeps the shipment, whose operational and billing record stays.
- ``rotate_keys``: re-encrypts with the first key of ``PII_ENCRYPTION_KEYS``, after a new one is
  added in front.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEventRow, ConversationRow, HandoffRow, MessageRow
from app.shipments.infrastructure.tables import ShipmentRecipientRow

ERASED = "[ELIMINADO]"
OPEN_HANDOFF = ("PENDING", "ASSIGNED")


@dataclass(frozen=True)
class PurgeReport:
    conversaciones: int
    mensajes: int
    ejecutado: bool


async def purge_conversations(
    session: AsyncSession, *, older_than_days: int, now: datetime, execute: bool
) -> PurgeReport:
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    cutoff = now - timedelta(days=older_than_days)
    open_cases = select(HandoffRow.session_id).where(HandoffRow.status.in_(OPEN_HANDOFF))
    stale = select(ConversationRow.session_id).where(
        ConversationRow.updated_at < cutoff, ConversationRow.session_id.not_in(open_cases)
    )
    ids = list(await session.scalars(stale))
    messages = (
        await session.scalar(
            select(text("count(*)")).select_from(MessageRow).where(MessageRow.session_id.in_(ids))
        )
        if ids
        else 0
    )
    if execute and ids:
        await session.execute(delete(MessageRow).where(MessageRow.session_id.in_(ids)))
        await session.execute(delete(AuditEventRow).where(AuditEventRow.session_id.in_(ids)))
        await session.execute(delete(HandoffRow).where(HandoffRow.session_id.in_(ids)))
        await session.execute(delete(ConversationRow).where(ConversationRow.session_id.in_(ids)))
        await session.commit()
    return PurgeReport(conversaciones=len(ids), mensajes=int(messages or 0), ejecutado=execute)


async def erase_recipient(session: AsyncSession, shipment_id: UUID, now: datetime) -> bool:
    row = await session.scalar(
        select(ShipmentRecipientRow).where(ShipmentRecipientRow.shipment_id == shipment_id)
    )
    if row is None:
        return False
    row.nombre = row.telefono = row.direccion = ERASED
    row.numero_documento = row.email = None
    row.updated_at = now
    await session.commit()
    return True


async def rotate_keys(session: AsyncSession) -> int:
    """Reads and rewrites every encrypted row: the ORM types decrypt with any key and encrypt
    with the first one."""
    count = 0
    for recipient in (await session.scalars(select(ShipmentRecipientRow))).all():
        for field in ("nombre", "numero_documento", "telefono", "email", "direccion"):
            setattr(recipient, field, getattr(recipient, field))
        count += 1
    for message in (await session.scalars(select(MessageRow))).all():
        message.content_json = dict(message.content_json)
        count += 1
    await session.commit()
    return count
