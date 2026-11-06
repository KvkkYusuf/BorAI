"""Model izleme, veri kayması, performans düşüşü, gecikme ve tahmin dağılımı testleri."""

import random

import pytest

from stock_analysis.models.monitoring import (
    DEFAULT_MONITORING_DISCLAIMER,
    AlertSeverity,
    DataDriftDetector,
    LatencyMonitor,
    ModelAlert,
    ModelMonitoringService,
    MonitoringCategory,
    PerformanceDegradationDetector,
    PredictionDistributionDetector,
    calculate_psi,
)


def test_calculate_psi_identical_distributions() -> None:
    data = [float(i) for i in range(100)]
    psi = calculate_psi(data, data)
    assert psi == pytest.approx(0.0, abs=1e-3)


def test_calculate_psi_shifted_distribution() -> None:
    base = [random.gauss(0.0, 1.0) for _ in range(500)]
    shifted = [random.gauss(3.0, 1.0) for _ in range(500)]

    psi = calculate_psi(base, shifted)
    # 3 sigma kayma ciddi bir PSI üretmelidir (> 0.25)
    assert psi > 0.25


def test_calculate_psi_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="en az 2 örnek"):
        calculate_psi([1.0], [1.0, 2.0])

    with pytest.raises(ValueError, match="num_bins"):
        calculate_psi([1.0, 2.0], [1.0, 2.0], num_bins=1)


def test_data_drift_detector_thresholds() -> None:
    detector = DataDriftDetector(psi_warning_threshold=0.10, psi_critical_threshold=0.25)

    base = [float(i) for i in range(200)]
    stable = [float(i) + random.uniform(-0.1, 0.1) for i in range(200)]
    critical_shift = [float(i) * 5.0 + 100.0 for i in range(200)]

    alerts_stable = detector.check_feature_drift(
        model_id="test_model_v1",
        baseline_features={"f1": base},
        current_features={"f1": stable},
    )
    assert len(alerts_stable) == 0

    alerts_crit = detector.check_feature_drift(
        model_id="test_model_v1",
        baseline_features={"f1": base},
        current_features={"f1": critical_shift},
    )
    assert len(alerts_crit) == 1
    assert alerts_crit[0].severity == AlertSeverity.CRITICAL
    assert alerts_crit[0].category == MonitoringCategory.DATA_DRIFT
    assert alerts_crit[0].current_value >= 0.25


def test_performance_degradation_detector_accuracy() -> None:
    detector = PerformanceDegradationDetector(
        min_direction_accuracy_warning=0.55,
        min_direction_accuracy_critical=0.45,
    )

    # 1. Yüksek başarı (%80)
    preds_good = [0.01, 0.02, 0.01, -0.01, -0.02, 0.03, 0.02, 0.01, -0.01, 0.02]
    acts_good = [0.02, 0.01, 0.03, -0.02, -0.01, 0.01, 0.02, 0.02, -0.03, 0.01]
    alerts_good = detector.check_performance("m1", preds_good, acts_good)
    assert len(alerts_good) == 0

    # 2. Kritik düşük başarı (%10)
    preds_bad = [0.01] * 10
    acts_bad = [-0.02] * 9 + [0.01]
    alerts_bad = detector.check_performance("m1", preds_bad, acts_bad)
    crit_alerts = [a for a in alerts_bad if a.severity == AlertSeverity.CRITICAL]
    assert len(crit_alerts) >= 1
    assert crit_alerts[0].category == MonitoringCategory.PERFORMANCE_DEGRADATION
    assert crit_alerts[0].current_value == 0.10


def test_performance_degradation_mae_multiplier() -> None:
    detector = PerformanceDegradationDetector(
        max_error_multiplier_warning=1.4,
        max_error_multiplier_critical=2.0,
    )

    preds = [0.10, 0.20, 0.15, 0.12, 0.18, 0.22, 0.11, 0.14, 0.19, 0.16]
    acts = [0.50, 0.60, 0.55, 0.52, 0.58, 0.62, 0.51, 0.54, 0.59, 0.56]
    # Her örnekte hata ~0.40
    # Baseline MAE 0.10 ise, multiplier = 0.40 / 0.10 = 4.0 >= 2.0 (CRITICAL)
    alerts = detector.check_performance("m1", preds, acts, baseline_mae=0.10)
    crit_mae = [a for a in alerts if a.metric_name == "error_multiplier"]
    assert len(crit_mae) == 1
    assert crit_mae[0].severity == AlertSeverity.CRITICAL
    assert crit_mae[0].current_value >= 2.0


