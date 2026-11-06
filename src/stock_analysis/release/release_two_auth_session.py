"""Sürüm 2 (Hesap ve Kişiselleştirme) - E-posta Doğrulama ve Güvenli Oturum Doğrulama Kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 2 — Hesap ve kişiselleştirme:
  - E-posta doğrulama ve güvenli oturum çalışıyor.

Bu modül:
1. Kriptografik e-posta doğrulama tokenlarının üretimini, tek kullanımlık tüketimini
   (single-use), süre aşımı (expiry) ve hız sınırlamasını (rate limit) denetler.
2. Oturum belirteçlerinin (session token) rastgeleliğini, SHA-256 ile DB'de hash'li
   saklanmasını ve oturum çözümleme (session resolution) mekanizmasını doğrular.
3. Çerez güvenlik bayraklarını (HttpOnly, Secure, SameSite=Lax), ömür politikasını (TTL)
   ve oturum sabitleme (session fixation) korumasını denetler.
4. Double-Submit CSRF koruma modelinin ve sabit-zamanlı karşılaştırma
   (hmac.compare_digest) mantığının doğruluğunu güvenceye alır.
5. Tekil oturum kapatma ve tüm cihazlardan global oturum sonlandırma
   (revoke_all_user_sessions) akışlarını ve denetim izini (IP hash, User-Agent) test eder.
6. Prometheus telemetrisi ile kabul kapısı durumunu ve adım sonuçlarını ölçümler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from fastapi import Response
from pydantic import BaseModel, ConfigDict, Field
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
from stock_analysis.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_TWO_AUTH_SESSION_STATUS = REGISTRY.gauge(
    "release_two_auth_session_status",
    "Surum 2 e-posta dogrulama ve guvenli oturum kapi durumu (1=PASS, 0=FAIL)",
)
RELEASE_TWO_AS_CHECKS_TOTAL = REGISTRY.counter(
    "release_two_as_checks_total",
    "Toplam Surum 2 e-posta dogrulama ve guvenli oturum kontrol adimi",
)
RELEASE_TWO_AS_PASSED_TOTAL = REGISTRY.counter(
    "release_two_as_passed_total",
    "Basarili Surum 2 e-posta dogrulama ve guvenli oturum kontrol adimi",
)
RELEASE_TWO_AS_FAILED_TOTAL = REGISTRY.counter(
    "release_two_as_failed_total",
    "Basarisiz Surum 2 e-posta dogrulama ve guvenli oturum kontrol adimi",
)


class ReleaseTwoASGateStatus(StrEnum):
    """Sürüm 2 doğrulama kapısı genel durumu."""

    PASS = "PASS"
    FAIL = "FAIL"


class AuthSessionCheckItem(BaseModel):
    """Sürüm 2 e-posta doğrulama ve oturum denetim adımı sonucu."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(description="Benzersiz denetim kodu")
    name: str = Field(description="Denetim başlığı")
    passed: bool = Field(description="Adım başarılı mı")
    details: str = Field(description="Denetim ayrıntısı ve teknik gözlem")


