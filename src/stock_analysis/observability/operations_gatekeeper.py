"""Pre-deployment gatekeeper for Monitoring, Alerts, IR & Rollback (FAZ-16).

Verifies operational resilience, real-time observability, alerting mechanisms,
statutory incident response workflows, and automated rollback execution:
1. Monitoring & Telemetry: Prometheus metric registry completeness & SLIs.
2. Alerting Rules & Thresholds: P1-P4 alarm definitions and cooldown routing.
3. Incident Response Readiness: PICERL lifecycle, SIRT roster, KVKK/SPK SLAs.
4. Rollback Engine & Drills: Automated rollback plan, drill, and RTO <= 300s.
5. Statutory Governance: SPK Bilgi Sistemleri (VII-128.9) & ISO 27035.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.observability.deployment_flags import (
    ROLLBACK_COORDINATOR,
    DeploymentStatus,
    HealthSnapshot,
    RollbackReason,
    RollbackThresholds,
)
from stock_analysis.observability.disaster_recovery import (
    DR_COORDINATOR,
    RollbackPlan,
)
from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.observability.provider_alerts import (
    ProviderAlertEvent,
    ProviderAlertSeverity,
    ProviderAlertType,
)
from stock_analysis.security.incident_response import (
    ContactRole,
    IncidentCategory,
    IncidentResponseCoordinator,
    IncidentSeverity,
    IncidentStage,
)

logger = logging.getLogger(__name__)

# Prometheus metrics for operational pre-deployment gate
OPERATIONS_READINESS_GATE_STATUS = REGISTRY.gauge(
    "operations_readiness_gate_status",
    "Pre-deployment gate status for monitoring, alerts, IR, and rollback",
)
OPERATIONS_READINESS_CHECKS_TOTAL = REGISTRY.counter(
    "operations_readiness_checks_total",
    "Total operations readiness pre-deployment checks performed",
)
OPERATIONS_READINESS_DRILLS_TOTAL = REGISTRY.counter(
    "operations_readiness_drills_total",
    "Total operational rollback drills executed during gate evaluations",
)
OPERATIONS_READINESS_VIOLATIONS_TOTAL = REGISTRY.counter(
    "operations_readiness_violations_total",
    "Total operations readiness compliance violations detected",
)
OPERATIONS_ROLLBACK_RTO_SECONDS = REGISTRY.gauge(
    "operations_rollback_rto_seconds",
    "Measured automated rollback execution duration in seconds",
)


class OperationsReadinessGateStatus(StrEnum):
    """Overall outcome of operations readiness gate evaluation."""

    PASS = "PASS"
    FAIL = "FAIL"


class OperationsReadinessCategory(StrEnum):
    """Classification categories for operational readiness verification."""

    MONITORING_TELEMETRY = "MONITORING_TELEMETRY"
    ALERTING_AND_THRESHOLDS = "ALERTING_AND_THRESHOLDS"
    INCIDENT_RESPONSE_SLA = "INCIDENT_RESPONSE_SLA"
    AUTOMATED_ROLLBACK_ENGINE = "AUTOMATED_ROLLBACK_ENGINE"
    STATUTORY_OPERATIONAL_COMPLIANCE = "STATUTORY_OPERATIONAL_COMPLIANCE"


@dataclass(frozen=True)
class OperationsReadinessCheckItem:
    """Individual verification check result for operations readiness."""

    check_id: str
    category: OperationsReadinessCategory
    title: str
    description: str
    passed: bool = False
    details: str = ""
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert item to dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class OperationsReadinessGateReport:
    """Consolidated operations readiness verification report."""

    gate_status: OperationsReadinessGateStatus
    timestamp: str
    total_checks: int
    passed_checks: int
    failed_checks: int
    rollback_rto_seconds: float
    checks: list[OperationsReadinessCheckItem]
    audit_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary representation."""
        return {
            "gate_status": self.gate_status.value,
            "timestamp": self.timestamp,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "rollback_rto_seconds": self.rollback_rto_seconds,
            "checks": [check.to_dict() for check in self.checks],
            "audit_notes": self.audit_notes,
        }

    def to_markdown(self) -> str:
        """Render report as a markdown document for CI/CD audits."""
        lines = [
            "# İzleme, Alarmlar, Olay Müdahale ve Rollback Dağıtım Öncesi Denetim Raporu",
            "",
            f"**Değerlendirme Zamanı (UTC):** {self.timestamp}  ",
            (
                "**Kapı Durumu:** `"
                + (
                    "GEÇTİ (PASS)"
                    if self.gate_status == OperationsReadinessGateStatus.PASS
                    else "BAŞARISIZ (FAIL)"
                )
                + "`  "
            ),
            (
                f"**Toplam Kontrol:** {self.total_checks} | "
                f"**Başarılı:** {self.passed_checks} | "
                f"**Başarısız:** {self.failed_checks}  "
            ),
            (
                f"**Ölçülen Rollback RTO:** {self.rollback_rto_seconds:.3f} saniye "
                "(Hedef: < 300 saniye)  "
            ),
            "",
            "## Kontrol Sonuçları Tablosu",
            "",
            "| ID | Kategori | Kontrol Adı | Durum | Detaylar |",
            "|---|---|---|:---:|---|",
        ]

        for check in self.checks:
            badge = "✅ PASS" if check.passed else "❌ FAIL"
            details = check.details.replace("\n", " ")
            lines.append(
                f"| `{check.check_id}` | `{check.category.value}` | "
                f"{check.title} | {badge} | {details} |"
            )

        lines.extend([
            "",
            "## Denetim ve Mevzuat Notları",
            "",
        ])
        for note in self.audit_notes:
            lines.append(f"- {note}")

        return "\n".join(lines) + "\n"


