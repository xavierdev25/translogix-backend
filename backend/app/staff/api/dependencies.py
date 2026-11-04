"""FastAPI dependency for the internal routes (``/api/v1/internal/``, CR-004/CR-005).

``Authorization: Bearer <tlxs_…>`` → ``StaffContext`` holding one of the allowed roles.
A B2B API key never authenticates here (different family and pattern).
"""

from collections.abc import Awaitable, Callable

from fastapi import Request

from app.audit.domain.models import ApiAuditEvent, AuditAction
from app.contracts.api_spec import StaffTokenDep
from app.contracts.common import ContractError
from app.contracts.staff import StaffRole
from app.shared.http import request_id_of
from app.staff.application.services import StaffAuthenticator, require_role
from app.staff.domain.models import StaffContext


def staff_authenticated(*allowed: StaffRole) -> Callable[..., Awaitable[StaffContext]]:
    if not allowed:
        raise ValueError("an internal route must name the roles it accepts")

    async def dependency(request: Request, credentials: StaffTokenDep) -> StaffContext:
        box = request.app.state.container
        context: StaffContext | None = None
        try:
            async with box.sessions() as session:
                context = await StaffAuthenticator(session, box.clock).authenticate(
                    credentials.credentials if credentials else None
                )
            require_role(context, *allowed)
        except ContractError as error:
            await box.audit.record(
                ApiAuditEvent(
                    action=AuditAction.AUTH_FAILED,
                    http_status=error.http_status,
                    request_id=request_id_of(request),
                    created_at=box.clock.now(),
                    staff_user_id=context.staff_user_id if context else None,
                    resource_type="staff_token",
                    resource_id=str(context.token_id) if context else None,
                )
            )
            raise
        return context

    return dependency
