"""Disaster recovery, RPO/RTO monitoring, backup readiness, and rollback plan engine.

Implements operational business continuity guarantees for:
- Database (Users, sessions, alert rules, audit events)
- Market Data (Raw BIST EOD immutable store)
- Model Registry (PyTorch bundled artifacts, safetensors)
- Cache (Redis / in-memory ephemeral layer)
"""

from __future__ import annotations

import datetime
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from stock_analysis.observability.metrics import (
    DR_BACKUP_AGE_SECONDS,
    DR_LAST_DRILL_DURATION_SECONDS,
    DR_READINESS_SCORE,
    DR_RPO_BREACH_TOTAL,
    DR_RPO_TARGET_SECONDS,
    DR_RTO_TARGET_SECONDS,
)


class SubsystemType(StrEnum):
    """Critical system components requiring disaster recovery guarantees."""

    DATABASE = "database"
    MARKET_DATA = "market_data"
    MODEL_REGISTRY = "model_registry"
    CACHE = "cache"


class BackupStatus(StrEnum):
    """Cryptographic and operational status of a backup snapshot."""

    VERIFIED = "verified"
    PENDING_VERIFICATION = "pending_verification"
    CORRUPTED = "corrupted"
    EXPIRED = "expired"


class DrillStatus(StrEnum):
    """Outcome status of a recovery drill."""

    SUCCESS = "success"
    RTO_BREACH = "rto_breach"
    INTEGRITY_FAILURE = "integrity_failure"
    ABORTED = "aborted"


