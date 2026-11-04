"""Authentication: turn a credential into a ``Principal``. It knows nothing about routes.

Three credential families:

| Credential | Header | Role |
|---|---|---|
| API key ``tlx_…`` | ``Authorization: Bearer`` | ``CLIENT`` with its scopes |
| Staff token ``tlxs_…`` | ``Authorization: Bearer`` | The user's roles (ADMIN / REVIEWER) |
| Legacy ``ADMIN_TOKEN`` | ``X-Admin-Token`` | ``ADMIN`` (shared, not attributable to a person) |

Every failure is ``NO_AUTENTICADO`` (401) and does not say which part failed.
"""

import hmac
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.clients.application.services import ApiKeyAuthenticator
from app.clients.domain.models import ClientContext
from app.contracts.auth import AuthMethod
from app.contracts.common import ContractError, ErrorCode
from app.contracts.shipment_batches import ApiScope
from app.contracts.staff import Role
from app.staff.application.services import StaffAuthenticator
from app.staff.domain.models import staff_token_prefix


@dataclass(frozen=True)
class Principal:
    roles: frozenset[Role]
    method: AuthMethod
    client_id: UUID | None = None
    api_key_id: UUID | None = None
    scopes: frozenset[ApiScope] = field(default_factory=frozenset)
    staff_user_id: UUID | None = None
    token_id: UUID | None = None

    def has(self, role: Role) -> bool:
        return role in self.roles

    def client_context(self) -> ClientContext:
        """The B2B client acting; only a CLIENT principal has one."""
        if self.client_id is None or self.api_key_id is None:
            raise ContractError(ErrorCode.NO_AUTORIZADO)
        return ClientContext(
            client_id=self.client_id, api_key_id=self.api_key_id, scopes=self.scopes
        )


async def authenticate(box: Any, bearer: str | None, admin_token: str | None) -> Principal:
    """``bearer`` wins over ``X-Admin-Token`` when both are sent."""
    if bearer:
        if staff_token_prefix(bearer) is not None:
            async with box.sessions() as session:
                staff = await StaffAuthenticator(session, box.clock).authenticate(bearer)
            return Principal(
                roles=staff.roles,
                method=AuthMethod.STAFF_TOKEN,
                staff_user_id=staff.staff_user_id,
                token_id=staff.token_id,
            )
        async with box.sessions() as session:
            client = await ApiKeyAuthenticator(session, box.clock).authenticate(bearer)
        return Principal(
            roles=frozenset({Role.CLIENT}),
            method=AuthMethod.API_KEY,
            client_id=client.client_id,
            api_key_id=client.api_key_id,
            scopes=client.scopes,
        )
    if admin_token:
        expected = box.settings.admin_token.get_secret_value()
        if hmac.compare_digest(admin_token.encode(), expected.encode()):
            return Principal(roles=frozenset({Role.ADMIN}), method=AuthMethod.ADMIN_TOKEN)
    raise ContractError(ErrorCode.NO_AUTENTICADO)
