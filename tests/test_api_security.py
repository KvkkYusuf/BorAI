"""Security, rate limiting, pagination, and authorization tests for API endpoints."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import (
    SlidingWindowRateLimiter,
    TieredRateLimiter,
    reset_all_rate_limiters,
)
from stock_analysis.db.base import Base
from stock_analysis.db.models import AlertRule, Instrument, User
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
    """Create test tables."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(setup_test_db: None) -> Generator[DBSession, None, None]:
    """Isolated db session per test."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    """TestClient resetting rate limiters before test execution."""
    reset_all_rate_limiters()
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Unit Tests for RateLimiter ---


def _build_request(
    ip: str = "1.2.3.4",
    forwarded_for: str | None = None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    hdrs: list[tuple[bytes, bytes]] = list(headers or [])
    if forwarded_for:
        hdrs.append((b"x-forwarded-for", forwarded_for.encode("utf-8")))

    scope: dict[str, object] = {
        "type": "http",
        "client": (ip, 12345),
        "headers": hdrs,
    }
    return Request(scope)


def test_sliding_window_rate_limiter_unit() -> None:
    """Test sliding window rate limiter logic directly."""
    limiter = SlidingWindowRateLimiter(times=3, seconds=10)
    req = _build_request()

    # First 3 requests should pass
    limiter(req)  # 1
    limiter(req)  # 2
    limiter(req)  # 3

    # 4th request must raise 429
    with pytest.raises(HTTPException) as exc_info:
        limiter(req)
    assert exc_info.value.status_code == 429
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "RATE_LIMIT_EXCEEDED"
    assert exc_info.value.headers is not None
    assert "Retry-After" in exc_info.value.headers

    # Reset allows new requests
    limiter.reset()
    limiter(req)


def test_tiered_rate_limiter_unit() -> None:
    """Test TieredRateLimiter enforcing separate limits for anonymous vs authenticated requests."""
    tiered = TieredRateLimiter(anon_times=2, anon_seconds=60, auth_times=5, auth_seconds=60)
    req_anon = _build_request(ip="10.0.0.1")
    req_auth = _build_request(
        ip="10.0.0.1",
        headers=[(b"authorization", b"Bearer secret_token_xyz")],
    )

    # Anonymous tier reaches limit at 2
    tiered(req_anon)
    tiered(req_anon)
    with pytest.raises(HTTPException) as exc_anon:
        tiered(req_anon)
    assert exc_anon.value.status_code == 429

    # Authenticated tier from same IP is separate and still has quota
    for _ in range(5):
        tiered(req_auth)
    with pytest.raises(HTTPException) as exc_auth:
        tiered(req_auth)
    assert exc_auth.value.status_code == 429

    # Reset works properly
    tiered.reset()
    tiered(req_anon)


def test_rate_limiter_forwarded_for_header() -> None:
    """Test rate limiter extracts client IP from X-Forwarded-For header."""
    limiter = SlidingWindowRateLimiter(times=1, seconds=60)

    req1 = _build_request(forwarded_for="203.0.113.195, 70.41.3.18")
    req2 = _build_request(forwarded_for="198.51.100.1")

    limiter(req1)
    # req2 has a different client IP, should succeed
    limiter(req2)

    # Calling req1 again should trigger 429
    with pytest.raises(HTTPException):
        limiter(req1)


# --- Endpoint Integration Tests ---


def test_rate_limiting_on_auth_endpoint(client: TestClient) -> None:
    """Ensure sensitive auth endpoint enforces rate limiting of 5 req/min."""
    payload = {"email": "rate_test@example.com"}

    # 5 requests should succeed
    for _ in range(5):
        resp = client.post("/api/v1/auth/magic-link", json=payload)
        assert resp.status_code == 200

    # 6th request must receive 429 Too Many Requests
    resp_blocked = client.post("/api/v1/auth/magic-link", json=payload)
    assert resp_blocked.status_code == 429
    assert "Retry-After" in resp_blocked.headers
    data = resp_blocked.json()
    assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_anonymous_market_rate_limiting_strict(client: TestClient) -> None:
    """Ensure anonymous read-only endpoints (symbols/chart) have separate strict rate limits.

    Anonymous quota is restricted to 20 req/min while authenticated requests use 60 req/min.
    """
    reset_all_rate_limiters()

    # 20 anonymous calls to /symbols succeed
    for _ in range(20):
        resp = client.get("/api/v1/market/symbols")
        assert resp.status_code == 200

    # 21st anonymous call is rejected with 429
    resp_anon_blocked = client.get("/api/v1/market/symbols")
    assert resp_anon_blocked.status_code == 429
    assert resp_anon_blocked.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    # Authenticated user with valid token is separate and not blocked
    auth_headers = {"Authorization": "Bearer test_token_1"}
    resp_auth = client.get("/api/v1/market/symbols", headers=auth_headers)
    assert resp_auth.status_code == 200


def test_pagination_validation_on_symbols(client: TestClient) -> None:
    """Ensure pagination parameters are validated with strict bounds."""
    # Negative page number -> 422
    resp_negative = client.get("/api/v1/market/symbols?page=-1")
    assert resp_negative.status_code == 422

    # Exceeding page_size max bound of 100 -> 422
    resp_huge = client.get("/api/v1/market/symbols?page_size=200")
    assert resp_huge.status_code == 422

    # Valid pagination returns 200 and matches expected structure
    resp_valid = client.get("/api/v1/market/symbols?page=1&page_size=5")
    assert resp_valid.status_code == 200
    data = resp_valid.json()
    assert len(data["symbols"]) <= 5
    assert data["page"] == 1
    assert data["page_size"] == 5


def test_pagination_on_alerts(client: TestClient, db_session: DBSession) -> None:
    """Verify paginated listing of user alert rules."""
    user = User(email="pager@example.com", is_active=True, role="user")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    inst = Instrument(symbol="SASA", name="SASA Polyester", is_active=True)
    db_session.add(inst)
    db_session.commit()
    db_session.refresh(inst)

    # Insert 3 alert rules
    for i in range(3):
        rule = AlertRule(
            user_id=user.id,
            instrument_id=inst.id,
            condition_type="PRICE_ABOVE",
            threshold=100.0 + i,
            is_active=True,
        )
        db_session.add(rule)
    db_session.commit()

    headers = {"Authorization": f"Bearer test_token_{user.id}"}

    # Fetch page 1 with page_size=2
    resp_p1 = client.get("/api/v1/user/alerts?page=1&page_size=2", headers=headers)
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert data_p1["total"] == 3
    assert len(data_p1["alerts"]) == 2

    # Fetch page 2 with page_size=2
    resp_p2 = client.get("/api/v1/user/alerts?page=2&page_size=2", headers=headers)
    assert resp_p2.status_code == 200
    data_p2 = resp_p2.json()
    assert data_p2["total"] == 3
    assert len(data_p2["alerts"]) == 1


def test_authorization_required(client: TestClient) -> None:
    """Verify endpoints requiring authentication reject missing or invalid bearer credentials."""
    # Missing authorization header -> 401
    resp_no_auth = client.get("/api/v1/user/watchlist")
    assert resp_no_auth.status_code == 401

    # Empty token -> 401
    resp_empty = client.get("/api/v1/user/watchlist", headers={"Authorization": "Bearer "})
    assert resp_empty.status_code == 401

    # Invalid token -> 401
    resp_invalid = client.get(
        "/api/v1/user/watchlist", headers={"Authorization": "Bearer non_existent_token_123"}
    )
    assert resp_invalid.status_code == 401


def test_alert_deletion_idor_protection(client: TestClient, db_session: DBSession) -> None:
    """Verify that User A cannot delete User B's alert rule (IDOR prevention)."""
    user_a = User(email="user_a@example.com", is_active=True, role="user")
    user_b = User(email="user_b@example.com", is_active=True, role="user")
    db_session.add_all([user_a, user_b])
    db_session.commit()
    db_session.refresh(user_a)
    db_session.refresh(user_b)

    inst = Instrument(symbol="KCHOL", name="Koc Holding", is_active=True)
    db_session.add(inst)
    db_session.commit()
    db_session.refresh(inst)

    # Alert owned by User B
    rule_b = AlertRule(
        user_id=user_b.id,
        instrument_id=inst.id,
        condition_type="PRICE_ABOVE",
        threshold=250.0,
        is_active=True,
    )
    db_session.add(rule_b)
    db_session.commit()
    db_session.refresh(rule_b)

    # User A attempts to delete User B's alert
    headers_a = {"Authorization": f"Bearer test_token_{user_a.id}"}
    resp = client.delete(f"/api/v1/user/alerts/alt_{rule_b.id}", headers=headers_a)
    # Must be 404 Not Found (ownership check blocks IDOR)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ALERT_NOT_FOUND"

    # Verify rule still exists in database
    existing_rule = db_session.get(AlertRule, rule_b.id)
    assert existing_rule is not None


