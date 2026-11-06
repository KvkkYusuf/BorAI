"""Distributed tracing and correlation context for requests, jobs, and market pipeline."""

from __future__ import annotations

import contextvars
import secrets
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

TRACEPARENT_HEADER = "traceparent"
REQUEST_ID_HEADER = "x-request-id"
TRACE_ID_HEADER = "x-trace-id"


@dataclass(frozen=True)
class SpanContext:
    """Immutable representation of a distributed trace span."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    sampled: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_w3c_traceparent(self) -> str:
        """Format as W3C Trace Context traceparent string (version-traceid-spanid-flags)."""
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id.lower()}-{self.span_id.lower()}-{flags}"


_current_span_context: contextvars.ContextVar[SpanContext | None] = contextvars.ContextVar(
    "current_span_context", default=None
)


def generate_trace_id() -> str:
    """Generate a 128-bit (32 hex chars) random trace ID according to W3C specification."""
    return secrets.token_hex(16)


def generate_span_id() -> str:
    """Generate a 64-bit (16 hex chars) random span ID according to W3C specification."""
    return secrets.token_hex(8)


def get_current_span() -> SpanContext | None:
    """Return the current active span context in the current execution context."""
    return _current_span_context.get()


def get_current_trace_id() -> str | None:
    """Helper to retrieve current active trace ID if present."""
    ctx = _current_span_context.get()
    return ctx.trace_id if ctx else None


def set_span_context(ctx: SpanContext | None) -> contextvars.Token[SpanContext | None]:
    """Explicitly set the active span context, returning a reset token."""
    return _current_span_context.set(ctx)


def reset_span_context(token: contextvars.Token[SpanContext | None]) -> None:
    """Reset the span context using a previous token."""
    _current_span_context.reset(token)


def parse_w3c_traceparent(header_val: str | None) -> SpanContext | None:
    """Parse a W3C traceparent header value.

    Format: 00-{trace_id_32_hex}-{span_id_16_hex}-{flags_2_hex}
    """
    if not header_val or not isinstance(header_val, str):
        return None

    parts = header_val.strip().split("-")
    if len(parts) != 4:
        return None

    version, trace_id, parent_span_id, flags = parts
    if version != "00":
        # Unsupported or invalid version
        return None

    if len(trace_id) != 32 or not all(c in "0123456789abcdefABCDEF" for c in trace_id):
        return None
    if trace_id == "0" * 32:
        return None  # All zeroes is invalid per W3C spec

    if len(parent_span_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in parent_span_id):
        return None
    if parent_span_id == "0" * 16:
        return None  # All zeroes is invalid per W3C spec

    sampled = flags == "01"
    # Child span created for the current execution unit
    new_span_id = generate_span_id()
    return SpanContext(
        trace_id=trace_id.lower(),
        span_id=new_span_id,
        parent_span_id=parent_span_id.lower(),
        sampled=sampled,
    )


def extract_trace_context(headers: Mapping[str, str]) -> SpanContext:
    """Extract trace context from HTTP headers, or initialize a new root trace context.

    Supports W3C `traceparent` and legacy `X-Request-ID` / `X-Trace-ID`.
    """
    # Normalize headers to lowercase lookup
    lower_headers = {k.lower(): v for k, v in headers.items()}

    # 1. Check W3C traceparent
    traceparent = lower_headers.get(TRACEPARENT_HEADER)
    if traceparent:
        parsed = parse_w3c_traceparent(traceparent)
        if parsed:
            return parsed

    # 2. Check X-Trace-ID or X-Request-ID
    custom_trace = lower_headers.get(TRACE_ID_HEADER) or lower_headers.get(REQUEST_ID_HEADER)
    if custom_trace and len(custom_trace) >= 16:
        # Normalize to 32 chars hex if valid hex or hash
        clean_trace = custom_trace.replace("-", "").lower()
        if len(clean_trace) == 32 and all(c in "0123456789abcdef" for c in clean_trace):
            return SpanContext(trace_id=clean_trace, span_id=generate_span_id(), sampled=True)

    # 3. Default: brand new root trace
    return SpanContext(
        trace_id=generate_trace_id(),
        span_id=generate_span_id(),
        parent_span_id=None,
        sampled=True,
    )


def inject_trace_context(
    ctx: SpanContext | None, headers: dict[str, str] | None = None
) -> dict[str, str]:
    """Inject active trace context into outbound HTTP request headers."""
    target = {} if headers is None else dict(headers)
    active = ctx or get_current_span()
    if active:
        target[TRACEPARENT_HEADER] = active.to_w3c_traceparent()
        target[TRACE_ID_HEADER] = active.trace_id
        target[REQUEST_ID_HEADER] = active.trace_id
    return target


@contextmanager
def trace_span(
    name: str, attributes: dict[str, Any] | None = None
) -> Generator[SpanContext, None, None]:
    """Context manager to spawn a new child span or root span within the active context."""
    parent = get_current_span()
    if parent:
        span = SpanContext(
            trace_id=parent.trace_id,
            span_id=generate_span_id(),
            parent_span_id=parent.span_id,
            sampled=parent.sampled,
            attributes={**parent.attributes, **(attributes or {}), "span_name": name},
        )
    else:
        span = SpanContext(
            trace_id=generate_trace_id(),
            span_id=generate_span_id(),
            parent_span_id=None,
            sampled=True,
            attributes={"span_name": name, **(attributes or {})},
        )

    token = set_span_context(span)
    try:
        yield span
    finally:
        reset_span_context(token)
