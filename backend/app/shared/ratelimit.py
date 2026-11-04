"""Rate limit shared between instances, on PostgreSQL (CR-010, OPS-01).

Fixed one-minute window per key:
- a single statement ``INSERT … ON CONFLICT DO UPDATE … RETURNING hits`` counts the hit;
- one ``DELETE`` drops that key's previous windows, so the table holds about one row per key
  active in the last minute.

Redis is not added: PostgreSQL is already a dependency and one round trip (~1 ms) is enough
for this traffic.

The limiter runs on **its own small pool**: a chat turn already holds a connection, and taking a
second one from the same pool is the self-blocking found in REL-01. If PostgreSQL does not answer
within ``timeout`` seconds, or fails, the limiter **degrades to the per-process limiter**
(``SlidingWindowRateLimiter``) and logs a warning. It neither lets everything through nor blocks
the service.

Trade-off of the fixed window: at the edge between two windows up to twice the limit can pass in
a few seconds.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.common import ContractError, ErrorCode
from app.shared.http import SlidingWindowRateLimiter

logger = logging.getLogger("app.ratelimit")

_HIT = text(
    """
    INSERT INTO rate_limit_counters (key, window_start, hits)
    VALUES (:key, :window, 1)
    ON CONFLICT (key, window_start) DO UPDATE SET hits = rate_limit_counters.hits + 1
    RETURNING hits
    """
)
_PRUNE = text("DELETE FROM rate_limit_counters WHERE key = :key AND window_start < :window")


class RateLimiter(Protocol):
    async def check(self, key: str) -> None: ...


class LocalRateLimiter:
    """Per-process limiter behind the async interface (``RATE_LIMIT_BACKEND=memory``)."""

    def __init__(self, per_minute: int) -> None:
        self._local = SlidingWindowRateLimiter(per_minute)

    async def check(self, key: str) -> None:
        self._local.check(key)


class PostgresRateLimiter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        per_minute: int,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._limit = per_minute
        self._now = now
        self._fallback = SlidingWindowRateLimiter(per_minute)

    async def check(self, key: str) -> None:
        if self._limit <= 0:
            return
        window = self._now().replace(second=0, microsecond=0)
        try:
            async with self._sessions() as session:
                hits = (
                    await session.execute(_HIT, {"key": key[:128], "window": window})
                ).scalar_one()
                await session.execute(_PRUNE, {"key": key[:128], "window": window})
                await session.commit()
        except Exception as error:  # the shared store failed: degrade, do not fail open
            logger.warning("rate limiter degraded to per-process: %s", type(error).__name__)
            self._fallback.check(key)
            return
        if hits > self._limit:
            raise ContractError(ErrorCode.LIMITE_DE_TASA, details={"por_minuto": self._limit})
