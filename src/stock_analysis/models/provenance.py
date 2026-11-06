"""Tahmin kökeni ve model izlenebilirliği (Forecast Provenance & Model Lineage).

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Her finansal tahmin, onu üreten modelin sürümü (model_version / model_id),
  eğitim tarihi (trained_at), veri aralığı (data_range_start / data_range_end),
  kullanılan özellik seti (feature_set), hedef türü ve ufku ile açıkça ilişkilendirilir.
- Kökeni doğrulanmamış, modeli veya eğitim parametreleri bilinmeyen hiçbir tahmin
  kullanıcıya sunulamaz veya canlı sisteme aktarılamaz (MissingForecastProvenanceError).
- Tahmin kökeni denetim izlenebilirliği sağlar ve SPK mevzuatına uygun yasal uyarı içerir.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stock_analysis.indicators.trend import validate_compliant_text

DEFAULT_PROVENANCE_DISCLAIMER = (
    "Bu tahmin meta verisi ve model köken kaydı, sistem teknik izlenebilirliği ve "
    "denetlenebilirliği amacıyla üretilmiştir. Tahmin çıktısı geçmiş verilere ve "
    "istatistiksel modellemeye dayanmakta olup hiçbir kesinlik veya getiri vaadi içermez. "
    "SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında değildir."
)

_SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class ProvenanceError(Exception):
    """Tahmin kökeni ve model meta verisi genel hata sınıfı."""


class MissingForecastProvenanceError(ProvenanceError):
    """Tahmin nesnesinde zorunlu model köken bilgisi bulunmadığında fırlatılır."""


class InvalidProvenanceMetadataError(ProvenanceError):
    """Model sürümü, veri aralığı veya özellik seti geçersiz olduğunda fırlatılır."""


def _validate_safe_identifier(val: str, field_name: str) -> None:
    if not val or not _SAFE_ID_REGEX.match(val) or ".." in val:
        raise InvalidProvenanceMetadataError(
            f"Geçersiz veya güvensiz {field_name}: '{val}'. "
            "Yalnızca alfanümerik, tire, alt çizgi ve nokta içerebilir."
        )


class ForecastProvenance(BaseModel):
    """Tek bir tahminin arkasındaki model sürümü, veri penceresi ve özellik seti köken kaydı."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(
        description="Tahmini üreten modelin benzersiz kimliği (örn. 'bist_mlp_v1.0.0')"
    )
    model_version: str = Field(description="Model sürümü (örn. '1.0.0' veya Git/DVC sürüm etiketi)")
    trained_at: datetime = Field(description="Modelin eğitim tamamlanma tarihi ve saati (UTC)")
    data_range_start: str = Field(
        description="Eğitim veri setinin başlangıç tarihi (ISO format YYYY-MM-DD)"
    )
    data_range_end: str = Field(
        description="Eğitim veri setinin bitiş tarihi (ISO format YYYY-MM-DD)"
    )
    feature_set: list[str] = Field(
        min_length=1, description="Modelin girdi olarak kullandığı özellik adları"
    )
    target_type: str = Field(
        default="percentage_return",
        description="Hedef türü (örn. 'percentage_return', 'direction')",
    )
    target_horizon: str = Field(default="1d", description="Tahmin ufku (örn. '1d', '5d', '20d')")
    data_version: str | None = Field(default=None, description="Veri seti sürümü / DVC hash'i")
    data_split_id: str | None = Field(default=None, description="Eğitim/test bölünme tanımlayıcısı")
    model_hash_sha256: str | None = Field(
        default=None, description="Model ağırlıkları SHA-256 kriptografik özeti"
    )
    disclaimer: str = Field(
        default=DEFAULT_PROVENANCE_DISCLAIMER, description="SPK uyumlu yasal uyarı"
    )

    @field_validator("model_id")
    @classmethod
    def check_model_id(cls, v: str) -> str:
        _validate_safe_identifier(v, "model_id")
        return v

    @field_validator("model_version")
    @classmethod
    def check_model_version(cls, v: str) -> str:
        _validate_safe_identifier(v, "model_version")
        return v

    @field_validator("disclaimer")
    @classmethod
    def check_disclaimer(cls, v: str) -> str:
        validate_compliant_text(v)
        return v

    @classmethod
    def create(
        cls,
        *,
        model_id: str,
        model_version: str,
        trained_at: datetime | str,
        data_range_start: str | date | datetime,
        data_range_end: str | date | datetime,
        feature_set: Sequence[str],
        target_type: str = "percentage_return",
        target_horizon: str = "1d",
        data_version: str | None = None,
        data_split_id: str | None = None,
        model_hash_sha256: str | None = None,
        disclaimer: str = DEFAULT_PROVENANCE_DISCLAIMER,
    ) -> ForecastProvenance:
        """Doğrulanmış ForecastProvenance örneği oluşturan fabrika metodu."""
        # Feature seti temizle ve sırala
        cleaned_features = [f.strip() for f in feature_set if f and f.strip()]
        if not cleaned_features:
            raise InvalidProvenanceMetadataError(
                "Tahmin kökeni için özellik seti (feature_set) boş olamaz."
            )

        # Tarih aralığı doğrulama
        def _to_date_str(val: str | date | datetime) -> str:
            if isinstance(val, datetime):
                return val.date().isoformat()
            if isinstance(val, date):
                return val.isoformat()
            parts = str(val).split("T")[0].split(" ")[0].strip()
            date.fromisoformat(parts)
            return parts

        start_str = _to_date_str(data_range_start)
        end_str = _to_date_str(data_range_end)

        if start_str > end_str:
            raise InvalidProvenanceMetadataError(
                f"Veri aralığı başlangıcı ({start_str}) bitişinden ({end_str}) sonra olamaz."
            )

        # Trained at doğrulama
        if isinstance(trained_at, str):
            dt = datetime.fromisoformat(trained_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        else:
            dt = trained_at if trained_at.tzinfo is not None else trained_at.replace(tzinfo=UTC)

        return cls(
            model_id=model_id,
            model_version=model_version,
            trained_at=dt,
            data_range_start=start_str,
            data_range_end=end_str,
            feature_set=sorted(set(cleaned_features)),
            target_type=target_type,
            target_horizon=target_horizon,
            data_version=data_version,
            data_split_id=data_split_id,
            model_hash_sha256=model_hash_sha256,
            disclaimer=disclaimer,
        )

    @classmethod
    def from_artifact_record(
        cls,
        record: Any,
        *,
        data_range_start: str | date | datetime,
        data_range_end: str | date | datetime,
        model_version: str | None = None,
        disclaimer: str = DEFAULT_PROVENANCE_DISCLAIMER,
    ) -> ForecastProvenance:
        """ModelArtifactRecord nesnesinden otomatik olarak ForecastProvenance üretir."""
        ver = model_version or "1.0.0"
        feature_names = getattr(record, "feature_names", []) or []
        model_id = getattr(record, "model_id", "unknown_model")
        trained_at = getattr(record, "trained_at", datetime.now(UTC))
        target_type = getattr(record, "target_type", "percentage_return")
        target_horizon = getattr(record, "target_horizon", "1d")
        data_version = getattr(record, "data_version", None)
        data_split_id = getattr(record, "data_split_id", None)
        weights_sha = getattr(record, "weights_sha256", None)

        return cls.create(
            model_id=model_id,
            model_version=ver,
            trained_at=trained_at,
            data_range_start=data_range_start,
            data_range_end=data_range_end,
            feature_set=feature_names,
            target_type=target_type,
            target_horizon=target_horizon,
            data_version=data_version,
            data_split_id=data_split_id,
            model_hash_sha256=weights_sha,
            disclaimer=disclaimer,
        )

    def to_display_summary(self) -> str:
        """Kullanıcı arayüzü ve raporlama için şeffaf model köken özeti üretir."""
        date_str = self.trained_at.strftime("%Y-%m-%d")
        feat_count = len(self.feature_set)
        feat_preview = ", ".join(self.feature_set[:3])
        if feat_count > 3:
            feat_preview += f" (+{feat_count - 3} özellik)"

        summary = (
            f"Model: {self.model_id} (v{self.model_version}) | "
            f"Eğitim: {date_str} | "
            f"Veri Penceresi: {self.data_range_start} -> {self.data_range_end} | "
            f"Özellikler ({feat_count}): [{feat_preview}] | "
            f"Hedef: {self.target_type} ({self.target_horizon})"
        )
        return summary


def verify_forecast_provenance(
    forecast: Any,
    required_features: Sequence[str] | None = None,
) -> ForecastProvenance:
    """Bir tahminin zorunlu 4 köken alanını doğrular:
    Model sürümü, eğitim tarihi, veri aralığı, özellik seti.

    FAZ-11 Kuralı:
    Herhangi biri eksik veya boşsa MissingForecastProvenanceError fırlatır.
    """
    prov = getattr(forecast, "provenance", None)
    if prov is None:
        raise MissingForecastProvenanceError(
            "FAZ-11 Kuralı: Tahmin kökeni (provenance) eksik! "
            "Her tahmin model sürümü, eğitim tarihi, veri aralığı ve "
            "özellik setiyle ilişkilendirilmelidir."
        )

    if not prov.model_id or not prov.model_version:
        raise InvalidProvenanceMetadataError("Model kimliği ve sürümü zorunludur.")
    if not prov.trained_at:
        raise InvalidProvenanceMetadataError("Model eğitim tarihi zorunludur.")
    if not prov.data_range_start or not prov.data_range_end:
        raise InvalidProvenanceMetadataError(
            "Veri aralığı başlangıç ve bitiş tarihleri zorunludur."
        )
    if not prov.feature_set:
        raise InvalidProvenanceMetadataError("Modelin özellik seti (feature_set) boş olamaz.")

    if required_features:
        missing = [f for f in required_features if f not in prov.feature_set]
        if missing:
            raise InvalidProvenanceMetadataError(
                f"Tahmin kökeninde beklenen özellikler bulunamadı: {missing}"
            )

    if not isinstance(prov, ForecastProvenance):
        raise InvalidProvenanceMetadataError(
            f"Tahmin köken nesnesi ForecastProvenance tipinde olmalıdır, gelen: {type(prov)}"
        )

    return prov
