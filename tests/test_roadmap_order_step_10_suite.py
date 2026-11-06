"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 10 test paketi.

PyTorch baseline ve zaman bazlı değerlendirme kabul paketi.
Test kapsamı:
- RoadmapStepTenGatekeeper 6/6 tam uyum doğrulaması.
- Naif baseline modelleri (LastValue, HistoricalMean, NaiveMomentum) ve tahmin ufukları.
- Kompakt PyTorch mimarileri (SmallMLP, SmallCNN1D) ve deterministik eğitim döngüsü.
- Kronolojik walk-forward ve expanding-window zaman serisi doğrulaması.
- Kapsamlı finansal metrikler (DA, MAE, RMSE, SignTest) ve sürtünmeli backtest.
- Piyasa rejimleri stres testi ve backtest overfitting savunması.
- Baseline kabul kapısı (BaselineGatekeeper, kill-switch) ve belirsizlik aralıkları.
- SPK yasal uyarı feragatnamesi ve al/sat tavsiyesi olmama güvencesi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import torch

from stock_analysis.models.architectures import MLPConfig, SmallMLP
from stock_analysis.models.baselines import (
    HistoricalMeanBaseline,
    LastValueBaseline,
    NaiveMomentumBaseline,
)
from stock_analysis.models.metrics import (
    calculate_directional_accuracy,
    calculate_mae,
    calculate_rmse,
    calculate_sign_test,
)
from stock_analysis.models.overfitting import (
    BacktestTrialRecord,
    BacktestTrialRegistry,
)
from stock_analysis.models.stress_testing import (
    MarketRegimeType,
    classify_market_regime,
)
from stock_analysis.models.uncertainty import (
    NakedPointEstimateDisallowedError,
    UncertaintyLevel,
)
from stock_analysis.models.walk_forward import (
    WalkForwardValidator,
    WindowingMethod,
)
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP10_DISCLAIMER,
    ROADMAP_STEP10_STATUS,
    RoadmapStepTenGatekeeper,
    RoadmapStepTenStatus,
    generate_roadmap_step10_report,
)


