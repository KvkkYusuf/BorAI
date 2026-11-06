"""FAZ-12: E-posta bildirimlerinde hassas hesap bilgisi veya
gereksiz finansal veri göstermeme testleri.
"""

from datetime import UTC, datetime

import pytest

from stock_analysis.alerts.email_notifier import (
    FORBIDDEN_FINANCIAL_FIELDS,
    LEGAL_DISCLAIMER_ALERT_EMAIL,
    AlertEmailComposer,
    AlertEmailSanitizer,
    AlertNotificationPayload,
    FormattedEmailNotification,
    SensitiveDataLeakError,
)


@pytest.fixture
def sample_payload() -> AlertNotificationPayload:
    """Geçerli ve SPK uyumlu örnek uyarı bildirimi yükü."""
    return AlertNotificationPayload(
        alert_id="alt-12345-uuid",
        symbol="THYAO",
        rule_name="SMA20 Yukarı Kesişimi",
        condition_summary="Kapanış fiyatı (305.50 TRY) SMA20 seviyesini (298.20 TRY) yukarı kesti",
        data_timestamp_utc=datetime(2026, 10, 24, 15, 30, 0, tzinfo=UTC),
        data_source="BIST Gün Sonu (EOD)",
        recipient_email="yatirimci@example.com",
        is_delayed=False,
        user_display_name="Ahmet Bey",
    )


def test_composer_generates_valid_email(sample_payload: AlertNotificationPayload) -> None:
    """Başarılı e-posta oluşturma ve format doğrulaması."""
    composer = AlertEmailComposer()
    result = composer.compose(sample_payload)

    assert isinstance(result, FormattedEmailNotification)
    assert result.symbol == "THYAO"
    assert result.alert_id == "alt-12345-uuid"
    assert "[Bora AI Trend Uyarısı] THYAO: SMA20 Yukarı Kesişimi" in result.subject
    assert result.masked_recipient == "y***@example.com"

    # Plaintext kontrolleri
    assert "THYAO" in result.plaintext_body
    assert "SMA20 Yukarı Kesişimi" in result.plaintext_body
    assert "BIST Gün Sonu (EOD)" in result.plaintext_body
    assert LEGAL_DISCLAIMER_ALERT_EMAIL in result.plaintext_body
    assert "GÜVENLİK VE GİZLİLİK NOTU:" in result.plaintext_body
    assert "şifrenizi, hesap bakiyenizi, portföy tutarınızı" in result.plaintext_body

    # HTML kontrolleri
    assert "<!DOCTYPE html>" in result.html_body
    assert "Bora AI Piyasa Trend Uyarısı" in result.html_body
    assert "THYAO" in result.html_body
    assert "Güncel Piyasa Verisi (EOD)" in result.html_body
    assert LEGAL_DISCLAIMER_ALERT_EMAIL in result.html_body


def test_delayed_data_notice(sample_payload: AlertNotificationPayload) -> None:
    """Gecikmeli veri durumunda e-postada açık uyarı yer almalıdır."""
    delayed_payload = AlertNotificationPayload(
        alert_id=sample_payload.alert_id,
        symbol=sample_payload.symbol,
        rule_name=sample_payload.rule_name,
        condition_summary=sample_payload.condition_summary,
        data_timestamp_utc=sample_payload.data_timestamp_utc,
        data_source="BIST 15 Dk Gecikmeli Feed",
        recipient_email=sample_payload.recipient_email,
        is_delayed=True,
        delay_description="15 Dakika Gecikmeli Veri",
    )
    composer = AlertEmailComposer()
    result = composer.compose(delayed_payload)

    assert "Dikkat: 15 Dakika Gecikmeli Veri" in result.plaintext_body
    assert "delayed" in result.html_body
    assert "Dikkat: 15 Dakika Gecikmeli Veri" in result.html_body


@pytest.mark.parametrize("forbidden_field", sorted(FORBIDDEN_FINANCIAL_FIELDS))
def test_rejects_forbidden_sensitive_financial_fields(
    sample_payload: AlertNotificationPayload, forbidden_field: str
) -> None:
    """Hassas portföy, bakiye veya kimlik alanları extra_metadata'ya eklenirse engellenmelidir."""
    with pytest.raises(SensitiveDataLeakError, match="yasaklı hassas hesap/finansal alan"):
        AlertNotificationPayload(
            alert_id=sample_payload.alert_id,
            symbol=sample_payload.symbol,
            rule_name=sample_payload.rule_name,
            condition_summary=sample_payload.condition_summary,
            data_timestamp_utc=sample_payload.data_timestamp_utc,
            data_source=sample_payload.data_source,
            recipient_email=sample_payload.recipient_email,
            extra_metadata={forbidden_field: "sensitive_value_123"},
        )


