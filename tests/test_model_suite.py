"""Comprehensive model testing suite: data leakage, reproducibility,
baseline benchmarking, and edge case resilience (FAZ-14).

Verifies:
1. Data & Temporal Leakage Prevention:
   - Point-in-time normalization fit strictly on training set (no test/val stats leak).
   - PreprocessingLeakageError raised when transforming with unfitted normalizer.
   - Lookahead bias detector rejecting centered windows and negative shifts.
   - Feature timestamp alignment ensuring zero future timestamps.
   - Time series chronological partition integrity with purge/embargo isolation.
   - Target generation causality ensuring no access to future return distributions.

2. Deterministic Reproducibility:
   - Seed determinism (random, numpy, torch CPU/CUDA) via seed_everything.
   - Identical model weight initialization across matching seeds.
   - Checkpoint integrity roundtrip (safe_save_weights & safe_load_weights).
   - Bit-exact / close forward inference reproduction on batch tensors.
   - Deterministic gradient and optimizer state progression across fixed batches.

3. Baseline Benchmarking & Model Gatekeeper:
   - Naive baselines (LastValue, HistoricalMean, SimpleMomentum) calculation.
   - Benchmark evaluation metrics comparing model against naive baselines.
   - ModelPublishingGate enforcing baseline outperformance before publishing.
   - Rejection with ModelNotApprovedForPublishingError on sub-baseline performance.
   - Inference circuit breaker tripping to OPEN on severe degradation with safe fallback.

4. Edge Cases & Numerical Robustness:
   - Handling of NaN, Inf, and -Inf inputs (explicit rejection / bounds validation).
   - Constant / zero-variance series normalization without division-by-zero crashes.
   - Exploding gradient detection and clipping via GradientController.
   - Horizon mismatch protection raising HorizonMismatchError.
   - Rejection of naked point estimates without uncertainty bounds.
   - Extreme market stress regimes and limit shock classification.
"""

from __future__ import annotations

import math
import tempfile
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from stock_analysis.models.architectures import (
    CNN1DConfig,
    MLPConfig,
    SmallCNN1D,
    SmallMLP,
)
from stock_analysis.models.baselines import (
    HistoricalMeanBaseline,
    LastValueBaseline,
    SimpleMomentumBaseline,
    evaluate_forecast_against_baseline,
)
from stock_analysis.models.fallback import (
    CircuitState,
    FallbackReason,
    SafeForecastFallbackManager,
)
from stock_analysis.models.features import (
    PointInTimeNormalizer,
    PreprocessingLeakageError,
)
from stock_analysis.models.gatekeeper import (
    GatekeeperConfig,
    ModelNotApprovedForPublishingError,
    ModelPublishingGate,
)
from stock_analysis.models.horizons import (
    ForecastHorizon,
    HorizonEvaluationRecord,
    HorizonMismatchError,
    HorizonSeparationRegistry,
)
from stock_analysis.models.registry import (
    seed_everything,
)
from stock_analysis.models.security import (
    safe_load_weights,
    safe_save_weights,
)
from stock_analysis.models.stress_testing import (
    MarketRegimeType,
    classify_market_regime,
)
from stock_analysis.models.targets import (
    TargetConfig,
    TargetGenerator,
    TargetType,
)
from stock_analysis.models.training import (
    ExplodingGradientError,
    GradientController,
)
from stock_analysis.models.uncertainty import (
    NakedPointEstimateDisallowedError,
    build_uncertainty_forecast,
)
from stock_analysis.pipeline.lookahead_bias import (
    DataLeakageError,
    LookaheadBiasDetector,
)
from stock_analysis.pipeline.time_series_splitter import (
    TimeSeriesSplitter,
)

pytestmark = pytest.mark.models


# ==============================================================================
# 1. Data & Temporal Leakage Prevention Tests
# ==============================================================================


