"""Tahmin belirsizliği, aralıklar ve yön olasılığı testleri."""

import pytest

from stock_analysis.models.uncertainty import (
    DEFAULT_UNCERTAINTY_DISCLAIMER,
    ConformalIntervalCalibrator,
    Direction,
    DirectionalProbabilities,
    DirectionalProbabilityCalibrator,
    EnsembleUncertaintyEstimator,
    IntervalBound,
    NakedPointEstimateDisallowedError,
    UncertaintyBreakdown,
    UncertaintyForecast,
    UncertaintyLevel,
    build_uncertainty_forecast,
)


def test_interval_bound_creation_and_ordering() -> None:
    bound = IntervalBound.create(
        confidence_level=0.80,
        lower_bound=-0.02,
        upper_bound=0.03,
        median_point=0.005,
    )
    assert bound.confidence_level == 0.80
    assert bound.lower_bound == -0.02
    assert bound.upper_bound == 0.03
    assert bound.median_point == 0.005
    assert pytest.approx(bound.interval_width) == 0.05


def test_interval_bound_invalid_ordering_raises() -> None:
    # lower > upper
    with pytest.raises(ValueError, match="büyük olamaz"):
        IntervalBound.create(
            confidence_level=0.90,
            lower_bound=0.05,
            upper_bound=0.01,
            median_point=0.02,
        )

    # median outside range
    with pytest.raises(ValueError, match="aralığında yer almalıdır"):
        IntervalBound.create(
            confidence_level=0.90,
            lower_bound=0.01,
            upper_bound=0.05,
            median_point=0.08,
        )


def test_directional_probabilities_normalization_and_entropy() -> None:
    # 60% artış, 30% azalış, 10% yatay
    probs = DirectionalProbabilities.create(prob_up=6.0, prob_down=3.0, prob_neutral=1.0)
    assert pytest.approx(probs.prob_up, abs=1e-3) == 0.60
    assert pytest.approx(probs.prob_down, abs=1e-3) == 0.30
    assert pytest.approx(probs.prob_neutral, abs=1e-3) == 0.10
    assert probs.most_likely_direction == Direction.UP
    assert probs.directional_entropy > 0.5


def test_directional_probabilities_invalid_sum_raises() -> None:
    with pytest.raises(ValueError, match="Geçersiz olasılık"):
        DirectionalProbabilities.create(prob_up=0.0, prob_down=0.0, prob_neutral=0.0)


def test_naked_point_estimate_protection() -> None:
    bound = IntervalBound.create(
        confidence_level=0.80,
        lower_bound=-0.01,
        upper_bound=0.03,
        median_point=0.01,
    )
    probs = DirectionalProbabilities.create(prob_up=0.55, prob_down=0.45)
    breakdown = UncertaintyBreakdown(
        aleatoric_std=0.01,
        epistemic_std=0.005,
        total_std=0.011,
        level=UncertaintyLevel.LOW,
    )

    forecast = UncertaintyForecast(
        point_forecast=0.01,
        intervals=[bound],
        probabilities=probs,
        uncertainty=breakdown,
        disclaimer=DEFAULT_UNCERTAINTY_DISCLAIMER,
        allow_naked_point=False,
    )

    # Doğrudan tek sayı çekmeye çalışıldığında hata fırlatılmalı
    with pytest.raises(NakedPointEstimateDisallowedError):
        forecast.get_naked_point_estimate()

    # İzin verildiğinde okunabilmeli
    permissive_forecast = forecast.model_copy(update={"allow_naked_point": True})
    assert permissive_forecast.get_naked_point_estimate() == 0.01


def test_format_display_text_and_compliance() -> None:
    forecast = build_uncertainty_forecast(
        point_forecast=0.015,
        aleatoric_std=0.012,
        ensemble_predictions=[0.013, 0.017, 0.015],
        confidence_levels=[0.80, 0.95],
    )

    text = forecast.format_display_text(percentage_mode=True)
    assert "Tahmin Aralığı" in text
    assert "80% Güven" in text
    assert "Yön Olasılığı" in text
    assert "Belirsizlik Seviyesi" in text
    assert "SPK" in text or "yatırım danışmanlığı" in text