# --- Information Leak & Error Sanitization Tests ---


def test_sql_leak_prevention_on_database_error() -> None:
    """Ensure database errors (SQLAlchemyError) never leak table, column, or raw SQL queries."""
    from sqlalchemy.exc import OperationalError

    app = create_app()

    @app.get("/api/v1/test-db-error")
    def trigger_db_error() -> None:
        raise OperationalError(
            statement="SELECT secret_key, password_hash FROM private_users WHERE user_id=123",
            params={},
            orig=Exception("internal sqlite3 operational failure"),
        )

    test_client = TestClient(app, raise_server_exceptions=False)
    resp = test_client.get("/api/v1/test-db-error")
    assert resp.status_code == 500

    data = resp.json()
    assert data["error"]["code"] == "DATABASE_ERROR"
    assert data["error"]["message"] == "Veritabani islemi sirasinda bir hata meydana geldi."

    # Raw response body must not leak query, tables, or drivers
    raw_body = resp.text
    assert "SELECT" not in raw_body
    assert "secret_key" not in raw_body
    assert "password_hash" not in raw_body
    assert "private_users" not in raw_body
    assert "sqlite3" not in raw_body


def test_unhandled_exception_does_not_leak_stack_trace() -> None:
    """Ensure unexpected python exceptions do not leak stack traces or filenames."""
    app = create_app()

    @app.get("/api/v1/test-crash")
    def trigger_crash() -> None:
        # Simulate unhandled calculation/code error
        raise ZeroDivisionError("division by zero in secret_algorithm.py line 88")

    test_client = TestClient(app, raise_server_exceptions=False)
    resp = test_client.get("/api/v1/test-crash")
    assert resp.status_code == 500

    data = resp.json()
    assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert data["error"]["message"] == "Sunucuda beklenmeyen bir hata olustu."

    raw_body = resp.text
    assert "ZeroDivisionError" not in raw_body
    assert "secret_algorithm.py" not in raw_body
    assert "Traceback" not in raw_body


