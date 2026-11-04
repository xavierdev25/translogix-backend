"""Unit tests of staff identities (CR-002): no database."""

from uuid import uuid4

import pytest

from app.clients.domain.models import KEY_PATTERN, key_prefix_of, new_api_key
from app.contracts.common import ContractError, ErrorCode
from app.contracts.staff import Role
from app.staff.api.dependencies import staff_authenticated
from app.staff.application.services import require_role
from app.staff.domain.models import (
    TOKEN_PATTERN,
    StaffContext,
    hash_staff_token,
    new_staff_token,
    normalize_email,
    staff_token_prefix,
)


def context(*roles: Role) -> StaffContext:
    return StaffContext(staff_user_id=uuid4(), token_id=uuid4(), roles=frozenset(roles))


def test_staff_tokens_are_random_prefixed_and_hashed() -> None:
    prefix, token = new_staff_token()
    other_prefix, other = new_staff_token()

    assert TOKEN_PATTERN.match(token)
    assert token.startswith(f"tlxs_{prefix}_")
    assert staff_token_prefix(token) == prefix
    assert prefix != other_prefix
    assert token != other
    assert hash_staff_token(token) == hash_staff_token(token) != token


def test_staff_tokens_and_b2b_keys_never_match_each_others_pattern() -> None:
    _, staff = new_staff_token()
    _, b2b = new_api_key()

    assert staff_token_prefix(b2b) is None
    assert key_prefix_of(staff) is None
    assert not KEY_PATTERN.match(staff)


def test_emails_are_normalised_and_validated() -> None:
    assert normalize_email("  Asesora.Demo@TransLogix.CO ") == "asesora.demo@translogix.co"
    with pytest.raises(ValueError, match="correo"):
        normalize_email("sin-arroba")


def test_one_allowed_role_is_enough() -> None:
    require_role(context(Role.REVIEWER), Role.REVIEWER, Role.ADMIN)


def test_missing_role_is_forbidden_and_names_the_required_roles() -> None:
    with pytest.raises(ContractError) as error:
        require_role(context(Role.REVIEWER), Role.ADMIN)

    assert error.value.code is ErrorCode.NO_AUTORIZADO
    assert error.value.http_status == 403
    assert error.value.details == {"roles_requeridos": ["ADMIN"]}


def test_an_internal_route_must_declare_its_roles() -> None:
    with pytest.raises(ValueError, match="roles"):
        staff_authenticated()
