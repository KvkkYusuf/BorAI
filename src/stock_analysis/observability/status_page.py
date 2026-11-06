"""Status page management, service outage notifications, and incident coordination.

Provides:
- Component operational status tracking (API, Market Data, Database, ML Forecasts, Workers, Alerts)
- Incident lifecycle management (Investigating, Identified, Monitoring, Resolved)
- Scheduled maintenance window tracking
- Service outage notification banners for web/mobile client display
- Responsive HTML status page rendering and JSON status API endpoints
- Dynamic Prometheus metrics integration for availability and incident tracking
"""

from __future__ import annotations

import datetime
import html
import threading
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    STATUS_PAGE_ACTIVE_INCIDENTS_GAUGE,
    STATUS_PAGE_COMPONENT_STATUS,
    STATUS_PAGE_INCIDENT_UPDATES_TOTAL,
    STATUS_PAGE_OVERALL_STATUS_GAUGE,
)


class SystemComponent(StrEnum):
    """Core identifiable system services and infrastructure components."""

    CORE_API = "core_api"
    MARKET_DATA = "market_data"
    DATABASE = "database"
    ML_FORECAST = "ml_forecast"
    ASYNC_WORKER = "async_worker"
    NOTIFICATIONS = "notifications"


class ComponentStatus(StrEnum):
    """Operational health status of an individual system component."""

    OPERATIONAL = "operational"
    DEGRADED_PERFORMANCE = "degraded_performance"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    MAINTENANCE = "maintenance"


class SystemOverallStatus(StrEnum):
    """Aggregate high-level health state of the entire platform."""

    ALL_SYSTEMS_OPERATIONAL = "all_systems_operational"
    DEGRADED_PERFORMANCE = "degraded_performance"
    PARTIAL_SYSTEM_OUTAGE = "partial_system_outage"
    MAJOR_SYSTEM_OUTAGE = "major_system_outage"
    SCHEDULED_MAINTENANCE = "scheduled_maintenance"


class IncidentSeverity(StrEnum):
    """Severity classification of an outage or operational disruption."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"
    MAINTENANCE = "maintenance"


class IncidentStage(StrEnum):
    """Lifecycle stage of an ongoing or resolved operational incident."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class MaintenanceStatus(StrEnum):
    """Execution status of a planned maintenance window."""

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


COMPONENT_STATUS_CODE_MAP: dict[ComponentStatus, int] = {
    ComponentStatus.OPERATIONAL: 0,
    ComponentStatus.DEGRADED_PERFORMANCE: 1,
    ComponentStatus.PARTIAL_OUTAGE: 2,
    ComponentStatus.MAJOR_OUTAGE: 3,
    ComponentStatus.MAINTENANCE: 4,
}

OVERALL_STATUS_CODE_MAP: dict[SystemOverallStatus, int] = {
    SystemOverallStatus.ALL_SYSTEMS_OPERATIONAL: 0,
    SystemOverallStatus.DEGRADED_PERFORMANCE: 1,
    SystemOverallStatus.PARTIAL_SYSTEM_OUTAGE: 2,
    SystemOverallStatus.MAJOR_SYSTEM_OUTAGE: 3,
    SystemOverallStatus.SCHEDULED_MAINTENANCE: 4,
}


@dataclass
class ComponentRecord:
    """Status record and metadata for a registered platform component."""

    component_id: str
    name: str
    description: str
    status: ComponentStatus = ComponentStatus.OPERATIONAL
    updated_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    status_message: str | None = None
    is_third_party: bool = False
    uptime_percentage: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        """Convert component state to public JSON-serializable dictionary."""
        return {
            "component_id": self.component_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "updated_at": self.updated_at.isoformat(),
            "status_message": self.status_message,
            "is_third_party": self.is_third_party,
            "uptime_percentage": round(self.uptime_percentage, 2),
        }


