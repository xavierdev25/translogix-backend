import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.domain.models import ApiAuditEvent
from app.audit.infrastructure.tables import ApiAuditEventRow

logger = logging.getLogger("app.audit")


class ApiAuditService:
    """Writes in its own transaction, so a rolled-back operation is still audited."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, event: ApiAuditEvent) -> None:
        try:
            async with self._sessions() as session:
                session.add(
                    ApiAuditEventRow(
                        id=uuid4(),
                        client_id=event.client_id,
                        api_key_id=event.api_key_id,
                        action=event.action.value,
                        resource_type=event.resource_type,
                        resource_id=event.resource_id,
                        request_id=event.request_id,
                        http_status=event.http_status,
                        created_at=event.created_at,
                    )
                )
                await session.commit()
        except Exception as error:  # auditing must not break the operation; logged, not hidden
            logger.error("audit write failed", extra={"error_type": type(error).__name__})
