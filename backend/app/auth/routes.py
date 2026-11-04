"""RBAC routes (CR-009, 1.7.0). Same paths, operation ids and models as the contract.

| Route | Roles |
|---|---|
| ``GET /api/v1/auth/me`` | ADMIN, CLIENT, REVIEWER |
| ``POST /api/v1/internal/staff-users`` | ADMIN |
| ``PATCH /api/v1/internal/staff-users/{id}/roles`` | ADMIN, never on oneself |
| ``GET /api/v1/internal/audit-events`` | ADMIN |
| ``GET /api/v1/internal/dangerous-goods`` | ADMIN, REVIEWER |
| ``POST /api/v1/internal/shipments/{id}/dangerous-goods-review`` | REVIEWER |
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.exc import IntegrityError

from app.audit.domain.models import AuditAction
from app.audit.infrastructure.repository import ApiAuditReader
from app.auth.dependencies import audit, require
from app.auth.principal import Principal
from app.contracts.api_spec import API_PREFIX
from app.contracts.auth import (
    AuditEventItem,
    AuditEventPage,
    DangerousGoodsItem,
    DangerousGoodsList,
    DangerousGoodsReview,
    DangerousGoodsReviewRequest,
    LoginRequest,
    LoginResponse,
    ReviewDecision,
    StaffRolesUpdateRequest,
    StaffUserCreateRequest,
    StaffUserResponse,
    WhoAmI,
)
from app.contracts.common import ContractError, ErrorCode
from app.contracts.shipment_batches import ReviewStatus
from app.contracts.staff import Role
from app.shipments.infrastructure.repositories import ShipmentReviewRepository
from app.shipments.infrastructure.tables import ShipmentReviewRow
from app.staff.application.services import StaffAdminService
from app.staff.domain.models import StaffUser

router = APIRouter(prefix=API_PREFIX)

Anyone = Annotated[Principal, Depends(require(Role.ADMIN, Role.CLIENT, Role.REVIEWER))]
AdminOnly = Annotated[Principal, Depends(require(Role.ADMIN))]
ReviewerOnly = Annotated[Principal, Depends(require(Role.REVIEWER))]
AdminOrReviewer = Annotated[Principal, Depends(require(Role.ADMIN, Role.REVIEWER))]


def _staff(user: StaffUser) -> StaffUserResponse:
    return StaffUserResponse(
        id=user.id,
        email=user.email,
        nombre=user.nombre,
        roles=sorted(user.roles),
        estado=user.estado.value,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _review(row: ShipmentReviewRow) -> DangerousGoodsReview:
    return DangerousGoodsReview(
        shipment_id=row.shipment_id,
        decision=ReviewDecision(row.decision),
        observaciones=row.observaciones,
        reviewer_staff_user_id=row.reviewer_staff_user_id,
        created_at=row.created_at,
    )


@router.get("/auth/me", tags=["autenticación"], operation_id="getCurrentPrincipal")
async def get_current_principal(principal: Anyone) -> WhoAmI:
    return WhoAmI(
        roles=sorted(principal.roles),
        metodo=principal.method,
        staff_user_id=principal.staff_user_id,
        client_id=principal.client_id,
        scopes=sorted(principal.scopes),
    )


@router.post("/auth/login", tags=["autenticación"], operation_id="login")
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Email + password → short-lived Bearer token (1 day). No prior credential required."""
    box = request.app.state.container
    status = 500
    try:
        async with box.sessions() as session:
            admin = StaffAdminService(session, box.clock)
            record, full_token, user = await admin.login(body.email, body.password)
            await session.commit()
        status = 200
        return LoginResponse(
            token=full_token,
            expires_at=record.expires_at,
            roles=sorted(user.roles),
            staff_user_id=user.id,
            nombre=user.nombre,
        )
    except ContractError as error:
        status = error.http_status
        raise
    finally:
        await audit(
            request,
            None,
            AuditAction.AUTH_FAILED if status == 401 else AuditAction.AUTH_LOGIN,
            status,
            resource_type="auth",
            resource_id=body.email[:64],
        )


@router.post("/auth/logout", tags=["autenticación"], operation_id="logout")
async def logout(principal: Anyone, request: Request) -> dict[str, str]:
    """Revoke the token used in this request. Requires a valid Bearer token."""
    box = request.app.state.container
    status = 500
    try:
        if principal.token_id is None:
            # Legacy X-Admin-Token: nothing to revoke
            status = 200
            return {"message": "ok"}
        async with box.sessions() as session:
            admin = StaffAdminService(session, box.clock)
            await admin.revoke_token(principal.token_id)
            await session.commit()
        status = 200
        return {"message": "ok"}
    except ContractError as error:
        status = error.http_status
        raise
    finally:
        await audit(
            request,
            principal,
            AuditAction.AUTH_LOGOUT,
            status,
            resource_type="auth",
        )


@router.post(
    "/internal/staff-users",
    status_code=201,
    tags=["administración"],
    operation_id="createStaffUser",
)
async def create_staff_user(
    body: StaffUserCreateRequest, principal: AdminOnly, request: Request
) -> StaffUserResponse:
    box = request.app.state.container
    status, user_id = 500, None
    try:
        async with box.sessions() as session:
            user = await StaffAdminService(session, box.clock).create_user(
                body.email, body.nombre, body.roles
            )
            await session.commit()
        status, user_id = 201, user.id
        return _staff(user)
    except ContractError as error:
        status = error.http_status
        raise
    finally:
        await audit(
            request,
            principal,
            AuditAction.STAFF_CREATE,
            status,
            resource_type="staff_user",
            resource_id=user_id,
        )


