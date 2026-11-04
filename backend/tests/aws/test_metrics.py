"""Phase 3 (CR-011): CloudWatch EMF metrics on stdout and structured logs without PII."""

import json
import logging
from typing import Any

import pytest

from app.shared import metrics
from app.shared.logging import PiiRedactingFormatter


def emf_lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.startswith('{"_aws"')]


def test_a_metric_is_one_emf_line_with_closed_dimensions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    metrics.configure(namespace="TransLogix", service="worker")
    metrics.emit("JobMessages", 1, outcome="retry", kind="RAG_REINDEX")
    metrics.emit("JobMessages", 1, outcome="zuleima@correo.co", cliente="Zuleima Parrado")  # PII
    metrics.configure(namespace="TransLogix", service="api")

    first, second = emf_lines(capsys.readouterr().out)
    spec = first["_aws"]["CloudWatchMetrics"][0]

    assert spec["Namespace"] == "TransLogix"
    assert spec["Dimensions"] == [["Service", "kind", "outcome"]]
    assert spec["Metrics"] == [{"Name": "JobMessages", "Unit": "Count"}]
    assert (first["Service"], first["outcome"], first["JobMessages"]) == ("worker", "retry", 1)
    assert "zuleima" not in json.dumps(second)
    assert "Zuleima" not in json.dumps(second)
    assert second["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Service"]]


def test_timer_reports_latency_and_outcome(capsys: pytest.CaptureFixture[str]) -> None:
    metrics.configure(namespace="TransLogix", service="api")
    with metrics.timer("OllamaLatency", operation="embed"):
        pass
    with pytest.raises(RuntimeError), metrics.timer("OllamaLatency", operation="chat"):
        raise RuntimeError

    ok, error = emf_lines(capsys.readouterr().out)

    assert (ok["operation"], ok["outcome"]) == ("embed", "ok")
    assert (error["operation"], error["outcome"]) == ("chat", "error")
    assert error["_aws"]["CloudWatchMetrics"][0]["Metrics"] == [
        {"Name": "OllamaLatency", "Unit": "Milliseconds"}
    ]


def test_metrics_can_be_switched_off(capsys: pytest.CaptureFixture[str]) -> None:
    metrics.configure(namespace="TransLogix", service="api", enabled=False)
    metrics.emit("AttachmentUploads", 1)
    metrics.configure(namespace="TransLogix", service="api", enabled=True)

    assert emf_lines(capsys.readouterr().out) == []


def test_structured_log_fields_are_redacted() -> None:
    record = logging.LogRecord(
        "app.worker", logging.WARNING, __file__, 1, "job failed para 3157779911", None, None
    )
    record.job_kind = "RAG_REINDEX"
    record.error_type = "zuleima@correo.co"  # never expected, but it would be redacted

    entry = json.loads(PiiRedactingFormatter().format(record))

    assert entry["job_kind"] == "RAG_REINDEX"
    assert "3157779911" not in entry["msg"]
    assert "zuleima@correo.co" not in entry["error_type"]
