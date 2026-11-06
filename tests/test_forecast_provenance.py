"""Tahmin kökeni ve model izlenebilirliği birim testleri (FAZ-11).

Kural: Model sürümü, eğitim tarihi, veri aralığı ve özellik seti her tahminle ilişkilendirilir.
"""

from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel, ValidationError

from stock_analysis.models.provenance import (
    ForecastProvenance,
    InvalidProvenanceMetadataError,
    MissingForecastProvenanceError,
    verify_forecast_provenance,
)
from stock_analysis.models.uncertainty import (
    build_uncertainty_forecast,
)


class MockArtifactRecord(BaseModel):
    model_id: str = "bist30_mlp_v1.0"
    trained_at: datetime = datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
    feature_names: list[str] = ["rsi_14", "macd", "bollinger_pband", "atr_14", "volume_zscore"]
    target_type: str = "percentage_return"
    target_horizon: str = "5d"
    data_version: str = "dvc://v1.2.0"
    data_split_id: str = "split_2020_2025"
    weights_sha256: str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_forecast_provenance_creation_valid() -> None:
    prov = ForecastProvenance.create(
        model_id="bist_mlp_v1",
        model_version="1.0.0",
        trained_at=datetime(2026, 10, 24, 10, 0, tzinfo=UTC),
        data_range_start="2020-01-01",
        data_range_end="2024-12-31",
        feature_set=["rsi_14", "sma_20", "volume_ma"],
        target_type="percentage_return",
        target_horizon="1d",
        data_version="v2.1",
        data_split_id="train_split_a",
    )

    assert prov.model_id == "bist_mlp_v1"
    assert prov.model_version == "1.0.0"
    assert prov.data_range_start == "2020-01-01"
    assert prov.data_range_end == "2024-12-31"
    assert len(prov.feature_set) == 3
    assert "rsi_14" in prov.feature_set
    assert prov.target_type == "percentage_return"
    assert prov.target_horizon == "1d"
    assert "SPK" in prov.disclaimer


def test_forecast_provenance_rejects_empty_features() -> None:
    with pytest.raises(InvalidProvenanceMetadataError, match="özellik seti"):
        ForecastProvenance.create(
            model_id="bist_model",
            model_version="1.0.0",
            trained_at=datetime.now(UTC),
            data_range_start="2020-01-01",
            data_range_end="2024-01-01",
            feature_set=[],
        )


def test_forecast_provenance_rejects_invalid_date_range() -> None:
    with pytest.raises(InvalidProvenanceMetadataError, match="Veri aralığı başlangıcı"):
        ForecastProvenance.create(
            model_id="bist_model",
            model_version="1.0.0",
            trained_at=datetime.now(UTC),
            data_range_start="2024-12-31",
            data_range_end="2020-01-01",
            feature_set=["rsi_14"],
        )


def test_forecast_provenance_rejects_unsafe_identifiers() -> None:
    with pytest.raises((InvalidProvenanceMetadataError, ValidationError)):
        ForecastProvenance.create(
            model_id="../malicious_path/model",
            model_version="1.0.0",
            trained_at=datetime.now(UTC),
            data_range_start="2020-01-01",
            data_range_end="2024-01-01",
            feature_set=["rsi_14"],
        )

    with pytest.raises((InvalidProvenanceMetadataError, ValidationError)):
        ForecastProvenance.create(
            model_id="safe_id",
            model_version="1.0.0; rm -rf",
            trained_at=datetime.now(UTC),
            data_range_start="2020-01-01",
            data_range_end="2024-01-01",
            feature_set=["rsi_14"],
        )


def test_from_artifact_record() -> None:
    record = MockArtifactRecord()
    prov = ForecastProvenance.from_artifact_record(
        record,
        data_range_start=date(2020, 1, 1),
        data_range_end=date(2025, 12, 31),
        model_version="1.0.0",
    )

    assert prov.model_id == "bist30_mlp_v1.0"
    assert prov.model_version == "1.0.0"
    assert prov.data_range_start == "2020-01-01"
    assert prov.data_range_end == "2025-12-31"
    assert len(prov.feature_set) == 5
    assert prov.target_type == "percentage_return"
    assert prov.target_horizon == "5d"
    assert prov.model_hash_sha256 == record.weights_sha256


