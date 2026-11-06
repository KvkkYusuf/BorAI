"""Tests for safe forecast fallback and silent failure prevention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.config import Settings, get_settings
from stock_analysis.models.fallback import (
    CircuitState,
    FallbackReason,
    InferenceCircuitBreaker,
    SafeForecastFallbackManager,
    SafeForecastFallbackResult,
)


def test_successful_safe_inference() -> None:
    """Dogru ciktida cikarim basariyla tamamlanmali ve circuit breaker CLOSED kalmali."""
    manager = SafeForecastFallbackManager(max_staleness_hours=24.0)

    def mock_inference() -> dict[str, Any]:
        return {
            "model_version": "bist-trend-v1.0.0",
            "evaluation_date": datetime.now(UTC),
            "horizon_days": 5,
            "predicted_direction": "upward",
            "confidence_score": 0.72,
            "uncertainty_interval": [250.0, 265.0],
            "baseline_comparison": "Naive momentum outperformance: +4.2%",
        }

    res = manager.execute_safe_inference(
        symbol="THYAO",
        inference_callable=mock_inference,
        model_version="bist-trend-v1.0.0",
    )

    assert res.forecast_available is True
    assert res.status == "OK"
    assert res.symbol == "THYAO"
    assert res.predicted_direction == "upward"
    assert res.confidence_score == 0.72
    assert res.uncertainty_interval == [250.0, 265.0]
    assert manager.circuit_breaker.state == CircuitState.CLOSED


def test_red_line_violation_in_fallback_result() -> None:
    """Kirmizi cizgi: forecast_available=False iken yon/skor sunulamaz."""
    with pytest.raises(ValueError, match="Kural İhlali"):
        SafeForecastFallbackResult(
            symbol="GARAN",
            forecast_available=False,
            status="FALLBACK_TRIGGERED",
            reason_code=FallbackReason.INFERENCE_EXCEPTION,
            user_message="Hata olustu",
            predicted_direction="downward",
        )

    with pytest.raises(ValueError, match="Kural İhlali"):
        SafeForecastFallbackResult(
            symbol="GARAN",
            forecast_available=False,
            status="FALLBACK_TRIGGERED",
            reason_code=FallbackReason.INFERENCE_EXCEPTION,
            user_message="Hata olustu",
            uncertainty_interval=[100.0, 110.0],
        )


def test_silent_failure_prevented_on_exception() -> None:
    """Model calisirken exception firlatilirsa sessiz uydurma tahmin engellenmeli."""
    manager = SafeForecastFallbackManager()

    def crashing_inference() -> dict[str, Any]:
        raise RuntimeError("PyTorch CUDA out of memory error!")

    res = manager.execute_safe_inference(
        symbol="ASELS",
        inference_callable=crashing_inference,
        model_version="asels-v1",
    )

    assert res.forecast_available is False
    assert res.status == "FALLBACK_TRIGGERED"
    assert res.reason_code == FallbackReason.INFERENCE_EXCEPTION
    assert "PyTorch CUDA out of memory" in (res.technical_details or "")
    assert res.predicted_direction is None
    assert res.confidence_score is None
    assert res.uncertainty_interval is None
    assert "RSI_14" in res.alternative_indicators


def test_stale_prediction_rejection() -> None:
    """24 saati gecmis tahminler bayat kabul edilmeli ve reddedilmeli."""
    manager = SafeForecastFallbackManager(max_staleness_hours=24.0)
    now = datetime.now(UTC)
    old_time = now - timedelta(hours=25)

    def stale_inference() -> dict[str, Any]:
        return {
            "model_version": "bist-trend-v1.0.0",
            "evaluation_date": old_time,
            "predicted_direction": "upward",
            "confidence_score": 0.65,
            "uncertainty_interval": [100.0, 105.0],
        }

    res = manager.execute_safe_inference(
        symbol="EREGL",
        inference_callable=stale_inference,
        current_time=now,
    )

    assert res.forecast_available is False
    assert res.reason_code == FallbackReason.STALE_PREDICTION
    assert "bayat" in (res.technical_details or "").lower()


def test_circuit_breaker_transitions() -> None:
    """Devre kesici ardisik hatalarda OPEN durumuna gecmeli ve gereksiz cagiriyi onlemeli."""
    cb = InferenceCircuitBreaker(failure_threshold=3, recovery_timeout_seconds=5.0)
    now = datetime.now(UTC)

    assert cb.is_call_allowed(now) is True

    cb.record_failure(now)
    cb.record_failure(now)
    assert cb.state.value == CircuitState.CLOSED.value
    assert cb.is_call_allowed(now) is True

    # 3. hata devreyi acar
    cb.record_failure(now)
    assert str(cb.state) == str(CircuitState.OPEN)
    assert cb.is_call_allowed(now) is False

    # 2 saniye sonra hala OPEN
    assert cb.is_call_allowed(now + timedelta(seconds=2)) is False

    # 6 saniye sonra recovery timeout asilir, HALF_OPEN denemesine izin verilir
    later = now + timedelta(seconds=6)
    assert cb.is_call_allowed(later) is True
    assert str(cb.state) == str(CircuitState.HALF_OPEN)

    # Basari devreyi CLOSED yapar
    cb.record_success(later)
    assert str(cb.state) == str(CircuitState.CLOSED)
    assert cb.failure_count == 0


def test_governance_inactive_model_trigger() -> None:
    """Model onayli veya aktif degilse tahmin calistirilmamali."""
    mock_registry = MagicMock()
    mock_active = MagicMock()
    mock_active.model_name = "approved-model-v1"
    mock_registry.get_active_model.return_value = mock_active

    manager = SafeForecastFallbackManager(governance_registry=mock_registry)

    inference_called = False

    def dummy_inference() -> dict[str, Any]:
        nonlocal inference_called
        inference_called = True
        return {}

    res = manager.execute_safe_inference(
        symbol="BIMAS",
        inference_callable=dummy_inference,
        model_version="unapproved-model-v2",
    )

    assert res.forecast_available is False
    assert res.reason_code == FallbackReason.MODEL_INACTIVE
    assert inference_called is False


def test_monitoring_critical_alert_trigger() -> None:
    """Izleme servisinde kritik veri kaymasi varsa tahmin guvenlik amaciyla kapatilmali."""
    mock_monitoring = MagicMock()
    mock_alert = MagicMock()
    mock_alert.severity = "CRITICAL"
    mock_alert.description = "Feature PSI drift exceeded 0.25 on volume indicator"
    mock_monitoring.get_active_alerts.return_value = [mock_alert]

    manager = SafeForecastFallbackManager(monitoring_service=mock_monitoring)

    res = manager.execute_safe_inference(
        symbol="KCHOL",
        inference_callable=lambda: {},
        model_version="kchol-v1",
    )

    assert res.forecast_available is False
    assert res.reason_code == FallbackReason.DATA_DRIFT_ALERT
    assert "PSI drift" in (res.technical_details or "")


def test_corrupted_model_output_rejection() -> None:
    """Model ciktisi eksik veya ters aralik iceriyorsa guvenli fallback tetiklenmeli."""
    manager = SafeForecastFallbackManager()

    # Ters belirsizlik araligi: lower > upper
    def bad_interval_inference() -> dict[str, Any]:
        return {
            "predicted_direction": "upward",
            "confidence_score": 0.60,
            "uncertainty_interval": [350.0, 310.0],  # Gecersiz
        }

    res = manager.execute_safe_inference(
        symbol="SAHOL",
        inference_callable=bad_interval_inference,
    )

    assert res.forecast_available is False
    assert res.reason_code == FallbackReason.INFERENCE_EXCEPTION
    assert "Geçersiz belirsizlik aralığı" in (res.technical_details or "")


def test_audit_log_recording() -> None:
    """Tum fallback ve basari olaylari denetim gunlugune yazilmali."""
    manager = SafeForecastFallbackManager()

    manager.execute_safe_inference(
        symbol="TUPRS",
        inference_callable=lambda: {
            "predicted_direction": "neutral",
            "confidence_score": 0.50,
            "uncertainty_interval": [150.0, 160.0],
        },
    )

    def failing_call() -> dict[str, Any]:
        raise ZeroDivisionError("division by zero")

    manager.execute_safe_inference(
        symbol="TUPRS",
        inference_callable=failing_call,
    )

    logs = manager.get_audit_log()
    assert len(logs) == 2
    assert logs[0]["event_type"] == "INFERENCE_SUCCESS"
    assert logs[1]["event_type"] == "FALLBACK_TRIGGERED"
    assert logs[1]["reason"] == FallbackReason.INFERENCE_EXCEPTION.value

    manager.clear_audit_log()
    assert len(manager.get_audit_log()) == 0


def test_api_forecast_failure_simulation() -> None:
    """API uzerinden model arizasi simule edildiginde HTTP 200 ile seffaf fallback donmeli."""
    app = create_app()

    def override_settings() -> Settings:
        return Settings(FORECAST_ENABLED=True)

    app.dependency_overrides[get_settings] = override_settings

    with TestClient(app) as client:
        # 1. Normal basarili istek
        resp = client.get("/api/v1/inference/forecast/SISE")
        assert resp.status_code == 200
        data = resp.json()
        assert data["forecast_available"] is True
        assert data["status"] == "OK"
        assert data["predicted_direction"] == "upward"

        # 2. Arizalanan model istegi
        fail_resp = client.get("/api/v1/inference/forecast/SISE?simulate_failure=true")
        assert fail_resp.status_code == 200
        fail_data = fail_resp.json()
        assert fail_data["forecast_available"] is False
        assert fail_data["status"] == "FALLBACK_TRIGGERED"
        assert fail_data["fallback_reason"] == "INFERENCE_EXCEPTION"
        assert fail_data["predicted_direction"] is None
        assert fail_data["confidence_score"] is None
        assert fail_data["uncertainty_interval"] is None
        assert "RSI_14" in fail_data["alternative_indicators"]

        # 3. Bayat veri simule edilen istek
        stale_resp = client.get("/api/v1/inference/forecast/SISE?simulate_stale=true")
        assert stale_resp.status_code == 200
        stale_data = stale_resp.json()
        assert stale_data["forecast_available"] is False
        assert stale_data["fallback_reason"] == "STALE_PREDICTION"