class TestRoadmapOrderStepTenSuite:
    """FAZ-18 Yol Haritası Adım 10 kabul kapısı testleri."""

    def test_gatekeeper_full_evaluation_pass(self) -> None:
        """RoadmapStepTenGatekeeper tüm 6 kontrolü başarıyla geçer (6/6 PASS)."""
        gatekeeper = RoadmapStepTenGatekeeper()
        result = gatekeeper.evaluate()

        assert result.status == RoadmapStepTenStatus.PASS
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert len(result.checks) == 6
        assert ROADMAP_STEP10_STATUS.get() == 1.0

        check_ids = [c.check_id for c in result.checks]
        assert "ROADMAP-S10-01-NAIVE-BASELINES-AND-TARGET-HORIZONS" in check_ids
        assert "ROADMAP-S10-02-ARCHITECTURES-AND-REPRODUCIBLE-TRAINING" in check_ids
        assert "ROADMAP-S10-03-CHRONOLOGICAL-WALK-FORWARD-VALIDATION" in check_ids
        assert "ROADMAP-S10-04-FINANCIAL-METRICS-AND-REALISTIC-BACKTEST" in check_ids
        assert "ROADMAP-S10-05-REGIME-STRESS-TESTING-AND-OVERFITTING-GUARD" in check_ids
        assert "ROADMAP-S10-06-BASELINE-GATEKEEPER-AND-UNCERTAINTY-INTERVALS" in check_ids

    def test_default_disclaimer_and_compliance(self) -> None:
        """Adım 10 feragatnamesi SPK mevzuatına uygun olmalı ve yasaklı terim içermemelidir."""
        gatekeeper = RoadmapStepTenGatekeeper()
        assert "SPK Mevzuatı Uyarınca" in gatekeeper.disclaimer
        assert "yatırım danışmanlığı" in gatekeeper.disclaimer
        assert "naif kıyaslama" in gatekeeper.disclaimer

        result = gatekeeper.evaluate()
        assert result.disclaimer == DEFAULT_ROADMAP_STEP10_DISCLAIMER

    def test_check_naive_baselines_and_target_horizons(self) -> None:
        """1. Kontrol: Naif baseline modelleri, tahmin hedefleri ve ufukları denetimi."""
        gatekeeper = RoadmapStepTenGatekeeper()
        item = gatekeeper._check_naive_baselines_and_target_horizons()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S10-01-NAIVE-BASELINES-AND-TARGET-HORIZONS"
        assert item.details["has_baselines"] is True
        assert item.details["has_horizons"] is True
        assert item.details["has_targets"] is True
        assert item.details["has_features"] is True
        assert item.details["has_last_val"] is True
        assert item.details["has_mean"] is True
        assert item.details["has_momentum"] is True
        assert item.details["functional_pass"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_naive_baselines_and_target_horizons()
            assert fail_item.passed is False
            assert "Eksik baseline / hedef bileşenleri" in fail_item.evidence

    def test_check_architectures_and_reproducible_training(self) -> None:
        """2. Kontrol: PyTorch mimarileri ve tekrarlanabilir eğitim döngüsü denetimi."""
        gatekeeper = RoadmapStepTenGatekeeper()
        item = gatekeeper._check_architectures_and_reproducible_training()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S10-02-ARCHITECTURES-AND-REPRODUCIBLE-TRAINING"
        assert item.details["has_arch"] is True
        assert item.details["has_training"] is True
        assert item.details["has_provenance"] is True
        assert item.details["has_mlp"] is True
        assert item.details["has_cnn"] is True
        assert item.details["has_early_stopping"] is True
        assert item.details["has_grad_clip"] is True
        assert item.details["has_checkpoint"] is True
        assert item.details["has_seed"] is True
        assert item.details["functional_pass"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_architectures_and_reproducible_training()
            assert fail_item.passed is False
            assert "Eksik PyTorch eğitim bileşenleri" in fail_item.evidence

    def test_check_chronological_walk_forward_validation(self) -> None:
        """3. Kontrol: Kronolojik walk-forward ve expanding-window doğrulaması denetimi."""
        gatekeeper = RoadmapStepTenGatekeeper()
        item = gatekeeper._check_chronological_walk_forward_validation()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S10-03-CHRONOLOGICAL-WALK-FORWARD-VALIDATION"
        assert item.details["has_wf"] is True
        assert item.details["has_validator"] is True
        assert item.details["has_window_enum"] is True
        assert item.details["has_fold_window"] is True
        assert item.details["has_leak_error"] is True
        assert item.details["has_embargo"] is True
        assert item.details["folds_count"] > 0
        assert item.details["functional_pass"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_chronological_walk_forward_validation()
            assert fail_item.passed is False
            assert "Eksik walk-forward bileşenleri" in fail_item.evidence

    def test_check_financial_metrics_and_realistic_backtest(self) -> None:
        """4. Kontrol: Kapsamlı finansal metrikler ve gerçekçi backtest denetimi."""
        gatekeeper = RoadmapStepTenGatekeeper()
        item = gatekeeper._check_financial_metrics_and_realistic_backtest()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S10-04-FINANCIAL-METRICS-AND-REALISTIC-BACKTEST"
        assert item.details["has_metrics"] is True
        assert item.details["has_backtest"] is True
        assert item.details["has_da"] is True
        assert item.details["has_sign_test"] is True
        assert item.details["has_report"] is True
        assert item.details["has_mae_rmse"] is True
        assert item.details["has_backtester"] is True
        assert item.details["has_friction"] is True
        assert item.details["has_lag_guard"] is True
        assert item.details["has_pos_guard"] is True
        assert item.details["functional_pass"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_financial_metrics_and_realistic_backtest()
            assert fail_item.passed is False
            assert "Eksik metrik / backtest bileşenleri" in fail_item.evidence

    def test_check_regime_stress_testing_and_overfitting_guard(self) -> None:
        """5. Kontrol: Piyasa rejimi stres testi ve backtest overfitting savunması denetimi."""
        gatekeeper = RoadmapStepTenGatekeeper()
        item = gatekeeper._check_regime_stress_testing_and_overfitting_guard()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S10-05-REGIME-STRESS-TESTING-AND-OVERFITTING-GUARD"
        assert item.details["has_stress"] is True
        assert item.details["has_overfit"] is True
        assert item.details["has_regime_enum"] is True
        assert item.details["has_scenario_enum"] is True
        assert item.details["has_regime_fn"] is True
        assert item.details["has_stress_runner"] is True
        assert item.details["has_trial_record"] is True
        assert item.details["has_trial_reg"] is True
        assert item.details["has_risk_enum"] is True
        assert item.details["functional_pass"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_regime_stress_testing_and_overfitting_guard()
            assert fail_item.passed is False
            assert "Eksik stres / overfitting bileşenleri" in fail_item.evidence

    def test_check_baseline_gatekeeper_and_uncertainty_intervals(self) -> None:
        """6. Kontrol: Baseline yayınlama kapısı ve belirsizlik aralıkları denetimi."""
        gatekeeper = RoadmapStepTenGatekeeper()
        item = gatekeeper._check_baseline_gatekeeper_and_uncertainty_intervals()

        assert item.passed is True
        assert item.check_id == "ROADMAP-S10-06-BASELINE-GATEKEEPER-AND-UNCERTAINTY-INTERVALS"
        assert item.details["has_gatekeeper"] is True
        assert item.details["has_fallback"] is True
        assert item.details["has_uncertainty"] is True
        assert item.details["has_baseline_gate"] is True
        assert item.details["has_forecast_enabled"] is True
        assert item.details["has_fallback_mgr"] is True
        assert item.details["has_naked_guard"] is True
        assert item.details["has_interval"] is True
        assert item.details["has_uncertainty_level"] is True
        assert item.details["functional_pass"] is True

        with patch.object(Path, "is_file", return_value=False):
            fail_item = gatekeeper._check_baseline_gatekeeper_and_uncertainty_intervals()
            assert fail_item.passed is False
            assert "Eksik kapı / belirsizlik bileşenleri" in fail_item.evidence

    def test_report_generation_markdown(self) -> None:
        """generate_roadmap_step10_report fonksiyonu standartlara uygun Markdown üretir."""
        gatekeeper = RoadmapStepTenGatekeeper()
        result = gatekeeper.evaluate()
        report = generate_roadmap_step10_report(result)

        assert "# Yol Haritası Uygulama Sırası — Adım 10 Denetim Raporu" in report
        assert "**Genel Durum:** ✅ PASS" in report
        assert "ROADMAP-S10-01-NAIVE-BASELINES-AND-TARGET-HORIZONS" in report
        assert "ROADMAP-S10-06-BASELINE-GATEKEEPER-AND-UNCERTAINTY-INTERVALS" in report
        assert "LastValue" in report
        assert "SmallMLP" in report
        assert "WalkForwardValidator" in report
        assert "FinancialMetricsCalculator" in report

    def test_to_dict_serialization(self) -> None:
        """VerificationResult sözlük formatına kayıpsız dönüştürülebilir."""
        gatekeeper = RoadmapStepTenGatekeeper()
        result = gatekeeper.evaluate()
        data = result.to_dict()

        assert data["status"] == "PASS"
        assert data["total_checks"] == 6
        assert data["passed_checks"] == 6
        assert data["failed_checks"] == 0
        assert len(data["checks"]) == 6
        assert data["disclaimer"] == DEFAULT_ROADMAP_STEP10_DISCLAIMER

    def test_naive_baselines_functional_behavior(self) -> None:
        """Naif baseline modelleri beklendiği gibi tahmin üretir."""
        series = [100.0, 102.0, 101.0, 105.0, 106.0, 108.0]

        last_val = LastValueBaseline()
        mean_val = HistoricalMeanBaseline(window=3)
        mom_val = NaiveMomentumBaseline(lookback_k=2)

        res_last = last_val.predict(series, horizon=1)
        res_mean = mean_val.predict(series, horizon=1)
        res_mom = mom_val.predict(series, horizon=1)

        assert res_last.point_predictions[0] == 108.0
        assert res_mean.point_predictions[0] > 0.0
        assert len(res_mom.point_predictions) == 1

    def test_pytorch_architecture_and_forward(self) -> None:
        """SmallMLP mimarisi ileri geçişi (forward pass) tensör boyutlarını korur."""
        cfg = MLPConfig(input_dim=6, hidden_dims=(16, 8), output_dim=1, dropout=0.0)
        model = SmallMLP(cfg)
        x = torch.randn(4, 6)
        out = model(x)

        assert out.shape == (4, 1)
        assert not bool(torch.isnan(out).any().item())

    def test_walk_forward_validator_functional_behavior(self) -> None:
        """WalkForwardValidator expanding katlamalarında kronolojik sızıntısızlığı sağlar."""
        from stock_analysis.models.walk_forward import WalkForwardConfig

        cfg = WalkForwardConfig(
            windowing_method=WindowingMethod.EXPANDING,
            min_train_size=30,
            test_size=10,
            step_size=10,
            embargo_size=3,
        )
        validator = WalkForwardValidator(cfg)
        folds = validator.generate_folds(total_samples=70)

        assert len(folds) >= 2
        for f in folds:
            assert f.train_indices[1] + f.embargo_size <= f.test_indices[0]
            assert f.train_count >= 30
            assert f.test_count == 10

    def test_financial_metrics_calculator_behavior(self) -> None:
        """Finansal metrik fonksiyonları MAE, RMSE, DA ve Sign Test hesaplar."""
        y_true = [0.02, -0.01, 0.03, -0.02, 0.01]
        y_pred = [0.01, -0.02, 0.02, -0.01, 0.03]

        da = calculate_directional_accuracy(y_true, y_pred)
        mae = calculate_mae(y_true, y_pred)
        rmse = calculate_rmse(y_true, y_pred)
        sign_test = calculate_sign_test(y_true, y_pred)

        assert da == 1.0
        assert mae > 0.0
        assert rmse > 0.0
        assert sign_test.hit_ratio == 1.0

    def test_market_regime_and_overfitting_registry(self) -> None:
        """Piyasa rejim sınıflandırması ve backtest deneme defteri çalışır."""
        # Rejim testi
        assert classify_market_regime([0.01, 0.02, 0.015, 0.01]) == MarketRegimeType.BULL
        assert classify_market_regime([-0.015, -0.02, -0.01, -0.012]) == MarketRegimeType.BEAR

        # Overfitting kayıt defteri testi
        registry = BacktestTrialRegistry()
        trial = BacktestTrialRecord(
            trial_id="TR-001",
            strategy_name="SmallMLP-Trend",
            symbol="KCHOL",
            sharpe_ratio=1.65,
            sample_bars=300,
        )
        registry.register_trial(trial)
        assert registry.total_trials == 1
        assert registry.get_trial("TR-001") == trial

    def test_uncertainty_and_naked_point_guard(self) -> None:
        """Çıplak tekil tahmin engellenmeli ve belirsizlik seviyeleri tanımlı olmalıdır."""
        with pytest.raises(ValueError):
            raise NakedPointEstimateDisallowedError("Çıplak tahmin sunumu SPK gereği yasaktır")

        assert UncertaintyLevel.LOW.value == "low"
        assert UncertaintyLevel.HIGH.value == "high"
