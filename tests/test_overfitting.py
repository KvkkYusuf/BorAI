"""Backtest overfitting (aşırı uyum) ve Deflated Sharpe Ratio testleri."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stock_analysis.models.overfitting import (
    BacktestOverfittingReport,
    BacktestTrialRecord,
    BacktestTrialRegistry,
    OverfittingRiskLevel,
    calculate_deflated_sharpe_ratio,
    calculate_expected_max_sharpe,
    calculate_haircut_sharpe_ratio,
    calculate_minimum_track_record_length,
    calculate_moments,
    evaluate_backtest_overfitting,
    inverse_normal_cdf,
    standard_normal_cdf,
)


class TestStatisticalHelpers:
    """Normal dağılım ve moment fonksiyonları testleri."""

    def test_standard_normal_cdf_known_values(self) -> None:
        """Standart normal CDF bilinen referans değerlerini sağlamalıdır."""
        assert math.isclose(standard_normal_cdf(0.0), 0.5, abs_tol=1e-6)
        assert math.isclose(standard_normal_cdf(1.96), 0.975002, abs_tol=1e-4)
        assert math.isclose(standard_normal_cdf(-1.96), 0.024998, abs_tol=1e-4)

    def test_inverse_normal_cdf_known_values(self) -> None:
        """Acklam probit ters CDF bilinen değerleri doğrulamalıdır."""
        assert math.isclose(inverse_normal_cdf(0.5), 0.0, abs_tol=1e-6)
        assert math.isclose(inverse_normal_cdf(0.975), 1.95996, abs_tol=1e-4)
        assert math.isclose(inverse_normal_cdf(0.025), -1.95996, abs_tol=1e-4)

    def test_inverse_normal_cdf_out_of_bounds_raises_error(self) -> None:
        """Olasılık (0, 1) aralığı dışındaysa ValueError verilmelidir."""
        with pytest.raises(ValueError, match="açık aralığında"):
            inverse_normal_cdf(0.0)
        with pytest.raises(ValueError, match="açık aralığında"):
            inverse_normal_cdf(1.0)
        with pytest.raises(ValueError, match="açık aralığında"):
            inverse_normal_cdf(-0.1)

    def test_calculate_moments_symmetric_distribution(self) -> None:
        """Simetrik seride ortalama, standart sapma, çarpıklık ve basıklık doğrulanır."""
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.015, size=500)
        mean, std, skew, kurt = calculate_moments(rets)

        assert math.isclose(mean, 0.001, abs_tol=0.002)
        assert math.isclose(std, 0.015, abs_tol=0.002)
        assert abs(skew) < 0.25  # Simetrik dağılımda çarpıklık ~ 0
        assert 2.0 < kurt < 4.0  # Normal dağılımda basıklık ~ 3

    def test_calculate_moments_too_short_raises_error(self) -> None:
        """3 elemandan kısa serilerde hata fırlatılmalıdır."""
        with pytest.raises(ValueError, match="en az 3 getiri"):
            calculate_moments([0.01, -0.01])


class TestTrialRecordAndRegistry:
    """Deneme kaydı ve kayıt defteri testleri."""

    def test_trial_record_invalid_bars_raises_error(self) -> None:
        """sample_bars 2'den küçükse hata fırlatılmalıdır."""
        with pytest.raises(ValueError, match="sample_bars en az 2"):
            BacktestTrialRecord(
                trial_id="t1",
                strategy_name="MLP-v1",
                symbol="THYAO",
                sharpe_ratio=1.5,
                sample_bars=1,
            )

    def test_trial_registry_register_and_retrieve(self) -> None:
        """Deneme defterine kayıt ekleme ve sorgulama test edilir."""
        registry = BacktestTrialRegistry()
        assert registry.total_trials == 0

        t1 = BacktestTrialRecord(
            trial_id="t1",
            strategy_name="MLP-H1",
            symbol="THYAO",
            sharpe_ratio=1.8,
            sample_bars=252,
        )
        t2 = BacktestTrialRecord(
            trial_id="t2",
            strategy_name="CNN-H1",
            symbol="THYAO",
            sharpe_ratio=1.2,
            sample_bars=252,
        )

        registry.register_trial(t1)
        registry.register_trial(t2)

        assert registry.total_trials == 2
        assert registry.get_trial("t1") == t1
        assert registry.get_trial("t99") is None

        dist = registry.get_sharpe_distribution()
        assert len(dist) == 2
        assert dist[0] == 1.8
        assert dist[1] == 1.2