def test_forecast_with_provenance_lifecycle() -> None:
    prov = ForecastProvenance.create(
        model_id="bist_cnn_v2",
        model_version="2.0.1",
        trained_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC),
        data_range_start="2018-01-01",
        data_range_end="2023-12-31",
        feature_set=["rsi_14", "sma_50", "atr_14"],
        target_type="percentage_return",
        target_horizon="1d",
    )

    # 1. build_uncertainty_forecast içinde doğrudan provenance bağlama
    forecast = build_uncertainty_forecast(
        point_forecast=0.015,
        aleatoric_std=0.01,
        provenance=prov,
    )

    assert forecast.provenance is not None
    assert forecast.get_provenance().model_id == "bist_cnn_v2"
    assert forecast.get_provenance().model_version == "2.0.1"
    assert forecast.get_provenance().data_range_start == "2018-01-01"

    # 2. verify_forecast_provenance başarılı geçmeli
    verified_prov = verify_forecast_provenance(forecast, required_features=["rsi_14", "atr_14"])
    assert verified_prov.model_id == "bist_cnn_v2"


def test_missing_provenance_raises_error() -> None:
    # Provenance verilmeden oluşturulan tahmin
    forecast = build_uncertainty_forecast(
        point_forecast=0.01,
        aleatoric_std=0.01,
    )

    assert forecast.provenance is None

    with pytest.raises(MissingForecastProvenanceError, match="Model sürümü|denetlenemez"):
        forecast.get_provenance()

    with pytest.raises(MissingForecastProvenanceError, match="Tahmin kökeni"):
        verify_forecast_provenance(forecast)


def test_with_provenance_chaining() -> None:
    forecast = build_uncertainty_forecast(
        point_forecast=0.02,
        aleatoric_std=0.012,
    )
    assert forecast.provenance is None

    prov = ForecastProvenance.create(
        model_id="bist_chained",
        model_version="1.0.0",
        trained_at=datetime.now(UTC),
        data_range_start="2021-01-01",
        data_range_end="2024-06-30",
        feature_set=["macd", "rsi_14"],
    )

    attributed = forecast.with_provenance(prov)
    assert attributed.provenance is not None
    assert attributed.get_provenance().model_id == "bist_chained"
    # Orijinal nesne değişmez (immutable) kalır
    assert forecast.provenance is None


def test_display_text_transparency_and_spk_compliance() -> None:
    prov = ForecastProvenance.create(
        model_id="bist_prod_mlp",
        model_version="3.1.0",
        trained_at=datetime(2026, 10, 24, 9, 30, tzinfo=UTC),
        data_range_start="2019-01-01",
        data_range_end="2024-10-01",
        feature_set=["rsi_14", "sma_20", "atr_14", "volume_zscore", "macd"],
        target_type="percentage_return",
        target_horizon="5d",
    )

    forecast = build_uncertainty_forecast(
        point_forecast=0.018,
        aleatoric_std=0.015,
        provenance=prov,
    )

    text_with_prov = forecast.format_display_text(include_provenance=True)
    assert "Model: bist_prod_mlp (v3.1.0)" in text_with_prov
    assert "2019-01-01 -> 2024-10-01" in text_with_prov
    assert "Özellikler (5)" in text_with_prov
    assert "Hedef: percentage_return (5d)" in text_with_prov
    assert "SPK mevzuatı uyarınca" in text_with_prov

    text_without_prov = forecast.format_display_text(include_provenance=False)
    assert "Model: bist_prod_mlp" not in text_without_prov
    assert "SPK mevzuatı uyarınca" in text_without_prov


def test_verify_forecast_provenance_missing_feature() -> None:
    prov = ForecastProvenance.create(
        model_id="bist_model",
        model_version="1.0.0",
        trained_at=datetime.now(UTC),
        data_range_start="2020-01-01",
        data_range_end="2024-01-01",
        feature_set=["rsi_14"],
    )
    forecast = build_uncertainty_forecast(
        point_forecast=0.01,
        aleatoric_std=0.01,
        provenance=prov,
    )

    with pytest.raises(InvalidProvenanceMetadataError, match="beklenen özellikler bulunamadı"):
        verify_forecast_provenance(forecast, required_features=["rsi_14", "volume_ma"])
