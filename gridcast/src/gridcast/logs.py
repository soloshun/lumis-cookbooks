"""Structured logging.

Services log one JSON object per line to stdout. The OpenTelemetry Collector tails container
logs, parses the JSON and ships it to Loki, so trace/span IDs written here let Grafana (and,
later, Lumis) pivot from a log line to its trace.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, version: str) -> None:
        super().__init__()
        self.service = service
        self.version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "service": self.service,
            "version": self.version,
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            payload["trace_id"] = format(span.trace_id, "032x")
            payload["span_id"] = format(span.span_id, "016x")
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str, version: str, level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter(service, version))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Uvicorn installs its own handlers; route them through ours and drop per-request noise.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    for noisy in ("httpx", "httpcore", "botocore", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel("WARNING")
