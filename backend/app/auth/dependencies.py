"""Authorization: reusable FastAPI dependencies. Deny by default.

``require(Role.ADMIN, Role.REVIEWER)`` authenticates the caller (``principal.authenticate``),
then checks the role, and for a CLIENT the API key scope too:
- no credential, or an invalid one → 401 ``NO_AUTENTICADO``;
- valid credential without the role → 403 ``NO_AUTORIZADO``.

Both outcomes are audited. Ownership is not checked here: it is a rule of the use case
(the repository filters by owner), because only the use case knows which resource is
asked for.

The dependency only declares the security schemes its roles can use, so the OpenAPI
shows the right ones:
- ``ApiKey`` for CLIENT;
- ``StaffToken`` for ADMIN and REVIEWER;
- ``AdminToken`` (legacy ``X-Admin-Token``) for ADMIN.
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import Request

from app.audit.domain.models import ApiAuditEvent, AuditAction
from app.auth.principal import Principal, authenticate
from app.contracts.api_spec import AdminTokenDep, ApiKeyDep, StaffTokenDep
from app.contracts.common import ContractError, ErrorCode
from app.contracts.shipment_batches import ApiScope
from app.contracts.staff import Role
from app.shared.http import request_id_of

RBAC_ROLES_ATTRIBUTE = "__rbac_roles__"


async def audit(
    request: Request,
    principal: Principal | None,
    action: AuditAction,
    status: int,
    *,
    resource_type: str | None = None,
    resource_id: UUID | str | None = None,
) -> None:
    box = request.app.state.container
    await box.audit.record(
        ApiAuditEvent(
            action=action,
            http_status=status,
            request_id=request_id_of(request),
            created_at=box.clock.now(),
            client_id=principal.client_id if principal else None,
            api_key_id=principal.api_key_id if principal else None,
            staff_user_id=principal.staff_user_id if principal else None,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
        )
    )


def require(*allowed: Role, scope: ApiScope | None = None) -> Callable[..., Awaitable[Principal]]:
    if not allowed:
        raise ValueError("deny by default: a protected route must name the roles it accepts")
    wanted = frozenset(allowed)

    async def dependency(request: Request, **credentials: Any) -> Principal:
        bearer = credentials.get("api_key") or credentials.get("staff_token")
        principal: Principal | None = None
        try:
            principal = await authenticate(
                request.app.state.container,
                bearer.credentials if bearer else None,
                credentials.get("admin_token"),
            )
            if not principal.roles & wanted:
                raise ContractError(
                    ErrorCode.NO_AUTORIZADO,
                    details={"roles_requeridos": sorted(r.value for r in wanted)},
                )
            if scope is not None and principal.has(Role.CLIENT) and scope not in principal.scopes:
                raise ContractError(
                    ErrorCode.NO_AUTORIZADO, details={"scope_requerido": scope.value}
                )
        except ContractError as error:
            failed = error.http_status == 401
            await audit(
                request,
                principal,
                AuditAction.AUTH_FAILED if failed else AuditAction.ACCESS_DENIED,
                error.http_status,
                resource_type="route",
                resource_id=request.url.path[:64],
            )
            raise
        return principal

    parameters = [inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request)]
    if Role.CLIENT in wanted:
        parameters.append(_credential("api_key", ApiKeyDep))
    if wanted & {Role.ADMIN, Role.REVIEWER}:
        parameters.append(_credential("staff_token", StaffTokenDep))
    if Role.ADMIN in wanted:
        parameters.append(_credential("admin_token", AdminTokenDep))
    dependency.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    setattr(dependency, RBAC_ROLES_ATTRIBUTE, wanted)
    return dependency


def _credential(name: str, annotation: Any) -> inspect.Parameter:
    return inspect.Parameter(
        name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=None
    )
