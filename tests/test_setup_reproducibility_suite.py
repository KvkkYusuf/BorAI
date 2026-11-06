"""Tests for Sürüm 0 Local Setup Reproducibility Gatekeeper."""

from __future__ import annotations

import tempfile
from pathlib import Path

from stock_analysis.release.setup_reproducibility import (
    SETUP_REPRODUCIBILITY_CHECKS_TOTAL,
    SETUP_REPRODUCIBILITY_PASSED_TOTAL,
    SETUP_REPRODUCIBILITY_STATUS,
    SetupReproducibilityGatekeeper,
    SetupReproducibilityStatus,
)


def test_setup_reproducibility_gatekeeper_real_workspace() -> None:
    """Mevcut çalışma alanında tüm 6 kurulum tekrarlanabilirlik kontrolü PASS vermelidir."""
    gatekeeper = SetupReproducibilityGatekeeper()
    report = gatekeeper.evaluate()

    assert [(r.check_id, r.passed, r.evidence) for r in report.results if not r.passed] == []
    assert report.status == SetupReproducibilityStatus.PASS
    assert report.total_checks == 6
    assert report.passed_checks == 6
    assert report.failed_checks == 0
    assert len(report.results) == 6

    # Markdown raporu içeriği
    md = report.generate_markdown()
    assert "# Sürüm 0: Yerel Kurulum Tekrarlanabilirlik Denetim Raporu" in md
    assert "CHECK-REPRO-01" in md
    assert "CHECK-REPRO-06" in md
    assert "**PASS**" in md

    # Sözlük yapısı
    d = report.to_dict()
    assert d["status"] == "PASS"
    assert d["total_checks"] == 6

    # Metrikler
    assert SETUP_REPRODUCIBILITY_STATUS.get() == 1.0
    assert SETUP_REPRODUCIBILITY_CHECKS_TOTAL.get() >= 6
    assert SETUP_REPRODUCIBILITY_PASSED_TOTAL.get() >= 6


def test_setup_reproducibility_missing_files_in_temp_dir() -> None:
    """Dosyaları eksik geçici bir dizinde kontroller FAIL vermeli ve eksikleri bildirmelidir."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_root = Path(tmp_dir)
        gatekeeper = SetupReproducibilityGatekeeper(workspace_root=temp_root)
        report = gatekeeper.evaluate()

        assert report.status == SetupReproducibilityStatus.FAIL
        assert report.failed_checks > 0

        res_map = {r.check_id: r for r in report.results}
        assert not res_map["CHECK-REPRO-01"].passed
        assert not res_map["CHECK-REPRO-02"].passed
        assert not res_map["CHECK-REPRO-03"].passed
        assert not res_map["CHECK-REPRO-04"].passed
        assert not res_map["CHECK-REPRO-05"].passed
        assert not res_map["CHECK-REPRO-06"].passed


def test_setup_reproducibility_detects_secret_leak_in_env_example() -> None:
    """Gerçek sır içeren .env.example tespit edilip engellenmelidir."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_root = Path(tmp_dir)
        # Sahte .env.example ve tehlikeli sır
        (temp_root / ".env.example").write_text(
            "JWT_SECRET_KEY=ghp_1234567890123456789012345678901234\nDATABASE_URL=sqlite:///./test.db\nAPP_NAME=BoraI\n",
            encoding="utf-8",
        )
        gatekeeper = SetupReproducibilityGatekeeper(workspace_root=temp_root)
        res = gatekeeper._check_env_template_safety()

        assert not res.passed
        assert "Güvenlik zafiyeti" in res.evidence
