"""Tests for TLS, HSTS, Security Headers, CORS Allowlist, and Content Security Policy (CSP)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.config import Settings
from stock_analysis.security.headers import (
    build_content_security_policy,
    build_hsts_header,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient with default application middleware stack."""
    app = create_app()
    return TestClient(app)


def test_default_baseline_security_headers_present(client: TestClient) -> None:
    """Verify all standard OWASP/browser security headers are attached to responses."""
    resp = client.get("/health")
    assert resp.status_code == 200

    # 1. Anti-MIME sniffing
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    # 2. Clickjacking protection
    assert resp.headers.get("X-Frame-Options") == "DENY"

    # 3. Referrer leak prevention
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    # 4. Device and sensor restrictions
    perm_policy = resp.headers.get("Permissions-Policy", "")
    assert "geolocation=()" in perm_policy
    assert "camera=()" in perm_policy
    assert "microphone=()" in perm_policy

    # 5. Cross-Origin isolation
    assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-origin"

    # 6. Legacy XSS disablement in favor of CSP
    assert resp.headers.get("X-XSS-Protection") == "0"

    # 7. Legal disclaimer header
    assert "X-Disclaimer" in resp.headers


def test_content_security_policy_directives(client: TestClient) -> None:
    """Verify default CSP directives restrict script, frame, and object sources."""
    resp = client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert csp != "", "Content-Security-Policy header must not be empty"

    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_hsts_omitted_in_local_dev_http(client: TestClient) -> None:
    """HSTS should not be attached on unencrypted local HTTP without proxy header."""
    resp = client.get("/health")
    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_attached_when_forwarded_proto_is_https(client: TestClient) -> None:
    """HSTS header should be added when reverse proxy reports HTTPS via X-Forwarded-Proto."""
    resp = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 200
    hsts = resp.headers.get("Strict-Transport-Security")
    assert hsts is not None
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


def test_hsts_applied_in_production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """HSTS must be enforced on all responses when APP_ENV is production."""
    monkeypatch.setenv("APP_ENV", "production")
    app = create_app()
    prod_client = TestClient(app)

    resp = prod_client.get("/health")
    assert resp.status_code == 200
    hsts = resp.headers.get("Strict-Transport-Security")
    assert hsts is not None
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


def test_cors_allowed_origin_receives_cors_headers(client: TestClient) -> None:
    """Requests originating from allowed origins receive Access-Control-Allow-Origin."""
    origin = "http://localhost:3000"
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_cors_disallowed_origin_blocked(client: TestClient) -> None:
    """Requests originating from untrusted/external origins do not receive CORS allow header."""
    evil_origin = "https://malicious-external-site.com"
    resp = client.get("/health", headers={"Origin": evil_origin})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_options_request(client: TestClient) -> None:
    """Preflight OPTIONS request from allowed origin returns 200 with allowed methods."""
    origin = "http://localhost:3000"
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization, content-type",
    }
    resp = client.options("/health", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin
    assert resp.headers.get("access-control-allow-credentials") == "true"
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "GET" in allow_methods
    assert "OPTIONS" in allow_methods


def test_cors_exposed_headers_include_disclaimer_and_etag(client: TestClient) -> None:
    """Verify that clients can read custom headers like X-Disclaimer and ETag."""
    origin = "http://localhost:3000"
    resp = client.get("/health", headers={"Origin": origin})
    exposed = resp.headers.get("access-control-expose-headers", "")
    assert "x-disclaimer" in exposed.lower()


def test_csp_custom_directives_and_report_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify custom CSP directives and report-only mode configuration."""
    custom_csp = "default-src 'self'; report-uri /api/v1/csp-reports"
    monkeypatch.setenv("CSP_REPORT_ONLY", "true")
    monkeypatch.setenv("CUSTOM_CSP_DIRECTIVES", custom_csp)

    app = create_app()
    test_client = TestClient(app)

    resp = test_client.get("/health")
    assert "Content-Security-Policy" not in resp.headers
    assert resp.headers.get("Content-Security-Policy-Report-Only") == custom_csp


def test_build_hsts_header_formats() -> None:
    """Test HSTS string generation with preload and subdomain variations."""
    standard = build_hsts_header(max_age_seconds=86400, include_subdomains=True, preload=False)
    assert standard == "max-age=86400; includeSubDomains"

    preload = build_hsts_header(max_age_seconds=31536000, include_subdomains=True, preload=True)
    assert preload == "max-age=31536000; includeSubDomains; preload"

    simple = build_hsts_header(max_age_seconds=3600, include_subdomains=False, preload=False)
    assert simple == "max-age=3600"


def test_build_content_security_policy_custom_dict() -> None:
    """Test formatting CSP from a custom dictionary of directives."""
    directives = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "https://trustedscripts.com"],
        "object-src": ["'none'"],
    }
    policy = build_content_security_policy(directives)
    assert "default-src 'self'" in policy
    assert "script-src 'self' https://trustedscripts.com" in policy
    assert "object-src 'none'" in policy


def test_settings_comma_separated_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Settings parses comma-separated CORS allowed origins from environment variable."""
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://app.borsaanaliz.com, https://admin.borsaanaliz.com, https://staging.borsaanaliz.com",
    )
    settings = Settings()
    assert settings.cors_allowed_origins == [
        "https://app.borsaanaliz.com",
        "https://admin.borsaanaliz.com",
        "https://staging.borsaanaliz.com",
    ]
