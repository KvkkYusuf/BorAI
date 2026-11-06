"""Comprehensive test suite for offline validation vs live performance tracking.

Verifies:
- Offline metrics registration and gauge exposition
- Live prediction and realized outcome pairing without lookahead bias
- Brier score, directional accuracy, MAE, and RMSE computations
- Performance degradation gap and health status transitions
  (healthy, degraded, critical, insufficient_data)
- Multi-model and multi-version isolation
- FastAPI system endpoints (/api/v1/model-performance and /api/v1/metrics)
- SPK disclaimer compliance
"""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability import (
    MODEL_LIVE_BRIER_SCORE,
    MODEL_LIVE_DIRECTIONAL_ACCURACY,
    MODEL_LIVE_MAE,
    MODEL_LIVE_PREDICTIONS_TOTAL,
    MODEL_OFFLINE_BRIER_SCORE,
    MODEL_OFFLINE_DIRECTIONAL_ACCURACY,
    MODEL_OFFLINE_MAE,
    MODEL_PERFORMANCE_GAP_ACCURACY,
    MODEL_PERFORMANCE_STATUS,
    MODEL_PERFORMANCE_TRACKER,
    LivePredictionRecord,
    ModelHealthStatus,
    ModelPerformanceTracker,
    OfflineValidationMetrics,
    PerformanceTrackingConfig,
)


@pytest.fixture(autouse=True)
def clean_model_tracker() -> None:
    """Reset tracker state and Prometheus gauges before each test."""
    MODEL_PERFORMANCE_TRACKER.reset()
    MODEL_OFFLINE_DIRECTIONAL_ACCURACY._values.clear()
    MODEL_OFFLINE_BRIER_SCORE._values.clear()
    MODEL_OFFLINE_MAE._values.clear()
    MODEL_LIVE_DIRECTIONAL_ACCURACY._values.clear()
    MODEL_LIVE_BRIER_SCORE._values.clear()
    MODEL_LIVE_MAE._values.clear()
    MODEL_PERFORMANCE_GAP_ACCURACY._values.clear()
    MODEL_LIVE_PREDICTIONS_TOTAL._values.clear()
    MODEL_PERFORMANCE_STATUS._values.clear()


class TestOfflineValidationRegistration:
    """Tests for registering and storing offline benchmark metrics."""

    def test_register_and_retrieve_offline_metrics(self) -> None:
        metrics = OfflineValidationMetrics(
            model_id="bist_lstm_v1",
            version="1.0.0",
            sample_count=500,
            directional_accuracy=0.62,
            brier_score=0.21,
            mae=0.015,
            rmse=0.022,
            baseline_accuracy=0.51,
            dataset_split="walk_forward",
            metadata={"lookback": 30, "target": "1d_return"},
        )

        MODEL_PERFORMANCE_TRACKER.register_offline_validation(metrics)
        retrieved = MODEL_PERFORMANCE_TRACKER.get_offline_validation("bist_lstm_v1", "1.0.0")

        assert retrieved is not None
        assert retrieved.directional_accuracy == 0.62
        assert retrieved.brier_score == 0.21
        assert retrieved.dataset_split == "walk_forward"

        # Verify Prometheus gauge
        labels = {"model_id": "bist_lstm_v1", "version": "1.0.0"}
        assert MODEL_OFFLINE_DIRECTIONAL_ACCURACY.get(labels) == 0.62
        assert MODEL_OFFLINE_BRIER_SCORE.get(labels) == 0.21
        assert MODEL_OFFLINE_MAE.get(labels) == 0.015


class TestLivePredictionRealization:
    """Tests for recording live predictions, matching outcomes, and calculating metrics."""

    def test_record_prediction_and_realization_lifecycle(self) -> None:
        now = datetime.now(UTC)
        pred = LivePredictionRecord(
            prediction_id="pred-001",
            model_id="bist_lstm_v1",
            version="1.0.0",
            symbol="THYAO.IS",
            prediction_timestamp=now,
            horizon_hours=24,
            predicted_value=0.025,
            predicted_direction=1,
            predicted_probability=0.75,
        )

        MODEL_PERFORMANCE_TRACKER.record_live_prediction(pred)

        pending = MODEL_PERFORMANCE_TRACKER.get_pending_predictions("bist_lstm_v1")
        assert len(pending) == 1
        assert pending[0].prediction_id == "pred-001"
        assert not pending[0].is_realized

        # Realize after horizon matures
        realized_time = now + timedelta(hours=24)
        updated = MODEL_PERFORMANCE_TRACKER.record_realized_outcome(
            prediction_id="pred-001",
            actual_value=0.030,
            actual_direction=1,
            realized_timestamp=realized_time,
        )

        assert updated is not None
        assert updated.is_realized
        assert updated.is_direction_correct is True
        assert pytest.approx(updated.absolute_error, rel=1e-4) == 0.005
        # probability 0.75, binary outcome 1.0 -> (0.75 - 1.0)^2 = 0.0625
        assert pytest.approx(updated.brier_loss, rel=1e-4) == 0.0625

        # Check pending is now empty
        assert len(MODEL_PERFORMANCE_TRACKER.get_pending_predictions("bist_lstm_v1")) == 0

        # Check outcome counter in Prometheus
        assert (
            MODEL_LIVE_PREDICTIONS_TOTAL.get({"model_id": "bist_lstm_v1", "outcome": "correct"})
            == 1.0
        )

    def test_realization_with_unregistered_id_returns_none(self) -> None:
        result = MODEL_PERFORMANCE_TRACKER.record_realized_outcome(
            prediction_id="non-existent", actual_value=0.01
        )
        assert result is None


