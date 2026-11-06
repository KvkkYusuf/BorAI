"""E-posta bildirimlerinde hassas hesap bilgisi veya gereksiz finansal veri filtresi.

Bu modül, FAZ-12 güvenlik ve mevzuat gereksinimlerine uygundur:
- E-posta bildirimlerinde parola, token gibi kimlik/sır verilerini kesinlikle barındırmaz.
- Kullanıcıya ait bakiye, portföy kâr/zarar, IBAN, TCKN veya maliyet gibi verileri filtreler.
- Yalnızca ilgili sembol, veri zamanı, tetiklenen koşul özeti ve gecikme durumunu içerir.
- SPK mevzuatına uygun yatırım tavsiyesi değildir yasal uyarısını zorunlu kılar.
- HTML ve Düz Metin (Plaintext) formatlarında XSS ve sızıntı denetimli şablonlar üretir.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from stock_analysis.alerts.trend_disclaimer import (
    BULLISH_TREND_DISCLAIMER_FULL,
    BULLISH_TREND_SHORT_RISK_NOTE,
    assert_bullish_alert_has_disclaimer,
    is_bullish_alert_payload,
)
from stock_analysis.api.sanitizer import mask_email
from stock_analysis.indicators.trend import validate_compliant_text

LEGAL_DISCLAIMER_ALERT_EMAIL: str = (
    "YASAL UYARI: Bu e-posta bilgilendirmesi yalnızca önceden tanımlanmış teknik/matematiksel "
    "trend göstergelerinin gerçekleşme durumunu bildirir. Sermaye Piyasası Kurulu (SPK) mevzuatı "
    "uyarınca kesinlikle yatırım danışmanlığı, yönlendirme, portföy yönetimi veya alım-satım "
    "tavsiyesi niteliğinde değildir."
)

# Sızıntı tespiti için regex kalıpları
_IBAN_PATTERN = re.compile(r"\bTR\d{2}[0-9A-Z]{5}\d{17}\b", re.IGNORECASE)
_TCKN_PATTERN = re.compile(r"\b[1-9]\d{10}\b")
_CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
_SECRET_KEYWORD_PATTERN = re.compile(
    r"\b(password|parola|sifre|passwd|token|api_key|access_token|refresh_token|"
    r"private_key|secret_key|session_id|auth_token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_ACCOUNT_FINANCIAL_KEYWORD_PATTERN = re.compile(
    r"\b(bakiye|hesap\s*bakiyesi|portfoy\s*tutari|toplam\s*varlik|kar/zarar|"
    r"maliyet\s*tutari|iban|kredi\s*karti|hesap\s*no)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

FORBIDDEN_FINANCIAL_FIELDS = frozenset(
    {
        "account_balance",
        "balance",
        "portfolio_value",
        "portfolio_amount",
        "profit_loss",
        "pnl",
        "net_worth",
        "total_assets",
        "cost_basis",
        "iban",
        "account_number",
        "card_number",
        "credit_card",
        "tckn",
        "identity_number",
        "password",
        "password_hash",
        "token",
        "api_key",
        "secret",
        "session_id",
    }
)


class SensitiveDataLeakError(ValueError):
    """E-posta bildiriminde hassas veri veya yasaklı finansal alan tespit edildiğinde fırlatılır."""


@dataclass(frozen=True)
class AlertNotificationPayload:
    """E-posta bildirimi için filtrelenmiş ve güvenli asgari veri modeli."""

    alert_id: str
    symbol: str
    rule_name: str
    condition_summary: str
    data_timestamp_utc: datetime
    data_source: str
    recipient_email: str
    is_delayed: bool = False
    delay_description: str | None = None
    user_display_name: str | None = None
    extra_metadata: dict[str, Any] | None = None
    unsubscribe_url: str | None = None
    one_click_headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """Veri güvenliğini ve SPK uyumluluğunu doğrula."""
        if not self.alert_id or not self.alert_id.strip():
            raise SensitiveDataLeakError("Uyarı kimliği (alert_id) boş olamaz.")

        clean_symbol = self.symbol.strip().upper()
        if not clean_symbol:
            raise SensitiveDataLeakError("Sembol boş olamaz.")
        object.__setattr__(self, "symbol", clean_symbol)

        clean_rule_name = self.rule_name.strip()
        if not clean_rule_name:
            raise SensitiveDataLeakError("Kural adı boş olamaz.")
        validate_compliant_text(clean_rule_name)
        object.__setattr__(self, "rule_name", clean_rule_name)

        clean_summary = self.condition_summary.strip()
        if not clean_summary:
            raise SensitiveDataLeakError("Koşul özeti boş olamaz.")
        validate_compliant_text(clean_summary)
        object.__setattr__(self, "condition_summary", clean_summary)

        if not self.data_source or not self.data_source.strip():
            raise SensitiveDataLeakError("Veri kaynağı boş olamaz.")

        if not self.recipient_email or "@" not in self.recipient_email:
            raise SensitiveDataLeakError("Geçerli bir alıcı e-posta adresi gereklidir.")

        # Ekstra meta veride yasaklı hassas finansal veya hesap alanlarını denetle
        if self.extra_metadata:
            for key in self.extra_metadata:
                lower_key = str(key).strip().lower()
                if lower_key in FORBIDDEN_FINANCIAL_FIELDS:
                    raise SensitiveDataLeakError(
                        f"E-posta bildiriminde yasaklı hassas hesap/finansal alan tespit edildi: "
                        f"'{key}'. Hassas hesap bakiyesi veya kimlik verisi dahil edilemez."
                    )


@dataclass(frozen=True)
class FormattedEmailNotification:
    """Formatlanmış, sızıntı denetiminden geçmiş e-posta çıktısı."""

    subject: str
    plaintext_body: str
    html_body: str
    recipient_email: str
    masked_recipient: str
    symbol: str
    alert_id: str
    unsubscribe_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class AlertEmailSanitizer:
    """E-posta gövdesi ve başlığında hassas hesap ve finansal veri tarayıcısı."""

    @classmethod
    def scan_for_leaks(cls, text: str) -> None:
        """Metinde hassas anahtar kelime, IBAN veya sır kalıplarını denetle."""
        # Meşru tek tıkla abonelikten çıkma URL'lerindeki query parametrelerini
        # sır taramasından muaf tut
        clean_text = re.sub(r"https?://[^\s<>\"']+/unsubscribe[^\s<>\"']*", "", text)
        if _SECRET_KEYWORD_PATTERN.search(clean_text):
            raise SensitiveDataLeakError(
                "E-posta metninde parola, token veya gizli anahtar kalıbı tespit edildi."
            )
        if _ACCOUNT_FINANCIAL_KEYWORD_PATTERN.search(text):
            raise SensitiveDataLeakError(
                "E-posta metninde bakiye veya portföy gibi gereksiz hassas finansal veri "
                "tespit edildi."
            )
        if _IBAN_PATTERN.search(text):
            raise SensitiveDataLeakError("E-posta metninde IBAN numarası tespit edildi.")
        if _CREDIT_CARD_PATTERN.search(text):
            raise SensitiveDataLeakError("E-posta metninde kart numarası deseni tespit edildi.")

    @classmethod
    def sanitize_user_display_name(cls, name: str | None) -> str:
        """Kullanıcı adını temizle ve gereksiz kişisel bilgileri çıkar."""
        if not name or not name.strip():
            return "Değerli Kullanıcımız"
        cleaned = re.sub(r"[<>\"\'&;]", "", name.strip())
        cls.scan_for_leaks(cleaned)
        return cleaned[:50]


class AlertEmailComposer:
    """SPK uyumlu, güvenli ve sade trend uyarı e-postası üreticisi."""

    def __init__(self, sanitizer: AlertEmailSanitizer | None = None) -> None:
        self.sanitizer = sanitizer or AlertEmailSanitizer()

    def compose(self, payload: AlertNotificationPayload) -> FormattedEmailNotification:
        """Güvenlik ve mevzuat kontrollerini uygulayarak e-posta içeriği üret."""
        # 1. Başlık ve konu üretimi
        subject = f"[Bora AI Trend Uyarısı] {payload.symbol}: {payload.rule_name}"
        validate_compliant_text(subject)
        self.sanitizer.scan_for_leaks(subject)

        # 2. Zaman damgası ve gecikme metni
        ts_utc = payload.data_timestamp_utc
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=UTC)
        formatted_time = ts_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

        data_status_str = "Güncel Piyasa Verisi (EOD)"
        if payload.is_delayed:
            delay_detail = payload.delay_description or "Gecikmeli Veri"
            data_status_str = f"Dikkat: {delay_detail}"

        greeting_name = self.sanitizer.sanitize_user_display_name(payload.user_display_name)
        masked_recipient = mask_email(payload.recipient_email)
        is_bullish = is_bullish_alert_payload(payload)

        # 3. Düz Metin (Plaintext) Gövdesi
        plaintext_lines = [
            f"Merhaba {greeting_name},",
            "",
            f"İzleme listenizdeki '{payload.symbol}' sembolü için trend kuralı karşılandı.",
            "",
            "BİLDİRİM DETAYLARI:",
            f"  • Sembol: {payload.symbol}",
            f"  • Kural: {payload.rule_name}",
            f"  • Durum: {payload.condition_summary}",
            f"  • Veri Zamanı: {formatted_time}",
            f"  • Veri Kaynağı: {payload.data_source}",
            f"  • Veri Tazeliği: {data_status_str}",
            "",
            "GÜVENLİK VE GİZLİLİK NOTU:",
            "Bu bildirim şifrenizi, hesap bakiyenizi, portföy tutarınızı veya kişisel finansal",
            "verilerinizi kesinlikle içermez.",
        ]
        if is_bullish:
            plaintext_lines.extend(
                [
                    "",
                    "----------------------------------------------------------------------",
                    "DİKKAT - YÜKSELİŞ TRENDİ YASAL UYARISI:",
                    BULLISH_TREND_DISCLAIMER_FULL,
                    "",
                    f"RİSK NOTU: {BULLISH_TREND_SHORT_RISK_NOTE}",
                    "----------------------------------------------------------------------",
                ]
            )
        plaintext_lines.extend(
            [
                "",
                "----------------------------------------------------------------------",
                LEGAL_DISCLAIMER_ALERT_EMAIL,
                "----------------------------------------------------------------------",
                "",
                "Bildirim tercihlerinizi yönetmek için hesap ayarlarınızı ziyaret edebilirsiniz.",
            ]
        )
        if payload.unsubscribe_url:
            plaintext_lines.extend(
                [
                    "",
                    "ABONELİKTEN ÇIKMA:",
                    "Abonelikten tek tıkla çıkmak için aşağıdaki bağlantıyı kullanabilirsiniz:",
                    payload.unsubscribe_url,
                ]
            )
        plaintext_body = "\n".join(plaintext_lines)

        # Plaintext güvenlik taraması
        self.sanitizer.scan_for_leaks(plaintext_body)

        # 4. HTML Gövdesi (XSS korumalı, responsive ve sade)
        safe_symbol = html.escape(payload.symbol)
        safe_rule_name = html.escape(payload.rule_name)
        safe_condition = html.escape(payload.condition_summary)
        safe_source = html.escape(payload.data_source)
        safe_status = html.escape(data_status_str)
        safe_greeting = html.escape(greeting_name)
        safe_disclaimer = html.escape(LEGAL_DISCLAIMER_ALERT_EMAIL)

        safe_unsubscribe_html = ""
        if payload.unsubscribe_url:
            safe_unsub_url = html.escape(payload.unsubscribe_url)
            safe_unsubscribe_html = (
                f'<p style="margin: 10px 0 0 0; text-align: center; font-size: 11px;">'
                f"Bu uyarı e-postalarını almak istemiyorsanız "
                f'<a href="{safe_unsub_url}" style="color: #2563eb; text-decoration: underline;">'
                f"tek tıkla abonelikten çıkabilirsiniz</a>.</p>"
            )

        safe_bullish_warning_html = ""
        if is_bullish:
            safe_disclaimer_text = html.escape(BULLISH_TREND_DISCLAIMER_FULL)
            safe_risk_note = html.escape(BULLISH_TREND_SHORT_RISK_NOTE)
            safe_bullish_warning_html = (
                '\n      <div style="background-color: #fffbeb; border: 1px solid #fef3c7; '
                "border-left: 4px solid #f59e0b; padding: 14px 16px; margin: 18px 0; "
                'border-radius: 6px;">\n'
                '        <div style="font-weight: 700; color: #92400e; font-size: 13px; '
                'margin-bottom: 6px;">\n'
                "          ⚠️ YÜKSELİŞ TRENDİ YASAL UYARISI (AL/SAT TAVSİYESİ DEĞİLDİR)\n"
                "        </div>\n"
                f'        <p style="margin: 0 0 6px 0; font-size: 12px; color: #78350f; '
                f'line-height: 1.5;">{safe_disclaimer_text}</p>\n'
                f'        <p style="margin: 0; font-size: 11px; font-weight: 600; '
                f'color: #b45309;">{safe_risk_note}</p>\n'
                "      </div>"
            )

        css_style = (
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', "
            "Roboto, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; "
            "padding: 24px; line-height: 1.5; }\n"
            ".container { max-width: 600px; margin: 0 auto; background: #ffffff; "
            "border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; }\n"
            ".header { background-color: #0f172a; color: #ffffff; padding: 20px 24px; }\n"
            ".header h1 { margin: 0; font-size: 18px; font-weight: 600; }\n"
            ".content { padding: 24px; }\n"
            ".badge { display: inline-block; padding: 4px 10px; font-size: 12px; "
            "font-weight: 600; border-radius: 4px; background: #e0f2fe; color: #0369a1; }\n"
            ".badge.delayed { background: #fef3c7; color: #92400e; }\n"
            ".detail-table { width: 100%; border-collapse: collapse; margin: 16px 0; "
            "font-size: 14px; }\n"
            ".detail-table td { padding: 10px 12px; border-bottom: 1px solid #f1f5f9; }\n"
            ".detail-table td.label { color: #64748b; font-weight: 500; width: 35%; }\n"
            ".detail-table td.val { color: #0f172a; font-weight: 600; }\n"
            ".privacy-notice { background-color: #f8fafc; border-left: 3px solid #94a3b8; "
            "padding: 12px; font-size: 12px; color: #475569; margin: 20px 0; }\n"
            ".footer { background-color: #f8fafc; border-top: 1px solid #e2e8f0; "
            "padding: 20px 24px; font-size: 11px; color: #64748b; text-align: justify; }"
        )

        badge_class = "badge delayed" if payload.is_delayed else "badge"

        html_body = f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(subject)}</title>
  <style>
{css_style}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Bora AI Piyasa Trend Uyarısı</h1>
    </div>
    <div class="content">
      <p style="margin-top:0;">Merhaba {safe_greeting},</p>
      <p>İzleme listenizdeki <strong>{safe_symbol}</strong> hissesi için teknik koşul sağlandı.</p>
{safe_bullish_warning_html}
      <span class="{badge_class}">{safe_status}</span>

      <table class="detail-table">
        <tr>
          <td class="label">Sembol</td>
          <td class="val">{safe_symbol}</td>
        </tr>
        <tr>
          <td class="label">Kural Adı</td>
          <td class="val">{safe_rule_name}</td>
        </tr>
        <tr>
          <td class="label">Tetiklenen Koşul</td>
          <td class="val">{safe_condition}</td>
        </tr>
        <tr>
          <td class="label">Veri Zamanı</td>
          <td class="val">{formatted_time}</td>
        </tr>
        <tr>
          <td class="label">Veri Kaynağı</td>
          <td class="val">{safe_source}</td>
        </tr>
      </table>

      <div class="privacy-notice">
        <strong>Gizlilik Güvencesi:</strong> Bu bildirim e-postasında şifreniz, hesap bakiyeniz,
        portföy tutarınız veya kişisel finansal bilgileriniz asla yer almaz.
      </div>
    </div>
    <div class="footer">
      <p style="margin: 0 0 10px 0;">{safe_disclaimer}</p>
      <p style="margin: 0; text-align: center; color: #94a3b8;">
        Bildirim tercihlerini güncellemek için hesap panelinizi ziyaret edebilirsiniz.
      </p>
      {safe_unsubscribe_html}
    </div>
  </div>
</body>
</html>"""

        # HTML güvenlik taraması
        self.sanitizer.scan_for_leaks(html_body)

        headers: dict[str, str] = {}
        if payload.one_click_headers:
            headers.update(payload.one_click_headers)
        elif payload.unsubscribe_url:
            headers["List-Unsubscribe"] = f"<{payload.unsubscribe_url}>"
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        notification = FormattedEmailNotification(
            subject=subject,
            plaintext_body=plaintext_body,
            html_body=html_body,
            recipient_email=payload.recipient_email,
            masked_recipient=masked_recipient,
            symbol=payload.symbol,
            alert_id=payload.alert_id,
            unsubscribe_url=payload.unsubscribe_url,
            headers=headers,
        )
        assert_bullish_alert_has_disclaimer(notification)
        return notification
