"""Sürüm 1 Veri Sağlayıcı Hata Yönetimi Doğrulama Kapısı Test Paketi."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stock_analysis.providers.base import (
    ProviderDataFormatError,
    ProviderError,
    ProviderQuotaExceededError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from stock_analysis.providers.licensed_base import LicensedProviderAuthError
from stock_analysis.release.release_one_provider_error import (
    RELEASE_ONE_PROVIDER_ERROR_STATUS,
    ProviderErrorKind,
    ProviderErrorSanitizer,
    ProviderErrorVerificationResult,
    ReleaseOnePEGateStatus,
    ReleaseOneProviderErrorGatekeeper,
    SafeProviderErrorResponse,
)


class TestReleaseOneProviderErrorSuite:
    """Sürüm 1 Veri Sağlayıcı Hata Yönetimi Kapısı testleri."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseOneProviderErrorGatekeeper()
        now = datetime.now(UTC)

        result: ProviderErrorVerificationResult = (
            gatekeeper.verify_release_one_provider_error_handling(now=now)
        )

        assert result.status == ReleaseOnePEGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_ONE_PROVIDER_ERROR_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary

    def test_timeout_handling_transient_and_safe(self) -> None:
        """Zaman aşımında kullanıcıya güvenli mesaj ve retry imkanı verildiğini doğrular."""
        sanitizer = ProviderErrorSanitizer()
        now = datetime.now(UTC)
        exc = ProviderTimeoutError("Connection timed out after 5000ms connecting to 10.0.0.5:443")

        resp: SafeProviderErrorResponse = sanitizer.create_safe_response(
            exc, symbol="THYAO", now=now
        )

        assert resp.success is False
        assert resp.kind == ProviderErrorKind.TIMEOUT
        assert resp.error_code == "ERR_PROVIDER_TIMEOUT"
        assert resp.is_transient is True
        assert resp.retry_after_sec == 15
        assert "zaman aşımı" in resp.user_message.lower()
        assert "THYAO" in resp.user_message
        assert "10.0.0.5" not in resp.user_message
        assert "10.0.0.5" not in resp.actionable_guidance
        assert resp.timestamp == now

    def test_rate_limit_handling_with_retry_after(self) -> None:
        """Kota aşımında retry_after süresinin kullanıcıya aktarıldığını doğrular."""
        sanitizer = ProviderErrorSanitizer()
        exc = ProviderQuotaExceededError(
            "Rate limit exceeded: 429 Too Many Requests", retry_after_sec=75.0
        )

        resp = sanitizer.create_safe_response(exc, symbol="GARAN")

        assert resp.kind == ProviderErrorKind.RATE_LIMIT
        assert resp.error_code == "ERR_PROVIDER_RATE_LIMIT"
        assert resp.is_transient is True
        assert resp.retry_after_sec == 75
        assert "kota" in resp.user_message.lower()
        assert "75 saniye" in resp.actionable_guidance

    def test_leak_prevention_scrubs_all_secrets(self) -> None:
        """Sırların, token, IP ve sunucu yollarının sansürlendiğini doğrular."""
        raw_text = (
            "Exception: api_key=SECRET_TOKEN_9999 bearer eyJhbGciOiJIUzI1NiJ9.test "
            "connecting to 172.16.254.1:8000 at C:\\Windows\\System32\\app.dll "
            "with password=SuperSecretPassword123"
        )
        sanitized = ProviderErrorSanitizer.sanitize_raw_string(raw_text)

        assert "SECRET_TOKEN_9999" not in sanitized
        assert "SuperSecretPassword123" not in sanitized
        assert "172.16.254.1" not in sanitized
        assert "C:\\Windows\\System32" not in sanitized
        assert "[REDACTED]" in sanitized
        assert "[MASKED_IP]" in sanitized
        assert "[SYSTEM_PATH]" in sanitized

        # DTO seviyesinde sızıntı kontrolü
        exc = LicensedProviderAuthError(raw_text)
        resp = ProviderErrorSanitizer.create_safe_response(exc, symbol="ASELS")

        assert resp.kind == ProviderErrorKind.AUTH_FAILURE
        assert resp.error_code == "ERR_PROVIDER_AUTH"
        assert "SECRET_TOKEN_9999" not in resp.user_message
        assert "SuperSecretPassword123" not in resp.actionable_guidance
        assert "yetkilendirmesi" in resp.user_message

    def test_stale_data_fallback_with_warning_banner(self) -> None:
        """Sağlayıcı çöktüğünde bayat verinin açık bir uyarıyla sunulduğunu doğrular."""
        sanitizer = ProviderErrorSanitizer()
        stale_time = datetime(2026, 10, 29, 10, 0, 0, tzinfo=UTC)
        exc = ProviderUnavailableError("Provider backend outage")

        resp = sanitizer.create_safe_response(exc, symbol="KCHOL", stale_data_timestamp=stale_time)

        assert resp.has_stale_fallback is True
        assert resp.stale_data_warning is not None
        assert "2026-10-29 10:00 UTC" in resp.stale_data_warning
        assert "Canlı veri sağlayıcısı şu anda erişilemez" in resp.stale_data_warning
        assert resp.kind == ProviderErrorKind.UPSTREAM_UNAVAILABLE

    def test_incident_id_uniqueness_and_format(self) -> None:
        """Her hatada benzersiz ve geçerli bir incident_id oluşturulduğunu test eder."""
        sanitizer = ProviderErrorSanitizer()
        r1 = sanitizer.create_safe_response(ProviderError("Error 1"))
        r2 = sanitizer.create_safe_response(ProviderError("Error 2"))

        assert r1.incident_id.startswith("inc-")
        assert r2.incident_id.startswith("inc-")
        assert r1.incident_id != r2.incident_id
        assert len(r1.incident_id) >= 10

    def test_malformed_and_generic_data_format_errors(self) -> None:
        """Bozuk format ve beklenmeyen hataların güvenli DTO'ya dönüştürüldüğünü doğrular."""
        sanitizer = ProviderErrorSanitizer()

        # Bozuk veri formatı
        fmt_exc = ProviderDataFormatError("Corrupted JSON received: unexpected EOF")
        resp_fmt = sanitizer.create_safe_response(fmt_exc)
        assert resp_fmt.kind == ProviderErrorKind.MALFORMED_DATA
        assert resp_fmt.error_code == "ERR_PROVIDER_MALFORMED"
        assert resp_fmt.is_transient is False

        # Genel beklenmeyen hata
        generic_exc = RuntimeError("System memory bus fault at 0xdeadbeef")
        resp_gen = sanitizer.create_safe_response(generic_exc)
        assert resp_gen.kind == ProviderErrorKind.UNKNOWN
        assert resp_gen.error_code == "ERR_PROVIDER_GENERIC"
        assert "0xdeadbeef" not in resp_gen.user_message

    def test_audit_report_generation(self, tmp_path: Path) -> None:
        """Resmi denetim markdown raporu dosyasının doğru üretildiğini doğrular."""
        gatekeeper = ReleaseOneProviderErrorGatekeeper()
        report_file = tmp_path / "RELEASE_1_PROVIDER_ERROR_HANDLING_REPORT.md"

        result = gatekeeper.verify_release_one_provider_error_handling()
        content = gatekeeper.generate_audit_report(result=result, output_path=report_file)

        assert report_file.exists()
        assert "# Sürüm 1 — Veri Sağlayıcı Hatası Güvenli Gösterim Raporu" in content
        assert "✅ PASS" in content
        assert "REL1-PE-01-TIMEOUT-SAFE-DISPLAY" in content
        assert "REL1-PE-03-CREDENTIAL-AND-LEAK-PREVENTION" in content
        assert "REL1-PE-04-STALE-DATA-FALLBACK-WARNING" in content
