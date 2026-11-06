"""Unit and integration test suite for UI transparency pre-deployment gate (FAZ-16).

Tests:
1. Evaluation of actual repository templates (index.html, app.js) for full compliance.
2. Verification of all 8 data latency disclosure checks.
3. Verification of all 8 model uncertainty disclosure checks.
4. Verification of all 3 safe fallback and safety disclosure checks.
5. Rejection and blocking when statutory BIST delay notice is omitted or tampered with.
6. Rejection and blocking when model baseline / no-target-price disclaimers are missing.
7. Rejection when safe fallback container is missing.
8. Prometheus telemetry exposition and metrics tracking.
9. FastAPI REST endpoints (/pre-deployment/ui-transparency/verify and /report).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.security.ui_transparency_gatekeeper import (
    UI_LATENCY_DISCLOSURES_GAUGE,
    UI_TRANSPARENCY_GATE_STATUS,
    UI_UNCERTAINTY_DISCLOSURES_GAUGE,
    TransparencyCategory,
    UiTransparencyGatekeeper,
    UiTransparencyGateReport,
    UiTransparencyGateStatus,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(create_app())


@pytest.fixture
def gatekeeper() -> UiTransparencyGatekeeper:
    """Create isolated UiTransparencyGatekeeper instance."""
    return UiTransparencyGatekeeper()


def test_ui_transparency_default_gate_evaluation(
    gatekeeper: UiTransparencyGatekeeper, tmp_path: Path
) -> None:
    """Test that actual repository template files pass 100% of UI transparency checks."""
    report_file = tmp_path / "UI_TRANSPARENCY_VERIFICATION_REPORT.md"
    report: UiTransparencyGateReport = gatekeeper.evaluate_gate(
        auto_publish_report=True,
        report_output_path=report_file,
    )

    assert report.gate_status == UiTransparencyGateStatus.PASS
    assert report.sign_off_ready is True
    assert report.total_checks == 19
    assert report.passed_checks == 19
    assert report.failed_checks == 0
    assert report.data_latency_verified_count == 8
    assert report.model_uncertainty_verified_count == 8
    assert report.fallback_safety_verified_count == 3
    assert len(report.blocking_violations) == 0

    # Verify markdown report output
    assert report_file.exists()
    report_content = report_file.read_text(encoding="utf-8")
    assert "Dağıtım Öncesi Arayüz Şeffaflık Denetim Raporu" in report_content
    assert "ONAYLANDI (PASS)" in report_content
    assert "LAT-01" in report_content
    assert "UNC-01" in report_content
    assert "Borsa İstanbul" in report_content
    assert "en az 15 dakika gecikmelidir" in report_content
    assert "naive baseline" in report_content


def test_ui_transparency_data_latency_checks_detail(
    gatekeeper: UiTransparencyGatekeeper,
) -> None:
    """Verify specific data latency items are correctly flagged as passed."""
    report = gatekeeper.evaluate_gate(auto_publish_report=False)

    lat_checks = [c for c in report.checks if c.category == TransparencyCategory.DATA_LATENCY]
    assert len(lat_checks) == 8
    assert all(c.passed for c in lat_checks)

    # Specific check IDs
    lat_ids = {c.check_id for c in lat_checks}
    assert lat_ids == {
        "LAT-01",
        "LAT-02",
        "LAT-03",
        "LAT-04",
        "LAT-05",
        "LAT-06",
        "LAT-07",
        "LAT-08",
    }


def test_ui_transparency_model_uncertainty_checks_detail(
    gatekeeper: UiTransparencyGatekeeper,
) -> None:
    """Verify specific model uncertainty items are correctly flagged as passed."""
    report = gatekeeper.evaluate_gate(auto_publish_report=False)

    unc_checks = [c for c in report.checks if c.category == TransparencyCategory.MODEL_UNCERTAINTY]
    assert len(unc_checks) == 8
    assert all(c.passed for c in unc_checks)

    unc_ids = {c.check_id for c in unc_checks}
    assert unc_ids == {
        "UNC-01",
        "UNC-02",
        "UNC-03",
        "UNC-04",
        "UNC-05",
        "UNC-06",
        "UNC-07",
        "UNC-08",
    }


def test_ui_transparency_missing_latency_statutory_text(
    gatekeeper: UiTransparencyGatekeeper,
) -> None:
    """Tampering with statutory BIST delay notice must immediately fail the gate."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        fake_static = Path(tmp_dir)
        fake_js_dir = fake_static / "js"
        fake_js_dir.mkdir(parents=True)

        # Copy original JS
        orig_js = gatekeeper.static_dir / "js" / "app.js"
        (fake_js_dir / "app.js").write_text(orig_js.read_text(encoding="utf-8"), encoding="utf-8")

        # Create tampered index.html without BIST 15 min statement
        orig_html = (gatekeeper.static_dir / "index.html").read_text(encoding="utf-8")
        tampered_html = orig_html.replace(
            "en az 15 dakika gecikmelidir",
            "[BU İBARE KALDIRILMIŞTIR]",
        )
        (fake_static / "index.html").write_text(tampered_html, encoding="utf-8")

        report = gatekeeper.evaluate_gate(static_dir=fake_static, auto_publish_report=False)
        assert report.gate_status == UiTransparencyGateStatus.FAIL
        assert report.sign_off_ready is False
        assert report.failed_checks >= 1
        assert any("LAT-05" in v for v in report.blocking_violations)


