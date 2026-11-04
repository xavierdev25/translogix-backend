"""Internal staff identities (CR-002, added in 1.4.0).

Advisors, supervisors, logistics operators and specialised reviewers authenticate
with a personal token ``tlxs_<prefix>_<secret>`` sent as ``Authorization: Bearer``
(OpenAPI scheme ``StaffToken``). It is a different credential family from the B2B
API key (``tlx_…``): neither is accepted where the other is expected. Users and
tokens are managed with ``python -m app.staff.cli``; there are no admin endpoints.
"""

from enum import StrEnum


class StaffRole(StrEnum):
    ASESOR = "ASESOR"
    SUPERVISOR = "SUPERVISOR"
    OPERADOR_LOGISTICO = "OPERADOR_LOGISTICO"
    REVISOR_ESPECIALIZADO = "REVISOR_ESPECIALIZADO"
