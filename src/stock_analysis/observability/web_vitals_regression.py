"""Web Vitals and API p95 latency regression detection and alerting engine.

Provides automated client-side Web Vitals telemetry ingestion (LCP, INP, CLS, FCP, TTFB),
rolling percentiles (p50, p75, p95, p99), baseline regression detection, SLA breach
alarms, cooldown deduplication, and Prometheus exposition.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    API_P95_LATENCY_SECONDS,
    API_P95_REGRESSION_ALERTS_TOTAL,
    WEB_VITALS_REGRESSION_ALERTS_TOTAL,
    WEB_VITALS_TOTAL,
    WEB_VITALS_VALUE_GAUGE,
)

logger = logging.getLogger("stock_analysis.observability.web_vitals_regression")


class WebVitalName(StrEnum):
    """Core Web Vitals and supplementary front-end performance metric identifiers."""

    LCP = "LCP"  # Largest Contentful Paint (ms)
    INP = "INP"  # Interaction to Next Paint (ms)
    CLS = "CLS"  # Cumulative Layout Shift (unitless score)
    FCP = "FCP"  # First Contentful Paint (ms)
    TTFB = "TTFB"  # Time to First Byte (ms)


class WebVitalRating(StrEnum):
    """Performance rating tier aligned with Google Web Vitals criteria."""

    GOOD = "good"
    NEEDS_IMPROVEMENT = "needs_improvement"
    POOR = "poor"


class PerformanceAlertSeverity(StrEnum):
    """Severity tier for performance regression alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PerformanceAlertType(StrEnum):
    """Categorized alarm trigger type for API latency and Web Vitals."""

    P95_SLA_BREACH = "p95_sla_breach"
    P95_REGRESSION_SPIKE = "p95_regression_spike"
    P95_RECOVERED = "p95_recovered"
    WEB_VITAL_POOR_THRESHOLD = "web_vital_poor_threshold"
    WEB_VITAL_REGRESSION = "web_vital_regression"
    WEB_VITAL_RECOVERED = "web_vital_recovered"


# Official Web Vitals thresholds (good upper bound, needs_improvement upper bound)
WEB_VITALS_THRESHOLDS: dict[WebVitalName, tuple[float, float]] = {
    WebVitalName.LCP: (2500.0, 4000.0),  # ms
    WebVitalName.INP: (200.0, 500.0),  # ms
    WebVitalName.CLS: (0.1, 0.25),  # score
    WebVitalName.FCP: (1800.0, 3000.0),  # ms
    WebVitalName.TTFB: (800.0, 1800.0),  # ms
}


def classify_web_vital(metric: str | WebVitalName, value: float) -> WebVitalRating:
    """Classify a raw Web Vital metric value into good, needs_improvement, or poor."""
    try:
        vital_name = WebVitalName(str(metric).upper())
    except ValueError:
        return WebVitalRating.NEEDS_IMPROVEMENT

    good_thresh, poor_thresh = WEB_VITALS_THRESHOLDS[vital_name]
    if value <= good_thresh:
        return WebVitalRating.GOOD
    if value <= poor_thresh:
        return WebVitalRating.NEEDS_IMPROVEMENT
    return WebVitalRating.POOR


_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUMERIC_ID_PATTERN = re.compile(r"/\d+(?=/|$)")


def sanitize_route(route: str | None) -> str:
    """Normalize and sanitize client routes to prevent high-cardinality metric explosion."""
    if not route:
        return "/"
    clean = route.strip().split("?")[0].split("#")[0]
    if not clean.startswith("/"):
        clean = "/" + clean
    # Remove trailing slash unless root
    if len(clean) > 1 and clean.endswith("/"):
        clean = clean[:-1]
    # Replace UUIDs and numeric IDs with tokens
    clean = _UUID_PATTERN.sub(":id", clean)
    clean = _NUMERIC_ID_PATTERN.sub("/:id", clean)
    # Clamp length
    if len(clean) > 96:
        clean = clean[:96]
    return clean or "/"


