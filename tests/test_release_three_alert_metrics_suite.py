"""Sürüm 3 Uyarıların Tekrarları, Gecikmesi ve Teslimatı Ölçüm Kabul Kapısı Test Paketi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 3 — Klasik analiz ve uyarı:
  - Uyarıların tekrarları, gecikmesi ve teslimatı ölçülüyor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_analysis.release.release_three_alert_metrics import (
    RELEASE_THREE_ALERT_METRICS_STATUS,
    AlertMetricCheckItem,
    AlertMetricsVerificationResult,
    ReleaseThreeAlertMetricsGateStatus,
    ReleaseThreeAlertMetricsGatekeeper,
    generate_markdown_report,
)


class TestReleaseThreeAlertMetricsSuite:
    """Sürüm 3 Uyarı Metrikleri Doğrulama Kabul Kapısı test paketi."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        now = datetime.now(UTC)

        result: AlertMetricsVerificationResult = (
            gatekeeper.verify_release_three_alert_metrics(now=now)
        )

        assert result.status == ReleaseThreeAlertMetricsGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_THREE_ALERT_METRICS_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary
        assert result.evaluated_at == now

    def test_repeat_and_cooldown_limiting_check(self) -> None:
        """Tekrarlı bildirim ve cooldown sınırlandırma ölçüm kontrolü."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        item: AlertMetricCheckItem = gatekeeper._check_repeat_and_cooldown_limiting()

        assert item.check_id == "REL3-ALERT-01-REPEAT-LIMITING"
        assert item.passed is True
        assert "İlk gönderim: allowed" in item.details
        assert "Cooldown tekrar engelleme: suppressed_cooldown" in item.details
        assert "Farklı sembol kabul: allowed" in item.details
        assert "Saatlik kota dolumu: allowed" in item.details
        assert "Saatlik limit engeli: suppressed_hourly_limit" in item.details

    def test_quiet_hours_and_deferral_check(self) -> None:
        """Sessiz saatler (quiet hours) erteleme ve iptal ölçümü kontrolü."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        item: AlertMetricCheckItem = gatekeeper._check_quiet_hours_and_deferral()

        assert item.check_id == "REL3-ALERT-02-QUIET-HOURS"
        assert item.passed is True
        assert "deferred_quiet_hours" in item.details
        assert "dropped_quiet_hours" in item.details

    def test_latency_measurement_and_freshness_check(self) -> None:
        """Uyarı gecikmesi ve sağlayıcı tazelik ölçümü kontrolü."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        item: AlertMetricCheckItem = gatekeeper._check_latency_measurement_and_freshness()

        assert item.check_id == "REL3-ALERT-03-LATENCY-MEASUREMENT"
        assert item.passed is True
        assert "REALTIME" in item.details
        assert "DELAYED" in item.details
        assert "STALE" in item.details
        assert "Payload gecikme etiketi: True" in item.details

    def test_delivery_queue_lifecycle_check(self) -> None:
        """Teslimat yaşam döngüsü ve kuyruk durumu geçişleri kontrolü."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        item: AlertMetricCheckItem = gatekeeper._check_delivery_queue_lifecycle()

        assert item.check_id == "REL3-ALERT-04-DELIVERY-LIFECYCLE"
        assert item.passed is True
        assert "İş durumu: completed" in item.details
        assert "Kuyrukta bekleyen: 0" in item.details

    def test_idempotency_duplicate_prevention_check(self) -> None:
        """Idempotency ve çift teslimat engelleme kontrolü."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        item: AlertMetricCheckItem = gatekeeper._check_idempotency_duplicate_prevention()

        assert item.check_id == "REL3-ALERT-05-IDEMPOTENCY-PREVENTION"
        assert item.passed is True
        assert "Birinci iş durumu: completed" in item.details
        assert "Mükerrer iş reddi: True" in item.details
        assert "Mükerrer düşen sayaç: 1" in item.details
        assert "Gerçekleşen teslimat adedi: 1" in item.details

    def test_retry_backoff_and_dead_letter_safety_check(self) -> None:
        """Yeniden deneme, backoff, dead-letter ve hassas veri koruması kontrolü."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        item: AlertMetricCheckItem = gatekeeper._check_retry_backoff_and_dead_letter_safety()

        assert item.check_id == "REL3-ALERT-06-RETRY-AND-SAFETY"
        assert item.passed is True
        assert "Maksimum deneme sonrası durum: dead_lettered" in item.details
        assert "Dead-letter nedeni: max_retries_exceeded" in item.details
        assert "Hassas metin sızıntı yakalama: True" in item.details
        assert "Hassas meta veri yakalama: True" in item.details

    def test_markdown_report_generation(self, tmp_path: Path) -> None:
        """Rapor oluşturma ve dosya yazma doğrulaması."""
        gatekeeper = ReleaseThreeAlertMetricsGatekeeper()
        result = gatekeeper.verify_release_three_alert_metrics()
        report_file = tmp_path / "RELEASE_3_ALERT_METRICS_REPORT.md"

        content = generate_markdown_report(result, report_path=report_file)

        assert report_file.exists()
        assert "# Sürüm 3 (Klasik Analiz ve Uyarı) — Uyarı Metrikleri" in content
        assert "REL3-ALERT-01-REPEAT-LIMITING" in content
        assert "REL3-ALERT-03-LATENCY-MEASUREMENT" in content
        assert "REL3-ALERT-04-DELIVERY-LIFECYCLE" in content
        assert "✅ PASS" in content
        assert "- [x] Uyarıların tekrarları, gecikmesi ve teslimatı ölçülüyor." in content
