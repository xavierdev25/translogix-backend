"""Staff identities against PostgreSQL (CR-002).

CR-002 adds no endpoint: each test mounts a probe route protected by the real
dependency, the way the internal routes of CR-004/CR-005 will use it.
"""

from collections.abc import Iterable
from typing import Annotated, Any
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text

from app.contracts.common import ContractError, ErrorCode
from app.contracts.staff import Role
from app.staff.api.dependencies import staff_authenticated
from app.staff.application.services import StaffAdminService
from app.staff.domain.models import StaffContext, StaffStatus
from tests.db import http, scalar
from tests.shipments.support import envio, lote, new_client

PROBE = "/api/v1/internal/_probe"


def mount_probe(app: FastAPI) -> None:
    router = APIRouter()

    @router.get(PROBE)
    async def probe(
        who: Annotated[StaffContext, Depends(staff_authenticated(Role.ADMIN))],
    ) -> dict[str, Any]:
        return {"staff_user_id": str(who.staff_user_id), "roles": sorted(who.roles)}

    app.include_router(router)


async def admin(app: FastAPI, action: str, *args: Any, **kwargs: Any) -> Any:
    box = app.state.container
    async with box.sessions() as session:
        result = await getattr(StaffAdminService(session, box.clock), action)(*args, **kwargs)
        await session.commit()
    return result


async def new_staff(
    app: FastAPI,
    email: str = "asesora@translogix.co",
    roles: Iterable[Role] = (Role.ADMIN,),
) -> tuple[UUID, UUID, str]:
    """(user_id, token_id, full token)."""
    user = await admin(app, "create_user", email, "Asesora Demo", roles)
    record, token = await admin(app, "issue_token", user.id, expires_in_days=30)
    return user.id, record.id, token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_valid_token_identifies_the_person_and_is_stored_only_as_a_hash(
    app: FastAPI,
) -> None:
    mount_probe(app)
    user_id, token_id, token = await new_staff(app)
    async with http(app) as client:
        response = await client.get(PROBE, headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"staff_user_id": str(user_id), "roles": ["ADMIN"]}
    stored = await scalar(app, "SELECT key_hash FROM staff_tokens WHERE id = :id", id=token_id)
    assert stored != token
    assert await scalar(app, "SELECT count(*) FROM staff_tokens WHERE key_hash LIKE '%tlxs%'") == 0
    assert await scalar(
        app, "SELECT last_used_at IS NOT NULL FROM staff_tokens WHERE id = :id", id=token_id
    )


async def test_every_invalid_credential_is_the_same_401(app: FastAPI) -> None:
    mount_probe(app)
    user_id, token_id, token = await new_staff(app)
    _, expired_id, expired = await new_staff(app, email="otro@translogix.co")
    _, _, b2b_key = await new_client(app)
    async with app.state.container.sessions() as session:
        await session.execute(
            text("UPDATE staff_tokens SET expires_at = now() - interval '1 s' WHERE id = :id"),
            {"id": expired_id},
        )
        await session.commit()
    async with http(app) as client:
        missing = await client.get(PROBE)
        malformed = await client.get(PROBE, headers=bearer("not-a-token"))
        forged = await client.get(PROBE, headers=bearer(token[:-4] + "AAAA"))
        b2b = await client.get(PROBE, headers=bearer(b2b_key))
        past_expiry = await client.get(PROBE, headers=bearer(expired))
        await admin(app, "set_status", user_id, StaffStatus.INACTIVE)
        inactive = await client.get(PROBE, headers=bearer(token))
        await admin(app, "set_status", user_id, StaffStatus.ACTIVE)
        await admin(app, "revoke_token", token_id)
        revoked = await client.get(PROBE, headers=bearer(token))

    for response in (missing, malformed, forged, b2b, past_expiry, inactive, revoked):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "NO_AUTENTICADO"
    failures = await scalar(
        app, "SELECT count(*) FROM api_audit_events WHERE action = 'auth.failed'"
    )
    assert failures == 7


