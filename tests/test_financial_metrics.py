"""Finansal metrikler ve değerlendirme motoru birim testleri.

FAZ-11 gereksinimleri:
- MAE/RMSE'nin yön için tek başına yetersiz olduğunu doğrular.
- Yön doğruluğu (Directional Accuracy), Sign Test (işaret testi), Theil's U,
  Direction-Penalized Loss, Interval Coverage (PICP/Winkler) ve kalibrasyon (ECE/Brier) test edilir.
"""

from __future__ import annotations

import pytest

from stock_analysis.models.metrics import (
    CalibrationResult,
    DirectionalAssessment,
    DirectionalPnLResult,
    FinancialMetricsReport,
    IntervalCoverageResult,
    SignTestResult,
    calculate_brier_score,
    calculate_calibration,
    calculate_direction_penalized_loss,
    calculate_directional_accuracy,
    calculate_directional_pnl,
    calculate_expected_calibration_error,
    calculate_interval_coverage,
    calculate_mae,
    calculate_mape,
    calculate_rmse,
    calculate_sign_test,
    calculate_smape,
    calculate_theils_u,
    evaluate_financial_forecasts,
)


class TestMagnitudeMetrics:
    """Regresyon büyüklük hatası metrikleri testleri."""

    def test_mae_and_rmse_exact_values(self) -> None:
        y_true = [1.0, 2.0, 3.0, 4.0]
        y_pred = [1.5, 2.5, 2.5, 3.5]
        # Hatalar: 0.5, 0.5, -0.5, -0.5 -> mutlaklar hepsi 0.5
        assert calculate_mae(y_true, y_pred) == pytest.approx(0.5)
        # Kareler: 0.25, 0.25, 0.25, 0.25 -> ortalama 0.25 -> sqrt = 0.5
        assert calculate_rmse(y_true, y_pred) == pytest.approx(0.5)

    def test_zero_error(self) -> None:
        y_true = [10.0, 20.0, -5.0]
        assert calculate_mae(y_true, y_true) == 0.0
        assert calculate_rmse(y_true, y_true) == 0.0

    def test_mape_and_smape(self) -> None:
        y_true = [100.0, 200.0]
        y_pred = [110.0, 190.0]
        # Yüzde hatalar: 10/100 = 10%, 10/200 = 5% -> ortalama %7.5
        assert calculate_mape(y_true, y_pred) == pytest.approx(7.5)
        smape = calculate_smape(y_true, y_pred)
        assert 0.0 < smape < 10.0

    def test_dimension_mismatch_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Dizi boyutları uyuşmuyor"):
            calculate_mae([1.0, 2.0], [1.0])

        with pytest.raises(ValueError, match="boş olamaz"):
            calculate_mae([], [])


class TestDirectionalAndSignMetrics:
    """Finansal yön ve işaret istatistiği testleri."""

    def test_directional_accuracy_perfect_and_inverse(self) -> None:
        y_true = [0.02, -0.01, 0.03, -0.04]
        # Tam doğru yön
        y_pred_perfect = [0.01, -0.05, 0.02, -0.01]
        assert calculate_directional_accuracy(y_true, y_pred_perfect) == 1.0

        # Tam ters yön
        y_pred_inverse = [-0.01, 0.05, -0.02, 0.01]
        assert calculate_directional_accuracy(y_true, y_pred_inverse) == 0.0

    def test_directional_accuracy_partial(self) -> None:
        y_true = [0.01, 0.02, -0.03, -0.02]
        y_pred = [0.05, -0.01, -0.02, 0.01]  # 1. ve 3. doğru (2/4 = 0.50)
        assert calculate_directional_accuracy(y_true, y_pred) == 0.50

    def test_sign_test_random_walk(self) -> None:
        # 100 örneklemde 50 doğru: şans düzeyinde (p-value > 0.50)
        y_true = [0.01] * 50 + [-0.01] * 50
        y_pred = [0.01] * 50 + [0.01] * 50  # 50 doğru, 50 yanlış
        res = calculate_sign_test(y_true, y_pred)
        assert isinstance(res, SignTestResult)
        assert res.hit_ratio == 0.50
        assert res.p_value > 0.50
        assert not res.is_statistically_significant

    def test_sign_test_strong_signal(self) -> None:
        # 100 örneklemde 80 doğru: p < 0.001 anlamlı
        y_true = [0.01] * 80 + [-0.01] * 20
        y_pred = [0.01] * 100  # ilk 80 doğru
        res = calculate_sign_test(y_true, y_pred)
        assert res.hit_ratio == 0.80
        assert res.z_score > 3.0
        assert res.p_value < 0.001
        assert res.is_statistically_significant