class TestDeflatedSharpeCalculations:
    """DSR, Beklenen Max Sharpe ve Haircut Sharpe hesaplama testleri."""

    def test_expected_max_sharpe_increases_with_trials(self) -> None:
        """Deneme sayısı arttıkça şans eseri beklenen azami Sharpe yükselmelidir."""
        std = 0.50
        exp_max_1 = calculate_expected_max_sharpe(n_trials=1, sharpe_std=std)
        exp_max_10 = calculate_expected_max_sharpe(n_trials=10, sharpe_std=std)
        exp_max_100 = calculate_expected_max_sharpe(n_trials=100, sharpe_std=std)
        exp_max_1000 = calculate_expected_max_sharpe(n_trials=1000, sharpe_std=std)

        assert exp_max_1 == 0.0
        assert 0.0 < exp_max_10 < exp_max_100 < exp_max_1000

    def test_deflated_sharpe_ratio_penalizes_multiple_trials(self) -> None:
        """Aynı Sharpe oranı, çok fazla deneme yapıldığında daha düşük DSR üretmelidir."""
        sr = 2.0
        sample_bars = 504  # 2 yıllık günlük veri
        std = 0.60

        dsr_few = calculate_deflated_sharpe_ratio(
            selected_sharpe=sr,
            sample_bars=sample_bars,
            n_trials=3,
            sharpe_std=std,
        )

        dsr_many = calculate_deflated_sharpe_ratio(
            selected_sharpe=sr,
            sample_bars=sample_bars,
            n_trials=2000,
            sharpe_std=std,
        )

        assert dsr_few > 0.95
        assert dsr_many < dsr_few

    def test_deflated_sharpe_ratio_penalizes_negative_skewness(self) -> None:
        """Sol kuyruk riski (negatif çarpıklık / crash riski) DSR'yi düşürmelidir."""
        sr = 1.8
        sample_bars = 252
        std = 0.40

        dsr_symmetric = calculate_deflated_sharpe_ratio(
            selected_sharpe=sr,
            sample_bars=sample_bars,
            n_trials=10,
            sharpe_std=std,
            skewness=0.0,
            kurtosis=3.0,
        )

        dsr_skewed = calculate_deflated_sharpe_ratio(
            selected_sharpe=sr,
            sample_bars=sample_bars,
            n_trials=10,
            sharpe_std=std,
            skewness=-1.5,  # Belirgin sol kuyruk riski
            kurtosis=6.0,  # Kalın kuyruk
        )

        assert dsr_skewed < dsr_symmetric

    def test_haircut_sharpe_discount(self) -> None:
        """Haircut Sharpe deneme sayısı arttıkça nominal Sharpe'ı tıraşlamalıdır."""
        sr = 2.0
        hc_1 = calculate_haircut_sharpe_ratio(sr, n_trials=1, sample_bars=252)
        hc_20 = calculate_haircut_sharpe_ratio(sr, n_trials=20, sample_bars=252)
        hc_100 = calculate_haircut_sharpe_ratio(sr, n_trials=100, sample_bars=252)

        assert hc_1 == sr
        assert hc_100 < hc_20 < sr

    def test_minimum_track_record_length(self) -> None:
        """Sharpe oranı azaldıkça istatistiksel anlam için gereken MinTRL artmalıdır."""
        min_trl_strong = calculate_minimum_track_record_length(
            sharpe=2.5, n_trials=5, sharpe_std=0.40
        )
        min_trl_weak = calculate_minimum_track_record_length(
            sharpe=0.5, n_trials=50, sharpe_std=0.50
        )
        min_trl_negative = calculate_minimum_track_record_length(
            sharpe=-0.2, n_trials=10, sharpe_std=0.50
        )

        assert min_trl_strong < 500
        assert min_trl_weak > min_trl_strong
        assert min_trl_negative == 999_999


