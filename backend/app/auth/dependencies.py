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
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Request

from app.audit.domain.models import ApiAuditEvent, AuditAction
from app.auth.principal import Principal, authenticate
from app.contracts.api_spec import AdminTokenDep, ApiKeyDep, ClientIdDep, StaffTokenDep
from app.contracts.common import ChannelType, ContractError, ErrorCode
from app.contracts.shipment_batches import ApiScope
from app.contracts.staff import Role
from app.modules.conversation.service import hash_user
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


# --- 2.0.0 (CR-010): the web-chat user (SEC-01, SEC-06) ------------------------------

SESSION_OWNER_ATTRIBUTE = "__session_owner__"


@dataclass(frozen=True)
class ChatCaller:
    """The owner of a web-chat session (``X-Client-Id``) and/or a staff ``Principal``."""

    user_hash: str | None
    principal: Principal | None


def _user_hash(request: Request, raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        client_id = UUID(raw)
    except ValueError:
        raise ContractError(ErrorCode.NO_AUTENTICADO) from None
    salt = request.app.state.container.settings.pii_hash_salt.get_secret_value()
    return hash_user(salt, ChannelType.WEB_CHAT, str(client_id))


def web_chat_user() -> Callable[..., Awaitable[str]]:
    """``X-Client-Id`` required → the user's HMAC (never the raw id); 401 if missing/malformed."""

    async def dependency(request: Request, client: ClientIdDep) -> str:
        try:
            user_hash = _user_hash(request, client)
            if user_hash is None:
                raise ContractError(ErrorCode.NO_AUTENTICADO)
        except ContractError as error:
            await audit(
                request,
                None,
                AuditAction.AUTH_FAILED,
                error.http_status,
                resource_type="route",
                resource_id=request.url.path[:64],
            )
            raise
        return user_hash

    setattr(dependency, SESSION_OWNER_ATTRIBUTE, True)
    return dependency


def chat_history_reader() -> Callable[..., Awaitable[ChatCaller]]:
    """The owner (``X-Client-Id``) or ADMIN/REVIEWER staff. Which session they may read is
    decided by the route (ownership, or a case in the REVIEWER's inbox)."""

    async def dependency(
        request: Request,
        client: ClientIdDep,
        staff_token: StaffTokenDep = None,
        admin_token: AdminTokenDep = None,
    ) -> ChatCaller:
        principal: Principal | None = None
        try:
            user_hash = _user_hash(request, client)
            if user_hash is not None:
                return ChatCaller(user_hash=user_hash, principal=None)
            principal = await authenticate(
                request.app.state.container,
                staff_token.credentials if staff_token else None,
                admin_token,
            )
            if not principal.roles & {Role.ADMIN, Role.REVIEWER}:
                raise ContractError(
                    ErrorCode.NO_AUTORIZADO, details={"roles_requeridos": ["ADMIN", "REVIEWER"]}
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
        return ChatCaller(user_hash=None, principal=principal)

    setattr(dependency, SESSION_OWNER_ATTRIBUTE, True)
    return dependency