class TestTheilsUAndPenalizedLoss:
    """Theil's U ve asimetrik finansal kayıp testleri."""

    def test_theils_u_benchmark_comparison(self) -> None:
        y_true = [0.02, -0.01, 0.03, -0.02]
        # Model actual ile aynı ise Theil's U = 0.0
        assert calculate_theils_u(y_true, y_true) == 0.0

        # Model naif sıfır tahmini ile aynı ise Theil's U = 1.0
        zero_pred = [0.0, 0.0, 0.0, 0.0]
        assert calculate_theils_u(y_true, zero_pred) == pytest.approx(1.0)

        # Model naiften daha kötü hata yapıyorsa Theil's U > 1.0
        bad_pred = [-0.04, 0.05, -0.06, 0.08]
        assert calculate_theils_u(y_true, bad_pred) > 1.0

    def test_direction_penalized_loss(self) -> None:
        y_true = [0.02, 0.02]
        # Doğru yön tahmini: hata = 0.01 her ikisinde de
        y_pred_correct = [0.01, 0.01]
        loss_correct = calculate_direction_penalized_loss(
            y_true, y_pred_correct, penalty_factor=3.0
        )
        assert loss_correct == pytest.approx(0.01)

        # Yanlış yön tahmini: hata = 0.03 her ikisinde de, penalty=3.0 ile 0.09
        y_pred_wrong = [-0.01, -0.01]
        loss_wrong = calculate_direction_penalized_loss(y_true, y_pred_wrong, penalty_factor=3.0)
        assert loss_wrong == pytest.approx(0.09)


class TestDirectionalPnL:
    """Tahmin işaretiyle işlem simülasyonu testleri."""

    def test_directional_pnl_profitable(self) -> None:
        y_true = [0.05, 0.02, -0.03, 0.04]
        # Pozisyon yönü tam isabet: +0.05, +0.02, -(-0.03)=+0.03, +0.04 -> toplam 0.14
        y_pred = [0.01, 0.02, -0.01, 0.03]
        pnl = calculate_directional_pnl(y_true, y_pred)
        assert isinstance(pnl, DirectionalPnLResult)
        assert pnl.cumulative_return == pytest.approx(0.14)
        assert pnl.win_rate == 1.0
        assert pnl.profit_factor >= 999.0

    def test_directional_pnl_losing(self) -> None:
        y_true = [0.05, -0.05]
        y_pred = [-0.01, 0.01]  # her ikisinde de ters pozisyon -> -0.05, -0.05
        pnl = calculate_directional_pnl(y_true, y_pred)
        assert pnl.cumulative_return == pytest.approx(-0.10)
        assert pnl.win_rate == 0.0