async def test_missing_role_is_403_and_role_changes_apply_on_the_next_request(app: FastAPI) -> None:
    mount_probe(app)
    user_id, _, token = await new_staff(app, roles=[Role.REVIEWER])
    async with http(app) as client:
        forbidden = await client.get(PROBE, headers=bearer(token))
        await admin(app, "set_roles", user_id, [Role.REVIEWER, Role.ADMIN])
        allowed = await client.get(PROBE, headers=bearer(token))

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "NO_AUTORIZADO"
    assert forbidden.json()["error"]["details"] == {"roles_requeridos": ["ADMIN"]}
    assert allowed.status_code == 200
    assert allowed.json()["roles"] == ["ADMIN", "REVIEWER"]
    audited = await scalar(
        app,
        "SELECT staff_user_id FROM api_audit_events"
        " WHERE action = 'auth.failed' AND http_status = 403",
    )
    assert audited == user_id


async def test_staff_tokens_do_not_open_b2b_routes(app: FastAPI) -> None:
    _, _, token = await new_staff(app, roles=[Role.ADMIN, Role.REVIEWER])
    async with http(app) as client:
        response = await client.post(
            "/api/v1/shipment-batches", json=lote("L-1", envio("P-1")), headers=bearer(token)
        )

    # 1.7.0 (CR-009): the token is valid, so this is authorization (403), not authentication.
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NO_AUTORIZADO"
    assert await scalar(app, "SELECT count(*) FROM shipment_batches") == 0


async def test_audit_keeps_no_credentials(app: FastAPI) -> None:
    mount_probe(app)
    _, _, token = await new_staff(app, roles=[Role.REVIEWER])
    async with http(app) as client:
        await client.get(PROBE, headers=bearer(token))
        await client.get(PROBE, headers=bearer(token[:-4] + "AAAA"))

    leaked = await scalar(
        app,
        "SELECT count(*) FROM api_audit_events WHERE api_audit_events::text LIKE :t",
        t=f"%{token[5:17]}_%",
    )
    assert leaked == 0


@pytest.mark.parametrize(
    ("action", "args", "kwargs", "expected"),
    [
        ("create_user", ("no-es-correo", "X", [Role.REVIEWER]), {}, ErrorCode.VALIDACION),
        # CLIENT only comes from an API key: a staff user can never hold it (CR-009).
        ("create_user", ("c@translogix.co", "X", [Role.CLIENT]), {}, ErrorCode.VALIDACION),
        ("create_user", ("a@translogix.co", "X", []), {}, ErrorCode.VALIDACION),
        ("issue_token", (UUID(int=1),), {"expires_in_days": 30}, ErrorCode.NO_ENCONTRADO),
    ],
)
async def test_admin_service_rejects_invalid_requests(
    app: FastAPI, action: str, args: tuple[Any, ...], kwargs: dict[str, Any], expected: ErrorCode
) -> None:
    with pytest.raises(ContractError) as error:
        await admin(app, action, *args, **kwargs)

    assert error.value.code is expected


async def test_admin_rules_for_emails_expiry_and_inactive_users(app: FastAPI) -> None:
    user_id, _, _ = await new_staff(app, email="Asesora@TransLogix.co")

    with pytest.raises(ContractError) as duplicate:
        await admin(app, "create_user", "asesora@translogix.co", "Otra", [Role.REVIEWER])
    for days in (0, 366):
        with pytest.raises(ContractError) as out_of_range:
            await admin(app, "issue_token", user_id, expires_in_days=days)
        assert out_of_range.value.code is ErrorCode.VALIDACION
    await admin(app, "set_status", user_id, StaffStatus.INACTIVE)
    with pytest.raises(ContractError) as inactive:
        await admin(app, "issue_token", user_id, expires_in_days=30)
    described = await admin(app, "describe", user_id)

    assert duplicate.value.code is ErrorCode.VALIDACION
    assert inactive.value.code is ErrorCode.VALIDACION
    assert described[0].email == "asesora@translogix.co"
    assert len(described[1]) == 1
