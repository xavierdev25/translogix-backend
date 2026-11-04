"""Internal users (roles ADMIN and REVIEWER, CR-009) and their personal tokens.

A staff token is ``tlxs_<prefix>_<secret>`` (``app.shared.tokens``), always has an
expiry and belongs to exactly one person, so every change can name who made it.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.contracts.staff import Role
from app.shared.tokens import hash_token, new_token, prefix_of, token_pattern

TOKEN_KIND = "tlxs"  # noqa: S105 - credential family prefix, not a secret
TOKEN_PATTERN = token_pattern(TOKEN_KIND)
TOKEN_MAX_DAYS = 365
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StaffStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class StaffUser:
    id: UUID
    email: str
    nombre: str
    roles: frozenset[Role]
    estado: StaffStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StaffTokenRecord:
    id: UUID
    staff_user_id: UUID
    key_prefix: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class StaffContext:
    """Who is acting: the person behind the token, with the roles they hold right now."""

    staff_user_id: UUID
    token_id: UUID
    roles: frozenset[Role]


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if not _EMAIL.match(email):
        raise ValueError("correo inválido")
    return email


def new_staff_token() -> tuple[str, str]:
    """(prefix, full token). The full token is shown once and never stored."""
    return new_token(TOKEN_KIND)


def staff_token_prefix(full_token: str) -> str | None:
    return prefix_of(full_token, TOKEN_PATTERN)


def hash_staff_token(full_token: str) -> str:
    return hash_token(full_token)
