from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.staff import Role
from app.staff.domain.models import StaffStatus, StaffTokenRecord, StaffUser
from app.staff.infrastructure.tables import StaffTokenRow, StaffUserRow


def _user(row: StaffUserRow) -> StaffUser:
    return StaffUser(
        id=row.id,
        email=row.email,
        nombre=row.nombre,
        roles=frozenset(Role(r) for r in row.roles),
        estado=StaffStatus(row.estado),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _token(row: StaffTokenRow) -> StaffTokenRecord:
    return StaffTokenRecord(
        id=row.id,
        staff_user_id=row.staff_user_id,
        key_prefix=row.key_prefix,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _roles(roles: Iterable[Role]) -> list[str]:
    return sorted(r.value for r in roles)


class StaffUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: StaffUser) -> None:
        self._session.add(
            StaffUserRow(
                id=user.id,
                email=user.email,
                nombre=user.nombre,
                roles=_roles(user.roles),
                estado=user.estado.value,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
        await self._session.flush()

    async def get(self, user_id: UUID) -> StaffUser | None:
        row = await self._session.get(StaffUserRow, user_id)
        return _user(row) if row else None

    async def get_by_email(self, email: str) -> StaffUser | None:
        row = await self._session.scalar(select(StaffUserRow).where(StaffUserRow.email == email))
        return _user(row) if row else None

    async def update(
        self,
        user_id: UUID,
        at: datetime,
        *,
        roles: Iterable[Role] | None = None,
        estado: StaffStatus | None = None,
    ) -> bool:
        values: dict[str, object] = {"updated_at": at}
        if roles is not None:
            values["roles"] = _roles(roles)
        if estado is not None:
            values["estado"] = estado.value
        result = await self._session.execute(
            update(StaffUserRow).where(StaffUserRow.id == user_id).values(**values)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]


class StaffTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: StaffTokenRecord, key_hash: str) -> None:
        self._session.add(
            StaffTokenRow(
                id=record.id,
                staff_user_id=record.staff_user_id,
                key_prefix=record.key_prefix,
                key_hash=key_hash,
                created_at=record.created_at,
                expires_at=record.expires_at,
                last_used_at=None,
                revoked_at=None,
            )
        )
        await self._session.flush()

    async def find_by_prefix(self, prefix: str) -> tuple[StaffTokenRecord, str] | None:
        """(record, stored hash)."""
        row = await self._session.scalar(
            select(StaffTokenRow).where(StaffTokenRow.key_prefix == prefix)
        )
        return (_token(row), row.key_hash) if row else None

    async def list_for_user(self, user_id: UUID) -> list[StaffTokenRecord]:
        rows = await self._session.scalars(
            select(StaffTokenRow)
            .where(StaffTokenRow.staff_user_id == user_id)
            .order_by(StaffTokenRow.created_at)
        )
        return [_token(row) for row in rows]

    async def touch(self, token_id: UUID, at: datetime) -> None:
        await self._session.execute(
            update(StaffTokenRow).where(StaffTokenRow.id == token_id).values(last_used_at=at)
        )

    async def revoke(self, token_id: UUID, at: datetime) -> bool:
        result = await self._session.execute(
            update(StaffTokenRow)
            .where(StaffTokenRow.id == token_id, StaffTokenRow.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]
