"""Copying the attachments of ``var/attachments`` to S3, verified and resumable (CR-011).

Three steps, each one re-runnable:
1. ``copy``: for every row still on ``local``:
   - read the file and check its SHA-256 and size against the row (the source must be intact);
   - upload it under the server-generated key (conditional write: retrying never duplicates);
   - compare size and hash in S3;
   - only then point the row to S3.

   Each attempt is recorded in ``attachment_storage_migrations``.
2. ``verify``: downloads every COPIED object and recomputes the hash → VERIFIED. If it fails,
   the row goes back to its local original, which still exists.
3. ``cleanup``: deletes the local originals, only those VERIFIED and only with explicit
   confirmation.

Nothing is deleted or overwritten before step 3.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AttachmentRow, AttachmentStorageMigrationRow
from app.shared import metrics
from app.shared.storage import ObjectStorage, StorageError, object_key, sha256_hex


@dataclass
class MigrationReport:
    procesados: int = 0
    copiados: int = 0
    verificados: int = 0
    fallidos: int = 0
    eliminados_locales: int = 0
    errores: dict[str, int] = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.fallidos += 1
        self.errores[reason] = self.errores.get(reason, 0) + 1


def _extension(row: AttachmentRow) -> str:
    return "pdf" if row.mime_type == "application/pdf" else "jpg"


async def _record(
    session: AsyncSession, row: AttachmentRow, *, key: str, now: datetime
) -> AttachmentStorageMigrationRow:
    found = await session.get(AttachmentStorageMigrationRow, row.attachment_id)
    if found is None:
        found = AttachmentStorageMigrationRow(
            attachment_id=row.attachment_id,
            target_backend="s3",
            target_key=key,
            source_ref=row.storage_ref,
            status="PENDING",
            attempts=0,
            updated_at=now,
        )
        session.add(found)
    found.attempts += 1
    found.updated_at = now
    return found


async def copy(
    session: AsyncSession,
    source: ObjectStorage,
    target: ObjectStorage,
    *,
    now: datetime,
    limit: int = 500,
    dry_run: bool = False,
) -> MigrationReport:
    report = MigrationReport()
    rows = (
        await session.scalars(
            select(AttachmentRow)
            .where(AttachmentRow.storage_backend == source.backend)
            .order_by(AttachmentRow.created_at)
            .limit(limit)
        )
    ).all()
    for row in rows:
        report.procesados += 1
        key = object_key(row.attachment_id, _extension(row), row.created_at)
        if dry_run:
            continue
        record = await _record(session, row, key=key, now=now)
        reason = None
        try:
            data = await source.get(row.storage_ref)
            if len(data) != row.size_bytes or sha256_hex(data) != row.sha256:
                reason = "SOURCE_HASH_MISMATCH"
            else:
                await target.put(key, data, content_type=row.mime_type, sha256=row.sha256)
                info = await target.head(key)
                if info is None or info.size != row.size_bytes or info.sha256 != row.sha256:
                    reason = "TARGET_MISMATCH"
        except StorageError as error:
            reason = "SOURCE_MISSING" if "no encontrado" in str(error) else "STORAGE_ERROR"
        if reason is None:
            record.status, record.last_error = "COPIED", None
            row.storage_backend, row.storage_ref = target.backend, key
            report.copiados += 1
        else:
            record.status, record.last_error = "FAILED", reason
            report.fail(reason)
        await session.commit()  # one attachment at a time: a crash never loses finished work
        metrics.emit("AttachmentMigrations", 1, outcome="ok" if reason is None else reason)
    return report


async def verify(
    session: AsyncSession, source: ObjectStorage, target: ObjectStorage, *, now: datetime
) -> MigrationReport:
    report = MigrationReport()
    records = (
        await session.scalars(
            select(AttachmentStorageMigrationRow).where(
                AttachmentStorageMigrationRow.status == "COPIED"
            )
        )
    ).all()
    for record in records:
        report.procesados += 1
        row = await session.get_one(AttachmentRow, record.attachment_id)
        try:
            ok = sha256_hex(await target.get(record.target_key)) == row.sha256
        except StorageError:
            ok = False
        record.updated_at = now
        if ok:
            record.status = "VERIFIED"
            report.verificados += 1
        else:
            # The local original was not touched: the row goes back to it.
            record.status, record.last_error = "FAILED", "VERIFY_MISMATCH"
            row.storage_backend, row.storage_ref = source.backend, record.source_ref
            report.fail("VERIFY_MISMATCH")
        await session.commit()
    return report


async def cleanup(
    session: AsyncSession, source: ObjectStorage, *, confirm: bool
) -> MigrationReport:
    """Deletes the local originals of VERIFIED copies only. Without ``confirm`` it only counts."""
    report = MigrationReport()
    records = (
        await session.scalars(
            select(AttachmentStorageMigrationRow).where(
                AttachmentStorageMigrationRow.status == "VERIFIED"
            )
        )
    ).all()
    for record in records:
        report.procesados += 1
        if confirm and await source.head(record.source_ref) is not None:
            await source.delete(record.source_ref)
            report.eliminados_locales += 1
    return report
