"""``python -m app.storage.cli``: move attachments from ``var/attachments`` to S3 (CR-011).

    copy [--limit N] [--dry-run]   copy, check the hash and point each row to S3
    verify                         download the copies and confirm their SHA-256
    cleanup [--confirm]            delete the VERIFIED local originals (without --confirm it counts)

Needs ``ATTACHMENTS_STORAGE``, ``S3_BUCKET`` and ``S3_KMS_KEY_ID``. AWS credentials come from the
profile or the role.
"""

import argparse
import asyncio
import sys
from dataclasses import asdict

from app.db.session import create_engine, create_session_factory
from app.shared.clock import SystemClock
from app.shared.config import get_settings
from app.shared.crypto import configure
from app.shared.storage import LocalObjectStorage, build_storage
from app.storage import migration


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.storage.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    copy = commands.add_parser("copy")
    copy.add_argument("--limit", type=int, default=500)
    copy.add_argument("--dry-run", action="store_true")
    commands.add_parser("verify")
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--confirm", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    keys = settings.pii_encryption_keys
    configure(keys.get_secret_value() if keys else None)
    target = build_storage(settings.model_copy(update={"attachments_storage": "s3"}))
    source = LocalObjectStorage(settings.attachments_dir)
    engine = create_engine(settings)
    now = SystemClock().now()
    try:
        async with create_session_factory(engine)() as session:
            if args.command == "copy":
                report = await migration.copy(
                    session, source, target, now=now, limit=args.limit, dry_run=args.dry_run
                )
            elif args.command == "verify":
                report = await migration.verify(session, source, target, now=now)
            else:
                report = await migration.cleanup(session, source, confirm=args.confirm)
    finally:
        await engine.dispose()
    print(asdict(report))
    return 1 if report.fallidos else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
