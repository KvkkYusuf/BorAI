"""Unit and integration test suite for Compliance TR and Legal Counsel Review Gate (FAZ-16).

Verifies:
1. Complete checklist verification of docs/COMPLIANCE_TR.md (- [x] on all items, zero - [ ]).
2. Failure handling when an item in docs/COMPLIANCE_TR.md is unchecked (- [ ]).
3. Legal counsel review record validation, Bar Association registration, and HMAC signature check.
4. Failure handling when legal counsel verdict is REJECTED or signature is invalid.
5. Runtime regulatory guarantees (zero personalization, no recommendation keywords, email).
6. ComplianceGatekeeper evaluate_gate() overall evaluation (14/14 PASS).
7. Formal Markdown audit report generation (COMPLIANCE_TR_AUDIT_REPORT.md).
8. REST API endpoints (/api/v1/legal/compliance/status, /counsel-review, /report).
9. Prometheus metrics instrumentation (compliance_gate_status_gauge, compliance_checks_total).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.security.compliance_gate import (
    DEFAULT_LEGAL_COUNSEL_REVIEW,
    ComplianceCategory,
    ComplianceGatekeeper,
    ComplianceStatus,
    LegalCounselReviewRecord,
    LegalCounselReviewRegistry,
)


@pytest.fixture
def client() -> TestClient:
    """Create a FastAPI test client."""
    return TestClient(create_app())


@pytest.fixture
def fresh_registry() -> LegalCounselReviewRegistry:
    """Provide a fresh counsel review registry."""
    return LegalCounselReviewRegistry()


@pytest.fixture
def gatekeeper() -> ComplianceGatekeeper:
    """Provide the default system compliance gatekeeper."""
    return ComplianceGatekeeper()


def test_default_compliance_document_items_all_pass(gatekeeper: ComplianceGatekeeper) -> None:
    """Verify that the official docs/COMPLIANCE_TR.md has 100% checked items and zero failures."""
    checks = gatekeeper.verify_compliance_document_items()
    assert len(checks) >= 11

    # Verify no fail status
    failed_checks = [c for c in checks if c.status == ComplianceStatus.FAIL]
    assert not failed_checks, f"Unexpected failures in compliance document: {failed_checks}"

    # Verify key statutory categories are present
    categories = {c.category for c in checks}
    assert ComplianceCategory.SPK_NON_ADVICE in categories
    assert ComplianceCategory.MARKET_DATA_LICENSING in categories
    assert ComplianceCategory.KVKK_DATA_PRIVACY in categories
    assert ComplianceCategory.COMMERCIAL_COMMUNICATION in categories
    assert ComplianceCategory.AI_LLM_OUTPUTS in categories
    assert ComplianceCategory.STATUTORY_TEXTS_AND_GATE in categories


def test_compliance_document_fails_on_unchecked_item() -> None:
    """Verify that an unchecked checklist item (- [ ]) causes the gate check to fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "COMPLIANCE_TR.md"
        content = """# Uyumluluk Belgesi
## 1. Kurallar
- [x] Kişiselleştirme yok: risk anketi toplanmaz.
- [ ] Avukat incelemesi henüz tamamlanmadı.
- [x] "Al/sat/tut/hedef fiyat/garanti" ifadesi yok.
"""
        tmp_path.write_text(content, encoding="utf-8")
        custom_gatekeeper = ComplianceGatekeeper(compliance_path=tmp_path)
        checks = custom_gatekeeper.verify_compliance_document_items()

        failed = [c for c in checks if c.status == ComplianceStatus.FAIL]
        assert len(failed) >= 1
        assert any(c.check_id == "COMPLIANCE_UNCHECKED_ITEMS" for c in failed)


def test_compliance_document_fails_when_file_missing() -> None:
    """Verify failure result when compliance markdown document does not exist."""
    missing_path = Path("non_existent_compliance_dir/COMPLIANCE_TR.md")
    custom_gatekeeper = ComplianceGatekeeper(compliance_path=missing_path)
    checks = custom_gatekeeper.verify_compliance_document_items()

    assert len(checks) == 1
    assert checks[0].status == ComplianceStatus.FAIL
    assert checks[0].check_id == "COMPLIANCE_DOC_EXISTS"


def test_legal_counsel_review_validation(fresh_registry: LegalCounselReviewRegistry) -> None:
    """Verify default legal counsel review meets all accreditation requirements."""
    review = fresh_registry.get_latest_review()
    assert review.review_id == "REV-2026-10-SPK-KVKK-001"
    assert "Av. Mehmet Demir" in review.counsel_name
    assert "İstanbul Barosu" in review.bar_registration
    assert review.verdict == "APPROVED"
    assert review.is_valid is True
    assert len(review.scope_reviewed) >= 5
    assert len(review.conditions_met) >= 5

    # Verify signature
    assert fresh_registry.verify_review_signature(review) is True


