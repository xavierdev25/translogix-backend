from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.domain.models import ApiKeyRecord, ClientStatus, EnterpriseClient
from app.clients.infrastructure.tables import ClientApiKeyRow, EnterpriseClientRow
from app.contracts.shipment_batches import ApiScope


def _client(row: EnterpriseClientRow) -> EnterpriseClient:
    return EnterpriseClient(
        id=row.id,
        nit=row.nit,
        razon_social=row.razon_social,
        estado=ClientStatus(row.estado),
        tipo_cuenta=row.tipo_cuenta,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _key(row: ClientApiKeyRow) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=row.id,
        client_id=row.client_id,
        key_prefix=row.key_prefix,
        scopes=frozenset(ApiScope(s) for s in row.scopes),
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, client: EnterpriseClient) -> None:
        self._session.add(
            EnterpriseClientRow(
                id=client.id,
                nit=client.nit,
                razon_social=client.razon_social,
                estado=client.estado.value,
                tipo_cuenta=client.tipo_cuenta,
                created_at=client.created_at,
                updated_at=client.updated_at,
            )
        )
        await self._session.flush()

    async def get(self, client_id: UUID) -> EnterpriseClient | None:
        row = await self._session.get(EnterpriseClientRow, client_id)
        return _client(row) if row else None

    async def get_by_nit(self, nit: str) -> EnterpriseClient | None:
        row = await self._session.scalar(
            select(EnterpriseClientRow).where(EnterpriseClientRow.nit == nit)
        )
        return _client(row) if row else None

    async def set_status(self, client_id: UUID, estado: ClientStatus, at: datetime) -> bool:
        result = await self._session.execute(
            update(EnterpriseClientRow)
            .where(EnterpriseClientRow.id == client_id)
            .values(estado=estado.value, updated_at=at)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: ApiKeyRecord, key_hash: str) -> None:
        self._session.add(
            ClientApiKeyRow(
                id=record.id,
                client_id=record.client_id,
                key_prefix=record.key_prefix,
                key_hash=key_hash,
                scopes=sorted(s.value for s in record.scopes),
                created_at=record.created_at,
                last_used_at=None,
                revoked_at=None,
            )
        )
        await self._session.flush()

    async def find_by_prefix(self, prefix: str) -> tuple[ApiKeyRecord, str] | None:
        """(record, stored hash)."""
        row = await self._session.scalar(
            select(ClientApiKeyRow).where(ClientApiKeyRow.key_prefix == prefix)
        )
        return (_key(row), row.key_hash) if row else None

    async def list_for_client(self, client_id: UUID) -> list[ApiKeyRecord]:
        rows = await self._session.scalars(
            select(ClientApiKeyRow)
            .where(ClientApiKeyRow.client_id == client_id)
            .order_by(ClientApiKeyRow.created_at)
        )
        return [_key(row) for row in rows]

    async def touch(self, key_id: UUID, at: datetime) -> None:
        await self._session.execute(
            update(ClientApiKeyRow).where(ClientApiKeyRow.id == key_id).values(last_used_at=at)
        )

    async def revoke(self, key_id: UUID, at: datetime) -> bool:
        result = await self._session.execute(
            update(ClientApiKeyRow)
            .where(ClientApiKeyRow.id == key_id, ClientApiKeyRow.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]
