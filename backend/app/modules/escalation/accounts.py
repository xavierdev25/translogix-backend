"""Shipper accounts for CRIT-004, read from the SIMULATED fixture (DEC-ACC1).

The PDF gives no account master data; the fixture marks itself ``_simulado``
and the team, not the code, decides which NIT is CORPORATIVO_GOLD.
"""

import json
from pathlib import Path

from app.contracts.ports import AccountProfile


class FixtureAccountDirectory:
    """``AccountDirectoryPort`` over ``cuentas_simuladas.json``."""

    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.simulated = bool(raw.get("_simulado", False))
        self._accounts = {
            nit: AccountProfile.model_validate(profile)
            for nit, profile in raw.get("cuentas", {}).items()
        }

    async def get_account(self, nit: str) -> AccountProfile | None:
        return self._accounts.get(nit)
