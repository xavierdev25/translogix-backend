"""Business days (DEC-P5): weekends and the configured holidays are skipped.

The holiday calendar is never hard-coded: it comes from ``BUSINESS_HOLIDAYS_FILE``
(a JSON list of ``YYYY-MM-DD``). Without it, results say so explicitly.
"""

import json
from collections.abc import Collection
from datetime import date, timedelta
from pathlib import Path

NO_CALENDAR_NOTE = "días hábiles sin considerar festivos (calendario no configurado)"


def load_holidays(path: Path | None) -> frozenset[date]:
    if path is None or not path.exists():
        return frozenset()
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("festivos", []) if isinstance(raw, dict) else raw
    return frozenset(date.fromisoformat(str(item)) for item in items)


def is_business_day(day: date, holidays: Collection[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def add_business_days(start: date, days: int, holidays: Collection[date]) -> date:
    """The ``days``-th business day after ``start`` (``start`` itself does not count)."""
    if days < 0:
        raise ValueError("days must be >= 0")
    current = start
    remaining = days
    while remaining:
        current += timedelta(days=1)
        if is_business_day(current, holidays):
            remaining -= 1
    return current