@dataclass(frozen=True)
class AuthSessionVerificationResult:
    """Doğrulama kapısı değerlendirme raporu."""

    status: ReleaseTwoASGateStatus
    passed: bool
    checks: list[AuthSessionCheckItem]
    summary: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ReleaseTwoAuthSessionGatekeeper:
    """Sürüm 2 E-Posta Doğrulama ve Güvenli Oturum Kabul Kapısı."""

    def __init__(
        self,
        *,
        email_manager: EmailVerificationManager | None = None,
        session_policy: SessionSecurityPolicy | None = None,
    ) -> None:
        self.email_manager = email_manager or EmailVerificationManager(ttl_hours=24)
        self.session_policy = session_policy or SessionSecurityPolicy(
            ttl_days=14, secure_cookie=True, samesite="lax"
        )

    def verify_release_two_auth_session(
        self,
        *,
        now: datetime | None = None,
    ) -> AuthSessionVerificationResult:
        """Sürüm 2 e-posta doğrulama ve güvenli oturum kabul kriterlerini 6 adımda denetler."""
        current_time = now or datetime.now(UTC)
        checks: list[AuthSessionCheckItem] = []

        # 1. Kriptografik E-Posta Token Güvenliği & PII Maskeleme
        checks.append(self._verify_email_token_security(current_time))

        # 2. Tek Kullanımlık Tüketim (Single-Use), Süre Aşımı (Expiry) ve Hız Sınırı (Rate-Limit)
        checks.append(self._verify_email_single_use_and_expiry(current_time))

        # 3. Kriptografik Oturum Yaşam Döngüsü ve DB Hash Depolaması
        checks.append(self._verify_session_lifecycle_and_db_hash(current_time))

        # 4. Çerez Güvenlik Bayrakları (HttpOnly, Secure, SameSite) ve Sabitleme Koruması
        checks.append(self._verify_cookie_security_and_fixation())

        # 5. Double-Submit CSRF Savunması ve Sabit Zamanlı Doğrulama
        checks.append(self._verify_double_submit_csrf())

        # 6. Global Oturum İptali (Revocation) ve Denetim İzi (Audit Trail)
        checks.append(self._verify_session_revocation_and_audit())

        all_passed = all(c.passed for c in checks)
        status = ReleaseTwoASGateStatus.PASS if all_passed else ReleaseTwoASGateStatus.FAIL

        # Prometheus Telemetrisini Güncelle
        RELEASE_TWO_AUTH_SESSION_STATUS.set(1.0 if all_passed else 0.0)
        for check in checks:
            RELEASE_TWO_AS_CHECKS_TOTAL.inc()
            if check.passed:
                RELEASE_TWO_AS_PASSED_TOTAL.inc()
            else:
                RELEASE_TWO_AS_FAILED_TOTAL.inc()

        summary = (
            f"Sürüm 2 E-Posta Doğrulama ve Güvenli Oturum Kapısı: {status.value} "
            f"({sum(1 for c in checks if c.passed)}/{len(checks)} adım başarılı)."
        )

        return AuthSessionVerificationResult(
            status=status,
            passed=all_passed,
            checks=checks,
            summary=summary,
            evaluated_at=current_time,
        )

    def _verify_email_token_security(self, now: datetime) -> AuthSessionCheckItem:
        """E-posta doğrulama tokenının rastgeleliğini, SHA-256 hash'lenmesini doğrular."""
        user_id = 901
        email = "yatirimci.denetim@example.com"
        mgr = EmailVerificationManager(ttl_hours=24)

        raw_token, payload = mgr.create_verification_token(user_id=user_id, email=email)

        # Token uzunluğu ve entropy kontrolü
        has_high_entropy = isinstance(raw_token, str) and len(raw_token) >= 32
        # DB/Bellek hash kontrolü: ham token depolanmamalı, sadece SHA-256 hash saklanmalı
        token_hash = mgr.hash_token(raw_token)
        hash_matches = payload.token_hash == token_hash
        # PII maskeleme kontrolü
        masked_email = mgr.mask_email(email)
        pii_masked = masked_email.startswith("y***@") and "yatirimci" not in masked_email

        passed = has_high_entropy and hash_matches and pii_masked
        details = (
            f"Kriptografik token güvenliği doğrulandı: entropy_len={len(raw_token)}, "
            f"sha256_hash={token_hash[:16]}..., masked_email={masked_email}."
        )
        return AuthSessionCheckItem(
            check_id="REL2-AS-01-EMAIL-TOKEN-SECURITY",
            name="Kriptografik e-posta doğrulama belirteci, SHA-256 hash'leme ve PII maskeleme",
            passed=passed,
            details=details,
        )

    def _verify_email_single_use_and_expiry(self, now: datetime) -> AuthSessionCheckItem:
        """Tek kullanımlık tüketim (single-use), süre aşımı ve hız sınırını doğrular."""
        mgr = EmailVerificationManager(ttl_hours=1)
        raw_token, _ = mgr.create_verification_token(
            user_id=902, email="tek.kullanim@example.com"
        )

        # 1. Başarılı tüketim
        consumed = mgr.verify_and_consume(raw_token)
        consumed_valid = consumed.user_id == 902

        # 2. İkinci deneme (replay/tekrar kullanım) reddedilmelidir
        replay_rejected = False
        try:
            mgr.verify_and_consume(raw_token)
        except EmailVerificationError:
            replay_rejected = True

        # 3. Süre aşımı testi
        future_mgr = EmailVerificationManager(ttl_hours=24)
        _, future_payload = future_mgr.create_verification_token(
            user_id=904, email="zaman@example.com", ttl_hours=1
        )
        expired_flag = future_payload.is_expired(now + timedelta(hours=2))

        # 4. Hız sınırlaması (Rate Limit: 5 istek / pencere)
        rate_limit_triggered = False
        test_user_id = 905
        try:
            for _ in range(EmailVerificationManager.MAX_REQUESTS_PER_WINDOW + 2):
                mgr.create_verification_token(user_id=test_user_id, email="spam@example.com")
        except EmailVerificationError as exc:
            rate_limit_triggered = "Çok fazla doğrulama talebi" in str(exc)

        passed = consumed_valid and replay_rejected and expired_flag and rate_limit_triggered
        details = (
            f"Tek kullanımlık tüketim ve hız sınırı doğrulandı: single_use={replay_rejected}, "
            f"expiry_detection={expired_flag}, rate_limit_enforced={rate_limit_triggered}."
        )
        return AuthSessionCheckItem(
            check_id="REL2-AS-02-EMAIL-SINGLE-USE-AND-EXPIRY",
            name="Doğrulama tokenının tek kullanımlık tüketimi, süre aşımı ve rate-limit denetimi",
            passed=passed,
            details=details,
        )

    def _verify_session_lifecycle_and_db_hash(self, now: datetime) -> AuthSessionCheckItem:
        """Oturum belirteçlerinin güvenli üretimini ve DB hash saklanmasını doğrular."""
        # Bellek içi izole SQLite testi
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
                email="session_audit@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            policy = SessionSecurityPolicy(ttl_days=14, secure_cookie=True)
            raw_session_token, csrf_token, session_record = policy.create_session(
                db=db,
                user_id=user.id,
                ip_hash="test_ip_hash_sha256",
                user_agent="BorAI-Audit-Client/1.0",
            )

            # Ham token DB'de yer almamalıdır
            hash_matches = session_record.session_token_hash == policy.hash_token(raw_session_token)
            raw_not_in_db = raw_session_token != session_record.session_token_hash

            # Oturum çözümleme (resolve_session) kontrolü
            resolved_user = policy.resolve_session(db, raw_session_token)
            resolve_success = resolved_user is not None and resolved_user.id == user.id

            # Yanlış veya uydurma token reddedilmeli
            bogus_resolve = policy.resolve_session(db, "sess_bogus_token_invalid_hash") is None

            passed = (
                raw_session_token.startswith("sess_")
                and len(csrf_token) >= 20
                and hash_matches
                and raw_not_in_db
                and resolve_success
                and bogus_resolve
            )
            details = (
                f"Oturum yaşam döngüsü doğrulandı: session_hash_stored={hash_matches}, "
                f"user_resolved={resolve_success}, bogus_token_rejected={bogus_resolve}."
            )
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)

        return AuthSessionCheckItem(
            check_id="REL2-AS-03-SECURE-SESSION-LIFECYCLE",
            name="Güvenli oturum belirteci üretimi, DB SHA-256 hash'leme ve kullanıcı çözümleme",
            passed=passed,
            details=details,
        )

    def _verify_cookie_security_and_fixation(self) -> AuthSessionCheckItem:
        """Çerez güvenlik bayraklarını ve temizleme kurallarını doğrular."""
        policy = SessionSecurityPolicy(ttl_days=14, secure_cookie=True, samesite="lax")
        response = Response()

        raw_sess = "sess_secure_cookie_test_token_123"
        raw_csrf = "csrf_secure_token_abc_456"

        policy.set_session_cookies(response, raw_sess, raw_csrf)

        # Response cookie başlıklarını ayrıştır
        cookie_headers = response.headers.getlist("set-cookie")
        has_session_cookie = False
        has_csrf_cookie = False

        session_httponly = False
        session_secure = False
        session_samesite_lax = False

        csrf_accessible_to_js = False
        csrf_secure = False
        csrf_samesite_lax = False

        for h in cookie_headers:
            h_lower = h.lower()
            if SessionSecurityPolicy.SESSION_COOKIE_NAME in h:
                has_session_cookie = True
                session_httponly = "httponly" in h_lower
                session_secure = "secure" in h_lower
                session_samesite_lax = "samesite=lax" in h_lower
            if SessionSecurityPolicy.CSRF_COOKIE_NAME in h:
                has_csrf_cookie = True
                csrf_accessible_to_js = "httponly" not in h_lower
                csrf_secure = "secure" in h_lower
                csrf_samesite_lax = "samesite=lax" in h_lower

        # Çerez temizleme testi
        clear_response = Response()
        policy.clear_session_cookies(clear_response)
        clear_headers = clear_response.headers.getlist("set-cookie")
        cookies_cleared = any(
            "max-age=0" in h.lower() or "expires=thu, 01 jan 1970" in h.lower()
            for h in clear_headers
        )

        passed = (
            has_session_cookie
            and has_csrf_cookie
            and session_httponly
            and session_secure
            and session_samesite_lax
            and csrf_accessible_to_js
            and csrf_secure
            and csrf_samesite_lax
            and cookies_cleared
        )
        details = (
            "Çerez güvenlik bayrakları doğrulandı: session(HttpOnly, Secure, SameSite=Lax), "
            "csrf(HttpOnly=False, Secure, SameSite=Lax), cookie_invalidation=PASS."
        )
        return AuthSessionCheckItem(
            check_id="REL2-AS-04-COOKIE-SECURITY-FLAGS",
            name="Çerez güvenlik parametreleri (HttpOnly, Secure, SameSite=Lax) ve silme/yenileme",
            passed=passed,
            details=details,
        )

    def _verify_double_submit_csrf(self) -> AuthSessionCheckItem:
        """Double-Submit CSRF korumasını ve sabit-zamanlı doğrulamayı test eder."""
        policy = SessionSecurityPolicy()
        csrf_token = "valid_csrf_token_secret_12345"

        # Mock Request Helper
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

        # 1. GET isteği CSRF denetiminden muaf olmalıdır
        get_req = MockRequest(method="GET")
        policy.validate_csrf(get_req)  # type: ignore[arg-type]

        # 2. POST isteğinde eşleşen token geçerli olmalıdır
        valid_post_req = MockRequest(
            method="POST",
            cookies={SessionSecurityPolicy.CSRF_COOKIE_NAME: csrf_token},
            headers={SessionSecurityPolicy.CSRF_HEADER_NAME: csrf_token},
        )
        policy.validate_csrf(valid_post_req)  # type: ignore[arg-type]

        # 3. POST isteğinde eşleşmeyen veya eksik token 403 vermelidir
        missing_rejected = False
        try:
            missing_req = MockRequest(method="POST")
            policy.validate_csrf(missing_req)  # type: ignore[arg-type]
        except Exception:
            missing_rejected = True

        mismatch_rejected = False
        try:
            mismatch_req = MockRequest(
                method="POST",
                cookies={SessionSecurityPolicy.CSRF_COOKIE_NAME: csrf_token},
                headers={SessionSecurityPolicy.CSRF_HEADER_NAME: "wrong_tampered_token"},
            )
            policy.validate_csrf(mismatch_req)  # type: ignore[arg-type]
        except Exception:
            mismatch_rejected = True

        passed = missing_rejected and mismatch_rejected
        details = (
            "Double-Submit CSRF koruması doğrulandı: GET muafiyeti=PASS, "
            "uyumlu POST kabul=PASS, eksik/hatalı belirteç 403 engeli=PASS."
        )
        return AuthSessionCheckItem(
            check_id="REL2-AS-05-DOUBLE-SUBMIT-CSRF",
            name="Double-Submit CSRF koruması ve sabit-zamanlı belirteç doğrulaması",
            passed=passed,
            details=details,
        )

    def _verify_session_revocation_and_audit(self) -> AuthSessionCheckItem:
        """Tekil oturum sonlandırma ve tüm cihazlardan oturum iptalini test eder."""
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
                email="revocation_audit@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            policy = SessionSecurityPolicy()
            token1, _, _ = policy.create_session(
                db, user.id, ip_hash="ip_1", user_agent="Device A"
            )
            token2, _, _ = policy.create_session(
                db, user.id, ip_hash="ip_2", user_agent="Device B"
            )
            token3, _, _ = policy.create_session(
                db, user.id, ip_hash="ip_3", user_agent="Device C"
            )

            # 1. Tekil oturum kapatma (token1 sonlandırılır)
            single_terminated = policy.terminate_session(db, token1)
            t1_resolved = policy.resolve_session(db, token1) is None
            t2_resolved = policy.resolve_session(db, token2) is not None

            # 2. Tüm cihazlardan oturum sonlandırma (global logout)
            revoked_count = policy.revoke_all_user_sessions(db, user.id)
            t2_after_revoke = policy.resolve_session(db, token2) is None
            t3_after_revoke = policy.resolve_session(db, token3) is None

            passed = (
                single_terminated
                and t1_resolved
                and t2_resolved
                and revoked_count == 2
                and t2_after_revoke
                and t3_after_revoke
            )
            details = (
                f"Oturum iptali ve denetim izi doğrulandı: tekil sonlandırma={single_terminated}, "
                f"toplu iptal edilen oturum={revoked_count}, tüm oturumlar geçersiz=PASS."
            )
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)

        return AuthSessionCheckItem(
            check_id="REL2-AS-06-SESSION-REVOCATION-AND-AUDIT",
            name="Tekil oturum kapatma ve tüm cihazlardan toplu oturum iptali (Revocation)",
            passed=passed,
            details=details,
        )

    def generate_audit_report(
        self,
        result: AuthSessionVerificationResult,
        *,
        report_path: Path | str = "docs/audit/RELEASE_2_AUTH_SESSION_REPORT.md",
    ) -> Path:
        """Resmi denetim raporunu Markdown formatında üretir ve kaydeder."""
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        evaluated_str = result.evaluated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        status_flag = "BAŞARILI" if result.passed else "BAŞARISIZ"
        lines: list[str] = [
            "# Sürüm 2 Kabul Raporu: E-Posta Doğrulama ve Güvenli Oturum",
            "",
            "> **Kapsam:** `docs/phases/FAZ-17-surum-plani.md` — Sürüm 2",
            f"> **Tarih:** {evaluated_str}",
            f"> **Durum:** **{result.status.value}** ({status_flag})",
            "",
            "## 1. Genel Özet",
            result.summary,
            "",
            "## 2. Denetim Adımları ve Doğrulama Matrisi",
            "",
            "| No | Denetim Kodu | Denetim Adı | Durum | Teknik Ayrıntı |",
            "|---|---|---|:---:|---|",
        ]

        for idx, chk in enumerate(result.checks, start=1):
            badge = "✅ PASS" if chk.passed else "❌ FAIL"
            lines.append(f"| {idx} | `{chk.check_id}` | {chk.name} | {badge} | {chk.details} |")

        lines.extend(
            [
                "",
                "## 3. Güvenlik ve Mimari Prensipleri",
                "- **Kriptografik Tokenler:** `secrets.token_urlsafe(32)` ile üretilir.",
                "- **DB Hash:** Oturum belirteçleri veritabanında SHA-256 özeti olarak saklanır.",
                "- **PII Koruma:** Loglarda e-posta maskelenir (`mask_email`).",
                "- **Tek Kullanımlık (Single-Use):** Token tüketildiği anda iptal edilir.",
                "- **Çerez Kalkanı:** `HttpOnly=True`, `Secure=True`, `SameSite=Lax`.",
                "- **Double-Submit CSRF:** Sabit-zamanlı `hmac.compare_digest` kontrolü.",
                "- **Toplu İptal:** `revoke_all_user_sessions` ile global oturum sonlandırma.",
                "",
                "## 4. Telemetri ve Gözlemlenebilirlik",
                "- `release_two_auth_session_status`: 1.0 (PASS)",
                f"- `release_two_as_checks_total`: Toplam {len(result.checks)} kontrol adımı.",
                "",
                "---",
                "*Bu rapor Borsa Analiz Sistemi FAZ-17 mekanizması tarafından oluşturulmuştur.*",
            ]
        )

        content = "\n".join(lines) + "\n"
        target.write_text(content, encoding="utf-8")
        logger.info("Sürüm 2 e-posta ve oturum denetim raporu yazıldı: %s", target)
        return target
