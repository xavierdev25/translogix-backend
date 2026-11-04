# ruff: noqa: ASYNC210, ASYNC221, ASYNC240, S607, PT012
# Integration test against real AWS: blocking HTTP calls to S3 and the worker subprocess are what
# is being measured, not a hot path.
"""Tests against **real AWS** (CR-011). They are skipped unless explicitly enabled:

    set -a; source ../var/aws-dev-resources.env; set +a
    TRANSLOGIX_AWS_TESTS=1 uv run pytest -m aws tests/aws/test_real_aws.py

They use the active profile's credentials and the resources created by
``infra/aws/provision.sh``. They create and delete their own objects and messages (unique keys).
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI

from app.contracts.common import ContractError, ErrorCode
from app.jobs.outbox import OutboxRelay
from app.jobs.publisher import SqsPublisher
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.modules.rag.service import ReindexReport
from app.shared.storage import (
    S3_CLIENT_CONFIG,
    ObjectConflictError,
    S3ObjectStorage,
    StorageError,
    sha256_hex,
)
from tests.conftest import TEST_DATABASE_URL, TEST_PII_KEY
from tests.db import AppFactory, scalar

pytestmark = [
    pytest.mark.aws,
    pytest.mark.skipif(
        os.environ.get("TRANSLOGIX_AWS_TESTS") != "1", reason="AWS real desactivado"
    ),
]
REGION = os.environ.get("AWS_REGION", "us-east-1")
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"falta {name} (infra/aws/provision.sh)")
    return value


def s3_storage() -> tuple[S3ObjectStorage, Any]:
    client = boto3.client("s3", region_name=REGION, config=S3_CLIENT_CONFIG)
    return S3ObjectStorage(client, env("S3_BUCKET"), env("S3_KMS_KEY_ID")), client


def unique_key() -> str:
    return f"attachments/2026/11/{uuid.uuid4()}.pdf"


async def test_real_s3_kms_conditional_write_presign_and_policy() -> None:
    storage, client = s3_storage()
    bucket, key, digest = env("S3_BUCKET"), unique_key(), sha256_hex(PDF)
    try:
        await storage.put(key, PDF, content_type="application/pdf", sha256=digest)
        await storage.put(
            key, PDF, content_type="application/pdf", sha256=digest
        )  # idempotent retry
        with pytest.raises(ObjectConflictError):
            other = b"%PDF-1.4 otro %%EOF"
            await storage.put(key, other, content_type="application/pdf", sha256=sha256_hex(other))
        head = client.head_object(Bucket=bucket, Key=key)
        url = await storage.presigned_get(
            key, filename="x.pdf", content_type="application/pdf", ttl=60
        )
        assert url is not None
        signed = httpx.get(url, timeout=20)
        unsigned = httpx.get(f"https://{bucket}.s3.{REGION}.amazonaws.com/{key}", timeout=20)

        assert head["ServerSideEncryption"] == "aws:kms"
        assert head["SSEKMSKeyId"] == env("S3_KMS_KEY_ID")
        assert head["Metadata"] == {"sha256": digest}
        assert await storage.get(key) == PDF
        assert (signed.status_code, signed.content) == (200, PDF)  # SigV4 + KMS decrypt
        assert unsigned.status_code == 403  # the bucket is private
    finally:
        await storage.delete(key)
    assert await storage.head(key) is None


async def test_real_s3_rejects_wrong_checksums_and_unencrypted_uploads() -> None:
    storage, client = s3_storage()
    with pytest.raises(StorageError):
        await storage.put(
            unique_key(), PDF, content_type="application/pdf", sha256=sha256_hex(b"otra")
        )
    with pytest.raises(ClientError) as denied:  # bypassing the app: the bucket policy refuses it
        client.put_object(Bucket=env("S3_BUCKET"), Key=unique_key(), Body=PDF)
    assert denied.value.response["Error"]["Code"] == "AccessDenied"


class FailingIngestion:
    def __init__(self) -> None:
        self.calls = 0

    async def reindex(self, **_: Any) -> ReindexReport:
        self.calls += 1
        raise ContractError(ErrorCode.LLM_NO_DISPONIBLE)


async def test_real_sqs_retries_three_times_and_lands_in_the_dlq(make_app: AppFactory) -> None:
    queue, dlq = env("SQS_QUEUE_URL"), env("SQS_DLQ_URL")
    sqs = boto3.client("sqs", region_name=REGION)
    async with make_app(jobs_backend="sqs", sqs_queue_url=queue, sqs_dlq_url=dlq) as app:
        box = app.state.container
        job = await JobService(box.sessions).enqueue_reindex(
            doc_codes=None,
            force=False,
            staff_user_id=None,
            request_id="real-aws",
            now=box.clock.now(),
        )
        assert (
            await OutboxRelay(box.sessions, SqsPublisher(sqs, queue), box.clock).publish_pending()
            >= 1
        )
        ingestion = FailingIngestion()
        worker = JobWorker(
            box.sessions,
            sqs,
            queue_url=queue,
            dlq_url=dlq,
            ingestion=ingestion,
            clock=box.clock,
            embed_model="qwen3-embedding:0.6b",
            embed_dim=1024,
            max_attempts=3,
            visibility_timeout=60,
            wait_seconds=5,
            retry_backoff_seconds=0,
        )
        outcomes: list[str] = []
        for _ in range(8):
            response = sqs.receive_message(
                QueueUrl=queue,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=5,
                VisibilityTimeout=60,
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
            )
            mine = [m for m in response.get("Messages", []) if str(job.id) in m["Body"]]
            outcomes += [await worker.handle(dict(m)) for m in mine]
            if "dead_letter" in outcomes and not mine:
                break  # the receive after the last attempt moved it to the DLQ
        swept = 0
        for _ in range(6):
            swept += await worker.sweep_dlq()
            if swept:
                break
            await asyncio.sleep(5)
        status = await scalar(app, "SELECT status FROM processing_jobs WHERE id = :j", j=job.id)

    assert outcomes[:3] == ["retry", "retry", "dead_letter"]
    assert ingestion.calls == 3
    assert swept >= 1
    assert status == "DEAD_LETTER"


async def test_real_worker_process_reindexes_with_local_ollama(
    make_app: AppFactory, app: FastAPI
) -> None:
    """End to end: job + outbox in PostgreSQL → the real worker (``python -m app.worker once``)
    publishes to SQS, receives, and re-indexes one document with the local Ollama."""
    queue, dlq = env("SQS_QUEUE_URL"), env("SQS_DLQ_URL")
    from app.contracts.rag import DocCode

    box = app.state.container
    job = await JobService(box.sessions).enqueue_reindex(
        doc_codes=[DocCode.DOC_PQRS_2026_04],
        force=True,
        staff_user_id=None,
        request_id="real-e2e",
        now=box.clock.now(),
    )
    child_env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "PII_ENCRYPTION_KEYS": TEST_PII_KEY,
        "JOBS_BACKEND": "sqs",
        "SQS_QUEUE_URL": queue,
        "SQS_DLQ_URL": dlq,
        "OLLAMA_BASE_URL": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "OLLAMA_CHAT_MODEL": "qwen3:8b",
        "OLLAMA_EMBED_MODEL": "qwen3-embedding:0.6b",
    }
    done = None
    for _ in range(4):  # one relay pass + long-polling receive per run
        subprocess.run(
            [sys.executable, "-m", "app.worker", "once"],
            cwd=Path(__file__).resolve().parents[2],
            env=child_env,
            check=True,
            capture_output=True,
            timeout=180,
        )
        done = await scalar(app, "SELECT status FROM processing_jobs WHERE id = :j", j=job.id)
        if done == "DONE":
            break
    result = await scalar(
        app, "SELECT result_json::text FROM processing_jobs WHERE id = :j", j=job.id
    )

    assert done == "DONE"
    assert json.loads(result)["chunks"] > 0


def test_real_emf_lines_become_cloudwatch_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    """The collector is simulated with PutLogEvents (in ECS/EC2 the agent does it); the metric
    extraction is CloudWatch's real one."""
    from app.shared import metrics

    namespace = "TransLogixDevEvidence"
    run = f"run{uuid.uuid4().hex[:8]}"  # this run's own series: an earlier one cannot pass for it
    started = time.time()
    metrics.configure(namespace=namespace, service="api")
    metrics.emit("EvidenceProbe", 1, probe=run)
    metrics.configure(namespace="TransLogix", service="api")
    line = next(line for line in capsys.readouterr().out.splitlines() if namespace in line)
    logs = boto3.client("logs", region_name=REGION)
    stream = f"evidence-{uuid.uuid4().hex[:8]}"
    logs.create_log_stream(logGroupName=env("LOG_GROUP"), logStreamName=stream)
    logs.put_log_events(
        logGroupName=env("LOG_GROUP"),
        logStreamName=stream,
        logEvents=[{"timestamp": int(time.time() * 1000), "message": line}],
    )
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    points: list[Any] = []
    for _ in range(
        36
    ):  # extraction takes seconds to minutes; ListMetrics lags behind, so ask for data
        points = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName="EvidenceProbe",
            Dimensions=[{"Name": "Service", "Value": "api"}, {"Name": "probe", "Value": run}],
            StartTime=started - 120,
            EndTime=time.time() + 60,
            Period=60,
            Statistics=["Sum"],
        )["Datapoints"]
        if points:
            break
        time.sleep(10)

    assert [p["Sum"] for p in points] == [1.0], "CloudWatch no extrajo la métrica EMF"


def test_credentials_are_never_in_the_repository() -> None:
    root = Path(__file__).resolve().parents[3]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    leaked = [
        name
        for name in tracked
        if (root / name).is_file()
        and name.endswith((".py", ".json", ".md", ".sh", ".toml", ".yaml", ".env", ".example"))
        and any(
            marker in (root / name).read_text(errors="ignore")
            for marker in ("AKIA", "aws_secret_access_key")
        )
    ]
    assert leaked == []
    assert base64  # (imported for parity with the encryption tests)
