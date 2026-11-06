"""Uyarı gecikmesini ölçme ve sağlayıcı veri gecikmesi değerlendirme modülü.

Bu modül, FAZ-12 gereksinimlerine uygundur:
- Sağlayıcı verisinin zaman damgası (data_timestamp_utc) ile uyarının tetiklenme anı
  arasındaki gecikmeyi ölçer.
- Lisanslı gecikmeli veri (ör. Borsa İstanbul 15 dakika gecikmeli veri yayını),
  seans içi bayat/kesintiye uğramış veri ve piyasa kapanış durumlarını ayırt eder.
- Bildirimlerde (e-posta / push / arayüz) kullanıcılara veri gecikmesini şeffaf biçimde
  belirten ve SPK uyumlu açıklamalar üretir.
- Yatırım tavsiyesi niteliği taşımaz; bilgilendirme amaçlıdır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from stock_analysis.indicators.trend import validate_compliant_text

if TYPE_CHECKING:
    from stock_analysis.alerts.email_notifier import AlertNotificationPayload

LEGAL_DISCLAIMER_LATENCY: str = (
    "YASAL UYARI: Veri gecikmesi ve tazelik bilgileri teknik bilgilendirme "
    "amaçlıdır; yatırım tavsiyesi, portföy yönetimi veya alım-satım önerisi değildir."
)


class DataFreshnessStatus(StrEnum):
    """Veri tazeliği ve sağlayıcı gecikme durumu."""

    REALTIME = "REALTIME"  # Tolerans dahilinde anlık / eşzamanlı
    DELAYED = "DELAYED"  # Standart lisanslı gecikme (ör. BIST 15 dk gecikmeli)
    STALE = "STALE"  # Beklenen gecikmeyi aşan bayat veri / sağlayıcı kesintisi
    MARKET_CLOSED = "MARKET_CLOSED"  # Piyasa kapalı (gece / hafta sonu / tatil)


@dataclass(frozen=True)
class ProviderLatencyConfig:
    """Veri sağlayıcı gecikme ve tolerans yapılandırması."""

    expected_delay_minutes: float = 15.0  # Standart BIST lisanslı gecikme
    realtime_tolerance_seconds: float = 120.0  # Anlık kabul toleransı (2 dk)
    stale_threshold_minutes: float = 35.0  # Bayat veri eşiği
    warn_on_delayed: bool = True  # Gecikmeli veride uyarı bayrağını aktif et
    provider_name: str = "BIST"


@dataclass(frozen=True)
class LatencyEvaluationResult:
    """Veri gecikmesi değerlendirme sonucu."""

    status: DataFreshnessStatus
    data_timestamp_utc: datetime
    evaluated_at_utc: datetime
    latency_seconds: float
    latency_minutes: float
    is_delayed: bool
    delay_description: str
    provider_notice: str
    is_market_open: bool

    def __post_init__(self) -> None:
        """Açıklama metinlerinin SPK uyumluluğunu doğrula."""
        if self.delay_description:
            validate_compliant_text(self.delay_description)
        if self.provider_notice:
            validate_compliant_text(self.provider_notice)


class LatencyEvaluator:
    """Uyarı gecikmesini hesaplayan ve sağlayıcı durumunu değerlendiren motor."""

    def __init__(self, config: ProviderLatencyConfig | None = None) -> None:
        self.config = config or ProviderLatencyConfig()

    def evaluate(
        self,
        data_timestamp_utc: datetime,
        evaluated_at_utc: datetime | None = None,
        is_market_open: bool = True,
    ) -> LatencyEvaluationResult:
        """Veri zaman damgası ile değerlendirme anı arasındaki gecikmeyi ölçer.

        Args:
            data_timestamp_utc: Sağlayıcıdan gelen son bar veya fiyat verisinin UTC zamanı.
            evaluated_at_utc: Değerlendirmenin yapıldığı an (varsayılan: now(UTC)).
            is_market_open: Piyasanın açık olup olmadığı (seans dışı bayatlık ayrımı için).

        Returns:
            LatencyEvaluationResult: Gecikme durumu, süresi ve bildirim metinleri.
        """
        # Zaman damgalarını UTC normalize et
        data_ts = self._ensure_utc(data_timestamp_utc)
        now_ts = self._ensure_utc(evaluated_at_utc or datetime.now(UTC))

        raw_latency = (now_ts - data_ts).total_seconds()
        # Saat kayması (clock skew) veya gelecek zaman damgası durumu
        latency_seconds = max(0.0, raw_latency)
        latency_minutes = round(latency_seconds / 60.0, 1)

        # Durum belirleme
        if not is_market_open:
            status = DataFreshnessStatus.MARKET_CLOSED
            is_delayed = False
            delay_desc = (
                f"Piyasa kapalı. Veri zamanı: {data_ts.strftime('%Y-%m-%d %H:%M UTC')}. "
                f"Son seans kapanış verileri üzerinden değerlendirilmiştir."
            )
            notice = "Piyasa kapalı olduğu için son resmi seans kapanış verisi kullanılmıştır."
        elif latency_seconds <= self.config.realtime_tolerance_seconds:
            status = DataFreshnessStatus.REALTIME
            is_delayed = False
            delay_desc = f"Güncel piyasa verisi (gecikme: {int(latency_seconds)} sn)."
            notice = f"{self.config.provider_name} verisi anlık/eşzamanlı iletilmektedir."
        elif latency_minutes >= self.config.stale_threshold_minutes:
            status = DataFreshnessStatus.STALE
            is_delayed = True
            delay_desc = (
                f"Dikkat: Veri sağlayıcı gecikmesi yüksek ({latency_minutes} dakika). "
                f"Veri zamanı: {data_ts.strftime('%H:%M UTC')}."
            )
            notice = (
                f"UYARI: {self.config.provider_name} veri akışında gecikme veya "
                f"kesinti tespit edildi. Göstergeler {latency_minutes} dakika "
                f"önceki veriyle hesaplanmıştır."
            )
        else:
            status = DataFreshnessStatus.DELAYED
            is_delayed = self.config.warn_on_delayed
            delay_desc = (
                f"Sağlayıcı lisansı gereği {int(self.config.expected_delay_minutes)} dakika "
                f"gecikmeli veri (Ölçülen gecikme: {latency_minutes} dk)."
            )
            notice = (
                f"BİLGİ: {self.config.provider_name} verileri lisans/mevzuat kuralları uyarınca "
                f"{int(self.config.expected_delay_minutes)} dakika gecikmeli olarak iletilmektedir."
            )

        return LatencyEvaluationResult(
            status=status,
            data_timestamp_utc=data_ts,
            evaluated_at_utc=now_ts,
            latency_seconds=latency_seconds,
            latency_minutes=latency_minutes,
            is_delayed=is_delayed,
            delay_description=delay_desc,
            provider_notice=notice,
            is_market_open=is_market_open,
        )

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        """Datetime nesnesini UTC timezone'a zorlar."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)


def apply_latency_to_payload(
    payload: AlertNotificationPayload,
    eval_result: LatencyEvaluationResult,
) -> AlertNotificationPayload:
    """Uyarı bildirimi yüküne veri gecikmesi değerlendirme durumunu yansıtır."""
    import dataclasses

    return dataclasses.replace(
        payload,
        is_delayed=eval_result.is_delayed,
        delay_description=eval_result.delay_description,
    )