def test_sanitizer_detects_secret_patterns() -> None:
    """E-posta metninde parola veya token sızıntısı yakalanmalıdır."""
    sanitizer = AlertEmailSanitizer()

    with pytest.raises(SensitiveDataLeakError, match="parola, token veya gizli anahtar"):
        sanitizer.scan_for_leaks("Kullanıcı token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")

    with pytest.raises(SensitiveDataLeakError, match="parola, token veya gizli anahtar"):
        sanitizer.scan_for_leaks("Sistem password = SuperSecretPassword123")


def test_sanitizer_detects_unnecessary_financial_data() -> None:
    """E-posta metninde gereksiz hesap bakiyesi veya portföy verisi engellenmelidir."""
    sanitizer = AlertEmailSanitizer()

    with pytest.raises(SensitiveDataLeakError, match="gereksiz hassas finansal veri"):
        sanitizer.scan_for_leaks("Hesap bakiye: 54000.50 TRY")

    with pytest.raises(SensitiveDataLeakError, match="gereksiz hassas finansal veri"):
        sanitizer.scan_for_leaks("Toplam portfoy tutari = 250000 TRY")


def test_sanitizer_detects_iban_and_card_numbers() -> None:
    """E-posta metninde IBAN ve kredi kartı numaraları engellenmelidir."""
    sanitizer = AlertEmailSanitizer()

    with pytest.raises(SensitiveDataLeakError, match="IBAN numarası"):
        sanitizer.scan_for_leaks("Lütfen TR330006100511123456789012 nolu hesaba yatırın")

    with pytest.raises(SensitiveDataLeakError, match="kart numarası deseni"):
        sanitizer.scan_for_leaks("Kredi kartı 4543 2345 6789 0123 üzerinden işlem")


def test_spk_compliance_in_rule_name_and_summary(sample_payload: AlertNotificationPayload) -> None:
    """Kural adı veya özetinde SPK mevzuatına aykırı yönlendirici ifadeler reddedilmelidir."""
    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        AlertNotificationPayload(
            alert_id=sample_payload.alert_id,
            symbol=sample_payload.symbol,
            rule_name="Bu hisseyi hemen al kuralı",
            condition_summary=sample_payload.condition_summary,
            data_timestamp_utc=sample_payload.data_timestamp_utc,
            data_source=sample_payload.data_source,
            recipient_email=sample_payload.recipient_email,
        )

    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        AlertNotificationPayload(
            alert_id=sample_payload.alert_id,
            symbol=sample_payload.symbol,
            rule_name="Direnç Kırılımı",
            condition_summary="Hedef fiyat 450 TL olarak belirlendi",
            data_timestamp_utc=sample_payload.data_timestamp_utc,
            data_source=sample_payload.data_source,
            recipient_email=sample_payload.recipient_email,
        )


def test_xss_protection_in_html_email(sample_payload: AlertNotificationPayload) -> None:
    """HTML e-posta içeriğinde XSS enjeksiyonu önlenmelidir."""
    xss_payload = AlertNotificationPayload(
        alert_id=sample_payload.alert_id,
        symbol="THYAO",
        rule_name="RSI Asiri Alim Seviyesi",
        condition_summary="RSI degeri 75 seviyesine ulasti",
        data_timestamp_utc=sample_payload.data_timestamp_utc,
        data_source="BIST EOD",
        recipient_email=sample_payload.recipient_email,
        user_display_name="<script>alert('pwned')</script>John",
    )
    composer = AlertEmailComposer()
    result = composer.compose(xss_payload)

    # Betik etiketleri temizlenmiş olmalıdır
    assert "<script>" not in result.html_body
    assert "alert('pwned')" not in result.html_body
    assert "John" in result.html_body


def test_empty_fields_validation(sample_payload: AlertNotificationPayload) -> None:
    """Zorunlu alanların boş geçilmesi engellenmelidir."""
    with pytest.raises(SensitiveDataLeakError, match="alert_id"):
        AlertNotificationPayload(
            alert_id="",
            symbol="THYAO",
            rule_name="Test Kural",
            condition_summary="Test özet",
            data_timestamp_utc=sample_payload.data_timestamp_utc,
            data_source="BIST EOD",
            recipient_email="user@example.com",
        )

    with pytest.raises(SensitiveDataLeakError, match="Sembol boş olamaz"):
        AlertNotificationPayload(
            alert_id="id-1",
            symbol="   ",
            rule_name="Test Kural",
            condition_summary="Test özet",
            data_timestamp_utc=sample_payload.data_timestamp_utc,
            data_source="BIST EOD",
            recipient_email="user@example.com",
        )

    with pytest.raises(SensitiveDataLeakError, match="e-posta"):
        AlertNotificationPayload(
            alert_id="id-1",
            symbol="THYAO",
            rule_name="Test Kural",
            condition_summary="Test özet",
            data_timestamp_utc=sample_payload.data_timestamp_utc,
            data_source="BIST EOD",
            recipient_email="invalid-email",
        )
