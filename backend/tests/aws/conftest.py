"""AWS simulated with moto: no credentials or real calls. Real tests live in ``test_real_aws.py``
and only run with ``TRANSLOGIX_AWS_TESTS=1``."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.shared.storage import S3_CLIENT_CONFIG

REGION = "us-east-1"
BUCKET = "translogix-test-attachments"


@dataclass(frozen=True)
class FakeAws:
    s3: Any
    sqs: Any
    bucket: str
    kms_key_id: str
    queue_url: str
    dlq_url: str


@pytest.fixture
def fake_aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAws]:
    # moto convention: fake values so boto3 never picks a real profile up.
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION, config=S3_CLIENT_CONFIG)
        s3.create_bucket(Bucket=BUCKET)
        key_id = boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["KeyId"]
        sqs = boto3.client("sqs", region_name=REGION)
        dlq = sqs.create_queue(QueueName="jobs-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"][
            "QueueArn"
        ]
        queue = sqs.create_queue(
            QueueName="jobs",
            Attributes={
                "VisibilityTimeout": "0",  # tests see a failed message again immediately
                "RedrivePolicy": f'{{"deadLetterTargetArn":"{dlq_arn}","maxReceiveCount":"3"}}',
            },
        )["QueueUrl"]
        yield FakeAws(s3, sqs, BUCKET, key_id, queue, dlq)
