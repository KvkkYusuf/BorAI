"""API Versioning and Backward Compatibility module.

Defines Semantic Versioning metadata, RFC 8594 / RFC 9745 deprecation handling,
Sunset headers (RFC 7231), and client compatibility checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# --- Version Constants ---
API_SEMANTIC_VERSION: Final[str] = "1.0.0"
CURRENT_MAJOR_VERSION: Final[str] = "v1"
SUPPORTED_MAJOR_VERSIONS: Final[list[str]] = ["v1"]
MINIMUM_SUPPORTED_CLIENT_VERSION: Final[str] = "1.0.0"
CHANGELOG_URL: Final[str] = "https://github.com/bora-ai/borsa-analiz/blob/main/CHANGELOG.md"
DOCS_URL: Final[str] = "/docs"


class ApiLifecycleStatus(StrEnum):
    """Lifecycle stages for an API major version or endpoint."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUNSET = "sunset"


class ApiVersionInfo(BaseModel):
    """API version information and lifecycle metadata."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(
        default=API_SEMANTIC_VERSION,
        description="SemVer 2.0.0 semantic version (MAJOR.MINOR.PATCH)",
    )
    major_version: str = Field(
        default=CURRENT_MAJOR_VERSION,
        description="Active major API prefix (e.g. v1)",
    )
    status: ApiLifecycleStatus = Field(
        default=ApiLifecycleStatus.ACTIVE,
        description="Current lifecycle state (active, deprecated, sunset)",
    )
    supported_versions: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_MAJOR_VERSIONS),
        description="List of currently supported major API versions",
    )
    min_client_version: str = Field(
        default=MINIMUM_SUPPORTED_CLIENT_VERSION,
        description="Minimum recommended client application version",
    )
    sunset_date: str | None = Field(
        default=None,
        description="RFC 7231 IMF-fixdate string when this version will be decommissioned",
    )
    migration_guide_url: str | None = Field(
        default=None,
        description="URL for upgrading to the newer API version",
    )
    changelog_url: str = Field(
        default=CHANGELOG_URL,
        description="Public changelog link describing recent enhancements",
    )


def format_http_date(dt: datetime) -> str:
    """Format a datetime into RFC 7231 IMF-fixdate string (e.g. 'Wed, 21 Oct 2026 07:28:00 GMT')."""
    return dt.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


@dataclass(frozen=True)
class DeprecatedEndpoint:
    """Metadata describing a deprecated endpoint or route."""

    sunset_at: datetime | None = None
    migration_guide_url: str | None = None
    reason: str | None = None


class DeprecationRegistry:
    """Registry maintaining paths marked for deprecation and sunset."""

    def __init__(self) -> None:
        self._deprecated_paths: dict[str, DeprecatedEndpoint] = {}

    def register(
        self,
        path: str,
        sunset_at: datetime | None = None,
        migration_guide_url: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Register a path or route prefix as deprecated."""
        self._deprecated_paths[path] = DeprecatedEndpoint(
            sunset_at=sunset_at,
            migration_guide_url=migration_guide_url,
            reason=reason,
        )

    def unregister(self, path: str) -> None:
        """Remove a route from deprecation registry."""
        self._deprecated_paths.pop(path, None)

    def get(self, path: str) -> DeprecatedEndpoint | None:
        """Check if an exact path or prefix is deprecated."""
        # Check exact match first
        if path in self._deprecated_paths:
            return self._deprecated_paths[path]
        # Check path prefix matches
        for registered_path, meta in self._deprecated_paths.items():
            if path.startswith(registered_path):
                return meta
        return None

    def is_deprecated(self, path: str) -> bool:
        """Return True if path is marked as deprecated."""
        return self.get(path) is not None

    def clear(self) -> None:
        """Clear all registered deprecations."""
        self._deprecated_paths.clear()


# Global in-memory registry instance
deprecation_registry = DeprecationRegistry()


def get_current_api_version() -> ApiVersionInfo:
    """Return the active API version and lifecycle metadata."""
    return ApiVersionInfo()


class ApiVersioningMiddleware(BaseHTTPMiddleware):
    """Middleware attaching API version headers and RFC 8594 / RFC 7231 deprecation headers."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)

        # Always attach X-API-Version
        response.headers["X-API-Version"] = API_SEMANTIC_VERSION

        # Check if the requested path is marked deprecated
        path = request.url.path
        dep = deprecation_registry.get(path)
        if dep is not None:
            # RFC 8594: Deprecation header (boolean true or date)
            response.headers["Deprecation"] = "true"

            # RFC 7231 / RFC 8594: Sunset header
            if dep.sunset_at is not None:
                response.headers["Sunset"] = format_http_date(dep.sunset_at)

            # RFC 8288 / RFC 8594: Link rel="deprecation"
            if dep.migration_guide_url is not None:
                response.headers["Link"] = f'<{dep.migration_guide_url}>; rel="deprecation"'

        return response
