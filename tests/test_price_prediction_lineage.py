"""Unit and API integration tests for price and prediction lineage traceability (FAZ-15).

Verifies:
- Price and prediction records carry traceable data_source, calculation_version, and lineage_id.
- Thread-safe LineageTracker correctly indexes records by lineage_id, symbol, and trace_id.
- Ring buffer enforces maximum capacity and eviction of oldest records.
- API endpoints /market/history/{symbol} and /inference/forecast/{symbol}
  return verifiable lineage metadata.
- Compliance disclaimers conform to SPK non-advisory standards.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.session import get_db
from stock_analysis.observability.lineage import (
    LINEAGE_RECORDS_TOTAL,
    LINEAGE_TRACKER,
    DisplayedPredictionLineage,
    DisplayedPriceLineage,
    LineageTracker,
)

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def clean_lineage_tracker() -> Generator[None, None, None]:
    """Ensure clean tracker state for each test."""
    LINEAGE_TRACKER.clear()
    Base.metadata.create_all(bind=test_engine)
    yield
    LINEAGE_TRACKER.clear()
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provide FastAPI test client with overridden db dependency."""
    reset_all_rate_limiters()
    app = create_app()
    session = TestingSessionLocal()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_displayed_price_lineage_model() -> None:
    record = DisplayedPriceLineage(
        lineage_id="lin_prc_12345",
        symbol="THYAO",
        price_type="adjusted",
        data_source="official_eod_feed",
        calculation_version="v1.0.0",
        candle_count=120,
        is_downsampled=False,
    )
    assert record.lineage_id == "lin_prc_12345"
    assert record.symbol == "THYAO"
    assert record.calculation_version == "v1.0.0"
    assert record.data_source == "official_eod_feed"
    assert "SPK" in record.disclaimer or "yatırım tavsiyesi" in record.disclaimer.lower()

    attr_text = record.to_display_attribution()
    assert "official_eod_feed" in attr_text
    assert "v1.0.0" in attr_text
    assert "lin_prc_12345" in attr_text


def test_displayed_prediction_lineage_model() -> None:
    record = DisplayedPredictionLineage(
        lineage_id="lin_prd_67890",
        symbol="ASELS",
        model_id="bist-trend-v1.0.2",
        model_version="1.0.2",
        calculation_version="v1.0.0",
        data_source="official_eod_feed",
        horizon_days=5,
        predicted_direction="upward",
        confidence_score=0.68,
        status="OK",
    )
    assert record.lineage_id == "lin_prd_67890"
    assert record.symbol == "ASELS"
    assert record.model_id == "bist-trend-v1.0.2"
    assert record.calculation_version == "v1.0.0"
    assert record.status == "OK"

    attr_text = record.to_display_attribution()
    assert "bist-trend-v1.0.2" in attr_text
    assert "v1.0.0" in attr_text
    assert "lin_prd_67890" in attr_text


def test_lineage_tracker_record_and_retrieve() -> None:
    tracker = LineageTracker(max_records=100)

    price_rec = tracker.record_price(
        symbol="GARAN",
        candle_count=50,
        data_source="official_eod_feed",
        price_type="adjusted",
        calculation_version="v1.0.0",
        trace_id="trace-test-1",
    )
    assert price_rec.lineage_id.startswith("lin_prc_")
    assert price_rec.symbol == "GARAN"
    assert price_rec.trace_id == "trace-test-1"

    pred_rec = tracker.record_prediction(
        symbol="GARAN",
        model_id="bist-trend-v1.0.2",
        model_version="1.0.2",
        calculation_version="v1.0.0",
        data_source="official_eod_feed",
        predicted_direction="neutral",
        trace_id="trace-test-1",
    )
    assert pred_rec.lineage_id.startswith("lin_prd_")
    assert pred_rec.symbol == "GARAN"
    assert pred_rec.trace_id == "trace-test-1"

    # Lookup by ID
    found_price = tracker.get_by_lineage_id(price_rec.lineage_id)
    assert found_price is not None
    assert found_price.lineage_id == price_rec.lineage_id

    found_pred = tracker.get_by_lineage_id(pred_rec.lineage_id)
    assert found_pred is not None
    assert found_pred.lineage_id == pred_rec.lineage_id

    # Lookup by symbol
    by_symbol = tracker.list_by_symbol("GARAN")
    assert len(by_symbol) == 2

    # Lookup by trace_id
    by_trace = tracker.list_by_trace_id("trace-test-1")
    assert len(by_trace) == 2


