"""JSON logs with personal data removed before anything is written (rule 7)."""

import json
import logging
from datetime import UTC, datetime

from app.contracts.pii import redact_pii


class PiiRedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_pii(record.getMessage()),
        }
        for key in ("request_id", "trace_id", "channel", "intent"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = str(value)
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: messages and tracebacks may carry user data.
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(PiiRedactingFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
