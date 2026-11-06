"""Sürüm 4 (PyTorch Araştırma Hattı) - Baseline ve Walk-Forward Kabul Kapısı Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 4 — PyTorch araştırma hattı:
  - Baseline karşılaştırması ve walk-forward raporu mevcut.
"""

from __future__ import annotations

from unittest.mock import patch

from stock_analysis.release.release_four_baseline_walk_forward import (
    RELEASE_FOUR_BASELINE_WF_STATUS,
    BaselineWalkForwardCheckItem,
    BaselineWalkForwardVerificationResult,
    ReleaseFourBaselineGateStatus,
    ReleaseFourBaselineWalkForwardGatekeeper,
    generate_markdown_report,
)


class TestReleaseFourBaselineWalkForwardSuite:
    """Sürüm 4 baseline kıyaslama ve walk-forward kabul kriterleri testleri."""

    def test_check_naive_baselines_suite(self) -> None:
        """Naif modeller (LastValue, HistoricalMean, SimpleMomentum) kontrolü."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        item: BaselineWalkForwardCheckItem = gatekeeper._check_naive_baselines_suite()

        assert item.check_id == "REL4-BWF-01-NAIVE-BASELINES"
        assert item.passed is True
        assert "LastValue" in item.details
        assert item.metadata.get("deterministic") is True
        assert "HistoricalMean" in item.metadata.get("baselines", [])

    def test_check_walk_forward_expanding_window_engine(self) -> None:
        """Walk-forward & expanding-window motoru ve embargo kontrolü."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        item: BaselineWalkForwardCheckItem = (
            gatekeeper._check_walk_forward_expanding_window_engine()
        )

        assert item.check_id == "REL4-BWF-02-WALK-FORWARD-ENGINE"
        assert item.passed is True
        assert item.metadata.get("leak_protection") is True
        assert item.metadata.get("embargo_size", 0) > 0
        assert item.metadata.get("n_folds", 0) >= 2

    def test_check_financial_directional_metrics(self) -> None:
        """Finansal yön doğruluğu ve model karşılaştırma metrikleri kontrolü."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        item: BaselineWalkForwardCheckItem = gatekeeper._check_financial_directional_metrics()

        assert item.check_id == "REL4-BWF-03-DIRECTIONAL-METRICS"
        assert item.passed is True
        assert "Finansal yön doğruluğu" in item.details
        assert item.metadata.get("rejection_logic_active") is True
        assert "DirectionalAccuracy" in item.metadata.get("metrics_evaluated", [])

    def test_check_execution_frictions_and_strategy_simulation(self) -> None:
        """Gerçekçi işlem maliyeti (komisyon, kayma, BSMV) ve risk kontrolü."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        item: BaselineWalkForwardCheckItem = (
            gatekeeper._check_execution_frictions_and_strategy_simulation()
        )

        assert item.check_id == "REL4-BWF-04-EXECUTION-FRICTIONS"
        assert item.passed is True
        assert item.metadata.get("commission_rate") == 0.001
        assert item.metadata.get("slippage_rate") == 0.0005
        assert item.metadata.get("total_trades", 0) > 0
        assert item.metadata.get("sharpe_ratio") is not None

    def test_check_stress_testing_and_overfitting_controls(self) -> None:
        """Çoklu piyasa rejimleri ve aşırı öğrenme (overfitting) uyarısı kontrolü."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        item: BaselineWalkForwardCheckItem = (
            gatekeeper._check_stress_testing_and_overfitting_controls()
        )

        assert item.check_id == "REL4-BWF-05-OVERFITTING-ALERT"
        assert item.passed is True
        assert item.metadata.get("overfitting_detected") is True
        assert "varyansı" in item.metadata.get("warning_text", "")

    def test_check_audit_report_and_disclaimer_compliance(self) -> None:
        """SPK mevzuatına uygun yasal uyarı ve rapor hazırlık kontrolü."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        item: BaselineWalkForwardCheckItem = (
            gatekeeper._check_audit_report_and_disclaimer_compliance()
        )

        assert item.check_id == "REL4-BWF-06-AUDIT-DISCLAIMER"
        assert item.passed is True
        assert item.metadata.get("disclaimer_verified") is True
        assert "RELEASE_4_BASELINE_WALK_FORWARD_REPORT.md" in item.details

    def test_verify_all_baseline_walk_forward_criteria(self) -> None:
        """Tüm kontrollerin toplu yürütülmesi ve genel kapı onayı."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()
        result: BaselineWalkForwardVerificationResult = (
            gatekeeper.verify_all_baseline_walk_forward_criteria()
        )

        assert result.status == ReleaseFourBaselineGateStatus.PASSED
        assert result.is_passed is True
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert RELEASE_FOUR_BASELINE_WF_STATUS.get() == 1.0

        # Markdown rapor üretimi doğrulaması
        report = generate_markdown_report(result)
        assert "# Sürüm 4 — Baseline Karşılaştırması ve Walk-Forward Kabul Raporu" in report
        assert "✅ GEÇTİ (PASS)" in report
        assert "REL4-BWF-01-NAIVE-BASELINES" in report
        assert "REL4-BWF-06-AUDIT-DISCLAIMER" in report
        assert "Yatırım Tavsiyesi Yasağı" in report

    def test_gatekeeper_failure_scenario(self) -> None:
        """Herhangi bir kontrolün başarısız olması durumunda FAILED durumu üretilmesi."""
        gatekeeper = ReleaseFourBaselineWalkForwardGatekeeper()

        mock_failure_item = BaselineWalkForwardCheckItem(
            check_id="REL4-BWF-01-NAIVE-BASELINES",
            name="Naif Kıyas Modelleri Paketi",
            passed=False,
            details="Simüle edilmiş model arızası",
        )

        with patch.object(
            gatekeeper,
            "_check_naive_baselines_suite",
            return_value=mock_failure_item,
        ):
            result = gatekeeper.verify_all_baseline_walk_forward_criteria()

            assert result.status == ReleaseFourBaselineGateStatus.FAILED
            assert result.is_passed is False
            assert result.failed_checks >= 1
            assert RELEASE_FOUR_BASELINE_WF_STATUS.get() == 0.0

            report = generate_markdown_report(result)
            assert "❌ BAŞARISIZ (FAIL)" in report