def test_lineage_tracker_ring_buffer_eviction() -> None:
    tracker = LineageTracker(max_records=10)

    records = []
    for i in range(15):
        rec = tracker.record_price(
            symbol="KCHOL",
            candle_count=i + 1,
            calculation_version=f"v1.{i}",
        )
        records.append(rec)

    assert tracker.count() == 10
    # Oldest 5 records (0..4) should have been evicted
    for i in range(5):
        assert tracker.get_by_lineage_id(records[i].lineage_id) is None

    # Newest 10 records (5..14) should still be retrievable
    for i in range(5, 15):
        assert tracker.get_by_lineage_id(records[i].lineage_id) is not None


def test_market_history_endpoint_returns_traceable_lineage(client: TestClient) -> None:
    response = client.get("/api/v1/market/history/THYAO?range=1m")
    assert response.status_code == 200

    data = response.json()
    assert data["symbol"] == "THYAO"
    assert data["data_source"] == "official_eod_feed"
    assert data["calculation_version"] == "v1.0.0"
    assert data["lineage_id"] is not None
    assert data["lineage_id"].startswith("lin_prc_")

    # Verify audit record was tracked in LINEAGE_TRACKER
    tracked = LINEAGE_TRACKER.get_by_lineage_id(data["lineage_id"])
    assert tracked is not None
    assert tracked.symbol == "THYAO"
    assert tracked.calculation_version == "v1.0.0"
    assert tracked.data_source == "official_eod_feed"


def test_forecast_endpoint_returns_traceable_lineage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Enable forecast feature flag
    monkeypatch.setenv("FORECAST_ENABLED", "true")

    response = client.get("/api/v1/inference/forecast/ASELS")
    assert response.status_code == 200

    data = response.json()
    assert data["symbol"] == "ASELS"
    assert data["model_version"] == "bist-trend-v1.0.2"
    assert data["calculation_version"] == "v1.0.0"
    assert data["data_source"] == "official_eod_feed"
    assert data["lineage_id"] is not None
    assert data["lineage_id"].startswith("lin_prd_")

    tracked = LINEAGE_TRACKER.get_by_lineage_id(data["lineage_id"])
    assert tracked is not None
    assert isinstance(tracked, DisplayedPredictionLineage)
    assert tracked.symbol == "ASELS"
    assert tracked.model_id == "bist-trend-v1.0.2"
    assert tracked.calculation_version == "v1.0.0"


def test_lineage_records_counter_metric() -> None:
    tracker = LineageTracker(max_records=50)
    initial_price_count = LINEAGE_RECORDS_TOTAL.get(
        labels={"record_type": "price", "data_source": "test_feed"}
    )
    initial_pred_count = LINEAGE_RECORDS_TOTAL.get(
        labels={"record_type": "prediction", "data_source": "test_feed"}
    )

    tracker.record_price(
        symbol="SISE",
        candle_count=10,
        data_source="test_feed",
    )
    tracker.record_prediction(
        symbol="SISE",
        model_id="test_model",
        model_version="1.0",
        data_source="test_feed",
    )

    new_price_count = LINEAGE_RECORDS_TOTAL.get(
        labels={"record_type": "price", "data_source": "test_feed"}
    )
    new_pred_count = LINEAGE_RECORDS_TOTAL.get(
        labels={"record_type": "prediction", "data_source": "test_feed"}
    )

    assert new_price_count == initial_price_count + 1.0
    assert new_pred_count == initial_pred_count + 1.0