def calculate_percentile(values: Sequence[float], percentile: float) -> float:
    """Calculate the p-th percentile (0.0 to 100.0) using linear interpolation."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    k = (len(values) - 1) * (percentile / 100.0)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return float(values[f])
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return float(d0 + d1)


@dataclass(frozen=True)
class WebVitalSample:
    """Immutable single report of a Web Vital measurement."""

    metric_name: WebVitalName
    value: float
    rating: WebVitalRating
    route: str
    timestamp_utc: str
    navigation_type: str = "navigate"

    def to_dict(self) -> dict[str, Any]:
        """Convert sample to JSON serializable dictionary."""
        data = asdict(self)
        data["metric_name"] = self.metric_name.value
        data["rating"] = self.rating.value
        return data


@dataclass(frozen=True)
class WebVitalSummary:
    """Aggregated summary of a Web Vital metric over a rolling window."""

    metric_name: str
    route: str
    sample_count: int
    p75_value: float
    p90_value: float
    mean_value: float
    ratings_breakdown: dict[str, int]
    poor_ratio: float
    status: str  # healthy, degraded, critical

    def to_dict(self) -> dict[str, Any]:
        """Convert summary to JSON dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class EndpointLatencySummary:
    """Rolling latency statistical profile for an API endpoint group."""

    endpoint_group: str
    sample_count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    baseline_p95_ms: float
    max_sla_p95_ms: float
    regression_ratio: float
    is_regressed: bool
    status: str  # normal, warning, critical

    def to_dict(self) -> dict[str, Any]:
        """Convert summary to JSON dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class PerformanceAlertEvent:
    """Immutable event payload generated when an SLA breach or regression triggers."""

    alert_id: str
    alert_type: PerformanceAlertType
    target: str
    severity: PerformanceAlertSeverity
    message: str
    current_value: float
    threshold_or_baseline: float
    timestamp_utc: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert alert event to a JSON serializable dictionary."""
        data = asdict(self)
        data["alert_type"] = self.alert_type.value
        data["severity"] = self.severity.value
        return data


class BasePerformanceAlertHandler:
    """Base interface for dispatching performance regression alert events."""

    def handle_alert(self, event: PerformanceAlertEvent) -> None:
        """Process or forward an alert event."""
        raise NotImplementedError


class LogPerformanceAlertHandler(BasePerformanceAlertHandler):
    """Logs performance alert events with structured attributes."""

    def handle_alert(self, event: PerformanceAlertEvent) -> None:
        log_attrs = {
            "alert_id": event.alert_id,
            "target": event.target,
            "alert_type": event.alert_type.value,
            "severity": event.severity.value,
            "current_value": event.current_value,
            "threshold_or_baseline": event.threshold_or_baseline,
            "details": event.details,
        }
        if event.severity == PerformanceAlertSeverity.CRITICAL:
            logger.error(
                "PERFORMANCE REGRESSION [%s] %s: %s (current: %.2f, baseline/sla: %.2f)",
                event.target,
                event.alert_type.value.upper(),
                event.message,
                event.current_value,
                event.threshold_or_baseline,
                extra=log_attrs,
            )
        elif event.severity == PerformanceAlertSeverity.WARNING:
            logger.warning(
                "PERFORMANCE REGRESSION [%s] %s: %s (current: %.2f, baseline/sla: %.2f)",
                event.target,
                event.alert_type.value.upper(),
                event.message,
                event.current_value,
                event.threshold_or_baseline,
                extra=log_attrs,
            )
        else:
            logger.info(
                "PERFORMANCE ALERT [%s] %s: %s",
                event.target,
                event.alert_type.value.upper(),
                event.message,
                extra=log_attrs,
            )


class CallbackPerformanceAlertHandler(BasePerformanceAlertHandler):
    """Executes a custom callable callback when an alert event is raised."""

    def __init__(self, callback: Callable[[PerformanceAlertEvent], None]) -> None:
        self._callback = callback

    def handle_alert(self, event: PerformanceAlertEvent) -> None:
        try:
            self._callback(event)
        except Exception:
            logger.exception("CallbackPerformanceAlertHandler raised unhandled exception")


class InMemoryPerformanceAlertHistoryHandler(BasePerformanceAlertHandler):
    """Maintains a bounded in-memory audit log of recent performance alert events."""

    def __init__(self, max_history: int = 100) -> None:
        self._max_history = max_history
        self._history: deque[PerformanceAlertEvent] = deque(maxlen=max_history)
        self._lock = threading.Lock()

    def handle_alert(self, event: PerformanceAlertEvent) -> None:
        with self._lock:
            self._history.append(event)

    def get_history(self, limit: int = 50) -> list[PerformanceAlertEvent]:
        with self._lock:
            items = list(self._history)
            return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


