"""Outbox relay: publishes pending messages to SQS and marks them published.

``FOR UPDATE SKIP LOCKED`` lets several relays (one per worker) share the work without
publishing the same row twice at the same time. A publish that fails stays pending with its
attempt counted, and is retried on the next pass.

If the process dies between ``send`` and the commit, the message is published twice: the
consumer tolerates it (idempotency by ``job_id``).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import OutboxMessageRow
from app.jobs.publisher import MessagePublisher, PublishError
from app.shared import metrics

logger = logging.getLogger("app.outbox")


class OutboxRelay:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], publisher: MessagePublisher, clock: object
    ) -> None:
        self._sessions, self._publisher, self._clock = sessions, publisher, clock

    async def publish_pending(self, limit: int = 10) -> int:
        published = 0
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(OutboxMessageRow)
                    .where(OutboxMessageRow.published_at.is_(None))
                    .order_by(OutboxMessageRow.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                try:
                    await self._publisher.publish(str(row.id), row.payload_json)
                except PublishError as error:
                    row.attempts += 1
                    row.last_error = str(error)[:300]
                    logger.warning(
                        "outbox publish failed",
                        extra={"topic": row.topic, "attempts": row.attempts},
                    )
                    continue
                row.published_at = self._clock.now()  # type: ignore[attr-defined]
                published += 1
            await session.commit()
        if published:
            metrics.emit("OutboxPublished", published, topic="documents.reindex")
        return published
