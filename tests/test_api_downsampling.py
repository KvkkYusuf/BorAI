"""Tests for chart downsampling algorithms and historical API endpoint downsampling."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.cache import get_cache_backend
from stock_analysis.api.downsample import (
    aggregate_candle_bucket,
    downsample_bucket,
    downsample_monthly,
    downsample_series,
    downsample_weekly,
)
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.api.schemas import CandleItem
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
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Clear memory cache between tests."""
    get_cache_backend().clear_sync()


def _make_dummy_candles(n: int, base_date: datetime | None = None) -> list[CandleItem]:
    if base_date is None:
        base_date = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
    candles: list[CandleItem] = []
    for i in range(n):
        d = base_date + timedelta(days=i)
        candles.append(
            CandleItem(
                date=d,
                open=100.0 + i,
                high=105.0 + i,
                low=95.0 + i,
                close=102.0 + i,
                volume=1000.0 * (i + 1),
                turnover_try=round((102.0 + i) * 1000.0 * (i + 1), 2),
            )
        )
    return candles


def test_aggregate_candle_bucket_math() -> None:
    """Verify OHLCV financial aggregation properties."""
    candles = _make_dummy_candles(3)
    # Candle 0: O:100, H:105, L:95, C:102, V:1000
    # Candle 1: O:101, H:106, L:96, C:103, V:2000
    # Candle 2: O:102, H:107, L:97, C:104, V:3000

    aggregated = aggregate_candle_bucket(candles)
    assert aggregated.open == 100.0
    assert aggregated.high == 107.0
    assert aggregated.low == 95.0
    assert aggregated.close == 104.0
    assert aggregated.volume == 6000.0
    assert aggregated.date == candles[-1].date
    assert aggregated.turnover_try is not None


def test_downsample_bucket_point_count() -> None:
    """Ensure downsampling reduces to exactly max_points when N > max_points."""
    candles = _make_dummy_candles(100)
    downsampled = downsample_bucket(candles, max_points=20)
    assert len(downsampled) == 20
    assert downsampled[0].open == candles[0].open
    assert downsampled[-1].close == candles[-1].close


def test_downsample_bucket_no_op_when_small() -> None:
    """Ensure downsampling does not alter series if length <= max_points."""
    candles = _make_dummy_candles(10)
    downsampled = downsample_bucket(candles, max_points=20)
    assert len(downsampled) == 10
    assert downsampled == candles


def test_downsample_weekly_grouping() -> None:
    """Verify daily candles are properly aggregated into weekly bars."""
    # 14 consecutive days = spanning 2 or 3 calendar weeks
    candles = _make_dummy_candles(14, base_date=datetime(2026, 1, 5, tzinfo=UTC))  # Jan 5 is Monday
    weekly = downsample_weekly(candles)
    assert len(weekly) == 2
    # First week (Jan 5 - Jan 11: 7 days)
    assert weekly[0].open == candles[0].open
    assert weekly[0].close == candles[6].close
    assert weekly[0].volume == sum(c.volume for c in candles[:7])
    # Second week (Jan 12 - Jan 18: 7 days)
    assert weekly[1].open == candles[7].open
    assert weekly[1].close == candles[13].close


def test_downsample_monthly_grouping() -> None:
    """Verify daily candles are properly aggregated into monthly bars."""
    # 60 consecutive days from Jan 1
    candles = _make_dummy_candles(60, base_date=datetime(2026, 1, 1, tzinfo=UTC))
    monthly = downsample_monthly(candles)
    # January has 31 days, February has 28 or 29 days (60 days covers Jan, Feb, Mar)
    assert len(monthly) == 3
    assert monthly[0].open == candles[0].open
    assert monthly[-1].close == candles[-1].close


