"""Staff use cases: administration (CLI) and personal-token authentication."""

import hmac
from collections.abc import Iterable
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import Clock
from app.contracts.staff import STAFF_ROLES, Role
from app.shared.passwords import hash_password, verify_password
from app.staff.domain.models import (
    TOKEN_MAX_DAYS,
    StaffContext,
    StaffStatus,
    StaffTokenRecord,
    StaffUser,
    hash_staff_token,
    new_staff_token,
    normalize_email,
    staff_token_prefix,
)
from app.staff.infrastructure.repositories import StaffTokenRepository, StaffUserRepository


def _roles(roles: Iterable[Role]) -> frozenset[Role]:
    wanted = frozenset(roles)
    if not wanted:
        raise ContractError(ErrorCode.VALIDACION, "Un usuario interno necesita al menos un rol.")
    if not wanted <= STAFF_ROLES:  # CLIENT only ever comes from an API key
        raise ContractError(
            ErrorCode.VALIDACION,
            "Un usuario interno solo puede tener los roles ADMIN o REVIEWER.",
            details={"roles_permitidos": sorted(r.value for r in STAFF_ROLES)},
        )
    return wanted


class StaffAdminService:
    """Back-office operations; the caller owns the transaction (commit)."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._users = StaffUserRepository(session)
        self._tokens = StaffTokenRepository(session)
        self._clock = clock

    async def create_user(
        self,
        email: str,
        nombre: str,
        roles: Iterable[Role],
        *,
        password: str | None = None,
    ) -> StaffUser:
        try:
            normalized = normalize_email(email)
        except ValueError:
            raise ContractError(ErrorCode.VALIDACION, "El correo no es válido.") from None
        if await self._users.get_by_email(normalized) is not None:
            raise ContractError(ErrorCode.VALIDACION, "Ya existe un usuario con ese correo.")
        now = self._clock.now()
        user = StaffUser(
            id=uuid4(),
            email=normalized,
            nombre=nombre.strip(),
            roles=_roles(roles),
            estado=StaffStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        pw_hash = hash_password(password) if password else None
        await self._users.add(user, password_hash=pw_hash)
        return user

    async def set_roles(self, user_id: UUID, roles: Iterable[Role]) -> bool:
        return await self._users.update(user_id, self._clock.now(), roles=_roles(roles))

    async def set_status(self, user_id: UUID, estado: StaffStatus) -> bool:
        return await self._users.update(user_id, self._clock.now(), estado=estado)

    async def set_password(self, user_id: UUID, password: str) -> bool:
        """Set or change the password of a staff user."""
        if len(password) < 8:
            raise ContractError(
                ErrorCode.VALIDACION, "La contraseña debe tener al menos 8 caracteres."
            )
        return await self._users.set_password_hash(
            user_id,
            hash_password(password),
            self._clock.now(),
        )

    async def login(
        self,
        email: str,
        password: str,
        *,
        session_days: int = 1,
    ) -> tuple[StaffTokenRecord, str, StaffUser]:
        """Email + password → (token record, full token, user). The token is short-lived (1 day)."""
        try:
            normalized = normalize_email(email)
        except ValueError:
            raise ContractError(ErrorCode.NO_AUTENTICADO) from None
        found = await self._users.get_password_hash(normalized)
        if found is None:
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        user, pw_hash = found
        if pw_hash is None or not verify_password(password, pw_hash):
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        if user.estado is not StaffStatus.ACTIVE:
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        # Issue a short-lived session token
        prefix, full_token = new_staff_token()
        now = self._clock.now()
        record = StaffTokenRecord(
            id=uuid4(),
            staff_user_id=user.id,
            key_prefix=prefix,
            created_at=now,
            expires_at=now + timedelta(days=session_days),
            last_used_at=None,
            revoked_at=None,
        )
        await self._tokens.add(record, hash_staff_token(full_token))
        return record, full_token, user

    async def issue_token(
        self, user_id: UUID, *, expires_in_days: int
    ) -> tuple[StaffTokenRecord, str]:
        """(record, full token). Show the full token once; it cannot be recovered later."""
        if not 1 <= expires_in_days <= TOKEN_MAX_DAYS:
            raise ContractError(
                ErrorCode.VALIDACION, f"La vigencia debe estar entre 1 y {TOKEN_MAX_DAYS} días."
            )
        user = await self._users.get(user_id)
        if user is None:
            raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "staff_user"})
        if user.estado is not StaffStatus.ACTIVE:
            raise ContractError(ErrorCode.VALIDACION, "El usuario está inactivo.")
        prefix, full_token = new_staff_token()
        now = self._clock.now()
        record = StaffTokenRecord(
            id=uuid4(),
            staff_user_id=user_id,
            key_prefix=prefix,
            created_at=now,
            expires_at=now + timedelta(days=expires_in_days),
            last_used_at=None,
            revoked_at=None,
        )
        await self._tokens.add(record, hash_staff_token(full_token))
        return record, full_token

    async def revoke_token(self, token_id: UUID) -> bool:
        return await self._tokens.revoke(token_id, self._clock.now())

    async def describe(self, user_id: UUID) -> tuple[StaffUser, list[StaffTokenRecord]] | None:
        user = await self._users.get(user_id)
        if user is None:
            return None
        return user, await self._tokens.list_for_user(user_id)


class StaffAuthenticator:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._users = StaffUserRepository(session)
        self._tokens = StaffTokenRepository(session)
        self._clock = clock

    async def authenticate(self, full_token: str | None) -> StaffContext:
        """Valid, unrevoked, unexpired token of an active user → context; commits ``last_used_at``.

        Every failure is the same 401 (CR-002), so the answer reveals neither which prefixes
        exist nor whether the person was deactivated. Roles are read now, not from the token:
        a role change applies to the next request.
        """
        prefix = staff_token_prefix(full_token) if full_token else None
        found = await self._tokens.find_by_prefix(prefix) if prefix else None
        if full_token is None or found is None:
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        record, stored_hash = found
        now = self._clock.now()
        if (
            not hmac.compare_digest(stored_hash, hash_staff_token(full_token))
            or record.revoked_at is not None
            or record.expires_at <= now
        ):
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        user = await self._users.get(record.staff_user_id)
        if user is None or user.estado is not StaffStatus.ACTIVE:
            raise ContractError(ErrorCode.NO_AUTENTICADO)
        await self._tokens.touch(record.id, now)
        await self._session.commit()
        return StaffContext(staff_user_id=user.id, token_id=record.id, roles=user.roles)


def require_role(context: StaffContext, *allowed: Role) -> None:
    """At least one of ``allowed``. Finer rules (own followups only…) live in each use case."""
    if not context.roles & set(allowed):
        raise ContractError(
            ErrorCode.NO_AUTORIZADO, details={"roles_requeridos": sorted(r.value for r in allowed)}
        )
