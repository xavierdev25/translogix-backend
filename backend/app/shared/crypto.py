"""Authenticated encryption of personal data at rest (CR-010, PII-01).

Fernet (AES-128-CBC + HMAC-SHA256, authenticated) through ``MultiFernet``, so keys rotate:
the first key encrypts, every key decrypts.
- **Keys:** they come only from ``PII_ENCRYPTION_KEYS`` (environment or secret manager); none
  is written in the code or the repository.
- **Stored form:** ``enc:v1:<token>``.
- **Plaintext:** reading a value without that prefix is an error. The data migration encrypts
  every existing row, so plaintext means a bug, not a legacy value to be tolerated.

The SQLAlchemy types read the process-wide cipher configured at start-up (``configure``). It
cannot be per request: the column types are built at import time.
"""

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import JSON, TypeDecorator

from app.contracts.common import ContractError, ErrorCode

PREFIX = "enc:v1:"
_cipher: MultiFernet | None = None


class PiiKeyError(RuntimeError):
    """Missing or invalid keys: the app must not start (and must not store plaintext)."""


def parse_keys(raw: str) -> MultiFernet:
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise PiiKeyError("PII_ENCRYPTION_KEYS está vacío")
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError) as error:
        raise PiiKeyError("PII_ENCRYPTION_KEYS contiene una clave Fernet inválida") from error


def configure(raw_keys: str | None) -> None:
    global _cipher
    if not raw_keys:
        raise PiiKeyError(
            "Falta PII_ENCRYPTION_KEYS: sin clave no se guardan datos personales (PII-01)."
        )
    _cipher = parse_keys(raw_keys)


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _active() -> MultiFernet:
    if _cipher is None:
        raise PiiKeyError("El cifrado de PII no está configurado (PII_ENCRYPTION_KEYS).")
    return _cipher


def encrypt(value: str) -> str:
    return PREFIX + _active().encrypt(value.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored.startswith(PREFIX):
        raise ContractError(ErrorCode.ERROR_INTERNO, details={"motivo": "pii_sin_cifrar"})
    try:
        return _active().decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        # Wrong key or tampered value: never fall back to returning the raw bytes.
        raise ContractError(
            ErrorCode.ERROR_INTERNO, details={"motivo": "pii_no_descifrable"}
        ) from None


def rotate(stored: str) -> str:
    """Re-encrypts with the first (active) key; used after adding a new key."""
    return PREFIX + _active().rotate(stored[len(PREFIX) :].encode()).decode()


class EncryptedText(TypeDecorator[str]):
    """A text column stored encrypted. ``None`` stays ``None`` (no secret to protect)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else decrypt(value)


class EncryptedJson(TypeDecorator[Any]):
    """A JSON document stored as ``{"enc": "enc:v1:…"}``; the code keeps reading plain dicts."""

    impl = JSON().with_variant(JSONB(), "postgresql")
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return {"enc": encrypt(json.dumps(value, ensure_ascii=False, separators=(",", ":")))}

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"enc"}:
            raise ContractError(ErrorCode.ERROR_INTERNO, details={"motivo": "pii_sin_cifrar"})
        return json.loads(decrypt(value["enc"]))
