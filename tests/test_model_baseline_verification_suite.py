"""Unit and integration test suite for model baseline pre-deployment gatekeeper (FAZ-16).

Tests:
1. Baseline outperformance evaluation (MAE, directional accuracy, Sharpe, drawdown).
2. Rejection of models underperforming naive baselines or failing realistic friction.
3. Strict enforcement: FORECAST_ENABLED=True requires approved baseline-beating model.
4. Safe deployment with FORECAST_ENABLED=False (fail-safe fallback).
5. SafeForecastFallbackManager integration: zero silent guessing or fabricated predictions.
6. Statutory SPK disclaimer validation and integrity.
7. Markdown report publishing and formatting.
8. Prometheus telemetry exposition and metrics tracking.
9. FastAPI REST endpoints (/pre-deployment/model-baseline/verify & /report).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.models.baseline_gatekeeper import (
    DEFAULT_BASELINE_GATE_DISCLAIMER,
    MODEL_BASELINE_EVALUATIONS,
    MODEL_BASELINE_GATE_STATUS,
    MODEL_FORECAST_ENABLED_GAUGE,
    EvaluatedModelSummary,
    ModelBaselineGateStatus,
    ModelBaselineVerificationGatekeeper,
)
from stock_analysis.models.fallback import (
    FallbackReason,
    SafeForecastFallbackManager,
    SafeForecastFallbackResult,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(create_app())


@pytest.fixture
def clean_gatekeeper() -> ModelBaselineVerificationGatekeeper:
    """Create an isolated ModelBaselineVerificationGatekeeper instance."""
    gk = ModelBaselineVerificationGatekeeper()
    gk.reset_evaluations()
    return gk


def test_baseline_outperformance_evaluation(
    clean_gatekeeper: ModelBaselineVerificationGatekeeper,
) -> None:
    """Test that models beating naive baseline and friction are approved, others rejected."""
    # Register approved model
    approved_model = EvaluatedModelSummary(
        model_name="bist_lstm_v1",
        symbol="THYAO",
        horizon_days=1,
        model_mae=0.0125,
        best_baseline_mae=0.0165,
        baseline_type="LastValueBaseline",
        mae_improvement_pct=24.24,
        directional_accuracy=0.565,
        naive_directional_accuracy=0.495,
        net_return_pct=14.80,
        net_sharpe_ratio=1.42,
        max_drawdown_pct=11.5,
        beats_baseline=True,
        beats_friction=True,
        is_approved=True,
        recommended_forecast_enabled=True,
        rejection_reasons=[],
    )
    # Register underperforming model
    failing_model = EvaluatedModelSummary(
        model_name="bist_sub_baseline_mlp",
        symbol="ASELS",
        horizon_days=1,
        model_mae=0.0220,
        best_baseline_mae=0.0180,
        baseline_type="LastValueBaseline",
        mae_improvement_pct=-22.22,
        directional_accuracy=0.470,
        naive_directional_accuracy=0.505,
        net_return_pct=-3.50,
        net_sharpe_ratio=-0.45,
        max_drawdown_pct=25.0,
        beats_baseline=False,
        beats_friction=False,
        is_approved=False,
        recommended_forecast_enabled=False,
        rejection_reasons=["Model MAE is worse than LastValueBaseline", "Net return negative"],
    )

    clean_gatekeeper.register_model_evaluation(approved_model)
    clean_gatekeeper.register_model_evaluation(failing_model)

    report = clean_gatekeeper.evaluate_gate(target_forecast_enabled=True)

    assert report.total_models_evaluated == 2
    assert report.models_approved_count == 1
    assert report.models_rejected_count == 1
    assert report.gate_status == ModelBaselineGateStatus.PASS
    assert report.sign_off_ready is True
    assert report.forecast_enabled is True


def test_forecast_enabled_blocked_when_no_approved_model(
    clean_gatekeeper: ModelBaselineVerificationGatekeeper,
) -> None:
    """Enforce rule: If no model beats baseline, FORECAST_ENABLED=True must FAIL deployment."""
    failing_model = EvaluatedModelSummary(
        model_name="bist_sub_baseline_mlp",
        symbol="ASELS",
        horizon_days=1,
        model_mae=0.0220,
        best_baseline_mae=0.0180,
        baseline_type="LastValueBaseline",
        mae_improvement_pct=-22.22,
        directional_accuracy=0.470,
        naive_directional_accuracy=0.505,
        net_return_pct=-3.50,
        net_sharpe_ratio=-0.45,
        max_drawdown_pct=25.0,
        beats_baseline=False,
        beats_friction=False,
        is_approved=False,
        recommended_forecast_enabled=False,
        rejection_reasons=["Sub-baseline performance"],
    )
    clean_gatekeeper.register_model_evaluation(failing_model)

    report = clean_gatekeeper.evaluate_gate(target_forecast_enabled=True)

    assert report.gate_status == ModelBaselineGateStatus.REJECTED_BASELINE
    assert report.sign_off_ready is False
    assert any("FORECAST_ENABLED=True" in reason for reason in report.blocking_reasons)


def test_safe_deployment_with_forecast_disabled(
    clean_gatekeeper: ModelBaselineVerificationGatekeeper,
) -> None:
    """Safe deployment is permitted if FORECAST_ENABLED=False and fallback is verified."""
    # Even if an underperforming model is present, system safely deploys with forecast disabled
    failing_model = EvaluatedModelSummary(
        model_name="bist_sub_baseline_mlp",
        symbol="ASELS",
        horizon_days=1,
        model_mae=0.0220,
        best_baseline_mae=0.0180,
        baseline_type="LastValueBaseline",
        mae_improvement_pct=-22.22,
        directional_accuracy=0.470,
        naive_directional_accuracy=0.505,
        net_return_pct=-3.50,
        net_sharpe_ratio=-0.45,
        max_drawdown_pct=25.0,
        beats_baseline=False,
        beats_friction=False,
        is_approved=False,
        recommended_forecast_enabled=False,
        rejection_reasons=["Sub-baseline performance"],
    )
    clean_gatekeeper.register_model_evaluation(failing_model)

    report = clean_gatekeeper.evaluate_gate(target_forecast_enabled=False)

    assert report.gate_status == ModelBaselineGateStatus.PASS_WITH_FORECAST_DISABLED
    assert report.sign_off_ready is True
    assert report.forecast_enabled is False
    assert report.fallback_enforced_on_failure is True


def test_fallback_guarantee_prevents_silent_guessing() -> None:
    """Verify SafeForecastFallbackManager strictly sets predictions to None on failure."""
    mgr = SafeForecastFallbackManager()
    res: SafeForecastFallbackResult = mgr.build_fallback_response(
        symbol="EREGL",
        reason=FallbackReason.GATEKEEPER_REJECTED,
        technical_details="Model baseline altında kaldı",
    )

    assert res.forecast_available is False
    assert res.predicted_direction is None
    assert res.confidence_score is None
    assert res.uncertainty_interval is None
    assert len(res.alternative_indicators) > 0
    assert "RSI_14" in res.alternative_indicators
    assert "yatirim tavsiyesi degildir" in res.disclaimer.lower()


def test_statutory_spk_disclaimer_compliance() -> None:
    """Verify statutory disclaimer contains SPK required terms and is legally compliant."""
    assert "SPK mevzuatı" in DEFAULT_BASELINE_GATE_DISCLAIMER
    assert "yatırım danışmanlığı kapsamında olmayıp" in DEFAULT_BASELINE_GATE_DISCLAIMER
    assert "kalite güvencesi" in DEFAULT_BASELINE_GATE_DISCLAIMER


def test_markdown_report_generation(clean_gatekeeper: ModelBaselineVerificationGatekeeper) -> None:
    """Verify Markdown report contains structured tables, badges, and SPK text."""
    report = clean_gatekeeper.evaluate_gate(auto_populate_if_empty=True)
    md = report.generate_markdown_audit_report()

    assert "# Dagitim Oncesi Model Baseline ve Tahmin Ozelligi Dogrulama Raporu" in md
    assert "## 1. Model Baseline Degerlendirme Ozeti" in md
    assert "| `bist_lstm_v1` | THYAO |" in md
    assert "## 2. Tahmin Ozelligi ve Guvenli Fallback Denetimi" in md
    assert "## 4. Yasal Uyari ve Mevzuat Bildirimi" in md
    assert "SPK mevzuatı uyarınca" in md


def test_report_publishing_to_disk(clean_gatekeeper: ModelBaselineVerificationGatekeeper) -> None:
    """Verify publishing the markdown report writes file successfully to target directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        report_path = Path(tmpdir) / "test_model_report.md"
        published_path = clean_gatekeeper.publish_report(output_path=report_path)

        assert published_path.exists()
        content = published_path.read_text(encoding="utf-8")
        assert "Dagitim Oncesi Model Baseline" in content


