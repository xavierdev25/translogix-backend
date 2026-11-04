"""Perfiles tarifarios (CR-003): cargar versiones, activarlas y consultarlas.

    uv run python -m app.tariffs.cli show
    uv run python -m app.tariffs.cli load --file perfil.json      # queda INACTIVE
    uv run python -m app.tariffs.cli activate --profile-id <uuid>  # retira el activo del mismo tipo
    uv run python -m app.tariffs.cli deactivate --profile-id <uuid>

Una versión cargada no se edita: para cambiar una regla se carga la versión siguiente.
Formato del JSON: ``TariffProfileDefinition`` (``app.tariffs.application.service``).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.contracts.common import ContractError
from app.db.session import create_engine, create_session_factory
from app.modules.quotation.data import TariffData
from app.shared.clock import build_clock
from app.shared.config import get_settings
from app.tariffs.application.service import TariffAdminService, TariffProfileDefinition


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.tariffs.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    load = commands.add_parser("load")
    load.add_argument("--file", type=Path, required=True)
    for name in ("activate", "deactivate"):
        commands.add_parser(name).add_argument("--profile-id", type=UUID, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    source = settings.data_source_dir
    tariffs = TariffData(
        source / "matriz_tarifas_y_fletes.csv", source / "directorio_cobertura_y_tiempos.json"
    )
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            admin = TariffAdminService(session, tariffs, build_clock(settings.clock_override))
            if args.command == "load":
                definition = TariffProfileDefinition.model_validate(
                    json.loads(args.file.read_text(encoding="utf-8"))
                )
                profile = await admin.load(definition)
                print(f"profile_id={profile.id} {profile.name} v{profile.version} INACTIVE")
            elif args.command == "activate":
                profile = await admin.activate(args.profile_id)
                print(f"activo: {profile.name} v{profile.version} ({profile.environment_type})")
            elif args.command == "deactivate":
                await admin.deactivate(args.profile_id)
                print("inactivo")
            else:
                for profile, rules in await admin.listing():
                    until = (
                        f"{profile.effective_until:%Y-%m-%d}" if profile.effective_until else "-"
                    )
                    print(
                        f"{profile.id} {profile.name} v{profile.version} "
                        f"{profile.environment_type} {profile.status} IVA={profile.iva_pct}% "
                        f"vigente {profile.effective_from:%Y-%m-%d} → {until}"
                    )
                    for r in rules:
                        print(
                            f"  {r.destination_city_code}: base {r.base_price_cop} hasta "
                            f"{r.included_weight_kg} kg, excedente {r.excess_price_per_kg_cop}/kg"
                        )
            await session.commit()
    except (ContractError, ValidationError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
