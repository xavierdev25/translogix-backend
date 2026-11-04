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
    # 1.7.0 (CR-009): RBAC
    ACCESS_DENIED = "access.denied"
    STAFF_CREATE = "staff.create"
    STAFF_ROLES_UPDATE = "staff.roles.update"
    HANDOFF_READ = "handoff.read"
    HANDOFF_UPDATE = "handoff.update"
    RAG_REINDEX = "rag.reindex"
    AUDIT_READ = "audit.read"
    DANGEROUS_GOODS_READ = "dangerous_goods.read"
    DANGEROUS_GOODS_REVIEW = "dangerous_goods.review"
    # 2.0.0 (CR-010)
    ATTACHMENT_UPLOAD = "attachment.upload"
    CHAT_HISTORY_READ = "chat.history.read"
    # 2.1.0 (CR-011)
    ATTACHMENT_DOWNLOAD = "attachment.download"
    ATTACHMENT_LINK = "attachment.link"
    RAG_REINDEX_QUEUED = "rag.reindex.queued"


@dataclass(frozen=True)
class ApiAuditEvent:
    action: AuditAction
    http_status: int
    request_id: str
    created_at: datetime
    client_id: UUID | None = None
    api_key_id: UUID | None = None
    staff_user_id: UUID | None = None  # 1.4.0: internal actor (CR-002)
    resource_type: str | None = None
    resource_id: str | None = None
