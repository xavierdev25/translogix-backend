"""PII rules shared by every module.

* ``find_pii`` backs the client-safe contract models (DEC-S1): a model that
  would expose a phone number, ID document, NIT, plate, GPS position, exact
  address, URL or a person's name cannot be built at all.
* ``hash_external_user_id`` / ``mask_external_user_id`` implement rule 2 of the
  inbound contract. Every adapter must use them so the same person always maps
  to the same hash.
"""

import hashlib
import hmac
import re
from enum import StrEnum

MIN_SALT_LENGTH = 16


class PiiKind(StrEnum):
    PHONE = "PHONE"
    ID_DOCUMENT = "ID_DOCUMENT"
    NIT = "NIT"
    PLATE = "PLATE"
    URL = "URL"
    EMAIL = "EMAIL"
    GPS = "GPS"
    ADDRESS = "ADDRESS"
    PERSON_NAME = "PERSON_NAME"


_I = re.IGNORECASE
_NAME = r"(?:(?:Ing|Dr|Dra|Sr|Sra)\.\s*)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"

_PATTERNS: tuple[tuple[PiiKind, re.Pattern[str]], ...] = (
    (PiiKind.PHONE, re.compile(r"(?<!\d)(?:\+?57[\s.-]?)?3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")),
    (PiiKind.PHONE, re.compile(r"(?<!\d)60[1-8][\s.-]?\d{7}(?!\d)")),
    (
        PiiKind.PHONE,
        re.compile(r"\b(?:tel|tel[ée]fono|cel|celular|m[óo]vil)\b\.?\s*:?\s*\+?\d", _I),
    ),
    (
        PiiKind.ID_DOCUMENT,
        re.compile(r"(?:\bC\.\s?[CE]\.|\bc[ée]dula\b|\bpasaporte\b|\bNUIP\b)[^\d]{0,12}\d", _I),
    ),
    (PiiKind.NIT, re.compile(r"(?<![\d.])\d{3}\.?\d{3}\.?\d{3}-\d(?!\d)")),
    (PiiKind.NIT, re.compile(r"\bNIT\b\.?\s*:?\s*\d", _I)),
    # Case-sensitive: Colombian plates are upper case (cars ABC-123, motorbikes ABC-12D).
    (PiiKind.PLATE, re.compile(r"\b[A-Z]{3}[-\s]?(?:\d{3}|\d{2}[A-Z])\b")),
    (PiiKind.URL, re.compile(r"\bhttps?://\S+|\bwww\.\S+", _I)),
    (PiiKind.EMAIL, re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (PiiKind.GPS, re.compile(r"-?\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}")),
    (
        PiiKind.ADDRESS,
        re.compile(
            r"\b(?:calle|cl|carrera|cra|kr|kra|avenida|av|diagonal|dg|transversal|tv|v[íi]a)\.?\s*"
            r"\d+\s?[a-z]?(?:\s*bis)?\s*(?:#|no\.?|n[°º])\s*\d+\s?[a-z]?\s*-\s*\d+",
            _I,
        ),
    ),
    (PiiKind.ADDRESS, re.compile(r"\b(?:apto|apt|apartamento)\.?\s*\d+", _I)),
    (PiiKind.ADDRESS, re.compile(r"\bkm\.?\s*\d+\s+v[íi]a\b", _I)),
    (
        PiiKind.PERSON_NAME,
        re.compile(rf"\b(?:[Cc]onductora?|[Rr]epartidora?|[Mm]ensajer[oa])\s+{_NAME}"),
    ),
    (PiiKind.PERSON_NAME, re.compile(rf"\b(?:[Ff]irmado|[Rr]ecibido)\s+por\s+{_NAME}")),
)

_PHONE_LIKE = re.compile(r"^[\d\s()+.-]+$")


def find_pii(text: str) -> set[PiiKind]:
    """Kinds of personal data present in ``text`` (empty set when it is safe)."""
    return {kind for kind, pattern in _PATTERNS if pattern.search(text)}


def redact_pii(text: str) -> str:
    """``text`` with every personal datum replaced by ``[PII:<KIND>]`` (for logs)."""
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[PII:{kind}]", text)
    return text


def canonical_external_user_id(value: str) -> str:
    """Comparable form of a channel user id.

    Phone-like ids (WhatsApp ``wa_id``, PBX ANI) keep only their digits, so
    ``+57 310 987 6543`` and ``573109876543`` are the same person; any other id
    (the web client UUID) is trimmed and lower-cased.
    """
    stripped = value.strip()
    if _PHONE_LIKE.match(stripped):
        return re.sub(r"\D", "", stripped)
    return stripped.lower()


def hash_external_user_id(value: str, *, salt: str) -> str:
    """Keyed SHA-256 (HMAC) of the canonical id; ``salt`` is ``PII_HASH_SALT``."""
    if len(salt) < MIN_SALT_LENGTH:
        raise ValueError(f"PII_HASH_SALT must be at least {MIN_SALT_LENGTH} characters")
    canonical = canonical_external_user_id(value).encode()
    return hmac.new(salt.encode(), canonical, hashlib.sha256).hexdigest()


def mask_external_user_id(value: str) -> str:
    """Display form showing only the last four characters (``…4567``)."""
    return "…" + canonical_external_user_id(value)[-4:]
