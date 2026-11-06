"""Operational telemetry, data freshness tracking, SLA monitoring, and queue/email metrics."""

from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    DATA_FRESHNESS_SECONDS,
    DATA_PROVIDER_HEALTH,
    EMAIL_DELIVERIES_TOTAL,
    EMAIL_DELIVERY_DURATION_SECONDS,
    HTTP_ERRORS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    QUEUE_LATENCY_SECONDS,
    QUEUE_PENDING_TASKS,
    QUEUE_PROCESSED_TASKS_TOTAL,
)
from stock_analysis.observability.provider_alerts import (
    PROVIDER_ALERT_MANAGER,
    ProviderAlertEvent,
)


class DataFreshnessStatus(StrEnum):
    """Categorized data freshness state based on market schedule and lag."""

    FRESH = "fresh"
    DELAYED_15M = "delayed_15m"
    STALE = "stale"
    MARKET_CLOSED = "market_closed"


class EmailDeliveryStatus(StrEnum):
    """Status outcomes for outbound notification emails."""

    DELIVERED = "delivered"
    FAILED = "failed"
    BOUNCED = "bounced"
    RATE_LIMITED = "rate_limited"
    DROPPED = "dropped"


@dataclass(frozen=True)
class DataFreshnessRecord:
    """Snapshot record of symbol market data freshness."""

    symbol: str
    provider: str
    latest_bar_utc: str
    lag_seconds: float
    status: DataFreshnessStatus
    is_stale: bool

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary."""
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _compute_percentile(samples: list[float], percentile: float) -> float:
    """Compute percentile for a list of float samples."""
    if not samples:
        return 0.0
    sorted_samples = sorted(samples)
    if percentile <= 0:
        return sorted_samples[0]
    if percentile >= 100:
        return sorted_samples[-1]
    k = (len(sorted_samples) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_samples[int(k)]
    d0 = sorted_samples[int(f)] * (c - k)
    d1 = sorted_samples[int(c)] * (k - f)
    return d0 + d1


class OperationalMonitor:
    """Thread-safe operational telemetry coordinator.

    Monitors:
    1. Market data freshness and provider availability.
    2. HTTP API error rate and p50/p90/p95/p99 latency SLA targets.
    3. Alert/task queue wait latency and pending queue depth.
    4. Outbound email delivery attempts, failure rates, and transmission duration.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._freshness_records: dict[str, DataFreshnessRecord] = {}
        self._queue_stats: dict[str, dict[str, Any]] = {}
        self._email_stats: dict[str, int] = {
            "total": 0,
            "delivered": 0,
            "failed": 0,
            "bounced": 0,
            "rate_limited": 0,
            "dropped": 0,
        }

    # -------------------------------------------------------------------------
    # 1. Market Data Freshness
    # -------------------------------------------------------------------------

    def record_data_freshness(
        self,
        symbol: str,
        provider: str,
        latest_bar_utc: datetime,
        is_market_open: bool = True,
        now_utc: datetime | None = None,
    ) -> DataFreshnessRecord:
        """Evaluate and record data age for a given symbol and provider.

        Thresholds:
        - If market is closed: MARKET_CLOSED (acceptable stale)
        - If lag <= 90s: FRESH
        - If 90s < lag <= 1200s (20m): DELAYED_15M
        - If lag > 1200s: STALE (triggers alarm flag)
        """
        now = now_utc or datetime.now(UTC)
        if latest_bar_utc.tzinfo is None:
            bar_time = latest_bar_utc.replace(tzinfo=UTC)
        else:
            bar_time = latest_bar_utc

        lag_seconds = max(0.0, (now - bar_time).total_seconds())

        if not is_market_open:
            status = DataFreshnessStatus.MARKET_CLOSED
            is_stale = False
        elif lag_seconds <= 90.0:
            status = DataFreshnessStatus.FRESH
            is_stale = False
        elif lag_seconds <= 1200.0:
            status = DataFreshnessStatus.DELAYED_15M
            is_stale = False
        else:
            status = DataFreshnessStatus.STALE
            is_stale = True

        labels = {"symbol": symbol.upper(), "provider": provider}
        DATA_FRESHNESS_SECONDS.set(lag_seconds, labels=labels)
        DATA_PROVIDER_HEALTH.set(0.0 if is_stale else 1.0, labels={"provider": provider})

        record = DataFreshnessRecord(
            symbol=symbol.upper(),
            provider=provider,
            latest_bar_utc=bar_time.isoformat(),
            lag_seconds=round(lag_seconds, 2),
            status=status,
            is_stale=is_stale,
        )

        with self._lock:
            self._freshness_records[f"{symbol.upper()}:{provider}"] = record

        return record

    def get_data_freshness_records(self) -> list[DataFreshnessRecord]:
        """Return list of all tracked symbol freshness records."""
        with self._lock:
            return list(self._freshness_records.values())

    # -------------------------------------------------------------------------
    # 2. API Error Rate and Latency (p50 / p90 / p95 / p99)
    # -------------------------------------------------------------------------

    def get_api_error_rate(self, endpoint: str | None = None) -> float:
        """Calculate HTTP 5xx error rate as a float [0.0, 1.0]."""
        if endpoint:
            with HTTP_REQUESTS_TOTAL._lock:
                total_reqs = sum(
                    val
                    for key, val in HTTP_REQUESTS_TOTAL._values.items()
                    if dict(key).get("path") == endpoint
                )
            with HTTP_ERRORS_TOTAL._lock:
                total_errs = sum(
                    val
                    for key, val in HTTP_ERRORS_TOTAL._values.items()
                    if dict(key).get("path") == endpoint
                )
        else:
            total_reqs = HTTP_REQUESTS_TOTAL.total()
            total_errs = HTTP_ERRORS_TOTAL.total()

        if total_reqs <= 0.0:
            return 0.0
        return min(1.0, total_errs / total_reqs)

    def get_api_latency_percentiles(self, endpoint: str | None = None) -> dict[str, float]:
        """Compute p50, p90, p95, p99 latencies in milliseconds."""
        if endpoint:
            with HTTP_REQUEST_DURATION_SECONDS._lock:
                samples: list[float] = []
                for key, sample_list in HTTP_REQUEST_DURATION_SECONDS._samples.items():
                    if dict(key).get("path") == endpoint:
                        samples.extend(sample_list)
            p50_s = _compute_percentile(samples, 50.0)
            p90_s = _compute_percentile(samples, 90.0)
            p95_s = _compute_percentile(samples, 95.0)
            p99_s = _compute_percentile(samples, 99.0)
        else:
            p50_s = HTTP_REQUEST_DURATION_SECONDS.get_percentile(50.0)
            p90_s = HTTP_REQUEST_DURATION_SECONDS.get_percentile(90.0)
            p95_s = HTTP_REQUEST_DURATION_SECONDS.get_percentile(95.0)
            p99_s = HTTP_REQUEST_DURATION_SECONDS.get_percentile(99.0)

        return {
            "p50_ms": round(p50_s * 1000.0, 2),
            "p90_ms": round(p90_s * 1000.0, 2),
            "p95_ms": round(p95_s * 1000.0, 2),
            "p99_ms": round(p99_s * 1000.0, 2),
        }

    # -------------------------------------------------------------------------
    # 3. Queue Latency and Depth
    # -------------------------------------------------------------------------

    def record_queue_job(
        self,
        queue_name: str,
        wait_duration_seconds: float,
        queue_depth: int,
        status: str = "completed",
    ) -> None:
        """Record queue task wait latency, resulting status, and updated depth."""
        QUEUE_LATENCY_SECONDS.observe(max(0.0, wait_duration_seconds), labels={"queue": queue_name})
        QUEUE_PENDING_TASKS.set(max(0, queue_depth), labels={"queue": queue_name})
        QUEUE_PROCESSED_TASKS_TOTAL.inc(labels={"queue": queue_name, "status": status})

        with self._lock:
            if queue_name not in self._queue_stats:
                self._queue_stats[queue_name] = {
                    "pending_depth": queue_depth,
                    "processed_count": 0,
                    "last_wait_seconds": round(wait_duration_seconds, 4),
                }
            self._queue_stats[queue_name]["pending_depth"] = queue_depth
            self._queue_stats[queue_name]["processed_count"] += 1
            self._queue_stats[queue_name]["last_wait_seconds"] = round(wait_duration_seconds, 4)

    def get_queue_summary(self, queue_name: str | None = None) -> dict[str, Any]:
        """Retrieve queue telemetry summary including p95 wait latency."""
        with self._lock:
            queues = [queue_name] if queue_name else list(self._queue_stats.keys())
            summary: dict[str, Any] = {}
            for q in queues:
                stats = self._queue_stats.get(
                    q, {"pending_depth": 0, "processed_count": 0, "last_wait_seconds": 0.0}
                )
                p95_wait = QUEUE_LATENCY_SECONDS.get_percentile(95.0, labels={"queue": q})
                summary[q] = {
                    "pending_depth": stats.get("pending_depth", 0),
                    "processed_count": stats.get("processed_count", 0),
                    "p95_wait_seconds": round(p95_wait, 4),
                }
            return summary

    # -------------------------------------------------------------------------
    # 4. Email Delivery Tracking
    # -------------------------------------------------------------------------

    def record_email_delivery(
        self,
        provider: str,
        status: EmailDeliveryStatus | str,
        duration_seconds: float,
        template: str = "alert",
    ) -> None:
        """Record an outbound email transmission attempt."""
        status_str = status.value if isinstance(status, EmailDeliveryStatus) else str(status)
        EMAIL_DELIVERIES_TOTAL.inc(
            labels={"provider": provider, "status": status_str, "template": template}
        )
        EMAIL_DELIVERY_DURATION_SECONDS.observe(
            max(0.0, duration_seconds),
            labels={"provider": provider, "status": status_str},
        )

        with self._lock:
            self._email_stats["total"] += 1
            if status_str in self._email_stats:
                self._email_stats[status_str] += 1
            else:
                self._email_stats[status_str] = 1

    def get_email_delivery_summary(self) -> dict[str, Any]:
        """Calculate aggregate email delivery rates and transmission latency."""
        with self._lock:
            total = self._email_stats.get("total", 0)
            delivered = self._email_stats.get("delivered", 0)
            failed = (
                self._email_stats.get("failed", 0)
                + self._email_stats.get("bounced", 0)
                + self._email_stats.get("dropped", 0)
            )
            success_rate = (delivered / total) if total > 0 else 1.0

        p95_duration = EMAIL_DELIVERY_DURATION_SECONDS.get_percentile(95.0)

        return {
            "total_attempts": total,
            "delivered": delivered,
            "failed": failed,
            "success_rate": round(success_rate, 4),
            "p95_duration_seconds": round(p95_duration, 4),
        }

    # -------------------------------------------------------------------------
    # 5. Composite Operational Health Report & Provider Telemetry
    # -------------------------------------------------------------------------

    def record_provider_call(
        self,
        provider: str,
        success: bool,
        status_code: int = 200,
        error_message: str = "",
        is_timeout: bool = False,
        calls_made: int | None = None,
        daily_limit: int | None = None,
        now_utc: datetime | None = None,
    ) -> list[ProviderAlertEvent]:
        """Record provider call outcome and trigger outage or quota alerts as needed."""
        events: list[ProviderAlertEvent] = []
        if success:
            events.extend(PROVIDER_ALERT_MANAGER.record_success(provider, now_utc=now_utc))
        else:
            events.extend(
                PROVIDER_ALERT_MANAGER.record_failure(
                    provider=provider,
                    status_code=status_code,
                    error_message=error_message,
                    is_timeout=is_timeout,
                    now_utc=now_utc,
                )
            )

        if calls_made is not None and daily_limit is not None:
            events.extend(
                PROVIDER_ALERT_MANAGER.record_quota_usage(
                    provider=provider,
                    calls_made=calls_made,
                    daily_limit=daily_limit,
                    now_utc=now_utc,
                )
            )
        return events

    def get_operational_report(
        self,
        p95_sla_seconds: float = 0.30,
    ) -> dict[str, Any]:
        """Produce an end-to-end operational dashboard report."""
        latencies = self.get_api_latency_percentiles()
        error_rate = self.get_api_error_rate()
        p95_ms = latencies["p95_ms"]
        p95_sla_ms = p95_sla_seconds * 1000.0
        sla_breached = p95_ms > p95_sla_ms

        freshness_records = self.get_data_freshness_records()
        stale_symbols = [r.symbol for r in freshness_records if r.is_stale]

        email_summary = self.get_email_delivery_summary()
        queue_summary = self.get_queue_summary()

        active_outages = PROVIDER_ALERT_MANAGER.get_active_outages()
        provider_statuses = {
            k: v.to_dict() for k, v in PROVIDER_ALERT_MANAGER.get_all_statuses().items()
        }
        recent_provider_alerts = PROVIDER_ALERT_MANAGER.get_recent_alerts(limit=10)

        # Determine overall system status
        if error_rate > 0.20 or len(active_outages) > 0:
            overall_status = "critical"
        elif error_rate > 0.05 or sla_breached or len(stale_symbols) > 0:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return {
            "status": overall_status,
            "timestamp": datetime.now(UTC).isoformat(),
            "api": {
                "total_requests": int(HTTP_REQUESTS_TOTAL.total()),
                "total_errors": int(HTTP_ERRORS_TOTAL.total()),
                "error_rate": round(error_rate, 4),
                "latencies_ms": latencies,
                "p95_sla_breached": sla_breached,
                "p95_sla_target_ms": p95_sla_ms,
            },
            "data_freshness": {
                "tracked_symbols_count": len(freshness_records),
                "stale_symbols": stale_symbols,
                "items": [r.to_dict() for r in freshness_records],
            },
            "provider_health": {
                "active_outages": active_outages,
                "providers": provider_statuses,
                "recent_alerts": recent_provider_alerts,
            },
            "queues": queue_summary,
            "email_delivery": email_summary,
        }

    def reset(self) -> None:
        """Clear local records and telemetry for test isolation."""
        with self._lock:
            self._freshness_records.clear()
            self._queue_stats.clear()
            self._email_stats = {
                "total": 0,
                "delivered": 0,
                "failed": 0,
                "bounced": 0,
                "rate_limited": 0,
                "dropped": 0,
            }
        PROVIDER_ALERT_MANAGER.reset()


# Default global operational monitor instance
OPERATIONAL_MONITOR = OperationalMonitor()
