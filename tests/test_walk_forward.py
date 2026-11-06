"""Tests for walk-forward and expanding-window evaluation framework (FAZ-11)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_analysis.models.walk_forward import (
    FoldWindow,
    WalkForwardConfig,
    WalkForwardConfigError,
    WalkForwardLeakageError,
    WalkForwardReport,
    WalkForwardValidator,
    WindowingMethod,
    assert_walk_forward_leak_free,
)


def test_expanding_window_folds_generation() -> None:
    """Expanding window folds must expand train window and slide test window forward."""
    config = WalkForwardConfig(
        windowing_method=WindowingMethod.EXPANDING,
        min_train_size=50,
        test_size=10,
        step_size=10,
        embargo_size=0,
    )
    validator = WalkForwardValidator(config)
    total_samples = 100

    folds = validator.generate_folds(total_samples)

    assert len(folds) == 5  # 50..60, 60..70, 70..80, 80..90, 90..100
    assert_walk_forward_leak_free(folds)

    for i, fold in enumerate(folds):
        assert fold.fold_index == i
        assert fold.train_indices[0] == 0  # Expanding always starts at 0
        expected_train_end = 50 + i * 10
        expected_test_start = expected_train_end
        expected_test_end = expected_test_start + 10

        assert fold.train_indices[1] == expected_train_end
        assert fold.test_indices[0] == expected_test_start
        assert fold.test_indices[1] == expected_test_end
        assert fold.train_count == expected_train_end
        assert fold.test_count == 10


def test_rolling_window_folds_generation() -> None:
    """Rolling window folds must maintain a fixed train window size."""
    config = WalkForwardConfig(
        windowing_method=WindowingMethod.ROLLING,
        min_train_size=40,
        rolling_window_size=40,
        test_size=15,
        step_size=15,
        embargo_size=0,
    )
    validator = WalkForwardValidator(config)
    total_samples = 100

    folds = validator.generate_folds(total_samples)

    assert len(folds) == 4  # 40..55, 55..70, 70..85, 85..100
    assert_walk_forward_leak_free(folds)

    for fold in folds:
        assert fold.train_count == 40
        assert fold.test_count == 15
        assert fold.train_indices[1] == fold.test_indices[0]


def test_embargo_purge_gap_enforcement() -> None:
    """Embargo size must create a temporal buffer between train end and test start."""
    embargo = 5
    config = WalkForwardConfig(
        windowing_method=WindowingMethod.EXPANDING,
        min_train_size=50,
        test_size=10,
        step_size=10,
        embargo_size=embargo,
    )
    validator = WalkForwardValidator(config)
    total_samples = 100

    folds = validator.generate_folds(total_samples)
    assert len(folds) > 0
    assert_walk_forward_leak_free(folds)

    for fold in folds:
        gap = fold.test_indices[0] - fold.train_indices[1]
        assert gap == embargo
        assert fold.embargo_size == embargo


def test_temporal_leakage_detection() -> None:
    """Overlapping train/test or violated embargo must raise WalkForwardLeakageError."""
    # Test starting before train ends
    leaky_fold = FoldWindow(
        fold_index=0,
        train_indices=(0, 50),
        test_indices=(45, 55),
        embargo_size=0,
    )
    with pytest.raises(WalkForwardLeakageError, match="Gelecek sızıntısı"):
        leaky_fold.assert_no_temporal_leakage()

    # Embargo violated
    embargo_violated_fold = FoldWindow(
        fold_index=1,
        train_indices=(0, 50),
        test_indices=(52, 60),
        embargo_size=5,  # gap is only 2, but embargo is 5
    )
    with pytest.raises(WalkForwardLeakageError, match="Embargo ihlali"):
        embargo_violated_fold.assert_no_temporal_leakage()


def test_non_monotonic_folds_detection() -> None:
    """Non-chronologically advancing folds must be caught by assert_walk_forward_leak_free."""
    fold1 = FoldWindow(fold_index=0, train_indices=(0, 50), test_indices=(50, 60))
    # fold2 moved backwards
    fold2 = FoldWindow(fold_index=1, train_indices=(0, 40), test_indices=(40, 50))

    with pytest.raises(WalkForwardLeakageError, match="kronolojik olarak ilerlemiyor"):
        assert_walk_forward_leak_free([fold1, fold2])


def test_config_validation_and_errors() -> None:
    """Invalid sample size or mismatched timestamps must raise WalkForwardConfigError."""
    config = WalkForwardConfig(
        min_train_size=50,
        test_size=20,
        embargo_size=5,
    )
    validator = WalkForwardValidator(config)

    # Required: 50 + 5 + 20 = 75
    with pytest.raises(WalkForwardConfigError, match="yetersiz"):
        validator.generate_folds(total_samples=70)

    # Mismatched timestamps
    with pytest.raises(WalkForwardConfigError, match="eşleşmiyor"):
        validator.generate_folds(
            total_samples=80,
            timestamps=["2026-01-01", "2026-01-02"],
        )


def test_max_folds_limiter() -> None:
    """max_folds constraint must cap the number of generated windows."""
    config = WalkForwardConfig(
        min_train_size=30,
        test_size=10,
        step_size=5,
        max_folds=3,
    )
    validator = WalkForwardValidator(config)
    folds = validator.generate_folds(total_samples=100)
    assert len(folds) == 3


def test_timestamps_propagation() -> None:
    """Date strings must be correctly propagated into train_period and test_period."""
    start_date = date(2026, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(80)]

    config = WalkForwardConfig(
        min_train_size=50,
        test_size=10,
        embargo_size=2,
    )
    validator = WalkForwardValidator(config)
    folds = validator.generate_folds(total_samples=80, timestamps=dates)

    first_fold = folds[0]
    assert first_fold.train_start_date == "2026-01-01"
    assert first_fold.train_end_date == str(dates[49])  # idx 49 is last train bar
    assert first_fold.test_start_date == str(dates[52])  # 50 + 2 embargo = 52
    assert first_fold.test_end_date == str(dates[61])


def test_evaluate_walk_forward_aggregation_and_overfitting_warning() -> None:
    """WalkForwardValidator.evaluate must compute cross-fold statistics and detect instability."""
    config = WalkForwardConfig(
        windowing_method=WindowingMethod.EXPANDING,
        min_train_size=40,
        test_size=10,
        step_size=10,
    )
    validator = WalkForwardValidator(config)

    # Simulate fluctuating fold losses (high variance)
    fold_losses = [0.10, 0.45, 0.05, 0.80]

    def mock_eval(fold: FoldWindow) -> dict[str, float]:
        idx = min(fold.fold_index, len(fold_losses) - 1)
        return {
            "loss": fold_losses[idx],
            "mae": 0.05 + idx * 0.01,
            "directional_accuracy": 0.60,
        }

    report: WalkForwardReport = validator.evaluate(total_samples=80, eval_fn=mock_eval)

    assert report.n_folds == 4
    assert report.is_leak_free is True
    assert "loss" in report.aggregated_metrics
    assert "directional_accuracy" in report.aggregated_metrics

    dir_acc = report.aggregated_metrics["directional_accuracy"]
    assert dir_acc["mean"] == 0.60
    assert dir_acc["std"] == 0.0

    # Overfitting / high fold variance warning must trigger
    assert report.overfitting_risk_warning is not None
    assert "Yüksek katlama varyansı" in report.overfitting_risk_warning


def test_walk_forward_baseline_evaluation() -> None:
    """Walk-forward evaluation must work cleanly on financial time series with baseline logic."""
    import numpy as np

    # Generate synthetic price series
    np.random.seed(42)
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.001, 0.02, size=120)))

    config = WalkForwardConfig(
        windowing_method=WindowingMethod.EXPANDING,
        min_train_size=60,
        test_size=20,
        embargo_size=1,  # 1-day forecast horizon embargo
    )
    validator = WalkForwardValidator(config)

    def evaluate_last_value(fold: FoldWindow) -> dict[str, float]:
        train_s, train_e = fold.train_indices
        test_s, test_e = fold.test_indices

        # Model is naive last-value baseline: predicts last known train price
        last_train_price = float(prices[train_e - 1])
        actual_test_prices = prices[test_s:test_e]

        mae = float(np.mean(np.abs(actual_test_prices - last_train_price)))
        rmse = float(np.sqrt(np.mean((actual_test_prices - last_train_price) ** 2)))

        return {
            "loss": rmse,
            "mae": mae,
            "test_sample_size": float(len(actual_test_prices)),
        }

    report = validator.evaluate(total_samples=len(prices), eval_fn=evaluate_last_value)

    assert report.n_folds >= 2
    assert report.is_leak_free is True
    assert report.aggregated_metrics["mae"]["mean"] > 0.0