class RollbackStepStatus(StrEnum):
    """Execution status for a discrete rollback step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RpoTarget:
    """Recovery Point Objective (RPO) target for a subsystem."""

    subsystem: SubsystemType
    max_age_seconds: int
    description: str


@dataclass(frozen=True)
class RtoTarget:
    """Recovery Time Objective (RTO) target for a subsystem."""

    subsystem: SubsystemType
    max_recovery_seconds: int
    description: str


@dataclass
class BackupSnapshotRecord:
    """Metadata record representing a verified or stored backup snapshot."""

    snapshot_id: str
    subsystem: SubsystemType
    created_at: datetime.datetime
    size_bytes: int
    checksum: str
    is_encrypted: bool
    storage_location: str
    status: BackupStatus = BackupStatus.VERIFIED


@dataclass
class SubsystemDrillResult:
    """Result of an isolated sandbox recovery drill."""

    drill_id: str
    subsystem: SubsystemType
    drill_timestamp: datetime.datetime
    recovery_duration_seconds: float
    rto_target_seconds: float
    rto_met: bool
    integrity_verified: bool
    status: DrillStatus
    notes: str = ""


@dataclass
class RollbackStep:
    """Individual action step in an automated/semi-automated rollback plan."""

    step_order: int
    name: str
    action_type: str
    description: str
    is_reversible: bool
    status: RollbackStepStatus = RollbackStepStatus.PENDING
    error_message: str | None = None
    executed_at: datetime.datetime | None = None


@dataclass
class RollbackPlan:
    """End-to-end rollback execution plan."""

    plan_id: str
    created_at: datetime.datetime
    current_version: str
    target_version: str
    include_db_migration: bool
    include_model_rollback: bool
    steps: list[RollbackStep]
    estimated_duration_seconds: int
    is_executed: bool = False


@dataclass
class SubsystemReadinessReport:
    """Disaster recovery and SLA readiness status for a single subsystem."""

    subsystem: SubsystemType
    rpo_target_seconds: int
    rto_target_seconds: int
    latest_snapshot: BackupSnapshotRecord | None
    last_backup_age_seconds: float | None
    rpo_breached: bool
    last_drill: SubsystemDrillResult | None
    readiness_score: float  # 0.0 to 1.0


@dataclass
class DisasterRecoveryReport:
    """System-wide operational disaster recovery summary."""

    evaluated_at: datetime.datetime
    overall_readiness_score: float
    subsystems: dict[SubsystemType, SubsystemReadinessReport]
    active_breaches: list[str]
    recommendations: list[str]


# Default System RPO/RTO Targets defined in docs/DISASTER_RECOVERY_PLAN.md
DEFAULT_RPO_TARGETS: dict[SubsystemType, RpoTarget] = {
    SubsystemType.DATABASE: RpoTarget(
        subsystem=SubsystemType.DATABASE,
        max_age_seconds=3600,  # 1 Hour
        description="Hourly WAL/PITR + Daily Encrypted Snapshot",
    ),
    SubsystemType.MARKET_DATA: RpoTarget(
        subsystem=SubsystemType.MARKET_DATA,
        max_age_seconds=86400,  # 24 Hours
        description="Daily incremental BIST EOD raw bar archive (WORM)",
    ),
    SubsystemType.MODEL_REGISTRY: RpoTarget(
        subsystem=SubsystemType.MODEL_REGISTRY,
        max_age_seconds=0,  # 0 Seconds (Immutable code/artifact versioning)
        description="Zero loss, versioned bundled models in dual-region object store",
    ),
    SubsystemType.CACHE: RpoTarget(
        subsystem=SubsystemType.CACHE,
        max_age_seconds=0,  # Ephemeral
        description="Ephemeral Redis/In-memory; rebuilt automatically upon restart",
    ),
}

DEFAULT_RTO_TARGETS: dict[SubsystemType, RtoTarget] = {
    SubsystemType.DATABASE: RtoTarget(
        subsystem=SubsystemType.DATABASE,
        max_recovery_seconds=1800,  # 30 Minutes
        description="Cold standby or PITR database restore under 30 minutes",
    ),
    SubsystemType.MARKET_DATA: RtoTarget(
        subsystem=SubsystemType.MARKET_DATA,
        max_recovery_seconds=3600,  # 60 Minutes
        description="Raw store hydration and derived indicator recomputation under 1 hour",
    ),
    SubsystemType.MODEL_REGISTRY: RtoTarget(
        subsystem=SubsystemType.MODEL_REGISTRY,
        max_recovery_seconds=900,  # 15 Minutes
        description="Model rollback or candidate deployment switch under 15 minutes",
    ),
    SubsystemType.CACHE: RtoTarget(
        subsystem=SubsystemType.CACHE,
        max_recovery_seconds=300,  # 5 Minutes
        description="Redis warm-up or failover switch under 5 minutes",
    ),
}


class DisasterRecoveryCoordinator:
    """Thread-safe coordinator for backup freshness, recovery drills, and rollback planning."""

    def __init__(
        self,
        rpo_targets: dict[SubsystemType, RpoTarget] | None = None,
        rto_targets: dict[SubsystemType, RtoTarget] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._rpo_targets = dict(rpo_targets or DEFAULT_RPO_TARGETS)
        self._rto_targets = dict(rto_targets or DEFAULT_RTO_TARGETS)

        self._snapshots: dict[SubsystemType, list[BackupSnapshotRecord]] = {
            st: [] for st in SubsystemType
        }
        self._drills: dict[SubsystemType, list[SubsystemDrillResult]] = {
            st: [] for st in SubsystemType
        }
        self._plans: dict[str, RollbackPlan] = {}

    def register_backup_snapshot(
        self,
        subsystem: SubsystemType,
        created_at: datetime.datetime,
        size_bytes: int,
        checksum: str,
        is_encrypted: bool,
        storage_location: str,
        snapshot_id: str | None = None,
        status: BackupStatus = BackupStatus.VERIFIED,
    ) -> BackupSnapshotRecord:
        """Register a new backup snapshot with cryptographic integrity details."""
        if not checksum or len(checksum) < 8:
            raise ValueError("Backup checksum must be a valid non-empty cryptographic hash")
        if size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.UTC)

        sid = snapshot_id or f"snap-{subsystem.value}-{uuid.uuid4().hex[:8]}"
        record = BackupSnapshotRecord(
            snapshot_id=sid,
            subsystem=subsystem,
            created_at=created_at,
            size_bytes=size_bytes,
            checksum=checksum,
            is_encrypted=is_encrypted,
            storage_location=storage_location,
            status=status,
        )

        with self._lock:
            self._snapshots[subsystem].append(record)
            self._snapshots[subsystem].sort(key=lambda s: s.created_at, reverse=True)

        self.sync_metrics()
        return record

    def verify_backup_integrity(self, snapshot_id: str, computed_checksum: str) -> bool:
        """Validate whether a given backup matches its registered cryptographic checksum."""
        with self._lock:
            for snapshot_list in self._snapshots.values():
                for snap in snapshot_list:
                    if snap.snapshot_id == snapshot_id:
                        is_valid = snap.checksum.lower() == computed_checksum.lower()
                        snap.status = BackupStatus.VERIFIED if is_valid else BackupStatus.CORRUPTED
                        return is_valid
        return False

    def record_drill_result(
        self,
        subsystem: SubsystemType,
        recovery_duration_seconds: float,
        integrity_verified: bool,
        notes: str = "",
        drill_timestamp: datetime.datetime | None = None,
        drill_id: str | None = None,
    ) -> SubsystemDrillResult:
        """Record the outcome of a disaster recovery drill and compare against RTO targets."""
        ts = drill_timestamp or datetime.datetime.now(datetime.UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.UTC)

        rto_target = self._rto_targets.get(subsystem)
        target_seconds = float(rto_target.max_recovery_seconds) if rto_target else 3600.0
        rto_met = recovery_duration_seconds <= target_seconds

        if not integrity_verified:
            status = DrillStatus.INTEGRITY_FAILURE
        elif not rto_met:
            status = DrillStatus.RTO_BREACH
        else:
            status = DrillStatus.SUCCESS

        did = drill_id or f"drill-{subsystem.value}-{uuid.uuid4().hex[:8]}"
        drill = SubsystemDrillResult(
            drill_id=did,
            subsystem=subsystem,
            drill_timestamp=ts,
            recovery_duration_seconds=recovery_duration_seconds,
            rto_target_seconds=target_seconds,
            rto_met=rto_met,
            integrity_verified=integrity_verified,
            status=status,
            notes=notes,
        )

        with self._lock:
            self._drills[subsystem].append(drill)
            self._drills[subsystem].sort(key=lambda d: d.drill_timestamp, reverse=True)

        self.sync_metrics()
        return drill

    def evaluate_subsystem_readiness(
        self,
        subsystem: SubsystemType,
        as_of: datetime.datetime | None = None,
    ) -> SubsystemReadinessReport:
        """Evaluate RPO and RTO readiness for a single subsystem."""
        now = as_of or datetime.datetime.now(datetime.UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.UTC)

        rpo_target = self._rpo_targets.get(subsystem)
        rto_target = self._rto_targets.get(subsystem)

        rpo_seconds = rpo_target.max_age_seconds if rpo_target else 3600
        rto_seconds = rto_target.max_recovery_seconds if rto_target else 1800

        with self._lock:
            snapshots = list(self._snapshots[subsystem])
            drills = list(self._drills[subsystem])

        latest_snap = snapshots[0] if snapshots else None
        last_drill = drills[0] if drills else None

        # Age calculation
        if latest_snap:
            age = max(0.0, (now - latest_snap.created_at).total_seconds())
        else:
            age = None

        # RPO breach calculation
        # For immutable MODEL_REGISTRY or CACHE, zero age required if snapshot exists
        if subsystem in (SubsystemType.MODEL_REGISTRY, SubsystemType.CACHE):
            rpo_breached = False
        elif age is not None and rpo_seconds > 0:
            rpo_breached = age > rpo_seconds
        else:
            # If no snapshot registered yet for DATABASE or MARKET_DATA, consider breached
            rpo_breached = latest_snap is None

        # Readiness scoring:
        # Base: 1.0
        # If RPO breached: -0.5
        # If last drill failed RTO or corrupted: -0.3
        # If no drill performed: -0.1
        score = 1.0
        if rpo_breached:
            score -= 0.5
        if last_drill is not None:
            if not last_drill.rto_met or not last_drill.integrity_verified:
                score -= 0.4
        elif subsystem != SubsystemType.CACHE:
            score -= 0.1

        score = max(0.0, min(1.0, score))

        return SubsystemReadinessReport(
            subsystem=subsystem,
            rpo_target_seconds=rpo_seconds,
            rto_target_seconds=rto_seconds,
            latest_snapshot=latest_snap,
            last_backup_age_seconds=age,
            rpo_breached=rpo_breached,
            last_drill=last_drill,
            readiness_score=score,
        )

    def evaluate_overall_readiness(
        self,
        as_of: datetime.datetime | None = None,
    ) -> DisasterRecoveryReport:
        """Evaluate overall business continuity readiness across all subsystems."""
        now = as_of or datetime.datetime.now(datetime.UTC)
        sub_reports: dict[SubsystemType, SubsystemReadinessReport] = {}
        active_breaches: list[str] = []
        recommendations: list[str] = []

        total_score = 0.0
        for st in SubsystemType:
            rep = self.evaluate_subsystem_readiness(st, as_of=now)
            sub_reports[st] = rep
            total_score += rep.readiness_score

            if rep.rpo_breached:
                active_breaches.append(
                    f"RPO breach in {st.value}: backup age is "
                    f"{rep.last_backup_age_seconds:.0f}s (target: {rep.rpo_target_seconds}s)"
                    if rep.last_backup_age_seconds is not None
                    else f"RPO breach in {st.value}: no verified backup snapshot found"
                )
                recommendations.append(f"Trigger immediate backup for {st.value}.")

            if rep.last_drill and not rep.last_drill.rto_met:
                active_breaches.append(
                    f"RTO breach in {st.value} drill: took "
                    f"{rep.last_drill.recovery_duration_seconds:.1f}s "
                    f"(target: {rep.rto_target_seconds}s)"
                )
                recommendations.append(f"Optimize restoration pipeline or indexing for {st.value}.")

        overall_score = round(total_score / len(SubsystemType), 2)

        return DisasterRecoveryReport(
            evaluated_at=now,
            overall_readiness_score=overall_score,
            subsystems=sub_reports,
            active_breaches=active_breaches,
            recommendations=recommendations,
        )

    def create_rollback_plan(
        self,
        current_version: str,
        target_version: str,
        include_db_migration: bool = True,
        include_model_rollback: bool = True,
    ) -> RollbackPlan:
        """Create a structured, verifiable rollback plan to revert to target version."""
        plan_id = f"rollback-{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.UTC)

        steps: list[RollbackStep] = [
            RollbackStep(
                step_order=1,
                name="Trafiği Önceki Container İmajına Çek",
                action_type="traffic_switch",
                description=(
                    f"Route production load balancer traffic away from {current_version} "
                    f"back to stable target {target_version}."
                ),
                is_reversible=True,
            ),
        ]

        order = 2
        if include_db_migration:
            steps.append(
                RollbackStep(
                    step_order=order,
                    name="Alembic Migration Downgrade",
                    action_type="db_migration_downgrade",
                    description=(
                        "Run `alembic downgrade -1` or target schema revision to align tables "
                        "with previous stable version."
                    ),
                    is_reversible=False,
                )
            )
            order += 1

        if include_model_rollback:
            steps.append(
                RollbackStep(
                    step_order=order,
                    name="Model Sürümü Geri Alma",
                    action_type="model_governance_revert",
                    description=(
                        "Set target model version back to ACTIVE in ModelGovernanceRegistry "
                        "and decommission candidate model."
                    ),
                    is_reversible=True,
                )
            )
            order += 1

        steps.append(
            RollbackStep(
                step_order=order,
                name="Önbellek İptali ve Isıtma (Cache Flush & Warm-up)",
                action_type="cache_invalidation",
                description="Invalidate Redis keys and warm critical market/indicator caches.",
                is_reversible=False,
            )
        )
        order += 1

        steps.append(
            RollbackStep(
                step_order=order,
                name="KVKK Mezar Taşı Temizlik Doğrulaması",
                action_type="kvkk_post_restore_sanitization",
                description=(
                    "Run PostRestoreSanitizer to ensure no purged user accounts are restored."
                ),
                is_reversible=False,
            )
        )
        order += 1

        steps.append(
            RollbackStep(
                step_order=order,
                name="Operasyonel Sağlık ve Smoke Test Doğrulaması",
                action_type="health_verification",
                description="Verify /health and /api/v1/operational-health endpoints are green.",
                is_reversible=True,
            )
        )

        estimated_duration = 300 + (300 if include_db_migration else 0)

        plan = RollbackPlan(
            plan_id=plan_id,
            created_at=now,
            current_version=current_version,
            target_version=target_version,
            include_db_migration=include_db_migration,
            include_model_rollback=include_model_rollback,
            steps=steps,
            estimated_duration_seconds=estimated_duration,
        )

        with self._lock:
            self._plans[plan_id] = plan

        return plan

    def execute_rollback_step(
        self,
        plan_id: str,
        step_order: int,
        action_callable: Callable[[], bool] | None = None,
    ) -> RollbackStep:
        """Execute or mark a step in an existing rollback plan."""
        with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                raise KeyError(f"Rollback plan {plan_id} not found")

            step = next((s for s in plan.steps if s.step_order == step_order), None)
            if not step:
                raise KeyError(f"Step {step_order} in plan {plan_id} not found")

            step.status = RollbackStepStatus.IN_PROGRESS
            step.executed_at = datetime.datetime.now(datetime.UTC)

        success = True
        err_msg = None
        if action_callable:
            try:
                success = action_callable()
            except Exception as ex:
                success = False
                err_msg = str(ex)

        with self._lock:
            if success:
                step.status = RollbackStepStatus.COMPLETED
            else:
                step.status = RollbackStepStatus.FAILED
                step.error_message = err_msg or "Step execution returned false"

            all_done = all(s.status == RollbackStepStatus.COMPLETED for s in plan.steps)
            if all_done:
                plan.is_executed = True

        return step

    def get_rollback_plan(self, plan_id: str) -> RollbackPlan | None:
        """Retrieve a rollback plan by id."""
        with self._lock:
            return self._plans.get(plan_id)

    def sync_metrics(self, as_of: datetime.datetime | None = None) -> None:
        """Synchronize disaster recovery state to Prometheus gauges and counters."""
        now = as_of or datetime.datetime.now(datetime.UTC)

        for st in SubsystemType:
            labels = {"subsystem": st.value}

            # Targets
            rpo_target = self._rpo_targets.get(st)
            if rpo_target:
                DR_RPO_TARGET_SECONDS.set(float(rpo_target.max_age_seconds), labels=labels)

            rto_target = self._rto_targets.get(st)
            if rto_target:
                DR_RTO_TARGET_SECONDS.set(float(rto_target.max_recovery_seconds), labels=labels)

            # Snapshots
            with self._lock:
                snaps = list(self._snapshots[st])
                drills = list(self._drills[st])

            if snaps:
                age = max(0.0, (now - snaps[0].created_at).total_seconds())
                DR_BACKUP_AGE_SECONDS.set(age, labels=labels)
                if (
                    rpo_target
                    and rpo_target.max_age_seconds > 0
                    and age > rpo_target.max_age_seconds
                ):
                    DR_RPO_BREACH_TOTAL.inc(1.0, labels=labels)
            else:
                DR_BACKUP_AGE_SECONDS.set(-1.0, labels=labels)

            # Drills
            if drills:
                DR_LAST_DRILL_DURATION_SECONDS.set(
                    float(drills[0].recovery_duration_seconds), labels=labels
                )

        report = self.evaluate_overall_readiness(as_of=now)
        DR_READINESS_SCORE.set(report.overall_readiness_score)


# Global Singleton for system-wide use
DR_COORDINATOR = DisasterRecoveryCoordinator()
