"""Tests for status page, service outage notifications, and incident coordination."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.observability.status_page import (
    COMPONENT_STATUS_CODE_MAP,
    OVERALL_STATUS_CODE_MAP,
    ComponentStatus,
    IncidentSeverity,
    IncidentStage,
    MaintenanceStatus,
    OutageBanner,
    StatusPageCoordinator,
    SystemComponent,
    SystemOverallStatus,
)


@pytest.fixture
def coordinator() -> StatusPageCoordinator:
    """Create an isolated StatusPageCoordinator instance for testing."""
    return StatusPageCoordinator()


@pytest.fixture
def client() -> TestClient:
    """Create FastAPI test client."""
    return TestClient(create_app())


class TestComponentManagement:
    """Tests for system component registration and status transitions."""

    def test_default_components_seeded(self, coordinator: StatusPageCoordinator) -> None:
        """Verify all default core components are registered and operational."""
        components = coordinator.get_all_components()
        comp_ids = {c.component_id for c in components}

        expected = {
            SystemComponent.CORE_API.value,
            SystemComponent.MARKET_DATA.value,
            SystemComponent.DATABASE.value,
            SystemComponent.ML_FORECAST.value,
            SystemComponent.ASYNC_WORKER.value,
            SystemComponent.NOTIFICATIONS.value,
        }
        assert expected.issubset(comp_ids)
        for comp in components:
            assert comp.status == ComponentStatus.OPERATIONAL
            assert comp.uptime_percentage == 100.0

    def test_register_custom_component(self, coordinator: StatusPageCoordinator) -> None:
        """Verify custom component registration."""
        comp = coordinator.register_component(
            component_id="search_index",
            name="Arama İndeksi",
            description="Vektör ve tam metin arama servisi.",
            is_third_party=False,
        )
        assert comp.component_id == "search_index"
        assert comp.status == ComponentStatus.OPERATIONAL
        assert coordinator.get_component("search_index") == comp

    def test_update_component_status(self, coordinator: StatusPageCoordinator) -> None:
        """Verify updating component status and uptime percentage."""
        comp = coordinator.update_component_status(
            component_id=SystemComponent.DATABASE.value,
            status=ComponentStatus.DEGRADED_PERFORMANCE,
            status_message="Yüksek bağlantı havuzu kullanımı",
            uptime_percentage=99.85,
        )
        assert comp.status == ComponentStatus.DEGRADED_PERFORMANCE
        assert comp.status_message == "Yüksek bağlantı havuzu kullanımı"
        assert comp.uptime_percentage == 99.85

    def test_update_unknown_component_raises(self, coordinator: StatusPageCoordinator) -> None:
        """Verify updating non-existent component raises KeyError."""
        with pytest.raises(KeyError, match="not registered"):
            coordinator.update_component_status("invalid_service", ComponentStatus.MAJOR_OUTAGE)


class TestOverallStatusDerivation:
    """Tests for aggregating individual components and incidents into overall system status."""

    def test_all_systems_operational(self, coordinator: StatusPageCoordinator) -> None:
        """All components operational yields ALL_SYSTEMS_OPERATIONAL."""
        assert coordinator.calculate_overall_status() == SystemOverallStatus.ALL_SYSTEMS_OPERATIONAL

    def test_degraded_performance_override(self, coordinator: StatusPageCoordinator) -> None:
        """Any degraded component yields DEGRADED_PERFORMANCE."""
        coordinator.update_component_status(
            SystemComponent.CORE_API.value, ComponentStatus.DEGRADED_PERFORMANCE
        )
        assert coordinator.calculate_overall_status() == SystemOverallStatus.DEGRADED_PERFORMANCE

    def test_partial_outage_override(self, coordinator: StatusPageCoordinator) -> None:
        """Partial outage component yields PARTIAL_SYSTEM_OUTAGE."""
        coordinator.update_component_status(
            SystemComponent.MARKET_DATA.value, ComponentStatus.PARTIAL_OUTAGE
        )
        assert coordinator.calculate_overall_status() == SystemOverallStatus.PARTIAL_SYSTEM_OUTAGE

    def test_major_outage_override(self, coordinator: StatusPageCoordinator) -> None:
        """Major outage component yields MAJOR_SYSTEM_OUTAGE."""
        coordinator.update_component_status(
            SystemComponent.DATABASE.value, ComponentStatus.MAJOR_OUTAGE
        )
        assert coordinator.calculate_overall_status() == SystemOverallStatus.MAJOR_SYSTEM_OUTAGE

    def test_critical_incident_forces_major_outage(
        self, coordinator: StatusPageCoordinator
    ) -> None:
        """Critical incident forces MAJOR_SYSTEM_OUTAGE regardless of component status."""
        coordinator.create_incident(
            title="Verimerkezi Bağlantı Kesintisi",
            severity=IncidentSeverity.CRITICAL,
            affected_components=[SystemComponent.CORE_API.value],
            initial_message="Tüm API istekleri zaman aşımına uğruyor.",
        )
        assert coordinator.calculate_overall_status() == SystemOverallStatus.MAJOR_SYSTEM_OUTAGE


class TestIncidentLifecycle:
    """Tests for declaring, updating, and resolving operational incidents."""

    def test_incident_declaration_and_updates(self, coordinator: StatusPageCoordinator) -> None:
        """Declare incident, add investigation steps, and resolve."""
        inc = coordinator.create_incident(
            title="BIST Veri Hattı Gecikmesi",
            severity=IncidentSeverity.MAJOR,
            affected_components=[SystemComponent.MARKET_DATA.value],
            initial_message="BIST verilerinde 30 saniyelik gecikme tespit edildi.",
            client_banner_text="Piyasa verileri gecikmeli yansıyabilir.",
        )
        assert inc.stage == IncidentStage.INVESTIGATING
        assert len(inc.updates) == 1
        assert inc.client_banner_text == "Piyasa verileri gecikmeli yansıyabilir."

        # Verify affected component state updated to PARTIAL_OUTAGE
        market_comp = coordinator.get_component(SystemComponent.MARKET_DATA.value)
        assert market_comp is not None
        assert market_comp.status == ComponentStatus.PARTIAL_OUTAGE

        # Add identified stage update
        inc = coordinator.add_incident_update(
            incident_id=inc.incident_id,
            stage=IncidentStage.IDENTIFIED,
            message="Sağlayıcı websocket hattında paket kaybı belirlendi.",
        )
        assert inc.stage == IncidentStage.IDENTIFIED
        assert len(inc.updates) == 2

        # Resolve incident
        inc = coordinator.resolve_incident(
            incident_id=inc.incident_id,
            resolution_message="Yedek sağlayıcıya geçildi; veri akışı normale döndü.",
        )
        assert inc.stage == IncidentStage.RESOLVED
        assert inc.resolved_at is not None
        assert len(inc.updates) == 3

        # Component should recover to operational
        recovered_comp = coordinator.get_component(SystemComponent.MARKET_DATA.value)
        assert recovered_comp is not None
        assert recovered_comp.status == ComponentStatus.OPERATIONAL

    def test_active_vs_history_incidents(self, coordinator: StatusPageCoordinator) -> None:
        """Verify active incidents only return unresolved entries."""
        inc1 = coordinator.create_incident(
            title="Incident 1",
            severity=IncidentSeverity.MINOR,
            affected_components=[SystemComponent.ASYNC_WORKER.value],
            initial_message="Worker kuyruğu yavaşladı.",
        )
        inc2 = coordinator.create_incident(
            title="Incident 2",
            severity=IncidentSeverity.MAJOR,
            affected_components=[SystemComponent.DATABASE.value],
            initial_message="DB disk uyarısı.",
        )

        assert len(coordinator.get_active_incidents()) == 2
        assert len(coordinator.get_incident_history()) == 2

        # Resolve inc1
        coordinator.resolve_incident(inc1.incident_id, "Kuyruk boşaltıldı.")

        active = coordinator.get_active_incidents()
        assert len(active) == 1
        assert active[0].incident_id == inc2.incident_id

        history = coordinator.get_incident_history()
        assert len(history) == 2

    def test_update_unknown_incident_raises(self, coordinator: StatusPageCoordinator) -> None:
        """Updating non-existent incident raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            coordinator.add_incident_update("inc-nonexistent", IncidentStage.IDENTIFIED, "msg")


