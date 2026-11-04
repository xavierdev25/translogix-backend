"""Audit of B2B operations, separate from the conversational ``audit_events``.

Only identifiers and outcomes are recorded: never API keys, documents, phone
numbers, addresses or request bodies.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AuditAction(StrEnum):
    AUTH_FAILED = "auth.failed"
    BATCH_CREATE = "batch.create"
    BATCH_READ = "batch.read"
    BATCH_ITEMS_READ = "batch.items.read"


@dataclass(frozen=True)
class ApiAuditEvent:
    action: AuditAction
    http_status: int
    request_id: str
    created_at: datetime
    client_id: UUID | None = None
    api_key_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None
