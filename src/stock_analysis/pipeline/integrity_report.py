"""Daily market data integrity auditing, aggregation, and alert dispatch module."""

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.validator import DataIntegrityReport, IssueSeverity

logger = logging.getLogger(__name__)


class AlertSeverity(StrEnum):
    """Severity classification for integrity alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IntegrityAlert(BaseModel):
    """An actionable alert generated from integrity report anomalies."""

    model_config = ConfigDict(frozen=True)

    alert_id: str = Field(default_factory=lambda: f"ALT-{uuid.uuid4().hex[:8].upper()}")
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: AlertSeverity
    symbol: str | None = None
    summary: str
    details: str
    action_required: str


@runtime_checkable
class AlertHandler(Protocol):
    """Protocol for dispatching integrity alerts to notification sinks."""

    def handle_alert(self, alert: IntegrityAlert) -> None:
        """Handle or forward the given alert."""
        ...


class LogAlertHandler:
    """Dispatches alerts via structured standard logging."""

    def __init__(self, target_logger: logging.Logger | None = None) -> None:
        self._logger = target_logger or logger

    def handle_alert(self, alert: IntegrityAlert) -> None:
        """Log the alert using the matching logging level."""
        target_symbol = alert.symbol or "ALL"
        msg = (
            f"[{alert.severity.upper()}] {alert.summary} "
            f"(Symbol: {target_symbol}, ID: {alert.alert_id}) - {alert.action_required}"
        )
        if alert.severity == AlertSeverity.CRITICAL:
            self._logger.critical(msg)
        elif alert.severity == AlertSeverity.WARNING:
            self._logger.warning(msg)
        else:
            self._logger.info(msg)


class CallbackAlertHandler:
    """Dispatches alerts to a custom Python callback function."""

    def __init__(self, callback: Callable[[IntegrityAlert], None]) -> None:
        self._callback = callback

    def handle_alert(self, alert: IntegrityAlert) -> None:
        """Pass the alert directly to the registered callable."""
        self._callback(alert)


class DailyDataIntegrityReport(BaseModel):
    """Aggregated daily data integrity report across all monitored symbols."""

    model_config = ConfigDict(frozen=True)

    audit_date: date
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_symbols_checked: int
    clean_symbols_count: int
    failed_symbols_count: int
    total_bars_checked: int
    total_errors: int
    total_warnings: int
    is_pipeline_healthy: bool
    symbol_reports: list[DataIntegrityReport] = Field(default_factory=list)
    alerts: list[IntegrityAlert] = Field(default_factory=list)


class DailyIntegrityAuditor:
    """Performs daily multi-symbol integrity evaluation, metric rollup, and alerting."""

    def __init__(
        self,
        handlers: Sequence[AlertHandler] | None = None,
        warning_alert_threshold: int = 5,
    ) -> None:
        """
        Initialize the daily auditor.

        Args:
            handlers: Collection of AlertHandler sinks. Defaults to [LogAlertHandler()].
            warning_alert_threshold: Threshold of total warnings above which a summary
                WARNING alert is emitted.
        """
        self._handlers: list[AlertHandler] = (
            list(handlers) if handlers is not None else [LogAlertHandler()]
        )
        self.warning_alert_threshold = warning_alert_threshold

    def register_handler(self, handler: AlertHandler) -> None:
        """Register an additional alert handler."""
        self._handlers.append(handler)

    def _dispatch_alert(self, alert: IntegrityAlert) -> None:
        """Send alert to all registered handlers."""
        for handler in self._handlers:
            try:
                handler.handle_alert(alert)
            except Exception:
                logger.exception(
                    "Failed to dispatch alert %s via handler %s", alert.alert_id, handler
                )

    def audit_daily_reports(
        self,
        reports: Sequence[DataIntegrityReport],
        audit_date: date | None = None,
    ) -> DailyDataIntegrityReport:
        """
        Aggregate individual symbol integrity reports into a daily audit report
        and trigger alerts for critical flaws or excessive warnings.

        Policy:
        - Any ERROR issue in any symbol triggers an immediate CRITICAL alert.
        - Cumulative WARNING count exceeding warning_alert_threshold triggers a WARNING alert.
        """
        target_date = audit_date or date.today()
        total_symbols = len(reports)
        clean_symbols = 0
        failed_symbols = 0
        total_bars = 0
        total_errors = 0
        total_warnings = 0

        alerts: list[IntegrityAlert] = []

        for report in reports:
            total_bars += report.total_bars
            errs = report.error_count
            warns = report.warning_count
            total_errors += errs
            total_warnings += warns

            if errs > 0:
                failed_symbols += 1
                critical_issues = [
                    f"[{issue.issue_type}] {issue.description}"
                    for issue in report.issues
                    if issue.severity == IssueSeverity.ERROR
                ]
                alert = IntegrityAlert(
                    severity=AlertSeverity.CRITICAL,
                    symbol=report.symbol,
                    summary=f"Critical data integrity failure in symbol {report.symbol}",
                    details="; ".join(critical_issues),
                    action_required=(
                        "Quarantine corrupt bars, reject ingestion, and alert data operations "
                        "before publishing derived time series."
                    ),
                )
                alerts.append(alert)
                self._dispatch_alert(alert)
            else:
                clean_symbols += 1

        # Evaluate system-wide warning threshold
        if total_warnings >= self.warning_alert_threshold and total_symbols > 0:
            warning_alert = IntegrityAlert(
                severity=AlertSeverity.WARNING,
                symbol=None,
                summary=f"Daily warning threshold exceeded: {total_warnings} warnings detected",
                details=(
                    f"Threshold: {self.warning_alert_threshold}. "
                    f"Total warnings: {total_warnings} across {total_symbols} symbols."
                ),
                action_required=(
                    "Review missing bars, holiday anomalies, and volume spikes in the daily report."
                ),
            )
            alerts.append(warning_alert)
            self._dispatch_alert(warning_alert)

        is_healthy = failed_symbols == 0

        return DailyDataIntegrityReport(
            audit_date=target_date,
            total_symbols_checked=total_symbols,
            clean_symbols_count=clean_symbols,
            failed_symbols_count=failed_symbols,
            total_bars_checked=total_bars,
            total_errors=total_errors,
            total_warnings=total_warnings,
            is_pipeline_healthy=is_healthy,
            symbol_reports=list(reports),
            alerts=alerts,
        )