@dataclass
class PerformanceRegressionConfig:
    """Tunable thresholds for API p95 regression and Web Vitals degradation."""

    window_size: int = 200
    min_samples_for_regression: int = 15
    p95_regression_threshold_pct: float = 30.0  # +30% above baseline triggers warning
    p95_min_delta_ms: float = 40.0  # minimum absolute regression in ms
    default_p95_baseline_ms: float = 120.0
    default_p95_sla_ms: float = 400.0
    endpoint_baselines: dict[str, float] = field(
        default_factory=lambda: {
            "/api/v1/market": 100.0,
            "/api/v1/forecasts": 250.0,
            "/api/v1/analytics": 180.0,
            "/api/v1/alerts": 120.0,
            "/status": 80.0,
        }
    )
    endpoint_slas: dict[str, float] = field(
        default_factory=lambda: {
            "/api/v1/market": 300.0,
            "/api/v1/forecasts": 600.0,
            "/api/v1/analytics": 450.0,
            "/api/v1/alerts": 350.0,
            "/status": 200.0,
        }
    )
    web_vitals_poor_ratio_alarm_threshold: float = 0.20  # >= 20% poor samples triggers alert
    alert_cooldown_seconds: float = 300.0  # 5 min deduplication cooldown


class PerformanceRegressionCoordinator:
    """Thread-safe coordinator for Web Vitals telemetry, p95 regression, and alerting."""

    def __init__(
        self,
        config: PerformanceRegressionConfig | None = None,
        handlers: list[BasePerformanceAlertHandler] | None = None,
    ) -> None:
        self.config = config or PerformanceRegressionConfig()
        self._lock = threading.Lock()

        # Bounded deques for metrics
        # Key: (metric_name, route) -> deque of WebVitalSample
        self._web_vitals: dict[tuple[str, str], deque[WebVitalSample]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )
        # Key: endpoint_group -> deque of latency float in ms
        self._api_latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )

        # Alarm state tracking: key -> (is_regressed, last_alert_time, last_alert_type)
        self._api_alert_state: dict[str, tuple[bool, datetime, PerformanceAlertType]] = {}
        self._vitals_alert_state: dict[str, tuple[bool, datetime, PerformanceAlertType]] = {}

        # Alert handlers
        self._history_handler = InMemoryPerformanceAlertHistoryHandler()
        self._handlers: list[BasePerformanceAlertHandler] = handlers or [
            LogPerformanceAlertHandler(),
            self._history_handler,
        ]

    def add_handler(self, handler: BasePerformanceAlertHandler) -> None:
        """Register an additional performance alert handler."""
        with self._lock:
            self._handlers.append(handler)

    def _dispatch_alert(self, event: PerformanceAlertEvent) -> None:
        """Deliver alert event to all registered handlers."""
        for handler in self._handlers:
            try:
                handler.handle_alert(event)
            except Exception:
                logger.exception("Performance alert handler failed to process event")

    def record_web_vital(
        self,
        metric_name: str | WebVitalName,
        value: float,
        route: str = "/",
        navigation_type: str = "navigate",
        rating: str | None = None,
    ) -> WebVitalSample:
        """Ingest and record a single client Web Vital report."""
        if value < 0:
            raise ValueError(f"Web vital value must be non-negative, got {value}")

        try:
            vital_name = WebVitalName(str(metric_name).upper())
        except ValueError as err:
            valid_names = [m.value for m in WebVitalName]
            raise ValueError(
                f"Unsupported Web Vital name: {metric_name}. Supported: {valid_names}"
            ) from err

        clean_route = sanitize_route(route)
        assigned_rating = (
            WebVitalRating(rating.lower())
            if rating and rating.lower() in [r.value for r in WebVitalRating]
            else classify_web_vital(vital_name, value)
        )

        now_utc = datetime.now(UTC)
        sample = WebVitalSample(
            metric_name=vital_name,
            value=round(value, 4),
            rating=assigned_rating,
            route=clean_route,
            timestamp_utc=now_utc.isoformat(),
            navigation_type=navigation_type[:32],
        )

        # Update Prometheus metrics
        WEB_VITALS_TOTAL.inc(
            labels={
                "metric_name": vital_name.value,
                "rating": assigned_rating.value,
                "route": clean_route,
            }
        )
        WEB_VITALS_VALUE_GAUGE.set(
            value,
            labels={"metric_name": vital_name.value, "route": clean_route},
        )

        with self._lock:
            bucket = self._web_vitals[(vital_name.value, clean_route)]
            bucket.append(sample)
            self._evaluate_web_vitals_regression(vital_name, clean_route, now_utc)

        return sample

    def _evaluate_web_vitals_regression(
        self, vital_name: WebVitalName, route: str, now: datetime
    ) -> None:
        """Internal lock-held check for Web Vitals degradation."""
        bucket = self._web_vitals[(vital_name.value, route)]
        if len(bucket) < self.config.min_samples_for_regression:
            return

        target_key = f"{vital_name.value}@{route}"
        poor_samples = sum(1 for s in bucket if s.rating == WebVitalRating.POOR)
        poor_ratio = poor_samples / len(bucket)

        values_sorted = sorted(s.value for s in bucket)
        p75 = calculate_percentile(values_sorted, 75.0)
        good_thresh, poor_thresh = WEB_VITALS_THRESHOLDS[vital_name]

        is_regressed, last_alert_time, last_alert_type = self._vitals_alert_state.get(
            target_key,
            (False, datetime.min.replace(tzinfo=UTC), PerformanceAlertType.WEB_VITAL_RECOVERED),
        )
        elapsed = (now - last_alert_time).total_seconds()
        in_cooldown = elapsed < self.config.alert_cooldown_seconds

        # Condition 1: Poor ratio threshold exceeded OR p75 exceeds poor threshold
        if poor_ratio >= self.config.web_vitals_poor_ratio_alarm_threshold or p75 > poor_thresh:
            if not is_regressed or (
                not in_cooldown and last_alert_type != PerformanceAlertType.WEB_VITAL_REGRESSION
            ):
                alert = PerformanceAlertEvent(
                    alert_id=f"wv-alert-{uuid.uuid4().hex[:10]}",
                    alert_type=PerformanceAlertType.WEB_VITAL_REGRESSION,
                    target=target_key,
                    severity=PerformanceAlertSeverity.WARNING,
                    message=(
                        f"Web Vital '{vital_name.value}' on route '{route}' regressed: "
                        f"p75={p75:.2f} (poor threshold: {poor_thresh:.2f}), "
                        f"poor ratio={poor_ratio * 100:.1f}% "
                        f"(limit: {self.config.web_vitals_poor_ratio_alarm_threshold * 100:.0f}%)"
                    ),
                    current_value=round(p75, 2),
                    threshold_or_baseline=poor_thresh,
                    timestamp_utc=now.isoformat(),
                    details={
                        "metric_name": vital_name.value,
                        "route": route,
                        "p75": round(p75, 2),
                        "poor_ratio": round(poor_ratio, 4),
                        "samples": len(bucket),
                    },
                )
                self._vitals_alert_state[target_key] = (
                    True,
                    now,
                    PerformanceAlertType.WEB_VITAL_REGRESSION,
                )
                WEB_VITALS_REGRESSION_ALERTS_TOTAL.inc(
                    labels={
                        "metric_name": vital_name.value,
                        "route": route,
                        "severity": PerformanceAlertSeverity.WARNING.value,
                    }
                )
                self._dispatch_alert(alert)

        # Condition 2: Recovered back to healthy p75 and low poor ratio
        elif (
            is_regressed
            and p75 <= good_thresh
            and poor_ratio < self.config.web_vitals_poor_ratio_alarm_threshold
        ):
            alert = PerformanceAlertEvent(
                alert_id=f"wv-recov-{uuid.uuid4().hex[:10]}",
                alert_type=PerformanceAlertType.WEB_VITAL_RECOVERED,
                target=target_key,
                severity=PerformanceAlertSeverity.INFO,
                message=(
                    f"Web Vital '{vital_name.value}' on route '{route}' recovered to healthy: "
                    f"p75={p75:.2f} (target <= {good_thresh:.2f}), "
                    f"poor ratio={poor_ratio * 100:.1f}%"
                ),
                current_value=round(p75, 2),
                threshold_or_baseline=good_thresh,
                timestamp_utc=now.isoformat(),
                details={"p75": round(p75, 2), "poor_ratio": round(poor_ratio, 4)},
            )
            self._vitals_alert_state[target_key] = (
                False,
                now,
                PerformanceAlertType.WEB_VITAL_RECOVERED,
            )
            self._dispatch_alert(alert)

    def record_api_latency(
        self, endpoint_group: str, latency_ms: float, status_code: int = 200
    ) -> None:
        """Record an API response latency observation and check for p95 regression."""
        if latency_ms < 0:
            latency_ms = 0.0

        clean_group = sanitize_route(endpoint_group)
        now_utc = datetime.now(UTC)

        with self._lock:
            bucket = self._api_latencies[clean_group]
            bucket.append(latency_ms)

            # Calculate rolling p95
            sorted_latencies = sorted(bucket)
            p95 = calculate_percentile(sorted_latencies, 95.0)

            # Update Prometheus metric (in seconds)
            API_P95_LATENCY_SECONDS.set(
                round(p95 / 1000.0, 4), labels={"endpoint_group": clean_group}
            )

            # Evaluate regression alarm
            self._evaluate_api_p95_regression(clean_group, p95, len(bucket), now_utc)

    def _evaluate_api_p95_regression(
        self, endpoint_group: str, p95: float, sample_count: int, now: datetime
    ) -> None:
        """Internal lock-held check for API p95 regression against baseline and SLA."""
        if sample_count < self.config.min_samples_for_regression:
            return

        baseline_p95 = self.config.endpoint_baselines.get(
            endpoint_group, self.config.default_p95_baseline_ms
        )
        sla_p95 = self.config.endpoint_slas.get(endpoint_group, self.config.default_p95_sla_ms)

        regression_ratio = p95 / baseline_p95 if baseline_p95 > 0 else 1.0
        delta_ms = p95 - baseline_p95

        is_regressed, last_alert_time, last_alert_type = self._api_alert_state.get(
            endpoint_group,
            (False, datetime.min.replace(tzinfo=UTC), PerformanceAlertType.P95_RECOVERED),
        )
        elapsed = (now - last_alert_time).total_seconds()
        in_cooldown = elapsed < self.config.alert_cooldown_seconds

        # Case 1: Hard SLA breach (CRITICAL)
        if p95 > sla_p95:
            if not is_regressed or (
                not in_cooldown and last_alert_type != PerformanceAlertType.P95_SLA_BREACH
            ):
                alert = PerformanceAlertEvent(
                    alert_id=f"p95-sla-{uuid.uuid4().hex[:10]}",
                    alert_type=PerformanceAlertType.P95_SLA_BREACH,
                    target=endpoint_group,
                    severity=PerformanceAlertSeverity.CRITICAL,
                    message=(
                        f"API latency SLA breach on '{endpoint_group}': "
                        f"p95={p95:.1f}ms exceeded max SLA threshold of {sla_p95:.1f}ms "
                        f"(baseline: {baseline_p95:.1f}ms)"
                    ),
                    current_value=round(p95, 2),
                    threshold_or_baseline=sla_p95,
                    timestamp_utc=now.isoformat(),
                    details={
                        "p95_ms": round(p95, 2),
                        "sla_p95_ms": sla_p95,
                        "baseline_p95_ms": baseline_p95,
                        "samples": sample_count,
                    },
                )
                self._api_alert_state[endpoint_group] = (
                    True,
                    now,
                    PerformanceAlertType.P95_SLA_BREACH,
                )
                API_P95_REGRESSION_ALERTS_TOTAL.inc(
                    labels={
                        "endpoint_group": endpoint_group,
                        "severity": PerformanceAlertSeverity.CRITICAL.value,
                        "trigger_reason": "sla_breach",
                    }
                )
                self._dispatch_alert(alert)

        # Case 2: Relative regression spike above baseline (WARNING)
        elif (
            regression_ratio >= (1.0 + self.config.p95_regression_threshold_pct / 100.0)
            and delta_ms >= self.config.p95_min_delta_ms
        ):
            if not is_regressed or (
                not in_cooldown and last_alert_type != PerformanceAlertType.P95_REGRESSION_SPIKE
            ):
                alert = PerformanceAlertEvent(
                    alert_id=f"p95-spike-{uuid.uuid4().hex[:10]}",
                    alert_type=PerformanceAlertType.P95_REGRESSION_SPIKE,
                    target=endpoint_group,
                    severity=PerformanceAlertSeverity.WARNING,
                    message=(
                        f"API latency regression on '{endpoint_group}': "
                        f"p95={p95:.1f}ms is +{(regression_ratio - 1.0) * 100:.1f}% above "
                        f"baseline ({baseline_p95:.1f}ms, delta: +{delta_ms:.1f}ms)"
                    ),
                    current_value=round(p95, 2),
                    threshold_or_baseline=baseline_p95,
                    timestamp_utc=now.isoformat(),
                    details={
                        "p95_ms": round(p95, 2),
                        "baseline_p95_ms": baseline_p95,
                        "delta_ms": round(delta_ms, 2),
                        "regression_ratio": round(regression_ratio, 3),
                        "samples": sample_count,
                    },
                )
                self._api_alert_state[endpoint_group] = (
                    True,
                    now,
                    PerformanceAlertType.P95_REGRESSION_SPIKE,
                )
                API_P95_REGRESSION_ALERTS_TOTAL.inc(
                    labels={
                        "endpoint_group": endpoint_group,
                        "severity": PerformanceAlertSeverity.WARNING.value,
                        "trigger_reason": "regression_spike",
                    }
                )
                self._dispatch_alert(alert)

        # Case 3: Recovery back to within safe margin of baseline
        elif is_regressed and p95 <= (baseline_p95 * 1.10):
            alert = PerformanceAlertEvent(
                alert_id=f"p95-recov-{uuid.uuid4().hex[:10]}",
                alert_type=PerformanceAlertType.P95_RECOVERED,
                target=endpoint_group,
                severity=PerformanceAlertSeverity.INFO,
                message=(
                    f"API latency on '{endpoint_group}' recovered: "
                    f"p95={p95:.1f}ms has returned to normal range (baseline: {baseline_p95:.1f}ms)"
                ),
                current_value=round(p95, 2),
                threshold_or_baseline=baseline_p95,
                timestamp_utc=now.isoformat(),
                details={"p95_ms": round(p95, 2), "baseline_p95_ms": baseline_p95},
            )
            self._api_alert_state[endpoint_group] = (
                False,
                now,
                PerformanceAlertType.P95_RECOVERED,
            )
            self._dispatch_alert(alert)

    def get_web_vitals_summary(self) -> list[WebVitalSummary]:
        """Produce statistical summaries for all observed Web Vitals routes."""
        summaries: list[WebVitalSummary] = []
        with self._lock:
            for (metric_name, route), bucket in sorted(self._web_vitals.items()):
                if not bucket:
                    continue
                values = sorted(s.value for s in bucket)
                p75 = calculate_percentile(values, 75.0)
                p90 = calculate_percentile(values, 90.0)
                mean_val = sum(values) / len(values)

                breakdown: dict[str, int] = {
                    WebVitalRating.GOOD.value: 0,
                    WebVitalRating.NEEDS_IMPROVEMENT.value: 0,
                    WebVitalRating.POOR.value: 0,
                }
                for s in bucket:
                    breakdown[s.rating.value] = breakdown.get(s.rating.value, 0) + 1

                poor_count = breakdown[WebVitalRating.POOR.value]
                poor_ratio = poor_count / len(bucket)

                try:
                    v_enum = WebVitalName(metric_name)
                    good_thresh, poor_thresh = WEB_VITALS_THRESHOLDS[v_enum]
                except ValueError:
                    good_thresh, poor_thresh = (100.0, 300.0)

                if (
                    poor_ratio >= self.config.web_vitals_poor_ratio_alarm_threshold
                    or p75 > poor_thresh
                ):
                    status = "critical"
                elif p75 > good_thresh or poor_ratio > 0.05:
                    status = "degraded"
                else:
                    status = "healthy"

                summaries.append(
                    WebVitalSummary(
                        metric_name=metric_name,
                        route=route,
                        sample_count=len(bucket),
                        p75_value=round(p75, 4),
                        p90_value=round(p90, 4),
                        mean_value=round(mean_val, 4),
                        ratings_breakdown=breakdown,
                        poor_ratio=round(poor_ratio, 4),
                        status=status,
                    )
                )
        return summaries

    def get_endpoint_latencies(self) -> list[EndpointLatencySummary]:
        """Produce rolling latency summaries for all recorded API endpoint groups."""
        results: list[EndpointLatencySummary] = []
        with self._lock:
            # Union of known configured baselines and active observation groups
            all_groups = set(self._api_latencies.keys()) | set(
                self.config.endpoint_baselines.keys()
            )
            for group in sorted(all_groups):
                bucket = self._api_latencies.get(group)
                baseline_p95 = self.config.endpoint_baselines.get(
                    group, self.config.default_p95_baseline_ms
                )
                sla_p95 = self.config.endpoint_slas.get(group, self.config.default_p95_sla_ms)

                if not bucket:
                    results.append(
                        EndpointLatencySummary(
                            endpoint_group=group,
                            sample_count=0,
                            p50_ms=0.0,
                            p95_ms=0.0,
                            p99_ms=0.0,
                            baseline_p95_ms=baseline_p95,
                            max_sla_p95_ms=sla_p95,
                            regression_ratio=0.0,
                            is_regressed=False,
                            status="normal",
                        )
                    )
                    continue

                sorted_vals = sorted(bucket)
                p50 = calculate_percentile(sorted_vals, 50.0)
                p95 = calculate_percentile(sorted_vals, 95.0)
                p99 = calculate_percentile(sorted_vals, 99.0)

                regression_ratio = p95 / baseline_p95 if baseline_p95 > 0 else 1.0
                is_regressed = p95 > sla_p95 or (
                    regression_ratio >= (1.0 + self.config.p95_regression_threshold_pct / 100.0)
                    and (p95 - baseline_p95) >= self.config.p95_min_delta_ms
                )

                if p95 > sla_p95:
                    status = "critical"
                elif is_regressed:
                    status = "warning"
                else:
                    status = "normal"

                results.append(
                    EndpointLatencySummary(
                        endpoint_group=group,
                        sample_count=len(bucket),
                        p50_ms=round(p50, 2),
                        p95_ms=round(p95, 2),
                        p99_ms=round(p99, 2),
                        baseline_p95_ms=baseline_p95,
                        max_sla_p95_ms=sla_p95,
                        regression_ratio=round(regression_ratio, 3),
                        is_regressed=is_regressed,
                        status=status,
                    )
                )
        return results

    def get_endpoint_latency(self, endpoint_group: str) -> EndpointLatencySummary | None:
        """Lookup latency profile for a specific endpoint group."""
        clean = sanitize_route(endpoint_group)
        for summary in self.get_endpoint_latencies():
            if summary.endpoint_group == clean:
                return summary
        return None

    def get_recent_alerts(self, limit: int = 50) -> list[PerformanceAlertEvent]:
        """Fetch chronological list of recent performance and Web Vitals alert events."""
        return self._history_handler.get_history(limit=limit)

    def clear_history(self) -> None:
        """Reset all in-memory buffers and alerts (primarily for unit test isolation)."""
        with self._lock:
            self._web_vitals.clear()
            self._api_latencies.clear()
            self._api_alert_state.clear()
            self._vitals_alert_state.clear()
            self._history_handler.clear()

    def generate_html_dashboard(self) -> str:
        """Render a modern, responsive HTML dashboard for Web Vitals & p95 regression."""
        vitals_summaries = self.get_web_vitals_summary()
        latency_summaries = self.get_endpoint_latencies()
        alerts = self.get_recent_alerts(limit=15)

        # Build Web Vitals rows
        vitals_html_rows = ""
        for v in vitals_summaries:
            badge_color = (
                "#10b981"
                if v.status == "healthy"
                else ("#f59e0b" if v.status == "degraded" else "#ef4444")
            )
            poor_col = "#ef4444" if v.poor_ratio > 0.1 else "#10b981"
            vitals_html_rows += (
                f'<tr class="row">'
                f'<td class="cell bold text-light">{html.escape(v.metric_name)}</td>'
                f'<td class="cell text-mono">{html.escape(v.route)}</td>'
                f'<td class="cell text-num text-white">{v.p75_value:.2f}</td>'
                f'<td class="cell text-num text-muted">{v.p90_value:.2f}</td>'
                f'<td class="cell text-num text-muted">{v.sample_count}</td>'
                f'<td class="cell text-num bold" style="color: {poor_col};">'
                f"{v.poor_ratio * 100:.1f}%</td>"
                f'<td class="cell text-center">'
                f'<span class="badge" style="background: {badge_color}22; '
                f'color: {badge_color}; border-color: {badge_color}55;">'
                f"{html.escape(v.status)}</span></td></tr>\n"
            )
        if not vitals_html_rows:
            vitals_html_rows = (
                '<tr><td colspan="7" class="cell text-center text-muted" '
                'style="padding: 24px;">Henüz telemetri verisi kaydedilmedi.</td></tr>'
            )

        # Build API Latency rows
        latency_html_rows = ""
        for lat in latency_summaries:
            status_color = (
                "#10b981"
                if lat.status == "normal"
                else ("#f59e0b" if lat.status == "warning" else "#ef4444")
            )
            reg_text = (
                f"+{(lat.regression_ratio - 1.0) * 100:.1f}%"
                if lat.regression_ratio > 1.0
                else f"-{(1.0 - lat.regression_ratio) * 100:.1f}%"
                if lat.sample_count > 0
                else "-"
            )
            reg_col = "#ef4444" if lat.regression_ratio > 1.3 else "#94a3b8"
            latency_html_rows += (
                f'<tr class="row">'
                f'<td class="cell bold text-mono">{html.escape(lat.endpoint_group)}</td>'
                f'<td class="cell text-num text-muted">{lat.sample_count}</td>'
                f'<td class="cell text-num">{lat.p50_ms:.1f}ms</td>'
                f'<td class="cell text-num bold" style="color: {status_color};">'
                f"{lat.p95_ms:.1f}ms</td>"
                f'<td class="cell text-num text-muted">{lat.baseline_p95_ms:.1f}ms</td>'
                f'<td class="cell text-num text-muted">{lat.max_sla_p95_ms:.1f}ms</td>'
                f'<td class="cell text-num bold" style="color: {reg_col};">{reg_text}</td>'
                f'<td class="cell text-center">'
                f'<span class="badge" style="background: {status_color}22; '
                f'color: {status_color}; border-color: {status_color}55;">'
                f"{html.escape(lat.status)}</span></td></tr>\n"
            )

        # Build Alert items
        alerts_html = ""
        for a in reversed(alerts):
            sev_color = (
                "#ef4444"
                if a.severity == PerformanceAlertSeverity.CRITICAL
                else ("#f59e0b" if a.severity == PerformanceAlertSeverity.WARNING else "#3b82f6")
            )
            alerts_html += (
                f'<div class="alert-box" style="border-left-color: {sev_color};">'
                f'<div class="alert-header">'
                f'<span class="bold" style="color: {sev_color}; font-size: 13px;">'
                f"{html.escape(a.alert_type.value.upper())}</span>"
                f'<span class="text-muted" style="font-size: 12px;">'
                f"{html.escape(a.timestamp_utc[:19])}</span>"
                f"</div>"
                f'<div class="text-white" style="font-size: 13px;">'
                f"{html.escape(a.message)}</div>"
                f"</div>\n"
            )
        if not alerts_html:
            alerts_html = (
                '<div class="text-muted" style="padding: 12px 0; font-size: 13px;">'
                "Herhangi bir performans regresyon uyarısı bulunmuyor (sistem normal).</div>"
            )

        return f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web Vitals & API p95 Performans Paneli</title>
    <style>
        body {{
            background-color: #020617;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 32px 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .card {{
            background-color: #0b1120;
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
        }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        th {{
            color: #64748b;
            font-weight: 600;
            text-align: left;
            padding: 12px;
            border-bottom: 2px solid #1e293b;
            font-size: 12px;
            text-transform: uppercase;
        }}
        .cell {{ padding: 12px; }}
        .row {{ border-bottom: 1px solid #1e293b; }}
        .bold {{ font-weight: 600; }}
        .text-num {{ text-align: right; }}
        .text-center {{ text-align: center; }}
        .text-muted {{ color: #64748b; }}
        .text-white {{ color: #e2e8f0; }}
        .text-light {{ color: #f8fafc; }}
        .text-mono {{ color: #94a3b8; font-family: monospace; }}
        .badge {{
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            border: 1px solid transparent;
        }}
        .alert-box {{
            background: #0f172a;
            border-left: 4px solid #3b82f6;
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 8px;
        }}
        .alert-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 4px;
        }}
        .title-grad {{
            font-size: 26px;
            font-weight: 800;
            margin: 0 0 6px 0;
            background: linear-gradient(135deg, #60a5fa 0%, #a78bfa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header style="margin-bottom: 28px; border-bottom: 1px solid #1e293b;
                       padding-bottom: 16px;">
            <h1 class="title-grad">Web Vitals & API p95 Performans Regresyon Paneli</h1>
            <p class="text-muted" style="margin: 0; font-size: 14px;">
                İstemci Web Vitals ölçümleri, kayan pencere p95 gecikmeleri ve regresyon alarmları.
            </p>
        </header>

        <section class="card">
            <h2 style="font-size: 18px; margin: 0 0 16px 0; color: #38bdf8;">
                API Endpoint p95 Gecikme ve Regresyon Takibi
            </h2>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Uç Nokta Grubu</th>
                            <th class="text-num">Örneklem</th>
                            <th class="text-num">p50</th>
                            <th class="text-num">p95 (Canlı)</th>
                            <th class="text-num">Baseline p95</th>
                            <th class="text-num">Max SLA</th>
                            <th class="text-num">Regresyon</th>
                            <th class="text-center">Durum</th>
                        </tr>
                    </thead>
                    <tbody>
                        {latency_html_rows}
                    </tbody>
                </table>
            </div>
        </section>

        <section class="card">
            <h2 style="font-size: 18px; margin: 0 0 16px 0; color: #a78bfa;">
                Ön Yüz Web Vitals (LCP, INP, CLS, FCP, TTFB)
            </h2>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Metrik</th>
                            <th>Rota / Sayfa</th>
                            <th class="text-num">p75</th>
                            <th class="text-num">p90</th>
                            <th class="text-num">Örneklem</th>
                            <th class="text-num">Zayıf Oranı</th>
                            <th class="text-center">Sağlık</th>
                        </tr>
                    </thead>
                    <tbody>
                        {vitals_html_rows}
                    </tbody>
                </table>
            </div>
        </section>

        <section class="card">
            <h2 style="font-size: 18px; margin: 0 0 16px 0; color: #f59e0b;">
                Son Performans & Regresyon Alarmları
            </h2>
            <div>
                {alerts_html}
            </div>
        </section>
    </div>
</body>
</html>"""


# Global singleton instance for application runtime
PERFORMANCE_DETECTOR = PerformanceRegressionCoordinator()
