"""Tests for daily data integrity reporting and alert dispatching."""

import logging
from datetime import date
from unittest.mock import MagicMock

import pytest

from stock_analysis.pipeline import (
    AlertSeverity,
    CallbackAlertHandler,
    DailyDataIntegrityReport,
    DailyIntegrityAuditor,
    DataIntegrityIssue,
    DataIntegrityReport,
    DataIssueType,
    IntegrityAlert,
    IssueSeverity,
    LogAlertHandler,
)


def _make_sample_report(
    symbol: str,
    total_bars: int = 20,
    errors: int = 0,
    warnings: int = 0,
) -> DataIntegrityReport:
    """Helper to construct synthetic DataIntegrityReport."""
    issues: list[DataIntegrityIssue] = []
    for i in range(errors):
        issues.append(
            DataIntegrityIssue(
                issue_type=DataIssueType.NON_POSITIVE_PRICE,
                severity=IssueSeverity.ERROR,
                trade_date=date(2026, 1, 10 + i),
                description=f"Negative price error #{i + 1}",
            )
        )
    for j in range(warnings):
        issues.append(
            DataIntegrityIssue(
                issue_type=DataIssueType.MISSING_BAR,
                severity=IssueSeverity.WARNING,
                trade_date=date(2026, 1, 15 + j),
                description=f"Missing bar warning #{j + 1}",
            )
        )
    return DataIntegrityReport(
        symbol=symbol,
        total_bars=total_bars,
        is_valid=(errors == 0),
        issues=issues,
    )


def test_daily_auditor_all_clean() -> None:
    """Auditing clean symbol reports should report healthy pipeline with no alerts."""
    reports = [
        _make_sample_report("THYAO", total_bars=22),
        _make_sample_report("GARAN", total_bars=22),
    ]
    received_alerts: list[IntegrityAlert] = []
    handler = CallbackAlertHandler(callback=received_alerts.append)
    auditor = DailyIntegrityAuditor(handlers=[handler])

    daily_report = auditor.audit_daily_reports(reports, audit_date=date(2026, 1, 30))

    assert daily_report.audit_date == date(2026, 1, 30)
    assert daily_report.total_symbols_checked == 2
    assert daily_report.clean_symbols_count == 2
    assert daily_report.failed_symbols_count == 0
    assert daily_report.total_bars_checked == 44
    assert daily_report.total_errors == 0
    assert daily_report.total_warnings == 0
    assert daily_report.is_pipeline_healthy is True
    assert len(daily_report.alerts) == 0
    assert len(received_alerts) == 0


def test_daily_auditor_critical_alert_on_error() -> None:
    """Any ERROR issue must immediately trigger a CRITICAL alert."""
    reports = [
        _make_sample_report("THYAO", total_bars=20, errors=0),
        _make_sample_report("AKBNK", total_bars=20, errors=1),
    ]
    dispatched_alerts: list[IntegrityAlert] = []
    auditor = DailyIntegrityAuditor(
        handlers=[CallbackAlertHandler(callback=dispatched_alerts.append)]
    )

    daily_report = auditor.audit_daily_reports(reports, audit_date=date(2026, 1, 30))

    assert daily_report.is_pipeline_healthy is False
    assert daily_report.failed_symbols_count == 1
    assert daily_report.clean_symbols_count == 1
    assert daily_report.total_errors == 1

    # Verify critical alert properties
    assert len(dispatched_alerts) == 1
    alert = dispatched_alerts[0]
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.symbol == "AKBNK"
    assert "Critical data integrity failure in symbol AKBNK" in alert.summary
    assert "Quarantine" in alert.action_required
    assert alert.alert_id.startswith("ALT-")