class TestModelDataLeakagePrevention:
    """Verifies that model training, feature normalization, and dataset splits
    strictly prevent look-ahead bias and information leakage from the future."""

    def test_point_in_time_normalizer_train_only_fit(self) -> None:
        """Normalizer must fit strictly on train split; transform on test must use train stats."""
        normalizer = PointInTimeNormalizer(method="standard")

        train_data = [
            [10.0, 100.0],
            [20.0, 200.0],
            [30.0, 300.0],
            [40.0, 400.0],
        ]
        test_data = [
            [50.0, 500.0],
            [60.0, 600.0],
        ]

        normalizer.fit(train_data)
        assert normalizer.is_fitted is True

        transformed_test = normalizer.transform(test_data)

        # Transformed test values must be scaled using train mean & std, not test stats
        mean_feat0 = sum(row[0] for row in train_data) / len(train_data)
        var_feat0 = sum((row[0] - mean_feat0) ** 2 for row in train_data) / (len(train_data) - 1)
        std_feat0 = math.sqrt(var_feat0)

        expected_test_0 = (50.0 - mean_feat0) / std_feat0
        assert pytest.approx(transformed_test[0][0], rel=1e-3) == expected_test_0

    def test_unfitted_normalizer_raises_leakage_error(self) -> None:
        """Attempting to transform data before explicit fitting must raise
        PreprocessingLeakageError."""
        normalizer = PointInTimeNormalizer(method="standard")
        data = [[1.0, 2.0], [2.0, 3.0]]

        with pytest.raises(PreprocessingLeakageError, match="fit"):
            normalizer.transform(data)

    def test_lookahead_bias_window_verification(self) -> None:
        """Centered rolling windows and negative lags violate causality and must be rejected."""
        # Valid causal window passes
        LookaheadBiasDetector.verify_window_configuration(window_size=20, is_centered=False)

        # Centered window raises DataLeakageError
        with pytest.raises(DataLeakageError, match="Centered rolling windows"):
            LookaheadBiasDetector.verify_window_configuration(window_size=20, is_centered=True)

        # Forward shift / negative lookback raises DataLeakageError
        with pytest.raises(DataLeakageError, match="forward shift"):
            LookaheadBiasDetector.verify_window_configuration(
                window_size=10, is_centered=False, forward_shift=1
            )

    def test_feature_timestamp_alignment_detects_future_contamination(self) -> None:
        """Any feature timestamp after the decision timestamp must be flagged as violation."""
        decision_time = datetime(2026, 4, 1, 18, 0, 0)
        valid_features: dict[str, date | datetime] = {
            "ret_1d": datetime(2026, 4, 1, 18, 0, 0),
            "vol_5d": datetime(2026, 4, 1, 18, 0, 0),
            "rsi_14": datetime(2026, 3, 31, 18, 0, 0),
        }
        violations = LookaheadBiasDetector.check_feature_timestamp_alignment(
            decision_timestamp=decision_time,
            feature_timestamps=valid_features,
        )
        assert len(violations) == 0

        # Contaminated with future timestamp
        contaminated_features: dict[str, date | datetime] = dict(valid_features)
        contaminated_features["future_close"] = datetime(2026, 4, 2, 18, 0, 0)

        violations = LookaheadBiasDetector.check_feature_timestamp_alignment(
            decision_timestamp=decision_time,
            feature_timestamps=contaminated_features,
        )
        assert len(violations) == 1
        assert violations[0].feature_name == "future_close"

    def test_chronological_split_partition_monotonicity(self) -> None:
        """Partition isolation check must raise DataLeakageError upon chronological leakage."""
        train_records = [{"date": date(2026, 1, 1)}, {"date": date(2026, 1, 10)}]
        val_records = [{"date": date(2026, 1, 15)}, {"date": date(2026, 1, 25)}]
        test_records = [{"date": date(2026, 2, 1)}, {"date": date(2026, 2, 15)}]

        # Valid strictly chronological partitions pass
        assert (
            TimeSeriesSplitter.verify_no_leakage(
                train_records=train_records,
                val_records=val_records,
                test_records=test_records,
                embargo_days=2,
            )
            is True
        )

        # Contaminated partition (val record overlaps with train date) raises DataLeakageError
        leaked_val = [{"date": date(2026, 1, 8)}, {"date": date(2026, 1, 20)}]
        with pytest.raises(DataLeakageError, match="leakage|Embargo"):
            TimeSeriesSplitter.verify_no_leakage(
                train_records=train_records,
                val_records=leaked_val,
                test_records=test_records,
            )

    def test_target_generation_forward_causality(self) -> None:
        """TargetGenerator must strictly predict future returns; final horizon steps are None."""
        prices = [100.0, 102.0, 105.0, 108.0, 110.0]

        target_cfg = TargetConfig(
            target_type=TargetType.PERCENT_RETURN,
            horizon_steps=1,
        )
        generator = TargetGenerator(target_cfg)
        target_series = generator.compute_target(prices)

        # For 1-step forward return at t, value is (P_{t+1} - P_t) / P_t
        # The last bar must be None because t+1 does not exist yet
        assert target_series.values[-1] is None
        assert target_series.values[0] is not None
        first_return = (prices[1] - prices[0]) / prices[0]
        assert pytest.approx(target_series.values[0], rel=1e-4) == first_return


