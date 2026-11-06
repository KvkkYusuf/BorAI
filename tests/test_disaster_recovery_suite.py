"""Tests for disaster recovery, RPO/RTO compliance, and rollback plan engine."""

import datetime

import pytest

from stock_analysis.observability.disaster_recovery import (
    DEFAULT_RPO_TARGETS,
    DEFAULT_RTO_TARGETS,
    BackupSnapshotRecord,
    BackupStatus,
    DisasterRecoveryCoordinator,
    DrillStatus,
    RollbackStepStatus,
    SubsystemType,
)
from stock_analysis.observability.metrics import (
    DR_BACKUP_AGE_SECONDS,
    DR_READINESS_SCORE,
    DR_RPO_TARGET_SECONDS,
    DR_RTO_TARGET_SECONDS,
)


@pytest.fixture
def dr_coordinator() -> DisasterRecoveryCoordinator:
    """Provide a fresh DisasterRecoveryCoordinator for each test."""
    return DisasterRecoveryCoordinator()


def test_default_targets_match_spec(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Verify default RPO and RTO targets match system specifications."""
    assert DEFAULT_RPO_TARGETS[SubsystemType.DATABASE].max_age_seconds == 3600  # 1 hour
    assert DEFAULT_RTO_TARGETS[SubsystemType.DATABASE].max_recovery_seconds == 1800  # 30 min

    assert DEFAULT_RPO_TARGETS[SubsystemType.MARKET_DATA].max_age_seconds == 86400  # 24 hours
    assert DEFAULT_RTO_TARGETS[SubsystemType.MARKET_DATA].max_recovery_seconds == 3600  # 60 min

    assert DEFAULT_RPO_TARGETS[SubsystemType.MODEL_REGISTRY].max_age_seconds == 0
    assert DEFAULT_RTO_TARGETS[SubsystemType.MODEL_REGISTRY].max_recovery_seconds == 900  # 15 min


def test_register_backup_snapshot_success(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test successful registration and retrieval of a backup snapshot."""
    created_at = datetime.datetime.now(datetime.UTC)
    snap = dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.DATABASE,
        created_at=created_at,
        size_bytes=1048576,
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        is_encrypted=True,
        storage_location="s3://backup-vault/db-2026-10-28.enc",
    )

    assert isinstance(snap, BackupSnapshotRecord)
    assert snap.subsystem == SubsystemType.DATABASE
    assert snap.is_encrypted is True
    assert snap.status == BackupStatus.VERIFIED


def test_register_backup_snapshot_invalid_inputs(
    dr_coordinator: DisasterRecoveryCoordinator,
) -> None:
    """Test that invalid checksums or negative size raise validation errors."""
    now = datetime.datetime.now(datetime.UTC)

    with pytest.raises(ValueError, match="checksum"):
        dr_coordinator.register_backup_snapshot(
            subsystem=SubsystemType.DATABASE,
            created_at=now,
            size_bytes=100,
            checksum="",
            is_encrypted=True,
            storage_location="loc",
        )

    with pytest.raises(ValueError, match="size_bytes"):
        dr_coordinator.register_backup_snapshot(
            subsystem=SubsystemType.DATABASE,
            created_at=now,
            size_bytes=-10,
            checksum="abcdef123456",
            is_encrypted=True,
            storage_location="loc",
        )


def test_verify_backup_integrity(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test cryptographic checksum verification."""
    now = datetime.datetime.now(datetime.UTC)
    snap = dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.DATABASE,
        created_at=now,
        size_bytes=5000,
        checksum="abcd1234ef567890",
        is_encrypted=True,
        storage_location="loc",
    )

    # Valid checksum match
    assert dr_coordinator.verify_backup_integrity(snap.snapshot_id, "abcd1234ef567890") is True

    # Tampered / mismatched checksum
    assert (
        dr_coordinator.verify_backup_integrity(snap.snapshot_id, "corrupted_or_different_hash")
        is False
    )
    assert snap.status == BackupStatus.CORRUPTED


def test_rpo_evaluation_and_breach_detection(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test detection of RPO SLA breaches when backup age exceeds target."""
    base_time = datetime.datetime(2026, 10, 28, 12, 0, 0, tzinfo=datetime.UTC)

    # Register backup created 2 hours before evaluation time (target is 1 hour = 3600s)
    backup_time = base_time - datetime.timedelta(hours=2)
    dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.DATABASE,
        created_at=backup_time,
        size_bytes=1024,
        checksum="validchecksum1234",
        is_encrypted=True,
        storage_location="loc",
    )

    report = dr_coordinator.evaluate_subsystem_readiness(
        SubsystemType.DATABASE,
        as_of=base_time,
    )
    assert report.rpo_breached is True
    assert report.last_backup_age_seconds == 7200.0
    assert report.readiness_score < 1.0


