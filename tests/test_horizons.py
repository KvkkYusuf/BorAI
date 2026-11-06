"""Tahmin ufukları ve sonuç ayrıştırma testleri (FAZ-11)."""

import pytest

from stock_analysis.models.horizons import (
    ForecastHorizon,
    HorizonEvaluationRecord,
    HorizonMismatchError,
    HorizonSeparationRegistry,
)
from stock_analysis.models.targets import (
    TargetConfig,
    TargetGenerator,
    TargetType,
)


def test_forecast_horizon_definitions() -> None:
    """Standart tahmin ufukları (1 gün, 5 gün, 20 işlem günü) tanımlarını doğrular."""
    assert ForecastHorizon.DAY_1.steps == 1
    assert ForecastHorizon.DAY_1.value == "1d"
    assert "Günlük" in ForecastHorizon.DAY_1.label

    assert ForecastHorizon.DAY_5.steps == 5
    assert ForecastHorizon.DAY_5.value == "5d"
    assert "1 Hafta" in ForecastHorizon.DAY_5.label

    assert ForecastHorizon.DAY_20.steps == 20
    assert ForecastHorizon.DAY_20.value == "20d"
    assert "1 Ay" in ForecastHorizon.DAY_20.label


def test_forecast_horizon_from_steps() -> None:
    """Adım sayısından standart ufuk çözümleme ve geçersiz adım denetimi."""
    assert ForecastHorizon.from_steps(1) == ForecastHorizon.DAY_1
    assert ForecastHorizon.from_steps(5) == ForecastHorizon.DAY_5
    assert ForecastHorizon.from_steps(20) == ForecastHorizon.DAY_20

    with pytest.raises(ValueError, match="Desteklenmeyen tahmin ufkuna"):
        ForecastHorizon.from_steps(10)


def test_target_config_horizon_integration() -> None:
    """TargetConfig içinde ForecastHorizon kullanımını doğrular."""
    # Horizon nesnesi ile yapılandırma
    cfg1 = TargetConfig(target_type=TargetType.LOG_RETURN, horizon=ForecastHorizon.DAY_5)
    assert cfg1.horizon == ForecastHorizon.DAY_5
    assert cfg1.horizon_steps == 5

    # horizon_steps ile otomatik çözümleme
    cfg2 = TargetConfig(target_type=TargetType.PERCENT_RETURN, horizon_steps=20)
    assert cfg2.horizon == ForecastHorizon.DAY_20
    assert cfg2.horizon_steps == 20

    # Generator ile seriye taşınması
    prices = [100.0, 102.0, 105.0, 103.0, 108.0, 110.0]
    gen = TargetGenerator(cfg1)
    series = gen.compute_target(prices)
    assert series.horizon == ForecastHorizon.DAY_5
    assert series.horizon_steps == 5


def test_horizon_evaluation_record_validation() -> None:
    """HorizonEvaluationRecord doğrulama kontrolleri."""
    rec = HorizonEvaluationRecord(
        horizon=ForecastHorizon.DAY_1,
        model_name="NaifBaseline",
        target_type=TargetType.LOG_RETURN,
        metrics={"mae": 0.012, "rmse": 0.015},
        sample_count=100,
    )
    assert rec.horizon == ForecastHorizon.DAY_1
    assert rec.metrics["mae"] == 0.012

    with pytest.raises(ValueError, match="Örneklem sayısı pozitif olmalıdır"):
        HorizonEvaluationRecord(
            horizon=ForecastHorizon.DAY_1,
            model_name="NaifBaseline",
            target_type=TargetType.LOG_RETURN,
            metrics={"mae": 0.012},
            sample_count=0,
        )

    with pytest.raises(ValueError, match="En az bir değerlendirme metriği"):
        HorizonEvaluationRecord(
            horizon=ForecastHorizon.DAY_1,
            model_name="NaifBaseline",
            target_type=TargetType.LOG_RETURN,
            metrics={},
            sample_count=50,
        )


def test_horizon_separation_registry_isolation() -> None:
    """Farklı ufukların kayıt defterinde kesinlikle izole tutulduğunu doğrular."""
    registry = HorizonSeparationRegistry()

    rec_1d_a = HorizonEvaluationRecord(
        horizon=ForecastHorizon.DAY_1,
        model_name="Model_A_1d",
        target_type=TargetType.DIRECTION_CLASSIFICATION,
        metrics={"accuracy": 0.58},
        sample_count=200,
    )
    rec_1d_b = HorizonEvaluationRecord(
        horizon=ForecastHorizon.DAY_1,
        model_name="Model_B_1d",
        target_type=TargetType.DIRECTION_CLASSIFICATION,
        metrics={"accuracy": 0.62},
        sample_count=200,
    )
    rec_5d = HorizonEvaluationRecord(
        horizon=ForecastHorizon.DAY_5,
        model_name="Model_5d",
        target_type=TargetType.DIRECTION_CLASSIFICATION,
        metrics={"accuracy": 0.54},
        sample_count=180,
    )

    registry.register_evaluation(rec_1d_a)
    registry.register_evaluation(rec_1d_b)
    registry.register_evaluation(rec_5d)

    # 1 günlük ufuk kayıtları yalnızca 1 günlük modelleri içerir
    records_1d = registry.get_records(ForecastHorizon.DAY_1)
    assert len(records_1d) == 2
    assert all(r.horizon == ForecastHorizon.DAY_1 for r in records_1d)

    # 5 günlük ufuk kayıtları bağımsızdır
    records_5d = registry.get_records(ForecastHorizon.DAY_5)
    assert len(records_5d) == 1
    assert records_5d[0].model_name == "Model_5d"

    # 20 günlük henüz boş
    assert len(registry.get_records(ForecastHorizon.DAY_20)) == 0

    # Model kıyaslama aynı ufuk içinde sıralar
    ranking_1d = registry.compare_models(
        ForecastHorizon.DAY_1, metric_name="accuracy", higher_is_better=True
    )
    assert ranking_1d == [("Model_B_1d", 0.62), ("Model_A_1d", 0.58)]


def test_cross_horizon_contamination_prevention() -> None:
    """FAZ-11 kuralı: Farklı ufuklar karıştırıldığında HorizonMismatchError fırlatılır."""
    rec_1d = HorizonEvaluationRecord(
        horizon=ForecastHorizon.DAY_1,
        model_name="Model1D",
        target_type=TargetType.LOG_RETURN,
        metrics={"mae": 0.01},
        sample_count=100,
    )
    rec_20d = HorizonEvaluationRecord(
        horizon=ForecastHorizon.DAY_20,
        model_name="Model20D",
        target_type=TargetType.LOG_RETURN,
        metrics={"mae": 0.04},
        sample_count=100,
    )

    # Aynı ufuk grubu güvenle doğrulanır
    assert (
        HorizonSeparationRegistry.assert_no_cross_horizon_contamination([rec_1d, rec_1d])
        == ForecastHorizon.DAY_1
    )

    # Farklı ufuklar bir araya getirilirse hata fırlatılır
    with pytest.raises(HorizonMismatchError, match="FAZ-11 Kuralı İhlali: Farklı ufuklar"):
        HorizonSeparationRegistry.assert_no_cross_horizon_contamination([rec_1d, rec_20d])

    # Boş liste hatası
    with pytest.raises(ValueError, match="boş olamaz"):
        HorizonSeparationRegistry.assert_no_cross_horizon_contamination([])
