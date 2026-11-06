"""Tests for database slow query monitoring, tracking, and logging."""

import logging

import pytest
from sqlalchemy import create_engine, text

from stock_analysis.db.slow_query import (
    SlowQueryEvent,
    SlowQueryTracker,
    get_slow_query_tracker,
    register_slow_query_logger,
)


def test_slow_query_tracker_normal_and_slow() -> None:
    """Test tracker increments counts and flags only queries exceeding threshold."""
    tracker = SlowQueryTracker(default_threshold_ms=50.0)

    # 1. Normal query below threshold
    event = tracker.record_query("SELECT 1 FROM instruments", duration_ms=12.5)
    assert event is None
    assert tracker.total_queries == 1
    assert tracker.slow_queries_count == 0
    assert tracker.max_duration_ms == 12.5

    # 2. Slow query exceeding threshold
    event2 = tracker.record_query("SELECT * FROM market_bars WHERE id = 1", duration_ms=85.0)
    assert event2 is not None
    assert isinstance(event2, SlowQueryEvent)
    assert event2.duration_ms == 85.0
    assert event2.threshold_ms == 50.0
    assert tracker.total_queries == 2
    assert tracker.slow_queries_count == 1
    assert tracker.max_duration_ms == 85.0
    assert len(tracker.recent_slow_queries) == 1


def test_slow_query_tracker_callbacks() -> None:
    """Test callback is triggered when slow query occurs."""
    tracker = SlowQueryTracker(default_threshold_ms=30.0)
    received: list[SlowQueryEvent] = []

    tracker.add_callback(lambda ev: received.append(ev))

    # Trigger normal query
    tracker.record_query("SELECT * FROM instruments", duration_ms=10.0)
    assert len(received) == 0

    # Trigger slow query
    tracker.record_query("SELECT * FROM market_bars", duration_ms=45.0)
    assert len(received) == 1
    assert received[0].duration_ms == 45.0
    assert "market_bars" in received[0].statement


def test_slow_query_statement_sanitization_and_truncation() -> None:
    """Test query whitespace normalization and length truncation."""
    tracker = SlowQueryTracker(default_threshold_ms=10.0)
    long_sql = "SELECT " + ("col, " * 150) + "1"
    ev = tracker.record_query("   SELECT   *\n\n   FROM   instruments\n   ", duration_ms=25.0)
    assert ev is not None
    assert ev.statement == "SELECT * FROM instruments"

    ev_long = tracker.record_query(long_sql, duration_ms=20.0)
    assert ev_long is not None
    assert ev_long.statement.endswith("... [truncated]")


def test_slow_query_logger_sqlalchemy_engine(caplog: pytest.LogCaptureFixture) -> None:
    """Test engine query interception via SQLAlchemy events."""
    engine = create_engine("sqlite:///:memory:")
    fired_events: list[SlowQueryEvent] = []

    # Set very low threshold (0.0 ms) to capture any executed query
    register_slow_query_logger(
        engine, threshold_ms=0.0, callback=lambda ev: fired_events.append(ev)
    )

    with caplog.at_level(logging.WARNING):
        with engine.connect() as conn:
            conn.execute(text("SELECT 42 AS num"))

    # Engine query should be detected as slow because threshold is 0.0ms
    assert len(fired_events) >= 1
    assert any("SELECT 42" in ev.statement for ev in fired_events)


def test_slow_query_tracker_reset_and_threshold_update() -> None:
    """Test updating threshold and resetting tracker state."""
    tracker = get_slow_query_tracker()
    tracker.reset()
    assert tracker.total_queries == 0
    assert tracker.slow_queries_count == 0

    tracker.set_threshold(150.0)
    assert tracker.threshold_ms == 150.0

    # Below new threshold
    assert tracker.record_query("SELECT 1", 100.0) is None
    # Above new threshold
    assert tracker.record_query("SELECT 2", 160.0) is not None
    assert tracker.slow_queries_count == 1
