"""Sürüm 2 E-Posta Doğrulama ve Güvenli Oturum Doğrulama Kapısı Test Paketi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 2 — Hesap ve kişiselleştirme:
  - E-posta doğrulama ve güvenli oturum çalışıyor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.auth.email_verification import (
    EmailVerificationError,
    EmailVerificationManager,
)
from stock_analysis.auth.session import (
    SessionSecurityPolicy,
)
from stock_analysis.db.base import Base
from stock_analysis.db.models import User
from stock_analysis.release.release_two_auth_session import (
    RELEASE_TWO_AUTH_SESSION_STATUS,
    AuthSessionVerificationResult,
    ReleaseTwoASGateStatus,
    ReleaseTwoAuthSessionGatekeeper,
)


class TestReleaseTwoAuthSessionSuite:
    """Sürüm 2 E-Posta Doğrulama ve Güvenli Oturum Kapısı testleri."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseTwoAuthSessionGatekeeper()
        now = datetime.now(UTC)

        result: AuthSessionVerificationResult = gatekeeper.verify_release_two_auth_session(now=now)

        assert result.status == ReleaseTwoASGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_TWO_AUTH_SESSION_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary

    def test_email_token_crypto_and_pii_masking(self) -> None:
        """Kriptografik token üretimi, SHA-256 hash'leme ve PII maskelemesini test eder."""
        mgr = EmailVerificationManager(ttl_hours=24)
        email = "kullanici.denetim@finans.com"
        raw_token, payload = mgr.create_verification_token(user_id=101, email=email)

        assert len(raw_token) >= 32
        assert payload.token_hash == mgr.hash_token(raw_token)
        assert raw_token != payload.token_hash

        masked = mgr.mask_email(email)
        assert masked == "k***@finans.com"
        assert "kullanici" not in masked

    def test_email_single_use_and_rate_limit(self) -> None:
        """Tek kullanımlık token tüketimi ve rate limit korumasını test eder."""
        mgr = EmailVerificationManager(ttl_hours=24)
        raw_token, _ = mgr.create_verification_token(user_id=102, email="single@example.com")

        # İlk tüketim başarılı
        payload = mgr.verify_and_consume(raw_token)
        assert payload.user_id == 102

        # İkinci deneme (replay attack) kesinlikle hata vermeli
        with pytest.raises(EmailVerificationError, match="Geçersiz veya daha önce kullanılmış"):
            mgr.verify_and_consume(raw_token)

        # Rate limit testi: 5'ten fazla istekte engelleme
        user_id = 103
        for _ in range(EmailVerificationManager.MAX_REQUESTS_PER_WINDOW):
            mgr.create_verification_token(user_id=user_id, email="limit@example.com")

        with pytest.raises(EmailVerificationError, match="Çok fazla doğrulama talebi"):
            mgr.create_verification_token(user_id=user_id, email="limit@example.com")

    def test_secure_session_token_hashing_and_resolution(self) -> None:
        """Oturum belirteçlerinin DB'de hash'li saklandığını ve çözümlemeyi test eder."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db: DBSession = session_factory()

        try:
            user = User(
                email="secure_user@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            policy = SessionSecurityPolicy(ttl_days=14, secure_cookie=True)
            raw_token, csrf_token, session_record = policy.create_session(
                db=db,
                user_id=user.id,
                ip_hash="test_ip_hash",
                user_agent="BorAI-Test",
            )

            assert raw_token.startswith("sess_")
            assert session_record.session_token_hash == policy.hash_token(raw_token)
            assert raw_token != session_record.session_token_hash

            resolved = policy.resolve_session(db, raw_token)
            assert resolved is not None
            assert resolved.id == user.id

            # Yanlış token çözümlenmemeli
            assert policy.resolve_session(db, "sess_invalid_token") is None
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)

    def test_cookie_security_flags(self) -> None:
        """Oturum ve CSRF çerezlerinin güvenlik bayraklarını test eder."""
        policy = SessionSecurityPolicy(ttl_days=14, secure_cookie=True, samesite="lax")
        resp = Response()

        policy.set_session_cookies(resp, "sess_token_123", "csrf_token_456")
        headers = resp.headers.getlist("set-cookie")

        session_cookie_header = next(
            (h for h in headers if SessionSecurityPolicy.SESSION_COOKIE_NAME in h), ""
        )
        csrf_cookie_header = next(
            (h for h in headers if SessionSecurityPolicy.CSRF_COOKIE_NAME in h), ""
        )

        assert "httponly" in session_cookie_header.lower()
        assert "secure" in session_cookie_header.lower()
        assert "samesite=lax" in session_cookie_header.lower()

        assert "httponly" not in csrf_cookie_header.lower()
        assert "secure" in csrf_cookie_header.lower()
        assert "samesite=lax" in csrf_cookie_header.lower()

        # Çerez silme testi
        clear_resp = Response()
        policy.clear_session_cookies(clear_resp)
        clear_headers = clear_resp.headers.getlist("set-cookie")
        assert any("max-age=0" in h.lower() or "expires=" in h.lower() for h in clear_headers)

    def test_double_submit_csrf_validation(self) -> None:
        """Double-Submit CSRF doğrulamasını test eder."""
        policy = SessionSecurityPolicy()

        class MockRequest:
            def __init__(
                self,
                method: str,
                cookies: dict[str, str] | None = None,
                headers: dict[str, str] | None = None,
            ) -> None:
                self.method = method
                self.cookies = cookies or {}
                self.headers = headers or {}

        # GET isteği muaf
        policy.validate_csrf(MockRequest(method="GET"))  # type: ignore[arg-type]

        # POST isteğinde eşleşen token kabul edilir
        token = "valid_csrf_token_abc"
        valid_post = MockRequest(
            method="POST",
            cookies={SessionSecurityPolicy.CSRF_COOKIE_NAME: token},
            headers={SessionSecurityPolicy.CSRF_HEADER_NAME: token},
        )
        policy.validate_csrf(valid_post)  # type: ignore[arg-type]

        # POST isteğinde eksik token 403 vermeli
        with pytest.raises(HTTPException):
            policy.validate_csrf(MockRequest(method="POST"))  # type: ignore[arg-type]

        # POST isteğinde uyuşmayan token 403 vermeli
        invalid_post = MockRequest(
            method="POST",
            cookies={SessionSecurityPolicy.CSRF_COOKIE_NAME: token},
            headers={SessionSecurityPolicy.CSRF_HEADER_NAME: "tampered_token"},
        )
        with pytest.raises(HTTPException):
            policy.validate_csrf(invalid_post)  # type: ignore[arg-type]

    def test_session_revocation_and_audit(self) -> None:
        """Tekil ve global oturum sonlandırmayı test eder."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db: DBSession = session_factory()

        try:
            user = User(
                email="multi_session@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            policy = SessionSecurityPolicy()
            token1, _, _ = policy.create_session(db, user.id, ip_hash="h1", user_agent="App 1")
            token2, _, _ = policy.create_session(db, user.id, ip_hash="h2", user_agent="App 2")

            # Tekil sonlandırma
            assert policy.terminate_session(db, token1) is True
            assert policy.resolve_session(db, token1) is None
            assert policy.resolve_session(db, token2) is not None

            # Toplu iptal
            revoked_count = policy.revoke_all_user_sessions(db, user.id)
            assert revoked_count == 1
            assert policy.resolve_session(db, token2) is None
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)

    def test_generate_audit_report(self, tmp_path: Path) -> None:
        """Denetim raporu Markdown üretimini ve içeriğini test eder."""
        gatekeeper = ReleaseTwoAuthSessionGatekeeper()
        result = gatekeeper.verify_release_two_auth_session()

        report_file = tmp_path / "RELEASE_2_AUTH_SESSION_REPORT.md"
        saved_path = gatekeeper.generate_audit_report(result, report_path=report_file)

        assert saved_path.exists()
        content = saved_path.read_text(encoding="utf-8")
        assert "# Sürüm 2 Kabul Raporu: E-Posta Doğrulama ve Güvenli Oturum" in content
        assert "REL2-AS-01-EMAIL-TOKEN-SECURITY" in content
        assert "REL2-AS-06-SESSION-REVOCATION-AND-AUDIT" in content
        assert "Double-Submit CSRF" in content
        assert "HttpOnly=True" in content
        assert "Secure=True" in content
        assert "SameSite=Lax" in content