def test_conformal_interval_calibrator() -> None:
    residuals = [0.005, -0.012, 0.008, -0.003, 0.015, -0.020, 0.002, -0.007, 0.011, -0.009]
    calibrator = ConformalIntervalCalibrator(residuals)

    bound_80 = calibrator.create_interval(point_forecast=0.02, confidence_level=0.80)
    bound_95 = calibrator.create_interval(point_forecast=0.02, confidence_level=0.95)

    assert bound_80.lower_bound <= bound_80.median_point <= bound_80.upper_bound
    assert bound_95.lower_bound <= bound_95.median_point <= bound_95.upper_bound
    # Yüksek güven düzeyi daha geniş olmalıdır
    assert bound_95.interval_width >= bound_80.interval_width


def test_conformal_calibrator_insufficient_data_raises() -> None:
    with pytest.raises(ValueError, match="en az 5 artık"):
        ConformalIntervalCalibrator([0.01, -0.01])


def test_ensemble_uncertainty_estimator() -> None:
    estimator = EnsembleUncertaintyEstimator()
    # Benzer tahminler -> düşük epistemik varyans
    tight_ensemble = [0.010, 0.011, 0.0105]
    tight_breakdown = estimator.estimate_uncertainty(tight_ensemble, aleatoric_std=0.005)
    assert tight_breakdown.epistemic_std < 0.002
    assert tight_breakdown.level in (UncertaintyLevel.LOW, UncertaintyLevel.MEDIUM)

    # Çok farklı tahminler -> yüksek epistemik varyans
    divergent_ensemble = [-0.08, 0.09, 0.01, -0.04, 0.07]
    div_breakdown = estimator.estimate_uncertainty(divergent_ensemble, aleatoric_std=0.01)
    assert div_breakdown.epistemic_std > 0.05
    assert div_breakdown.level in (UncertaintyLevel.HIGH, UncertaintyLevel.EXTREME)


def test_directional_probability_calibrator() -> None:
    # Kuvvetli pozitif getiri ve makul varyans -> prob_up yüksek olmalı
    probs_up = DirectionalProbabilityCalibrator.calculate_probabilities(
        expected_return=0.03,
        total_std=0.01,
        threshold=0.002,
    )
    assert probs_up.prob_up > 0.90
    assert probs_up.prob_down < 0.01
    assert probs_up.most_likely_direction == Direction.UP

    # Negatif getiri -> prob_down yüksek olmalı
    probs_down = DirectionalProbabilityCalibrator.calculate_probabilities(
        expected_return=-0.04,
        total_std=0.01,
        threshold=0.002,
    )
    assert probs_down.prob_down > 0.90
    assert probs_down.most_likely_direction == Direction.DOWN

    # Sıfır etrafında dar getiri -> yatay/nötr olasılığı olmalı
    probs_flat = DirectionalProbabilityCalibrator.calculate_probabilities(
        expected_return=0.0001,
        total_std=0.001,
        threshold=0.002,
    )
    assert probs_flat.prob_neutral > probs_flat.prob_up


def test_build_uncertainty_forecast_conformal_vs_parametric() -> None:
    residuals = [0.01, -0.01, 0.02, -0.02, 0.015, -0.005]

    # Konformal aktif
    fc_conformal = build_uncertainty_forecast(
        point_forecast=0.02,
        aleatoric_std=0.01,
        calibration_residuals=residuals,
        confidence_levels=[0.80, 0.95],
    )
    assert len(fc_conformal.intervals) == 2
    b80 = fc_conformal.get_interval(0.80)
    assert b80.lower_bound < 0.02 < b80.upper_bound

    # Konformal yokken parametrik fallback
    fc_parametric = build_uncertainty_forecast(
        point_forecast=-0.015,
        aleatoric_std=0.015,
        confidence_levels=[0.80, 0.95],
    )
    assert len(fc_parametric.intervals) == 2
    assert fc_parametric.probabilities.prob_down > fc_parametric.probabilities.prob_up
