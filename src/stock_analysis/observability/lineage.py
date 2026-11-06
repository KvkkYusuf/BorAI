"""Price and prediction lineage tracking and calculation version traceability.

FAZ-15 Kuralı (docs/phases/FAZ-15-gozlemlenebilirlik.md):
- Kullanıcıya sunulan veya API yanıtlarında dönülen tüm fiyat ve tahminlerin
  kaynağı (data_source), hesaplama sürümü (calculation_version), model sürümü (model_version)
  ve denetim izleme kimliği (lineage_id) uçtan uca izlenebilir olmalıdır.
- SPK mevzuatına uygun olarak bu meta veriler yalnızca teknik izlenebilirlik
  ve sistem güvenilirliği sağlar; hiçbir getiri vaadi veya yatırım tavsiyesi içermez.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.observability.metrics import LINEAGE_RECORDS_TOTAL

DEFAULT_LINEAGE_DISCLAIMER: str = (
    "Bu veri ve hesaplama köken kaydı teknik izlenebilirlik ve denetim "
    "amacıyla tutulmaktadır. Yatırım tavsiyesi veya getiri vaadi değildir."
)


class DisplayedPriceLineage(BaseModel):
    """Lineage and calculation metadata for market prices presented to consumers."""

    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(description="Unique audit identifier for this price lineage record")
    symbol: str = Field(description="Ticker symbol (e.g. THYAO)")
    price_type: str = Field(
        default="adjusted", description="Price calculation tier ('adjusted' or 'raw')"
    )
    data_source: str = Field(
        default="official_eod_feed",
        description="Underlying market data provider tier or feed origin",
    )
    calculation_version: str = Field(
        default="v1.0.0",
        description="Data transformation and price processing engine version",
    )
    as_of_utc: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of observation or ingestion (UTC)",
    )
    candle_count: int = Field(ge=0, description="Total candles in the associated price dataset")
    is_downsampled: bool = Field(default=False, description="Whether candle series was downsampled")
    downsample_method: str | None = Field(default=None, description="Downsampling method applied")
    trace_id: str | None = Field(default=None, description="Distributed request trace identifier")
    disclaimer: str = Field(
        default=DEFAULT_LINEAGE_DISCLAIMER, description="Mandatory compliance disclaimer"
    )

    @field_validator("disclaimer")
    @classmethod
    def check_disclaimer(cls, v: str) -> str:
        validate_compliant_text(v)
        return v

    def to_display_attribution(self) -> str:
        """Produce a compact audit attribution notice for UI footers or headers."""
        return (
            f"Kaynak: {self.data_source} | "
            f"Hesaplama: {self.calculation_version} ({self.price_type}) | "
            f"İzleme ID: {self.lineage_id}"
        )


class DisplayedPredictionLineage(BaseModel):
    """Lineage and model/calculation version metadata for inferences presented to consumers."""

    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(
        description="Unique audit identifier for this prediction lineage record"
    )
    symbol: str = Field(description="Ticker symbol (e.g. THYAO)")
    model_id: str = Field(description="Machine learning model unique identifier")
    model_version: str = Field(description="Model registry or Git/DVC artifact version tag")
    calculation_version: str = Field(
        default="v1.0.0",
        description="Inference pipeline and feature pre-processing version",
    )
    data_source: str = Field(
        default="official_eod_feed",
        description="Data feed tier used for model feature inputs",
    )
    data_version: str | None = Field(default=None, description="Dataset DVC or commit version tag")
    evaluation_date: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Inference execution timestamp (UTC)",
    )
    horizon_days: int | None = Field(
        default=None, ge=1, description="Projection forecast window in calendar days"
    )
    predicted_direction: str | None = Field(
        default=None, description="Directional tendency ('upward', 'neutral', 'downward')"
    )
    confidence_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Confidence level or probability"
    )
    status: str = Field(
        default="OK", description="Inference health status ('OK' or 'FALLBACK_TRIGGERED')"
    )
    fallback_reason: str | None = Field(
        default=None, description="Reason code if fallback mode was engaged"
    )
    feature_count: int = Field(default=0, ge=0, description="Number of model input features used")
    trace_id: str | None = Field(default=None, description="Distributed request trace identifier")
    disclaimer: str = Field(
        default=DEFAULT_LINEAGE_DISCLAIMER, description="Mandatory compliance disclaimer"
    )

    @field_validator("disclaimer")
    @classmethod
    def check_disclaimer(cls, v: str) -> str:
        validate_compliant_text(v)
        return v

    def to_display_attribution(self) -> str:
        """Produce a transparent model provenance attribution summary."""
        return (
            f"Kaynak: {self.data_source} | "
            f"Model: {self.model_id} (v{self.model_version}) | "
            f"Hesaplama: {self.calculation_version} | "
            f"Durum: {self.status} | "
            f"İzleme ID: {self.lineage_id}"
        )


class LineageTracker:
    """Thread-safe in-memory ring-buffer registry for tracking
    displayed price and prediction lineages.
    """

    def __init__(self, max_records: int = 5000) -> None:
        if max_records < 10:
            raise ValueError("max_records must be at least 10")
        self._max_records = max_records
        self._lock = threading.RLock()
        self._records: deque[DisplayedPriceLineage | DisplayedPredictionLineage] = deque(
            maxlen=max_records
        )
        self._by_id: dict[str, DisplayedPriceLineage | DisplayedPredictionLineage] = {}

    def record_price(
        self,
        *,
        symbol: str,
        candle_count: int,
        data_source: str = "official_eod_feed",
        price_type: str = "adjusted",
        calculation_version: str = "v1.0.0",
        as_of_utc: datetime | None = None,
        is_downsampled: bool = False,
        downsample_method: str | None = None,
        trace_id: str | None = None,
        lineage_id: str | None = None,
        disclaimer: str = DEFAULT_LINEAGE_DISCLAIMER,
    ) -> DisplayedPriceLineage:
        """Register a displayed price dataset lineage record."""
        clean_symbol = symbol.strip().upper()
        effective_id = lineage_id or f"lin_prc_{uuid.uuid4().hex[:12]}"
        effective_time = as_of_utc or datetime.now(UTC)

        record = DisplayedPriceLineage(
            lineage_id=effective_id,
            symbol=clean_symbol,
            price_type=price_type,
            data_source=data_source.strip(),
            calculation_version=calculation_version.strip(),
            as_of_utc=effective_time,
            candle_count=candle_count,
            is_downsampled=is_downsampled,
            downsample_method=downsample_method,
            trace_id=trace_id,
            disclaimer=disclaimer,
        )

        with self._lock:
            # If ring buffer is full, remove oldest from _by_id
            if len(self._records) == self._max_records:
                oldest = self._records[0]
                self._by_id.pop(oldest.lineage_id, None)

            self._records.append(record)
            self._by_id[record.lineage_id] = record

        LINEAGE_RECORDS_TOTAL.inc(
            labels={"record_type": "price", "data_source": record.data_source}
        )
        return record

    def record_prediction(
        self,
        *,
        symbol: str,
        model_id: str,
        model_version: str,
        calculation_version: str = "v1.0.0",
        data_source: str = "official_eod_feed",
        data_version: str | None = None,
        evaluation_date: datetime | None = None,
        horizon_days: int | None = None,
        predicted_direction: str | None = None,
        confidence_score: float | None = None,
        status: str = "OK",
        fallback_reason: str | None = None,
        feature_count: int = 0,
        trace_id: str | None = None,
        lineage_id: str | None = None,
        disclaimer: str = DEFAULT_LINEAGE_DISCLAIMER,
    ) -> DisplayedPredictionLineage:
        """Register a displayed prediction inference lineage record."""
        clean_symbol = symbol.strip().upper()
        effective_id = lineage_id or f"lin_prd_{uuid.uuid4().hex[:12]}"
        effective_time = evaluation_date or datetime.now(UTC)

        record = DisplayedPredictionLineage(
            lineage_id=effective_id,
            symbol=clean_symbol,
            model_id=model_id.strip(),
            model_version=model_version.strip(),
            calculation_version=calculation_version.strip(),
            data_source=data_source.strip(),
            data_version=data_version,
            evaluation_date=effective_time,
            horizon_days=horizon_days,
            predicted_direction=predicted_direction,
            confidence_score=confidence_score,
            status=status,
            fallback_reason=fallback_reason,
            feature_count=feature_count,
            trace_id=trace_id,
            disclaimer=disclaimer,
        )

        with self._lock:
            if len(self._records) == self._max_records:
                oldest = self._records[0]
                self._by_id.pop(oldest.lineage_id, None)

            self._records.append(record)
            self._by_id[record.lineage_id] = record

        LINEAGE_RECORDS_TOTAL.inc(
            labels={"record_type": "prediction", "data_source": record.data_source}
        )
        return record

    def get_by_lineage_id(
        self, lineage_id: str
    ) -> DisplayedPriceLineage | DisplayedPredictionLineage | None:
        """Find a lineage record by its unique audit identifier."""
        with self._lock:
            return self._by_id.get(lineage_id)

    def list_by_symbol(
        self, symbol: str, limit: int = 50
    ) -> list[DisplayedPriceLineage | DisplayedPredictionLineage]:
        """List lineage records for a specific symbol in reverse chronological order."""
        clean = symbol.strip().upper()
        with self._lock:
            matches = [r for r in reversed(self._records) if r.symbol == clean]
            return matches[:limit]

    def list_by_trace_id(
        self, trace_id: str
    ) -> list[DisplayedPriceLineage | DisplayedPredictionLineage]:
        """Find all lineage records associated with a distributed request trace ID."""
        clean = trace_id.strip()
        with self._lock:
            return [r for r in self._records if r.trace_id == clean]

    def clear(self) -> None:
        """Reset all tracked lineage records (primarily for testing)."""
        with self._lock:
            self._records.clear()
            self._by_id.clear()

    def count(self) -> int:
        """Return total number of retained lineage records."""
        with self._lock:
            return len(self._records)


# Global singleton instance
LINEAGE_TRACKER: LineageTracker = LineageTracker()

__all__ = [
    "DEFAULT_LINEAGE_DISCLAIMER",
    "DisplayedPredictionLineage",
    "DisplayedPriceLineage",
    "LINEAGE_RECORDS_TOTAL",
    "LINEAGE_TRACKER",
    "LineageTracker",
]
