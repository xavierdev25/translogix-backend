"""FastAPI dependency: ``Authorization: Bearer <api_key>`` → ``ClientContext`` with a scope."""

from collections.abc import Awaitable, Callable

from fastapi import Request

from app.audit.domain.models import ApiAuditEvent, AuditAction
from app.clients.application.services import ApiKeyAuthenticator, require_scope
from app.clients.domain.models import ClientContext
from app.contracts.api_spec import ApiKeyDep
from app.contracts.common import ContractError
from app.contracts.shipment_batches import ApiScope
from app.shared.http import request_id_of


def authenticated(scope: ApiScope) -> Callable[..., Awaitable[ClientContext]]:
    async def dependency(request: Request, credentials: ApiKeyDep) -> ClientContext:
        box = request.app.state.container
        context: ClientContext | None = None
        try:
            async with box.sessions() as session:
                context = await ApiKeyAuthenticator(session, box.clock).authenticate(
                    credentials.credentials if credentials else None
                )
            require_scope(context, scope)
        except ContractError as error:
            await box.audit.record(
                ApiAuditEvent(
                    action=AuditAction.AUTH_FAILED,
                    http_status=error.http_status,
                    request_id=request_id_of(request),
                    created_at=box.clock.now(),
                    client_id=context.client_id if context else None,
                    api_key_id=context.api_key_id if context else None,
                    resource_type="api_key",
                )
            )
            raise
        return context

    return dependency
