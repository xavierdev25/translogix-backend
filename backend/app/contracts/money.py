"""Money convention (DEC-Q8): compute with ``Decimal``, round each line half-up to whole COP."""

from decimal import ROUND_HALF_UP, Decimal


def to_cop(amount: Decimal) -> int:
    """Round a computed line to an integer COP amount (``16493.75 -> 16494``).

    Floats are rejected on purpose: binary floating point makes half-up
    rounding of values such as ``x.5`` unpredictable.
    """
    if not isinstance(amount, Decimal):
        raise TypeError(f"to_cop expects Decimal, got {type(amount).__name__}")
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
