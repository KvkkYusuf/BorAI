"""“Yükseliş trendi” ve teknik koşul bildirimlerinde al/sat tavsiyesi olmama güvencesi.

Bu modül, FAZ-12 ve SPK mevzuatı (COMPLIANCE_TR.md madde 4) gereksinimlerine uygundur:
- "Yükseliş trendi", boğa eğilimi veya yukarı yönlü teknik koşulların sağlandığı tüm uyarılarda
  bunun KESİNLİKLE bir alım/satım, yönlendirme veya yatırım tavsiyesi OLMADIĞINI açıkça belirtir.
- Her yükseliş uyarısına standart risk notu ve anapara kaybı ihtimali uyarısını iliştirir.
- Kullanıcı ve sistem tarafından üretilen uyarı metinlerini SPK kırmızı çizgileri ve
  yasaklı tavsiye ifadelerine ("şimdi al", "kesin", "garanti", "hedef fiyat" vb.) karşı doğrular.
- E-posta (HTML/Plaintext) ve arayüz bildirim çıktılarında yasal feragatnamenin varlığını denetler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stock_analysis.indicators.trend import validate_compliant_text

if TYPE_CHECKING:
    from stock_analysis.alerts.email_notifier import (
        AlertNotificationPayload,
        FormattedEmailNotification,
    )
    from stock_analysis.alerts.rules import (
        AlertCondition,
        AlertRuleConfig,
        RuleExplanation,
    )

# SPK uyumlu kapsamlı yükseliş trendi yasal feragatnamesi
BULLISH_TREND_DISCLAIMER_FULL: str = (
    "ÖNEMLİ YASAL UYARI: 'Yükseliş trendi' veya yukarı yönlü teknik koşulların "
    "sağlanmış olması, yalnızca geçmiş piyasa verilerine dayalı matematiksel/istatistiksel "
    "bir gösterge tespitidir; kesinlikle AL, SAT veya YATIRIM TAVSİYESİ DEĞİLDİR. "
    "Sermaye piyasalarında fiyat dalgalanmaları ve anapara kaybı riski mevcuttur; "
    "geçmiş veriler veya teknik modeller gelecekteki getirilerin garantisi olamaz."
)

# Kısa risk ve sorumluluk reddi notu (mobil / e-posta özeti)
BULLISH_TREND_SHORT_RISK_NOTE: str = (
    "Bu yükseliş trendi tespiti al/sat tavsiyesi değildir; piyasa riski ve "
    "sermaye kaybı ihtimali içerir."
)

# Başlık ve etiket göstergesi
BULLISH_TREND_BADGE_LABEL: str = "Teknik Eğilim Tespiti (Yatırım Tavsiyesi Değildir)"

# Yükseliş veya boğa eğilimi ima eden anahtar kelimeler
_BULLISH_KEYWORDS = frozenset(
    {
        "yükseliş",
        "yukarı",
        "yukari",
        "bullish",
        "boğa",
        "boga",
        "golden cross",
        "altın kesişim",
        "altin kesisim",
        "pozitif",
        "artan",
        "tırmanış",
        "tirmanis",
        "üzerinde",
        "uzerinde",
        "üstünde",
        "ustunde",
        "üzeri",
        "uzeri",
    }
)

# Yasaklı al-sat veya yatırım tavsiyesi ifadeleri (ek denetim listesi)
_FORBIDDEN_RECOMMENDATIONS = (
    "hedef fiyat",
    "şimdi al",
    "kesin kazanç",
    "garanti",
    "mutlaka al",
    "kaçırma",
    "tavan gidecek",
    "ralli garantisi",
    "riski yok",
    "risksiz",
    "al tavsiyesi",
    "sat tavsiyesi",
    "portföyüne ekle",
    "paranı yatır",
)


class AlertComplianceError(ValueError):
    """Uyarı metinlerinde mevzuat ihlali veya yasaklı tavsiye tespit edildiğinde fırlatılır."""


def is_bullish_text(text: str | None) -> bool:
    """Verilen metnin yükseliş veya boğa eğilimi içerip içermediğini tespit eder."""
    if not text:
        return False
    lower = text.lower()
    return any(keyword in lower for keyword in _BULLISH_KEYWORDS)


def is_bullish_condition(condition: AlertCondition) -> bool:
    """Bireysel uyarı koşulunun yukarı/yükseliş yönlü olup olmadığını belirler."""
    from stock_analysis.alerts.rules import ConditionOperator, ConditionType

    c_type = condition.condition_type
    op = condition.operator

    if c_type == ConditionType.PRICE_ABOVE:
        return True

    if c_type == ConditionType.PRICE_CHANGE_PCT:
        return condition.threshold > 0 or op in (
            ConditionOperator.GREATER_THAN,
            ConditionOperator.GREATER_THAN_OR_EQUAL,
        )

    if c_type == ConditionType.MA_CROSSOVER:
        return op == ConditionOperator.CROSSES_ABOVE

    if c_type == ConditionType.RSI_THRESHOLD:
        if op == ConditionOperator.CROSSES_ABOVE:
            return True
        if (
            op in (ConditionOperator.GREATER_THAN, ConditionOperator.GREATER_THAN_OR_EQUAL)
            and condition.threshold >= 50.0
        ):
            return True

    return False


def is_bullish_rule(rule: AlertRuleConfig) -> bool:
    """Kural yapılandırmasının yükseliş yönlü bir eğilimi izleyip izlemediğini belirler."""
    if is_bullish_text(rule.name):
        return True
    return any(is_bullish_condition(cond) for cond in rule.conditions)


def is_bullish_alert_payload(payload: AlertNotificationPayload) -> bool:
    """Bildirim yükünün yükseliş trendine ait olup olmadığını denetler."""
    if payload.extra_metadata:
        if payload.extra_metadata.get("is_bullish_trend") is True:
            return True
        direction = str(payload.extra_metadata.get("direction", "")).upper()
        if "BULLISH" in direction:
            return True

    if is_bullish_text(payload.rule_name) or is_bullish_text(payload.condition_summary):
        return True

    return False


def validate_alert_texts_compliance(
    subject: str,
    rule_name: str,
    condition_summary: str,
    extra_notes: list[str] | None = None,
) -> None:
    """Uyarı metinlerinin SPK yatırım danışmanlığı kırmızı çizgilerini ihlal etmediğini doğrular.

    Tüm metinleri hem standart validate_compliant_text'ten hem de ek tavsiye ifadelerinden geçirir.
    """
    fields_to_check = [
        ("Konu", subject),
        ("Kural Adı", rule_name),
        ("Koşul Özeti", condition_summary),
    ]
    if extra_notes:
        for idx, note in enumerate(extra_notes):
            fields_to_check.append((f"Ek Not {idx + 1}", note))

    for field_name, text in fields_to_check:
        if not text or not text.strip():
            continue

        # 1. Standart SPK kırmızı çizgi kontrolü
        try:
            validate_compliant_text(text)
        except ValueError as err:
            raise AlertComplianceError(
                f"{field_name} alanında yasaklı ifade tespit edildi: {err}"
            ) from err

        # 2. Ek yatırım danışmanlığı / garanti ifadeleri kontrolü
        lower_text = text.lower()
        for forbidden in _FORBIDDEN_RECOMMENDATIONS:
            if forbidden in lower_text:
                raise AlertComplianceError(
                    f"{field_name} alanında mevzuata aykırı yönlendirici ifade tespit edildi: "
                    f"'{forbidden}'"
                )


def enrich_payload_with_bullish_disclaimer(
    payload: AlertNotificationPayload,
    force_bullish: bool | None = None,
) -> AlertNotificationPayload:
    """Bildirim yükü yükseliş trendi içeriyorsa, yasal uyarı ve risk meta verilerini ekler."""
    from stock_analysis.alerts.email_notifier import AlertNotificationPayload

    is_bullish = force_bullish if force_bullish is not None else is_bullish_alert_payload(payload)

    new_metadata: dict[str, Any] = dict(payload.extra_metadata) if payload.extra_metadata else {}

    if is_bullish:
        new_metadata["is_bullish_trend"] = True
        new_metadata["bullish_disclaimer"] = BULLISH_TREND_DISCLAIMER_FULL
        new_metadata["risk_note"] = BULLISH_TREND_SHORT_RISK_NOTE
        new_metadata["badge_label"] = BULLISH_TREND_BADGE_LABEL
    else:
        new_metadata.setdefault("is_bullish_trend", False)

    return AlertNotificationPayload(
        alert_id=payload.alert_id,
        symbol=payload.symbol,
        rule_name=payload.rule_name,
        condition_summary=payload.condition_summary,
        data_timestamp_utc=payload.data_timestamp_utc,
        data_source=payload.data_source,
        recipient_email=payload.recipient_email,
        is_delayed=payload.is_delayed,
        delay_description=payload.delay_description,
        user_display_name=payload.user_display_name,
        extra_metadata=new_metadata,
        unsubscribe_url=payload.unsubscribe_url,
        one_click_headers=payload.one_click_headers,
    )


def enrich_explanation_with_bullish_disclaimer(
    explanation: RuleExplanation,
    is_bullish: bool | None = None,
) -> RuleExplanation:
    """Kural açıklamasını yükseliş eğilimi durumunda belirgin yasal uyarı ile günceller."""
    from stock_analysis.alerts.rules import RuleExplanation

    bullish = (
        is_bullish
        if is_bullish is not None
        else (
            is_bullish_text(explanation.rule_name) or is_bullish_text(explanation.overall_summary)
        )
    )

    if not bullish:
        return explanation

    disclaimer = (
        f"{BULLISH_TREND_DISCLAIMER_FULL}\n\nKural Feragatnamesi: {explanation.legal_disclaimer}"
    )

    return RuleExplanation(
        rule_name=explanation.rule_name,
        symbol=explanation.symbol,
        logical_operator=explanation.logical_operator,
        overall_summary=explanation.overall_summary,
        condition_explanations=explanation.condition_explanations,
        cooldown_info=explanation.cooldown_info,
        legal_disclaimer=disclaimer,
    )


def assert_bullish_alert_has_disclaimer(email_notification: FormattedEmailNotification) -> None:
    """Formatlanmış bildirim e-postasında zorunlu 'al/sat tavsiyesi değildir' uyarısını teyit eder.

    Eğer e-posta bir yükseliş uyarısı ise, hem düz metin hem de HTML gövdesinde:
    1. Açıkça 'al/sat tavsiyesi değildir' veya 'yatırım tavsiyesi değildir' ibaresi
    2. Sermaye kaybı veya risk uyarısı
    bulunmalıdır. Aksi halde AssertionError fırlatılır.
    """
    is_bullish = is_bullish_text(email_notification.subject) or is_bullish_text(
        email_notification.plaintext_body
    )

    if not is_bullish:
        return

    # Plaintext kontrolü
    plain = email_notification.plaintext_body.lower()
    has_plain_disclaimer = (
        "al/sat" in plain or "al-sat" in plain or "alım-satım" in plain or "alım/satım" in plain
    ) and (
        "tavsiyesi değildir" in plain
        or "tavsiyesi niteliğinde değildir" in plain
        or "danışmanlığı değildir" in plain
    )
    if not has_plain_disclaimer:
        raise AssertionError(
            "Yükseliş trendi e-posta düz metin gövdesinde açık 'al/sat tavsiyesi değildir' "
            "uyarısı bulunamadı."
        )

    # HTML kontrolü
    html_content = email_notification.html_body.lower()
    has_html_disclaimer = (
        "al/sat" in html_content
        or "al-sat" in html_content
        or "alım-satım" in html_content
        or "alım/satım" in html_content
    ) and (
        "tavsiyesi değildir" in html_content
        or "tavsiyesi niteliğinde değildir" in html_content
        or "danışmanlığı değildir" in html_content
    )
    if not has_html_disclaimer:
        raise AssertionError(
            "Yükseliş trendi e-posta HTML gövdesinde açık 'al/sat tavsiyesi değildir' "
            "uyarısı bulunamadı."
        )

    # Risk notu kontrolü
    if "risk" not in plain or "risk" not in html_content:
        raise AssertionError(
            "Yükseliş trendi e-posta gövdesinde zorunlu piyasa/anapara risk notu bulunamadı."
        )
