"""Test suite for legal, SPK statutory disclaimers, and open-source license governance."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.security.legal_compliance import (
    BANNED_LEGAL_PHRASES,
    MANDATORY_DOCUMENT_KEYWORDS,
    DependencyLicenseAuditor,
    GateStatus,
    LegalDocType,
    LegalDocumentRegistry,
    LegalGatekeeper,
    LicenseClassification,
    LicenseStatus,
    PackageLicenseInfo,
)


@pytest.fixture
def client() -> TestClient:
    """Create FastAPI test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def registry() -> LegalDocumentRegistry:
    """Instantiate document registry."""
    return LegalDocumentRegistry()


@pytest.fixture
def auditor() -> DependencyLicenseAuditor:
    """Instantiate license auditor."""
    return DependencyLicenseAuditor()


@pytest.fixture
def gatekeeper(
    registry: LegalDocumentRegistry, auditor: DependencyLicenseAuditor
) -> LegalGatekeeper:
    """Instantiate gatekeeper with fresh registry and auditor."""
    return LegalGatekeeper(registry=registry, auditor=auditor)


def test_all_canonical_legal_documents_exist_and_load(registry: LegalDocumentRegistry) -> None:
    """Verify that all 5 canonical legal markdown documents exist, load, and have SHA-256 hashes."""
    docs = registry.list_documents()
    assert len(docs) == 5

    for doc in docs:
        assert doc.content, f"{doc.title} content is empty!"
        assert len(doc.checksum_sha256) == 64, f"Invalid SHA-256 checksum for {doc.title}"
        assert doc.is_approved is True, f"{doc.title} failed approval checks!"
        assert doc.version == "1.0.0"


def test_mandatory_clauses_in_each_legal_document(registry: LegalDocumentRegistry) -> None:
    """Confirm that every mandatory statutory clause is present in the respective document."""
    for doc_type, mandatory_phrases in MANDATORY_DOCUMENT_KEYWORDS.items():
        doc = registry.get_document(doc_type)
        assert doc is not None, f"Document {doc_type} not found in registry"
        for phrase in mandatory_phrases:
            assert (
                phrase in doc.mandatory_clauses_verified
            ), f"Mandatory phrase '{phrase}' missing from {doc.title}!"


def test_zero_banned_promotional_phrases(registry: LegalDocumentRegistry) -> None:
    """Assert that prohibited investment advice and promotional phrases are absent."""
    for doc in registry.list_documents():
        assert (
            len(doc.banned_phrases_found) == 0
        ), f"Found banned phrases in {doc.title}: {doc.banned_phrases_found}"
        for banned in BANNED_LEGAL_PHRASES:
            assert banned.lower() not in doc.content.lower()


def test_spk_disclaimer_snippet(registry: LegalDocumentRegistry) -> None:
    """Validate standard short SPK disclaimer content."""
    snippet = registry.get_spk_disclaimer_snippet()
    assert "yatırım danışmanlığı veya al-sat tavsiyesi değildir" in snippet
    assert "15 dakika gecikmeli" in snippet
    assert "Geçmiş performans" in snippet


def test_dependency_license_inventory(auditor: DependencyLicenseAuditor) -> None:
    """Verify that all required production packages are audited and approved."""
    pkgs = auditor.audit_all()
    assert len(pkgs) >= 15

    pkg_map = {p.name: p for p in pkgs}
    critical_packages = ["fastapi", "uvicorn", "pydantic", "sqlalchemy", "torch", "pandas", "numpy"]
    for pkg_name in critical_packages:
        assert pkg_name in pkg_map, f"Missing critical package: {pkg_name}"
        pkg = pkg_map[pkg_name]
        assert pkg.status == LicenseStatus.APPROVED
        assert pkg.classification == LicenseClassification.PERMISSIVE
        assert pkg.copyleft_risk is False


def test_copyleft_prohibition(auditor: DependencyLicenseAuditor) -> None:
    """Assert that copyleft/GPL licenses are detected and marked as prohibited."""
    assert auditor.check_license_string("GPL-3.0") == LicenseStatus.PROHIBITED
    assert auditor.check_license_string("AGPL-3.0") == LicenseStatus.PROHIBITED
    assert auditor.check_license_string("SSPL") == LicenseStatus.PROHIBITED
    assert auditor.check_license_string("CC-BY-NC-4.0") == LicenseStatus.PROHIBITED

    # Permissive should be approved
    assert auditor.check_license_string("MIT") == LicenseStatus.APPROVED
    assert auditor.check_license_string("Apache-2.0") == LicenseStatus.APPROVED
    assert auditor.check_license_string("BSD-3-Clause") == LicenseStatus.APPROVED


def test_kaggle_data_card_license_compliance(auditor: DependencyLicenseAuditor) -> None:
    """Verify that Kaggle dataset is listed with CC-BY-SA-4.0 and approved."""
    pkgs = auditor.audit_all()
    kaggle_entry = next((p for p in pkgs if p.name == "kaggle-bist-daily-dataset"), None)
    assert kaggle_entry is not None
    assert kaggle_entry.license_name == "CC-BY-SA-4.0"
    assert kaggle_entry.status == LicenseStatus.APPROVED
    assert "DATASET_CARD_KAGGLE.md" in kaggle_entry.notes