def test_prometheus_telemetry_metrics(
    clean_gatekeeper: ModelBaselineVerificationGatekeeper,
) -> None:
    """Verify Prometheus metrics update during evaluation."""
    initial_evals = MODEL_BASELINE_EVALUATIONS.get()

    report = clean_gatekeeper.evaluate_gate(auto_populate_if_empty=True)

    assert MODEL_BASELINE_GATE_STATUS.get() == (1.0 if report.sign_off_ready else 0.0)
    assert MODEL_FORECAST_ENABLED_GAUGE.get() == (1.0 if report.forecast_enabled else 0.0)
    assert MODEL_BASELINE_EVALUATIONS.get() >= initial_evals + report.total_models_evaluated


def test_system_router_endpoints(client: TestClient) -> None:
    """Test REST API endpoints for pre-deployment model baseline verification."""
    # 1. Test JSON verify endpoint
    resp = client.get("/pre-deployment/model-baseline/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert "gate_status" in data
    assert "forecast_enabled" in data
    assert "sign_off_ready" in data
    assert "evaluated_models" in data
    assert data["total_models_evaluated"] >= 0

    # 2. Test Markdown report endpoint
    report_resp = client.get("/pre-deployment/model-baseline/report")
    assert report_resp.status_code == 200
    assert "text/markdown" in report_resp.headers["content-type"]
    assert "Dagitim Oncesi Model Baseline" in report_resp.text
