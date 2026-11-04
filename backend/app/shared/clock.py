from datetime import datetime

from app.contracts.fakes import FixedClock
from app.contracts.ports import Clock
from app.contracts.timezone import BOGOTA


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(BOGOTA)


def build_clock(override: datetime | None) -> Clock:
    """``CLOCK_OVERRIDE`` freezes time for tests and the demo (rule 10)."""
    return FixedClock(override) if override else SystemClock()
