"""Sürüm 4 (PyTorch Araştırma Hattı) - Zaman Bazlı Split ve Sızıntı Kabul Kapısı Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 4 — PyTorch araştırma hattı:
  - Zaman bazlı split ve sızıntı testleri var.
"""

from __future__ import annotations

from unittest.mock import patch

from stock_analysis.release.release_four_time_split_leakage import (
    RELEASE_FOUR_TIME_SPLIT_STATUS,
    ReleaseFourTimeSplitGateStatus,
    ReleaseFourTimeSplitLeakageGatekeeper,
    TimeSplitLeakageCheckItem,
    TimeSplitLeakageVerificationResult,
    generate_markdown_report,
)


class TestReleaseFourTimeSplitLeakageSuite:
    """Sürüm 4 zaman bazlı split ve sızıntı kabul kriterleri testleri."""

    def test_check_chronological_order_and_shuffle_prevention(self) -> None:
        """Kronolojik sıralama ve rastgele satır karıştırma engeli kontrolü."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        item: TimeSplitLeakageCheckItem = (
            gatekeeper._check_chronological_order_and_shuffle_prevention()
        )

        assert item.check_id == "REL4-SPLIT-01-CHRONOLOGICAL-ORDER"
        assert item.passed is True
        assert "Kronolojik zaman sıralılığı güvenceye alındı" in item.details
        assert item.metadata.get("shuffle_protection_active") is True

    def test_check_partition_boundary_purity(self) -> None:
        """Kesin bölüm sınırları ve çakışma olmaması (Train < Val < Test) kontrolü."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        item: TimeSplitLeakageCheckItem = gatekeeper._check_partition_boundary_purity()

        assert item.check_id == "REL4-SPLIT-02-PARTITION-PURITY"
        assert item.passed is True
        assert "Train < Val < Test" in item.details
        assert item.metadata.get("strict_temporal_purity") is True

    def test_check_embargo_purge_isolation(self) -> None:
        """Embargo ve arındırma penceresi (purge gap window) kontrolü."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        item: TimeSplitLeakageCheckItem = gatekeeper._check_embargo_purge_isolation()

        assert item.check_id == "REL4-SPLIT-03-EMBARGO-ISOLATION"
        assert item.passed is True
        assert "embargo_days" in item.details
        assert item.metadata.get("embargo_verification") == "ACTIVE"

    def test_check_lookahead_bias_and_window_safety(self) -> None:
        """Gelecek bilgisi sızıntısı (lookahead bias) ve pencere güvenliği kontrolü."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        item: TimeSplitLeakageCheckItem = (
            gatekeeper._check_lookahead_bias_and_window_safety()
        )

        assert item.check_id == "REL4-SPLIT-04-LOOKAHEAD-BIAS-SAFETY"
        assert item.passed is True
        assert "center=True" in item.details
        assert "forward shift" in item.details
        assert item.metadata.get("lookahead_detector_status") == "ACTIVE"

    def test_check_target_leakage_detection(self) -> None:
        """Hedef değişken sızıntısı ve aşırı korelasyon denetimi kontrolü."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        item: TimeSplitLeakageCheckItem = gatekeeper._check_target_leakage_detection()

        assert item.check_id == "REL4-SPLIT-05-TARGET-LEAKAGE"
        assert item.passed is True
        assert "|r| >= 0.999" in item.details
        assert item.metadata.get("target_leakage_detector_status") == "ACTIVE"

    def test_check_preprocessing_isolation_and_registry(self) -> None:
        """Ön işleme parametre izolasyonu ve bölünme kayıt defteri kontrolü."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        item: TimeSplitLeakageCheckItem = (
            gatekeeper._check_preprocessing_isolation_and_registry()
        )

        assert item.check_id == "REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY"
        assert item.passed is True
        assert item.metadata.get("scaler_isolation") is True
        assert item.metadata.get("split_registry_verified") is True
        assert len(item.metadata.get("dataset_sha256", "")) == 16

    def test_verify_all_split_leakage_criteria_full_pass(self) -> None:
        """Tüm kontrollerin eksiksiz geçmesi ve Prometheus telemetri güncellemesi."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        res: TimeSplitLeakageVerificationResult = (
            gatekeeper.verify_all_split_leakage_criteria()
        )

        assert res.is_passed is True
        assert res.status == ReleaseFourTimeSplitGateStatus.PASSED
        assert res.total_checks == 6
        assert res.passed_checks == 6
        assert res.failed_checks == 0
        assert len(res.items) == 6

        # Prometheus gauge kontrolü
        assert RELEASE_FOUR_TIME_SPLIT_STATUS.get() == 1.0

    def test_gatekeeper_handles_failure_state(self) -> None:
        """Herhangi bir kontrol başarısız olduğunda kapının FAILED durumuna geçmesi."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()

        failing_item = TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-01-CHRONOLOGICAL-ORDER",
            name="Kronolojik Sıralama ve Rastgele Karıştırma Engeli",
            passed=False,
            details="Test amaçlı zorlanmış sızıntı hatası.",
        )

        with patch.object(
            gatekeeper,
            "_check_chronological_order_and_shuffle_prevention",
            return_value=failing_item,
        ):
            res = gatekeeper.verify_all_split_leakage_criteria()

            assert res.is_passed is False
            assert res.status == ReleaseFourTimeSplitGateStatus.FAILED
            assert res.failed_checks >= 1
            assert RELEASE_FOUR_TIME_SPLIT_STATUS.get() == 0.0

    def test_generate_markdown_report_formatting(self) -> None:
        """Markdown rapor oluşturucunun biçimi ve içeriği."""
        gatekeeper = ReleaseFourTimeSplitLeakageGatekeeper()
        res = gatekeeper.verify_all_split_leakage_criteria()
        report = generate_markdown_report(res)

        assert "# Sürüm 4 — Zaman Bazlı Split ve Sızıntı Kabul Raporu" in report
        assert "✅ GEÇTİ (PASS)" in report
        assert "REL4-SPLIT-01-CHRONOLOGICAL-ORDER" in report
        assert "REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY" in report
        assert "Train < Validation < Test" in report
        assert "SplitRegistry" in report
