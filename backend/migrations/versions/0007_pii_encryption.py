"""Authenticated encryption of personal data at rest (PII-01, CR-010).

- ``shipment_recipients``: ``nombre``, ``numero_documento``, ``telefono``, ``email`` and
  ``direccion`` become TEXT (the ciphertext is longer) and are encrypted.
- ``messages.content_json`` becomes ``{"enc": "enc:v1:…"}``.

**Key:** ``PII_ENCRYPTION_KEYS`` from the environment or ``.env``. If there are rows to encrypt and
no key, the migration **fails**: it never leaves plaintext marked as encrypted.

**Downgrade:** decrypts with the same key and restores the original column lengths. It is
reversible while the key is still available.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04
"""

import json
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREFIX = "enc:v1:"
RECIPIENT_COLUMNS = {
    "nombre": 120,
    "numero_documento": 20,
    "telefono": 16,
    "email": 120,
    "direccion": 200,
}
NULLABLE = {"numero_documento", "email"}

recipients = sa.table(
    "shipment_recipients",
    sa.column("id", sa.Uuid()),
    *(sa.column(c, sa.Text()) for c in RECIPIENT_COLUMNS),
)
messages = sa.table(
    "messages", sa.column("id", sa.Uuid()), sa.column("content_json", postgresql.JSONB())
)


def _cipher():  # type: ignore[no-untyped-def]
    from app.shared.crypto import parse_keys

    raw = os.environ.get("PII_ENCRYPTION_KEYS")
    if not raw:
        from app.shared.config import Settings

        secret = Settings().pii_encryption_keys  # type: ignore[call-arg]
        raw = secret.get_secret_value() if secret else None
    if not raw:
        raise RuntimeError(
            "Falta PII_ENCRYPTION_KEYS: hay datos personales que cifrar y no hay clave. "
            "No se migra para no dejar datos en claro."
        )
    return parse_keys(raw)


def _has_rows() -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM shipment_recipients)")).scalar()
        or bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM messages)")).scalar()
    )


def upgrade() -> None:
    for column in RECIPIENT_COLUMNS:
        op.alter_column(
            "shipment_recipients", column, type_=sa.Text(), existing_nullable=column in NULLABLE
        )
    if not _has_rows():
        return
    cipher = _cipher()
    bind = op.get_bind()

    def enc(value: str | None) -> str | None:
        if value is None or value.startswith(PREFIX):
            return value
        return PREFIX + cipher.encrypt(value.encode()).decode()

    for row in bind.execute(sa.select(recipients)).mappings().all():
        values = {c: enc(row[c]) for c in RECIPIENT_COLUMNS}
        bind.execute(recipients.update().where(recipients.c.id == row["id"]).values(**values))
    for row in bind.execute(sa.select(messages.c.id, messages.c.content_json)).all():
        if isinstance(row.content_json, dict) and set(row.content_json) == {"enc"}:
            continue
        plain = json.dumps(row.content_json, ensure_ascii=False, separators=(",", ":"))
        bind.execute(
            messages.update()
            .where(messages.c.id == row.id)
            .values(content_json={"enc": enc(plain)})
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_rows():
        cipher = _cipher()

        def dec(value: str | None) -> str | None:
            if value is None or not value.startswith(PREFIX):
                return value
            return cipher.decrypt(value[len(PREFIX) :].encode()).decode()

        for row in bind.execute(sa.select(recipients)).mappings().all():
            values = {c: dec(row[c]) for c in RECIPIENT_COLUMNS}
            bind.execute(recipients.update().where(recipients.c.id == row["id"]).values(**values))
        for row in bind.execute(sa.select(messages.c.id, messages.c.content_json)).all():
            if isinstance(row.content_json, dict) and set(row.content_json) == {"enc"}:
                plain = json.loads(dec(row.content_json["enc"]) or "null")
                bind.execute(
                    messages.update().where(messages.c.id == row.id).values(content_json=plain)
                )
    for column, length in RECIPIENT_COLUMNS.items():
        op.alter_column(
            "shipment_recipients",
            column,
            type_=sa.String(length=length),
            existing_nullable=column in NULLABLE,
        )
