"""``MessagePublisher``: who carries outbox messages to the queue."""

import asyncio
import json
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from app.shared import metrics


class PublishError(Exception):
    """The queue did not accept the message; the outbox keeps it for the next pass."""


class MessagePublisher(Protocol):
    async def publish(self, message_id: str, body: dict[str, Any]) -> None: ...


class SqsPublisher:
    """Standard queue: at-least-once delivery, so duplicates are possible. The consumer is
    idempotent (job state in PostgreSQL)."""

    def __init__(self, client: Any, queue_url: str) -> None:
        self._sqs, self._url = client, queue_url

    async def publish(self, message_id: str, body: dict[str, Any]) -> None:
        try:
            with metrics.timer("QueueLatency", operation="send"):
                await asyncio.to_thread(
                    self._sqs.send_message,
                    QueueUrl=self._url,
                    MessageBody=json.dumps(body, separators=(",", ":")),
                    MessageAttributes={
                        "outbox_id": {"DataType": "String", "StringValue": message_id}
                    },
                )
        except (BotoCoreError, ClientError) as error:
            metrics.emit("InfrastructureErrors", 1, component="sqs", operation="send")
            raise PublishError(type(error).__name__) from error
