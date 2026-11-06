"""Market data provider outage detection and quota exhaustion alerting engine.

Provides automated detection, cooldown deduplication, structured notification,
and Prometheus metrics for provider failures, HTTP 5xx/timeouts, rate limits,
and daily call quotas.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    DATA_PROVIDER_HEALTH,
    PROVIDER_ALERTS_TOTAL,
    PROVIDER_CONSECUTIVE_FAILURES,
    PROVIDER_OUTAGE_STATE,
    PROVIDER_QUOTA_REMAINING,
    PROVIDER_QUOTA_USAGE_RATIO,
)

logger = logging.getLogger("stock_analysis.observability.provider_alerts")


class ProviderAlertSeverity(StrEnum):
    """Severity tier for provider alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ProviderAlertType(StrEnum):
    """Categorized alarm trigger type for market data providers."""

    OUTAGE = "outage"
    RECOVERED = "recovered"
    QUOTA_WARNING = "quota_warning"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMIT_BURST = "rate_limit_burst"


@dataclass(frozen=True)
class ProviderAlertEvent:
    """Immutable event payload generated when a provider condition triggers."""

    alert_id: str
    provider: str
    alert_type: ProviderAlertType
    severity: ProviderAlertSeverity
    message: str
    timestamp_utc: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert alert event to a JSON-serializable dictionary."""
        data = asdict(self)
        data["alert_type"] = self.alert_type.value
        data["severity"] = self.severity.value
        return data


class BaseProviderAlertHandler:
    """Base interface for dispatching provider alert events."""

    def handle_alert(self, event: ProviderAlertEvent) -> None:
        """Process or forward an alert event."""
        raise NotImplementedError


class LogProviderAlertHandler(BaseProviderAlertHandler):
    """Logs provider alert events as structured log entries."""

    def handle_alert(self, event: ProviderAlertEvent) -> None:
        log_attrs = {
            "alert_id": event.alert_id,
            "provider": event.provider,
            "alert_type": event.alert_type.value,
            "severity": event.severity.value,
            "details": event.details,
        }
        if event.severity == ProviderAlertSeverity.CRITICAL:
            logger.error(
                "PROVIDER ALERT [%s] %s: %s",
                event.provider,
                event.alert_type.value.upper(),
                event.message,
                extra=log_attrs,
            )
        elif event.severity == ProviderAlertSeverity.WARNING:
            logger.warning(
                "PROVIDER ALERT [%s] %s: %s",
                event.provider,
                event.alert_type.value.upper(),
                event.message,
                extra=log_attrs,
            )
        else:
            logger.info(
                "PROVIDER ALERT [%s] %s: %s",
                event.provider,
                event.alert_type.value.upper(),
                event.message,
                extra=log_attrs,
            )


class CallbackProviderAlertHandler(BaseProviderAlertHandler):
    """Dispatches alert events to a registered sync callback callable."""

    def __init__(self, callback: Callable[[ProviderAlertEvent], None]) -> None:
        self.callback = callback

    def handle_alert(self, event: ProviderAlertEvent) -> None:
        self.callback(event)


class InMemoryAlertHistoryHandler(BaseProviderAlertHandler):
    """Retains a rolling buffer of latest emitted alerts for auditing and API access."""

    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max_size
        self._history: deque[ProviderAlertEvent] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def handle_alert(self, event: ProviderAlertEvent) -> None:
        with self._lock:
            self._history.append(event)

    def get_alerts(
        self,
        provider: str | None = None,
        alert_type: ProviderAlertType | None = None,
        limit: int = 50,
    ) -> list[ProviderAlertEvent]:
        """Query alert history with optional filtering."""
        with self._lock:
            items = list(self._history)
        if provider:
            items = [a for a in items if a.provider.lower() == provider.lower()]
        if alert_type:
            items = [a for a in items if a.alert_type == alert_type]
        items.reverse()  # Newest first
        return items[:limit]

    def clear(self) -> None:
        """Clear all stored history."""
        with self._lock:
            self._history.clear()


@dataclass
class ProviderAlertConfig:
    """Configurable thresholds and cooldown durations for provider alerts."""

    consecutive_failure_threshold: int = 3
    recovery_success_threshold: int = 2
    quota_warning_ratio: float = 0.85
    quota_exhausted_ratio: float = 1.0
    cooldown_seconds: float = 300.0  # 5 minutes suppression window for duplicate alerts


@dataclass
class _ProviderInternalState:
    """Internal mutable tracking state for an individual provider."""

    provider: str
    is_outage: bool = False
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_error: str | None = None
    last_status_code: int | None = None
    last_failure_utc: datetime | None = None
    last_success_utc: datetime | None = None
    calls_made: int = 0
    daily_limit: int | None = None
    quota_remaining: int | None = None
    last_alert_time: dict[ProviderAlertType, datetime] = field(default_factory=dict)
    quota_warning_emitted: bool = False
    quota_exhausted_emitted: bool = False


@dataclass(frozen=True)
class ProviderStatusReport:
    """Immutable status snapshot for external querying."""

    provider: str
    is_outage: bool
    consecutive_failures: int
    consecutive_successes: int
    last_error: str | None
    last_status_code: int | None
    last_seen_utc: str | None
    calls_made: int
    daily_limit: int | None
    quota_remaining: int | None
    quota_usage_ratio: float

    def to_dict(self) -> dict[str, Any]:
        """Convert status report to dictionary."""
        return asdict(self)


class ProviderAlertManager:
    """Thread-safe coordinator for provider failure detection, quota alarms, and handlers.

    Guarantees:
    - Debounces transient single-request hiccups (consecutive failure threshold).
    - Prevents alert notification storming (cooldown windows and deduplication).
    - Automatically marks outage recovery upon consecutive operational successes.
    - Synchronizes Prometheus metrics for alerting dashboards.
    """

    def __init__(
        self,
        config: ProviderAlertConfig | None = None,
        handlers: list[BaseProviderAlertHandler] | None = None,
    ) -> None:
        self.config = config or ProviderAlertConfig()
        self._lock = threading.Lock()
        self._states: dict[str, _ProviderInternalState] = {}
        self.history_handler = InMemoryAlertHistoryHandler(max_size=200)
        default_handlers: list[BaseProviderAlertHandler] = [
            LogProviderAlertHandler(),
            self.history_handler,
        ]
        self._handlers = list(handlers) if handlers is not None else default_handlers

    def add_handler(self, handler: BaseProviderAlertHandler) -> None:
        """Register an additional alert handler."""
        with self._lock:
            self._handlers.append(handler)

    def _get_or_create_state(self, provider: str) -> _ProviderInternalState:
        key = provider.lower()
        if key not in self._states:
            self._states[key] = _ProviderInternalState(provider=provider)
        return self._states[key]

    def _dispatch_alert(self, event: ProviderAlertEvent) -> None:
        """Send alert event to all registered handlers and bump Prometheus counter."""
        lbls = {
            "provider": event.provider,
            "alert_type": event.alert_type.value,
            "severity": event.severity.value,
        }
        PROVIDER_ALERTS_TOTAL.inc(1.0, labels=lbls)
        for handler in self._handlers:
            try:
                handler.handle_alert(event)
            except Exception:
                logger.exception("Failed to dispatch alert to handler %s", handler)

    def _is_in_cooldown(
        self,
        state: _ProviderInternalState,
        alert_type: ProviderAlertType,
        now: datetime,
    ) -> bool:
        """Check if an alert of this type was dispatched recently."""
        last_time = state.last_alert_time.get(alert_type)
        if last_time is None:
            return False
        return (now - last_time).total_seconds() < self.config.cooldown_seconds

    def record_success(
        self,
        provider: str,
        now_utc: datetime | None = None,
    ) -> list[ProviderAlertEvent]:
        """Record a successful response from a market data provider."""
        now = now_utc or datetime.now(UTC)
        events: list[ProviderAlertEvent] = []

        with self._lock:
            state = self._get_or_create_state(provider)
            state.consecutive_successes += 1
            state.consecutive_failures = 0
            state.last_success_utc = now

            # Sync gauge for consecutive failures to zero
            PROVIDER_CONSECUTIVE_FAILURES.set(0.0, labels={"provider": provider})

            # Check if recovering from an outage
            if state.is_outage:
                if state.consecutive_successes >= self.config.recovery_success_threshold:
                    state.is_outage = False
                    state.last_alert_time[ProviderAlertType.RECOVERED] = now

                    # Update gauges
                    PROVIDER_OUTAGE_STATE.set(0.0, labels={"provider": provider})
                    DATA_PROVIDER_HEALTH.set(1.0, labels={"provider": provider})

                    event = ProviderAlertEvent(
                        alert_id=str(uuid.uuid4()),
                        provider=provider,
                        alert_type=ProviderAlertType.RECOVERED,
                        severity=ProviderAlertSeverity.INFO,
                        message=(
                            f"Market data provider '{provider}' has recovered. "
                            f"{state.consecutive_successes} consecutive successful calls."
                        ),
                        timestamp_utc=now.isoformat(),
                        details={
                            "consecutive_successes": state.consecutive_successes,
                            "previous_error": state.last_error,
                        },
                    )
                    events.append(event)

        for event in events:
            self._dispatch_alert(event)

        return events

    def record_failure(
        self,
        provider: str,
        status_code: int = 500,
        error_message: str = "",
        is_timeout: bool = False,
        now_utc: datetime | None = None,
    ) -> list[ProviderAlertEvent]:
        """Record an error response, timeout, or rate-limit from a provider."""
        now = now_utc or datetime.now(UTC)
        events: list[ProviderAlertEvent] = []

        with self._lock:
            state = self._get_or_create_state(provider)
            state.consecutive_failures += 1
            state.consecutive_successes = 0
            state.last_error = error_message or ("Timeout" if is_timeout else f"HTTP {status_code}")
            state.last_status_code = status_code
            state.last_failure_utc = now

            # Update failure gauge
            PROVIDER_CONSECUTIVE_FAILURES.set(
                float(state.consecutive_failures), labels={"provider": provider}
            )

            # 1. Rate-Limit Burst (HTTP 429)
            if status_code == 429:
                if not self._is_in_cooldown(state, ProviderAlertType.RATE_LIMIT_BURST, now):
                    state.last_alert_time[ProviderAlertType.RATE_LIMIT_BURST] = now
                    events.append(
                        ProviderAlertEvent(
                            alert_id=str(uuid.uuid4()),
                            provider=provider,
                            alert_type=ProviderAlertType.RATE_LIMIT_BURST,
                            severity=ProviderAlertSeverity.WARNING,
                            message=(
                                f"Provider '{provider}' returned HTTP 429 Too Many Requests. "
                                f"Backing off requests."
                            ),
                            timestamp_utc=now.isoformat(),
                            details={
                                "status_code": 429,
                                "consecutive_failures": state.consecutive_failures,
                                "error": error_message,
                            },
                        )
                    )

            # 2. Outage Trigger: consecutive failures >= threshold
            if (
                not state.is_outage
                and state.consecutive_failures >= self.config.consecutive_failure_threshold
            ):
                state.is_outage = True
                state.last_alert_time[ProviderAlertType.OUTAGE] = now

                # Sync gauges
                PROVIDER_OUTAGE_STATE.set(1.0, labels={"provider": provider})
                DATA_PROVIDER_HEALTH.set(0.0, labels={"provider": provider})

                events.append(
                    ProviderAlertEvent(
                        alert_id=str(uuid.uuid4()),
                        provider=provider,
                        alert_type=ProviderAlertType.OUTAGE,
                        severity=ProviderAlertSeverity.CRITICAL,
                        message=(
                            f"CRITICAL: Market data provider '{provider}' is down! "
                            f"{state.consecutive_failures} consecutive failures detected. "
                            f"Last error: {state.last_error}"
                        ),
                        timestamp_utc=now.isoformat(),
                        details={
                            "consecutive_failures": state.consecutive_failures,
                            "last_status_code": status_code,
                            "is_timeout": is_timeout,
                            "error": state.last_error,
                        },
                    )
                )

        for event in events:
            self._dispatch_alert(event)

        return events

    def record_quota_usage(
        self,
        provider: str,
        calls_made: int,
        daily_limit: int | None,
        now_utc: datetime | None = None,
    ) -> list[ProviderAlertEvent]:
        """Record quota consumption and trigger quota warning or exhaustion alarms."""
        now = now_utc or datetime.now(UTC)
        events: list[ProviderAlertEvent] = []

        if daily_limit is None or daily_limit <= 0:
            return events

        with self._lock:
            state = self._get_or_create_state(provider)
            state.calls_made = calls_made
            state.daily_limit = daily_limit
            remaining = max(0, daily_limit - calls_made)
            state.quota_remaining = remaining
            ratio = calls_made / float(daily_limit)

            # Update Prometheus gauges
            PROVIDER_QUOTA_USAGE_RATIO.set(round(ratio, 4), labels={"provider": provider})
            PROVIDER_QUOTA_REMAINING.set(float(remaining), labels={"provider": provider})

            # 1. Quota Exhausted: calls_made >= daily_limit
            if ratio >= self.config.quota_exhausted_ratio or remaining == 0:
                if not state.quota_exhausted_emitted or not self._is_in_cooldown(
                    state, ProviderAlertType.QUOTA_EXHAUSTED, now
                ):
                    state.quota_exhausted_emitted = True
                    state.last_alert_time[ProviderAlertType.QUOTA_EXHAUSTED] = now
                    events.append(
                        ProviderAlertEvent(
                            alert_id=str(uuid.uuid4()),
                            provider=provider,
                            alert_type=ProviderAlertType.QUOTA_EXHAUSTED,
                            severity=ProviderAlertSeverity.CRITICAL,
                            message=(
                                f"CRITICAL: Provider '{provider}' daily quota exhausted! "
                                f"Used: {calls_made}/{daily_limit} calls (100%)."
                            ),
                            timestamp_utc=now.isoformat(),
                            details={
                                "calls_made": calls_made,
                                "daily_limit": daily_limit,
                                "remaining": remaining,
                                "usage_ratio": round(ratio, 4),
                            },
                        )
                    )

            # 2. Quota Warning: ratio >= warning threshold (e.g. 0.85)
            elif ratio >= self.config.quota_warning_ratio:
                if not state.quota_warning_emitted and not self._is_in_cooldown(
                    state, ProviderAlertType.QUOTA_WARNING, now
                ):
                    state.quota_warning_emitted = True
                    state.last_alert_time[ProviderAlertType.QUOTA_WARNING] = now
                    events.append(
                        ProviderAlertEvent(
                            alert_id=str(uuid.uuid4()),
                            provider=provider,
                            alert_type=ProviderAlertType.QUOTA_WARNING,
                            severity=ProviderAlertSeverity.WARNING,
                            message=(
                                f"WARNING: Provider '{provider}' daily quota reached "
                                f"{round(ratio * 100, 1)}%! Used: {calls_made}/{daily_limit} calls."
                            ),
                            timestamp_utc=now.isoformat(),
                            details={
                                "calls_made": calls_made,
                                "daily_limit": daily_limit,
                                "remaining": remaining,
                                "usage_ratio": round(ratio, 4),
                            },
                        )
                    )

        for event in events:
            self._dispatch_alert(event)

        return events

    def get_provider_status(self, provider: str) -> ProviderStatusReport | None:
        """Retrieve current operational and quota status for a provider."""
        with self._lock:
            state = self._states.get(provider.lower())
            if not state:
                return None
            last_seen = (
                state.last_success_utc.isoformat()
                if state.last_success_utc
                else (state.last_failure_utc.isoformat() if state.last_failure_utc else None)
            )
            ratio = (
                (state.calls_made / float(state.daily_limit))
                if state.daily_limit and state.daily_limit > 0
                else 0.0
            )
            return ProviderStatusReport(
                provider=state.provider,
                is_outage=state.is_outage,
                consecutive_failures=state.consecutive_failures,
                consecutive_successes=state.consecutive_successes,
                last_error=state.last_error,
                last_status_code=state.last_status_code,
                last_seen_utc=last_seen,
                calls_made=state.calls_made,
                daily_limit=state.daily_limit,
                quota_remaining=state.quota_remaining,
                quota_usage_ratio=round(ratio, 4),
            )

    def get_all_statuses(self) -> dict[str, ProviderStatusReport]:
        """Retrieve status reports for all tracked providers."""
        with self._lock:
            keys = list(self._states.keys())
        reports: dict[str, ProviderStatusReport] = {}
        for key in keys:
            rep = self.get_provider_status(key)
            if rep:
                reports[rep.provider] = rep
        return reports

    def get_active_outages(self) -> list[str]:
        """List all providers currently flagged in outage state."""
        with self._lock:
            return [s.provider for s in self._states.values() if s.is_outage]

    def get_recent_alerts(
        self,
        provider: str | None = None,
        alert_type: ProviderAlertType | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve serialized recent alert history."""
        alerts = self.history_handler.get_alerts(
            provider=provider, alert_type=alert_type, limit=limit
        )
        return [a.to_dict() for a in alerts]

    def reset(self) -> None:
        """Reset internal provider states and alert history (for test isolation)."""
        with self._lock:
            self._states.clear()
            self.history_handler.clear()


# Default global instance
PROVIDER_ALERT_MANAGER = ProviderAlertManager()
