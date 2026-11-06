"""Structured JSON logging with trace context correlation and PII redaction."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from stock_analysis.observability.tracing import get_current_span
from stock_analysis.security.logging_sanitizer import (
    SensitiveDataFilter,
    sanitize_data_structure,
    sanitize_text,
)


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects with trace context and sanitization."""

    def __init__(
        self,
        service_name: str = "stock_analysis",
        environment: str | None = None,
    ) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment or os.getenv("APP_ENV", "production")

    def format(self, record: logging.LogRecord) -> str:
        # Resolve exception info if requested
        if getattr(record, "exc_info", None) is True:
            record.exc_info = sys.exc_info()

        # Format message safely
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        # Sanitize message string
        clean_message = sanitize_text(message)

        # Base structured fields
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": clean_message,
            "service": self.service_name,
            "environment": self.environment,
            "source": {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            },
        }

        # Correlate distributed trace context
        span = get_current_span()
        if span:
            log_entry["trace_id"] = span.trace_id
            log_entry["span_id"] = span.span_id
            if span.parent_span_id:
                log_entry["parent_span_id"] = span.parent_span_id
        elif hasattr(record, "trace_id"):
            log_entry["trace_id"] = str(record.trace_id)

        # Add exception details if present
        if record.exc_info and record.exc_info[0] is not None:
            exc_type = record.exc_info[0].__name__
            exc_msg = str(record.exc_info[1])
            formatted_tb = self.formatException(record.exc_info)
            log_entry["error"] = {
                "type": exc_type,
                "message": sanitize_text(exc_msg),
                "stacktrace": sanitize_text(formatted_tb),
            }

        # Include custom extra fields passed to logger.info(..., extra={...})
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "asctime",
            "trace_id",
            "span_id",
        }

        extra_fields: dict[str, Any] = {}
        for key, val in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                extra_fields[key] = sanitize_data_structure(val)

        if extra_fields:
            log_entry["extra"] = extra_fields

        return json.dumps(log_entry, default=str, ensure_ascii=False)


def configure_structured_logging(
    level: int = logging.INFO,
    service_name: str = "stock_analysis",
    logger_names: list[str] | None = None,
) -> logging.Handler:
    """Configure structured JSON logging handler with security redaction filter."""
    formatter = StructuredJsonFormatter(service_name=service_name)
    filter_instance = SensitiveDataFilter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(filter_instance)
    handler.setLevel(level)

    targets = logger_names or ["stock_analysis", "uvicorn.access", "uvicorn.error"]
    for name in targets:
        lgr = logging.getLogger(name)
        lgr.setLevel(level)
        lgr.addHandler(handler)
        if not any(isinstance(f, SensitiveDataFilter) for f in lgr.filters):
            lgr.addFilter(filter_instance)

    return handler
