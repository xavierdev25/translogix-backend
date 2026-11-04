"""CloudWatch metrics through the Embedded Metric Format (EMF), written to stdout (CR-011).

**No call to AWS from the request path.** Each metric is one JSON line with an ``_aws`` block.
The environment's collector (CloudWatch agent, ECS ``awslogs`` or Lambda) ships it to CloudWatch
Logs, and CloudWatch extracts the metric.

**Dimensions are closed vocabularies** (operation, outcome, backend): a value with spaces, ``@``
or more than 64 characters is dropped. A dimension therefore cannot carry PII, a token or an id
of high cardinality.
"""

import json
import logging
import re
import sys
import time

_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_UNITS = frozenset({"Count", "Milliseconds", "Bytes", "Seconds", "None"})
logger = logging.getLogger("app.metrics")
_state = {"namespace": "TransLogix", "service": "api", "enabled": True}


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Always writes to the *current* ``sys.stdout`` (it may be replaced after start-up)."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


class _EmfFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(getattr(record, "emf", {}), ensure_ascii=False, separators=(",", ":"))


def configure(*, namespace: str, service: str, enabled: bool = True) -> None:
    _state.update(namespace=namespace, service=service, enabled=enabled)
    if not any(isinstance(h.formatter, _EmfFormatter) for h in logger.handlers):
        handler = _StdoutHandler(sys.stdout)
        handler.setFormatter(_EmfFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # never mixed with (or redacted as) the application log


def emit(name: str, value: float, unit: str = "Count", **dimensions: str) -> None:
    if not _state["enabled"]:
        return
    dims = {k: v for k, v in dimensions.items() if isinstance(v, str) and _VALUE.match(v)}
    keys = ["Service", *sorted(dims)]
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": _state["namespace"],
                    "Dimensions": [keys],
                    "Metrics": [{"Name": name, "Unit": unit if unit in _UNITS else "None"}],
                }
            ],
        },
        "Service": _state["service"],
        **dims,
        name: value,
    }
    logger.info("metric", extra={"emf": record})


class timer:  # noqa: N801 - used as a context manager: ``with metrics.timer("x", op="y")``
    """Milliseconds of a block, emitted with ``outcome=ok|error``."""

    def __init__(self, name: str, **dimensions: str) -> None:
        self._name, self._dims = name, dimensions

    def __enter__(self) -> "timer":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        elapsed = (time.perf_counter() - self._started) * 1000
        outcome = "ok" if exc_type is None else "error"
        emit(self._name, round(elapsed, 1), "Milliseconds", **self._dims, outcome=outcome)
