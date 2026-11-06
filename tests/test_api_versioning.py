"""Tests for API Versioning, Backward Compatibility, and Deprecation/Sunset Headers (RFC 8594)."""

from datetime import UTC, datetime

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from stock_analysis.api import (
    API_SEMANTIC_VERSION,
    CURRENT_MAJOR_VERSION,
    SUPPORTED_MAJOR_VERSIONS,
    ApiLifecycleStatus,
    ApiVersionInfo,
    create_app,
)
from stock_analysis.api.versioning import (
    deprecation_registry,
    format_http_date,
)


@pytest.fixture
def client() -> TestClient:
    """Provide clean FastAPI test client."""
    app = create_app()
    return TestClient(app)


def test_api_version_constants() -> None:
    """Verify versioning constants align with Semantic Versioning 2.0.0."""
    assert API_SEMANTIC_VERSION == "1.0.0"
    assert CURRENT_MAJOR_VERSION == "v1"
    assert "v1" in SUPPORTED_MAJOR_VERSIONS


def test_format_http_date() -> None:
    """Verify format_http_date produces standard RFC 7231 IMF-fixdate."""
    dt = datetime(2027, 4, 22, 12, 30, 0, tzinfo=UTC)
    formatted = format_http_date(dt)
    assert formatted == "Thu, 22 Apr 2027 12:30:00 GMT"


def test_version_endpoint_v1(client: TestClient) -> None:
    """Verify /api/v1/version returns full version and compatibility metadata."""
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0.0"
    assert data["major_version"] == "v1"
    assert data["status"] == "active"
    assert data["supported_versions"] == ["v1"]
    assert "min_client_version" in data
    assert "changelog_url" in data


def test_root_version_endpoint(client: TestClient) -> None:
    """Verify root /version is also accessible."""
    response = client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0.0"
    assert data["major_version"] == "v1"


def test_response_has_x_api_version_header(client: TestClient) -> None:
    """Verify all API responses include the X-API-Version header."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers.get("X-API-Version") == "1.0.0"

    response_ver = client.get("/api/v1/version")
    assert response_ver.headers.get("X-API-Version") == "1.0.0"


def test_openapi_contains_version_metadata(client: TestClient) -> None:
    """Verify OpenAPI specification reflects versioning contracts and tags."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    info = schema.get("info", {})
    assert info.get("version") == "1.0.0"
    assert info.get("x-api-version") == "1.0.0"
    assert info.get("x-supported-versions") == ["v1"]
    assert "x-disclaimer" in info


def test_standard_endpoints_do_not_contain_deprecation_headers(client: TestClient) -> None:
    """Verify active endpoints do not emit deprecation or sunset headers."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert "Deprecation" not in response.headers
    assert "Sunset" not in response.headers
    assert "Link" not in response.headers


def test_deprecated_endpoint_emits_rfc8594_headers() -> None:
    """Verify registered deprecated routes emit Deprecation, Sunset, and Link headers."""
    deprecation_registry.clear()
    test_path = "/api/v1/mock-deprecated"
    sunset_target = datetime(2027, 10, 1, 0, 0, 0, tzinfo=UTC)
    migration_url = "https://borsa.local/docs/migrating-to-v2"

    deprecation_registry.register(
        path=test_path,
        sunset_at=sunset_target,
        migration_guide_url=migration_url,
        reason="Upgraded to v2 analytics engine",
    )

    try:
        app = create_app()
        dummy_router = APIRouter()

        @dummy_router.get("/mock-deprecated")
        def mock_deprecated_route() -> dict[str, str]:
            return {"message": "old legacy data"}

        app.include_router(dummy_router, prefix="/api/v1")

        with TestClient(app) as test_client:
            res = test_client.get(test_path)
            assert res.status_code == 200
            assert res.headers.get("Deprecation") == "true"
            assert res.headers.get("Sunset") == "Fri, 01 Oct 2027 00:00:00 GMT"
            assert f'<{migration_url}>; rel="deprecation"' in res.headers.get("Link", "")
    finally:
        deprecation_registry.clear()


def test_api_version_info_model() -> None:
    """Verify ApiVersionInfo model serialization and lifecycle status enum."""
    info = ApiVersionInfo(
        version="1.1.0",
        major_version="v1",
        status=ApiLifecycleStatus.DEPRECATED,
        supported_versions=["v1", "v2"],
        min_client_version="1.0.0",
        sunset_date="Mon, 01 Jan 2029 00:00:00 GMT",
        migration_guide_url="https://borsa.local/docs/v2",
    )
    assert info.version == "1.1.0"
    assert info.status == ApiLifecycleStatus.DEPRECATED
    assert info.sunset_date == "Mon, 01 Jan 2029 00:00:00 GMT"