@dataclass
class IncidentUpdate:
    """Chronological progress update entry for an incident."""

    update_id: str
    timestamp: datetime.datetime
    stage: IncidentStage
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Convert update entry to public dictionary."""
        return {
            "update_id": self.update_id,
            "timestamp": self.timestamp.isoformat(),
            "stage": self.stage.value,
            "message": self.message,
        }


@dataclass
class IncidentRecord:
    """Complete incident record with historical lifecycle updates."""

    incident_id: str
    title: str
    severity: IncidentSeverity
    stage: IncidentStage
    affected_components: list[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime
    resolved_at: datetime.datetime | None = None
    updates: list[IncidentUpdate] = field(default_factory=list)
    client_banner_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert incident record to public dictionary."""
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity.value,
            "stage": self.stage.value,
            "affected_components": list(self.affected_components),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "client_banner_text": self.client_banner_text,
            "updates": [u.to_dict() for u in self.updates],
        }


@dataclass
class MaintenanceWindow:
    """Scheduled infrastructure or software maintenance window."""

    maintenance_id: str
    title: str
    description: str
    affected_components: list[str]
    scheduled_start: datetime.datetime
    scheduled_end: datetime.datetime
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert maintenance window to dictionary."""
        return {
            "maintenance_id": self.maintenance_id,
            "title": self.title,
            "description": self.description,
            "affected_components": list(self.affected_components),
            "scheduled_start": self.scheduled_start.isoformat(),
            "scheduled_end": self.scheduled_end.isoformat(),
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@dataclass
class OutageBanner:
    """Notice banner for web and mobile client applications during disruptions."""

    active: bool
    severity: str  # "info", "warning", "critical"
    title: str
    message: str
    incident_id: str | None = None
    affected_components: list[str] = field(default_factory=list)
    estimated_resolution: str | None = None
    action_required: str | None = None
    banner_html: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert notice banner to dictionary."""
        return {
            "active": self.active,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "incident_id": self.incident_id,
            "affected_components": self.affected_components,
            "estimated_resolution": self.estimated_resolution,
            "action_required": self.action_required,
            "banner_html": self.banner_html,
        }


