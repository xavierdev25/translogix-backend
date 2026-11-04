"""``python -m app.privacy.cli``: keys, retention and erasure of personal data (CR-010).

generate-key                       new Fernet key (shown once; store it in the secret manager)
purge-conversations --older-than-days N [--execute]   without --execute it only counts
erase-recipient --shipment-id ID   data subject's erasure request
rotate-keys                        re-encrypt with the first key of PII_ENCRYPTION_KEYS
"""

import argparse
import asyncio
import sys
from uuid import UUID

from app.db.session import create_engine, create_session_factory
from app.privacy import service
from app.shared.clock import SystemClock
from app.shared.config import get_settings
from app.shared.crypto import configure, generate_key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.privacy.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate-key")
    purge = commands.add_parser("purge-conversations")
    purge.add_argument("--older-than-days", type=int, required=True)
    purge.add_argument("--execute", action="store_true", help="sin esta opción solo cuenta")
    erase = commands.add_parser("erase-recipient")
    erase.add_argument("--shipment-id", type=UUID, required=True)
    commands.add_parser("rotate-keys")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    keys = settings.pii_encryption_keys
    configure(keys.get_secret_value() if keys else None)
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            now = SystemClock().now()
            if args.command == "purge-conversations":
                report = await service.purge_conversations(
                    session, older_than_days=args.older_than_days, now=now, execute=args.execute
                )
                verb = "eliminadas" if report.ejecutado else "a eliminar (sin --execute)"
                print(
                    f"conversaciones {verb}: {report.conversaciones}; mensajes: {report.mensajes}"
                )
            elif args.command == "erase-recipient":
                if not await service.erase_recipient(session, args.shipment_id, now):
                    print("Envío sin destinatario.", file=sys.stderr)
                    return 1
                print("destinatario eliminado")
            elif args.command == "rotate-keys":
                print(f"filas recifradas: {await service.rotate_keys(session)}")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "generate-key":
        print(generate_key())
        print(
            "Guárdala en el gestor de secretos o en .env (nunca en el repositorio).",
            file=sys.stderr,
        )
        return 0
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
