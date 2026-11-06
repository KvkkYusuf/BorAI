"""FAZ-12: Uyarı gecikmesini ölçme ve sağlayıcı verisi gecikmişse bildirimde belirtme testleri."""

from datetime import UTC, datetime, timedelta

import pytest

from stock_analysis.alerts.email_notifier import AlertEmailComposer, AlertNotificationPayload
from stock_analysis.alerts.latency import (
    LEGAL_DISCLAIMER_LATENCY,
    DataFreshnessStatus,
    LatencyEvaluationResult,
    LatencyEvaluator,
    ProviderLatencyConfig,
    apply_latency_to_payload,
)


@pytest.fixture
def evaluator() -> LatencyEvaluator:
    """Varsayılan yapılandırmalı gecikme değerlendirici."""
    return LatencyEvaluator(
        ProviderLatencyConfig(
            expected_delay_minutes=15.0,
            realtime_tolerance_seconds=120.0,
            stale_threshold_minutes=35.0,
            provider_name="Borsa Istanbul",
        )
    )


@pytest.fixture
def base_payload() -> AlertNotificationPayload:
    """Örnek temel uyarı bildirimi yükü."""
    return AlertNotificationPayload(
        alert_id="alt-latency-test-1",
        symbol="THYAO",
        rule_name="RSI 30 Aşırı Satım Seviyesi",
        condition_summary="RSI(14) 28.4 seviyesinde, aşırı satım eşiğinin altında",
        data_timestamp_utc=datetime(2026, 10, 24, 15, 30, 0, tzinfo=UTC),
        data_source="BIST Seans İçi",
        recipient_email="yatirimci@example.com",
    )


def test_realtime_freshness(evaluator: LatencyEvaluator) -> None:
    """Tolerans dahilindeki veri anlık (REALTIME) ve gecikmesiz olarak değerlendirilir."""
    now = datetime(2026, 10, 24, 15, 31, 0, tzinfo=UTC)  # 60 saniye fark
    data_ts = datetime(2026, 10, 24, 15, 30, 0, tzinfo=UTC)

    result = evaluator.evaluate(
        data_timestamp_utc=data_ts,
        evaluated_at_utc=now,
        is_market_open=True,
    )

    assert isinstance(result, LatencyEvaluationResult)
    assert result.status == DataFreshnessStatus.REALTIME
    assert result.is_delayed is False
    assert result.latency_seconds == 60.0
    assert result.latency_minutes == 1.0
    assert "Güncel piyasa verisi" in result.delay_description
    assert "anlık/eşzamanlı" in result.provider_notice


def test_standard_delayed_provider(evaluator: LatencyEvaluator) -> None:
    """15 dakikalık lisanslı BIST gecikmesi DELAYED olarak etiketlenir ve uyarısı üretilir."""
    data_ts = datetime(2026, 10, 24, 15, 0, 0, tzinfo=UTC)
    now = datetime(2026, 10, 24, 15, 16, 30, tzinfo=UTC)  # 16.5 dakika fark

    result = evaluator.evaluate(
        data_timestamp_utc=data_ts,
        evaluated_at_utc=now,
        is_market_open=True,
    )

    assert isinstance(result, LatencyEvaluationResult)
    assert result.status == DataFreshnessStatus.DELAYED
    assert result.is_delayed is True
    assert result.latency_minutes == 16.5
    assert "15 dakika gecikmeli veri" in result.delay_description
    assert "lisans/mevzuat kuralları uyarınca 15 dakika gecikmeli" in result.provider_notice


def test_stale_data_warning(evaluator: LatencyEvaluator) -> None:
    """35 dakikayı aşan veri STALE (bayat/akış kesintisi) olarak değerlendirilir."""
    data_ts = datetime(2026, 10, 24, 14, 0, 0, tzinfo=UTC)
    now = datetime(2026, 10, 24, 15, 0, 0, tzinfo=UTC)  # 60 dakika fark

    result = evaluator.evaluate(
        data_timestamp_utc=data_ts,
        evaluated_at_utc=now,
        is_market_open=True,
    )

    assert isinstance(result, LatencyEvaluationResult)
    assert result.status == DataFreshnessStatus.STALE
    assert result.is_delayed is True
    assert result.latency_minutes == 60.0
    assert "gecikmesi yüksek" in result.delay_description
    assert "akışında gecikme veya kesinti tespit edildi" in result.provider_notice


