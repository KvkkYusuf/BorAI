"""Integration tests for REST API endpoints, security, and disclaimers."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.config import Settings
from stock_analysis.db.base import Base
from stock_analysis.db.session import get_db

# Create test in-memory SQLite DB with StaticPool so all connections share the database
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
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Health & System Tests ---


def test_health_check(client: TestClient) -> None:
    """Test health check returns 200, flags, and disclaimer headers."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    expected_disc = "Bu sistem bilgilendirme amaclidir, yatirim tavsiyesi degildir."
    assert response.headers["X-Disclaimer"] == expected_disc
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_api_v1_health(client: TestClient) -> None:
    """Test /api/v1/health prefix routing."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


# --- Market Endpoints Tests ---


def test_list_symbols_and_search(client: TestClient) -> None:
    """Test listing BIST symbols and query filtering."""
    # List all
    response = client.get("/api/v1/market/symbols?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["universe"] == "BIST_100"
    assert len(data["symbols"]) > 0
    assert "disclaimer" in data

    # Search filter
    response_search = client.get("/api/v1/market/symbols?q=THY")
    assert response_search.status_code == 200
    search_data = response_search.json()
    assert len(search_data["symbols"]) >= 1
    assert any(s["symbol"] == "THYAO" for s in search_data["symbols"])


def test_get_history_success_and_validation(client: TestClient) -> None:
    """Test historical EOD bar retrieval and parameter validation."""
    response = client.get("/api/v1/market/history/THYAO?interval=1d&range=1m&price_type=adjusted")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "THYAO"
    assert data["currency"] == "TRY"
    assert data["price_type"] == "adjusted"
    assert len(data["candles"]) > 0

    # Invalid interval
    res_inv_int = client.get("/api/v1/market/history/THYAO?interval=1h")
    assert res_inv_int.status_code == 400
    assert res_inv_int.json()["error"]["code"] == "INVALID_INTERVAL"

    # Invalid range
    res_inv_range = client.get("/api/v1/market/history/THYAO?range=invalid")
    assert res_inv_range.status_code == 400
    assert res_inv_range.json()["error"]["code"] == "INVALID_RANGE"

    # Invalid price type
    res_inv_pt = client.get("/api/v1/market/history/THYAO?price_type=invalid")
    assert res_inv_pt.status_code == 400
    assert res_inv_pt.json()["error"]["code"] == "INVALID_PRICE_TYPE"


# --- Analytics & Screener Tests ---


def test_indicators_neutral_summary(client: TestClient) -> None:
    """Test indicator calculations and strictly neutral summary."""
    response = client.get("/api/v1/analytics/indicators/THYAO?indicators=rsi,sma20,sma50,macd")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "THYAO"
    assert data["indicators"]["rsi_14"] is not None
    assert data["indicators"]["macd"] is not None
    # Verify neutral language without recommendation keywords
    summary = data["neutral_summary"].lower()
    for forbidden in ["al", "sat", "tut", "hedef"]:
        assert f" {forbidden} " not in f" {summary} "
    assert "Teknik gostergeler deterministik hesaplamadir" in data["disclaimer"]


def test_screener_filtering(client: TestClient) -> None:
    """Test objective screener filtering."""
    response = client.get("/api/v1/analytics/screener?rsi_max=35")
    assert response.status_code == 200
    data = response.json()
    assert data["filter_applied"]["rsi_max"] == 35.0
    for match in data["matches"]:
        assert match["rsi_14"] <= 35.0


# --- Forecast / Inference Tests ---


def test_forecast_disabled_by_default(client: TestClient) -> None:
    """Verify that forecasting returns 503 when feature toggle is disabled."""
    response = client.get("/api/v1/inference/forecast/THYAO")
    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == "FORECAST_DISABLED"


def test_forecast_enabled_output(db_session: DBSession) -> None:
    """Verify forecasting returns probability bounds when feature toggle is enabled."""
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    def override_settings() -> Settings:
        return Settings(FORECAST_ENABLED=True)

    from stock_analysis.config import get_settings

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_settings

    with TestClient(app) as custom_client:
        response = custom_client.get("/api/v1/inference/forecast/THYAO")
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "THYAO"
        assert len(data["uncertainty_interval"]) == 2
        assert "baseline_comparison" in data
        assert "kesinlik icermez" in data["disclaimer"]


# --- Authentication Tests ---


def test_magic_link_flow(client: TestClient) -> None:
    """Test requesting magic link and verifying token."""
    # Request magic link
    res_req = client.post("/api/v1/auth/magic-link", json={"email": "kullanici@example.com"})
    assert res_req.status_code == 200
    assert "gonderildi" in res_req.json()["message"]

    # Invalid email validation
    res_invalid = client.post("/api/v1/auth/magic-link", json={"email": "not-an-email"})
    assert res_invalid.status_code == 422
    assert res_invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    # Verify token (test token)
    res_verify = client.post("/api/v1/auth/verify", json={"token": "test_magic_token_1234567890"})
    assert res_verify.status_code == 200
    data = res_verify.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


# --- User, Watchlist & Alerts Tests ---


def test_user_endpoints_unauthorized_without_token(client: TestClient) -> None:
    """Verify that user endpoints reject unauthorized requests with 401."""
    res_wl = client.get("/api/v1/user/watchlist")
    assert res_wl.status_code == 401
    assert res_wl.json()["error"]["code"] == "UNAUTHORIZED"


def test_watchlist_crud_no_portfolio_fields(client: TestClient) -> None:
    """Verify watchlist CRUD and guarantee absolute absence of portfolio fields."""
    headers = {"Authorization": "Bearer test_token_1"}

    # Initially empty
    res_get1 = client.get("/api/v1/user/watchlist", headers=headers)
    assert res_get1.status_code == 200
    assert res_get1.json()["watchlist"] == []

    # Add symbol
    res_add = client.post("/api/v1/user/watchlist/THYAO", headers=headers)
    assert res_add.status_code == 201
    assert res_add.json()["symbol"] == "THYAO"

    # Check updated watchlist
    res_get2 = client.get("/api/v1/user/watchlist", headers=headers)
    assert res_get2.status_code == 200
    wl = res_get2.json()["watchlist"]
    assert "THYAO" in wl
    # Strict compliance verification: watchlist items must be pure string symbols
    assert all(isinstance(item, str) for item in wl)

    # Delete symbol
    res_del = client.delete("/api/v1/user/watchlist/THYAO", headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["symbol"] == "THYAO"


def test_alert_quota_enforcement(client: TestClient) -> None:
    """Verify that alert rules are capped at max 5 active alerts per user."""
    headers = {"Authorization": "Bearer test_token_2"}

    # Add 5 alerts
    symbols = ["THYAO", "ASELS", "GARAN", "EREGL", "KCHOL"]
    for sym in symbols:
        res = client.post(
            "/api/v1/user/alerts",
            json={"symbol": sym, "condition_type": "PRICE_ABOVE", "target_value": 300.0},
            headers=headers,
        )
        assert res.status_code == 201

    # Attempt 6th alert (must be rejected)
    res_overflow = client.post(
        "/api/v1/user/alerts",
        json={"symbol": "BIMAS", "condition_type": "PRICE_ABOVE", "target_value": 400.0},
        headers=headers,
    )
    assert res_overflow.status_code == 400
    assert res_overflow.json()["error"]["code"] == "ALERT_QUOTA_EXCEEDED"

    # List alerts
    res_list = client.get("/api/v1/user/alerts", headers=headers)
    assert res_list.status_code == 200
    assert res_list.json()["total"] == 5

    # Delete an alert
    first_id = res_list.json()["alerts"][0]["alert_id"]
    res_del = client.delete(f"/api/v1/user/alerts/{first_id}", headers=headers)
    assert res_del.status_code == 200

    # Now 5th slot is free
    res_retry = client.post(
        "/api/v1/user/alerts",
        json={"symbol": "BIMAS", "condition_type": "PRICE_ABOVE", "target_value": 400.0},
        headers=headers,
    )
    assert res_retry.status_code == 201


def test_kvkk_export_and_hard_delete(client: TestClient) -> None:
    """Verify KVKK data portability export and hard deletion of account."""
    headers = {"Authorization": "Bearer test_token_3"}

    # Add an item to watchlist
    client.post("/api/v1/user/watchlist/ASELS", headers=headers)

    # Export data
    res_exp = client.get("/api/v1/user/export", headers=headers)
    assert res_exp.status_code == 200
    exp_data = res_exp.json()
    assert exp_data["user_id"] == 3
    assert "ASELS" in exp_data["watchlists"]
    assert "disclaimer" in exp_data

    # Delete account
    res_del = client.delete("/api/v1/user/me", headers=headers)
    assert res_del.status_code == 200
    assert "kalici olarak silindi" in res_del.json()["message"]


# --- OpenAPI Schema Test ---


def test_openapi_schema_contains_disclaimer(client: TestClient) -> None:
    """Verify that OpenAPI documentation includes mandatory legal notice and routes."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "x-disclaimer" in schema["info"]
    assert "/api/v1/market/symbols" in schema["paths"]
    assert "/api/v1/analytics/indicators/{symbol}" in schema["paths"]
    assert "/api/v1/inference/forecast/{symbol}" in schema["paths"]
    assert "/api/v1/user/watchlist" in schema["paths"]