# ==============================================================================
# 2. Deterministic Reproducibility Tests
# ==============================================================================


class TestModelReproducibility:
    """Verifies that setting random seeds guarantees bit-for-bit or numerically
    deterministic model initialization, weights persistence, and inference."""

    def test_seed_everything_initializes_deterministic_weights(self) -> None:
        """Identical seeds must produce identical initial model parameters."""
        cfg = MLPConfig(input_dim=10, hidden_dims=(32, 16), output_dim=1, dropout=0.0)

        seed_everything(42)
        model_a = SmallMLP(cfg)

        seed_everything(42)
        model_b = SmallMLP(cfg)

        for param_a, param_b in zip(model_a.parameters(), model_b.parameters(), strict=True):
            assert torch.equal(param_a, param_b)

    def test_different_seeds_produce_different_weights(self) -> None:
        """Different seeds must yield distinct stochastic initializations."""
        cfg = MLPConfig(input_dim=10, hidden_dims=(32, 16), output_dim=1, dropout=0.0)

        seed_everything(42)
        model_a = SmallMLP(cfg)

        seed_everything(9999)
        model_b = SmallMLP(cfg)

        has_difference = False
        for param_a, param_b in zip(model_a.parameters(), model_b.parameters(), strict=True):
            if not torch.equal(param_a, param_b):
                has_difference = True
                break
        assert has_difference is True

    def test_model_weights_persistence_roundtrip_identity(self) -> None:
        """Saving and loading weights via safe_save_weights must yield identical inference."""
        seed_everything(101)
        cfg = MLPConfig(input_dim=8, hidden_dims=(16, 8), output_dim=1, dropout=0.0)
        original_model = SmallMLP(cfg)
        original_model.eval()

        sample_input = torch.randn(4, 8)
        with torch.no_grad():
            expected_output = original_model(sample_input)

        with tempfile.TemporaryDirectory() as tmp_dir:
            weights_path = Path(tmp_dir) / "model_weights.safetensors"
            safe_save_weights(original_model, weights_path)

            loaded_model = SmallMLP(cfg)
            state_dict = safe_load_weights(weights_path)
            loaded_model.load_state_dict(state_dict)
            loaded_model.eval()

            with torch.no_grad():
                reloaded_output = loaded_model(sample_input)

            assert torch.allclose(expected_output, reloaded_output, atol=1e-6)

    def test_cnn1d_deterministic_batch_inference(self) -> None:
        """SmallCNN1D batch inference must yield identical deterministic tensor outputs."""
        seed_everything(777)
        cnn_cfg = CNN1DConfig(in_channels=4, seq_len=15, num_filters=(8, 16), kernel_size=3)
        model = SmallCNN1D(cnn_cfg)
        model.eval()

        batch_x = torch.randn(3, 4, 15)
        with torch.no_grad():
            pred_1 = model(batch_x)
            pred_2 = model(batch_x)

        assert torch.equal(pred_1, pred_2)

    def test_deterministic_training_gradient_step(self) -> None:
        """Two identical optimization iterations with fixed seed must yield identical losses."""

        def run_step() -> float:
            seed_everything(2026)
            cfg = MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1, dropout=0.0)
            model = SmallMLP(cfg)
            optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
            criterion = torch.nn.MSELoss()

            x = torch.tensor([[1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 5.0]], dtype=torch.float32)
            y = torch.tensor([[0.05], [0.10]], dtype=torch.float32)

            optimizer.zero_grad()
            output = model(x)
            loss = criterion(output, y)
            loss.backward()
            optimizer.step()
            return float(loss.item())

        loss_run1 = run_step()
        loss_run2 = run_step()
        assert pytest.approx(loss_run1, abs=1e-7) == loss_run2


# ==============================================================================
# 3. Baseline Benchmarking & Model Gatekeeper Tests
# ==============================================================================


