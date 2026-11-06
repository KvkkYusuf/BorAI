"""Naif kıyas (baseline) modelleri ve benchmark onay testleri."""

import math

import pytest

from stock_analysis.models.baselines import (
    BaselineForecastResult,
    HistoricalMeanBaseline,
    LastValueBaseline,
    ModelBenchmarkReport,
    SimpleMomentumBaseline,
    compute_metrics,
    evaluate_forecast_against_baseline,
)


def test_last_value_baseline_predict() -> None:
    baseline = LastValueBaseline()
    prices = [10.0, 10.5, 11.0, 10.8, 12.0]

    result: BaselineForecastResult = baseline.predict(prices, horizon=3, symbol="THYAO")

    assert result.baseline_name == "last_value"
    assert result.symbol == "THYAO"
    assert result.horizon_steps == 3
    assert result.point_predictions == [12.0, 12.0, 12.0]
    assert result.directional_predictions == [0, 0, 0]
    assert result.metadata["last_observed_price"] == 12.0


def test_historical_mean_baseline_predict() -> None:
    baseline = HistoricalMeanBaseline(window=3)
    # 4 fiyat -> 3 günlük getiri
    prices = [100.0, 102.0, 104.04, 106.1208]  # her gün yaklaşık %2 artış

    result = baseline.predict(prices, horizon=2, symbol="BIMAS")

    assert "historical_mean_w3" in result.baseline_name
    assert result.symbol == "BIMAS"
    assert result.horizon_steps == 2
    assert result.directional_predictions == [1, 1]
    assert result.point_predictions[0] > 106.1208
    assert result.point_predictions[1] > result.point_predictions[0]
    assert pytest.approx(result.metadata["mean_return"], rel=1e-3) == 0.02


def test_historical_mean_baseline_downward() -> None:
    baseline = HistoricalMeanBaseline(window=3)
    prices = [100.0, 95.0, 90.0, 85.0]

    result = baseline.predict(prices, horizon=1, symbol="GARAN")

    assert result.directional_predictions == [-1]
    assert result.point_predictions[0] < 85.0


def test_simple_momentum_baseline_predict() -> None:
    baseline = SimpleMomentumBaseline(lookback_k=3)
    prices = [100.0, 101.0, 102.0, 106.0]  # k=3 önce 100.0 idi, şimdi 106.0 -> %6 toplam momentum

    result = baseline.predict(prices, horizon=2, symbol="KCHOL")

    assert result.baseline_name == "simple_momentum_k3"
    assert result.symbol == "KCHOL"
    assert result.directional_predictions == [1, 1]
    assert result.point_predictions[0] > 106.0
    assert result.point_predictions[1] > result.point_predictions[0]
    assert pytest.approx(result.metadata["total_momentum"], rel=1e-3) == 0.06
    assert pytest.approx(result.metadata["step_return"], rel=1e-3) == 0.02


def test_baseline_input_validations() -> None:
    baseline = LastValueBaseline()

    with pytest.raises(ValueError, match="Tahmin ufku"):
        baseline.predict([10.0, 11.0], horizon=0)

    with pytest.raises(ValueError, match="en az 1 adet"):
        baseline.predict([], horizon=1)

    with pytest.raises(ValueError, match="pozitif olmalıdır"):
        baseline.predict([10.0, -5.0], horizon=1)

    with pytest.raises(ValueError, match="pozitif olmalıdır"):
        baseline.predict([10.0, float("nan")], horizon=1)

    hist_baseline = HistoricalMeanBaseline(window=5)
    with pytest.raises(ValueError, match="en az 6 adet"):
        hist_baseline.predict([10.0, 11.0, 12.0], horizon=1)

    with pytest.raises(ValueError, match="Pencere boyutu en az 2"):
        HistoricalMeanBaseline(window=1)

    with pytest.raises(ValueError, match="Lookback periyodu en az 1"):
        SimpleMomentumBaseline(lookback_k=0)


def test_compute_metrics() -> None:
    y_true = [10.0, 12.0, 11.0, 14.0]
    y_pred = [10.0, 12.0, 11.0, 14.0]

    met = compute_metrics(y_true, y_pred)
    assert met.mae == 0.0
    assert met.rmse == 0.0
    assert met.directional_accuracy == 1.0
    assert met.sample_count == 4

    # Farklı uzunlukta hata
    with pytest.raises(ValueError, match="eşit olmalıdır"):
        compute_metrics([1.0], [1.0, 2.0])

    # Boş veri hatası
    with pytest.raises(ValueError, match="en az bir veri"):
        compute_metrics([], [])


def test_evaluate_forecast_against_baseline_approved() -> None:
    y_true = [100.0, 102.0, 105.0, 103.0, 108.0]
    last_known = [99.0, 100.0, 102.0, 105.0, 103.0]

    # Başarılı model (gerçeğe çok yakın ve yönü doğru)
    y_model = [99.8, 101.9, 104.7, 103.2, 107.8]
    # Naif baseline (sadece son bilinen fiyatı tahmin etti)
    y_base = last_known

    report: ModelBenchmarkReport = evaluate_forecast_against_baseline(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_base,
        last_known_prices=last_known,
        model_name="PyTorch_LSTM_v1",
        baseline_name="last_value",
    )

    assert report.beats_baseline is True
    assert report.forecast_approved is True
    assert report.rejection_reason is None
    assert report.relative_mae_improvement > 0.5
    assert report.relative_rmse_improvement > 0.5


def test_evaluate_forecast_against_baseline_rejected() -> None:
    y_true = [100.0, 102.0, 105.0, 103.0, 108.0]
    last_known = [99.0, 100.0, 102.0, 105.0, 103.0]

    # Başarısız model: rastgele veya hatalı tahminler
    y_model = [120.0, 80.0, 130.0, 70.0, 140.0]
    y_base = last_known

    report = evaluate_forecast_against_baseline(
        y_true=y_true,
        y_model=y_model,
        y_baseline=y_base,
        last_known_prices=last_known,
        model_name="Flawed_Model",
        baseline_name="last_value",
    )

    assert report.beats_baseline is False
    assert report.forecast_approved is False
    assert report.rejection_reason is not None
    assert "baseline MAE'den" in report.rejection_reason


def test_walk_forward_simulation_leak_free() -> None:
    """Tüm adımlarda walk-forward tahmin simülasyonu."""
    series = [10.0 + math.sin(i / 5.0) for i in range(30)]
    baseline = LastValueBaseline()

    true_values = []
    base_predictions = []
    last_known = []

    # t=10'dan t=29'a kadar adım adım test
    for t in range(10, len(series) - 1):
        history = series[: t + 1]  # Sadece t anına kadar olan veri! Gelecek asla sızmaz.
        pred_res = baseline.predict(history, horizon=1, symbol="SIM")
        pred_val = pred_res.point_predictions[0]
        actual_val = series[t + 1]

        true_values.append(actual_val)
        base_predictions.append(pred_val)
        last_known.append(history[-1])

    metrics = compute_metrics(true_values, base_predictions, last_known)
    assert metrics.sample_count == len(true_values)
    assert metrics.mae > 0.0
    assert metrics.rmse > 0.0
