"""FAZ-12: “Yükseliş trendi” uyarısının al/sat tavsiyesi olmadığını açıkça belirtme testleri."""

from datetime import UTC, datetime

import pytest

from stock_analysis.alerts.email_notifier import (
    AlertEmailComposer,
    AlertNotificationPayload,
    FormattedEmailNotification,
)
from stock_analysis.alerts.rules import (
    AlertCondition,
    ConditionOperator,
    ConditionType,
    ExplainableRuleBuilder,
)
from stock_analysis.alerts.trend_disclaimer import (
    BULLISH_TREND_DISCLAIMER_FULL,
    BULLISH_TREND_SHORT_RISK_NOTE,
    AlertComplianceError,
    assert_bullish_alert_has_disclaimer,
    enrich_explanation_with_bullish_disclaimer,
    enrich_payload_with_bullish_disclaimer,
    is_bullish_alert_payload,
    is_bullish_condition,
    is_bullish_rule,
    is_bullish_text,
    validate_alert_texts_compliance,
)


def test_bullish_text_detection() -> None:
    """Yükseliş ve boğa eğilimi terimlerinin tespiti."""
    assert is_bullish_text("BIST 100 Yükseliş Trendi Tespiti") is True
    assert is_bullish_text("SMA20/SMA50 Yukarı Kesişimi (Golden Cross)") is True
    assert is_bullish_text("Günlük Pozitif Değişim Eşiği") is True
    assert is_bullish_text("Fiyat 20 Günlük Ortalamanın Üzerinde") is True
    assert is_bullish_text("Düşüş Eşiği ve Aşırı Satım Bölgesi") is False
    assert is_bullish_text("RSI 30 Seviyesi Altı") is False
    assert is_bullish_text(None) is False


def test_bullish_condition_detection() -> None:
    """Teknik koşul bazında yukarı/yükseliş yönü tespiti."""
    # Fiyat üzeri -> Bullish
    c1 = AlertCondition(
        condition_type=ConditionType.PRICE_ABOVE,
        operator=ConditionOperator.GREATER_THAN,
        threshold=250.0,
    )
    assert is_bullish_condition(c1) is True

    # Fiyat altı -> Bearish
    c2 = AlertCondition(
        condition_type=ConditionType.PRICE_BELOW,
        operator=ConditionOperator.LESS_THAN,
        threshold=200.0,
    )
    assert is_bullish_condition(c2) is False

    # Pozitif fiyat değişimi -> Bullish
    c3 = AlertCondition(
        condition_type=ConditionType.PRICE_CHANGE_PCT,
        operator=ConditionOperator.GREATER_THAN,
        threshold=3.5,
    )
    assert is_bullish_condition(c3) is True

    # Negatif fiyat değişimi -> Bearish
    c4 = AlertCondition(
        condition_type=ConditionType.PRICE_CHANGE_PCT,
        operator=ConditionOperator.LESS_THAN,
        threshold=-4.0,
    )
    assert is_bullish_condition(c4) is False

    # MA Yukarı Kesişim (Golden cross) -> Bullish
    c5 = AlertCondition(
        condition_type=ConditionType.MA_CROSSOVER,
        operator=ConditionOperator.CROSSES_ABOVE,
        threshold=0.0,
        period=20,
        secondary_period=50,
    )
    assert is_bullish_condition(c5) is True

    # MA Aşağı Kesişim (Death cross) -> Bearish
    c6 = AlertCondition(
        condition_type=ConditionType.MA_CROSSOVER,
        operator=ConditionOperator.CROSSES_BELOW,
        threshold=0.0,
        period=20,
        secondary_period=50,
    )
    assert is_bullish_condition(c6) is False


def test_bullish_rule_detection_and_explanation() -> None:
    """Kural yapılandırmasında yükseliş tespiti ve açıklanabilirlik feragatnamesi."""
    rule_bullish = ExplainableRuleBuilder.create_ma_crossover_alert(
        name="SMA Golden Cross Yükseliş Eğilimi",
        user_id=101,
        symbol="THYAO",
        fast_period=20,
        slow_period=50,
        bullish_cross=True,
    )
    assert is_bullish_rule(rule_bullish) is True

    # Explain çıktısında feragatname zenginleştirmesi kontrolü
    base_explanation = rule_bullish.explain()
    explanation = enrich_explanation_with_bullish_disclaimer(base_explanation)
    assert "AL, SAT veya YATIRIM TAVSİYESİ DEĞİLDİR" in explanation.legal_disclaimer
    assert "anapara kaybı riski" in explanation.legal_disclaimer
    assert "Kural Feragatnamesi:" in explanation.legal_disclaimer

    # Ayı/düşüş kuralı zenginleştirmesi
    rule_bearish = ExplainableRuleBuilder.create_price_threshold_alert(
        name="Taban Fiyat Destek Altı",
        user_id=101,
        symbol="ASELS",
        price_threshold=50.0,
        is_above=False,
    )
    assert is_bullish_rule(rule_bearish) is False
    explanation_bearish = enrich_explanation_with_bullish_disclaimer(rule_bearish.explain())
    assert "ÖNEMLİ YASAL UYARI: 'Yükseliş trendi'" not in explanation_bearish.legal_disclaimer


