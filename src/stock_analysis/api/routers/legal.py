"""Legal, regulatory compliance, and open-source license endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from stock_analysis.api.schemas import (
    ComplianceGateResponse,
    LegalComplianceGateResponse,
    LegalCounselReviewResponse,
    LegalDocumentDetailResponse,
    LegalDocumentMetadataResponse,
    PackageLicenseResponse,
    SpkDisclaimerResponse,
)
from stock_analysis.security.compliance_gate import (
    COMPLIANCE_GATEKEEPER,
)
from stock_analysis.security.legal_compliance import (
    LEGAL_GATEKEEPER,
    LegalDocType,
)

router = APIRouter(prefix="/legal", tags=["Legal & Compliance"])


@router.get(
    "/documents",
    response_model=list[LegalDocumentMetadataResponse],
    summary="Yönetilen hukuki ve regülasyon metinlerinin listesi",
    description=(
        "SPK uyarısı, kullanıcı sözleşmesi, KVKK ve çerez politikalarının "
        "versiyon ve bütünlük özetini döndürür."
    ),
)
def list_legal_documents() -> list[dict[str, Any]]:
    """Return metadata summary for all managed legal documents."""
    docs = LEGAL_GATEKEEPER.registry.list_documents()
    return [doc.to_dict(include_content=False) for doc in docs]


@router.get(
    "/documents/{doc_type}",
    response_model=LegalDocumentDetailResponse,
    summary="Belirli bir hukuki metnin tam içeriği",
    description=(
        "Seçilen belge türünün (terms, privacy, cookies, spk_disclaimer, licenses) "
        "tam metnini ve doğrulama durumunu sunar."
    ),
)
def get_legal_document(doc_type: str) -> dict[str, Any]:
    """Return full content and integrity verification for a requested document."""
    try:
        typed_doc = LegalDocType(doc_type.lower())
    except ValueError:
        valid_types = [t.value for t in LegalDocType]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DOCUMENT_NOT_FOUND",
                "message": (
                    f"Geçersiz hukuki belge türü: '{doc_type}'. Geçerli türler: {valid_types}"
                ),
            },
        ) from None

    doc = LEGAL_GATEKEEPER.registry.get_document(typed_doc)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DOCUMENT_NOT_FOUND",
                "message": f"'{doc_type}' belgesi sistemde bulunamadı.",
            },
        )
    return doc.to_dict(include_content=True)


@router.get(
    "/licenses",
    response_model=list[PackageLicenseResponse],
    summary="Üçüncü taraf bağımlılık ve açık kaynak lisans dökümü",
    description=(
        "Kullanılan tüm Python paketlerinin ve veri setlerinin "
        "lisanslarını ve copyleft risk değerlendirmesini sunar."
    ),
)
def list_dependency_licenses() -> list[dict[str, Any]]:
    """Return the audited third-party license inventory."""
    packages = LEGAL_GATEKEEPER.auditor.audit_all()
    return [p.to_dict() for p in packages]


@router.get(
    "/gate",
    response_model=LegalComplianceGateResponse,
    summary="Dağıtım öncesi hukuki ve lisans kapı kontrol raporu",
    description=(
        "SPK uyarıları, KVKK asgari veri kontrolü ve copyleft yasaklı lisans "
        "denetiminin genel kapı sonucunu döndürür."
    ),
)
def get_legal_gate_report() -> dict[str, Any]:
    """Execute pre-distribution legal compliance gate audit."""
    report = LEGAL_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/disclaimer",
    response_model=SpkDisclaimerResponse,
    summary="Standart SPK yasal uyarı ve gecikme metni",
    description=(
        "Arayüz ve widget'larda gösterilmek üzere standart SPK çekince "
        "ve veri gecikmesi metnini sağlar."
    ),
)
def get_spk_disclaimer() -> SpkDisclaimerResponse:
    """Return the statutory SPK disclaimer snippet."""
    return SpkDisclaimerResponse(
        disclaimer=LEGAL_GATEKEEPER.registry.get_spk_disclaimer_snippet(),
        data_delay_minutes=15,
        personalization_allowed=False,
        source="Lisanslı Veri Sağlayıcı",
    )


@router.get(
    "/compliance/status",
    response_model=ComplianceGateResponse,
    summary="Türkiye mevzuatı ve COMPLIANCE_TR kapı kontrol raporu",
    description=(
        "SPK m. 6362, III-37.1 yatırım danışmanlığı yasağı, sıfır kişiselleştirme, "
        "KVKK veri minimizasyonu ve avukat inceleme durumunu doğrular."
    ),
)
def get_compliance_gate_status() -> dict[str, Any]:
    """Execute statutory compliance gate evaluation and return JSON report."""
    report = COMPLIANCE_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/compliance/counsel-review",
    response_model=LegalCounselReviewResponse,
    summary="Hukuk müşaviri resmi onay ve inceleme kaydı",
    description="Baro sicil kayıtlı avukatın ürün metinleri için verdiği resmi inceleme kararı.",
)
def get_counsel_review() -> dict[str, Any]:
    """Return active legal counsel review and sign-off record."""
    review = COMPLIANCE_GATEKEEPER.counsel_registry.get_latest_review()
    return review.to_dict()


@router.get(
    "/compliance/report",
    summary="Resmi mevzuat uyumluluk ve denetim raporu (Markdown)",
    description="SPK ve KVKK kapı kontrol maddelerinin ayrıntılı markdown denetim raporu.",
)
def get_compliance_markdown_report() -> dict[str, Any]:
    """Generate and return formal markdown audit report."""
    markdown_content = COMPLIANCE_GATEKEEPER.generate_audit_report()
    return {"markdown": markdown_content}
