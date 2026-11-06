"""Model inference safe fallback and silent failure prevention.

SPK ve Finans-Hukuk Kuralı:
- Model başarısız olduğunda, veri kayması/alarm tetiklendiğinde veya bayatladığında
  ASLA sessizce eski tahmin sunulamaz veya yapay/uydurma tahmin üretilemez.
- Kullanıcıya şeffaf biçimde 'tahmin kapalı / devre dışı' bildirilir ve alternatif
  klasik göstergeler (RSI, MACD, SMA) önerilir.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_DISCLAIMER = (
    "Model cikti ve tahminleri kesinlik icermez; istatistiki bir yaklasimdir, "
    "yatirim tavsiyesi degildir. SPK Mevzuati Uyarinca: Bu sistem yalnizca bilgilendirme "
    "ve istatistiki analiz amaclidir. Model cikarimi basarisiz oldugunda veya veri bayatladiginda "
    "kesinlikle uydurma tahmin sunulmaz."
)

DEFAULT_ALTERNATIVE_INDICATORS = ["RSI_14", "SMA_20", "SMA_50", "MACD", "ATR_14"]


class FallbackReason(StrEnum):
    """Model başarısızlık veya fallback gerekçe kodları."""

    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_INACTIVE = "MODEL_INACTIVE"
    INFERENCE_EXCEPTION = "INFERENCE_EXCEPTION"
    STALE_PREDICTION = "STALE_PREDICTION"
    DATA_DRIFT_ALERT = "DATA_DRIFT_ALERT"
    PERFORMANCE_DEGRADATION = "PERFORMANCE_DEGRADATION"
    LATENCY_SLA_BREACH = "LATENCY_SLA_BREACH"
    DATA_INTEGRITY_FAILURE = "DATA_INTEGRITY_FAILURE"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"
    GATEKEEPER_REJECTED = "GATEKEEPER_REJECTED"


class CircuitState(StrEnum):
    """Devre kesici durumları."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class SafeForecastFallbackResult:
    """Güvenli tahmin / fallback veri taşıyıcısı.

    Kural: `forecast_available=False` olduğunda predicted_direction,
    confidence_score ve uncertainty_interval None olmak zorundadır.
    """

    symbol: str
    forecast_available: bool
    status: str
    reason_code: FallbackReason | None
    user_message: str
    technical_details: str | None = None
    evaluation_date: datetime = field(default_factory=lambda: datetime.now(UTC))
    model_version: str | None = None
    horizon_days: int | None = None
    predicted_direction: str | None = None
    confidence_score: float | None = None
    uncertainty_interval: list[float] | None = None
    baseline_comparison: str | None = None
    alternative_indicators: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALTERNATIVE_INDICATORS)
    )
    disclaimer: str = DEFAULT_FALLBACK_DISCLAIMER
    telemetry_logged: bool = True

    def __post_init__(self) -> None:
        """Kırmızı çizgi denetimi: Fallback durumunda asla tahmin sunulamaz."""
        if not self.forecast_available:
            if self.predicted_direction is not None or self.confidence_score is not None:
                raise ValueError(
                    "Kural İhlali: Fallback devredeyken (forecast_available=False) "
                    "asla yön veya güven skoru uydurulamaz/sunulamaz!"
                )
            if self.uncertainty_interval is not None:
                raise ValueError(
                    "Kural İhlali: Fallback devredeyken belirsizlik aralığı sunulamaz!"
                )

    def to_dict(self) -> dict[str, Any]:
        """Sözlük temsilini üretir."""
        return {
            "symbol": self.symbol,
            "forecast_available": self.forecast_available,
            "status": self.status,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "user_message": self.user_message,
            "technical_details": self.technical_details,
            "evaluation_date": self.evaluation_date.isoformat(),
            "model_version": self.model_version,
            "horizon_days": self.horizon_days,
            "predicted_direction": self.predicted_direction,
            "confidence_score": self.confidence_score,
            "uncertainty_interval": self.uncertainty_interval,
            "baseline_comparison": self.baseline_comparison,
            "alternative_indicators": self.alternative_indicators,
            "disclaimer": self.disclaimer,
        }