@router.patch(
    "/internal/staff-users/{user_id}/roles",
    tags=["administración"],
    operation_id="updateStaffUserRoles",
)
async def update_staff_user_roles(
    user_id: UUID, body: StaffRolesUpdateRequest, principal: AdminOnly, request: Request
) -> StaffUserResponse:
    box = request.app.state.container
    status = 500
    try:
        if principal.staff_user_id == user_id:
            raise ContractError(
                ErrorCode.NO_AUTORIZADO,
                "No puedes cambiar tus propios roles.",
                details={"motivo": "propio_rol"},
            )
        async with box.sessions() as session:
            admin = StaffAdminService(session, box.clock)
            if not await admin.set_roles(user_id, body.roles):
                raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "staff_user"})
            await session.commit()
            found = await admin.describe(user_id)
        if found is None:  # deleted concurrently
            raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "staff_user"})
        status = 200
        return _staff(found[0])
    except ContractError as error:
        status = error.http_status
        raise
    finally:
        await audit(
            request,
            principal,
            AuditAction.STAFF_ROLES_UPDATE,
            status,
            resource_type="staff_user",
            resource_id=user_id,
        )


@router.get("/internal/audit-events", tags=["administración"], operation_id="listAuditEvents")
async def list_audit_events(
    principal: AdminOnly,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    action: Annotated[str | None, Query(max_length=32)] = None,
) -> AuditEventPage:
    box = request.app.state.container
    async with box.sessions() as session:
        rows = await ApiAuditReader(session).latest(limit=limit, action=action)
    await audit(request, principal, AuditAction.AUDIT_READ, 200, resource_type="api_audit_events")
    return AuditEventPage(
        items=[
            AuditEventItem(
                id=r.id,
                action=r.action,
                http_status=r.http_status,
                request_id=r.request_id,
                client_id=r.client_id,
                staff_user_id=r.staff_user_id,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


@router.get("/internal/dangerous-goods", tags=["revisión"], operation_id="listDangerousGoods")
async def list_dangerous_goods(
    principal: AdminOrReviewer,
    request: Request,
    estado: Annotated[Literal["PENDIENTE", "REVISADO"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DangerousGoodsList:
    box = request.app.state.container
    reviewed = None if estado is None else estado == "REVISADO"
    async with box.sessions() as session:
        rows = await ShipmentReviewRepository(session).dangerous(reviewed=reviewed, limit=limit)
    await audit(request, principal, AuditAction.DANGEROUS_GOODS_READ, 200, resource_type="shipment")
    return DangerousGoodsList(
        items=[
            DangerousGoodsItem(
                shipment_id=shipment.id,
                numero_guia=shipment.numero_guia,
                codigo_trayecto=shipment.codigo_trayecto,
                created_at=shipment.created_at,
                revision=_review(review) if review is not None else None,
            )
            for shipment, review in rows
        ]
    )


@router.post(
    "/internal/shipments/{shipment_id}/dangerous-goods-review",
    status_code=201,
    tags=["revisión"],
    operation_id="reviewDangerousGoods",
)
async def review_dangerous_goods(
    shipment_id: UUID, body: DangerousGoodsReviewRequest, principal: ReviewerOnly, request: Request
) -> DangerousGoodsReview:
    """Records the specialised decision. It does not yet change what the shipment may do:
    that rule (blocking until approval) is decision Q1 / CR-006."""
    box = request.app.state.container
    status = 500
    try:
        async with box.sessions() as session:
            repo = ShipmentReviewRepository(session)
            shipment = await repo.shipment(shipment_id)
            if shipment is None:
                raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "shipment"})
            if not shipment.mercancia_peligrosa:
                raise ContractError(
                    ErrorCode.VALIDACION, "El envío no está declarado como mercancía peligrosa."
                )
            if (
                await repo.review_of(shipment_id) is not None
                or shipment.review_status != ReviewStatus.PENDIENTE.value
            ):
                raise ContractError(ErrorCode.VALIDACION, "El envío ya tiene una decisión.")
            if principal.staff_user_id is None:  # REVIEWER only comes from a staff token
                raise ContractError(ErrorCode.NO_AUTORIZADO)
            row = ShipmentReviewRow(
                id=uuid4(),
                shipment_id=shipment_id,
                decision=body.decision.value,
                observaciones=body.observaciones.strip(),
                reviewer_staff_user_id=principal.staff_user_id,
                created_at=_now(box),
            )
            try:
                await repo.add(row)
                await session.commit()
            except IntegrityError:  # a concurrent decision won the unique constraint
                raise ContractError(
                    ErrorCode.VALIDACION, "El envío ya tiene una decisión."
                ) from None
        status = 201
        return _review(row)
    except ContractError as error:
        status = error.http_status
        raise
    finally:
        await audit(
            request,
            principal,
            AuditAction.DANGEROUS_GOODS_REVIEW,
            status,
            resource_type="shipment",
            resource_id=shipment_id,
        )


def _now(box: object) -> datetime:
    return box.clock.now()  # type: ignore[attr-defined, no-any-return]