def test_enrich_payload_with_bullish_disclaimer() -> None:
    """Bildirim yükünün yükseliş yasal feragatnamesi ile zenginleştirilmesi."""
    payload = AlertNotificationPayload(
        alert_id="alt-bullish-99",
        symbol="KCHOL",
        rule_name="Hızlı Yükseliş Trendi Tespiti",
        condition_summary="Son 3 barda fiyat artış eğilimi gerçekleşti",
        data_timestamp_utc=datetime(2026, 10, 24, 18, 45, 0, tzinfo=UTC),
        data_source="BIST EOD",
        recipient_email="yatirimci@example.com",
    )
    assert is_bullish_alert_payload(payload) is True

    enriched = enrich_payload_with_bullish_disclaimer(payload)
    assert enriched.extra_metadata is not None
    assert enriched.extra_metadata["is_bullish_trend"] is True
    assert enriched.extra_metadata["bullish_disclaimer"] == BULLISH_TREND_DISCLAIMER_FULL
    assert enriched.extra_metadata["risk_note"] == BULLISH_TREND_SHORT_RISK_NOTE
    assert "Yatırım Tavsiyesi Değildir" in enriched.extra_metadata["badge_label"]


def test_email_composer_renders_bullish_disclaimer() -> None:
    """Yükseliş uyarısı e-postasında zorunlu yasal uyarı kutusu ve risk metinleri yer almalıdır."""
    payload = AlertNotificationPayload(
        alert_id="alt-trend-100",
        symbol="BIMAS",
        rule_name="SMA20 Yukarı Kesişimi",
        condition_summary="Fiyat 20 günlük hareketli ortalamanın üzerine çıktı",
        data_timestamp_utc=datetime(2026, 10, 24, 17, 0, 0, tzinfo=UTC),
        data_source="BIST EOD",
        recipient_email="yatirimci@example.com",
        user_display_name="Cemil Bey",
    )
    composer = AlertEmailComposer()
    result = composer.compose(payload)

    # 1. Plaintext kontrolleri
    assert "DİKKAT - YÜKSELİŞ TRENDİ YASAL UYARISI:" in result.plaintext_body
    assert "AL, SAT veya YATIRIM TAVSİYESİ DEĞİLDİR" in result.plaintext_body
    assert "RİSK NOTU:" in result.plaintext_body
    assert "sermaye kaybı ihtimali içerir" in result.plaintext_body

    # 2. HTML kontrolleri
    assert "⚠️ YÜKSELİŞ TRENDİ YASAL UYARISI (AL/SAT TAVSİYESİ DEĞİLDİR)" in result.html_body
    assert "Sermaye piyasalarında fiyat dalgalanmaları" in result.html_body
    assert "anapara kaybı riski mevcuttur" in result.html_body
    assert "background-color: #fffbeb" in result.html_body  # Dikkat çekici uyarı kutusu rengi

    # 3. Otomatik feragatname güvencesi doğrulaması
    assert_bullish_alert_has_disclaimer(result)


def test_assert_bullish_alert_has_disclaimer_raises_on_missing_disclaimer() -> None:
    """Eğer bir yükseliş uyarısı yasal feragatnameyi içermezse assertion hatası fırlatılmalıdır."""
    # Sahte/hatalı formatlanmış e-posta (yükseliş konusu var ama al/sat uyarısı eksik)
    flawed_email = FormattedEmailNotification(
        subject="[Bora AI Trend Uyarısı] TUPRS: Güçlü Yükseliş Trendi",
        plaintext_body="TUPRS için yükseliş trendi kuralı gerçekleşti. Mutlu günler.",
        html_body="<p>TUPRS yükseliş trendi gerçekleşti.</p>",
        recipient_email="test@example.com",
        masked_recipient="t***@example.com",
        symbol="TUPRS",
        alert_id="alt-flawed",
    )

    with pytest.raises(AssertionError, match="açık 'al/sat tavsiyesi değildir' uyarısı bulunamadı"):
        assert_bullish_alert_has_disclaimer(flawed_email)


def test_validate_alert_texts_compliance_blocks_forbidden_terms() -> None:
    """Kullanıcı ve kural metinlerindeki yatırım tavsiyesi ve kesinlik ifadeleri engellenmelidir."""
    # Başarılı durum
    validate_alert_texts_compliance(
        subject="[Bora AI Trend] THYAO: SMA20 Eğilim Koşulu",
        rule_name="SMA20 Yukarı Kesme Koşulu",
        condition_summary="20 günlük ortalama eşiği sağlandı.",
    )

    # "hedef fiyat" içeren kural engellenmeli
    with pytest.raises(AlertComplianceError, match="hedef fiyat"):
        validate_alert_texts_compliance(
            subject="THYAO Uyarısı",
            rule_name="Hedef Fiyat 450 TL Takip",
            condition_summary="Koşul sağlandı",
        )

    # "kesin kazanç" veya "garanti" engellenmeli
    with pytest.raises(AlertComplianceError, match="garanti"):
        validate_alert_texts_compliance(
            subject="Garanti Kazanç Uyarısı",
            rule_name="RSI 30",
            condition_summary="Koşul sağlandı",
        )

    # "şimdi al" veya "al" engellenmeli
    with pytest.raises(AlertComplianceError, match=r"(yasaklı ifade|al)"):
        validate_alert_texts_compliance(
            subject="Alarm",
            rule_name="Kural",
            condition_summary="Bu seviyeden şimdi al tavsiyesidir",
        )