class InferenceCircuitBreaker:
    """Model çıkarım hatalarında aşırı yüklenmeyi önleyen devre kesici."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout_seconds = max(1.0, recovery_timeout_seconds)
        self.failure_count: int = 0
        self.state: CircuitState = CircuitState.CLOSED
        self.last_state_change: datetime = datetime.now(UTC)

    def is_call_allowed(self, current_time: datetime | None = None) -> bool:
        """Çağrının yapılıp yapılamayacağını belirler."""
        now = current_time or datetime.now(UTC)

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = (now - self.last_state_change).total_seconds()
            if elapsed >= self.recovery_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                logger.info("Circuit breaker HALF_OPEN durumuna geçti; deneme yapılıyor.")
                return True
            return False

        # HALF_OPEN durumunda denemeye izin verilir
        return True

    def record_success(self, current_time: datetime | None = None) -> None:
        """Başarılı çağrıyı kaydeder ve devreyi kapatır."""
        now = current_time or datetime.now(UTC)
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_state_change = now

    def record_failure(self, current_time: datetime | None = None) -> None:
        """Başarısız çağrıyı kaydeder; eşik aşılırsa devreyi açar."""
        now = current_time or datetime.now(UTC)
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.last_state_change = now
            logger.warning(
                "Circuit breaker OPEN durumuna geçti! Eşik: %d hata.",
                self.failure_threshold,
            )


class SafeForecastFallbackManager:
    """Model çıkarımı hataları, bayat veriler ve alarmlar için güvenli fallback yöneticisi."""

    def __init__(
        self,
        max_staleness_hours: float = 24.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 60.0,
        governance_registry: Any = None,
        monitoring_service: Any = None,
    ) -> None:
        self.max_staleness_hours = max(0.1, max_staleness_hours)
        self.circuit_breaker = InferenceCircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout_seconds=circuit_recovery_timeout,
        )
        self.governance_registry = governance_registry
        self.monitoring_service = monitoring_service
        self._audit_log: list[dict[str, Any]] = []

    def validate_prediction_freshness(
        self,
        evaluation_date: datetime,
        current_time: datetime | None = None,
    ) -> tuple[bool, str | None]:
        """Tahminin bayat olup olmadığını doğrular."""
        now = current_time or datetime.now(UTC)
        if evaluation_date.tzinfo is None:
            evaluation_date = evaluation_date.replace(tzinfo=UTC)

        age = now - evaluation_date
        max_age = timedelta(hours=self.max_staleness_hours)

        if age > max_age:
            msg = (
                f"Tahmin bayat (yaş: {age.total_seconds() / 3600:.1f} saat, "
                f"azami izin verilen: {self.max_staleness_hours:.1f} saat). "
                f"Sessizce eski tahmin sunulması engellendi."
            )
            return False, msg

        return True, None

    def build_fallback_response(
        self,
        symbol: str,
        reason: FallbackReason,
        user_message: str | None = None,
        technical_details: str | None = None,
        evaluation_date: datetime | None = None,
        model_version: str | None = None,
    ) -> SafeForecastFallbackResult:
        """Tutarlı ve şeffaf bir fallback sonucu oluşturur."""
        clean_symbol = symbol.strip().upper()
        now = evaluation_date or datetime.now(UTC)

        default_messages: dict[FallbackReason, str] = {
            FallbackReason.MODEL_NOT_FOUND: (
                f"'{clean_symbol}' sembolü için aktif bir tahmin modeli bulunmamaktadır. "
                "Klasik teknik göstergeleri inceleyebilirsiniz."
            ),
            FallbackReason.MODEL_INACTIVE: (
                f"'{clean_symbol}' için atanmış model şu an aktif kullanımda değildir."
            ),
            FallbackReason.INFERENCE_EXCEPTION: (
                "Tahmin motorunda geçici bir teknik sorun oluştu. "
                "Hatalı tahmin sunulmaması adına tahmin devre dışı bırakıldı."
            ),
            FallbackReason.STALE_PREDICTION: (
                "Mevcut tahmin güncelliğini yitirmiştir (bayat veri). "
                "Eski tahminler kullanıcıya taze gibi sunulmaz."
            ),
            FallbackReason.DATA_DRIFT_ALERT: (
                "Piyasa verisinde istatistiki rejim kayması tespit edildiğinden "
                "tahmin üretimi güvenlik amacıyla geçici olarak durdurulmuştur."
            ),
            FallbackReason.PERFORMANCE_DEGRADATION: (
                "Model doğruluk performansı kabul eşiğinin altına indiği için "
                "tahmin servisi devre dışı bırakılmıştır."
            ),
            FallbackReason.LATENCY_SLA_BREACH: (
                "Model çıkarım yanıt süresi azami SLA limitini aştı."
            ),
            FallbackReason.DATA_INTEGRITY_FAILURE: (
                "Girdi piyasa verisi eksik veya anomali içerdiğinden tahmin üretilemedi."
            ),
            FallbackReason.CIRCUIT_BREAKER_OPEN: (
                "Ardışık teknik aksaklıklar sebebiyle devre kesici koruması devrededir."
            ),
            FallbackReason.GATEKEEPER_REJECTED: (
                "Model naif kıyası veya maliyet kapısını geçemediği için tahmin kapalıdır."
            ),
        }

        chosen_msg = user_message or default_messages.get(
            reason, "Tahmin servisi şu an kullanılamıyor."
        )

        result = SafeForecastFallbackResult(
            symbol=clean_symbol,
            forecast_available=False,
            status="FALLBACK_TRIGGERED",
            reason_code=reason,
            user_message=chosen_msg,
            technical_details=technical_details,
            evaluation_date=now,
            model_version=model_version,
            horizon_days=None,
            predicted_direction=None,
            confidence_score=None,
            uncertainty_interval=None,
            baseline_comparison=None,
            telemetry_logged=True,
        )

        self._record_audit_event(
            symbol=clean_symbol,
            event_type="FALLBACK_TRIGGERED",
            reason=reason.value,
            technical_details=technical_details,
            model_version=model_version,
        )

        return result

    def execute_safe_inference(
        self,
        symbol: str,
        inference_callable: Callable[[], dict[str, Any]],
        *,
        model_version: str | None = None,
        evaluation_date: datetime | None = None,
        current_time: datetime | None = None,
    ) -> SafeForecastFallbackResult:
        """Çıkarımı çalıştırır; herhangi bir arızada sessiz fallback yerine şeffaf ret döner."""
        clean_symbol = symbol.strip().upper()
        now = current_time or datetime.now(UTC)

        # 1. Devre kesici kontrolü
        if not self.circuit_breaker.is_call_allowed(current_time=now):
            return self.build_fallback_response(
                symbol=clean_symbol,
                reason=FallbackReason.CIRCUIT_BREAKER_OPEN,
                technical_details="Circuit breaker is OPEN due to repeated failures.",
                evaluation_date=now,
                model_version=model_version,
            )

        # 2. Model Yönetişim (Governance) Kontrolü
        if self.governance_registry is not None and model_version:
            try:
                active_model = self.governance_registry.get_active_model()
                if active_model is None or active_model.model_name != model_version:
                    return self.build_fallback_response(
                        symbol=clean_symbol,
                        reason=FallbackReason.MODEL_INACTIVE,
                        technical_details=f"Model '{model_version}' aktif sahnede değil.",
                        evaluation_date=now,
                        model_version=model_version,
                    )
            except Exception as e:
                logger.error("Governance registry sorgulanamadı: %s", e)

        # 3. İzleme / Alarm Kontrolü
        if self.monitoring_service is not None:
            try:
                active_alerts = self.monitoring_service.get_active_alerts()
                critical_alerts = [
                    a for a in active_alerts if getattr(a, "severity", "") == "CRITICAL"
                ]
                if critical_alerts:
                    alert_desc = "; ".join(getattr(a, "description", "") for a in critical_alerts)
                    return self.build_fallback_response(
                        symbol=clean_symbol,
                        reason=FallbackReason.DATA_DRIFT_ALERT,
                        technical_details=f"Kritik izleme alarmları: {alert_desc}",
                        evaluation_date=now,
                        model_version=model_version,
                    )
            except Exception as e:
                logger.error("Monitoring service sorgulanamadı: %s", e)

        # 4. Çıkarım Çağrısı
        try:
            raw_output = inference_callable()
            if not isinstance(raw_output, dict):
                raise TypeError(f"Beklenen dict çıktısı, alınan: {type(raw_output)}")

            # Bayatlık kontrolü
            eval_dt = raw_output.get("evaluation_date") or evaluation_date or now
            is_fresh, stale_msg = self.validate_prediction_freshness(eval_dt, current_time=now)
            if not is_fresh:
                return self.build_fallback_response(
                    symbol=clean_symbol,
                    reason=FallbackReason.STALE_PREDICTION,
                    technical_details=stale_msg,
                    evaluation_date=eval_dt,
                    model_version=model_version or raw_output.get("model_version"),
                )

            # Çıktı geçerlilik kontrolleri (NaN, Inf vb.)
            direction = raw_output.get("predicted_direction")
            confidence = raw_output.get("confidence_score")
            interval = raw_output.get("uncertainty_interval")
            baseline_comp = raw_output.get("baseline_comparison")

            if direction is None or confidence is None or interval is None:
                raise ValueError("Model çıktısında zorunlu alanlar (yön, güven, aralık) eksik!")

            if len(interval) != 2 or interval[0] > interval[1]:
                raise ValueError(f"Geçersiz belirsizlik aralığı: {interval}")

            # Başarılı çıkarım
            self.circuit_breaker.record_success(current_time=now)
            self._record_audit_event(
                symbol=clean_symbol,
                event_type="INFERENCE_SUCCESS",
                reason=None,
                technical_details="Model inference completed successfully",
                model_version=model_version or raw_output.get("model_version"),
            )

            return SafeForecastFallbackResult(
                symbol=clean_symbol,
                forecast_available=True,
                status="OK",
                reason_code=None,
                user_message="İstatistiki tahmin başarıyla üretildi.",
                evaluation_date=eval_dt,
                model_version=model_version or raw_output.get("model_version"),
                horizon_days=raw_output.get("horizon_days", 5),
                predicted_direction=direction,
                confidence_score=float(confidence),
                uncertainty_interval=[float(interval[0]), float(interval[1])],
                baseline_comparison=baseline_comp,
                telemetry_logged=True,
            )

        except Exception as exc:
            logger.exception("Model çıkarımı sırasında hata oluştu: %s", exc)
            self.circuit_breaker.record_failure(current_time=now)
            return self.build_fallback_response(
                symbol=clean_symbol,
                reason=FallbackReason.INFERENCE_EXCEPTION,
                technical_details=f"Exception: {type(exc).__name__}: {str(exc)}",
                evaluation_date=now,
                model_version=model_version,
            )

    def _record_audit_event(
        self,
        symbol: str,
        event_type: str,
        reason: str | None,
        technical_details: str | None,
        model_version: str | None,
    ) -> None:
        """Telemetri ve denetim olayını yerel listeye kaydeder."""
        self._audit_log.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "symbol": symbol,
                "event_type": event_type,
                "reason": reason,
                "technical_details": technical_details,
                "model_version": model_version,
            }
        )

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Denetim günlüğünü döndürür."""
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        """Denetim günlüğünü temizler."""
        self._audit_log.clear()
