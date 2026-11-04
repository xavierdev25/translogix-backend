"""Client-safe TMS text (DEC-S1).

Known operational phrasings are rewritten or removed as whole clauses (a
surname left alone is not detectable as PII, so partial removal is not safe).
The result is then checked with the contract's PII detector; if anything is
still there, the text is replaced by the fallback: the sanitizer fails closed.
"""

import re

from app.contracts.pii import find_pii

_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    # "Asignado a conductor Juan Carlos Páez (Tel: 310-987-6543)." (P04, literal target)
    (
        re.compile(
            r"Asignad[oa] a (?:conductor|repartidor|mensajero)\b.*?(?:\)\.?|\.(?=\s|$)|$)", re.I
        ),
        "Asignado a un conductor para la ruta de reparto.",
    ),
    # Whoever signed or received: always the last clause of the TMS description.
    (re.compile(r"\s*(?:Firmado|Recibido) por\b.*$", re.I), ""),
    # "... camión refrigerado Placa WKN-482."
    (re.compile(r"\s*Placa\s+[A-Z]{3}[-\s]?\d{2,3}[A-Z]?", re.I), ""),
    (re.compile(r"\s*\((?:tel|cel)[^)]*\)", re.I), ""),
)


def _clean(raw: str) -> str:
    text = raw.strip()
    for pattern, replacement in _REWRITES:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s{2,}", " ", text).strip(" -,")


def sanitize_sentence(raw: str, *, fallback: str) -> str:
    """An event description: rewritten, punctuated, PII-free or the fallback."""
    text = _clean(raw)
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return text if text.strip(".") and not find_pii(text) else fallback


def sanitize_place(raw: str, *, fallback: str) -> str:
    """A place label (hub, reference point): no punctuation added."""
    text = _clean(raw).rstrip(".")
    return text if text and not find_pii(text) else fallback
