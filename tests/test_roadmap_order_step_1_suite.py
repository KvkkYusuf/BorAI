"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 1 (Kapsam ve hukuk/veri lisansı) test paketi.

Test kapsamı:
- RoadmapStepOneGatekeeper 6/6 tam uyum doğrulaması.
- BIST 30/100 EOD piyasa sınırları ve portföy takibi yasağı.
- SPK yetkisiz danışmanlık kırmızı çizgileri ve yasaklı terim dil kalkanı.
- Lisanslı veri adaptörü ve izinsiz web kazıma (scraping) engeli.
- KVKK tek tıkla doğrudan kalıcı silme ve veri taşınabilirliği (export).
- Telemetri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP1_DISCLAIMER,
    ROADMAP_STEP1_CHECKS_TOTAL,
    ROADMAP_STEP1_PASSED_TOTAL,
    ROADMAP_STEP1_STATUS,
    RoadmapStepOneGatekeeper,
    RoadmapStepOneStatus,
    generate_roadmap_step1_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    # tests/.. -> workspace_root
    return Path(__file__).resolve().parent.parent


def test_roadmap_step1_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 1 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepOneStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    # Tüm kontrollerin geçtiğini teyit et
    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step1_scope_check_details(workspace_root: Path) -> None:
    """Pazar kapsamı, BIST 30/100 ve portföy yasağı detaylarını denetler."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    scope_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S1-01-SCOPE-AND-MARKET-BOUNDARY"
    )
    assert scope_check.passed is True
    assert scope_check.details.get("has_bist") is True
    assert scope_check.details.get("has_eod") is True
    assert scope_check.details.get("has_web") is True
    assert scope_check.details.get("has_no_portfolio") is True


def test_roadmap_step1_acceptance_criteria_baseline(workspace_root: Path) -> None:
    """Kabul ölçütleri, 18:40 TSI EOD ve SLA sınırlarını doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    ac_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S1-02-ACCEPTANCE-CRITERIA-BASELINE"
    )
    assert ac_check.passed is True
    assert ac_check.details.get("has_eod_time") is True
    assert ac_check.details.get("has_latency") is True
    assert ac_check.details.get("has_lcp") is True


def test_roadmap_step1_spk_compliance_framework(workspace_root: Path) -> None:
    """SPK kırmızı çizgileri ve hukuki kapı listesini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    spk_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S1-03-LEGAL-FRAMEWORK-COMPLIANCE-TR"
    )
    assert spk_check.passed is True
    assert spk_check.details.get("has_spk") is True
    assert spk_check.details.get("has_no_advice") is True
    assert spk_check.details.get("has_gate_list") is True


def test_roadmap_step1_scraping_ban_and_licensing(workspace_root: Path) -> None:
    """Scraping yasağı ve lisanslı veri adaptörü korumasını doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    license_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S1-04-DATA-LICENSING-SCRAPING-BAN"
    )
    assert license_check.passed is True
    assert license_check.details.get("has_scraping_ban") is True
    assert license_check.details.get("has_kaggle_card") is True
    assert license_check.details.get("guard_exists") is True


def test_roadmap_step1_mandatory_disclaimer_and_guardrail(workspace_root: Path) -> None:
    """SPK yasal uyarısı ve yasaklı terim dil kalkanı işlevini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    disc_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S1-05-MANDATORY-DISCLAIMER-ENFORCEMENT"
    )
    assert disc_check.passed is True
    assert disc_check.details.get("has_spk_text") is True
    assert disc_check.details.get("guardrail_working") is True

    # Yasaklı terimlerin engellendiğini ayrıca doğrula
    with pytest.raises(ValueError):
        validate_compliant_text("Bu hisseyi kesinlikle satın al, 500 TL hedef fiyat!")


def test_roadmap_step1_kvkk_rights_and_minimization(workspace_root: Path) -> None:
    """KVKK m. 11/7 veri hakları ve doğrudan silme servisini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    kvkk_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S1-06-KVKK-DATA-MINIMIZATION-RIGHTS"
    )
    assert kvkk_check.passed is True
    assert kvkk_check.details.get("has_kvkk") is True
    assert kvkk_check.details.get("has_hard_delete") is True
    assert kvkk_check.details.get("has_export") is True
    assert kvkk_check.details.get("has_code_service") is True


def test_roadmap_step1_markdown_report_generation(workspace_root: Path) -> None:
    """Markdown denetim raporunun eksiksiz ve geçerli üretildiğini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    report_md = generate_roadmap_step1_report(result)

    assert "# FAZ-18 Yol Haritası Uygulama Sırası — Adım 1" in report_md
    assert "PASS" in report_md
    assert "ROADMAP-S1-01-SCOPE-AND-MARKET-BOUNDARY" in report_md
    assert "ROADMAP-S1-06-KVKK-DATA-MINIMIZATION-RIGHTS" in report_md
    assert DEFAULT_ROADMAP_STEP1_DISCLAIMER in report_md


def test_roadmap_step1_failure_on_empty_workspace(tmp_path: Path) -> None:
    """Boş bir dizinde çalıştırıldığında kapının FAIL verdiğini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(
        workspace_root=tmp_path,
        disclaimer=DEFAULT_ROADMAP_STEP1_DISCLAIMER,
    )
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepOneStatus.FAIL
    assert result.passed_checks == 0
    assert result.failed_checks == 6
    assert result.to_dict()["status"] == "FAIL"


def test_roadmap_step1_telemetry_metrics(workspace_root: Path) -> None:
    """Prometheus telemetri sayaç ve göstergelerinin güncellendiğini doğrular."""
    gatekeeper = RoadmapStepOneGatekeeper(workspace_root=workspace_root)
    initial_checks = ROADMAP_STEP1_CHECKS_TOTAL.get()
    initial_passed = ROADMAP_STEP1_PASSED_TOTAL.get()

    result = gatekeeper.evaluate()

    assert ROADMAP_STEP1_STATUS.get() == 1.0
    assert ROADMAP_STEP1_CHECKS_TOTAL.get() == initial_checks + result.total_checks
    assert ROADMAP_STEP1_PASSED_TOTAL.get() == initial_passed + result.passed_checks