class TestOverfittingEvaluationReport:
    """Aşırı uyum raporu motoru ve hukuki uyum testleri."""

    def test_evaluate_backtest_overfitting_low_risk(self) -> None:
        """Az sayıda denemeyle elde edilen güçlü Sharpe düşük aşırı uyum riski vermelidir."""
        registry = BacktestTrialRegistry()
        for i in range(3):
            registry.register_trial(
                BacktestTrialRecord(
                    trial_id=f"trial_{i}",
                    strategy_name=f"Strategy_{i}",
                    symbol="THYAO",
                    sharpe_ratio=1.5 + (i * 0.4),  # En yüksek 2.3
                    sample_bars=500,
                )
            )

        report = evaluate_backtest_overfitting(registry)
        assert isinstance(report, BacktestOverfittingReport)
        assert report.total_trials_tested == 3
        assert report.nominal_sharpe == 2.3
        assert report.risk_level == OverfittingRiskLevel.LOW
        assert report.is_statistically_significant is True

        r_dict = report.to_dict()
        assert r_dict["risk_level"] == "low"
        assert "min_track_record_length_bars" in r_dict

        summary = report.format_summary()
        assert "Backtest Aşırı Uyum (Overfitting) Raporu" in summary
        assert "Yasal Uyarı" in summary

    def test_evaluate_backtest_overfitting_critical_risk(self) -> None:
        """Çok fazla deneme yapılıp düşük/ortalama Sharpe bulunmuşsa CRITICAL risk verilmelidir."""
        registry = BacktestTrialRegistry()
        np.random.seed(42)
        # 100 rastgele deneme üretelim (ortalama 0.3, standart sapma 0.6)
        for i in range(100):
            sr = float(np.random.normal(0.3, 0.6))
            registry.register_trial(
                BacktestTrialRecord(
                    trial_id=f"grid_search_{i}",
                    strategy_name="HyperparamGrid",
                    symbol="GARAN",
                    sharpe_ratio=sr,
                    sample_bars=100,
                )
            )

        # Spesifik olarak vasat bir denemeyi seçelim
        report = evaluate_backtest_overfitting(registry, selected_trial_id="grid_search_0")
        assert report.risk_level in (OverfittingRiskLevel.HIGH, OverfittingRiskLevel.CRITICAL)
        assert report.is_statistically_significant is False

    def test_empty_registry_raises_error(self) -> None:
        """Kayıt yoksa evaluate_backtest_overfitting hata fırlatmalıdır."""
        empty_reg = BacktestTrialRegistry()
        with pytest.raises(ValueError, match="en az bir deneme"):
            evaluate_backtest_overfitting(empty_reg)

    def test_unknown_trial_id_raises_key_error(self) -> None:
        """Var olmayan trial_id KeyError fırlatmalıdır."""
        registry = BacktestTrialRegistry()
        registry.register_trial(
            BacktestTrialRecord(
                trial_id="valid_1",
                strategy_name="S1",
                symbol="KCHOL",
                sharpe_ratio=1.0,
                sample_bars=100,
            )
        )
        with pytest.raises(KeyError, match="Trial ID bulunamadı"):
            evaluate_backtest_overfitting(registry, selected_trial_id="non_existent")

    def test_non_compliant_disclaimer_raises_error(self) -> None:
        """Yasaklı/vaat içeren yasal metin uyum kuralından geçmemelidir."""
        registry = BacktestTrialRegistry()
        registry.register_trial(
            BacktestTrialRecord(
                trial_id="t1",
                strategy_name="S1",
                symbol="THYAO",
                sharpe_ratio=1.5,
                sample_bars=252,
            )
        )
        with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
            evaluate_backtest_overfitting(
                registry,
                disclaimer="Bu model kesin kâr vaat eder ve al/sat garantilidir.",
            )