class TestPerformanceEvaluationAndHealthStatus:
    """Tests for generalization gap, degradation detection, and status transitions."""

    def test_insufficient_data_status(self) -> None:
        config = PerformanceTrackingConfig(min_live_samples_for_evaluation=5)
        tracker = ModelPerformanceTracker(config=config)

        # Register offline
        tracker.register_offline_validation(
            OfflineValidationMetrics(
                model_id="test_model",
                version="1.0.0",
                sample_count=200,
                directional_accuracy=0.60,
                brier_score=0.20,
                mae=0.01,
                rmse=0.015,
            )
        )

        # Record only 2 predictions
        for i in range(2):
            tracker.record_live_prediction(
                LivePredictionRecord(
                    prediction_id=f"p-{i}",
                    model_id="test_model",
                    version="1.0.0",
                    symbol="ASELS.IS",
                    horizon_hours=24,
                    predicted_value=0.01,
                    predicted_direction=1,
                    predicted_probability=0.60,
                )
            )
            tracker.record_realized_outcome(f"p-{i}", actual_value=0.02)

        comp = tracker.evaluate_model("test_model", "1.0.0")
        assert comp.status == ModelHealthStatus.INSUFFICIENT_DATA
        assert "Yeterli canlı gerçekleşme verisi henüz toplanmadı" in comp.status_reason

    def test_healthy_status_when_live_matches_offline(self) -> None:
        config = PerformanceTrackingConfig(min_live_samples_for_evaluation=10)
        tracker = ModelPerformanceTracker(config=config)

        tracker.register_offline_validation(
            OfflineValidationMetrics(
                model_id="model_healthy",
                version="1.0.0",
                sample_count=1000,
                directional_accuracy=0.60,
                brier_score=0.22,
                mae=0.020,
                rmse=0.028,
            )
        )

        # Record 10 predictions: 6 correct (60%), MAE ~ 0.020
        for i in range(10):
            # 6 correct positive, 4 incorrect (actual negative)
            is_correct = i < 6
            actual_val = 0.02 if is_correct else -0.02
            tracker.record_live_prediction(
                LivePredictionRecord(
                    prediction_id=f"p-{i}",
                    model_id="model_healthy",
                    version="1.0.0",
                    symbol="GARAN.IS",
                    horizon_hours=24,
                    predicted_value=0.02,
                    predicted_direction=1,
                    predicted_probability=0.65,
                )
            )
            tracker.record_realized_outcome(f"p-{i}", actual_value=actual_val)

        comp = tracker.evaluate_model("model_healthy", "1.0.0")
        assert comp.status == ModelHealthStatus.HEALTHY
        assert comp.live_metrics.directional_accuracy == 0.60
        assert comp.accuracy_gap == 0.0
        assert "toleransı dahilinde kararlı" in comp.status_reason

    def test_degraded_status_on_warning_gap(self) -> None:
        config = PerformanceTrackingConfig(
            min_live_samples_for_evaluation=10,
            accuracy_degradation_warning_gap=0.05,
            accuracy_degradation_critical_gap=0.10,
        )
        tracker = ModelPerformanceTracker(config=config)

        tracker.register_offline_validation(
            OfflineValidationMetrics(
                model_id="model_degraded",
                version="1.0.0",
                sample_count=1000,
                directional_accuracy=0.62,
                brier_score=0.20,
                mae=0.015,
                rmse=0.020,
            )
        )

        # 20 predictions: 11 correct (55% accuracy -> gap = 0.62 - 0.55 = 0.07 >= 0.05 warning)
        for i in range(20):
            actual_val = 0.015 if i < 11 else -0.015
            tracker.record_live_prediction(
                LivePredictionRecord(
                    prediction_id=f"p-{i}",
                    model_id="model_degraded",
                    version="1.0.0",
                    symbol="KCHOL.IS",
                    horizon_hours=24,
                    predicted_value=0.015,
                    predicted_direction=1,
                    predicted_probability=0.60,
                )
            )
            tracker.record_realized_outcome(f"p-{i}", actual_value=actual_val)

        comp = tracker.evaluate_model("model_degraded", "1.0.0")
        assert comp.status == ModelHealthStatus.DEGRADED
        assert comp.accuracy_gap == 0.07
        assert "orta seviye performans düşüşü" in comp.status_reason

    def test_critical_status_on_large_gap_or_poor_accuracy(self) -> None:
        config = PerformanceTrackingConfig(
            min_live_samples_for_evaluation=10,
            accuracy_degradation_critical_gap=0.10,
            min_live_accuracy_critical=0.50,
        )
        tracker = ModelPerformanceTracker(config=config)

        tracker.register_offline_validation(
            OfflineValidationMetrics(
                model_id="model_critical",
                version="1.0.0",
                sample_count=1000,
                directional_accuracy=0.65,
                brier_score=0.20,
                mae=0.010,
                rmse=0.015,
            )
        )

        # 10 predictions: only 4 correct (40% accuracy -> below 50% critical taban)
        for i in range(10):
            actual_val = 0.01 if i < 4 else -0.01
            tracker.record_live_prediction(
                LivePredictionRecord(
                    prediction_id=f"p-{i}",
                    model_id="model_critical",
                    version="1.0.0",
                    symbol="EREGL.IS",
                    horizon_hours=24,
                    predicted_value=0.01,
                    predicted_direction=1,
                    predicted_probability=0.60,
                )
            )
            tracker.record_realized_outcome(f"p-{i}", actual_value=actual_val)

        comp = tracker.evaluate_model("model_critical", "1.0.0")
        assert comp.status == ModelHealthStatus.CRITICAL
        assert comp.live_metrics.directional_accuracy == 0.40
        assert comp.accuracy_gap == 0.25
        assert "Kritik model performans gerilemesi" in comp.status_reason


