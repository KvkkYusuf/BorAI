"""Unit tests for look-ahead bias and feature leakage detection module (FAZ-10)."""

from datetime import date, datetime, timedelta

import pytest

from stock_analysis.pipeline.lookahead_bias import (
    DataLeakageError,
    LeakageViolationType,
    LookaheadBiasDetector,
)


def test_verify_window_configuration_valid() -> None:
    """Standard causal rolling windows must be accepted without error."""
    # Should not raise
    LookaheadBiasDetector.verify_window_configuration(
        window_size=14, is_centered=False, forward_shift=0
    )
    LookaheadBiasDetector.verify_window_configuration(window_size=1, is_centered=False)


def test_verify_window_configuration_centered_rejected() -> None:
    """Centered rolling windows violate temporal causality and must be rejected."""
    with pytest.raises(DataLeakageError, match="Centered rolling windows"):
        LookaheadBiasDetector.verify_window_configuration(window_size=10, is_centered=True)


def test_verify_window_configuration_negative_shift_rejected() -> None:
    """Negative shifts look ahead into future values and must be rejected."""
    with pytest.raises(DataLeakageError, match="Negative/forward shift"):
        LookaheadBiasDetector.verify_window_configuration(window_size=5, forward_shift=1)


def test_verify_window_configuration_invalid_size() -> None:
    """Invalid window sizes must raise ValueError."""
    with pytest.raises(ValueError, match="Window size must be at least 1"):
        LookaheadBiasDetector.verify_window_configuration(window_size=0)


def test_check_feature_timestamp_alignment_clean() -> None:
    """Features timestamped at or before decision point must pass with zero violations."""
    decision_dt = datetime(2026, 3, 10, 18, 0, 0)
    feature_ts = {
        "rsi_14": datetime(2026, 3, 10, 18, 0, 0),
        "macd_lag1": datetime(2026, 3, 9, 18, 0, 0),
        "open_price": date(2026, 3, 10),
    }

    violations = LookaheadBiasDetector.check_feature_timestamp_alignment(
        decision_timestamp=decision_dt,
        feature_timestamps=feature_ts,
    )
    assert len(violations) == 0


def test_check_feature_timestamp_alignment_future_detected() -> None:
    """Features with future timestamps must be flagged immediately."""
    decision_dt = date(2026, 3, 10)
    feature_ts = {
        "historical_close": date(2026, 3, 10),
        "future_earnings_report": date(2026, 3, 11),  # Tomorrow!
        "next_day_news": datetime(2026, 3, 10, 23, 59, 59),
    }

    violations = LookaheadBiasDetector.check_feature_timestamp_alignment(
        decision_timestamp=decision_dt,
        feature_timestamps=feature_ts,
    )
    assert len(violations) == 2
    assert all(v.violation_type == LeakageViolationType.FUTURE_TIMESTAMP for v in violations)
    feat_names = {v.feature_name for v in violations}
    assert feat_names == {"future_earnings_report", "next_day_news"}


def test_check_target_leakage_clean() -> None:
    """Features that are not suspiciously correlated with the future target must pass."""
    records = [
        {"feature_a": 1.0, "feature_b": 10.0, "target_return": 0.02},
        {"feature_a": 1.5, "feature_b": 9.0, "target_return": -0.01},
        {"feature_a": 1.2, "feature_b": 11.0, "target_return": 0.05},
        {"feature_a": 0.8, "feature_b": 8.5, "target_return": -0.03},
        {"feature_a": 2.0, "feature_b": 12.0, "target_return": 0.01},
    ]

    violations = LookaheadBiasDetector.check_target_leakage(
        records=records,
        target_name="target_return",
        feature_names=["feature_a", "feature_b"],
    )
    assert len(violations) == 0


def test_check_target_leakage_target_in_features() -> None:
    """Accidental inclusion of target column inside feature set must be detected."""
    records = [
        {"feat": 1.0, "target": 0.5},
        {"feat": 2.0, "target": 0.8},
        {"feat": 3.0, "target": 1.2},
    ]
    violations = LookaheadBiasDetector.check_target_leakage(
        records=records,
        target_name="target",
        feature_names=["feat", "target"],
    )
    assert len(violations) == 1
    assert violations[0].violation_type == LeakageViolationType.TARGET_LEAKAGE
    assert violations[0].feature_name == "target"


