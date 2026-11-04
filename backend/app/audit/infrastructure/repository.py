"""Read side of ``api_audit_events`` for the ADMIN audit query (CR-009)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.infrastructure.tables import ApiAuditEventRow


class ApiAuditReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(self, *, limit: int, action: str | None = None) -> list[ApiAuditEventRow]:
        query = select(ApiAuditEventRow).order_by(ApiAuditEventRow.created_at.desc()).limit(limit)
        if action is not None:
            query = query.where(ApiAuditEventRow.action == action)
        return list(await self._session.scalars(query))
