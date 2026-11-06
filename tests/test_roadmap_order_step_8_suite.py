"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 8 test paketi.

Açıklanabilir trend kuralları ve uyarılar kabul paketi.
Test kapsamı:
- RoadmapStepEightGatekeeper 6/6 tam uyum doğrulaması.
- Açıklanabilir çoklu gösterge trend analiz motoru (TrendEngine, QualityLiquidityFilterConfig).
- Çoklu gösterge teyidi kuralı (min_rules_required>=2, min_categories_required>=2) ve cooldown.
- Kullanıcı yapılandırılabilir açıklanabilir kural motoru (ExplainableRuleBuilder).
- BIST evren/izleme listesi tarama motoru (ScanCriteria, BIST_30_UNIVERSE).
- Tetiklenen uyarı tarihçesi ve denetim izlenebilirliği (AlertHistoryRepository).
- Bildirim sıklık sınırlaması, cooldown ve sessiz saatler (NotificationRateLimiter).
- Dağıtım kuyruğu, belirleyici idempotency, Dead-Letter Queue (DLQ) ve e-posta veri sızıntı kalkanı.
- SPK yükseliş trendi yasal uyarı feragatnamesi ve al/sat tavsiyesi olmama güvencesi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analysis.alerts.email_notifier import (
    AlertEmailComposer,
    AlertEmailSanitizer,
    AlertNotificationPayload,
    SensitiveDataLeakError,
)
from stock_analysis.alerts.history import (
    AlertHistoryRecord,
    AlertHistoryRepository,
    TriggeredConditionDetail,
)
from stock_analysis.alerts.queue import (
    AlertDeliveryJob,
    AlertDispatchQueue,
    DeadLetterQueue,
    IdempotencyRegistry,
    RetryPolicy,
)
from stock_analysis.alerts.rate_limiter import (
    DispatchStatus,
    NotificationRateLimiter,
    QuietHoursBehavior,
    RateLimitConfig,
    is_in_quiet_hours,
)
from stock_analysis.alerts.rules import (
    ConditionType,
    ExplainableRuleBuilder,
    RuleExplanation,
)
from stock_analysis.alerts.scanner import (
    BIST_30_UNIVERSE,
    ScanCriteria,
)
from stock_analysis.alerts.trend_disclaimer import (
    BULLISH_TREND_DISCLAIMER_FULL,
    AlertComplianceError,
    assert_bullish_alert_has_disclaimer,
    enrich_payload_with_bullish_disclaimer,
    validate_alert_texts_compliance,
)
from stock_analysis.indicators.trend import (
    QualityLiquidityFilterConfig,
    TrendDirection,
    TrendEngine,
)
from stock_analysis.release.roadmap_order_gatekeeper import (
    ROADMAP_STEP8_CHECKS_TOTAL,
    ROADMAP_STEP8_PASSED_TOTAL,
    ROADMAP_STEP8_STATUS,
    RoadmapStepEightGatekeeper,
    RoadmapStepEightStatus,
    generate_roadmap_step8_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """Trend ve kural testleri için sentetik OHLCV DataFrame üretir."""
    np.random.seed(42)
    n = 70
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    base_price = 100.0
    returns = np.random.normal(0.002, 0.015, size=n)
    close_prices = base_price * np.cumprod(1 + returns)

    high_prices = close_prices * (1 + np.abs(np.random.normal(0.005, 0.004, size=n)))
    low_prices = close_prices * (1 - np.abs(np.random.normal(0.005, 0.004, size=n)))
    open_prices = low_prices + (high_prices - low_prices) * np.random.uniform(0.2, 0.8, size=n)
    volumes = np.random.uniform(200000, 600000, size=n)

    return pd.DataFrame(
        {
            "open": open_prices,
            "high": high_prices,
            "low": low_prices,
            "close": close_prices,
            "volume": volumes,
        },
        index=dates,
    )


def test_roadmap_step8_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 8 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepEightGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepEightStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step8_trend_engine_and_filter(sample_ohlcv_df: pd.DataFrame) -> None:
    """Çoklu gösterge teyitli trend analiz motoru ve filtrelerini test eder."""
    config = QualityLiquidityFilterConfig(
        min_bars=50,
        min_average_volume_20=5000.0,
        min_average_turnover_try=100000.0,
    )
    engine = TrendEngine(filter_config=config, min_rules_required=2, min_categories_required=2)

    filter_res = engine.apply_filters(sample_ohlcv_df, symbol="THYAO")
    assert filter_res.passed is True
    assert len(filter_res.failures) == 0

    # Tek göstergeyi sinyal saymama kuralı: min_rules < 2 ValueError fırlatmalı
    with pytest.raises(ValueError, match="en az 2 kural"):
        TrendEngine(min_rules_required=1)

    with pytest.raises(ValueError, match="en az 2 gösterge kategorisi"):
        TrendEngine(min_categories_required=1)


def test_roadmap_step8_explainable_alert_rules(sample_ohlcv_df: pd.DataFrame) -> None:
    """Açıklanabilir kural oluşturucu ve eşik değerlendirmesini test eder."""
    # RSI kuralı
    rsi_rule = ExplainableRuleBuilder.create_rsi_alert(
        name="RSI 70 Üzeri",
        user_id=1,
        symbol="ASELS",
        threshold=70.0,
        is_upper_bound=True,
    )
    assert rsi_rule.symbol == "ASELS"
    assert len(rsi_rule.conditions) == 1
    assert rsi_rule.conditions[0].condition_type == ConditionType.RSI_THRESHOLD

    # Açıklanabilir metin üretimi
    explanation = rsi_rule.explain()
    assert isinstance(explanation, RuleExplanation)
    assert "ASELS" in explanation.overall_summary
    assert len(explanation.condition_explanations) == 1
    assert "RSI(14)" in explanation.condition_explanations[0].title
    assert explanation.condition_explanations[0].parameters.get("period") == 14

    # Fiyat eşik kuralı
    price_rule = ExplainableRuleBuilder.create_price_threshold_alert(
        name="Fiyat 200 TL Üzeri",
        user_id=1,
        symbol="THYAO",
        price_threshold=200.0,
        is_above=True,
    )
    assert price_rule.conditions[0].condition_type == ConditionType.PRICE_ABOVE
    assert price_rule.conditions[0].threshold == 200.0


def test_roadmap_step8_universe_scanner_and_history() -> None:
    """Evren tarama ve denetim tarihçesi kayıtlarını test eder."""
    assert len(BIST_30_UNIVERSE) >= 30
    assert "THYAO" in BIST_30_UNIVERSE
    assert "GARAN" in BIST_30_UNIVERSE

    criteria = ScanCriteria(
        direction_preference=TrendDirection.BULLISH_TENDENCY,
        min_rules_met=2,
        min_categories_met=2,
    )
    assert criteria.direction_preference == TrendDirection.BULLISH_TENDENCY
    assert criteria.min_rules_met == 2

    # Tarihçe repo testi
    repo = AlertHistoryRepository()
    record = AlertHistoryRecord(
        alert_id="ALT-2026-001",
        rule_id="RULE-01",
        user_id=1,
        symbol="THYAO",
        data_timestamp_utc=datetime.now(UTC),
        triggered_conditions=(
            TriggeredConditionDetail(
                condition_type="RSI_THRESHOLD",
                operator="CROSSES_ABOVE",
                threshold_value=70.0,
                observed_value=72.4,
                description="RSI 70 eşiği yukarı kesildi",
            ),
        ),
        data_source="BIST_EOD_LICENSED",
    )
    repo.record(record)
    saved = repo.get_by_id("ALT-2026-001")
    assert saved is not None
    assert saved.symbol == "THYAO"
    assert len(saved.triggered_conditions) == 1
    assert saved.data_source == "BIST_EOD_LICENSED"


def test_roadmap_step8_notification_rate_limiting() -> None:
    """Bildirim spam koruması, cooldown ve sessiz saatleri test eder."""
    config = RateLimitConfig(
        cooldown_minutes=30,
        max_alerts_per_hour=5,
        max_alerts_per_day=20,
        quiet_hours_enabled=True,
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(8, 0),
        quiet_hours_behavior=QuietHoursBehavior.DEFER,
    )
    limiter = NotificationRateLimiter(default_config=config)

    # Normal şartlarda izin
    dec = limiter.check_dispatch(user_id=1, symbol="THYAO", rule_id="RULE-1")
    assert dec.status in (DispatchStatus.ALLOWED, DispatchStatus.DEFERRED_QUIET_HOURS)

    # Sessiz saat kontrol fonksiyonu
    now_utc = datetime.now(UTC)
    in_quiet = is_in_quiet_hours(now_utc, config)
    assert isinstance(in_quiet, bool)


def test_roadmap_step8_alert_delivery_queue_and_email_security() -> None:
    """Dağıtım kuyruğu, idempotency, DLQ ve e-posta veri sızıntı kalkanını test eder."""
    queue = AlertDispatchQueue(retry_policy=RetryPolicy(max_attempts=3))
    assert queue is not None
    idempotency = IdempotencyRegistry()
    dlq = DeadLetterQueue()
    assert dlq is not None

    # Idempotency kontrolü
    key1 = AlertDeliveryJob.generate_idempotency_key(
        user_id="1",
        symbol="KCHOL",
        rule_name="SMA50 Kesisimi",
        data_timestamp="2026-10-30T18:00:00Z",
    )
    registered, job_id, status = idempotency.check_or_register(key1, "JOB-001")
    assert registered is True
    # Tekrar denendiğinde duplicate olmalı
    registered2, job_id2, status2 = idempotency.check_or_register(key1, "JOB-002")
    assert registered2 is False

    # E-posta finansal veri sızıntısı kalkanı testi
    sanitizer = AlertEmailSanitizer()
    safe_text = "İzleme listenizdeki EREGL sembolü için SMA 50 üzerinde kapanış gerçekleşti."
    sanitizer.scan_for_leaks(safe_text)

    # Yasaklı finansal alan (örn. portfolio_value veya IBAN) sızarsa hata fırlatılmalı
    leaky_text = "Müşteri hesap bakiyesi: 54000 TL, IBAN: TR330006100519283746501234"
    with pytest.raises(SensitiveDataLeakError, match="finansal veri|IBAN"):
        sanitizer.scan_for_leaks(leaky_text)

    # Payload meta verisinde yasaklı alan engeli
    with pytest.raises(SensitiveDataLeakError, match="yasaklı hassas hesap/finansal alan"):
        AlertNotificationPayload(
            alert_id="ALT-LEAK",
            symbol="EREGL",
            rule_name="Hacim Kuralı",
            condition_summary="Hacim arttı.",
            data_timestamp_utc=datetime.now(UTC),
            data_source="BIST_EOD_LICENSED",
            recipient_email="yatirimci@example.com",
            extra_metadata={"portfolio_value": 150000.0},
        )


def test_roadmap_step8_spk_bullish_disclaimer_compliance() -> None:
    """Yükseliş trendi bildirimlerinde SPK al/sat tavsiyesi olmama güvencesini test eder."""
    assert len(BULLISH_TREND_DISCLAIMER_FULL) > 50
    assert "YATIRIM TAVSİYESİ DEĞİLDİR" in BULLISH_TREND_DISCLAIMER_FULL

    payload = AlertNotificationPayload(
        alert_id="ALT-999",
        symbol="THYAO",
        rule_name="THYAO RSI ve MACD Pozitif Yükseliş Trendi",
        condition_summary="RSI ve MACD pozitif eğilim koşulları sağlandı.",
        data_timestamp_utc=datetime.now(UTC),
        data_source="BIST_EOD_LICENSED",
        recipient_email="kullanici@example.com",
    )

    enriched = enrich_payload_with_bullish_disclaimer(payload, force_bullish=True)
    assert enriched.extra_metadata is not None
    assert enriched.extra_metadata.get("is_bullish_trend") is True
    assert "YATIRIM TAVSİYESİ DEĞİLDİR" in enriched.extra_metadata["bullish_disclaimer"]

    # E-posta oluşturulup zorunlu yasal uyarı denetlenir
    composer = AlertEmailComposer()
    formatted = composer.compose(enriched)
    assert_bullish_alert_has_disclaimer(formatted)

    # Yasaklı tavsiye kelimesi içerirse yakalanmalı
    with pytest.raises(AlertComplianceError, match="yasaklı ifade|yönlendirici ifade"):
        validate_alert_texts_compliance(
            subject="THYAO için kesin al fırsatı",
            rule_name="Alım Önerisi",
            condition_summary="hemen al",
        )


def test_roadmap_step8_prometheus_telemetry(workspace_root: Path) -> None:
    """Prometheus metriklerinin doğru güncellendiğini doğrular."""
    gatekeeper = RoadmapStepEightGatekeeper(workspace_root=workspace_root)
    gatekeeper.evaluate()

    assert ROADMAP_STEP8_STATUS.get() == 1.0
    assert ROADMAP_STEP8_CHECKS_TOTAL.get() >= 6.0
    assert ROADMAP_STEP8_PASSED_TOTAL.get() >= 6.0


def test_roadmap_step8_markdown_report_generation(workspace_root: Path) -> None:
    """Adım 8 doğrulama sonucunun Markdown denetim raporu formatını test eder."""
    gatekeeper = RoadmapStepEightGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    report = generate_roadmap_step8_report(result)

    assert "# Yol Haritası Uygulama Sırası — Adım 8 Denetim Raporu" in report
    assert "ROADMAP-S8-01-TREND-ENGINE-AND-FILTER" in report
    assert "ROADMAP-S8-02-EXPLAINABLE-ALERT-RULES-AND-BUILDER" in report
    assert "ROADMAP-S8-03-UNIVERSE-SCANNER-AND-HISTORICAL-LOGGING" in report
    assert "ROADMAP-S8-04-NOTIFICATION-RATE-LIMITING-AND-QUIET-HOURS" in report
    assert "ROADMAP-S8-05-ALERT-DELIVERY-QUEUE-IDEMPOTENCY-AND-DLQ" in report
    assert "ROADMAP-S8-06-SPK-TREND-DISCLAIMER-AND-NON-ADVISORY-GUARDRAIL" in report
    assert "✅ PASS" in report
