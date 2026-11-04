"""Roles (RBAC, CR-009 in 1.7.0) and internal staff identities (CR-002, 1.4.0).

There are exactly three roles:
- ``ADMIN``: global administration;
- ``CLIENT``: external B2B client, authenticated with its API key ``tlx_…``;
- ``REVIEWER``: human advisor and reviewer of escalated cases and dangerous goods.

Staff users hold ``ADMIN`` and/or ``REVIEWER`` and authenticate with a personal token
``tlxs_<prefix>_<secret>`` sent as ``Authorization: Bearer`` (OpenAPI scheme
``StaffToken``). ``CLIENT`` is never assigned to a staff user: it comes only from an
API key. The role always comes from the credential, never from the request.

1.7.0 replaced the 1.4.0 staff roles (ASESOR, SUPERVISOR, OPERADOR_LOGISTICO,
REVISOR_ESPECIALIZADO); migration 0005 maps every existing one to ``REVIEWER``, so
nobody gains ``ADMIN`` by the change.
"""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    CLIENT = "CLIENT"
    REVIEWER = "REVIEWER"


STAFF_ROLES = frozenset({Role.ADMIN, Role.REVIEWER})