def test_market_closed_handling(evaluator: LatencyEvaluator) -> None:
    """Piyasa kapalıyken eski veri bayat değil kapanış verisi olarak etiketlenir."""
    data_ts = datetime(2026, 10, 24, 18, 0, 0, tzinfo=UTC)
    now = datetime(2026, 10, 25, 10, 0, 0, tzinfo=UTC)  # Ertesi gün / seans kapalı

    result = evaluator.evaluate(
        data_timestamp_utc=data_ts,
        evaluated_at_utc=now,
        is_market_open=False,
    )

    assert isinstance(result, LatencyEvaluationResult)
    assert result.status == DataFreshnessStatus.MARKET_CLOSED
    assert result.is_delayed is False
    assert "Piyasa kapalı" in result.delay_description
    assert "son resmi seans kapanış verisi" in result.provider_notice


def test_clock_skew_future_timestamp(evaluator: LatencyEvaluator) -> None:
    """Gelecek zaman damgalı veri durumunda eksi süre yerine 0 kabul edilir."""
    now = datetime(2026, 10, 24, 15, 0, 0, tzinfo=UTC)
    future_data = now + timedelta(seconds=15)

    result = evaluator.evaluate(data_timestamp_utc=future_data, evaluated_at_utc=now)

    assert result.latency_seconds == 0.0
    assert result.status == DataFreshnessStatus.REALTIME


def test_timezone_naive_conversion(evaluator: LatencyEvaluator) -> None:
    """Naive datetime girildiğinde UTC olarak normalize edilir."""
    naive_ts = datetime(2026, 10, 24, 15, 10, 0)  # tzinfo=None
    now_utc = datetime(2026, 10, 24, 15, 26, 0, tzinfo=UTC)

    result = evaluator.evaluate(data_timestamp_utc=naive_ts, evaluated_at_utc=now_utc)

    assert result.data_timestamp_utc.tzinfo == UTC
    assert result.status == DataFreshnessStatus.DELAYED


def test_apply_latency_to_payload_enrichment(
    evaluator: LatencyEvaluator, base_payload: AlertNotificationPayload
) -> None:
    """Gecikme değerlendirmesi AlertNotificationPayload modeline başarıyla aktarılır."""
    data_ts = datetime(2026, 10, 24, 15, 0, 0, tzinfo=UTC)
    now = datetime(2026, 10, 24, 15, 18, 0, tzinfo=UTC)

    eval_result = evaluator.evaluate(data_timestamp_utc=data_ts, evaluated_at_utc=now)
    enriched_payload = apply_latency_to_payload(base_payload, eval_result)

    assert enriched_payload.is_delayed is True
    assert enriched_payload.delay_description == eval_result.delay_description
    assert enriched_payload.symbol == base_payload.symbol
    assert enriched_payload.alert_id == base_payload.alert_id


def test_email_composer_reflects_latency_warning(
    evaluator: LatencyEvaluator, base_payload: AlertNotificationPayload
) -> None:
    """Gecikmeli veri bilgisi e-posta gövdesinde ve HTML badge'inde şeffafça gösterilir."""
    data_ts = datetime(2026, 10, 24, 14, 0, 0, tzinfo=UTC)
    now = datetime(2026, 10, 24, 15, 0, 0, tzinfo=UTC)  # STALE (60 dk)

    eval_result = evaluator.evaluate(data_timestamp_utc=data_ts, evaluated_at_utc=now)
    enriched_payload = apply_latency_to_payload(base_payload, eval_result)

    composer = AlertEmailComposer()
    email_notification = composer.compose(enriched_payload)

    # Plaintext kontrolleri
    assert "Veri Tazeliği: Dikkat:" in email_notification.plaintext_body
    assert "gecikmesi yüksek" in email_notification.plaintext_body

    # HTML kontrolleri
    assert 'class="badge delayed"' in email_notification.html_body
    assert "gecikmesi yüksek" in email_notification.html_body


def test_legal_disclaimer_present() -> None:
    """Yasal uyarı metninin tanımlı ve SPK standartlarına uygun olduğunu doğrula."""
    assert "YASAL UYARI:" in LEGAL_DISCLAIMER_LATENCY
    assert "yatırım tavsiyesi" in LEGAL_DISCLAIMER_LATENCY