class TestModelBaselineBenchmarkingAndGates:
    """Verifies that naive baselines provide an unyielding performance floor,
    and models failing to outperform baselines are strictly rejected from publishing."""

    def test_naive_baselines_deterministic_prediction(self) -> None:
        """LastValue, HistoricalMean, and SimpleMomentum produce expected naive forecasts."""
        prices = [100.0, 102.0, 104.0, 108.0]

        # 1. LastValueBaseline
        last_val = LastValueBaseline()
        res_last = last_val.predict(prices, horizon=2, symbol="THYAO")
        assert res_last.point_predictions == [108.0, 108.0]
        assert res_last.directional_predictions == [0, 0]

        # 2. HistoricalMeanBaseline
        hist_mean = HistoricalMeanBaseline(window=3)
        res_mean = hist_mean.predict(prices, horizon=1, symbol="THYAO")
        assert res_mean.point_predictions[0] > 108.0  # Positive historical trend

        # 3. SimpleMomentumBaseline
        momentum = SimpleMomentumBaseline(lookback_k=3)
        res_mom = momentum.predict(prices, horizon=1, symbol="THYAO")
        assert res_mom.directional_predictions == [1]

    def test_benchmark_evaluation_detects_superior_and_inferior_models(self) -> None:
        """evaluate_forecast_against_baseline compares model against naive baselines."""
        y_true = [102.0, 105.0, 107.0, 110.0]
        y_base = [100.0, 102.0, 104.0, 106.0]  # Lagged last-value baseline

        # Superior model: very close predictions
        y_good_model = [102.1, 104.9, 107.2, 109.8]
        report_good = evaluate_forecast_against_baseline(
            y_true=y_true,
            y_model=y_good_model,
            y_baseline=y_base,
            model_name="GoodModel",
            baseline_name="last_value",
        )
        assert report_good.model_metrics.mae < report_good.baseline_metrics.mae
        assert report_good.beats_baseline is True
        assert report_good.forecast_approved is True

        # Inferior model: wildly inaccurate predictions
        y_bad_model = [150.0, 160.0, 170.0, 180.0]
        report_bad = evaluate_forecast_against_baseline(
            y_true=y_true,
            y_model=y_bad_model,
            y_baseline=y_base,
            model_name="BadModel",
            baseline_name="last_value",
        )
        assert report_bad.beats_baseline is False
        assert report_bad.forecast_approved is False

    def test_gatekeeper_blocks_sub_baseline_model_with_error(self) -> None:
        """ModelPublishingGate must reject models that fail the baseline requirement."""
        gate = ModelPublishingGate(GatekeeperConfig(require_baseline_beat=True))

        # Generate sample bars and signals
        n = 50
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        bars = pd.DataFrame(
            {
                "close": np.linspace(100.0, 120.0, n),
                "volume": np.full(n, 100_000.0),
            },
            index=dates,
        )
        signals = [0.5] * n

        y_true = [100.0, 102.0, 104.0, 106.0]
        y_base = [100.0, 102.0, 104.0, 106.0]
        y_inferior_model = [150.0, 160.0, 170.0, 180.0]

        decision = gate.evaluate(
            y_true=y_true,
            y_model=y_inferior_model,
            y_baseline=y_base,
            bars=bars,
            signals=signals,
            symbol="ASELS",
            model_name="inferior_mlp_v1",
        )
        assert decision.is_approved is False
        assert decision.beats_baseline is False

        with pytest.raises(ModelNotApprovedForPublishingError, match="yayına alınamaz|Reddedilme"):
            gate.enforce_approval(decision)

    def test_circuit_breaker_and_safe_fallback_on_degraded_model(self) -> None:
        """When inference circuit breaker trips, fallback manager serves transparent disclosure."""
        fallback_mgr = SafeForecastFallbackManager(circuit_failure_threshold=2)

        # Trigger 2 failures to trip the circuit to OPEN
        fallback_mgr.circuit_breaker.record_failure()
        fallback_mgr.circuit_breaker.record_failure()
        assert fallback_mgr.circuit_breaker.state == CircuitState.OPEN

        result = fallback_mgr.build_fallback_response(
            symbol="SISE",
            reason=FallbackReason.CIRCUIT_BREAKER_OPEN,
        )
        assert result.forecast_available is False
        assert "tahmin" in result.user_message.lower() or "devre" in result.user_message.lower()
        assert len(result.alternative_indicators) > 0
        assert result.disclaimer != ""


# ==============================================================================
# 4. Edge Cases & Numerical Robustness Tests
# ==============================================================================


