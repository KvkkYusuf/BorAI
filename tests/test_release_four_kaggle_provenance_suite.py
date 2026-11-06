"""Sürüm 4 (PyTorch Araştırma Hattı) - Kaggle Veri Kaynağı ve Lisansı Kabul Kapısı Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 4 — PyTorch araştırma hattı:
  - Kaggle veri kaynağı/lisansı belgeli.
"""

from __future__ import annotations

from pathlib import Path

from stock_analysis.pipeline.kaggle_verifier import (
    CorporateActionAdjustment,
    DatasetLicenseType,
    DatasetUsageScope,
)
from stock_analysis.release.release_four_kaggle_provenance import (
    KaggleProvenanceCheckItem,
    KaggleProvenanceVerificationResult,
    ReleaseFourKaggleGateStatus,
    ReleaseFourKaggleProvenanceGatekeeper,
    generate_markdown_report,
)


class TestReleaseFourKaggleProvenanceSuite:
    """Sürüm 4 Kaggle veri kaynağı ve lisans kabul kriterleri testleri."""

    def test_canonical_dataset_card_structure(self) -> None:
        """Kanonik veri kartının yapısı ve özniteliklerinin doğrulanması."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        card = gatekeeper.get_canonical_dataset_card()

        assert card.dataset_id == "bist-daily-stock-historical-data"
        assert card.license_type == DatasetLicenseType.CC_BY_4_0
        assert card.currency == "TRY"
        assert card.base_timezone == "Europe/Istanbul"
        assert card.corporate_action_handling == CorporateActionAdjustment.DUAL_STREAM
        assert card.includes_delisted_symbols is True
        assert card.delisted_symbols_count == 64
        assert len(card.provenance_chain) >= 4
        assert card.usage_scope == DatasetUsageScope.PRODUCTION_ALLOWED

    def test_check_dataset_card_documentation(self) -> None:
        """Kaggle Veri Kartı dosyasının (DATASET_CARD_KAGGLE.md) varlığı ve içeriği."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        item: KaggleProvenanceCheckItem = gatekeeper._check_dataset_card_documentation()

        assert item.check_id == "REL4-KAGGLE-01-DOC-AND-CARD"
        assert item.passed is True
        assert "DATASET_CARD_KAGGLE.md mevcut ve onaylı" in item.details

    def test_check_license_compliance_policy(self) -> None:
        """Lisans uygunluğu ve v2 kuralı (belirsiz lisansların yerel deneye kısıtlanması)."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        item: KaggleProvenanceCheckItem = gatekeeper._check_license_compliance_policy()

        assert item.check_id == "REL4-KAGGLE-02-LICENSE-COMPLIANCE"
        assert item.passed is True
        assert "PRODUCTION_ALLOWED" in item.details
        assert "LOCAL_EXPERIMENT_ONLY" in item.details

    def test_check_provenance_chain_integrity(self) -> None:
        """Kaynak zincirinin resmi birincil kaynakları (KAP / Borsa İstanbul) içermesi."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        item: KaggleProvenanceCheckItem = gatekeeper._check_provenance_chain_integrity()

        assert item.check_id == "REL4-KAGGLE-03-PROVENANCE-TRAIL"
        assert item.passed is True
        assert item.metadata["depth"] >= 4
        assert "Kaynak zinciri 4 katmanlı olarak doğrulandı" in item.details

    def test_check_currency_and_timezone_standards(self) -> None:
        """Pazar, para birimi (TRY) ve saat dilimi (Europe/Istanbul) standardizasyonu."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        item: KaggleProvenanceCheckItem = gatekeeper._check_currency_and_timezone_standards()

        assert item.check_id == "REL4-KAGGLE-04-CURRENCY-TIMEZONE"
        assert item.passed is True
        assert item.metadata["currency"] == "TRY"
        assert item.metadata["timezone"] == "Europe/Istanbul"

    def test_check_corporate_actions_and_bias(self) -> None:
        """DUAL_STREAM yöntemi, delist hisseler ve hayatta kalma yanlılığı risk notu."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        item: KaggleProvenanceCheckItem = gatekeeper._check_corporate_actions_and_bias()

        assert item.check_id == "REL4-KAGGLE-05-CORPORATE-ACTION-AND-BIAS"
        assert item.passed is True
        assert item.metadata["corporate_action"] == "dual_stream"
        assert item.metadata["delisted_count"] == 64

    def test_check_raw_data_git_exclusion(self) -> None:
        """Ham veri dosyalarının (.csv, .parquet vb.) .gitignore ile korunması."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        item: KaggleProvenanceCheckItem = gatekeeper._check_raw_data_git_exclusion()

        assert item.check_id == "REL4-KAGGLE-06-RAW-DATA-GIT-EXCLUSION"
        assert item.passed is True
        assert item.metadata["gitignore_verified"] is True

    def test_run_all_checks_overall_pass(self) -> None:
        """Tüm kontrollerin toplu çalıştırılması ve PASS durumunun teyidi."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        result: KaggleProvenanceVerificationResult = gatekeeper.run_all_checks()

        assert result.status == ReleaseFourKaggleGateStatus.PASS
        assert result.is_passed is True
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert result.dataset_report is not None
        assert result.dataset_report.is_production_ready is True

    def test_markdown_report_generation(self, tmp_path: Path) -> None:
        """Markdown raporu üretimi ve dosya yazma kontrolü."""
        gatekeeper = ReleaseFourKaggleProvenanceGatekeeper()
        result = gatekeeper.run_all_checks()
        markdown = generate_markdown_report(result)

        assert "# Sürüm 4 — Kaggle Veri Kaynağı ve Lisansı Kabul Raporu" in markdown
        assert "GEÇTİ (PASS)" in markdown
        assert "REL4-KAGGLE-01-DOC-AND-CARD" in markdown
        assert "REL4-KAGGLE-06-RAW-DATA-GIT-EXCLUSION" in markdown
        assert "bist-daily-stock-historical-data" in markdown

        report_file = tmp_path / "RELEASE_FOUR_KAGGLE_REPORT.md"
        report_file.write_text(markdown, encoding="utf-8")
        assert report_file.exists()
        assert len(report_file.read_text(encoding="utf-8")) > 500
