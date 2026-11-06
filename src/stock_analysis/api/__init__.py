"""REST API module for Borsa Analiz platform."""

from stock_analysis.api.app import create_app
from stock_analysis.api.versioning import (
    API_SEMANTIC_VERSION,
    CURRENT_MAJOR_VERSION,
    SUPPORTED_MAJOR_VERSIONS,
    ApiLifecycleStatus,
    ApiVersionInfo,
)

__all__ = [
    "API_SEMANTIC_VERSION",
    "CURRENT_MAJOR_VERSION",
    "SUPPORTED_MAJOR_VERSIONS",
    "ApiLifecycleStatus",
    "ApiVersionInfo",
    "create_app",
]