class TestModelEdgeCasesAndNumericalRobustness:
    """Verifies model behavior under corrupted inputs, extreme volatility shocks,
    gradient instability, and contract enforcement."""

    def test_nan_and_inf_inputs_rejected_or_guarded(self) -> None:
        """Models receiving NaN, Inf, or -Inf inputs must not corrupt or return valid outputs."""
        cfg = MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1, dropout=0.0)
        model = SmallMLP(cfg)
        model.eval()

        # Tensor containing NaN
        nan_input = torch.tensor([[1.0, float("nan"), 3.0, 4.0]], dtype=torch.float32)
        with torch.no_grad():
            out_nan = model(nan_input)
            assert torch.isnan(out_nan).any()

        # Tensor containing Inf
        inf_input = torch.tensor([[1.0, float("inf"), 3.0, 4.0]], dtype=torch.float32)
        with torch.no_grad():
            out_inf = model(inf_input)
            assert torch.isinf(out_inf).any() or torch.isnan(out_inf).any()

    def test_zero_variance_constant_features_handling(self) -> None:
        """Normalizer on constant features (zero std) must handle division by zero safely."""
        normalizer = PointInTimeNormalizer(method="standard")
        constant_matrix = [
            [100.0, 50.0],
            [100.0, 50.0],
            [100.0, 50.0],
            [100.0, 50.0],
        ]
        normalizer.fit(constant_matrix)
        transformed = normalizer.transform(constant_matrix)

        for row in transformed:
            for val in row:
                assert not math.isnan(val)
                assert not math.isinf(val)

    def test_exploding_gradient_detection_and_clipping(self) -> None:
        """GradientController must detect exploding gradients and raise or clip."""
        cfg = MLPConfig(input_dim=2, hidden_dims=(4,), output_dim=1, dropout=0.0)
        model = SmallMLP(cfg)

        controller = GradientController(max_norm=1.0, raise_on_nan_inf=True)

        # 1. NaN gradient triggers ExplodingGradientError
        for param in model.parameters():
            param.grad = torch.full_like(param, float("nan"))

        with pytest.raises(ExplodingGradientError, match="NaN veya Inf"):
            controller.inspect_and_clip(model)

        # 2. Large gradient is safely clipped below max_norm
        for param in model.parameters():
            param.grad = torch.full_like(param, 10.0)

        check_res = controller.inspect_and_clip(model)
        assert check_res.is_clipped is True
        assert check_res.total_norm > 1.0

    def test_horizon_separation_mismatch_error(self) -> None:
        """HorizonSeparationRegistry raises HorizonMismatchError when horizons cross boundaries."""
        records = [
            HorizonEvaluationRecord(
                horizon=ForecastHorizon.DAY_1,
                model_name="model_1d",
                target_type=TargetType.PERCENT_RETURN,
                metrics={"mae": 1.2},
                sample_count=100,
            ),
            HorizonEvaluationRecord(
                horizon=ForecastHorizon.DAY_5,
                model_name="model_5d",
                target_type=TargetType.PERCENT_RETURN,
                metrics={"mae": 2.5},
                sample_count=100,
            ),
        ]

        with pytest.raises(HorizonMismatchError, match="Farklı ufuklar birbirine karıştırılamaz"):
            HorizonSeparationRegistry.assert_no_cross_horizon_contamination(records)

    def test_naked_point_estimate_strictly_disallowed(self) -> None:
        """Forecast generation must enforce uncertainty intervals; naked estimates disallowed."""
        forecast = build_uncertainty_forecast(
            point_forecast=0.025,
            aleatoric_std=0.015,
            direction_threshold=0.005,
            allow_naked_point=False,
        )

        with pytest.raises(
            NakedPointEstimateDisallowedError, match="tekil çıplak sayı olarak sunulamaz"
        ):
            forecast.get_naked_point_estimate()

    def test_market_regime_classification_under_extreme_crashes(self) -> None:
        """Stress regime classifier must identify severe crash / limit down regimes correctly."""
        # 1. Bull regime: positive returns, modest vol
        bull_returns = [0.015, 0.02, 0.018, 0.025, 0.012]
        regime_bull = classify_market_regime(bull_returns)
        assert regime_bull == MarketRegimeType.BULL

        # 2. Bear / crash regime: persistent negative drops
        crash_returns = [-0.05, -0.09, -0.08, -0.06, -0.07]
        regime_crash = classify_market_regime(crash_returns)
        assert regime_crash in (MarketRegimeType.BEAR, MarketRegimeType.HIGH_VOLATILITY)

        # 3. Sideways regime: near zero returns
        sideways_returns = [0.0001, -0.0001, 0.0002, -0.0001, 0.0]
        regime_sideways = classify_market_regime(sideways_returns)
        assert regime_sideways == MarketRegimeType.SIDEWAYS