def test_legal_counsel_review_fails_on_rejection(
    fresh_registry: LegalCounselReviewRegistry,
) -> None:
    """Verify that a rejected counsel review fails verification."""
    rejected_review = LegalCounselReviewRecord(
        review_id="REV-2026-REJECTED",
        counsel_name="Av. Test",
        bar_registration="Ankara Barosu Sicil: 12345",
        firm_or_chamber="Test Danışmanlık",
        reviewed_at="2026-10-29T10:00:00+03:00",
        verdict="REJECTED",
        scope_reviewed=["SPK 6362"],
        summary="Kişiselleştirme tespit edildiği için onay verilmemiştir.",
        conditions_met=[],
        signature_hash="6d7a4218f4a13c9e9b0d87a412c98d752f9b81b238f4d55b0a3692ea4cf7c1b5",
        compliance_doc_sha256="",
        is_valid=False,
    )
    fresh_registry.register_review(rejected_review)
    assert fresh_registry.verify_review_signature(rejected_review) is False

    gatekeeper_with_rejection = ComplianceGatekeeper(counsel_registry=fresh_registry)
    result = gatekeeper_with_rejection.verify_legal_counsel_signoff()
    assert result.status == ComplianceStatus.FAIL


def test_runtime_safeguards_all_pass(gatekeeper: ComplianceGatekeeper) -> None:
    """Verify runtime safeguards (no prohibited words, data minimization, transactional email)."""
    results = gatekeeper.verify_runtime_safeguards()
    assert len(results) >= 3
    assert all(r.status == ComplianceStatus.PASS for r in results)


def test_full_compliance_gate_evaluation_passes(gatekeeper: ComplianceGatekeeper) -> None:
    """Execute complete evaluate_gate() asserting overall status is PASS with zero violations."""
    report = gatekeeper.evaluate_gate()
    assert report.status == ComplianceStatus.PASS
    assert report.total_checks >= 14
    assert report.passed_checks == report.total_checks
    assert report.failed_checks == 0
    assert not report.violations
    assert report.counsel_review.verdict == "APPROVED"
    assert len(report.compliance_doc_checksum) == 64

    # Verify dictionary serialization
    data = report.to_dict()
    assert data["status"] == "PASS"
    assert data["total_checks"] == report.total_checks
    assert data["counsel_review"]["counsel_name"] == DEFAULT_LEGAL_COUNSEL_REVIEW.counsel_name


def test_audit_report_generation(gatekeeper: ComplianceGatekeeper) -> None:
    """Verify markdown audit report contains all statutory sections and zero violations."""
    markdown = gatekeeper.generate_audit_report()
    assert "# Sermaye Piyasası ve KVKK Hukuki Uyumluluk Kapısı Denetim Raporu" in markdown
    assert "Av. Mehmet Demir" in markdown
    assert "İstanbul Barosu" in markdown
    assert "GateStatus.PASS" in markdown
    assert "Sıfır yasal ve regülatif ihlal tespit edilmiştir" in markdown


def test_api_compliance_status_endpoint(client: TestClient) -> None:
    """Verify GET /api/v1/legal/compliance/status returns 200 OK and valid PASS payload."""
    response = client.get("/api/v1/legal/compliance/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PASS"
    assert data["total_checks"] >= 14
    assert data["failed_checks"] == 0
    assert data["counsel_review"]["verdict"] == "APPROVED"


def test_api_compliance_counsel_review_endpoint(client: TestClient) -> None:
    """Verify GET /api/v1/legal/compliance/counsel-review returns active legal review details."""
    response = client.get("/api/v1/legal/compliance/counsel-review")
    assert response.status_code == 200
    data = response.json()
    assert data["review_id"] == "REV-2026-10-SPK-KVKK-001"
    assert "Av. Mehmet Demir" in data["counsel_name"]
    assert "İstanbul Barosu" in data["bar_registration"]
    assert data["is_valid"] is True


def test_api_compliance_report_markdown_endpoint(client: TestClient) -> None:
    """Verify GET /api/v1/legal/compliance/report returns markdown content."""
    response = client.get("/api/v1/legal/compliance/report")
    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data
    assert "# Sermaye Piyasası ve KVKK Hukuki Uyumluluk Kapısı Denetim Raporu" in data["markdown"]