def test_legal_gatekeeper_evaluation_pass(gatekeeper: LegalGatekeeper) -> None:
    """Verify evaluate_gate returns PASS when all documents and dependencies comply."""
    report = gatekeeper.evaluate_gate()
    assert report.overall_gate_status == GateStatus.PASS
    assert report.approved_documents == 5
    assert report.total_documents == 5
    assert report.prohibited_licenses_count == 0
    assert report.spk_compliance_passed is True
    assert report.kvkk_compliance_passed is True
    assert report.disclaimer_coverage_passed is True
    assert len(report.issues_found) == 0


def test_legal_gatekeeper_catches_unapproved_document(gatekeeper: LegalGatekeeper) -> None:
    """Ensure gatekeeper reports FAIL when a document is marked unapproved."""
    # Temporarily mark a document as unapproved
    terms_doc = gatekeeper.registry.get_document(LegalDocType.TERMS)
    assert terms_doc is not None
    original_approval = terms_doc.is_approved
    try:
        terms_doc.is_approved = False
        terms_doc.mandatory_clauses_verified = []
        report = gatekeeper.evaluate_gate()
        assert report.overall_gate_status == GateStatus.FAIL
        assert len(report.issues_found) > 0
    finally:
        terms_doc.is_approved = original_approval


def test_legal_gatekeeper_catches_prohibited_license() -> None:
    """Ensure gatekeeper reports FAIL if a prohibited copyleft package is in inventory."""
    class BadAuditor(DependencyLicenseAuditor):
        def audit_all(self) -> list[PackageLicenseInfo]:
            base = super().audit_all()
            base.append(
                PackageLicenseInfo(
                    name="rogue-agpl-lib",
                    version_spec=">=1.0",
                    license_name="AGPL-3.0",
                    classification=LicenseClassification.COPYLEFT,
                    status=LicenseStatus.PROHIBITED,
                    copyleft_risk=True,
                )
            )
            return base

    gk = LegalGatekeeper(auditor=BadAuditor())
    report = gk.evaluate_gate()
    assert report.overall_gate_status == GateStatus.FAIL
    assert report.prohibited_licenses_count == 1
    assert any("rogue-agpl-lib" in issue for issue in report.issues_found)


def test_api_list_legal_documents(client: TestClient) -> None:
    """Test GET /api/v1/legal/documents returns 5 document summaries."""
    response = client.get("/api/v1/legal/documents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5

    doc_types = {d["doc_type"] for d in data}
    assert doc_types == {"terms", "privacy", "cookies", "spk_disclaimer", "licenses"}
    for item in data:
        assert item["is_approved"] is True
        assert len(item["checksum_sha256"]) == 64
        assert "content" not in item  # content omitted in summary list


def test_api_get_legal_document_detail(client: TestClient) -> None:
    """Test GET /api/v1/legal/documents/{doc_type} returns full content."""
    for doc_type in ["terms", "privacy", "cookies", "spk_disclaimer", "licenses"]:
        response = client.get(f"/api/v1/legal/documents/{doc_type}")
        assert response.status_code == 200
        data = response.json()
        assert data["doc_type"] == doc_type
        assert len(data["content"]) > 100
        assert data["is_approved"] is True


def test_api_get_legal_document_invalid(client: TestClient) -> None:
    """Test GET /api/v1/legal/documents/unknown returns HTTP 404."""
    response = client.get("/api/v1/legal/documents/non_existent_doc")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_api_list_licenses(client: TestClient) -> None:
    """Test GET /api/v1/legal/licenses returns approved third-party licenses."""
    response = client.get("/api/v1/legal/licenses")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 15
    for pkg in data:
        assert pkg["status"] == "APPROVED"
        assert pkg["copyleft_risk"] is False


def test_api_legal_gate_report(client: TestClient) -> None:
    """Test GET /api/v1/legal/gate returns PASS status and clean report."""
    response = client.get("/api/v1/legal/gate")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_gate_status"] == "PASS"
    assert data["approved_documents"] == 5
    assert data["prohibited_licenses_count"] == 0
    assert data["spk_compliance_passed"] is True
    assert data["kvkk_compliance_passed"] is True


def test_api_spk_disclaimer_endpoint(client: TestClient) -> None:
    """Test GET /api/v1/legal/disclaimer returns standard disclaimer payload."""
    response = client.get("/api/v1/legal/disclaimer")
    assert response.status_code == 200
    data = response.json()
    assert data["data_delay_minutes"] == 15
    assert data["personalization_allowed"] is False
    assert "yatırım danışmanlığı" in data["disclaimer"]


def test_prometheus_metrics_recorded(gatekeeper: LegalGatekeeper) -> None:
    """Verify that Prometheus metrics are updated properly after gate evaluation."""
    gatekeeper.evaluate_gate()
    metrics_text = REGISTRY.generate_prometheus_text()
    assert "legal_gate_status_gauge 1.0" in metrics_text
    assert "legal_documents_approved_total 5.0" in metrics_text
    assert "legal_compliance_audits_total" in metrics_text
