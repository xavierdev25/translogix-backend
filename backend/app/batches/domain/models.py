"""Batch rules that do not need the database: per-item parsing, duplicates, hashing, cursors.

Each item is validated on its own, so one malformed item never rejects the batch.
Errors carry the code and the field path, never the submitted value (it may be PII).
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.contracts.common import ErrorCode
from app.contracts.shipment_batches import REFERENCE_PATTERN, BatchStatus, EnvioLoteItem, ItemError

FINAL_STATUSES = frozenset({BatchStatus.COMPLETED, BatchStatus.COMPLETED_WITH_ERRORS})
_REFERENCE = re.compile(REFERENCE_PATTERN)


@dataclass
class ParsedItem:
    linea: int
    referencia_cliente: str | None
    item: EnvioLoteItem | None
    errors: list[ItemError] = field(default_factory=list)


def _error_fields(error: ValidationError) -> list[ItemError]:
    seen: dict[str, ItemError] = {}
    for detail in error.errors(include_input=False, include_url=False):
        path = ".".join(str(part) for part in detail["loc"]) or None
        seen.setdefault(path or "", ItemError(code=ErrorCode.VALIDACION, field=path))
    return list(seen.values())


def parse_items(raw_items: list[Any]) -> list[ParsedItem]:
    parsed = []
    for linea, raw in enumerate(raw_items, start=1):
        reference = raw.get("referencia_cliente") if isinstance(raw, dict) else None
        reference = (
            reference if isinstance(reference, str) and _REFERENCE.match(reference) else None
        )
        try:
            item = EnvioLoteItem.model_validate(raw)
        except ValidationError as error:
            parsed.append(ParsedItem(linea, reference, None, _error_fields(error)))
        else:
            parsed.append(ParsedItem(linea, item.referencia_cliente, item))
    return parsed


def flag_duplicate_references(items: list[ParsedItem]) -> None:
    """Every repetition of a reference after its first line is rejected; the rest go on."""
    seen: set[str] = set()
    for parsed in items:
        if parsed.referencia_cliente is None:
            continue
        if parsed.referencia_cliente in seen:
            parsed.item = None
            parsed.errors = [
                ItemError(code=ErrorCode.REFERENCIA_DUPLICADA, field="referencia_cliente")
            ]
        seen.add(parsed.referencia_cliente)


def canonical_hash(referencia_lote: str, items: list[ParsedItem], raw_items: list[Any]) -> str:
    """SHA-256 of the request in canonical form: valid items as the contract normalises them."""
    canonical = {
        "referencia_lote": referencia_lote,
        "envios": [
            parsed.item.model_dump(mode="json") if parsed.item is not None else raw
            for parsed, raw in zip(items, raw_items, strict=True)
        ],
    }
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def encode_cursor(linea: int) -> str:
    return base64.urlsafe_b64encode(f"l:{linea}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    """Line after which the next page starts; ``ValueError`` if the cursor is not ours."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        text = base64.urlsafe_b64decode(padded.encode()).decode()
    except ValueError:  # binascii.Error and UnicodeDecodeError
        raise ValueError("cursor inválido") from None
    if not text.startswith("l:") or not text[2:].isdigit():
        raise ValueError("cursor inválido")
    return int(text[2:])