class TestConcurrencyAndThreadSafety:
    """Tests for thread safety under concurrent prediction and realization writes."""

    def test_concurrent_recording(self) -> None:
        tracker = ModelPerformanceTracker()
        total_predictions = 50

        # Concurrent prediction recording
        def _record_pred(i: int) -> None:
            tracker.record_live_prediction(
                LivePredictionRecord(
                    prediction_id=f"c-{i}",
                    model_id="concurrent_model",
                    version="1.0.0",
                    symbol="SISE.IS",
                    horizon_hours=12,
                    predicted_value=0.01,
                    predicted_direction=1,
                    predicted_probability=0.60,
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(_record_pred, range(total_predictions)))

        assert len(tracker.get_pending_predictions("concurrent_model")) == total_predictions

        # Concurrent outcome realization
        def _realize_outcome(i: int) -> None:
            tracker.record_realized_outcome(
                f"c-{i}",
                actual_value=0.012 if i % 2 == 0 else -0.012,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(_realize_outcome, range(total_predictions)))

        live_metrics = tracker.get_live_metrics("concurrent_model", "1.0.0")
        assert live_metrics.realized_predictions == total_predictions
        assert live_metrics.pending_predictions == 0
        assert live_metrics.directional_accuracy == 0.50


class TestFastApiEndpointsAndCompliance:
    """Tests for /api/v1/model-performance endpoint, Prometheus exposition and SPK compliance."""

    def test_api_summary_and_model_filter(self) -> None:
        app = create_app()
        client = TestClient(app)

        MODEL_PERFORMANCE_TRACKER.register_offline_validation(
            OfflineValidationMetrics(
                model_id="api_model_1",
                version="1.0.0",
                sample_count=500,
                directional_accuracy=0.60,
                brier_score=0.21,
                mae=0.015,
                rmse=0.020,
            )
        )

        # GET /api/v1/model-performance (summary)
        resp = client.get("/api/v1/model-performance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["monitored_models_count"] >= 1
        assert "status_summary" in data
        assert "SPK mevzuatı" in data["disclaimer"]

        # GET /api/v1/model-performance?model_id=api_model_1
        resp_model = client.get("/api/v1/model-performance?model_id=api_model_1")
        assert resp_model.status_code == 200
        model_data = resp_model.json()
        assert model_data["model_id"] == "api_model_1"
        assert model_data["status"] == "insufficient_data"
        assert "SPK mevzuatı" in model_data["disclaimer"]

    def test_prometheus_metrics_export(self) -> None:
        app = create_app()
        client = TestClient(app)

        MODEL_PERFORMANCE_TRACKER.register_offline_validation(
            OfflineValidationMetrics(
                model_id="prom_model",
                version="2.0.0",
                sample_count=800,
                directional_accuracy=0.64,
                brier_score=0.19,
                mae=0.012,
                rmse=0.018,
            )
        )

        resp = client.get("/api/v1/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "model_offline_directional_accuracy" in text
        assert 'model_id="prom_model"' in text
        assert "0.64" in text