def test_daily_auditor_warning_threshold_trigger() -> None:
    """When cumulative warnings exceed threshold, a WARNING alert is triggered."""
    reports = [
        _make_sample_report("THYAO", total_bars=20, warnings=2),
        _make_sample_report("GARAN", total_bars=20, warnings=4),
    ]
    # Total warnings: 6. Threshold: 5 -> Alert should trigger.
    dispatched: list[IntegrityAlert] = []
    auditor = DailyIntegrityAuditor(
        handlers=[CallbackAlertHandler(callback=dispatched.append)],
        warning_alert_threshold=5,
    )

    daily_report = auditor.audit_daily_reports(reports)

    # Pipeline is technically healthy (no fatal blocking errors) but warning alert emitted
    assert daily_report.is_pipeline_healthy is True
    assert daily_report.total_warnings == 6
    assert len(dispatched) == 1
    warning_alert = dispatched[0]
    assert warning_alert.severity == AlertSeverity.WARNING
    assert warning_alert.symbol is None
    assert "Daily warning threshold exceeded" in warning_alert.summary


def test_daily_auditor_warning_below_threshold() -> None:
    """Warnings below threshold should not trigger summary warning alert."""
    reports = [
        _make_sample_report("THYAO", total_bars=20, warnings=2),
    ]
    dispatched: list[IntegrityAlert] = []
    auditor = DailyIntegrityAuditor(
        handlers=[CallbackAlertHandler(callback=dispatched.append)],
        warning_alert_threshold=5,
    )

    daily_report = auditor.audit_daily_reports(reports)
    assert daily_report.total_warnings == 2
    assert len(dispatched) == 0


def test_log_alert_handler(caplog: pytest.LogCaptureFixture) -> None:
    """LogAlertHandler should emit proper log levels."""
    logger = logging.getLogger("test_alert_logger")
    handler = LogAlertHandler(target_logger=logger)

    crit_alert = IntegrityAlert(
        severity=AlertSeverity.CRITICAL,
        symbol="THYAO",
        summary="Fatal OHLC flaw",
        details="high < low",
        action_required="Halt feed",
    )
    warn_alert = IntegrityAlert(
        severity=AlertSeverity.WARNING,
        summary="High volume gap",
        details="5 missing bars",
        action_required="Check calendar",
    )
    info_alert = IntegrityAlert(
        severity=AlertSeverity.INFO,
        summary="Feed nominal",
        details="All nominal",
        action_required="None",
    )

    with caplog.at_level(logging.INFO):
        handler.handle_alert(crit_alert)
        handler.handle_alert(warn_alert)
        handler.handle_alert(info_alert)

    assert any(record.levelno == logging.CRITICAL for record in caplog.records)
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert any(record.levelno == logging.INFO for record in caplog.records)


def test_failing_handler_does_not_abort_audit() -> None:
    """A crashing alert handler should not prevent other handlers or the audit from completing."""
    failing_handler = MagicMock()
    failing_handler.handle_alert.side_effect = RuntimeError("Network error in webhook")

    successful_alerts: list[IntegrityAlert] = []
    working_handler = CallbackAlertHandler(callback=successful_alerts.append)

    auditor = DailyIntegrityAuditor(handlers=[failing_handler, working_handler])
    reports = [_make_sample_report("THYAO", total_bars=10, errors=1)]

    daily_report = auditor.audit_daily_reports(reports)
    assert daily_report.is_pipeline_healthy is False
    assert len(successful_alerts) == 1
    assert successful_alerts[0].symbol == "THYAO"


def test_report_json_serialization() -> None:
    """DailyDataIntegrityReport must serialize to JSON and deserialize back losslessly."""
    reports = [_make_sample_report("KCHOL", total_bars=15, errors=1, warnings=1)]
    auditor = DailyIntegrityAuditor(handlers=[])
    report = auditor.audit_daily_reports(reports, audit_date=date(2026, 1, 30))

    json_str = report.model_dump_json()
    reloaded = DailyDataIntegrityReport.model_validate_json(json_str)

    assert reloaded.audit_date == date(2026, 1, 30)
    assert reloaded.total_symbols_checked == 1
    assert reloaded.failed_symbols_count == 1
    assert len(reloaded.alerts) == 1
    assert reloaded.alerts[0].symbol == "KCHOL"
