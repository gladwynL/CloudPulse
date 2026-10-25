"""Structured logging built on the standard library.

JSON output (one object per line on stdout) is the default so logs can be ingested
by Docker log drivers and Azure Monitor / Log Analytics without extra parsing.
A human-readable text format is available for local debugging.
"""

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

# Attributes present on every LogRecord; anything else was passed via ``extra=``.
# ``color_message`` is uvicorn's ANSI-colored duplicate of the message.
_RESERVED_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    | {"message", "asctime", "taskName", "color_message"}
)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "environment": self._environment,
        }
        for key, value in vars(record).items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    service: str = "cloudpulse-api",
    environment: str = "local",
) -> None:
    """Configure root and uvicorn loggers to share one handler and format."""
    formatter: dict[str, Any]
    if fmt == "json":
        formatter = {
            "()": JsonFormatter,
            "service": service,
            "environment": environment,
        }
    else:
        formatter = {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"}

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": formatter},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": level, "handlers": ["stdout"]},
            "loggers": {
                # Route uvicorn through the root handler; our middleware logs requests.
                "uvicorn": {"level": level, "handlers": [], "propagate": True},
                "uvicorn.error": {"level": level, "handlers": [], "propagate": True},
                "uvicorn.access": {"level": "WARNING", "handlers": [], "propagate": True},
            },
        }
    )