def test_downsample_series_helper() -> None:
    """Test unified downsample_series dispatcher."""
    candles = _make_dummy_candles(50)

    # No downsampling requested
    res, is_down, method = downsample_series(candles)
    assert not is_down
    assert method is None
    assert len(res) == 50

    # Bucket with max_points
    res, is_down, method = downsample_series(candles, max_points=15)
    assert is_down
    assert method == "bucket"
    assert len(res) == 15

    # Unknown method raises ValueError
    with pytest.raises(ValueError, match="Unknown downsample method"):
        downsample_series(candles, method="invalid_algo")


def test_api_history_without_downsampling(client: TestClient) -> None:
    """Default request should not be downsampled."""
    response = client.get("/api/v1/market/history/THYAO?range=1m")
    assert response.status_code == 200
    data = response.json()
    assert data["is_downsampled"] is False
    assert data["downsample_method"] is None
    assert len(data["candles"]) > 0


def test_api_history_with_bucket_downsampling(client: TestClient) -> None:
    """Request with max_points downsampling should compress candles to max_points."""
    response = client.get("/api/v1/market/history/THYAO?range=1m&downsample=bucket&max_points=10")
    assert response.status_code == 200
    data = response.json()
    assert data["is_downsampled"] is True
    assert data["downsample_method"] == "bucket"
    assert data["raw_count"] >= 10
    assert data["downsampled_count"] == 10
    assert len(data["candles"]) == 10


def test_api_history_with_weekly_downsampling(client: TestClient) -> None:
    """Request with weekly downsampling should return weekly aggregated bars."""
    response = client.get("/api/v1/market/history/THYAO?range=1m&downsample=weekly")
    assert response.status_code == 200
    data = response.json()
    assert data["is_downsampled"] is True
    assert data["downsample_method"] == "weekly"
    assert data["downsampled_count"] == len(data["candles"])
    assert len(data["candles"]) < data["raw_count"]


def test_api_history_with_monthly_downsampling(client: TestClient) -> None:
    """Request with monthly downsampling should return monthly aggregated bars."""
    response = client.get("/api/v1/market/history/THYAO?range=1m&downsample=monthly")
    assert response.status_code == 200
    data = response.json()
    assert data["is_downsampled"] is True
    assert data["downsample_method"] == "monthly"
    assert len(data["candles"]) <= 2


def test_api_history_downsample_invalid_method(client: TestClient) -> None:
    """Request with invalid downsampling method returns 400 error."""
    response = client.get("/api/v1/market/history/THYAO?downsample=lttb_unsupported")
    assert response.status_code == 400
    err = response.json()
    assert err["error"]["code"] == "INVALID_DOWNSAMPLE"


def test_api_history_max_points_validation_bounds(client: TestClient) -> None:
    """max_points must adhere to validation boundaries (ge=10, le=2000)."""
    # Below minimum (ge=10)
    res_low = client.get("/api/v1/market/history/THYAO?max_points=5")
    assert res_low.status_code == 422

    # Above maximum (le=2000)
    res_high = client.get("/api/v1/market/history/THYAO?max_points=5000")
    assert res_high.status_code == 422


def test_api_history_cache_isolation_by_downsample(client: TestClient) -> None:
    """Different downsampling parameters must not collide in the cache."""
    # First call: full series
    resp_full = client.get("/api/v1/market/history/THYAO?range=1m")
    assert resp_full.status_code == 200
    assert resp_full.json()["is_downsampled"] is False

    # Second call: 10 points downsampled
    resp_down = client.get("/api/v1/market/history/THYAO?range=1m&max_points=10")
    assert resp_down.status_code == 200
    assert resp_down.json()["is_downsampled"] is True
    assert len(resp_down.json()["candles"]) == 10

    # Re-calling full series returns cached full series
    resp_full_again = client.get("/api/v1/market/history/THYAO?range=1m")
    assert resp_full_again.status_code == 200
    assert resp_full_again.json()["is_downsampled"] is False
    assert len(resp_full_again.json()["candles"]) > 10
