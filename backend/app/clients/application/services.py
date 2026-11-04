"""Use cases of enterprise clients: administration (CLI) and API key authentication."""

import hmac
from collections.abc import Iterable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.domain.models import (
    ApiKeyRecord,
    ClientContext,
    ClientStatus,
    EnterpriseClient,
    hash_api_key,
    key_prefix_of,
    new_api_key,
    normalize_nit,
)
from app.clients.infrastructure.repositories import ApiKeyRepository, ClientRepository
from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import Clock
from app.contracts.shipment_batches import ApiScope


class ClientAdminService:
    """Back-office operations; the caller owns the transaction (commit)."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._clients = ClientRepository(session)
        self._keys = ApiKeyRepository(session)
        self._clock = clock

    async def create_client(
        self, nit: str, razon_social: str, tipo_cuenta: str = "ESTANDAR"
    ) -> EnterpriseClient:
        try:
            normalized = normalize_nit(nit)
        except ValueError:
            raise ContractError(ErrorCode.VALIDACION, "El NIT no es válido.") from None
        if await self._clients.get_by_nit(normalized) is not None:
            raise ContractError(ErrorCode.VALIDACION, "Ya existe un cliente con ese NIT.")
        now = self._clock.now()
        client = EnterpriseClient(
            id=uuid4(),
            nit=normalized,
            razon_social=razon_social.strip(),
            estado=ClientStatus.ACTIVE,
            tipo_cuenta=tipo_cuenta,
            created_at=now,
            updated_at=now,
        )
        await self._clients.add(client)
        return client

    async def create_api_key(
        self, client_id: UUID, scopes: Iterable[ApiScope]
    ) -> tuple[ApiKeyRecord, str]:
        """(record, full key). Show the full key once; it cannot be recovered later."""
        if await self._clients.get(client_id) is None:
            raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "client"})
        wanted = frozenset(scopes)
        if not wanted:
            raise ContractError(ErrorCode.VALIDACION, "Una API key necesita al menos un scope.")
        prefix, full_key = new_api_key()
        record = ApiKeyRecord(
            id=uuid4(),
            client_id=client_id,
            key_prefix=prefix,
            scopes=wanted,
            created_at=self._clock.now(),
            last_used_at=None,
            revoked_at=None,
        )
        await self._keys.add(record, hash_api_key(full_key))
        return record, full_key

    async def revoke_api_key(self, key_id: UUID) -> bool:
        return await self._keys.revoke(key_id, self._clock.now())

    async def set_status(self, client_id: UUID, estado: ClientStatus) -> bool:
        return await self._clients.set_status(client_id, estado, self._clock.now())

    async def describe(self, client_id: UUID) -> tuple[EnterpriseClient, list[ApiKeyRecord]] | None:
        client = await self._clients.get(client_id)
        if client is None:
            return None
        return client, await self._keys.list_for_client(client_id)


class ApiKeyAuthenticator:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clients = ClientRepository(session)
        self._keys = ApiKeyRepository(session)
        self._clock = clock

    async def authenticate(self, full_key: str | None) -> ClientContext:
        """Valid, unrevoked key of an active client → its context; commits ``last_used_at``.

        Unknown, malformed and revoked keys fail the same way (401), so the
        response does not tell which prefixes exist.
        """
        prefix = key_prefix_of(full_key) if full_key else None
        found = await self._keys.find_by_prefix(prefix) if prefix else None
        if full_key is None or found is None:
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        record, stored_hash = found
        if not hmac.compare_digest(stored_hash, hash_api_key(full_key)) or record.revoked_at:
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        client = await self._clients.get(record.client_id)
        if client is None or client.estado is not ClientStatus.ACTIVE:
            raise ContractError(ErrorCode.NO_AUTORIZADO, details={"motivo": "cliente_inactivo"})
        await self._keys.touch(record.id, self._clock.now())
        await self._session.commit()
        return ClientContext(client_id=client.id, api_key_id=record.id, scopes=record.scopes)


def require_scope(context: ClientContext, scope: ApiScope) -> None:
    if scope not in context.scopes:
        raise ContractError(ErrorCode.NO_AUTORIZADO, details={"scope_requerido": scope.value})