def test_ui_transparency_missing_uncertainty_disclaimer(
    gatekeeper: UiTransparencyGatekeeper,
) -> None:
    """Tampering with statutory no-target-price or naive baseline statement must fail the gate."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        fake_static = Path(tmp_dir)
        fake_js_dir = fake_static / "js"
        fake_js_dir.mkdir(parents=True)

        orig_js = gatekeeper.static_dir / "js" / "app.js"
        (fake_js_dir / "app.js").write_text(orig_js.read_text(encoding="utf-8"), encoding="utf-8")

        orig_html = (gatekeeper.static_dir / "index.html").read_text(encoding="utf-8")
        tampered_html = orig_html.replace(
            "Kesin sonuç veya hedef fiyat vaadi teşkil etmez",
            "Gelecek ay %50 kazanç garantisi",
        ).replace(
            "Model, basit bir kıyas yöntemine (naive baseline) göre test edilmiştir",
            "[KIYAS BILGISI YOKTUR]",
        )
        (fake_static / "index.html").write_text(tampered_html, encoding="utf-8")

        report = gatekeeper.evaluate_gate(static_dir=fake_static, auto_publish_report=False)
        assert report.gate_status == UiTransparencyGateStatus.FAIL
        assert report.sign_off_ready is False
        assert report.failed_checks >= 2
        assert any("UNC-04" in v for v in report.blocking_violations)
        assert any("UNC-06" in v for v in report.blocking_violations)


def test_ui_transparency_missing_fallback_container(
    gatekeeper: UiTransparencyGatekeeper,
) -> None:
    """Missing fallback state container must fail the gate."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        fake_static = Path(tmp_dir)
        fake_js_dir = fake_static / "js"
        fake_js_dir.mkdir(parents=True)

        orig_js = gatekeeper.static_dir / "js" / "app.js"
        (fake_js_dir / "app.js").write_text(orig_js.read_text(encoding="utf-8"), encoding="utf-8")

        orig_html = (gatekeeper.static_dir / "index.html").read_text(encoding="utf-8")
        tampered_html = orig_html.replace('id="prediction-model-fallback"', 'id="removed-element"')
        (fake_static / "index.html").write_text(tampered_html, encoding="utf-8")

        report = gatekeeper.evaluate_gate(static_dir=fake_static, auto_publish_report=False)
        assert report.gate_status == UiTransparencyGateStatus.FAIL
        assert report.sign_off_ready is False
        assert any("SAF-01" in v for v in report.blocking_violations)


def test_ui_transparency_prometheus_metrics(
    gatekeeper: UiTransparencyGatekeeper,
) -> None:
    """Verify Prometheus telemetry gauges and counters are updated correctly."""
    report = gatekeeper.evaluate_gate(auto_publish_report=False)
    assert report.gate_status == UiTransparencyGateStatus.PASS

    assert UI_TRANSPARENCY_GATE_STATUS.get() == 1.0
    assert UI_LATENCY_DISCLOSURES_GAUGE.get() == 8.0
    assert UI_UNCERTAINTY_DISCLOSURES_GAUGE.get() == 8.0


def test_fastapi_ui_transparency_verify_endpoint(client: TestClient) -> None:
    """Test GET /api/v1/pre-deployment/ui-transparency/verify returns compliant report."""
    response = client.get("/api/v1/pre-deployment/ui-transparency/verify")
    assert response.status_code == 200

    data = response.json()
    assert data["gate_status"] == "PASS"
    assert data["sign_off_ready"] is True
    assert data["total_checks"] == 19
    assert data["passed_checks"] == 19
    assert data["failed_checks"] == 0
    assert data["data_latency_verified_count"] == 8
    assert data["model_uncertainty_verified_count"] == 8
    assert data["fallback_safety_verified_count"] == 3
    assert len(data["blocking_violations"]) == 0
    assert len(data["checks"]) == 19


def test_fastapi_ui_transparency_report_endpoint(client: TestClient) -> None:
    """Test GET /api/v1/pre-deployment/ui-transparency/report returns markdown audit report."""
    response = client.get("/api/v1/pre-deployment/ui-transparency/report")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]

    markdown_text = response.text
    assert "# Dağıtım Öncesi Arayüz Şeffaflık Denetim Raporu" in markdown_text
    assert "ONAYLANDI (PASS)" in markdown_text
    assert "Üretime Dağıtım İçin Uygundur" in markdown_text
    assert "Borsa İstanbul Veri Dağıtım Esasları" in markdown_text
    assert "SPK III-52.4" in markdown_text