class TestMaintenanceWindows:
    """Tests for scheduling and executing planned maintenance."""

    def test_maintenance_lifecycle(self, coordinator: StatusPageCoordinator) -> None:
        """Schedule, start, and complete maintenance window."""
        now = datetime.datetime.now(datetime.UTC)
        m = coordinator.schedule_maintenance(
            title="PostgreSQL Sürüm Yükseltme",
            description="Veritabanı 16.2 sürümüne güncellenecektir.",
            affected_components=[SystemComponent.DATABASE.value],
            scheduled_start=now - datetime.timedelta(minutes=10),
            scheduled_end=now + datetime.timedelta(hours=2),
        )
        assert m.status == MaintenanceStatus.SCHEDULED

        upcoming = coordinator.get_upcoming_maintenance()
        assert any(item.maintenance_id == m.maintenance_id for item in upcoming)

        # Start maintenance
        m = coordinator.start_maintenance(m.maintenance_id)
        assert m.status == MaintenanceStatus.IN_PROGRESS
        db_comp = coordinator.get_component(SystemComponent.DATABASE.value)
        assert db_comp is not None
        assert db_comp.status == ComponentStatus.MAINTENANCE
        assert coordinator.calculate_overall_status() == SystemOverallStatus.SCHEDULED_MAINTENANCE

        # Complete maintenance
        m = coordinator.complete_maintenance(m.maintenance_id)
        assert m.status == MaintenanceStatus.COMPLETED
        recovered_db = coordinator.get_component(SystemComponent.DATABASE.value)
        assert recovered_db is not None
        assert recovered_db.status == ComponentStatus.OPERATIONAL

    def test_unknown_maintenance_raises(self, coordinator: StatusPageCoordinator) -> None:
        """Starting unknown maintenance raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            coordinator.start_maintenance("maint-unknown")


class TestOutageBanners:
    """Tests for web and mobile client notification banner generation."""

    def test_banner_when_all_systems_normal(self, coordinator: StatusPageCoordinator) -> None:
        """When everything is normal, active banner is False."""
        banner = coordinator.get_active_banner()
        assert not banner.active
        assert banner.title == "Tüm Sistemler Çalışıyor"

    def test_banner_during_active_outage(self, coordinator: StatusPageCoordinator) -> None:
        """When outage occurs, active banner is True with critical severity."""
        coordinator.create_incident(
            title="Genel API Kesintisi",
            severity=IncidentSeverity.CRITICAL,
            affected_components=[SystemComponent.CORE_API.value],
            initial_message="Bağlantı hatası alınıyor.",
            client_banner_text=(
                "API servislerinde kesinti mevcuttur, teknik ekip müdahale etmektedir."
            ),
        )
        banner = coordinator.get_active_banner()
        assert banner.active
        assert banner.severity == "critical"
        assert "API servislerinde kesinti" in banner.message
        assert "status-banner--critical" in banner.banner_html

    def test_custom_banner_override(self, coordinator: StatusPageCoordinator) -> None:
        """Manually set custom banner overrides dynamic generation."""
        custom = OutageBanner(
            active=True,
            severity="warning",
            title="Piyasa Tatili",
            message="Bugün resmi tatil nedeniyle BIST kapalıdır.",
        )
        coordinator.set_custom_banner(custom)
        assert coordinator.get_active_banner() == custom

        coordinator.set_custom_banner(None)
        assert not coordinator.get_active_banner().active


class TestSystemSync:
    """Tests for synchronizing with ProviderAlertManager and OperationalMonitor."""

    def test_sync_with_provider_alerts(self, coordinator: StatusPageCoordinator) -> None:
        """Syncs provider outages to market_data component."""
        mock_provider_manager = MagicMock()
        mock_provider_manager.get_active_outages.return_value = ["yfinance", "bist_feed"]

        coordinator.sync_with_provider_alerts(mock_provider_manager)
        market_comp = coordinator.get_component(SystemComponent.MARKET_DATA.value)
        assert market_comp is not None
        assert market_comp.status == ComponentStatus.PARTIAL_OUTAGE
        assert "yfinance, bist_feed" in (market_comp.status_message or "")

        # Test recovery
        mock_provider_manager.get_active_outages.return_value = []
        coordinator.sync_with_provider_alerts(mock_provider_manager)
        recovered_market = coordinator.get_component(SystemComponent.MARKET_DATA.value)
        assert recovered_market is not None
        assert recovered_market.status == ComponentStatus.OPERATIONAL

    def test_sync_with_operational_monitor(self, coordinator: StatusPageCoordinator) -> None:
        """Syncs API SLA breaches to core_api component."""
        mock_operational_monitor = MagicMock()
        mock_operational_monitor.get_operational_report.return_value = {
            "api_sla": {
                "error_rate_percent": 8.5,
                "p95_latency_ms": 1500.0,
            }
        }

        coordinator.sync_with_operational_monitor(mock_operational_monitor)
        api_comp = coordinator.get_component(SystemComponent.CORE_API.value)
        assert api_comp is not None
        assert api_comp.status == ComponentStatus.DEGRADED_PERFORMANCE
        assert "%8.5" in (api_comp.status_message or "")

        # Test recovery
        mock_operational_monitor.get_operational_report.return_value = {
            "api_sla": {
                "error_rate_percent": 0.2,
                "p95_latency_ms": 85.0,
            }
        }
        coordinator.sync_with_operational_monitor(mock_operational_monitor)
        recovered_api = coordinator.get_component(SystemComponent.CORE_API.value)
        assert recovered_api is not None
        assert recovered_api.status == ComponentStatus.OPERATIONAL


class TestPrometheusMetricsAndExposition:
    """Tests for Prometheus gauge exposition for status page."""

    def test_prometheus_metrics_registered(self, coordinator: StatusPageCoordinator) -> None:
        """Verify status page gauges and counters appear in Prometheus text output."""
        coordinator.update_component_status(
            SystemComponent.CORE_API.value, ComponentStatus.OPERATIONAL
        )
        text = REGISTRY.generate_prometheus_text()
        assert "status_page_component_status" in text
        assert "status_page_overall_status_gauge" in text
        assert "status_page_active_incidents_gauge" in text

    def test_status_code_mappings(self) -> None:
        """Verify numeric mapping codes are contiguous integers."""
        assert COMPONENT_STATUS_CODE_MAP[ComponentStatus.OPERATIONAL] == 0
        assert COMPONENT_STATUS_CODE_MAP[ComponentStatus.MAJOR_OUTAGE] == 3
        assert OVERALL_STATUS_CODE_MAP[SystemOverallStatus.ALL_SYSTEMS_OPERATIONAL] == 0
        assert OVERALL_STATUS_CODE_MAP[SystemOverallStatus.MAJOR_SYSTEM_OUTAGE] == 3


class TestHTMLAndPublicOutput:
    """Tests for public status summary and standalone HTML generation."""

    def test_public_status_schema(self, coordinator: StatusPageCoordinator) -> None:
        """Verify public status dictionary structure."""
        data = coordinator.get_public_status()
        assert "overall_status" in data
        assert "overall_status_display" in data
        assert "components" in data
        assert "banner" in data
        assert "service_disclaimer" in data
        assert len(data["components"]) >= 6

    def test_generate_html_status_page(self, coordinator: StatusPageCoordinator) -> None:
        """Verify responsive HTML status dashboard rendering."""
        html_page = coordinator.generate_html_status_page()
        assert "<!DOCTYPE html>" in html_page
        assert "Borsa Analiz Sistem Durumu" in html_page
        assert "REST API ve Ağ Geçidi" in html_page
        assert "service_disclaimer" not in html_page  # checks rendered disclaimer string directly
        assert "Yatırım kararlarında doğrudan referans alınamaz" in html_page


class TestApiEndpoints:
    """Integration tests for FastAPI /status routes."""

    def test_get_status_json_endpoint(self, client: TestClient) -> None:
        """GET /status returns 200 with status summary."""
        res = client.get("/status")
        assert res.status_code == 200
        data = res.json()
        assert "overall_status" in data
        assert "components" in data
        assert isinstance(data["components"], list)

    def test_get_status_banner_endpoint(self, client: TestClient) -> None:
        """GET /status/banner returns 200 with banner payload."""
        res = client.get("/status/banner")
        assert res.status_code == 200
        data = res.json()
        assert "active" in data
        assert "severity" in data
        assert "title" in data

    def test_get_status_incidents_endpoint(self, client: TestClient) -> None:
        """GET /status/incidents returns 200 with incident list."""
        res = client.get("/status/incidents")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_status_html_endpoint(self, client: TestClient) -> None:
        """GET /status/html returns 200 with text/html content-type."""
        res = client.get("/status/html")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "<!DOCTYPE html>" in res.text
