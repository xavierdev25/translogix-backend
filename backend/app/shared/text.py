"""Spanish (es-CO) text helpers used by several modules. Deterministic, no I/O."""

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def fold(text: str) -> str:
    """Lower-case and strip accents: ``"Bogotá D.C."`` → ``"bogota d.c."``."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    """Whole words of the folded text (``"sic"`` never matches inside ``"básico"``)."""
    return re.findall(r"[a-z0-9ñ]+", fold(text))


_NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"


def parse_number_es(text: str) -> Decimal | None:
    """``"45,5"`` → 45.5, ``"1.450"`` → 1450, ``"3.2"`` → 3.2. ``None`` if not a number."""
    value = text.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", value):  # thousands with dots
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", ".")
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


_COP = re.compile(
    rf"\$?\s*(?P<num>{_NUMBER})\s*(?P<unit>millones|millon|millón|mil|m)?\b(?:\s*(?:cop|pesos))?",
    re.IGNORECASE,
)


def parse_cop(text: str) -> int | None:
    """Money in free text: ``"12.500.000"``, ``"$ 450 mil"``, ``"12,5 millones"`` → int COP."""
    match = _COP.search(text)
    if not match:
        return None
    number = parse_number_es(match.group("num"))
    if number is None:
        return None
    unit = fold(match.group("unit") or "")
    if unit in ("millones", "millon"):
        number *= 1_000_000
    elif unit in ("mil", "m"):
        number *= 1_000
    return int(number)


MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def format_date_es(moment: datetime | date) -> str:
    """``3 de noviembre de 2026``."""
    return f"{moment.day} de {MONTHS_ES[moment.month - 1]} de {moment.year}"


def format_time_es(moment: datetime) -> str:
    """``4:00 p. m.`` (12 h, es-CO)."""
    hour = moment.hour % 12 or 12
    suffix = "a. m." if moment.hour < 12 else "p. m."
    return f"{hour}:{moment.minute:02d} {suffix}"


def format_cop(amount: int) -> str:
    """``$190.975`` (Colombian thousands separator)."""
    return "$" + f"{amount:,}".replace(",", ".")
