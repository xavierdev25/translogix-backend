from datetime import datetime
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")


def to_bogota(value: datetime) -> datetime:
    """Same instant expressed in Bogotá time, so serialisation always shows ``-05:00``.

    There is deliberately no ``now()`` here: the current time comes from the
    injectable ``Clock`` port (rule 10 of the common block).
    """
    return value.astimezone(BOGOTA)
