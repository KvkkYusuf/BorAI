"""Test suite for Sürüm 0 (Proje İskeleti) doğrulama kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 0: Repo, Antigravity kuralları, CI ve dokümantasyon hazır.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from stock_analysis.release.release_zero_gatekeeper import (
    ReleaseZeroGatekeeper,
    ReleaseZeroGateStatus,
)


class TestReleaseZeroGateSuite:
    """Sürüm 0 gereksinimleri doğrulama testleri."""

    def test_live_workspace_passes_release_zero_gate(self) -> None:
        """Mevcut çalışma alanının Sürüm 0 kapısını %100 başarıyla geçtiğini doğrula."""
        gatekeeper = ReleaseZeroGatekeeper()
        report = gatekeeper.evaluate()

        assert report.status == ReleaseZeroGateStatus.PASS
        assert report.total_checks == 6
        assert report.passed_checks == 6
        assert report.failed_checks == 0

        # Kontrol ID'lerinin eksiksizliği
        check_ids = [r.check_id for r in report.results]
        assert "CHECK-R0-01" in check_ids
        assert "CHECK-R0-02" in check_ids
        assert "CHECK-R0-03" in check_ids
        assert "CHECK-R0-04" in check_ids
        assert "CHECK-R0-05" in check_ids
        assert "CHECK-R0-06" in check_ids

        # Raporlama fonksiyonları
        as_dict = report.to_dict()
        assert as_dict["status"] == "PASS"
        assert len(as_dict["results"]) == 6

        markdown = report.generate_markdown()
        assert "# Sürüm 0 (Proje İskeleti) Kabul ve Doğrulama Raporu" in markdown
        assert "CHECK-R0-01" in markdown
        assert "PASS" in markdown

    def test_simulated_missing_rules_fails_gate(self) -> None:
        """Kural eksikliği durumunda kapının FAIL vermesi."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Yalnızca boş dizin
            gatekeeper = ReleaseZeroGatekeeper(workspace_root=tmp_path)
            report = gatekeeper.evaluate()

            assert report.status == ReleaseZeroGateStatus.FAIL
            assert report.failed_checks > 0
            failed_ids = [r.check_id for r in report.results if not r.passed]
            assert "CHECK-R0-02" in failed_ids

    def test_simulated_missing_ci_fails_gate(self) -> None:
        """CI eksikliğinde kapının FAIL vermesi."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            gatekeeper = ReleaseZeroGatekeeper(workspace_root=tmp_path)
            res = gatekeeper._check_ci_pipeline()
            assert res.passed is False
            assert "bulunamadı" in res.evidence
