"""Metrics registry, counters, gauges, and latency histograms with Prometheus exposition."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# Standard latency buckets in seconds for web APIs and pipeline tasks
DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _format_labels(labels: dict[str, str] | None) -> str:
    """Format label dictionary into Prometheus label syntax {key=\"value\"}."""
    if not labels:
        return ""
    sorted_items = sorted(labels.items())
    formatted_pairs = [f'{k}="{v}"' for k, v in sorted_items]
    return "{" + ",".join(formatted_pairs) + "}"


@dataclass
class Counter:
    """Monotonically increasing cumulative metric counter."""

    name: str
    description: str
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _values: dict[tuple[tuple[str, str], ...], float] = field(
        default_factory=lambda: defaultdict(float), init=False
    )

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment the counter by amount (must be non-negative)."""
        if amount < 0:
            raise ValueError(f"Counter increment must be non-negative, got {amount}")
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] += amount

    def get(self, labels: dict[str, str] | None = None) -> float:
        """Retrieve current value for specified labels."""
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            return self._values.get(key, 0.0)

    def total(self) -> float:
        """Retrieve total sum across all label combinations."""
        with self._lock:
            return sum(self._values.values())

    def to_prometheus(self) -> list[str]:
        """Generate Prometheus exposition text lines."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            else:
                for key, val in sorted(self._values.items()):
                    lbl_str = _format_labels(dict(key))
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


@dataclass
class Gauge:
    """Metric that represents a single numerical value that can arbitrarily go up and down."""

    name: str
    description: str
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _values: dict[tuple[tuple[str, str], ...], float] = field(
        default_factory=lambda: defaultdict(float), init=False
    )

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Set the gauge to an arbitrary value."""
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment the gauge by amount."""
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] += amount

    def dec(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Decrement the gauge by amount."""
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] -= amount

    def get(self, labels: dict[str, str] | None = None) -> float:
        """Retrieve current value for specified labels."""
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            return self._values.get(key, 0.0)

    def to_prometheus(self) -> list[str]:
        """Generate Prometheus exposition text lines."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            else:
                for key, val in sorted(self._values.items()):
                    lbl_str = _format_labels(dict(key))
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


@dataclass
class Histogram:
    """Samples observations and counts them in configurable buckets for percentile calculations."""

    name: str
    description: str
    buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _samples: dict[tuple[tuple[str, str], ...], list[float]] = field(
        default_factory=lambda: defaultdict(list), init=False
    )
    _counts: dict[tuple[tuple[str, str], ...], int] = field(
        default_factory=lambda: defaultdict(int), init=False
    )
    _sums: dict[tuple[tuple[str, str], ...], float] = field(
        default_factory=lambda: defaultdict(float), init=False
    )

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Record an observed value."""
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._counts[key] += 1
            self._sums[key] += value
            # Keep sample window for accurate p50/p90/p95/p99 calculation
            # Cap memory at last 2000 observations per label set
            samples = self._samples[key]
            samples.append(value)
            if len(samples) > 2000:
                self._samples[key] = samples[-2000:]

    def get_percentile(self, percentile: float, labels: dict[str, str] | None = None) -> float:
        """Compute the requested percentile (0.0 to 100.0) for the recorded samples."""
        with self._lock:
            if labels is not None:
                key = tuple(sorted(labels.items()))
                samples = self._samples.get(key, [])
            else:
                if () in self._samples and len(self._samples) == 1:
                    samples = self._samples[()]
                else:
                    samples = [s for sublist in self._samples.values() for s in sublist]

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

    def to_prometheus(self) -> list[str]:
        """Generate Prometheus exposition text lines with _bucket, _count, and _sum."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        sorted_buckets = sorted(self.buckets)

        with self._lock:
            label_keys = set(self._counts.keys())
            if not label_keys:
                label_keys = {()}

            for key in sorted(label_keys):
                base_labels = dict(key)
                count = self._counts.get(key, 0)
                total_sum = self._sums.get(key, 0.0)
                samples = self._samples.get(key, [])

                # Calculate cumulative counts per bucket
                for b in sorted_buckets:
                    b_count = sum(1 for s in samples if s <= b)
                    b_labels = {**base_labels, "le": str(b)}
                    lines.append(f"{self.name}_bucket{_format_labels(b_labels)} {b_count}")

                # +Inf bucket equals total count
                inf_labels = {**base_labels, "le": "+Inf"}
                lines.append(f"{self.name}_bucket{_format_labels(inf_labels)} {count}")
                lines.append(f"{self.name}_sum{_format_labels(base_labels)} {total_sum}")
                lines.append(f"{self.name}_count{_format_labels(base_labels)} {count}")

        return lines


class MetricsRegistry:
    """Central container for application counters, gauges, and histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    def counter(self, name: str, description: str = "") -> Counter:
        """Register or retrieve an existing Counter."""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, description)
            return self._counters[name]

    def gauge(self, name: str, description: str = "") -> Gauge:
        """Register or retrieve an existing Gauge."""
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge(name, description)
            return self._gauges[name]

    def histogram(
        self,
        name: str,
        description: str = "",
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS,
    ) -> Histogram:
        """Register or retrieve an existing Histogram."""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(
                    name=name, description=description, buckets=tuple(buckets)
                )
            return self._histograms[name]

    def generate_prometheus_text(self) -> str:
        """Expose all metrics as standard Prometheus text lines."""
        lines: list[str] = []
        with self._lock:
            for c in self._counters.values():
                lines.extend(c.to_prometheus())
            for g in self._gauges.values():
                lines.extend(g.to_prometheus())
            for h in self._histograms.values():
                lines.extend(h.to_prometheus())
        return "\n".join(lines) + "\n"

    def get_snapshot(self) -> dict[str, Any]:
        """Return structured dictionary summary of all metrics for health/admin inspection."""
        snapshot: dict[str, Any] = {"counters": {}, "gauges": {}, "histograms": {}}
        with self._lock:
            for name, c in self._counters.items():
                snapshot["counters"][name] = {
                    _format_labels(dict(k)): val for k, val in c._values.items()
                }
            for name, g in self._gauges.items():
                snapshot["gauges"][name] = {
                    _format_labels(dict(k)): val for k, val in g._values.items()
                }
            for name, h in self._histograms.items():
                hist_data: dict[str, Any] = {}
                for k in h._counts.keys():
                    lbl = _format_labels(dict(k))
                    hist_data[lbl] = {
                        "count": h._counts[k],
                        "sum": h._sums[k],
                        "p50": h.get_percentile(50.0, dict(k)),
                        "p90": h.get_percentile(90.0, dict(k)),
                        "p95": h.get_percentile(95.0, dict(k)),
                        "p99": h.get_percentile(99.0, dict(k)),
                    }
                snapshot["histograms"][name] = hist_data
        return snapshot

    def clear(self) -> None:
        """Clear all registered metrics (useful in unit tests)."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# Default global metrics registry
REGISTRY = MetricsRegistry()

# Standard application metrics
HTTP_REQUESTS_TOTAL = REGISTRY.counter(
    "http_requests_total", "Total count of HTTP requests processed by endpoint and status"
)
HTTP_REQUEST_DURATION_SECONDS = REGISTRY.histogram(
    "http_request_duration_seconds", "HTTP request processing latency in seconds"
)
HTTP_ERRORS_TOTAL = REGISTRY.counter(
    "http_errors_total", "Total count of unhandled or 5xx server errors"
)
CACHE_OPERATIONS_TOTAL = REGISTRY.counter(
    "cache_operations_total", "Cache operations tracked by tier and status (hit/miss)"
)

# Operational metrics
DATA_FRESHNESS_SECONDS = REGISTRY.gauge(
    "data_freshness_seconds", "Age of latest market data bar in seconds by symbol and provider"
)
DATA_PROVIDER_HEALTH = REGISTRY.gauge(
    "data_provider_health", "Market data provider operational status (1=healthy, 0=down)"
)
QUEUE_LATENCY_SECONDS = REGISTRY.histogram(
    "queue_latency_seconds",
    "Queue dispatch wait latency in seconds from enqueue to execution",
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)
QUEUE_PENDING_TASKS = REGISTRY.gauge(
    "queue_pending_tasks", "Current count of pending tasks in queue by queue name"
)
QUEUE_PROCESSED_TASKS_TOTAL = REGISTRY.counter(
    "queue_processed_tasks_total", "Count of processed queue tasks by queue and outcome"
)
EMAIL_DELIVERIES_TOTAL = REGISTRY.counter(
    "email_deliveries_total", "Count of email delivery attempts by provider, status and template"
)
EMAIL_DELIVERY_DURATION_SECONDS = REGISTRY.histogram(
    "email_delivery_duration_seconds",
    "Email delivery dispatch latency in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# Provider outage and quota alerting metrics
PROVIDER_OUTAGE_STATE = REGISTRY.gauge(
    "provider_outage_state", "Market data provider outage state (0=normal, 1=outage)"
)
PROVIDER_CONSECUTIVE_FAILURES = REGISTRY.gauge(
    "provider_consecutive_failures", "Current consecutive request failures count for provider"
)
PROVIDER_ALERTS_TOTAL = REGISTRY.counter(
    "provider_alerts_total",
    "Total count of provider outage and quota alerts emitted by type and severity",
)
PROVIDER_QUOTA_USAGE_RATIO = REGISTRY.gauge(
    "provider_quota_usage_ratio",
    "Provider daily quota usage percentage as a ratio between 0.0 and 1.0+",
)
PROVIDER_QUOTA_REMAINING = REGISTRY.gauge(
    "provider_quota_remaining", "Current remaining daily quota allowance for provider"
)

# Model performance metrics (Offline validation vs Live realised tracking)
MODEL_OFFLINE_DIRECTIONAL_ACCURACY = REGISTRY.gauge(
    "model_offline_directional_accuracy",
    "Offline validation directional accuracy (0.0 to 1.0) by model_id and version",
)
MODEL_OFFLINE_BRIER_SCORE = REGISTRY.gauge(
    "model_offline_brier_score",
    "Offline validation Brier probability calibration score by model_id and version",
)
MODEL_OFFLINE_MAE = REGISTRY.gauge(
    "model_offline_mae",
    "Offline validation mean absolute error by model_id and version",
)
MODEL_LIVE_DIRECTIONAL_ACCURACY = REGISTRY.gauge(
    "model_live_directional_accuracy",
    "Live realized directional accuracy (0.0 to 1.0) by model_id and version",
)
MODEL_LIVE_BRIER_SCORE = REGISTRY.gauge(
    "model_live_brier_score",
    "Live realized Brier probability calibration score by model_id and version",
)
MODEL_LIVE_MAE = REGISTRY.gauge(
    "model_live_mae",
    "Live realized mean absolute error by model_id and version",
)
MODEL_PERFORMANCE_GAP_ACCURACY = REGISTRY.gauge(
    "model_performance_gap_accuracy",
    "Performance degradation gap (offline accuracy - live accuracy) by model_id and version",
)
MODEL_LIVE_PREDICTIONS_TOTAL = REGISTRY.counter(
    "model_live_predictions_total",
    "Total live predictions evaluated against ground truth by model_id and outcome",
)
MODEL_PERFORMANCE_STATUS = REGISTRY.gauge(
    "model_performance_status",
    "Model health status numeric code (0=healthy, 1=degraded, 2=critical, 3=insufficient_data)",
)

# Lineage and calculation provenance metrics
LINEAGE_RECORDS_TOTAL = REGISTRY.counter(
    "lineage_records_total",
    "Total lineage traceability records registered by record_type and data_source",
)

# Disaster Recovery, RPO/RTO, and Rollback metrics
DR_BACKUP_AGE_SECONDS = REGISTRY.gauge(
    "dr_backup_age_seconds", "Age in seconds of most recent backup snapshot by subsystem"
)
DR_RPO_TARGET_SECONDS = REGISTRY.gauge(
    "dr_rpo_target_seconds", "Target maximum RPO in seconds by subsystem"
)
DR_RTO_TARGET_SECONDS = REGISTRY.gauge(
    "dr_rto_target_seconds", "Target maximum RTO in seconds by subsystem"
)
DR_LAST_DRILL_DURATION_SECONDS = REGISTRY.gauge(
    "dr_last_drill_duration_seconds",
    "Duration of the last recovery drill in seconds by subsystem",
)
DR_RPO_BREACH_TOTAL = REGISTRY.counter(
    "dr_rpo_breach_total", "Count of detected RPO SLA breaches by subsystem"
)
DR_READINESS_SCORE = REGISTRY.gauge(
    "dr_readiness_score", "Overall disaster recovery readiness score (0.0 to 1.0)"
)

# Feature flag and deployment rollback metrics
FEATURE_FLAG_EVALUATIONS_TOTAL = REGISTRY.counter(
    "feature_flag_evaluations_total",
    "Total count of feature flag evaluations by flag name, result, and rule matched",
)
FEATURE_FLAG_ACTIVE_GAUGE = REGISTRY.gauge(
    "feature_flag_active_gauge",
    "Current active state of feature flag (1=active, 0=inactive/killed) by flag name",
)
DEPLOYMENT_ROLLBACK_TRIGGERS_TOTAL = REGISTRY.counter(
    "deployment_rollback_triggers_total",
    "Total count of deployment rollback triggers by deployment id and reason",
)
DEPLOYMENT_STATUS_GAUGE = REGISTRY.gauge(
    "deployment_status_gauge",
    "Numeric status code of deployment "
    "(0=pending, 1=canary, 2=active, 3=stable, 4=rolling_back, 5=rolled_back, 6=failed)",
)
DEPLOYMENT_CANARY_WEIGHT_GAUGE = REGISTRY.gauge(
    "deployment_canary_weight_gauge",
    "Current canary traffic percentage weight (0.0 to 100.0) by deployment id",
)

# Status page and service outage notification metrics
STATUS_PAGE_COMPONENT_STATUS = REGISTRY.gauge(
    "status_page_component_status",
    "Component status numeric code "
    "(0=operational, 1=degraded, 2=partial_outage, 3=major_outage, 4=maintenance)",
)
STATUS_PAGE_ACTIVE_INCIDENTS_GAUGE = REGISTRY.gauge(
    "status_page_active_incidents_gauge",
    "Number of active incidents by severity (minor, major, critical, maintenance)",
)
STATUS_PAGE_INCIDENT_UPDATES_TOTAL = REGISTRY.counter(
    "status_page_incident_updates_total",
    "Count of incident state updates published by severity and stage",
)
STATUS_PAGE_OVERALL_STATUS_GAUGE = REGISTRY.gauge(
    "status_page_overall_status_gauge",
    "Overall system health status numeric code "
    "(0=operational, 1=degraded, 2=partial_outage, 3=major_outage, 4=maintenance)",
)

# LLM token, call cost, cache hit ratio, and budget alert metrics
COST_LLM_TOKENS_TOTAL = REGISTRY.counter(
    "cost_llm_tokens_total",
    "Total LLM tokens consumed by feature, model, and token type (prompt, completion)",
)
COST_LLM_CALLS_TOTAL = REGISTRY.counter(
    "cost_llm_calls_total",
    "Total count of LLM requests by feature, model, and cache result (hit, miss)",
)
COST_CACHE_HIT_RATIO_GAUGE = REGISTRY.gauge(
    "cost_cache_hit_ratio_gauge",
    "Current cache hit ratio (0.0 to 1.0) by feature and aggregate",
)
COST_TOTAL_DOLLARS_COUNTER = REGISTRY.counter(
    "cost_total_dollars_counter",
    "Estimated cumulative monetary expenditure in USD by feature and model",
)
COST_BUDGET_USAGE_RATIO_GAUGE = REGISTRY.gauge(
    "cost_budget_usage_ratio_gauge",
    "Current budget consumption ratio (0.0 to 1.0+) by scope (daily_global, monthly_global)",
)
COST_BUDGET_ALERTS_TOTAL = REGISTRY.counter(
    "cost_budget_alerts_total",
    "Total count of budget threshold alerts triggered by level and scope",
)

# Web Vitals and API p95 performance regression metrics
WEB_VITALS_TOTAL = REGISTRY.counter(
    "web_vitals_total",
    "Total Web Vitals telemetry reports received by metric_name, rating, and route",
)
WEB_VITALS_VALUE_GAUGE = REGISTRY.gauge(
    "web_vitals_value_gauge",
    "Latest observed rolling value for Web Vitals by metric_name and route",
)
WEB_VITALS_REGRESSION_ALERTS_TOTAL = REGISTRY.counter(
    "web_vitals_regression_alerts_total",
    "Total count of Web Vitals regression and poor performance alerts by metric_name and severity",
)
API_P95_LATENCY_SECONDS = REGISTRY.gauge(
    "api_p95_latency_seconds",
    "Rolling p95 API response latency in seconds by endpoint_group",
)
API_P95_REGRESSION_ALERTS_TOTAL = REGISTRY.counter(
    "api_p95_regression_alerts_total",
    "Total count of API p95 latency regression alerts by endpoint_group and severity",
)

# Live Market Data Contract and Entitlement Verification Metrics
LIVE_DATA_CONTRACT_STATUS = REGISTRY.gauge(
    "live_data_contract_status_gauge",
    "Current status of provider market data contract (1=valid, 0=invalid/expired)",
)
LIVE_DATA_CONTRACT_EXPIRY_DAYS = REGISTRY.gauge(
    "live_data_contract_expiry_days_gauge",
    "Days remaining until provider live data distribution contract expires",
)
LIVE_DATA_ENTITLEMENT_CHECKS_TOTAL = REGISTRY.counter(
    "live_data_entitlement_checks_total",
    "Total count of runtime market data entitlement verification checks",
)
