"""RBAC (CR-009, 1.7.0): caller identity, staff administration, audit, dangerous goods review.

The role is resolved from the credential only:
- ``tlx_…`` API key → ``CLIENT``;
- ``tlxs_…`` staff token → the user's roles;
- legacy ``X-Admin-Token`` → ``ADMIN``.

A role sent in a header or body is never read.
"""

from enum import StrEnum

from pydantic import UUID4, Field

from app.contracts.common import BogotaDatetime, ContractModel, NumeroGuia
from app.contracts.shipment_batches import ApiScope
from app.contracts.staff import Role


class AuthMethod(StrEnum):
    API_KEY = "API_KEY"
    STAFF_TOKEN = "STAFF_TOKEN"  # noqa: S105 - method name, not a secret
    ADMIN_TOKEN = "ADMIN_TOKEN"  # noqa: S105 - legacy shared X-Admin-Token, kept for compatibility


class WhoAmI(ContractModel):
    """``GET /api/v1/auth/me``: who the credential belongs to."""

    roles: list[Role] = Field(min_length=1)
    metodo: AuthMethod
    staff_user_id: UUID4 | None = None
    client_id: UUID4 | None = None
    scopes: list[ApiScope] = Field(default_factory=list)


class StaffUserCreateRequest(ContractModel):
    email: str = Field(min_length=3, max_length=120)
    nombre: str = Field(min_length=1, max_length=120)
    roles: list[Role] = Field(min_length=1, description="Solo ADMIN o REVIEWER.")


class StaffRolesUpdateRequest(ContractModel):
    roles: list[Role] = Field(min_length=1, description="Solo ADMIN o REVIEWER.")


class StaffUserResponse(ContractModel):
    id: UUID4
    email: str
    nombre: str
    roles: list[Role]
    estado: str
    created_at: BogotaDatetime
    updated_at: BogotaDatetime


class AuditEventItem(ContractModel):
    """Row of ``api_audit_events``. Identifiers only: no PII, no credential."""

    id: UUID4
    action: str
    http_status: int
    request_id: str
    client_id: UUID4 | None
    staff_user_id: UUID4 | None
    resource_type: str | None
    resource_id: str | None
    created_at: BogotaDatetime


class AuditEventPage(ContractModel):
    items: list[AuditEventItem]


class ReviewDecision(StrEnum):
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"


class DangerousGoodsReviewRequest(ContractModel):
    decision: ReviewDecision
    observaciones: str = Field(min_length=5, max_length=2000)


class DangerousGoodsReview(ContractModel):
    shipment_id: UUID4
    decision: ReviewDecision
    observaciones: str
    reviewer_staff_user_id: UUID4
    created_at: BogotaDatetime


class DangerousGoodsItem(ContractModel):
    """A shipment declared as dangerous goods, without recipient data."""

    shipment_id: UUID4
    numero_guia: NumeroGuia
    codigo_trayecto: str
    created_at: BogotaDatetime
    revision: DangerousGoodsReview | None = Field(
        description="null: pendiente de revisión especializada."
    )


class DangerousGoodsList(ContractModel):
    items: list[DangerousGoodsItem]
