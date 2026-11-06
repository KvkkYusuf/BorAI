"""Trend uyarıları için sıklık sınırlama ve sessiz saatler modülü.

Bu modül FAZ-12 gereksinimlerine tam uygundur:
- Tekrarlı bildirimleri sınırla (sembol + kural bazlı cooldown).
- Kullanıcı bazlı uyarı sıklığı sınırları (saatlik ve günlük tavanlar).
- Sessiz saatler (quiet hours, gece saatleri desteği; erteleme veya iptal).
- Tüm zamanlar dahili olarak UTC saklanır ve hesaplanır;
  saat dilimi (varsayılan Europe/Istanbul) üzerinden yerel zamana dönüştürülür.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("stock_analysis.alerts.rate_limiter")

LEGAL_DISCLAIMER_RATE_LIMIT = (
    "Bu bildirim sıklık sınırlama ve sessiz saatler modülü bilgilendirme amaçlıdır; "
    "yatırım tavsiyesi veya emir iletimi içermez."
)


class DispatchStatus(StrEnum):
    """Bildirim dağıtım karar durumu."""

    ALLOWED = "allowed"
    DROPPED_QUIET_HOURS = "dropped_quiet_hours"
    DEFERRED_QUIET_HOURS = "deferred_quiet_hours"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    SUPPRESSED_HOURLY_LIMIT = "suppressed_hourly_limit"
    SUPPRESSED_DAILY_LIMIT = "suppressed_daily_limit"


class QuietHoursBehavior(StrEnum):
    """Sessiz saatlerde tetiklenen bildirimin davranışı."""

    DEFER = "defer"  # Sessiz saatler bitişinde gönderilmek üzere kuyruğa erteler
    DROP = "drop"  # Bildirimi sessizce atlar ve göndermez


@dataclass(frozen=True)
class RateLimitConfig:
    """Kullanıcı veya sistem düzeyinde uyarı sınırlandırma yapılandırması."""

    cooldown_minutes: int = 60
    max_alerts_per_hour: int = 5
    max_alerts_per_day: int = 20
    quiet_hours_enabled: bool = True
    quiet_hours_start: time = field(default_factory=lambda: time(22, 0))
    quiet_hours_end: time = field(default_factory=lambda: time(8, 0))
    timezone_name: str = "Europe/Istanbul"
    quiet_hours_behavior: QuietHoursBehavior = QuietHoursBehavior.DEFER

    def __post_init__(self) -> None:
        if self.cooldown_minutes < 1:
            msg = f"cooldown_minutes en az 1 olmalıdır, verilen: {self.cooldown_minutes}"
            raise ValueError(msg)
        if self.max_alerts_per_hour < 1:
            msg = f"max_alerts_per_hour en az 1 olmalıdır, verilen: {self.max_alerts_per_hour}"
            raise ValueError(msg)
        if self.max_alerts_per_day < self.max_alerts_per_hour:
            msg = (
                f"max_alerts_per_day ({self.max_alerts_per_day}) "
                f"max_alerts_per_hour'dan ({self.max_alerts_per_hour}) küçük olamaz"
            )
            raise ValueError(msg)
        # Saat dilimi geçerlilik kontrolü
        try:
            ZoneInfo(self.timezone_name)
        except Exception as e:
            msg = f"Geçersiz saat dilimi adı: {self.timezone_name}"
            raise ValueError(msg) from e


@dataclass(frozen=True)
class RateLimitDecision:
    """Bildirim dağıtım değerlendirmesi sonucu."""

    allowed: bool
    status: DispatchStatus
    reason: str
    deferred_until_utc: datetime | None = None
    cooldown_remaining_seconds: float | None = None
    disclaimer: str = LEGAL_DISCLAIMER_RATE_LIMIT


@dataclass
class _DispatchEntry:
    """İç bellek kayıt girdisi."""

    user_id: int
    symbol: str
    rule_id: str
    timestamp_utc: datetime


def is_in_quiet_hours(
    dt_utc: datetime,
    config: RateLimitConfig,
) -> bool:
    """Verilen UTC zamanının yapılandırılmış sessiz saatler aralığında olup olmadığını denetler.

    Gece yarısını aşan (22:00 -> 08:00) ve aynı gün içi (13:00 -> 15:00) aralıkları destekler.
    """
    if not config.quiet_hours_enabled:
        return False

    tz = ZoneInfo(config.timezone_name)
    local_dt = dt_utc.astimezone(tz)
    local_time = local_dt.time()

    start = config.quiet_hours_start
    end = config.quiet_hours_end

    if start == end:
        # Başlangıç ve bitiş aynı ise sessiz pencere yok sayılır
        return False

    if start < end:
        # Aynı gün içi (örn: 13:00 - 15:00)
        return start <= local_time < end
    else:
        # Gece yarısını aşan aralık (örn: 22:00 - 08:00)
        return local_time >= start or local_time < end


def calculate_next_quiet_hours_end(
    dt_utc: datetime,
    config: RateLimitConfig,
) -> datetime:
    """Sessiz saatler içindeki bir zaman için sessiz dönemin biteceği sonraki UTC anını hesaplar."""
    tz = ZoneInfo(config.timezone_name)
    local_dt = dt_utc.astimezone(tz)
    end_time = config.quiet_hours_end
    start_time = config.quiet_hours_start

    # Hedef yerel tarih ve saat
    if start_time > end_time:
        # Gece yarısını aşan aralık (örn: 22:00 -> 08:00)
        if local_dt.time() >= start_time:
            # Gece yarısından önce (örn: 23:00), bitiş ertesi günün end_time'ı
            target_date = local_dt.date() + timedelta(days=1)
        else:
            # Gece yarısından sonra (örn: 03:00), bitiş bugünün end_time'ı
            target_date = local_dt.date()
    else:
        # Aynı gün içi aralık (örn: 13:00 -> 15:00)
        if start_time <= local_dt.time() < end_time:
            target_date = local_dt.date()
        else:
            # Sessiz saat dışında çağrıldıysa en yakın sonraki bitiş
            if local_dt.time() < end_time:
                target_date = local_dt.date()
            else:
                target_date = local_dt.date() + timedelta(days=1)

    local_end_dt = datetime.combine(target_date, end_time, tzinfo=tz)
    return local_end_dt.astimezone(UTC)


class NotificationRateLimiter:
    """Bildirim sıklık sınırlama, tekrarlı bildirim engelleme ve sessiz saat motoru.

    Thread-safe iç bellek yönetimi ve sliding-window kontrolleri sunar.
    """

    def __init__(self, default_config: RateLimitConfig | None = None) -> None:
        self.default_config = default_config or RateLimitConfig()
        self._lock = threading.Lock()
        # user_id -> list of _DispatchEntry
        self._dispatch_history: dict[int, list[_DispatchEntry]] = {}
        # (user_id, symbol, rule_id) -> son gönderim zamanı UTC
        self._last_sent: dict[tuple[int, str, str], datetime] = {}
        # Ertelenmiş bildirimler: id -> dict
        self._deferred_notifications: list[dict[str, Any]] = []

    def check_dispatch(
        self,
        user_id: int,
        symbol: str,
        rule_id: str,
        timestamp_utc: datetime | None = None,
        config: RateLimitConfig | None = None,
    ) -> RateLimitDecision:
        """Bildirimin gönderilip gönderilemeyeceğini değerlendirir, kayıt tutmaz."""
        cfg = config or self.default_config
        now_utc = timestamp_utc or datetime.now(UTC)
        symbol_clean = symbol.strip().upper()
        rule_clean = rule_id.strip()

        # 1. Sessiz saatler kontrolü
        if is_in_quiet_hours(now_utc, cfg):
            if cfg.quiet_hours_behavior == QuietHoursBehavior.DROP:
                return RateLimitDecision(
                    allowed=False,
                    status=DispatchStatus.DROPPED_QUIET_HOURS,
                    reason=(
                        f"Sessiz saatler devrede ({cfg.quiet_hours_start.strftime('%H:%M')} - "
                        f"{cfg.quiet_hours_end.strftime('%H:%M')} {cfg.timezone_name}). "
                        f"Bildirim iptal edildi."
                    ),
                )
            # Defer (erteleme)
            next_end = calculate_next_quiet_hours_end(now_utc, cfg)
            return RateLimitDecision(
                allowed=False,
                status=DispatchStatus.DEFERRED_QUIET_HOURS,
                reason=(
                    f"Sessiz saatler devrede ({cfg.quiet_hours_start.strftime('%H:%M')} - "
                    f"{cfg.quiet_hours_end.strftime('%H:%M')} {cfg.timezone_name}). "
                    f"Bildirim {next_end.strftime('%Y-%m-%d %H:%M:%S')} UTC zamanına ertelendi."
                ),
                deferred_until_utc=next_end,
            )

        with self._lock:
            # 2. Cooldown (aynı sembol ve kural için tekrarlı bildirim engeli)
            cooldown_key = (user_id, symbol_clean, rule_clean)
            last_sent_time = self._last_sent.get(cooldown_key)
            if last_sent_time is not None:
                elapsed = (now_utc - last_sent_time).total_seconds()
                cooldown_seconds = cfg.cooldown_minutes * 60
                if elapsed < cooldown_seconds:
                    remaining = max(0.0, cooldown_seconds - elapsed)
                    rem_min = int(remaining // 60)
                    rem_sec = int(remaining % 60)
                    return RateLimitDecision(
                        allowed=False,
                        status=DispatchStatus.SUPPRESSED_COOLDOWN,
                        reason=(
                            f"{symbol_clean} [{rule_clean}] için cooldown süresi aktif. "
                            f"Kalan bekleme süresi: {rem_min} dk {rem_sec} sn."
                        ),
                        cooldown_remaining_seconds=remaining,
                    )

            # 3. Sıklık sınırlaması (Hourly ve Daily Rate Limits)
            history = self._dispatch_history.get(user_id, [])
            one_hour_ago = now_utc - timedelta(hours=1)
            one_day_ago = now_utc - timedelta(days=1)

            recent_hour_count = sum(1 for e in history if e.timestamp_utc >= one_hour_ago)
            if recent_hour_count >= cfg.max_alerts_per_hour:
                return RateLimitDecision(
                    allowed=False,
                    status=DispatchStatus.SUPPRESSED_HOURLY_LIMIT,
                    reason=(
                        f"Saatlik bildirim tavanına ulaşıldı "
                        f"({recent_hour_count}/{cfg.max_alerts_per_hour}). "
                        f"Bildirim taşkınını önlemek için sınırlandırıldı."
                    ),
                )

            recent_day_count = sum(1 for e in history if e.timestamp_utc >= one_day_ago)
            if recent_day_count >= cfg.max_alerts_per_day:
                return RateLimitDecision(
                    allowed=False,
                    status=DispatchStatus.SUPPRESSED_DAILY_LIMIT,
                    reason=(
                        f"Günlük bildirim tavanına ulaşıldı "
                        f"({recent_day_count}/{cfg.max_alerts_per_day}). "
                        f"Bildirim taşkınını önlemek için sınırlandırıldı."
                    ),
                )

        # Tüm kontrollerden geçti
        return RateLimitDecision(
            allowed=True,
            status=DispatchStatus.ALLOWED,
            reason="Bildirim dağıtım kriterlerini karşılıyor (cooldown ve sıklık sınırları uygun).",
        )

    def record_dispatch(
        self,
        user_id: int,
        symbol: str,
        rule_id: str,
        timestamp_utc: datetime | None = None,
    ) -> None:
        """Başarılı bir bildirim gönderimini kaydeder."""
        now_utc = timestamp_utc or datetime.now(UTC)
        symbol_clean = symbol.strip().upper()
        rule_clean = rule_id.strip()

        with self._lock:
            # Cooldown zaman damgası
            self._last_sent[(user_id, symbol_clean, rule_clean)] = now_utc

            # Sıklık geçmişi
            entry = _DispatchEntry(
                user_id=user_id,
                symbol=symbol_clean,
                rule_id=rule_clean,
                timestamp_utc=now_utc,
            )
            if user_id not in self._dispatch_history:
                self._dispatch_history[user_id] = []
            self._dispatch_history[user_id].append(entry)

            # 48 saatten eski kayıtları temizle (hafıza şişmesini önle)
            cutoff = now_utc - timedelta(hours=48)
            self._dispatch_history[user_id] = [
                e for e in self._dispatch_history[user_id] if e.timestamp_utc >= cutoff
            ]

    def evaluate_and_record(
        self,
        user_id: int,
        symbol: str,
        rule_id: str,
        timestamp_utc: datetime | None = None,
        config: RateLimitConfig | None = None,
    ) -> RateLimitDecision:
        """Değerlendirir ve eğer izin verildiyse gönderimi otomatik olarak kaydeder."""
        now_utc = timestamp_utc or datetime.now(UTC)
        decision = self.check_dispatch(
            user_id=user_id,
            symbol=symbol,
            rule_id=rule_id,
            timestamp_utc=now_utc,
            config=config,
        )
        if decision.allowed:
            self.record_dispatch(
                user_id=user_id,
                symbol=symbol,
                rule_id=rule_id,
                timestamp_utc=now_utc,
            )
        elif decision.status == DispatchStatus.DEFERRED_QUIET_HOURS and decision.deferred_until_utc:
            with self._lock:
                self._deferred_notifications.append(
                    {
                        "user_id": user_id,
                        "symbol": symbol.strip().upper(),
                        "rule_id": rule_id.strip(),
                        "created_at_utc": now_utc,
                        "deferred_until_utc": decision.deferred_until_utc,
                        "status": "pending",
                    }
                )
        return decision

    def get_user_stats(
        self,
        user_id: int,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        """Kullanıcının son saatlik ve günlük uyarı istatistiklerini döndürür."""
        curr_utc = now_utc or datetime.now(UTC)
        one_hour_ago = curr_utc - timedelta(hours=1)
        one_day_ago = curr_utc - timedelta(days=1)

        with self._lock:
            history = self._dispatch_history.get(user_id, [])
            alerts_last_hour = sum(1 for e in history if e.timestamp_utc >= one_hour_ago)
            alerts_last_day = sum(1 for e in history if e.timestamp_utc >= one_day_ago)
            total_recorded = len(history)

            active_cooldowns: list[dict[str, Any]] = []
            for (uid, sym, rid), last_t in self._last_sent.items():
                if uid == user_id:
                    cooldown_sec = self.default_config.cooldown_minutes * 60
                    elapsed = (curr_utc - last_t).total_seconds()
                    if elapsed < cooldown_sec:
                        active_cooldowns.append(
                            {
                                "symbol": sym,
                                "rule_id": rid,
                                "remaining_seconds": round(cooldown_sec - elapsed, 1),
                            }
                        )

        return {
            "user_id": user_id,
            "alerts_last_hour": alerts_last_hour,
            "alerts_last_day": alerts_last_day,
            "total_recorded": total_recorded,
            "active_cooldown_count": len(active_cooldowns),
            "active_cooldowns": active_cooldowns,
        }

    def get_deferred_queue(self) -> list[dict[str, Any]]:
        """Ertelenmiş bildirimler kuyruğunun kopyasını döndürür."""
        with self._lock:
            return list(self._deferred_notifications)

    def clear_deferred_queue(self) -> None:
        """Ertelenmiş kuyruğu sıfırlar."""
        with self._lock:
            self._deferred_notifications.clear()

    def reset(self, user_id: int | None = None) -> None:
        """Kayıtları temizler (testler ve sıfırlamalar için)."""
        with self._lock:
            if user_id is None:
                self._dispatch_history.clear()
                self._last_sent.clear()
                self._deferred_notifications.clear()
            else:
                self._dispatch_history.pop(user_id, None)
                keys_to_delete = [k for k in self._last_sent if k[0] == user_id]
                for k in keys_to_delete:
                    del self._last_sent[k]
                self._deferred_notifications = [
                    d for d in self._deferred_notifications if d["user_id"] != user_id
                ]


# Singleton / varsayılan örnek
default_rate_limiter = NotificationRateLimiter()
