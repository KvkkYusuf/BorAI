"""Uyarıların veri zamanı, sembol, tetiklenen koşul ve veri kaynağı kayıt modülü.

Bu modül, FAZ-12 gereksinimlerine ve SPK uyumluluk kırmızı çizgilerine tam uyumludur:
- Uyarıların veri zamanını (data_timestamp_utc), sembolü (symbol), tetiklenen koşulları
  (triggered_conditions) ve veri kaynağı/sağlayıcı bilgisini (data_source) eksiksiz kaydeder.
- Veri gecikmesi (provider delay) ve zaman damgası tutarlılığını doğrular.
- Tüm başlık, açıklama ve koşul metinlerini SPK mevzuatı gereğince yasaklı ifade
  (al/sat, hedef fiyat vb.) filtresinden geçirir.
- Bellek içi (in-memory) ve veritabanı (DB) denetim izlenebilirliği (audit trail) sunar.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from stock_analysis.indicators.trend import validate_compliant_text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession

LEGAL_DISCLAIMER_HISTORY: str = (
    "Kaydedilen bu uyarı bildirimleri yalnızca geçmiş teknik ve matematiksel gösterge "
    "tetiklenmelerini belgeler; kesinlikle yatırım danışmanlığı, yönlendirme, hedef fiyat "
    "veya al/sat tavsiyesi değildir."
)


class AlertRecordValidationError(ValueError):
    """Uyarı kaydı doğrulama hatası."""


@dataclass(frozen=True)
class TriggeredConditionDetail:
    """Tetiklenen tek bir koşulun detaylı teknik kaydı."""

    condition_type: str
    operator: str
    threshold_value: float
    observed_value: float
    description: str

    def __post_init__(self) -> None:
        """Koşul açıklaması ve parametrelerini doğrula."""
        if not self.condition_type or not self.condition_type.strip():
            raise AlertRecordValidationError("Koşul türü (condition_type) boş olamaz.")
        if not self.operator or not self.operator.strip():
            raise AlertRecordValidationError("Operatör (operator) boş olamaz.")
        if not self.description or not self.description.strip():
            raise AlertRecordValidationError("Koşul açıklaması (description) boş olamaz.")

        # SPK yasaklı ifade denetimi
        validate_compliant_text(self.description)

    def to_dict(self) -> dict[str, Any]:
        """Sözlük formatına dönüştür."""
        return {
            "condition_type": self.condition_type,
            "operator": self.operator,
            "threshold_value": self.threshold_value,
            "observed_value": self.observed_value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriggeredConditionDetail:
        """Sözlükten nesne oluştur."""
        return cls(
            condition_type=str(data["condition_type"]),
            operator=str(data["operator"]),
            threshold_value=float(data["threshold_value"]),
            observed_value=float(data["observed_value"]),
            description=str(data["description"]),
        )


@dataclass(frozen=True)
class AlertHistoryRecord:
    """Tetiklenen bir uyarının veri zamanı, sembol, koşullar ve kaynak bilgisi kaydı."""

    symbol: str
    data_timestamp_utc: datetime
    data_source: str
    triggered_conditions: tuple[TriggeredConditionDetail, ...]
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    triggered_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    rule_id: str | None = None
    rule_name: str | None = None
    user_id: int | None = None
    headline: str = "Teknik Gösterge Koşul Eşleşmesi"
    explanation: str = "Belirlenen teknik gösterge koşulları karşılanmıştır."
    provider_delay_seconds: int = 0
    is_delayed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = LEGAL_DISCLAIMER_HISTORY

    def __post_init__(self) -> None:
        """Kayıt bütünlüğünü ve mevzuat kurallarını doğrula."""
        clean_symbol = self.symbol.strip().upper() if self.symbol else ""
        if not clean_symbol:
            raise AlertRecordValidationError("Sembol (symbol) boş olamaz.")
        object.__setattr__(self, "symbol", clean_symbol)

        clean_source = self.data_source.strip() if self.data_source else ""
        if not clean_source:
            raise AlertRecordValidationError("Veri kaynağı (data_source) boş olamaz.")
        object.__setattr__(self, "data_source", clean_source)

        if not self.data_timestamp_utc:
            raise AlertRecordValidationError("Veri zamanı (data_timestamp_utc) zorunludur.")
        if self.data_timestamp_utc.tzinfo is None:
            raise AlertRecordValidationError(
                "data_timestamp_utc saat dilimi (timezone-aware) içermelidir."
            )

        if self.triggered_at_utc.tzinfo is None:
            raise AlertRecordValidationError(
                "triggered_at_utc saat dilimi (timezone-aware) içermelidir."
            )

        if not self.triggered_conditions:
            raise AlertRecordValidationError(
                "En az bir tetiklenen koşul (triggered_conditions) belirtilmelidir."
            )

        if self.provider_delay_seconds < 0:
            raise AlertRecordValidationError(
                "Sağlayıcı gecikmesi (provider_delay_seconds) negatif olamaz."
            )

        # SPK mevzuat uyumluluğu kontrolü
        validate_compliant_text(self.headline)
        validate_compliant_text(self.explanation)
        if self.rule_name:
            validate_compliant_text(self.rule_name)

    def to_dict(self) -> dict[str, Any]:
        """Serileştirilebilir sözlük biçimine dönüştür."""
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "user_id": self.user_id,
            "symbol": self.symbol,
            "data_timestamp_utc": self.data_timestamp_utc.isoformat(),
            "triggered_at_utc": self.triggered_at_utc.isoformat(),
            "data_source": self.data_source,
            "triggered_conditions": [c.to_dict() for c in self.triggered_conditions],
            "headline": self.headline,
            "explanation": self.explanation,
            "provider_delay_seconds": self.provider_delay_seconds,
            "is_delayed": self.is_delayed,
            "metadata": self.metadata,
            "disclaimer": self.disclaimer,
        }

    def to_json(self) -> str:
        """JSON dizgisine dönüştür."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlertHistoryRecord:
        """Sözlük verisinden AlertHistoryRecord nesnesi oluştur."""
        conditions = tuple(
            TriggeredConditionDetail.from_dict(c) for c in data.get("triggered_conditions", [])
        )
        return cls(
            alert_id=str(data.get("alert_id", uuid.uuid4())),
            rule_id=data.get("rule_id"),
            rule_name=data.get("rule_name"),
            user_id=data.get("user_id"),
            symbol=str(data["symbol"]),
            data_timestamp_utc=datetime.fromisoformat(data["data_timestamp_utc"]),
            triggered_at_utc=datetime.fromisoformat(data["triggered_at_utc"]),
            data_source=str(data["data_source"]),
            triggered_conditions=conditions,
            headline=str(data.get("headline", "Teknik Gösterge Koşul Eşleşmesi")),
            explanation=str(data.get("explanation", "Belirlenen koşullar karşılanmıştır.")),
            provider_delay_seconds=int(data.get("provider_delay_seconds", 0)),
            is_delayed=bool(data.get("is_delayed", False)),
            metadata=dict(data.get("metadata", {})),
            disclaimer=str(data.get("disclaimer", LEGAL_DISCLAIMER_HISTORY)),
        )