class TestIntervalCoverageAndCalibration:
    """Tahmin aralığı kapsaması (PICP) ve kalibrasyon testleri."""

    def test_interval_coverage_well_calibrated(self) -> None:
        y_true = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        # 10 örneklemden 9'u aralık içinde, 1'i (10.0) dışarıda (high=9.5) -> PICP = 0.90
        lower = [y - 1.0 for y in y_true]
        upper = [y + 1.0 for y in y_true]
        upper[-1] = 9.5  # 10.0 > 9.5 dışarıda

        res = calculate_interval_coverage(
            y_true, lower, upper, nominal_coverage=0.90, tolerance=0.05
        )
        assert isinstance(res, IntervalCoverageResult)
        assert res.picp == 0.90
        assert res.is_well_calibrated
        assert res.mpiw > 0.0
        assert res.mean_winkler_score > 0.0

    def test_invalid_interval_bounds_raises_error(self) -> None:
        with pytest.raises(ValueError, match="Alt sınır üst sınırdan büyük olamaz"):
            calculate_interval_coverage([1.0], [5.0], [2.0])

    def test_calibration_and_brier_score(self) -> None:
        y_true_binary = [1, 1, 0, 0]
        # Mükemmel tahmin
        perfect_probs = [1.0, 1.0, 0.0, 0.0]
        assert calculate_brier_score(y_true_binary, perfect_probs) == 0.0

        # Kusurlu tahmin
        probs = [0.8, 0.7, 0.2, 0.3]
        brier = calculate_brier_score(y_true_binary, probs)
        assert 0.0 < brier < 0.2

        calib = calculate_calibration(y_true_binary, probs, n_bins=2)
        assert isinstance(calib, CalibrationResult)
        assert 0.0 <= calib.expected_calibration_error <= 1.0

        ece = calculate_expected_calibration_error(y_true_binary, probs, n_bins=2)
        assert ece == calib.expected_calibration_error


class TestMAEInsufficiencyAndConflictAudit:
    """MAE/RMSE'nin yön için tek başına yetersizliğini ispatlayan denetim testleri."""

    def test_magnitude_directional_conflict_detected(self) -> None:
        """Kritik senaryo: Model A'nın MAE'si küçük olsa da yön doğruluğu şans düzeyinin altındadır.

        Finansal piyasada bu model iflas ettirir; sistem bu çelişkiyi yakalamalıdır.
        """
        # Gerçek getiriler: dalgalanan pozitif ve negatif getiriler
        y_true = [0.015, -0.012, 0.018, -0.014, 0.020, -0.016, 0.011, -0.015]

        # Aldatıcı model: Sabit 0.001 tahmin ediyor.
        # MAE gayet küçük (~0.015), fakat negatif günlerin hiçbirini bilemiyor (DA = 4/8 = 50%)
        # Ve Theil's U ~ 1.0
        deceptive_pred = [0.001] * len(y_true)

        report = evaluate_financial_forecasts(y_true, deceptive_pred)

        assert isinstance(report, FinancialMetricsReport)
        assert report.directional_accuracy <= 0.50
        assert report.magnitude_directional_conflict is True
        assert any("KRİTİK ÇELİŞKİ" in w for w in report.warnings)
        assert not report.beats_naive_baseline

    def test_superior_model_with_higher_mae_wins_directionally(self) -> None:
        """Kritik senaryo 2: Model B yönü %80 biliyor ve karlı getiri sağlıyor."""
        y_true = [0.02, -0.01, 0.03, -0.02, 0.04, -0.01, 0.02, -0.03, 0.01, 0.02]
        # Yönlerin %90'ı doğru
        y_pred = [0.015, -0.008, 0.025, -0.015, 0.035, -0.008, 0.018, -0.025, 0.008, 0.015]

        lower = [p - 0.01 for p in y_pred]
        upper = [p + 0.01 for p in y_pred]
        probs = [0.8 if p > 0 else 0.2 for p in y_pred]

        report = evaluate_financial_forecasts(
            y_true,
            y_pred,
            lower_bounds=lower,
            upper_bounds=upper,
            probabilities=probs,
        )

        assert report.directional_accuracy >= 0.80
        assert report.theils_u < 1.0
        assert report.beats_naive_baseline is True
        assert report.magnitude_directional_conflict is False
        assert report.directional_pnl.cumulative_return > 0
        assert report.assessment in (
            DirectionalAssessment.EXCELLENT,
            DirectionalAssessment.FAVORABLE,
        )
