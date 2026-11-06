"""Uyarı sıklık sınırlaması, cooldown ve sessiz saatler test paketi (FAZ-12)."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from stock_analysis.alerts.rate_limiter import (
    LEGAL_DISCLAIMER_RATE_LIMIT,
    DispatchStatus,
    NotificationRateLimiter,
    QuietHoursBehavior,
    RateLimitConfig,
    calculate_next_quiet_hours_end,
    is_in_quiet_hours,
)


def test_rate_limit_config_validation() -> None:
    """Yapılandırma parametrelerinin doğrulanması."""
    cfg = RateLimitConfig(
        cooldown_minutes=30,
        max_alerts_per_hour=10,
        max_alerts_per_day=50,
        quiet_hours_start=time(23, 0),
        quiet_hours_end=time(7, 0),
        timezone_name="Europe/Istanbul",
    )
    assert cfg.cooldown_minutes == 30
    assert cfg.max_alerts_per_hour == 10
    assert cfg.max_alerts_per_day == 50

    # Hatalı parametreler
    with pytest.raises(ValueError, match="cooldown_minutes en az 1"):
        RateLimitConfig(cooldown_minutes=0)

    with pytest.raises(ValueError, match="max_alerts_per_hour en az 1"):
        RateLimitConfig(max_alerts_per_hour=0)

    with pytest.raises(ValueError, match="küçük olamaz"):
        RateLimitConfig(max_alerts_per_hour=10, max_alerts_per_day=5)

    with pytest.raises(ValueError, match="Geçersiz saat dilimi"):
        RateLimitConfig(timezone_name="Invalid/Timezone_Name")


def test_is_in_quiet_hours_midnight_crossing() -> None:
    """Gece yarısını aşan sessiz saatler aralığı (22:00 - 08:00 Europe/Istanbul)."""
    # Europe/Istanbul UTC+3'tür:
    # 22:00 TR -> 19:00 UTC
    # 08:00 TR -> 05:00 UTC
    cfg = RateLimitConfig(
        quiet_hours_enabled=True,
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(8, 0),
        timezone_name="Europe/Istanbul",
    )

    tz = ZoneInfo("Europe/Istanbul")
    # 23:30 TR (Sessiz saatte)
    dt_inside_1 = datetime(2026, 10, 24, 23, 30, tzinfo=tz).astimezone(UTC)
    assert is_in_quiet_hours(dt_inside_1, cfg) is True

    # 03:15 TR (Sessiz saatte)
    dt_inside_2 = datetime(2026, 10, 25, 3, 15, tzinfo=tz).astimezone(UTC)
    assert is_in_quiet_hours(dt_inside_2, cfg) is True

    # 07:59:59 TR (Sessiz saatte)
    dt_inside_3 = datetime(2026, 10, 25, 7, 59, 59, tzinfo=tz).astimezone(UTC)
    assert is_in_quiet_hours(dt_inside_3, cfg) is True

    # 08:00:00 TR (Sessiz saat bitti)
    dt_outside_1 = datetime(2026, 10, 25, 8, 0, 0, tzinfo=tz).astimezone(UTC)
    assert is_in_quiet_hours(dt_outside_1, cfg) is False

    # 14:00 TR (Gündüz vakti, sessiz saat dışı)
    dt_outside_2 = datetime(2026, 10, 25, 14, 0, 0, tzinfo=tz).astimezone(UTC)
    assert is_in_quiet_hours(dt_outside_2, cfg) is False

    # Sessiz saatler devre dışı bırakılırsa
    cfg_disabled = RateLimitConfig(quiet_hours_enabled=False)
    assert is_in_quiet_hours(dt_inside_1, cfg_disabled) is False


def test_is_in_quiet_hours_same_day() -> None:
    """Aynı gün içi sessiz saat aralığı (örn: 13:00 - 15:00)."""
    cfg = RateLimitConfig(
        quiet_hours_enabled=True,
        quiet_hours_start=time(13, 0),
        quiet_hours_end=time(15, 0),
        timezone_name="Europe/Istanbul",
    )
    tz = ZoneInfo("Europe/Istanbul")

    dt_before = datetime(2026, 10, 24, 12, 59, tzinfo=tz).astimezone(UTC)
    dt_inside = datetime(2026, 10, 24, 14, 0, tzinfo=tz).astimezone(UTC)
    dt_after = datetime(2026, 10, 24, 15, 1, tzinfo=tz).astimezone(UTC)

    assert is_in_quiet_hours(dt_before, cfg) is False
    assert is_in_quiet_hours(dt_inside, cfg) is True
    assert is_in_quiet_hours(dt_after, cfg) is False


def test_calculate_next_quiet_hours_end() -> None:
    """Sessiz saat bitiş zamanının doğru hesaplanması."""
    cfg = RateLimitConfig(
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(8, 0),
        timezone_name="Europe/Istanbul",
    )
    tz = ZoneInfo("Europe/Istanbul")

    # 23:00 TR 2026-10-24 -> Ertesi gün sabah 08:00 TR = 05:00 UTC
    dt_utc = datetime(2026, 10, 24, 23, 0, tzinfo=tz).astimezone(UTC)
    next_end = calculate_next_quiet_hours_end(dt_utc, cfg)

    assert next_end == datetime(2026, 10, 25, 8, 0, tzinfo=tz).astimezone(UTC)
    assert next_end.tzinfo == UTC

    # 04:00 TR 2026-10-25 -> Aynı gün sabah 08:00 TR = 05:00 UTC
    dt_morning_utc = datetime(2026, 10, 25, 4, 0, tzinfo=tz).astimezone(UTC)
    next_end_morning = calculate_next_quiet_hours_end(dt_morning_utc, cfg)
    assert next_end_morning == datetime(2026, 10, 25, 8, 0, tzinfo=tz).astimezone(UTC)


def test_cooldown_suppression_and_granularity() -> None:
    """Aynı kullanıcı, sembol ve kural için tekrarlı bildirim engeli (cooldown)."""
    limiter = NotificationRateLimiter()
    limiter.reset()

    cfg = RateLimitConfig(
        cooldown_minutes=60,
        quiet_hours_enabled=False,  # Sessiz saatleri devre dışı bırakıp saf cooldown test edelim
    )

    t0 = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)

    # 1. İlk gönderim izin verilmeli
    d1 = limiter.evaluate_and_record(
        user_id=101,
        symbol="THYAO",
        rule_id="RULE_RSI_OVERSOLD",
        timestamp_utc=t0,
        config=cfg,
    )
    assert d1.allowed is True
    assert d1.status == DispatchStatus.ALLOWED

    # 2. 10 dakika sonra aynı sembol ve kural -> Cooldown nedeniyle reddedilmeli
    t_10m = t0 + timedelta(minutes=10)
    d2 = limiter.evaluate_and_record(
        user_id=101,
        symbol="THYAO",
        rule_id="RULE_RSI_OVERSOLD",
        timestamp_utc=t_10m,
        config=cfg,
    )
    assert d2.allowed is False
    assert d2.status == DispatchStatus.SUPPRESSED_COOLDOWN
    assert d2.cooldown_remaining_seconds is not None
    assert 2900 < d2.cooldown_remaining_seconds <= 3000

    # 3. Aynı kullanıcı, farklı sembol -> İzin verilmeli
    d3 = limiter.evaluate_and_record(
        user_id=101,
        symbol="GARAN",
        rule_id="RULE_RSI_OVERSOLD",
        timestamp_utc=t_10m,
        config=cfg,
    )
    assert d3.allowed is True

    # 4. Aynı kullanıcı, aynı sembol, farklı kural -> İzin verilmeli
    d4 = limiter.evaluate_and_record(
        user_id=101,
        symbol="THYAO",
        rule_id="RULE_MACD_CROSSOVER",
        timestamp_utc=t_10m,
        config=cfg,
    )
    assert d4.allowed is True

    # 5. Farklı kullanıcı, aynı sembol ve kural -> İzin verilmeli
    d5 = limiter.evaluate_and_record(
        user_id=202,
        symbol="THYAO",
        rule_id="RULE_RSI_OVERSOLD",
        timestamp_utc=t_10m,
        config=cfg,
    )
    assert d5.allowed is True

    # 6. Cooldown dolduktan sonra (61 dk sonra) -> İzin verilmeli
    t_61m = t0 + timedelta(minutes=61)
    d6 = limiter.evaluate_and_record(
        user_id=101,
        symbol="THYAO",
        rule_id="RULE_RSI_OVERSOLD",
        timestamp_utc=t_61m,
        config=cfg,
    )
    assert d6.allowed is True
    assert d6.status == DispatchStatus.ALLOWED


def test_hourly_rate_limiting() -> None:
    """Kullanıcı saatlik tavan sınırı (max_alerts_per_hour)."""
    limiter = NotificationRateLimiter()
    limiter.reset()

    cfg = RateLimitConfig(
        cooldown_minutes=1,
        max_alerts_per_hour=3,
        max_alerts_per_day=20,
        quiet_hours_enabled=False,
    )

    t0 = datetime(2026, 10, 24, 11, 0, tzinfo=UTC)

    # 3 farklı sembol için gönderim -> 3'ü de izin almalı
    for i, sym in enumerate(["SYM1", "SYM2", "SYM3"]):
        dec = limiter.evaluate_and_record(
            user_id=1,
            symbol=sym,
            rule_id="R1",
            timestamp_utc=t0 + timedelta(minutes=i),
            config=cfg,
        )
        assert dec.allowed is True

    # 4. gönderim (aynı 1 saatlik pencere içinde) -> Sınır aşımı
    dec4 = limiter.evaluate_and_record(
        user_id=1,
        symbol="SYM4",
        rule_id="R1",
        timestamp_utc=t0 + timedelta(minutes=10),
        config=cfg,
    )
    assert dec4.allowed is False
    assert dec4.status == DispatchStatus.SUPPRESSED_HOURLY_LIMIT
    assert "Saatlik bildirim tavanına ulaşıldı" in dec4.reason

    # 1 saat 5 dakika sonra -> Yeni pencere açılır, izin verilmeli
    dec5 = limiter.evaluate_and_record(
        user_id=1,
        symbol="SYM5",
        rule_id="R1",
        timestamp_utc=t0 + timedelta(minutes=65),
        config=cfg,
    )
    assert dec5.allowed is True


def test_daily_rate_limiting() -> None:
    """Kullanıcı günlük tavan sınırı (max_alerts_per_day)."""
    limiter = NotificationRateLimiter()
    limiter.reset()

    cfg = RateLimitConfig(
        cooldown_minutes=1,
        max_alerts_per_hour=2,
        max_alerts_per_day=4,
        quiet_hours_enabled=False,
    )

    t0 = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)

    # 4 farklı saatte 4 bildirim gönder
    for h in range(4):
        dec = limiter.evaluate_and_record(
            user_id=5,
            symbol=f"SYM_{h}",
            rule_id="R1",
            timestamp_utc=t0 + timedelta(hours=h),
            config=cfg,
        )
        assert dec.allowed is True

    # 5. bildirim -> Günlük sınıra takılmalı
    dec_limit = limiter.evaluate_and_record(
        user_id=5,
        symbol="SYM_5",
        rule_id="R1",
        timestamp_utc=t0 + timedelta(hours=5),
        config=cfg,
    )
    assert dec_limit.allowed is False
    assert dec_limit.status == DispatchStatus.SUPPRESSED_DAILY_LIMIT
    assert "Günlük bildirim tavanına ulaşıldı" in dec_limit.reason


def test_quiet_hours_defer_behavior() -> None:
    """Sessiz saatlerde DEFER davranışı ve kuyruğa alma."""
    limiter = NotificationRateLimiter()
    limiter.reset()

    cfg = RateLimitConfig(
        quiet_hours_enabled=True,
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(8, 0),
        timezone_name="Europe/Istanbul",
        quiet_hours_behavior=QuietHoursBehavior.DEFER,
    )

    tz = ZoneInfo("Europe/Istanbul")
    # Saat 23:00 TR (Sessiz saat)
    t_quiet = datetime(2026, 10, 24, 23, 0, tzinfo=tz).astimezone(UTC)

    decision = limiter.evaluate_and_record(
        user_id=42,
        symbol="AKBNK",
        rule_id="RULE_MA200_BREAKOUT",
        timestamp_utc=t_quiet,
        config=cfg,
    )

    assert decision.allowed is False
    assert decision.status == DispatchStatus.DEFERRED_QUIET_HOURS
    assert decision.deferred_until_utc is not None
    # 08:00 TR = 05:00 UTC
    assert decision.deferred_until_utc.hour == 5

    # Kuyrukta bir ertelenmiş kayıt olmalı
    queue = limiter.get_deferred_queue()
    assert len(queue) == 1
    assert queue[0]["user_id"] == 42
    assert queue[0]["symbol"] == "AKBNK"

    limiter.clear_deferred_queue()
    assert len(limiter.get_deferred_queue()) == 0


def test_quiet_hours_drop_behavior() -> None:
    """Sessiz saatlerde DROP davranışı (iptal etme / yoksayma)."""
    limiter = NotificationRateLimiter()
    limiter.reset()

    cfg = RateLimitConfig(
        quiet_hours_enabled=True,
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(8, 0),
        timezone_name="Europe/Istanbul",
        quiet_hours_behavior=QuietHoursBehavior.DROP,
    )

    tz = ZoneInfo("Europe/Istanbul")
    t_quiet = datetime(2026, 10, 24, 23, 0, tzinfo=tz).astimezone(UTC)

    decision = limiter.evaluate_and_record(
        user_id=42,
        symbol="AKBNK",
        rule_id="RULE_MA200_BREAKOUT",
        timestamp_utc=t_quiet,
        config=cfg,
    )

    assert decision.allowed is False
    assert decision.status == DispatchStatus.DROPPED_QUIET_HOURS
    assert decision.deferred_until_utc is None
    # DROP modunda kuyruğa eklenmez
    assert len(limiter.get_deferred_queue()) == 0


def test_user_stats_and_reset() -> None:
    """Kullanıcı istatistikleri ve sıfırlama işlevleri."""
    limiter = NotificationRateLimiter()
    limiter.reset()

    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

    limiter.record_dispatch(user_id=7, symbol="KCHOL", rule_id="R1", timestamp_utc=now)
    limiter.record_dispatch(user_id=7, symbol="SISE", rule_id="R2", timestamp_utc=now)
    limiter.record_dispatch(user_id=8, symbol="EREGL", rule_id="R1", timestamp_utc=now)

    stats7 = limiter.get_user_stats(user_id=7, now_utc=now + timedelta(minutes=5))
    assert stats7["user_id"] == 7
    assert stats7["alerts_last_hour"] == 2
    assert stats7["alerts_last_day"] == 2
    assert stats7["active_cooldown_count"] == 2

    # Sadece 7 numaralı kullanıcıyı sıfırla
    limiter.reset(user_id=7)
    stats7_after = limiter.get_user_stats(user_id=7, now_utc=now + timedelta(minutes=5))
    assert stats7_after["alerts_last_hour"] == 0
    assert stats7_after["active_cooldown_count"] == 0

    # 8 numaralı kullanıcı etkilenmemiş olmalı
    stats8 = limiter.get_user_stats(user_id=8, now_utc=now + timedelta(minutes=5))
    assert stats8["alerts_last_hour"] == 1


def test_legal_disclaimer_present() -> None:
    """Yasal uyarı ve bilgilendirme metninin kontrolü."""
    limiter = NotificationRateLimiter()
    decision = limiter.check_dispatch(
        user_id=1,
        symbol="BIST30",
        rule_id="R1",
        config=RateLimitConfig(quiet_hours_enabled=False),
    )
    assert decision.disclaimer == LEGAL_DISCLAIMER_RATE_LIMIT
    assert "bilgilendirme amaçlıdır" in decision.disclaimer
    assert "yatırım tavsiyesi" in decision.disclaimer