class StatusPageCoordinator:
    """Manages platform service health, incident declarations, and status notifications."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._components: dict[str, ComponentRecord] = {}
        self._incidents: dict[str, IncidentRecord] = {}
        self._maintenance_windows: dict[str, MaintenanceWindow] = {}
        self._custom_banner: OutageBanner | None = None

        self._initialize_default_components()
        self._update_all_metrics()

    def _initialize_default_components(self) -> None:
        """Seed the standard set of system components."""
        defaults = [
            (
                SystemComponent.CORE_API.value,
                "REST API ve Ağ Geçidi",
                "HTTP yönlendirme, kimlik doğrulama ve API istek işleme hattı.",
                False,
            ),
            (
                SystemComponent.MARKET_DATA.value,
                "Piyasa Veri Hattı",
                "BIST hisse fiyatları, emir defteri ve takas veri sağlayıcı entegrasyonu.",
                True,
            ),
            (
                SystemComponent.DATABASE.value,
                "Veritabanı ve Kalıcı Depolama",
                "İlişkisel veritabanı kümesi, işlem günlükleri ve anlık veriler.",
                False,
            ),
            (
                SystemComponent.ML_FORECAST.value,
                "Yapay Zeka Tahmin Motoru",
                "PyTorch model çıkarım ve tahmin üretim servisi.",
                False,
            ),
            (
                SystemComponent.ASYNC_WORKER.value,
                "Arka Plan Görev Kuyruğu",
                "Asenkron veri işleme, periyodik temizlik ve analiz kuyrukları.",
                False,
            ),
            (
                SystemComponent.NOTIFICATIONS.value,
                "Bildirim ve Alarm Servisi",
                "E-posta, WebSocket ve anlık fiyat uyarı iletim kanalları.",
                False,
            ),
        ]
        now = datetime.datetime.now(datetime.UTC)
        for comp_id, name, desc, is_third in defaults:
            self._components[comp_id] = ComponentRecord(
                component_id=comp_id,
                name=name,
                description=desc,
                status=ComponentStatus.OPERATIONAL,
                updated_at=now,
                is_third_party=is_third,
                uptime_percentage=100.0,
            )

    def register_component(
        self,
        component_id: str,
        name: str,
        description: str,
        is_third_party: bool = False,
    ) -> ComponentRecord:
        """Register a new or custom system component."""
        with self._lock:
            record = ComponentRecord(
                component_id=component_id,
                name=name,
                description=description,
                status=ComponentStatus.OPERATIONAL,
                updated_at=datetime.datetime.now(datetime.UTC),
                is_third_party=is_third_party,
            )
            self._components[component_id] = record
            self._update_all_metrics()
            return record

    def update_component_status(
        self,
        component_id: str,
        status: ComponentStatus,
        status_message: str | None = None,
        uptime_percentage: float | None = None,
    ) -> ComponentRecord:
        """Update operational status and diagnostic message of a component."""
        with self._lock:
            if component_id not in self._components:
                raise KeyError(f"Component '{component_id}' is not registered.")
            comp = self._components[component_id]
            comp.status = status
            comp.status_message = status_message
            comp.updated_at = datetime.datetime.now(datetime.UTC)
            if uptime_percentage is not None:
                comp.uptime_percentage = max(0.0, min(100.0, uptime_percentage))
            self._update_all_metrics()
            return comp

    def get_component(self, component_id: str) -> ComponentRecord | None:
        """Retrieve component record by ID."""
        with self._lock:
            return self._components.get(component_id)

    def get_all_components(self) -> list[ComponentRecord]:
        """Return list of all registered system components."""
        with self._lock:
            return list(self._components.values())

    def calculate_overall_status(self) -> SystemOverallStatus:
        """Derive overall system status from component statuses and active incidents."""
        with self._lock:
            active_incidents = [
                inc for inc in self._incidents.values() if inc.stage != IncidentStage.RESOLVED
            ]

            # Critical or Major incident overrides
            for inc in active_incidents:
                if inc.severity == IncidentSeverity.CRITICAL:
                    return SystemOverallStatus.MAJOR_SYSTEM_OUTAGE
                if inc.severity == IncidentSeverity.MAJOR:
                    return SystemOverallStatus.PARTIAL_SYSTEM_OUTAGE

            # In-progress maintenance check
            in_prog_maint = [
                m
                for m in self._maintenance_windows.values()
                if m.status == MaintenanceStatus.IN_PROGRESS
            ]
            if in_prog_maint:
                return SystemOverallStatus.SCHEDULED_MAINTENANCE

            # Component level status checks
            statuses = [c.status for c in self._components.values()]
            if any(s == ComponentStatus.MAJOR_OUTAGE for s in statuses):
                return SystemOverallStatus.MAJOR_SYSTEM_OUTAGE
            if any(s == ComponentStatus.PARTIAL_OUTAGE for s in statuses):
                return SystemOverallStatus.PARTIAL_SYSTEM_OUTAGE
            if any(s == ComponentStatus.DEGRADED_PERFORMANCE for s in statuses):
                return SystemOverallStatus.DEGRADED_PERFORMANCE
            if any(s == ComponentStatus.MAINTENANCE for s in statuses):
                return SystemOverallStatus.SCHEDULED_MAINTENANCE

            return SystemOverallStatus.ALL_SYSTEMS_OPERATIONAL

    def create_incident(
        self,
        title: str,
        severity: IncidentSeverity,
        affected_components: list[str],
        initial_message: str,
        client_banner_text: str | None = None,
    ) -> IncidentRecord:
        """Declare a new operational incident and publish initial investigation update."""
        with self._lock:
            incident_id = f"inc-{uuid.uuid4().hex[:8]}"
            now = datetime.datetime.now(datetime.UTC)
            update = IncidentUpdate(
                update_id=f"upd-{uuid.uuid4().hex[:6]}",
                timestamp=now,
                stage=IncidentStage.INVESTIGATING,
                message=initial_message,
            )
            incident = IncidentRecord(
                incident_id=incident_id,
                title=title,
                severity=severity,
                stage=IncidentStage.INVESTIGATING,
                affected_components=affected_components,
                created_at=now,
                updated_at=now,
                updates=[update],
                client_banner_text=client_banner_text or initial_message,
            )
            self._incidents[incident_id] = incident

            # Reflect incident on affected components if appropriate
            for comp_id in affected_components:
                if comp_id in self._components:
                    comp = self._components[comp_id]
                    if severity == IncidentSeverity.CRITICAL:
                        comp.status = ComponentStatus.MAJOR_OUTAGE
                    elif severity == IncidentSeverity.MAJOR:
                        comp.status = ComponentStatus.PARTIAL_OUTAGE
                    elif severity == IncidentSeverity.MINOR:
                        comp.status = ComponentStatus.DEGRADED_PERFORMANCE
                    comp.status_message = f"Olay: {title}"
                    comp.updated_at = now

            STATUS_PAGE_INCIDENT_UPDATES_TOTAL.inc(
                amount=1.0,
                labels={
                    "severity": severity.value,
                    "stage": IncidentStage.INVESTIGATING.value,
                },
            )
            self._update_all_metrics()
            return incident

    def add_incident_update(
        self,
        incident_id: str,
        stage: IncidentStage,
        message: str,
    ) -> IncidentRecord:
        """Append a chronological update to an existing incident."""
        with self._lock:
            if incident_id not in self._incidents:
                raise KeyError(f"Incident '{incident_id}' not found.")
            incident = self._incidents[incident_id]
            now = datetime.datetime.now(datetime.UTC)
            update = IncidentUpdate(
                update_id=f"upd-{uuid.uuid4().hex[:6]}",
                timestamp=now,
                stage=stage,
                message=message,
            )
            incident.updates.append(update)
            incident.stage = stage
            incident.updated_at = now

            if stage == IncidentStage.RESOLVED and not incident.resolved_at:
                incident.resolved_at = now
                self._recover_affected_components(incident.affected_components)

            STATUS_PAGE_INCIDENT_UPDATES_TOTAL.inc(
                amount=1.0,
                labels={
                    "severity": incident.severity.value,
                    "stage": stage.value,
                },
            )
            self._update_all_metrics()
            return incident

    def resolve_incident(
        self,
        incident_id: str,
        resolution_message: str,
    ) -> IncidentRecord:
        """Mark an incident as fully resolved and restore component states."""
        return self.add_incident_update(
            incident_id=incident_id,
            stage=IncidentStage.RESOLVED,
            message=resolution_message,
        )

    def _recover_affected_components(self, affected_components: list[str]) -> None:
        """Restore components to operational if no other active incident touches them."""
        active_incidents = [
            inc for inc in self._incidents.values() if inc.stage != IncidentStage.RESOLVED
        ]
        still_affected: set[str] = set()
        for inc in active_incidents:
            still_affected.update(inc.affected_components)

        now = datetime.datetime.now(datetime.UTC)
        for comp_id in affected_components:
            if comp_id in self._components and comp_id not in still_affected:
                comp = self._components[comp_id]
                comp.status = ComponentStatus.OPERATIONAL
                comp.status_message = None
                comp.updated_at = now

    def get_active_incidents(self) -> list[IncidentRecord]:
        """Retrieve all currently active (non-resolved) incidents."""
        with self._lock:
            return [
                inc
                for inc in sorted(
                    self._incidents.values(),
                    key=lambda x: x.created_at,
                    reverse=True,
                )
                if inc.stage != IncidentStage.RESOLVED
            ]

    def get_incident_history(self, limit: int = 20) -> list[IncidentRecord]:
        """Retrieve recent incidents including resolved ones."""
        with self._lock:
            all_incidents = sorted(
                self._incidents.values(),
                key=lambda x: x.created_at,
                reverse=True,
            )
            return all_incidents[:limit]

    def schedule_maintenance(
        self,
        title: str,
        description: str,
        affected_components: list[str],
        scheduled_start: datetime.datetime,
        scheduled_end: datetime.datetime,
    ) -> MaintenanceWindow:
        """Schedule an upcoming maintenance window."""
        with self._lock:
            maint_id = f"maint-{uuid.uuid4().hex[:8]}"
            maint = MaintenanceWindow(
                maintenance_id=maint_id,
                title=title,
                description=description,
                affected_components=affected_components,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                status=MaintenanceStatus.SCHEDULED,
            )
            self._maintenance_windows[maint_id] = maint
            self._update_all_metrics()
            return maint

    def start_maintenance(self, maintenance_id: str) -> MaintenanceWindow:
        """Begin execution of a scheduled maintenance window."""
        with self._lock:
            if maintenance_id not in self._maintenance_windows:
                raise KeyError(f"Maintenance '{maintenance_id}' not found.")
            m = self._maintenance_windows[maintenance_id]
            now = datetime.datetime.now(datetime.UTC)
            m.status = MaintenanceStatus.IN_PROGRESS
            m.started_at = now
            for comp_id in m.affected_components:
                if comp_id in self._components:
                    comp = self._components[comp_id]
                    comp.status = ComponentStatus.MAINTENANCE
                    comp.status_message = f"Planlı Bakım: {m.title}"
                    comp.updated_at = now
            self._update_all_metrics()
            return m

    def complete_maintenance(self, maintenance_id: str) -> MaintenanceWindow:
        """Conclude an in-progress maintenance window."""
        with self._lock:
            if maintenance_id not in self._maintenance_windows:
                raise KeyError(f"Maintenance '{maintenance_id}' not found.")
            m = self._maintenance_windows[maintenance_id]
            now = datetime.datetime.now(datetime.UTC)
            m.status = MaintenanceStatus.COMPLETED
            m.completed_at = now
            for comp_id in m.affected_components:
                if comp_id in self._components:
                    comp = self._components[comp_id]
                    comp.status = ComponentStatus.OPERATIONAL
                    comp.status_message = None
                    comp.updated_at = now
            self._update_all_metrics()
            return m

    def get_upcoming_maintenance(self) -> list[MaintenanceWindow]:
        """List active or future scheduled maintenance windows."""
        with self._lock:
            now = datetime.datetime.now(datetime.UTC)
            return [
                m
                for m in sorted(
                    self._maintenance_windows.values(),
                    key=lambda x: x.scheduled_start,
                )
                if m.status in (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS)
                and (m.scheduled_end >= now or m.status == MaintenanceStatus.IN_PROGRESS)
            ]

    def set_custom_banner(self, banner: OutageBanner | None) -> None:
        """Manually override or clear the system client notice banner."""
        with self._lock:
            self._custom_banner = banner

    def get_active_banner(self) -> OutageBanner:
        """Generate client-side outage notification banner payload."""
        with self._lock:
            if self._custom_banner is not None:
                return self._custom_banner

            active_incidents = self.get_active_incidents()
            if active_incidents:
                # Prioritize most severe incident
                highest_incident = sorted(
                    active_incidents,
                    key=lambda x: (
                        0
                        if x.severity == IncidentSeverity.CRITICAL
                        else 1
                        if x.severity == IncidentSeverity.MAJOR
                        else 2
                    ),
                )[0]
                sev_type = (
                    "critical"
                    if highest_incident.severity == IncidentSeverity.CRITICAL
                    else "warning"
                )
                title = f"Hizmet Kesintisi: {highest_incident.title}"
                msg = highest_incident.client_banner_text or (
                    highest_incident.updates[-1].message
                    if highest_incident.updates
                    else "Ekiplerimiz müdahale etmektedir."
                )
                return OutageBanner(
                    active=True,
                    severity=sev_type,
                    title=title,
                    message=msg,
                    incident_id=highest_incident.incident_id,
                    affected_components=highest_incident.affected_components,
                    estimated_resolution="Mühendislik ekibi aktif olarak incelemektedir.",
                    action_required=(
                        "Herhangi bir aksiyon gerekmemektedir; sistem otomatik toparlanacaktır."
                    ),
                    banner_html=(
                        f'<div class="status-banner status-banner--{sev_type}" role="alert">'
                        f"<strong>{html.escape(title)}</strong>: {html.escape(msg)}"
                        "</div>"
                    ),
                )

            # Check in-progress maintenance
            in_prog_maint = [
                m
                for m in self._maintenance_windows.values()
                if m.status == MaintenanceStatus.IN_PROGRESS
            ]
            if in_prog_maint:
                m = in_prog_maint[0]
                m_end_str = m.scheduled_end.strftime("%H:%M UTC")
                return OutageBanner(
                    active=True,
                    severity="info",
                    title=f"Planlı Bakım Çalışması: {m.title}",
                    message=m.description,
                    incident_id=m.maintenance_id,
                    affected_components=m.affected_components,
                    estimated_resolution=f"Planlanan bitiş: {m_end_str}",
                    action_required="Bazı sorgularda gecikmeler yaşanabilir.",
                    banner_html=(
                        f'<div class="status-banner status-banner--info" role="status">'
                        f"<strong>Planlı Bakım</strong>: {html.escape(m.title)} devam ediyor."
                        "</div>"
                    ),
                )

            # All systems normal banner
            return OutageBanner(
                active=False,
                severity="info",
                title="Tüm Sistemler Çalışıyor",
                message="Tüm servisler normal parametreler dahilinde hizmet vermektedir.",
                incident_id=None,
                affected_components=[],
                estimated_resolution=None,
                action_required=None,
                banner_html="",
            )

    def sync_with_provider_alerts(self, provider_alert_manager: Any) -> None:
        """Synchronize market data component health with external provider alert manager."""
        with self._lock:
            active_outages = provider_alert_manager.get_active_outages()
            comp = self._components.get(SystemComponent.MARKET_DATA.value)
            if not comp:
                return

            if active_outages:
                providers_str = ", ".join(active_outages)
                comp.status = ComponentStatus.PARTIAL_OUTAGE
                comp.status_message = f"Piyasa verisi sağlayıcılarında kesinti: {providers_str}"
                comp.updated_at = datetime.datetime.now(datetime.UTC)
            elif comp.status == ComponentStatus.PARTIAL_OUTAGE and "Piyasa verisi" in (
                comp.status_message or ""
            ):
                comp.status = ComponentStatus.OPERATIONAL
                comp.status_message = None
                comp.updated_at = datetime.datetime.now(datetime.UTC)
            self._update_all_metrics()

    def sync_with_operational_monitor(self, operational_monitor: Any) -> None:
        """Synchronize core API component health with operational SLA telemetry."""
        with self._lock:
            report = operational_monitor.get_operational_report()
            api_sla = report.get("api_sla", {})
            error_rate = api_sla.get("error_rate_percent", 0.0)
            p95_latency = api_sla.get("p95_latency_ms", 0.0)

            comp = self._components.get(SystemComponent.CORE_API.value)
            if not comp:
                return

            # Check thresholds: error rate > 5% or p95 > 1000ms
            if error_rate > 5.0:
                comp.status = ComponentStatus.DEGRADED_PERFORMANCE
                comp.status_message = f"Yüksek API hata oranı tespit edildi (%{error_rate:.1f})"
                comp.updated_at = datetime.datetime.now(datetime.UTC)
            elif p95_latency > 1000.0:
                comp.status = ComponentStatus.DEGRADED_PERFORMANCE
                comp.status_message = f"Yüksek API p95 yanıt süresi ({p95_latency:.0f}ms)"
                comp.updated_at = datetime.datetime.now(datetime.UTC)
            elif comp.status == ComponentStatus.DEGRADED_PERFORMANCE and (
                "API" in (comp.status_message or "")
            ):
                comp.status = ComponentStatus.OPERATIONAL
                comp.status_message = None
                comp.updated_at = datetime.datetime.now(datetime.UTC)
            self._update_all_metrics()

    def get_public_status(self) -> dict[str, Any]:
        """Generate comprehensive public status summary report."""
        with self._lock:
            overall = self.calculate_overall_status()
            components = [c.to_dict() for c in self._components.values()]
            active_incidents = [i.to_dict() for i in self.get_active_incidents()]
            upcoming_maintenance = [m.to_dict() for m in self.get_upcoming_maintenance()]
            banner = self.get_active_banner().to_dict()

            return {
                "overall_status": overall.value,
                "overall_status_display": self._format_status_display(overall),
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "banner": banner,
                "components": components,
                "active_incidents": active_incidents,
                "upcoming_maintenance": upcoming_maintenance,
                "service_disclaimer": (
                    "Bu durum sayfası bilgilendirme amaçlıdır. "
                    "Yatırım kararlarında doğrudan referans alınamaz."
                ),
            }

    @staticmethod
    def _format_status_display(status: SystemOverallStatus) -> str:
        """Return human-readable Turkish label for overall status."""
        mapping = {
            SystemOverallStatus.ALL_SYSTEMS_OPERATIONAL: "Tüm Sistemler Çalışıyor",
            SystemOverallStatus.DEGRADED_PERFORMANCE: "Performans Düşüşü",
            SystemOverallStatus.PARTIAL_SYSTEM_OUTAGE: "Kısmi Hizmet Kesintisi",
            SystemOverallStatus.MAJOR_SYSTEM_OUTAGE: "Büyük Hizmet Kesintisi",
            SystemOverallStatus.SCHEDULED_MAINTENANCE: "Planlı Bakım Devam Ediyor",
        }
        return mapping.get(status, status.value)

    def generate_html_status_page(self) -> str:
        """Render a standalone, high-performance HTML status dashboard."""
        status_data = self.get_public_status()
        overall = status_data["overall_status"]
        overall_display = status_data["overall_status_display"]
        timestamp = status_data["timestamp"]
        banner = status_data["banner"]

        status_color = (
            "#10b981"
            if overall == SystemOverallStatus.ALL_SYSTEMS_OPERATIONAL.value
            else "#f59e0b"
            if overall == SystemOverallStatus.DEGRADED_PERFORMANCE.value
            else "#ef4444"
            if overall == SystemOverallStatus.MAJOR_SYSTEM_OUTAGE.value
            else "#3b82f6"
        )

        banner_html = ""
        if banner["active"]:
            b_color = (
                "#fee2e2"
                if banner["severity"] == "critical"
                else "#fef3c7"
                if banner["severity"] == "warning"
                else "#e0e7ff"
            )
            b_border = (
                "#ef4444"
                if banner["severity"] == "critical"
                else "#f59e0b"
                if banner["severity"] == "warning"
                else "#6366f1"
            )
            b_text = (
                "#991b1b"
                if banner["severity"] == "critical"
                else "#92400e"
                if banner["severity"] == "warning"
                else "#3730a3"
            )
            res_info = ""
            if banner.get("estimated_resolution"):
                res_esc = html.escape(str(banner["estimated_resolution"]))
                res_info = (
                    f"<div style='font-size:12px; opacity:0.9;'>"
                    f"<strong>Tahmini Çözüm:</strong> {res_esc}</div>"
                )
            banner_html = f"""
            <div style="background:{b_color}; border:1px solid {b_border}; color:{b_text};
                        padding:16px; border-radius:8px; margin-bottom:24px;">
                <h3 style="margin:0 0 6px 0; font-size:16px;">
                    ⚠️ {html.escape(banner["title"])}
                </h3>
                <p style="margin:0 0 8px 0; font-size:14px;">
                    {html.escape(banner["message"])}
                </p>
                {res_info}
            </div>
            """

        components_rows = ""
        for comp in status_data["components"]:
            c_status = comp["status"]
            c_badge_color = (
                "#10b981"
                if c_status == "operational"
                else "#f59e0b"
                if c_status == "degraded_performance"
                else "#ef4444"
                if c_status == "major_outage"
                else "#6366f1"
            )
            status_text = comp["status"].replace("_", " ")
            msg_html = ""
            if comp.get("status_message"):
                msg_html = (
                    f"<div style='color:#f87171; font-size:11px; margin-top:3px;'>"
                    f"ℹ️ {html.escape(str(comp['status_message']))}</div>"
                )
            components_rows += f"""
            <div style="display:flex; justify-content:space-between; align-items:center;
                        padding:14px 18px; border-bottom:1px solid #1e293b;">
                <div>
                    <strong style="color:#f8fafc; font-size:15px;">
                        {html.escape(comp["name"])}
                    </strong>
                    <div style="color:#94a3b8; font-size:12px; margin-top:2px;">
                        {html.escape(comp["description"])}
                    </div>
                    {msg_html}
                </div>
                <div style="text-align:right;">
                    <span style="background:{c_badge_color}22; color:{c_badge_color};
                                 border:1px solid {c_badge_color}55; padding:4px 10px;
                                 border-radius:12px; font-size:12px; font-weight:600;
                                 text-transform:uppercase;">
                        {status_text}
                    </span>
                    <div style="color:#64748b; font-size:11px; margin-top:4px;">
                        %{comp["uptime_percentage"]} Çalışma
                    </div>
                </div>
            </div>
            """

        incidents_html = ""
        if status_data["active_incidents"]:
            incidents_html = (
                "<h2 style='color:#f8fafc; font-size:18px; "
                "margin-top:32px; margin-bottom:12px;'>Aktif Olaylar</h2>"
            )
            for inc in status_data["active_incidents"]:
                incidents_html += f"""
                <div style="background:#0f172a; border:1px solid #334155; border-radius:8px;
                            padding:16px; margin-bottom:12px;">
                    <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                        <strong style="color:#f8fafc; font-size:15px;">
                            {html.escape(inc["title"])}
                        </strong>
                        <span style="color:#f59e0b; font-size:12px; font-weight:600;
                                     text-transform:uppercase;">{inc["stage"]}</span>
                    </div>
                    <div style="color:#94a3b8; font-size:13px; line-height:1.5;">
                """
                for upd in inc.get("updates", []):
                    u_time = upd["timestamp"][:16]
                    u_stage = upd["stage"]
                    u_msg = html.escape(upd["message"])
                    incidents_html += (
                        f"<div style='margin-bottom:6px;'>"
                        f"• <strong>{u_time} [{u_stage}]:</strong> {u_msg}</div>"
                    )
                incidents_html += "</div></div>"
        else:
            incidents_html = (
                "<div style='color:#64748b; font-size:13px; margin-top:28px;'>"
                "Aktif kesinti veya raporlanmış sistem olayı bulunmuyor.</div>"
            )

        font_family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
        disclaimer = status_data["service_disclaimer"]

        return f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sistem Durum Sayfası | Borsa Analiz Platformu</title>
    <style>
        body {{
            background-color: #020617;
            color: #e2e8f0;
            font-family: {font_family};
            margin: 0;
            padding: 32px 16px;
        }}
        .container {{
            max-width: 760px;
            margin: 0 auto;
        }}
        .card {{
            background: #0b1329;
            border: 1px solid #1e293b;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
        }}
        .status-pill {{
            display: inline-flex;
            align-items: center;
            padding: 10px 18px;
            background: {status_color}18;
            border: 1px solid {status_color}55;
            color: {status_color};
            border-radius: 9999px;
            font-weight: 600;
            font-size: 15px;
        }}
        .status-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: {status_color};
            margin-right: 10px;
            box-shadow: 0 0 10px {status_color};
        }}
        .footer {{
            text-align: center;
            margin-top: 36px;
            color: #64748b;
            font-size: 12px;
            line-height: 1.6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1 style="color:#f8fafc; font-size:24px; margin:0 0 4px 0;">
                    Borsa Analiz Sistem Durumu
                </h1>
                <div style="color:#64748b; font-size:12px;">Güncelleme: {timestamp}</div>
            </div>
            <div class="status-pill">
                <span class="status-dot"></span>
                <span>{overall_display}</span>
            </div>
        </div>

        {banner_html}

        <div class="card">
            {components_rows}
        </div>

        {incidents_html}

        <div class="footer">
            Borsa Analiz Bilgilendirme ve Operasyon Portalı · {disclaimer}
        </div>
    </div>
</body>
</html>
"""

    def _update_all_metrics(self) -> None:
        """Refresh Prometheus gauge metrics for component states and incidents."""
        # 1. Component gauges
        for comp in self._components.values():
            code = COMPONENT_STATUS_CODE_MAP.get(comp.status, 0)
            STATUS_PAGE_COMPONENT_STATUS.set(
                float(code),
                labels={"component": comp.component_id},
            )

        # 2. Overall status gauge
        overall = self.calculate_overall_status()
        STATUS_PAGE_OVERALL_STATUS_GAUGE.set(
            float(OVERALL_STATUS_CODE_MAP.get(overall, 0)),
        )

        # 3. Active incidents gauges by severity
        severity_counts: dict[str, int] = {
            IncidentSeverity.MINOR.value: 0,
            IncidentSeverity.MAJOR.value: 0,
            IncidentSeverity.CRITICAL.value: 0,
            IncidentSeverity.MAINTENANCE.value: 0,
        }
        for inc in self._incidents.values():
            if inc.stage != IncidentStage.RESOLVED:
                severity_counts[inc.severity.value] = severity_counts.get(inc.severity.value, 0) + 1

        for sev_val, count in severity_counts.items():
            STATUS_PAGE_ACTIVE_INCIDENTS_GAUGE.set(
                float(count),
                labels={"severity": sev_val},
            )


# Global singleton instance for operational status page governance
STATUS_PAGE_COORDINATOR = StatusPageCoordinator()
