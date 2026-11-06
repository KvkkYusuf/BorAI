"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 6 test paketi.

E-posta girişi ve kullanıcı ayarları doğrulama kabul paketi.
Test kapsamı:
- RoadmapStepSixGatekeeper 6/6 tam uyum doğrulaması.
- E-posta kimlik doğrulama ve HMAC token tüketimi (EmailVerificationManager, /auth/verify-email).
- Güvenli oturum çerezleri ve CSRF savunması (SessionSecurityPolicy, HttpOnly, Secure, SameSite).
- Kaba kuvvet savunması ve scrypt parola güvenliği (BruteForceDefender, PasswordResetManager).
- Kullanıcı ayarları (/user/preferences GET/PUT, UserPreference ORM, bildirim, saat dilimi, tema).
- Çok kiracılı veri izolasyonu ve IDOR kalkanı (current_user.id, /user/sessions, logout).
- KVKK m. 11/7 ve SPK mevzuat uyumluluğu (/user/export, /user/account, sıfır portföy kolonu).
- Zorunlu SPK yasal uyarı şablonu ve yasaklı terim denetimi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP6_DISCLAIMER,
    ROADMAP_STEP6_CHECKS_TOTAL,
    ROADMAP_STEP6_PASSED_TOTAL,
    ROADMAP_STEP6_STATUS,
    RoadmapStepSixGatekeeper,
    RoadmapStepSixStatus,
    generate_roadmap_step6_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


def test_roadmap_step6_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 6 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepSixStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step6_email_auth_token_verification(workspace_root: Path) -> None:
    """E-posta kimlik doğrulama ve tek kullanımlık token kontrollerini denetler."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S6-01-EMAIL-AUTH-AND-TOKEN-VERIFICATION"
    )
    assert check.passed is True
    assert "EmailVerificationManager" in check.evidence
    assert check.details["has_verif_file"] is True
    assert check.details["has_manager"] is True
    assert check.details["has_token_hash"] is True
    assert check.details["has_token_consumption"] is True
    assert check.details["has_mask_email"] is True
    assert check.details["has_verify_endpoint"] is True


def test_roadmap_step6_secure_session_cookies_csrf(workspace_root: Path) -> None:
    """Güvenli oturum çerezleri, SameSite ve CSRF savunmasını denetler."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S6-02-SECURE-SESSION-COOKIES-AND-CSRF"
    )
    assert check.passed is True
    assert "SessionSecurityPolicy" in check.evidence
    assert check.details["has_session_file"] is True
    assert check.details["has_policy"] is True
    assert check.details["has_httponly"] is True
    assert check.details["has_secure"] is True
    assert check.details["has_samesite"] is True
    assert check.details["has_csrf"] is True
    assert check.details["has_compare_digest"] is True
    assert check.details["has_csrf_route"] is True


def test_roadmap_step6_brute_force_password_security(workspace_root: Path) -> None:
    """Kaba kuvvet koruması ve parola güvenliği kontrollerini denetler."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S6-03-BRUTE-FORCE-AND-PASSWORD-SECURITY"
    )
    assert check.passed is True
    assert "BruteForceDefender" in check.evidence
    assert check.details["has_brute"] is True
    assert check.details["has_defender"] is True
    assert check.details["has_lockout"] is True
    assert check.details["has_hasher"] is True
    assert check.details["has_scrypt"] is True
    assert check.details["has_policy"] is True
    assert check.details["has_reset"] is True
    assert check.details["has_reset_mgr"] is True


def test_roadmap_step6_user_settings_preferences(workspace_root: Path) -> None:
    """Kullanıcı tercihleri ve hesap ayarları kontrollerini denetler."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S6-04-USER-SETTINGS-AND-PREFERENCES"
    )
    assert check.passed is True
    assert "UserPreference" in check.evidence
    assert check.details["has_user_router"] is True
    assert check.details["has_pref_get"] is True
    assert check.details["has_pref_put"] is True
    assert check.details["has_pref_resp"] is True
    assert check.details["has_pref_req"] is True
    assert check.details["has_user_pref_model"] is True
    assert check.details["has_tz"] is True
    assert check.details["has_theme"] is True
    assert check.details["has_lang"] is True
    assert check.details["has_freq"] is True


