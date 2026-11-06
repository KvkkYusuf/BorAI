"""FastAPI application factory, global exception handling, and OpenAPI configuration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from stock_analysis.api.cache import HttpCacheMiddleware
from stock_analysis.api.middleware import DisclaimerAndSecurityMiddleware
from stock_analysis.api.routers import (
    admin,
    alerts,
    analytics,
    auth,
    beta,
    forecasts,
    legal,
    market,
    support,
    system,
    ui,
    user,
)
from stock_analysis.api.sanitizer import (
    SAFE_DATABASE_ERROR_MESSAGE,
    SAFE_GENERIC_ERROR_MESSAGE,
    SAFE_INTERNAL_ERROR_MESSAGE,
    sanitize_error_message,
    sanitize_validation_error_loc,
)
from stock_analysis.api.schemas import DISCLAIMER_TEXT
from stock_analysis.api.versioning import (
    API_SEMANTIC_VERSION,
    SUPPORTED_MAJOR_VERSIONS,
    ApiVersioningMiddleware,
)
from stock_analysis.config import get_settings
from stock_analysis.observability import ObservabilityMiddleware
from stock_analysis.security.logging_sanitizer import configure_secure_logging

logger = logging.getLogger("stock_analysis.api")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    configure_secure_logging()
    settings = get_settings()

    app = FastAPI(
        title="Borsa Analiz Sistemi API",
        version=API_SEMANTIC_VERSION,
        description=(
            "Borsa Analiz Sistemi - Bilgilendirme ve Teknik Analiz Platformu.\n\n"
            f"**YASAL UYARI:** {DISCLAIMER_TEXT}\n\n"
            "Bu sistem kisiye ozel portfoy yonetimi veya yatirim tavsiyesi icermez."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Register middleware
    # Execution order (inside-out):
    # ApiVersioning -> HttpCache -> Observability -> Disclaimer -> CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allowed_methods,
        allow_headers=settings.cors_allowed_headers,
        expose_headers=settings.cors_exposed_headers,
    )
    app.add_middleware(DisclaimerAndSecurityMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(HttpCacheMiddleware)
    app.add_middleware(ApiVersioningMiddleware)

    # --- Exception Handlers ---

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Handle standard HTTPExceptions returning unified error schema with sanitized messages."""
        now_iso = datetime.now(UTC).isoformat()
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", "HTTP_ERROR")
            raw_msg = exc.detail.get("message", str(exc.detail))
        else:
            code = "HTTP_ERROR"
            raw_msg = str(exc.detail)

        safe_message = sanitize_error_message(raw_msg, fallback_message=SAFE_GENERIC_ERROR_MESSAGE)

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": safe_message,
                    "timestamp": now_iso,
                }
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Handle Pydantic validation errors cleanly without leaking internals or credentials."""
        now_iso = datetime.now(UTC).isoformat()
        errors = exc.errors()
        err_msg = "Gecersiz istek verisi veya eksik parametre."
        if errors:
            first_err = errors[0]
            loc_tuple = tuple(first_err.get("loc", []))
            loc = sanitize_validation_error_loc(loc_tuple)
            raw_msg = first_err.get("msg", "Gecersiz deger")
            safe_msg = sanitize_error_message(
                raw_msg, fallback_message="Gecersiz format veya deger."
            )
            err_msg = f"{loc}: {safe_msg}"

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": err_msg,
                    "timestamp": now_iso,
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        """Prevent database schema, table names, or raw SQL queries from leaking in errors."""
        logger.error(
            "Database exception encountered processing request at %s: %s",
            request.url.path,
            type(exc).__name__,
            exc_info=True,
        )
        now_iso = datetime.now(UTC).isoformat()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "DATABASE_ERROR",
                    "message": SAFE_DATABASE_ERROR_MESSAGE,
                    "timestamp": now_iso,
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all unhandled server error handler preventing trace / SQL leakage."""
        logger.exception("Internal server exception at %s", request.url.path)
        now_iso = datetime.now(UTC).isoformat()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": SAFE_INTERNAL_ERROR_MESSAGE,
                    "timestamp": now_iso,
                }
            },
        )

    # --- Routers ---
    # Root health check
    app.include_router(system.router)

    # API v1 endpoints
    api_v1_prefix = "/api/v1"
    app.include_router(system.router, prefix=api_v1_prefix)
    app.include_router(market.router, prefix=api_v1_prefix)
    app.include_router(analytics.router, prefix=api_v1_prefix)
    app.include_router(forecasts.router, prefix=api_v1_prefix)
    app.include_router(auth.router, prefix=api_v1_prefix)
    app.include_router(user.router, prefix=api_v1_prefix)
    app.include_router(alerts.router, prefix=api_v1_prefix)
    app.include_router(admin.router, prefix=api_v1_prefix)
    app.include_router(legal.router, prefix=api_v1_prefix)
    app.include_router(support.router, prefix=api_v1_prefix)
    app.include_router(beta.router, prefix=api_v1_prefix)

    # Static assets and UI Web Prototype
    if ui.STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(ui.STATIC_DIR)), name="static")
    app.include_router(ui.router)

    # Custom OpenAPI schema
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        openapi_schema["info"]["x-disclaimer"] = DISCLAIMER_TEXT
        openapi_schema["info"]["x-api-version"] = API_SEMANTIC_VERSION
        openapi_schema["info"]["x-supported-versions"] = SUPPORTED_MAJOR_VERSIONS
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app
