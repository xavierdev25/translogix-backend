"""Enterprise clients and their API keys (B2B integrations).

An API key has the form ``tlx_<prefix>_<secret>`` (see ``app.shared.tokens``): the
prefix is a public identifier used to find the key, the secret never leaves the
response that created it, and only its SHA-256 is stored.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.contracts.shipment_batches import ApiScope
from app.shared.tokens import hash_token, new_token, prefix_of, token_pattern

KEY_PATTERN = token_pattern("tlx")
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
    return new_token("tlx")


def key_prefix_of(full_key: str) -> str | None:
    return prefix_of(full_key, KEY_PATTERN)


def hash_api_key(full_key: str) -> str:
    return hash_token(full_key)