def test_http_exception_with_leaked_info_is_sanitized() -> None:
    """Ensure HTTPException containing sensitive SQL or secrets is scrubbed."""
    app = create_app()

    @app.get("/api/v1/test-leak-http-exc")
    def trigger_leak_http() -> None:
        raise HTTPException(
            status_code=400,
            detail="Failed executing SELECT * FROM accounts WHERE token='secret_token_abc123'",
        )

    test_client = TestClient(app)
    resp = test_client.get("/api/v1/test-leak-http-exc")
    assert resp.status_code == 400

    data = resp.json()
    assert data["error"]["code"] == "HTTP_ERROR"
    assert data["error"]["message"] == "Guvenlik nedeniyle hata detaylari gizlendi."
    assert "SELECT" not in resp.text
    assert "secret_token_abc123" not in resp.text


def test_email_masking_in_error_message() -> None:
    """Ensure emails appearing in error messages are masked according to privacy guidelines."""
    app = create_app()

    @app.get("/api/v1/test-email-error")
    def trigger_email_error() -> None:
        raise HTTPException(
            status_code=404,
            detail="Kullanici mehmet.oz@borsa-analiz.com sistemde bulunamadi.",
        )

    test_client = TestClient(app)
    resp = test_client.get("/api/v1/test-email-error")
    assert resp.status_code == 404

    data = resp.json()
    assert "mehmet.oz@borsa-analiz.com" not in data["error"]["message"]
    assert "m***@borsa-analiz.com" in data["error"]["message"]
