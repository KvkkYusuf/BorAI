"""Tests for HTTP caching (ETag, Cache-Control, 304), Redis/In-Memory cache, and pagination."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.cache import (
    InMemoryCacheBackend,
    RedisCacheBackend,
    compute_etag,
)
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.session import get_db

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def setup_test_db() -> Generator[None, None, None]:
    """Create all tables in memory for API testing."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(setup_test_db: None) -> Generator[DBSession, None, None]:
    """Provide clean database session per test."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    """Provide FastAPI test client with overridden db dependency."""
    reset_all_rate_limiters()
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_etag_computation() -> None:
    """Test deterministic ETag generation from bytes."""
    data = b'{"symbol": "THYAO", "price": 310.25}'
    etag1 = compute_etag(data)
    etag2 = compute_etag(data)
    assert etag1 == etag2
    assert etag1.startswith('"') and etag1.endswith('"')
    assert len(etag1) == 18  # 16 hex chars + 2 quotes


def test_http_cache_headers_and_etag(client: TestClient) -> None:
    """Verify that cacheable GET endpoints return ETag and Cache-Control headers."""
    response = client.get("/api/v1/market/symbols")
    assert response.status_code == 200
    assert "etag" in response.headers
    assert "cache-control" in response.headers
    assert "public, max-age=" in response.headers["cache-control"]


def test_http_304_not_modified(client: TestClient) -> None:
    """Verify that conditional GET with matching If-None-Match returns HTTP 304."""
    # First request: get fresh response and ETag
    res1 = client.get("/api/v1/market/symbols")
    assert res1.status_code == 200
    etag = res1.headers.get("etag")
    assert etag is not None

    # Second request: conditional request with If-None-Match
    res2 = client.get("/api/v1/market/symbols", headers={"If-None-Match": etag})
    assert res2.status_code == 304
    assert res2.text == ""
    assert res2.headers.get("etag") == etag
    assert "X-Disclaimer" in res2.headers
    assert "X-API-Version" in res2.headers


def test_http_etag_mismatch_returns_200(client: TestClient) -> None:
    """Verify that conditional GET with mismatched ETag returns full 200 OK body."""
    response = client.get("/api/v1/market/symbols", headers={"If-None-Match": '"mismatched-etag"'})
    assert response.status_code == 200
    assert len(response.text) > 0


def test_post_requests_not_cached(client: TestClient) -> None:
    """Verify that POST mutations do not receive ETag and are not 304-cached."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "DummyPassword123!"},
    )
    # Status can be 401 or similar, but must not be 304
    assert response.status_code != 304


@pytest.mark.asyncio
async def test_in_memory_cache_backend_operations() -> None:
    """Test get, set, TTL expiration, delete, and clear in InMemoryCacheBackend."""
    cache = InMemoryCacheBackend()
    assert await cache.get("key1") is None

    # Test set & get
    await cache.set("key1", "value1", expire_seconds=10)
    assert await cache.get("key1") == "value1"

    # Test delete
    assert await cache.delete("key1") is True
    assert await cache.get("key1") is None
    assert await cache.delete("key1") is False

    # Test TTL expiration
    await cache.set("quick_expire", "temp_value", expire_seconds=0)
    val = await cache.get("quick_expire")
    assert val is None

    # Test clear
    await cache.set("a", "1", expire_seconds=60)
    await cache.set("b", "2", expire_seconds=60)
    assert len(cache) == 2
    await cache.clear()
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_redis_cache_backend_fallback_when_offline() -> None:
    """Test RedisCacheBackend gracefully falls back to in-memory store if Redis is unavailable."""
    backend = RedisCacheBackend(redis_url="redis://invalid-host-or-port:6379/0")
    await backend.set("test_key", "test_val", expire_seconds=60)
    result = await backend.get("test_key")
    assert result == "test_val"

    deleted = await backend.delete("test_key")
    assert deleted is True
    assert await backend.get("test_key") is None


def test_mandatory_pagination_symbols(client: TestClient) -> None:
    """Test mandatory pagination constraints on symbols endpoint."""
    # Valid pagination
    resp = client.get("/api/v1/market/symbols?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["symbols"]) <= 10

    # Reject page_size > 100
    resp_invalid = client.get("/api/v1/market/symbols?page_size=101")
    assert resp_invalid.status_code == 422

    # Reject page < 1
    resp_invalid_page = client.get("/api/v1/market/symbols?page=0")
    assert resp_invalid_page.status_code == 422


def test_mandatory_pagination_screener(client: TestClient) -> None:
    """Test mandatory pagination constraints on screener endpoint."""
    resp = client.get("/api/v1/analytics/screener?page=1&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert "total" in data
    assert len(data["matches"]) <= 2

    # Reject page_size > 100
    resp_invalid = client.get("/api/v1/analytics/screener?page_size=150")
    assert resp_invalid.status_code == 422

    # Reject page < 1
    resp_invalid_page = client.get("/api/v1/analytics/screener?page=-1")
    assert resp_invalid_page.status_code == 422