def test_roadmap_step6_multi_tenant_isolation_idor(workspace_root: Path) -> None:
    """Çok kiracılı veri izolasyonu ve IDOR koruması kontrollerini denetler."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S6-05-MULTI-TENANT-ISOLATION-AND-IDOR"
    )
    assert check.passed is True
    assert "Multi-tenancy" in check.evidence
    assert check.details["has_user_router"] is True
    assert check.details["has_current_user_dep"] is True
    assert check.details["has_user_id_filtering"] is True
    assert check.details["has_sessions_list"] is True
    assert check.details["has_sessions_other_delete"] is True
    assert check.details["has_logout"] is True
    assert check.details["has_revoke_all"] is True


def test_roadmap_step6_regulatory_compliance_kvkk_spk(workspace_root: Path) -> None:
    """KVKK veri hakları ve SPK portföy yasağı kontrollerini denetler."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S6-06-REGULATORY-COMPLIANCE-KVKK-AND-SPK"
    )
    assert check.passed is True
    assert "KVKK m. 11" in check.evidence
    assert check.details["has_export"] is True
    assert check.details["has_delete_account"] is True
    assert check.details["has_tombstone"] is True
    assert check.details["no_forbidden_columns"] is True
    assert check.details["has_valid_disclaimer"] is True


def test_roadmap_step6_disclaimer_and_legal_guardrails() -> None:
    """Zorunlu SPK yasal uyarısının doğruluğunu ve yasaklı terim içermediğini denetler."""
    assert "SPK Mevzuatı Uyarınca" in DEFAULT_ROADMAP_STEP6_DISCLAIMER
    assert "yatırım danışmanlığı" in DEFAULT_ROADMAP_STEP6_DISCLAIMER
    validate_compliant_text(DEFAULT_ROADMAP_STEP6_DISCLAIMER)


def test_roadmap_step6_report_generation(workspace_root: Path) -> None:
    """Adım 6 Markdown denetim raporunun eksiksiz üretildiğini doğrular."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    report = generate_roadmap_step6_report(result)

    assert "# Yol Haritası Uygulama Sırası — Adım 6 Denetim Raporu" in report
    assert "ROADMAP-S6-01-EMAIL-AUTH-AND-TOKEN-VERIFICATION" in report
    assert "ROADMAP-S6-02-SECURE-SESSION-COOKIES-AND-CSRF" in report
    assert "ROADMAP-S6-03-BRUTE-FORCE-AND-PASSWORD-SECURITY" in report
    assert "ROADMAP-S6-04-USER-SETTINGS-AND-PREFERENCES" in report
    assert "ROADMAP-S6-05-MULTI-TENANT-ISOLATION-AND-IDOR" in report
    assert "ROADMAP-S6-06-REGULATORY-COMPLIANCE-KVKK-AND-SPK" in report
    assert "✅ PASS" in report
    assert DEFAULT_ROADMAP_STEP6_DISCLAIMER in report


def test_roadmap_step6_missing_workspace_failure(tmp_path: Path) -> None:
    """Boş geçici dizinde Adım 6 değerlendirmesinin FAIL döndürdüğünü doğrular."""
    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=tmp_path)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepSixStatus.FAIL
    assert result.failed_checks > 0
    assert result.passed_checks == 0


def test_roadmap_step6_prometheus_telemetry(workspace_root: Path) -> None:
    """Prometheus sayaç ve durum metriklerinin güncellendiğini doğrular."""
    before_passed = ROADMAP_STEP6_PASSED_TOTAL.get()
    before_checks = ROADMAP_STEP6_CHECKS_TOTAL.get()

    gatekeeper = RoadmapStepSixGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepSixStatus.PASS
    assert ROADMAP_STEP6_STATUS.get() == 1.0
    assert ROADMAP_STEP6_PASSED_TOTAL.get() >= before_passed + result.passed_checks
    assert ROADMAP_STEP6_CHECKS_TOTAL.get() >= before_checks + result.total_checks