class AlertHistoryRepository:
    """Uyarı kayıtlarını izleyen, sorgulayan ve kalıcılaştıran depo sınıfı."""

    def __init__(self) -> None:
        self._records: list[AlertHistoryRecord] = []

    def record(self, record: AlertHistoryRecord) -> AlertHistoryRecord:
        """Yeni bir uyarı geçmişi kaydını kaydet."""
        self._records.append(record)
        return record

    def record_many(
        self, records: list[AlertHistoryRecord] | tuple[AlertHistoryRecord, ...]
    ) -> list[AlertHistoryRecord]:
        """Birden çok uyarı kaydını toplu kaydet."""
        for rec in records:
            self.record(rec)
        return list(records)

    def get_by_id(self, alert_id: str) -> AlertHistoryRecord | None:
        """Kimlik numarasına göre kaydı bul."""
        for rec in self._records:
            if rec.alert_id == alert_id:
                return rec
        return None

    def list_all(self) -> list[AlertHistoryRecord]:
        """Tüm kayıtları döndür (yeniden eskiye doğru)."""
        return sorted(self._records, key=lambda r: r.triggered_at_utc, reverse=True)

    def list_by_symbol(self, symbol: str) -> list[AlertHistoryRecord]:
        """Belirli bir sembole ait uyarıları listele."""
        clean = symbol.strip().upper()
        return [r for r in self.list_all() if r.symbol == clean]

    def list_by_user(self, user_id: int) -> list[AlertHistoryRecord]:
        """Belirli bir kullanıcıya ait uyarıları listele."""
        return [r for r in self.list_all() if r.user_id == user_id]

    def list_by_rule(self, rule_id: str) -> list[AlertHistoryRecord]:
        """Belirli bir kural kimliğine ait uyarıları listele."""
        return [r for r in self.list_all() if r.rule_id == rule_id]

    def list_by_source(self, data_source: str) -> list[AlertHistoryRecord]:
        """Veri kaynağına göre filtrele."""
        clean = data_source.strip().lower()
        return [r for r in self.list_all() if r.data_source.strip().lower() == clean]

    def list_between(self, start_utc: datetime, end_utc: datetime) -> list[AlertHistoryRecord]:
        """Belirli bir zaman aralığında tetiklenen uyarıları listele."""
        return [r for r in self.list_all() if start_utc <= r.triggered_at_utc <= end_utc]

    def count(self) -> int:
        """Toplam kayıt sayısını döndür."""
        return len(self._records)

    def clear(self) -> None:
        """Kayıtları temizle (test ortamları için)."""
        self._records.clear()

    def sync_to_db(self, db_session: DBSession, record: AlertHistoryRecord) -> None:
        """Uyarı kaydını veritabanındaki AlertEvent modeline senkronize et."""
        from stock_analysis.db.models import AlertEvent

        rule_id_int: int | None = None
        if record.rule_id is not None:
            try:
                rule_id_int = int(record.rule_id)
            except ValueError:
                rule_id_int = None

        if rule_id_int is not None:
            event = AlertEvent(
                alert_rule_id=rule_id_int,
                triggered_at=record.triggered_at_utc,
                message=record.headline[:512],
                payload=record.to_json(),
                is_read=False,
            )
            db_session.add(event)
            db_session.flush()