def test_check_target_leakage_high_correlation_detected() -> None:
    """Features with >0.999 correlation with future target indicate unshifted leakage."""
    # Synthesize target and a leaky feature that is basically target + tiny noise
    records = [
        {"clean_feat": float(i % 3), "leaky_target_copy": float(i) * 1.00001, "target": float(i)}
        for i in range(20)
    ]

    violations = LookaheadBiasDetector.check_target_leakage(
        records=records,
        target_name="target",
        feature_names=["clean_feat", "leaky_target_copy"],
    )
    assert len(violations) == 1
    assert violations[0].violation_type == LeakageViolationType.TARGET_LEAKAGE
    assert violations[0].feature_name == "leaky_target_copy"
    assert "suspicious absolute correlation" in violations[0].details


def test_verify_preprocessing_isolation_clean() -> None:
    """Preprocessors fitted only on training data must pass verification."""
    train_records = [{"price": 100.0}, {"price": 110.0}, {"price": 120.0}]  # mean = 110.0
    eval_records = [{"price": 200.0}, {"price": 220.0}]  # pooled mean = 150.0

    fitted_params = {
        "price": {"mean": 110.0, "std": 10.0}  # matches train_mean
    }

    violations = LookaheadBiasDetector.verify_preprocessing_isolation(
        train_records=train_records,
        eval_records=eval_records,
        fitted_scaler_params=fitted_params,
    )
    assert len(violations) == 0


def test_verify_preprocessing_isolation_leaked() -> None:
    """Preprocessors fitted on pooled train+eval data must be caught as leakage."""
    train_records = [{"price": 100.0}, {"price": 110.0}, {"price": 120.0}]  # mean = 110.0
    # pooled mean = (100+110+120+200+220)/5 = 150.0
    eval_records = [{"price": 200.0}, {"price": 220.0}]

    # Accidental leakage: scaler was fitted on entire dataset (pooled mean 150.0)
    fitted_params = {"price": {"mean": 150.0, "std": 50.0}}

    violations = LookaheadBiasDetector.verify_preprocessing_isolation(
        train_records=train_records,
        eval_records=eval_records,
        fitted_scaler_params=fitted_params,
    )
    assert len(violations) == 1
    assert violations[0].violation_type == LeakageViolationType.GLOBAL_PREPROCESSING_LEAKAGE
    assert "matches pooled dataset" in violations[0].details


def test_audit_chronological_feature_matrix_clean() -> None:
    """Full audit on valid chronological feature matrix passes cleanly."""
    base_date = date(2026, 1, 1)
    records = [
        {
            "date": base_date + timedelta(days=i),
            "rsi": 50.0 + (i % 10),
            "volume_z": float(i) * 0.1,
            "target": float(i % 5) * 0.01,
        }
        for i in range(15)
    ]

    result = LookaheadBiasDetector.audit_chronological_feature_matrix(
        records=records,
        feature_names=["rsi", "volume_z"],
        target_name="target",
    )
    assert result.is_leak_free is True
    assert len(result.violations) == 0
    assert result.total_records_checked == 15
    assert "0 look-ahead violations detected" in result.summary


def test_audit_chronological_feature_matrix_detects_shuffling() -> None:
    """Audit detects non-chronological sorting and chronological inversion."""
    records = [
        {"date": date(2026, 1, 1), "feat": 1.0},
        {"date": date(2026, 1, 5), "feat": 2.0},
        {"date": date(2026, 1, 3), "feat": 1.5},  # Inversion!
    ]

    result = LookaheadBiasDetector.audit_chronological_feature_matrix(
        records=records,
        feature_names=["feat"],
    )
    assert result.is_leak_free is False
    assert len(result.violations) >= 1
    assert result.violations[0].violation_type == LeakageViolationType.FUTURE_TIMESTAMP
    assert "Non-chronological order detected" in result.violations[0].details
