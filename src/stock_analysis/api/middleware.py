"""Custom API middleware for legal disclaimers, security headers, and safe error handling."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from stock_analysis.api.sanitizer import SAFE_INTERNAL_ERROR_MESSAGE
from stock_analysis.api.schemas import DISCLAIMER_TEXT
from stock_analysis.security.headers import apply_security_headers

logger = logging.getLogger("stock_analysis.api")


class DisclaimerAndSecurityMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing mandatory disclaimer headers and standard security headers.

    Prevents leaking internal stack traces, database schema specifics, or secrets
    to clients in case of unexpected exceptions.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled server exception processing request path: %s", request.url.path
            )
            now_iso = datetime.now(UTC).isoformat()
            error_payload = {
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": SAFE_INTERNAL_ERROR_MESSAGE,
                    "timestamp": now_iso,
                }
            }
            response = JSONResponse(status_code=500, content=error_payload)

        # Enforce mandatory legal disclaimer response header
        response.headers["X-Disclaimer"] = DISCLAIMER_TEXT

        # Enforce security headers, CSP, and HSTS
        apply_security_headers(response, request)

        return response
