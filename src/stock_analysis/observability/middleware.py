"""FastAPI/Starlette middleware for distributed tracing, metrics collection, and request logs."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from stock_analysis.observability.metrics import (
    HTTP_ERRORS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)
from stock_analysis.observability.tracing import (
    REQUEST_ID_HEADER,
    TRACE_ID_HEADER,
    TRACEPARENT_HEADER,
    extract_trace_context,
    reset_span_context,
    set_span_context,
)
from stock_analysis.observability.web_vitals_regression import PERFORMANCE_DETECTOR

logger = logging.getLogger("stock_analysis.observability")


def _normalize_endpoint_group(path: str) -> str:
    """Group API paths into coarse-grained endpoint categories for latency baselines."""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        return f"/api/v1/{parts[2]}"
    if len(parts) >= 1:
        return f"/{parts[0]}"
    return "/"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Binds distributed trace context, gathers latency metrics, and logs request lifecycle."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Extract or create W3C trace context
        span = extract_trace_context(request.headers)
        token = set_span_context(span)

        # Attach to request state for downstream handlers
        request.state.trace_id = span.trace_id
        request.state.span_id = span.span_id

        start_time = time.perf_counter()
        status_code = 500
        path = request.url.path

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            HTTP_ERRORS_TOTAL.inc(labels={"method": request.method, "path": path})
            raise
        finally:
            duration_sec = time.perf_counter() - start_time
            duration_ms = duration_sec * 1000.0

            # Record standard metrics
            labels = {
                "method": request.method,
                "path": path,
                "status": str(status_code),
            }
            HTTP_REQUESTS_TOTAL.inc(labels=labels)
            HTTP_REQUEST_DURATION_SECONDS.observe(
                duration_sec, labels={"method": request.method, "path": path}
            )

            if status_code >= 500:
                HTTP_ERRORS_TOTAL.inc(labels={"method": request.method, "path": path})

            # Record API latency into rolling p95 performance regression detector
            endpoint_group = _normalize_endpoint_group(path)
            PERFORMANCE_DETECTOR.record_api_latency(
                endpoint_group=endpoint_group,
                latency_ms=duration_ms,
                status_code=status_code,
            )

            # Emit structured access log
            logger.info(
                "%s %s -> %d (%.2fms)",
                request.method,
                path,
                status_code,
                duration_ms,
                extra={
                    "http": {
                        "method": request.method,
                        "path": path,
                        "status_code": status_code,
                        "duration_ms": round(duration_ms, 2),
                    },
                    "trace_id": span.trace_id,
                    "span_id": span.span_id,
                },
            )

            # Reset contextvar token
            reset_span_context(token)

        # Inject tracing headers into response
        response.headers[TRACEPARENT_HEADER] = span.to_w3c_traceparent()
        response.headers[TRACE_ID_HEADER] = span.trace_id
        response.headers[REQUEST_ID_HEADER] = span.trace_id

        return response
