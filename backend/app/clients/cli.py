"""Administración de clientes empresariales (spec P0 §5.4: comando controlado, sin endpoints).

    uv run python -m app.clients.cli create-client --nit 900123456-7 --razon-social "Cliente"
    uv run python -m app.clients.cli create-key --client-id <uuid> \\
        --scope batches:create --scope batches:read
    uv run python -m app.clients.cli revoke-key --key-id <uuid>
    uv run python -m app.clients.cli set-status --client-id <uuid> --estado INACTIVE
    uv run python -m app.clients.cli show --client-id <uuid>

La API key completa se imprime una sola vez, en ``create-key``; no se puede recuperar.
"""

import argparse
import asyncio
import sys
from uuid import UUID

from app.clients.application.services import ClientAdminService
from app.clients.domain.models import ClientStatus
from app.contracts.common import ContractError
from app.contracts.shipment_batches import ApiScope
from app.db.session import create_engine, create_session_factory
from app.shared.clock import build_clock
from app.shared.config import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.clients.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-client")
    create.add_argument("--nit", required=True)
    create.add_argument("--razon-social", required=True)
    create.add_argument("--tipo-cuenta", default="ESTANDAR")
    key = commands.add_parser("create-key")
    key.add_argument("--client-id", type=UUID, required=True)
    key.add_argument("--scope", action="append", required=True, choices=[s.value for s in ApiScope])
    revoke = commands.add_parser("revoke-key")
    revoke.add_argument("--key-id", type=UUID, required=True)
    status = commands.add_parser("set-status")
    status.add_argument("--client-id", type=UUID, required=True)
    status.add_argument("--estado", required=True, choices=[s.value for s in ClientStatus])
    show = commands.add_parser("show")
    show.add_argument("--client-id", type=UUID, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            admin = ClientAdminService(session, build_clock(settings.clock_override))
            if args.command == "create-client":
                client = await admin.create_client(args.nit, args.razon_social, args.tipo_cuenta)
                print(f"client_id={client.id} nit={client.nit} estado={client.estado}")
            elif args.command == "create-key":
                record, full_key = await admin.create_api_key(
                    args.client_id, [ApiScope(s) for s in args.scope]
                )
                print(f"key_id={record.id} prefix={record.key_prefix}")
                print(f"api_key={full_key}")
                print("Guárdala ahora: no se vuelve a mostrar.")
            elif args.command == "revoke-key":
                if not await admin.revoke_api_key(args.key_id):
                    print("No existe o ya estaba revocada.", file=sys.stderr)
                    return 1
                print("revocada")
            elif args.command == "set-status":
                if not await admin.set_status(args.client_id, ClientStatus(args.estado)):
                    print("Cliente no encontrado.", file=sys.stderr)
                    return 1
                print(f"estado={args.estado}")
            else:
                found = await admin.describe(args.client_id)
                if found is None:
                    print("Cliente no encontrado.", file=sys.stderr)
                    return 1
                client, keys = found
                print(f"{client.id} {client.nit} {client.razon_social} {client.estado}")
                for k in keys:
                    state = "revocada" if k.revoked_at else "activa"
                    scopes = ",".join(sorted(s.value for s in k.scopes))
                    print(f"  key {k.id} prefix={k.key_prefix} {state} scopes={scopes}")
            await session.commit()
    except ContractError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
