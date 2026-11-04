"""Enterprise clients and their API keys (B2B integrations).

An API key has the form ``tlx_<prefix>_<secret>``: the prefix is a public
identifier used to find the key, the secret never leaves the response that
created it. Only a SHA-256 of the full key is stored; keys are random with
256 bits of entropy, so a slow password hash is not needed.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.contracts.shipment_batches import ApiScope

KEY_PATTERN = re.compile(r"^tlx_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")
_NIT_PATTERN = re.compile(r"^\d{6,12}(-\d)?$")


class ClientStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class EnterpriseClient:
    id: UUID
    nit: str
    razon_social: str
    estado: ClientStatus
    tipo_cuenta: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ApiKeyRecord:
    id: UUID
    client_id: UUID
    key_prefix: str
    scopes: frozenset[ApiScope]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class ClientContext:
    """Who is calling: always taken from the credential, never from the request body."""

    client_id: UUID
    api_key_id: UUID
    scopes: frozenset[ApiScope]


def normalize_nit(raw: str) -> str:
    """``900.123.456-7`` → ``900123456-7``; raises ``ValueError`` if it is not a NIT."""
    nit = re.sub(r"[.\s]", "", raw)
    if not _NIT_PATTERN.match(nit):
        raise ValueError("NIT inválido")
    return nit


def new_api_key() -> tuple[str, str]:
    """(prefix, full key). The full key is shown once and never stored."""
    prefix = secrets.token_hex(6)
    return prefix, f"tlx_{prefix}_{secrets.token_urlsafe(32)}"


def key_prefix_of(full_key: str) -> str | None:
    match = KEY_PATTERN.match(full_key)
    return match.group(1) if match else None


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()
