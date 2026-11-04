"""Guía number normalisation shared by every channel.

Accepts the canonical form (``TLX-984021-CO``), loose typing (``tlx 984021 co``,
``TLX984021CO``) and ASR dictation (``t l x nueve ocho cuatro cero dos uno c o``,
also with letter names such as ``te ele equis``). Always returns the canonical
form or ``None``.
"""

import re
import unicodedata

NUMERO_GUIA_PATTERN = r"^TLX-\d{6}-CO$"

_SPOKEN_DIGITS = {
    "cero": "0",
    "uno": "1",
    "una": "1",
    "un": "1",
    "dos": "2",
    "tres": "3",
    "cuatro": "4",
    "cinco": "5",
    "seis": "6",
    "siete": "7",
    "ocho": "8",
    "nueve": "9",
}
_SPOKEN_LETTERS = {"te": "t", "ele": "l", "equis": "x", "ce": "c"}
# Separators people say out loud while dictating; they carry no characters.
_SPOKEN_SEPARATORS = {"guion", "menos", "raya"}

_TOKEN = re.compile(r"[a-z0-9]+")
_COLLAPSED_GUIA = re.compile(r"tlx(\d{6})co")


def _collapse(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    pieces = []
    for token in _TOKEN.findall(ascii_text):
        if token in _SPOKEN_SEPARATORS:
            continue
        pieces.append(_SPOKEN_DIGITS.get(token) or _SPOKEN_LETTERS.get(token) or token)
    return "".join(pieces)


def _canonical(digits: str) -> str:
    return f"TLX-{digits}-CO"


def normalize_numero_guia(value: str) -> str | None:
    """Canonical guía if the whole input is a guía in any accepted spelling."""
    match = _COLLAPSED_GUIA.fullmatch(_collapse(value))
    return _canonical(match.group(1)) if match else None


def extract_numero_guia(text: str) -> str | None:
    """First guía mentioned anywhere in free text (chat message or transcript)."""
    match = _COLLAPSED_GUIA.search(_collapse(text))
    return _canonical(match.group(1)) if match else None