def test_rpo_compliance_healthy(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test healthy RPO compliance when backup is fresh."""
    base_time = datetime.datetime(2026, 10, 28, 12, 0, 0, tzinfo=datetime.UTC)

    # Register backup created 15 minutes before evaluation time
    backup_time = base_time - datetime.timedelta(minutes=15)
    dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.DATABASE,
        created_at=backup_time,
        size_bytes=1024,
        checksum="validchecksum1234",
        is_encrypted=True,
        storage_location="loc",
    )

    report = dr_coordinator.evaluate_subsystem_readiness(
        SubsystemType.DATABASE,
        as_of=base_time,
    )
    assert report.rpo_breached is False
    assert report.last_backup_age_seconds == 900.0


def test_rto_drill_evaluation_success(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test successful drill result where recovery duration satisfies RTO target."""
    # Target RTO for DB is 1800s; drill completes in 600s
    drill = dr_coordinator.record_drill_result(
        subsystem=SubsystemType.DATABASE,
        recovery_duration_seconds=600.0,
        integrity_verified=True,
        notes="Sandbox DB PITR drill passed successfully",
    )

    assert drill.rto_met is True
    assert drill.integrity_verified is True
    assert drill.status == DrillStatus.SUCCESS


def test_rto_drill_evaluation_breach(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test drill result where recovery duration exceeds RTO target."""
    # Target RTO for DB is 1800s; drill takes 2400s
    drill = dr_coordinator.record_drill_result(
        subsystem=SubsystemType.DATABASE,
        recovery_duration_seconds=2400.0,
        integrity_verified=True,
        notes="Network throttling delayed restore",
    )

    assert drill.rto_met is False
    assert drill.status == DrillStatus.RTO_BREACH


def test_rto_drill_integrity_failure(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test drill where integrity verification fails."""
    drill = dr_coordinator.record_drill_result(
        subsystem=SubsystemType.DATABASE,
        recovery_duration_seconds=300.0,
        integrity_verified=False,
        notes="Restored database checksum mismatch",
    )

    assert drill.integrity_verified is False
    assert drill.status == DrillStatus.INTEGRITY_FAILURE


def test_overall_readiness_report(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test system-wide disaster recovery report generation and recommendations."""
    now = datetime.datetime.now(datetime.UTC)

    # Fresh DB backup
    dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.DATABASE,
        created_at=now - datetime.timedelta(minutes=10),
        size_bytes=50000,
        checksum="hash12345678",
        is_encrypted=True,
        storage_location="s3://db",
    )
    # Fresh Market Data backup
    dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.MARKET_DATA,
        created_at=now - datetime.timedelta(hours=4),
        size_bytes=2000000,
        checksum="hash98765432",
        is_encrypted=True,
        storage_location="s3://market",
    )

    report = dr_coordinator.evaluate_overall_readiness(as_of=now)
    assert report.overall_readiness_score >= 0.8
    assert isinstance(report.subsystems, dict)
    assert SubsystemType.DATABASE in report.subsystems


def test_create_and_execute_rollback_plan(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test rollback plan creation and execution tracking."""
    plan = dr_coordinator.create_rollback_plan(
        current_version="v2.1.0",
        target_version="v2.0.4",
        include_db_migration=True,
        include_model_rollback=True,
    )

    assert plan.current_version == "v2.1.0"
    assert plan.target_version == "v2.0.4"
    assert len(plan.steps) == 6
    assert plan.is_executed is False

    # Check step actions
    step_actions = [s.action_type for s in plan.steps]
    assert "traffic_switch" in step_actions
    assert "db_migration_downgrade" in step_actions
    assert "model_governance_revert" in step_actions
    assert "cache_invalidation" in step_actions
    assert "kvkk_post_restore_sanitization" in step_actions
    assert "health_verification" in step_actions

    # Execute step 1 successfully
    executed_step = dr_coordinator.execute_rollback_step(
        plan_id=plan.plan_id,
        step_order=1,
        action_callable=lambda: True,
    )
    assert executed_step.status == RollbackStepStatus.COMPLETED

    # Execute step 2 with failure
    failed_step = dr_coordinator.execute_rollback_step(
        plan_id=plan.plan_id,
        step_order=2,
        action_callable=lambda: False,
    )
    assert failed_step.status == RollbackStepStatus.FAILED
    assert failed_step.error_message is not None


def test_metrics_synchronization(dr_coordinator: DisasterRecoveryCoordinator) -> None:
    """Test syncing state to Prometheus gauges and counters."""
    now = datetime.datetime.now(datetime.UTC)
    dr_coordinator.register_backup_snapshot(
        subsystem=SubsystemType.DATABASE,
        created_at=now - datetime.timedelta(minutes=5),
        size_bytes=1000,
        checksum="samplecheck123",
        is_encrypted=True,
        storage_location="loc",
    )
    dr_coordinator.record_drill_result(
        subsystem=SubsystemType.DATABASE,
        recovery_duration_seconds=450.0,
        integrity_verified=True,
    )

    dr_coordinator.sync_metrics(as_of=now)

    db_labels = {"subsystem": "database"}
    assert DR_RPO_TARGET_SECONDS.get(db_labels) == 3600.0
    assert DR_RTO_TARGET_SECONDS.get(db_labels) == 1800.0
    assert DR_BACKUP_AGE_SECONDS.get(db_labels) >= 0.0
    assert DR_READINESS_SCORE.get() > 0.0