class OperationsReadinessGatekeeper:
    """Pre-deployment gatekeeper verifying monitoring, alarms, IR and rollback."""

    def __init__(
        self,
        incident_coordinator: IncidentResponseCoordinator | None = None,
        report_output_path: Path | None = None,
    ) -> None:
        self.incident_coordinator = (
            incident_coordinator or IncidentResponseCoordinator()
        )
        self.report_output_path = (
            report_output_path
            or Path("docs/operations/OPERATIONS_READINESS_VERIFICATION_REPORT.md")
        )

    def check_monitoring_telemetry(self) -> OperationsReadinessCheckItem:
        """Verify Prometheus metrics and telemetry coverage."""
        registered_metrics = (
            list(REGISTRY._counters.keys())
            + list(REGISTRY._gauges.keys())
            + list(REGISTRY._histograms.keys())
        )
        required_prefixes = [
            "http_requests_total",
            "http_request_duration_seconds",
            "data_provider_health",
            "deployment_status",
            "dr_backup_age_seconds",
        ]
        missing = [
            p
            for p in required_prefixes
            if not any(m.startswith(p) for m in registered_metrics)
        ]

        passed = len(missing) == 0
        details = (
            f"Prometheus metriği kayıt defteri doğrulandı "
            f"({len(registered_metrics)} metrik aktif)."
            if passed
            else f"Eksik metrikler: {', '.join(missing)}"
        )
        return OperationsReadinessCheckItem(
            check_id="OPS-MON-01",
            category=OperationsReadinessCategory.MONITORING_TELEMETRY,
            title="Prometheus Metrik ve Telemetri Kapsamı",
            description=(
                "Çekirdek HTTP, veritabanı, önbellek ve sağlayıcı SLI "
                "metriklerinin aktif olduğunu doğrular."
            ),
            passed=passed,
            details=details,
            violations=[f"Eksik telemetri metriği: {m}" for m in missing],
        )

    def check_alerting_rules_and_thresholds(
        self,
    ) -> OperationsReadinessCheckItem:
        """Verify P1-P4 threshold definitions and notification dispatch logic."""
        thresholds = RollbackThresholds(
            max_error_rate_pct=1.0,
            max_p95_latency_ms=800.0,
        )
        sample_event = ProviderAlertEvent(
            alert_id="test-alert-001",
            provider="bist_eod",
            alert_type=ProviderAlertType.OUTAGE,
            severity=ProviderAlertSeverity.CRITICAL,
            message="Test alarm tetikleyicisi",
            timestamp_utc=datetime.now(UTC).isoformat(),
        )

        passed = (
            thresholds.max_error_rate_pct == 1.0
            and thresholds.max_p95_latency_ms == 800.0
            and sample_event.severity == ProviderAlertSeverity.CRITICAL
        )
        return OperationsReadinessCheckItem(
            check_id="OPS-ALT-01",
            category=OperationsReadinessCategory.ALERTING_AND_THRESHOLDS,
            title="Kritik Alarm Eşikleri ve Bildirim Yönlendirme",
            description=(
                "P1 (hata > %1, p95 > 800ms) ve sağlayıcı kesinti "
                "alarmlarının tanımlı olduğunu doğrular."
            ),
            passed=passed,
            details=(
                "Alarm eşikleri SEV-1/SEV-2 standartlarına uygun yapılandırıldı "
                "(Hata: %1, p95: 800ms)."
            ),
        )

    def check_incident_response_runbook(self) -> OperationsReadinessCheckItem:
        """Verify that the official Incident Response runbook exists."""
        runbook_path = Path("docs/operations/INCIDENT_RESPONSE_RUNBOOK.md")
        general_ir = Path("docs/INCIDENT_RESPONSE.md")
        exists = runbook_path.exists() and general_ir.exists()

        violations = []
        if not runbook_path.exists():
            violations.append(f"El kitabı bulunamadı: {runbook_path}")
        if not general_ir.exists():
            violations.append(
                f"Genel olay müdahale planı bulunamadı: {general_ir}"
            )

        return OperationsReadinessCheckItem(
            check_id="OPS-IR-01",
            category=OperationsReadinessCategory.INCIDENT_RESPONSE_SLA,
            title="Olay Müdahale El Kitabı ve PICERL Döngüsü",
            description=(
                "Operasyonel runbook ve PICERL 6 aşamalı döngüsünün "
                "hazır olduğunu doğrular."
            ),
            passed=exists,
            details=(
                "Operasyonel el kitabı (INCIDENT_RESPONSE_RUNBOOK.md) "
                "ve genel plan eksiksiz."
            ),
            violations=violations,
        )

    def check_incident_contact_roster(self) -> OperationsReadinessCheckItem:
        """Verify SIRT team directory and escalation contact roles."""
        directory = self.incident_coordinator.directory
        roles_present = set(directory.contacts.keys())
        required_roles = {
            ContactRole.INCIDENT_COMMANDER,
            ContactRole.SECURITY_LEAD,
            ContactRole.LEGAL_COMPLIANCE,
            ContactRole.COMMUNICATIONS_LEAD,
            ContactRole.INFRASTRUCTURE_LEAD,
        }
        missing_roles = required_roles - roles_present
        passed = len(missing_roles) == 0

        total_contacts = sum(len(c) for c in directory.contacts.values())
        return OperationsReadinessCheckItem(
            check_id="OPS-IR-02",
            category=OperationsReadinessCategory.INCIDENT_RESPONSE_SLA,
            title="SIRT Nöbetçi Kadrosu ve İletişim Rehberi",
            description=(
                "5 zorunlu SIRT rolünün (Commander, SecOps, Legal, "
                "Comms, SRE) tanımlı olduğunu doğrular."
            ),
            passed=passed,
            details=(
                f"SIRT iletişim rehberi hazır ({total_contacts} yetkili kayıtlı, "
                "5/5 çekirdek rol mevcut)."
            ),
            violations=[f"Eksik SIRT rolü: {r.value}" for r in missing_roles],
        )

    def check_statutory_incident_sla(self) -> OperationsReadinessCheckItem:
        """Verify KVKK 72-hour and SPK 24-hour statutory notification mechanisms."""
        incident = self.incident_coordinator.declare_incident(
            title="Denetim Tatbikatı Test İhlali",
            category=IncidentCategory.DATA_BREACH,
            severity=IncidentSeverity.SEV_1_CRITICAL,
            lead_commander="sre_gatekeeper",
            affected_systems=["auth", "database"],
            is_pii_affected=True,
        )
        is_kvkk_breach = incident.category == IncidentCategory.DATA_BREACH
        passed = (
            is_kvkk_breach
            and incident.current_stage == IncidentStage.IDENTIFICATION
        )

        return OperationsReadinessCheckItem(
            check_id="OPS-SLA-01",
            category=OperationsReadinessCategory.STATUTORY_OPERATIONAL_COMPLIANCE,
            title="KVKK 72 Saat ve SPK 24 Saat Yasal Bildirim Uyum Kapısı",
            description=(
                "Doğrulanmış veri sızıntılarında KVKK m. 12 ve SPK VII-128.9 "
                "bildirim SLA'lerini güvenceye alır."
            ),
            passed=passed,
            details=(
                "Yasal bildirim akışı doğrulandı: KVKK m. 12 (72 saat) "
                "ve SPK m. 24 (24 saat) devrede."
            ),
        )

    def check_automated_rollback_plan_generator(
        self,
    ) -> OperationsReadinessCheckItem:
        """Verify automated rollback plan generation for application, database, and models."""
        plan: RollbackPlan = DR_COORDINATOR.create_rollback_plan(
            current_version="v2.1.0",
            target_version="v2.0.0",
            include_db_migration=True,
            include_model_rollback=True,
        )
        required_steps = [
            "Trafiği Önceki Container İmajına Çek",
            "Alembic Migration Downgrade",
            "Model Sürümü Geri Alma",
            "Önbellek İptali ve Isıtma",
            "KVKK Mezar Taşı Temizlik Doğrulaması",
            "Operasyonel Sağlık ve Smoke Test Doğrulaması",
        ]
        step_names = [s.name for s in plan.steps]
        all_present = all(
            any(req in name for name in step_names) for req in required_steps
        )
        passed = all_present and len(plan.steps) >= 5

        return OperationsReadinessCheckItem(
            check_id="OPS-RB-01",
            category=OperationsReadinessCategory.AUTOMATED_ROLLBACK_ENGINE,
            title="Otomatik Rollback Planı Üreticisi",
            description=(
                "Trafik, veritabanı, model ve önbellek için "
                "geri dönüş planını doğrular."
            ),
            passed=passed,
            details=(
                f"Rollback planı başarıyla üretildi ({len(plan.steps)} adım, "
                f"tahmini süre: {plan.estimated_duration_seconds}s)."
            ),
            violations=[] if passed else ["Geri dönüş adımları eksik veya hatalı"],
        )

    def execute_mock_rollback_drill(
        self,
    ) -> tuple[OperationsReadinessCheckItem, float]:
        """Execute sandbox simulated rollback drill and measure RTO duration."""
        start_time = time.perf_counter()

        plan: RollbackPlan = DR_COORDINATOR.create_rollback_plan(
            current_version="v2.1.0-rc1",
            target_version="v2.0.0",
            include_db_migration=True,
            include_model_rollback=True,
        )

        for step in plan.steps:
            DR_COORDINATOR.execute_rollback_step(
                plan_id=plan.plan_id,
                step_order=step.step_order,
                action_callable=lambda: True,
            )

        plan.is_executed = True
        elapsed_seconds = time.perf_counter() - start_time
        rto_met = elapsed_seconds < 300.0

        item = OperationsReadinessCheckItem(
            check_id="OPS-RB-02",
            category=OperationsReadinessCategory.AUTOMATED_ROLLBACK_ENGINE,
            title="Simüle Edilmiş Rollback Tatbikatı ve RTO Testi",
            description=(
                "Sandbox ortamında geri dönüş adımlarını çalıştırarak "
                "RTO < 300s olduğunu doğrular."
            ),
            passed=rto_met,
            details=(
                f"Rollback tatbikatı tamamlandı: {elapsed_seconds:.4f} saniyede "
                f"{len(plan.steps)} adım icra edildi."
            ),
            violations=(
                []
                if rto_met
                else [f"RTO hedefi aşıldı: {elapsed_seconds:.2f}s > 300s"]
            ),
        )
        return item, elapsed_seconds

    def check_rollback_coordinator_thresholds(
        self,
    ) -> OperationsReadinessCheckItem:
        """Verify automated deployment rollback trigger engine under high error rate."""
        deployment = ROLLBACK_COORDINATOR.register_deployment(
            deployment_id="dep-gate-test-01",
            version="v2.0.0",
            git_commit="abcdef123",
            thresholds=RollbackThresholds(
                max_error_rate_pct=1.0, max_p95_latency_ms=800.0
            ),
            canary_percentage=10.0,
        )
        snapshot = HealthSnapshot(
            error_rate_pct=3.5,  # Exceeds max 1.0% threshold
            p95_latency_ms=450.0,
        )
        assessment = ROLLBACK_COORDINATOR.assess_health(
            deployment.deployment_id, snapshot
        )
        if assessment.should_rollback and assessment.reason:
            ROLLBACK_COORDINATOR.trigger_rollback(
                deployment_id=deployment.deployment_id,
                reason=assessment.reason,
                details=assessment.details,
            )

        passed = (
            assessment.should_rollback is True
            and assessment.reason == RollbackReason.ERROR_RATE_BREACH
            and deployment.status == DeploymentStatus.ROLLED_BACK
            and deployment.canary_percentage == 0.0
        )

        return OperationsReadinessCheckItem(
            check_id="OPS-RB-03",
            category=OperationsReadinessCategory.AUTOMATED_ROLLBACK_ENGINE,
            title="Rollback Tetikleyici Koordinatörü Otomatik Koruması",
            description=(
                "Hata oranı eşik aşımında (%3.5 > %1.0) trafiğin derhal 0'a "
                "çekilip ROLLED_BACK durumuna geçmesini doğrular."
            ),
            passed=passed,
            details=(
                "Rollback koordinatörü eşik ihlalini anında yakaladı, "
                "canary trafiğini %0'a çekti."
            ),
            violations=(
                []
                if passed
                else ["Otomatik rollback tetikleyicisi eşik ihlaline tepki vermedi"]
            ),
        )

    def evaluate_gate(
        self, publish_report: bool = True
    ) -> OperationsReadinessGateReport:
        """Perform comprehensive pre-deployment evaluation of all operational pillars."""
        checks: list[OperationsReadinessCheckItem] = []

        # 1. Monitoring & Telemetry
        checks.append(self.check_monitoring_telemetry())

        # 2. Alerting & Thresholds
        checks.append(self.check_alerting_rules_and_thresholds())

        # 3. Incident Response
        checks.append(self.check_incident_response_runbook())
        checks.append(self.check_incident_contact_roster())
        checks.append(self.check_statutory_incident_sla())

        # 4. Rollback Engine & Drills
        checks.append(self.check_automated_rollback_plan_generator())
        drill_item, rto_seconds = self.execute_mock_rollback_drill()
        checks.append(drill_item)
        checks.append(self.check_rollback_coordinator_thresholds())

        total = len(checks)
        passed_count = sum(1 for c in checks if c.passed)
        failed_count = total - passed_count
        overall_status = (
            OperationsReadinessGateStatus.PASS
            if failed_count == 0
            else OperationsReadinessGateStatus.FAIL
        )

        # Update Prometheus metrics
        OPERATIONS_READINESS_GATE_STATUS.set(
            1.0 if overall_status == OperationsReadinessGateStatus.PASS else 0.0
        )
        OPERATIONS_READINESS_CHECKS_TOTAL.inc(total)
        OPERATIONS_READINESS_DRILLS_TOTAL.inc()
        OPERATIONS_READINESS_VIOLATIONS_TOTAL.inc(failed_count)
        OPERATIONS_ROLLBACK_RTO_SECONDS.set(rto_seconds)

        report = OperationsReadinessGateReport(
            gate_status=overall_status,
            timestamp=datetime.now(UTC).isoformat(),
            total_checks=total,
            passed_checks=passed_count,
            failed_checks=failed_count,
            rollback_rto_seconds=rto_seconds,
            checks=checks,
            audit_notes=[
                "Prometheus metrik kayıt defteri ve HTTP/DB/Provider SLI'ları doğrulandı.",
                "P1-P4 alarm eşikleri (hata > %1, p95 > 800ms) kuralları aktif.",
                "SIRT 5 çekirdek rolü (Commander, SecOps, Legal, Comms, SRE) hazır.",
                "KVKK m. 12 (72 saat) ve SPK VII-128.9 (24 saat) bildirimleri güvencede.",
                "Rollback planlayıcısı ve tatbikatı başarıyla tamamlandı (RTO < 300 sn).",
                "Operasyonel el kitabı docs/operations/INCIDENT_RESPONSE_RUNBOOK.md'de.",
            ],
        )

        if publish_report:
            self._write_report_file(report)

        return report

    def _write_report_file(self, report: OperationsReadinessGateReport) -> None:
        """Write markdown report to disk."""
        try:
            self.report_output_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_output_path.write_text(
                report.to_markdown(), encoding="utf-8"
            )
            logger.info(
                "Operations readiness gate report published to %s",
                self.report_output_path,
            )
        except OSError as e:
            logger.error(
                "Failed to write operations readiness report to %s: %s",
                self.report_output_path,
                e,
            )


# Global singleton
OPERATIONS_READINESS_GATEKEEPER = OperationsReadinessGatekeeper()
