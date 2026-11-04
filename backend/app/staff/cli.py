"""Administración de usuarios internos (CR-002: comando controlado, sin endpoints).

    uv run python -m app.staff.cli create-user --email asesor@translogix.co \\
        --nombre "Asesora Demo" --role ASESOR
    uv run python -m app.staff.cli issue-token --user-id <uuid> --expires-in-days 90
    uv run python -m app.staff.cli revoke-token --token-id <uuid>
    uv run python -m app.staff.cli set-roles --user-id <uuid> --role ASESOR --role SUPERVISOR
    uv run python -m app.staff.cli set-status --user-id <uuid> --estado INACTIVE
    uv run python -m app.staff.cli show --user-id <uuid>

El token completo se imprime una sola vez, en ``issue-token``; no se puede recuperar.
"""

import argparse
import asyncio
import sys
from uuid import UUID

from app.contracts.common import ContractError
from app.contracts.staff import STAFF_ROLES, Role
from app.db.session import create_engine, create_session_factory
from app.shared.clock import build_clock
from app.shared.config import get_settings
from app.staff.application.services import StaffAdminService
from app.staff.domain.models import TOKEN_MAX_DAYS, StaffStatus

ROLES = sorted(r.value for r in STAFF_ROLES)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.staff.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--nombre", required=True)
    create.add_argument("--role", action="append", required=True, choices=ROLES)
    token = commands.add_parser("issue-token")
    token.add_argument("--user-id", type=UUID, required=True)
    token.add_argument("--expires-in-days", type=int, default=90, help=f"1-{TOKEN_MAX_DAYS}")
    revoke = commands.add_parser("revoke-token")
    revoke.add_argument("--token-id", type=UUID, required=True)
    roles = commands.add_parser("set-roles")
    roles.add_argument("--user-id", type=UUID, required=True)
    roles.add_argument("--role", action="append", required=True, choices=ROLES)
    status = commands.add_parser("set-status")
    status.add_argument("--user-id", type=UUID, required=True)
    status.add_argument("--estado", required=True, choices=[s.value for s in StaffStatus])
    pw = commands.add_parser("set-password")
    pw.add_argument("--user-id", type=UUID, required=True)
    pw.add_argument("--password", required=True, help="Mínimo 8 caracteres")
    show = commands.add_parser("show")
    show.add_argument("--user-id", type=UUID, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            admin = StaffAdminService(session, build_clock(settings.clock_override))
            if args.command == "create-user":
                user = await admin.create_user(
                    args.email, args.nombre, [Role(r) for r in args.role]
                )
                roles = ",".join(sorted(r.value for r in user.roles))
                print(f"user_id={user.id} email={user.email} roles={roles}")
            elif args.command == "issue-token":
                record, full_token = await admin.issue_token(
                    args.user_id, expires_in_days=args.expires_in_days
                )
                print(f"token_id={record.id} prefix={record.key_prefix} expira={record.expires_at}")
                print(f"token={full_token}")
                print("Guárdalo ahora: no se vuelve a mostrar.")
            elif args.command == "revoke-token":
                if not await admin.revoke_token(args.token_id):
                    print("No existe o ya estaba revocado.", file=sys.stderr)
                    return 1
                print("revocado")
            elif args.command == "set-roles":
                if not await admin.set_roles(args.user_id, [Role(r) for r in args.role]):
                    print("Usuario no encontrado.", file=sys.stderr)
                    return 1
                print(f"roles={','.join(sorted(args.role))}")
            elif args.command == "set-status":
                if not await admin.set_status(args.user_id, StaffStatus(args.estado)):
                    print("Usuario no encontrado.", file=sys.stderr)
                    return 1
                print(f"estado={args.estado}")
            elif args.command == "set-password":
                if not await admin.set_password(args.user_id, args.password):
                    print("Usuario no encontrado.", file=sys.stderr)
                    return 1
                print("Contraseña actualizada.")
            else:
                found = await admin.describe(args.user_id)
                if found is None:
                    print("Usuario no encontrado.", file=sys.stderr)
                    return 1
                user, tokens = found
                roles = ",".join(sorted(r.value for r in user.roles))
                print(f"{user.id} {user.email} {user.nombre} {user.estado} roles={roles}")
                for t in tokens:
                    state = "revocado" if t.revoked_at else f"vence {t.expires_at:%Y-%m-%d}"
                    print(f"  token {t.id} prefix={t.key_prefix} {state}")
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
