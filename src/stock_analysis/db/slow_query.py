"""Slow query monitoring and logging for SQLAlchemy engines."""

from __future__ import annotations

import logging
import re
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, CursorResult, Engine

logger = logging.getLogger("stock_analysis.db.slow_query")


@dataclass
class SlowQueryEvent:
    """Record of an individual slow query event."""

    statement: str
    duration_ms: float
    threshold_ms: float
    timestamp: float


class SlowQueryTracker:
    """Thread-safe statistics collector for database queries and slow queries."""

    def __init__(self, default_threshold_ms: float = 200.0) -> None:
        self.threshold_ms: float = default_threshold_ms
        self._lock = threading.Lock()
        self.total_queries: int = 0
        self.slow_queries_count: int = 0
        self.total_duration_ms: float = 0.0
        self.max_duration_ms: float = 0.0
        self.recent_slow_queries: list[SlowQueryEvent] = []
        self._callbacks: list[Callable[[SlowQueryEvent], None]] = []

    def set_threshold(self, threshold_ms: float) -> None:
        """Update slow query alert threshold in milliseconds."""
        with self._lock:
            self.threshold_ms = max(0.0, threshold_ms)

    def add_callback(self, callback: Callable[[SlowQueryEvent], None]) -> None:
        """Register an alert/metric callback fired when a slow query is detected."""
        with self._lock:
            self._callbacks.append(callback)

    def reset(self) -> None:
        """Reset all counters and event history."""
        with self._lock:
            self.total_queries = 0
            self.slow_queries_count = 0
            self.total_duration_ms = 0.0
            self.max_duration_ms = 0.0
            self.recent_slow_queries.clear()

    def record_query(self, statement: str, duration_ms: float) -> SlowQueryEvent | None:
        """Record query execution time and return SlowQueryEvent if exceeded threshold."""
        slow_event: SlowQueryEvent | None = None
        callbacks_to_fire: list[Callable[[SlowQueryEvent], None]] = []

        with self._lock:
            self.total_queries += 1
            self.total_duration_ms += duration_ms
            if duration_ms > self.max_duration_ms:
                self.max_duration_ms = duration_ms

            if duration_ms >= self.threshold_ms:
                self.slow_queries_count += 1
                sanitized = self._sanitize_statement(statement)
                slow_event = SlowQueryEvent(
                    statement=sanitized,
                    duration_ms=duration_ms,
                    threshold_ms=self.threshold_ms,
                    timestamp=time.time(),
                )
                self.recent_slow_queries.append(slow_event)
                if len(self.recent_slow_queries) > 100:
                    self.recent_slow_queries.pop(0)
                callbacks_to_fire = list(self._callbacks)

        if slow_event is not None:
            logger.warning(
                "Slow query detected: %.2fms (threshold: %.2fms) | %s",
                slow_event.duration_ms,
                slow_event.threshold_ms,
                slow_event.statement,
            )
            for cb in callbacks_to_fire:
                try:
                    cb(slow_event)
                except Exception:
                    logger.exception("Error in slow query callback")

        return slow_event

    @staticmethod
    def _sanitize_statement(statement: str) -> str:
        """Normalize whitespaces and prevent multiline bloat in logs."""
        clean = re.sub(r"\s+", " ", statement).strip()
        if len(clean) > 500:
            return clean[:500] + "... [truncated]"
        return clean


# Global tracker singleton
tracker = SlowQueryTracker()
_registered_engines: weakref.WeakSet[Engine] = weakref.WeakSet()


def register_slow_query_logger(
    engine: Engine,
    threshold_ms: float = 200.0,
    callback: Callable[[SlowQueryEvent], None] | None = None,
) -> None:
    """Attach SQLAlchemy event listeners to log queries exceeding threshold_ms."""
    tracker.set_threshold(threshold_ms)
    if callback is not None:
        tracker.add_callback(callback)

    # Prevent attaching multiple times to the same engine
    if engine in _registered_engines:
        return
    _registered_engines.add(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if context is not None:
            context._query_start_perf = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(
        conn: Connection,
        cursor: CursorResult[Any],
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if context is not None and hasattr(context, "_query_start_perf"):
            start = context._query_start_perf
            duration_ms = (time.perf_counter() - start) * 1000.0
            tracker.record_query(statement, duration_ms)


def get_slow_query_tracker() -> SlowQueryTracker:
    """Return the global slow query tracker."""
    return tracker