def test_latency_monitor_thresholds() -> None:
    monitor = LatencyMonitor(
        p95_warning_ms=100.0,
        p95_critical_ms=250.0,
        max_single_critical_ms=500.0,
    )

    # Hızlı çıkarımlar (< 50ms)
    fast_latencies = [20.0, 25.0, 30.0, 22.0, 18.0] * 5
    assert len(monitor.check_latencies("m1", fast_latencies)) == 0

    # p95 aşımı (20 örnekten sonuncular > 300ms)
    slow_latencies = [50.0] * 18 + [300.0, 320.0]
    alerts_slow = monitor.check_latencies("m1", slow_latencies)
    assert len(alerts_slow) >= 1
    p95_alert = next(a for a in alerts_slow if a.metric_name == "p95_latency_ms")
    assert p95_alert.severity == AlertSeverity.CRITICAL
    assert p95_alert.current_value >= 250.0

    # Tekil aşırı tepe gecikme
    spike_latencies = [30.0] * 19 + [750.0]
    alerts_spike = monitor.check_latencies("m1", spike_latencies)
    max_alert = next(a for a in alerts_spike if a.metric_name == "max_latency_ms")
    assert max_alert.severity == AlertSeverity.CRITICAL
    assert max_alert.current_value == 750.0


def test_prediction_distribution_mode_collapse() -> None:
    detector = PredictionDistributionDetector(collapse_threshold_ratio=0.90)

    # 100 tahminin 95'i pozitif (tek yön çökmesi)
    collapsed_preds = [0.015] * 95 + [-0.005] * 5
    alerts = detector.check_distribution("m1", collapsed_preds)

    collapse_alerts = [a for a in alerts if a.metric_name == "direction_collapse_ratio"]
    assert len(collapse_alerts) == 1
    assert collapse_alerts[0].severity == AlertSeverity.CRITICAL
    assert collapse_alerts[0].current_value == 0.95


def test_prediction_distribution_zero_variance() -> None:
    detector = PredictionDistributionDetector()

    # Model hep tamamen aynı sayıyı üretiyor
    frozen_preds = [0.02] * 50
    alerts = detector.check_distribution("m1", frozen_preds)

    zero_var = [a for a in alerts if a.metric_name == "prediction_std_dev"]
    assert len(zero_var) == 1
    assert zero_var[0].severity == AlertSeverity.CRITICAL


def test_prediction_distribution_extreme_outliers() -> None:
    detector = PredictionDistributionDetector(max_absolute_return_threshold=0.30)

    # 2 adet %45 ve -%50 getiri tahmini (BIST günlük %10 marjı düşünülünce mantık dışı)
    outlier_preds = [0.01] * 20 + [0.45, -0.50]
    alerts = detector.check_distribution("m1", outlier_preds)

    ext_alerts = [a for a in alerts if a.metric_name == "extreme_outlier_count"]
    assert len(ext_alerts) == 1
    assert ext_alerts[0].severity == AlertSeverity.WARNING
    assert ext_alerts[0].current_value == 2.0


def test_model_monitoring_service_full_workflow() -> None:
    received_alerts: list[ModelAlert] = []

    def mock_handler(alert: ModelAlert) -> None:
        received_alerts.append(alert)

    service = ModelMonitoringService(alert_handlers=[mock_handler])

    # Hem gecikme hem de çökme içeren bir durum
    report = service.run_full_check(
        model_id="bist_mlp_prod_v1",
        recent_predictions=[0.02] * 25,  # Çökme + sıfır varyans
        recent_actuals=[-0.01] * 25,  # %0 yön başarısı
        recent_latencies_ms=[10.0] * 20 + [600.0],  # Gecikme uyarısı / tepe aşımı
    )

    assert report.total_alerts > 0
    assert report.has_critical_alerts is True
    assert len(service.get_alert_history()) == report.total_alerts
    assert len(received_alerts) == report.total_alerts

    # Yasal uyarı kontrolü
    assert DEFAULT_MONITORING_DISCLAIMER in report.disclaimer
    for a in report.alerts:
        assert DEFAULT_MONITORING_DISCLAIMER in a.disclaimer

    # Geçmişi temizleme
    service.clear_history()
    assert len(service.get_alert_history()) == 0
