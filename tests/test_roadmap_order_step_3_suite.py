"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 3 (Basit frontend/backend iskeleti) test paketi.

Test kapsamı:
- RoadmapStepThreeGatekeeper 6/6 tam uyum doğrulaması.
- FastAPI backend uygulama iskeleti (create_app, CORS, Disclaimer, Cache, Versioning middleware).
- Responsive web frontend iskeleti (static/index.html, tokens/layout/components CSS, app.js).
- Modüler API rotaları (market, analytics, auth, user, forecasts, ui) ve API_DRAFT.md sözleşmesi.
- Pydantic Settings (config.py) ve .env.example sır ayrıştırma şablonu.
- Statik dosya sunumu (ui.py FileResponse) ve frontend API entegrasyonu (fetch, durum yönetimi).
- Zorunlu SPK yasal uyarısı ve X-Disclaimer başlığı.
- Telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP3_DISCLAIMER,
    ROADMAP_STEP3_CHECKS_TOTAL,
    ROADMAP_STEP3_PASSED_TOTAL,
    ROADMAP_STEP3_STATUS,
    RoadmapStepThreeGatekeeper,
    RoadmapStepThreeStatus,
    generate_roadmap_step3_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


def test_roadmap_step3_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 3 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepThreeStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step3_backend_application_validation(workspace_root: Path) -> None:
    """FastAPI backend uygulama iskeleti ve middleware zincirini denetler."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S3-01-FASTAPI-BACKEND-APPLICATION"
    )
    assert check.passed is True
    assert check.details["has_create_app"] is True
    assert check.details["has_fastapi"] is True
    assert check.details["has_cors"] is True
    assert check.details["has_openapi"] is True
    assert check.details["has_middleware"] is True


def test_roadmap_step3_frontend_web_skeleton_validation(workspace_root: Path) -> None:
    """Duyarlı web frontend iskeleti ve CSS tasarım sistemini denetler."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S3-02-FRONTEND-WEB-SKELETON"
    )
    assert check.passed is True
    assert check.details["has_index"] is True
    assert check.details["has_tokens"] is True
    assert check.details["has_layout"] is True
    assert check.details["has_components"] is True
    assert check.details["has_js"] is True
    assert check.details["has_semantic_html"] is True
    assert check.details["has_responsive_meta"] is True


def test_roadmap_step3_api_contracts_and_routers_validation(workspace_root: Path) -> None:
    """Modüler API rotaları ve OpenAPI sözleşmesini denetler."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S3-03-API-CONTRACTS-AND-ROUTERS"
    )
    assert check.passed is True
    assert check.details["has_routers_dir"] is True
    assert check.details["has_schemas"] is True
    assert check.details["has_draft"] is True
    assert check.details["all_routers_present"] is True


def test_roadmap_step3_configuration_and_env_validation(workspace_root: Path) -> None:
    """Pydantic Settings ve .env.example yapılandırma iskeletini denetler."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S3-04-CONFIGURATION-AND-ENV-SKELETON"
    )
    assert check.passed is True
    assert check.details["has_config"] is True
    assert check.details["has_env_example"] is True
    assert check.details["has_base_settings"] is True
    assert check.details["has_get_settings"] is True
    assert check.details["has_app_env"] is True
    assert check.details["has_db_url"] is True


def test_roadmap_step3_static_assets_and_api_integration(workspace_root: Path) -> None:
    """Statik varlık sunumu ve frontend-backend köprüsünü denetler."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S3-05-STATIC-ASSETS-AND-API-INTEGRATION"
    )
    assert check.passed is True
    assert check.details["has_ui_router"] is True
    assert check.details["has_app_js"] is True
    assert check.details["has_root_endpoint"] is True
    assert check.details["has_file_response"] is True
    assert check.details["has_api_fetch"] is True


def test_roadmap_step3_mandatory_disclaimer_validation(workspace_root: Path) -> None:
    """Frontend ve API seviyesinde SPK yasal uyarı entegrasyonunu denetler."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S3-06-MANDATORY-DISCLAIMER-AND-UI-SAFETY"
    )
    assert check.passed is True
    assert check.details["has_index"] is True
    assert check.details["has_middleware"] is True
    assert check.details["has_spk_text"] is True
    assert check.details["has_disclaimer_header"] is True


def test_roadmap_step3_report_generation(workspace_root: Path) -> None:
    """Adım 3 denetim raporu çıktısının Markdown standartlarını sağladığını doğrular."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    report = generate_roadmap_step3_report(result)

    assert "# FAZ-18 Yol Haritası Uygulama Sırası — Adım 3 Kabul ve Uyum Raporu" in report
    assert "ROADMAP-S3-01-FASTAPI-BACKEND-APPLICATION" in report
    assert "ROADMAP-S3-06-MANDATORY-DISCLAIMER-AND-UI-SAFETY" in report
    assert "PASS" in report
    assert DEFAULT_ROADMAP_STEP3_DISCLAIMER in report


def test_roadmap_step3_telemetry_and_serialization(workspace_root: Path) -> None:
    """Telemetri metriklerinin ve sözlük çıktısının doğruluğunu test eder."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert ROADMAP_STEP3_STATUS.get() == 1.0
    assert ROADMAP_STEP3_CHECKS_TOTAL.get() >= 6.0
    assert ROADMAP_STEP3_PASSED_TOTAL.get() >= 6.0

    d = result.to_dict()
    assert d["status"] == "PASS"
    assert d["total_checks"] == 6
    assert d["passed_checks"] == 6
    assert d["failed_checks"] == 0
    assert len(d["checks"]) == 6


def test_roadmap_step3_failure_on_missing_components(tmp_path: Path) -> None:
    """Eksik bileşen durumunda Adım 3 gatekeeper'ın FAIL döndüğünü doğrular."""
    gatekeeper = RoadmapStepThreeGatekeeper(workspace_root=tmp_path)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepThreeStatus.FAIL
    assert result.failed_checks == 6
    assert result.passed_checks == 0
