"""Gölge modu (Shadow Mode) takip ve değerlendirme testleri."""

from datetime import UTC, datetime, timedelta

import pytest

from stock_analysis.indicators.trend import PROHIBITED_TERMS, validate_compliant_text
from stock_analysis.models.shadow_mode import (
    DEFAULT_SHADOW_MODE_DISCLAIMER,
    ShadowModeConfig,
    ShadowModeError,
    ShadowModeTracker,
    ShadowPromotionDecision,
)


def test_shadow_mode_disclaimer_compliance() -> None:
    """Yasal uyarı metninin SPK uyumlu olduğunu doğrular."""
    validate_compliant_text(DEFAULT_SHADOW_MODE_DISCLAIMER)
    assert "SPK" in DEFAULT_SHADOW_MODE_DISCLAIMER
    assert "yatırım danışmanlığı kapsamında değildir" in DEFAULT_SHADOW_MODE_DISCLAIMER
    normalized_disclaimer = f" {DEFAULT_SHADOW_MODE_DISCLAIMER.lower()} "
    for term in PROHIBITED_TERMS:
        assert f" {term.strip()} " not in normalized_disclaimer


def test_shadow_tracker_validation() -> None:
    """Geçersiz parametrelerle başlatma ve kayıt durumunda hata fırlatıldığını doğrular."""
    with pytest.raises(ShadowModeError, match="Model sürüm adı boş olamaz"):
        ShadowModeTracker(model_version="")

    tracker = ShadowModeTracker(model_version="v1.0.0")

    with pytest.raises(ShadowModeError, match="Hisse sembolü boş olamaz"):
        tracker.record_prediction(
            symbol="",
            timestamp=datetime.now(UTC),
            horizon="1d",
            shadow_prediction=0.02,
            baseline_prediction=0.01,
        )

    with pytest.raises(ShadowModeError, match="negatif olamaz"):
        tracker.record_prediction(
            symbol="THYAO",
            timestamp=datetime.now(UTC),
            horizon="1d",
            shadow_prediction=0.02,
            baseline_prediction=0.01,
            latency_ms=-5.0,
        )


def test_shadow_observation_lifecycle() -> None:
    """Gölge gözleminin kaydedilmesi, gerçekleşen değerin işlenmesi ve sözlük çevrimini doğrular."""
    tracker = ShadowModeTracker(model_version="v1.0.0")
    t0 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)

    obs = tracker.record_prediction(
        symbol="bımas",
        timestamp=t0,
        horizon="1d",
        shadow_prediction=0.025,
        baseline_prediction=0.010,
        latency_ms=12.5,
        interval_lower=0.005,
        interval_upper=0.045,
        epistemic_uncertainty=0.004,
    )

    assert obs.symbol == "BIMAS"  # Otomatik büyük harfe çevrilir
    assert not obs.is_realized()
    assert obs.latency_ms == 12.5

    # Gerçekleşen değerin çözülmesi
    t1 = t0 + timedelta(days=1)
    resolved = tracker.resolve_observation(obs.observation_id, actual_value=0.022, realized_at=t1)
    assert resolved.is_realized()
    assert resolved.actual_value == 0.022
    assert resolved.realized_at == t1

    # to_dict
    d = resolved.to_dict()
    assert d["symbol"] == "BIMAS"
    assert d["actual_value"] == 0.022
    assert d["latency_ms"] == 12.5

    # DataFrame çevrimi
    df = tracker.to_dataframe()
    assert len(df) == 1
    assert df["symbol"].iloc[0] == "BIMAS"


def test_shadow_mode_empty_tracker_evaluation() -> None:
    """Henüz tahmin kaydedilmemişken raporun EXTEND_SHADOW dönmesini doğrular."""
    tracker = ShadowModeTracker(model_version="v1.0.0")
    report = tracker.evaluate()

    assert report.total_observations == 0
    assert report.decision == ShadowPromotionDecision.EXTEND_SHADOW
    assert not report.is_approved_for_live()
    assert "Henüz hiç gölge modu tahmini kaydedilmedi" in report.pending_reasons[0]


def test_shadow_mode_insufficient_time_and_observations() -> None:
    """Gözlem sayısı ve takip süresi dolmadan EXTEND_SHADOW üretilmesini doğrular."""
    config = ShadowModeConfig(min_observations=10, min_shadow_duration_days=5)
    tracker = ShadowModeTracker(model_version="v1.0.0", config=config)

    t_start = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    for i in range(5):
        obs = tracker.record_prediction(
            symbol="AKBNK",
            timestamp=t_start + timedelta(hours=i * 2),
            horizon="1d",
            shadow_prediction=0.01,
            baseline_prediction=0.005,
            latency_ms=15.0,
        )
        tracker.resolve_observation(obs.observation_id, actual_value=0.012)

    # 1 gün sonra değerlendir
    report = tracker.evaluate(current_time=t_start + timedelta(days=1))
    assert report.decision == ShadowPromotionDecision.EXTEND_SHADOW
    assert not report.is_approved_for_live()
    assert any("asgari gereken" in r for r in report.pending_reasons)
    assert any("asgari süre" in r for r in report.pending_reasons)


