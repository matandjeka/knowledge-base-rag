"""Structured JSON logging configuration."""

import json
import logging
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Render standard log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record without including sensitive application data."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "trace_id",
            "workspace_id",
            "user_id",
            "retrieval_mode",
            "routing_intent",
            "selected_retrievers",
            "routing_confidence",
            "routing_fallback",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a structured, PII-redacting stdout handler."""
    from app.core.redaction import install_redaction

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    install_redaction(handler)
    logging.basicConfig(level=level, handlers=[handler], force=True)
