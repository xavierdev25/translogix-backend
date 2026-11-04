"""``python -m app.worker``: SQS document worker (CR-011).

    run                 outbox relay + consumer (long polling), until SIGTERM/SIGINT
    once                one relay pass + one receive (smoke tests)
    sweep-dlq           records and empties the DLQ
    retry --job-id ID   re-queues a DEAD_LETTER or FAILED job

It uses the same container as the API (``build_container``): same ``IngestionService``, same
Ollama and same PostgreSQL. It needs ``JOBS_BACKEND=sqs``, ``SQS_QUEUE_URL`` and AWS credentials
from the profile or role.
"""

import argparse
import asyncio
import signal
import sys
from uuid import UUID

import boto3

from app.jobs.outbox import OutboxRelay
from app.jobs.publisher import SqsPublisher
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.main import build_container
from app.shared import metrics
from app.shared.config import get_settings
from app.shared.logging import configure_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    commands.add_parser("once")
    commands.add_parser("sweep-dlq")
    retry = commands.add_parser("retry")
    retry.add_argument("--job-id", type=UUID, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.jobs_backend != "sqs" or not settings.sqs_queue_url:
        print("JOBS_BACKEND=sqs y SQS_QUEUE_URL son obligatorios para el worker.", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    metrics.configure(
        namespace=settings.metrics_namespace, service="worker", enabled=settings.metrics_enabled
    )
    box = build_container(settings)
    if box.ingestion is None:
        print(
            "OLLAMA_BASE_URL es obligatorio: el worker indexa con el mismo Ollama.", file=sys.stderr
        )
        return 2
    sqs = boto3.client("sqs", region_name=settings.aws_region)
    relay = OutboxRelay(box.sessions, SqsPublisher(sqs, settings.sqs_queue_url), box.clock)
    worker = JobWorker(
        box.sessions,
        sqs,
        queue_url=settings.sqs_queue_url,
        dlq_url=settings.sqs_dlq_url,
        ingestion=box.ingestion,
        clock=box.clock,
        embed_model=settings.ollama_embed_model or "",
        embed_dim=settings.embed_dim,
        max_attempts=settings.worker_max_attempts,
        visibility_timeout=settings.worker_visibility_timeout_seconds,
        wait_seconds=settings.worker_wait_seconds,
    )
    try:
        if args.command == "retry":
            ok = await JobService(box.sessions).requeue(args.job_id, box.clock.now())
            print("reencolado" if ok else "No existe o no está en DEAD_LETTER/FAILED.")
            return 0 if ok else 1
        if args.command == "sweep-dlq":
            print(f"mensajes registrados desde la DLQ: {await worker.sweep_dlq()}")
            return 0
        if args.command == "once":
            published = await relay.publish_pending()
            handled = await worker.poll_once()
            print(f"publicados={published} procesados={handled}")
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        while not stop.is_set():
            await relay.publish_pending()
            await worker.poll_once()  # long polling: blocks up to WaitTimeSeconds
        return 0
    finally:
        for engine in (box.engine, box.rag_engine, box.limiter_engine):
            if engine is not None:
                await engine.dispose()
        for client in (box.llm, box.embedding):
            if client is not None:
                await client.aclose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