def test_shadow_mode_successful_live_promotion() -> None:
    """Tüm kriterleri sağlayan modelin PROCEED_TO_LIVE onayı almasını doğrular."""
    config = ShadowModeConfig(
        min_observations=20,
        min_shadow_duration_days=3,
        max_allowed_latency_p95_ms=100.0,
        max_allowed_error_rate=0.02,
        require_beat_baseline_mae=True,
        min_directional_accuracy=0.55,
        min_interval_coverage=0.70,
    )
    tracker = ShadowModeTracker(model_version="candidate_gru_v2", config=config)
    t_start = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)

    # 25 adet başarılı gözlem ekle, 4 günlük süreye yay
    for i in range(25):
        t_obs = t_start + timedelta(hours=i * 4)
        actual = 0.02 if (i % 2 == 0) else -0.015
        model_pred = 0.019 if (i % 2 == 0) else -0.014
        base_pred = 0.005 if (i % 2 == 0) else 0.005

        obs = tracker.record_prediction(
            symbol="EREGL",
            timestamp=t_obs,
            horizon="1d",
            shadow_prediction=model_pred,
            baseline_prediction=base_pred,
            latency_ms=25.0 + (i % 5),
            interval_lower=-0.03,
            interval_upper=0.035,
        )
        tracker.resolve_observation(
            obs.observation_id,
            actual_value=actual,
            realized_at=t_obs + timedelta(hours=3),
        )

    # 4 gün sonra değerlendir
    eval_time = t_start + timedelta(days=4)
    report = tracker.evaluate(current_time=eval_time)

    assert report.decision == ShadowPromotionDecision.PROCEED_TO_LIVE
    assert report.is_approved_for_live()
    assert report.total_observations == 25
    assert report.realized_observations == 25
    assert report.unrealized_observations == 0
    assert report.shadow_duration_days >= 3.0
    assert report.model_mae is not None and report.baseline_mae is not None
    assert report.model_mae < report.baseline_mae
    assert report.model_directional_accuracy == 1.0
    assert report.interval_coverage == 1.0
    assert report.latency_p95_ms < 100.0
    assert report.error_count == 0
    assert report.error_rate == 0.0
    assert len(report.rejection_reasons) == 0
    assert len(report.pending_reasons) == 0

    # to_dict kontrolü
    d = report.to_dict()
    assert d["decision"] == "PROCEED_TO_LIVE"
    assert d["is_approved_for_live"] is True
    assert "SPK" in d["disclaimer"]


def test_shadow_mode_rejection_due_to_baseline_underperformance() -> None:
    """Model naif baseline'dan kötü performans gösterdiğinde REJECT üretilmesini doğrular."""
    config = ShadowModeConfig(
        min_observations=5,
        min_shadow_duration_days=2,
        require_beat_baseline_mae=True,
    )
    tracker = ShadowModeTracker(model_version="poor_model_v0", config=config)
    t_start = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)

    for i in range(6):
        t_obs = t_start + timedelta(hours=i * 10)
        actual = 0.05
        model_pred = -0.05
        base_pred = 0.04

        obs = tracker.record_prediction(
            symbol="KCHOL",
            timestamp=t_obs,
            horizon="1d",
            shadow_prediction=model_pred,
            baseline_prediction=base_pred,
            latency_ms=20.0,
        )
        tracker.resolve_observation(obs.observation_id, actual_value=actual)

    report = tracker.evaluate(current_time=t_start + timedelta(days=3))
    assert report.decision == ShadowPromotionDecision.REJECT
    assert not report.is_approved_for_live()
    assert any("naif baseline MAE" in r for r in report.rejection_reasons)


def test_shadow_mode_rejection_due_to_latency_sla_breach() -> None:
    """Çıkarım gecikmesi (latency) SLA tavanını aştığında REJECT üretilmesini doğrular."""
    config = ShadowModeConfig(
        min_observations=5,
        min_shadow_duration_days=1,
        max_allowed_latency_p95_ms=50.0,
    )
    tracker = ShadowModeTracker(model_version="slow_model_v1", config=config)
    t_start = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)

    for i in range(6):
        t_obs = t_start + timedelta(hours=i * 5)
        obs = tracker.record_prediction(
            symbol="TUPRS",
            timestamp=t_obs,
            horizon="1d",
            shadow_prediction=0.01,
            baseline_prediction=0.005,
            latency_ms=180.0,  # 50 ms tavanının çok üstünde
        )
        tracker.resolve_observation(obs.observation_id, actual_value=0.01)

    report = tracker.evaluate(current_time=t_start + timedelta(days=2))
    assert report.decision == ShadowPromotionDecision.REJECT
    assert any("SLA tavanını" in r for r in report.rejection_reasons)


def test_shadow_mode_rejection_due_to_high_error_rate() -> None:
    """İstisna veya hata oranı tavanı aştığında sessiz kalmayıp REJECT üretilmesini doğrular."""
    config = ShadowModeConfig(
        min_observations=5,
        min_shadow_duration_days=1,
        max_allowed_error_rate=0.05,
    )
    tracker = ShadowModeTracker(model_version="failing_model_v1", config=config)
    t_start = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)

    # 10 tahmin, 3 tanesi hatalı (%30 hata oranı > %5)
    for i in range(10):
        t_obs = t_start + timedelta(hours=i * 3)
        is_err = i < 3
        err_msg = "Tensor shape mismatch" if is_err else None
        obs = tracker.record_prediction(
            symbol="SISE",
            timestamp=t_obs,
            horizon="1d",
            shadow_prediction=0.0,
            baseline_prediction=0.0,
            latency_ms=10.0,
            is_error=is_err,
            error_message=err_msg,
        )
        if not is_err:
            tracker.resolve_observation(obs.observation_id, actual_value=0.01)

    report = tracker.evaluate(current_time=t_start + timedelta(days=2))
    assert report.decision == ShadowPromotionDecision.REJECT
    assert any("Hata oranı" in r for r in report.rejection_reasons)
