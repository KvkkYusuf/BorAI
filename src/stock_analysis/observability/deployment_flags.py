"""Production deployment rollback coordination and feature flag governance.

Provides:
- FeatureFlagRegistry: Deterministic canary percentage rollouts, user/role targeting,
  instant kill switches, and Prometheus metrics.
- RollbackTriggerCoordinator: Automated rollback triggers on metric breaches (error rate,
  p95 latency, health checks), progressive canary stages, and safe fallback activation.
"""

from __future__ import annotations

import collections
import datetime
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from stock_analysis.observability.metrics import (
    DEPLOYMENT_CANARY_WEIGHT_GAUGE,
    DEPLOYMENT_ROLLBACK_TRIGGERS_TOTAL,
    DEPLOYMENT_STATUS_GAUGE,
    FEATURE_FLAG_ACTIVE_GAUGE,
    FEATURE_FLAG_EVALUATIONS_TOTAL,
)


class FeatureFlagType(StrEnum):
    """Categorisation of feature flag behavior and evaluation mode."""

    BOOLEAN = "boolean"
    PERCENTAGE = "percentage"
    TARGETED = "targeted"
    MULTIVARIATE = "multivariate"


class EvaluationReason(StrEnum):
    """Reason code explaining why a feature flag evaluated to its given result."""

    KILL_SWITCH = "kill_switch"
    FLAG_DISABLED = "flag_disabled"
    USER_TARGETED = "user_targeted"
    ROLE_TARGETED = "role_targeted"
    PERCENTAGE_ROLLOUT_MATCH = "percentage_rollout_match"
    PERCENTAGE_ROLLOUT_MISMATCH = "percentage_rollout_mismatch"
    GLOBAL_ENABLED = "global_enabled"
    FLAG_NOT_FOUND = "flag_not_found"
    DEFAULT_VARIANT = "default_variant"
    VARIANT_MATCH = "variant_match"
    EVALUATION_ERROR = "evaluation_error"


class DeploymentStatus(StrEnum):
    """Lifecycle status of a production software deployment or canary rollout."""

    PENDING = "pending"
    CANARY = "canary"
    ACTIVE = "active"
    STABLE = "stable"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


_STATUS_NUMERIC_CODES: dict[DeploymentStatus, float] = {
    DeploymentStatus.PENDING: 0.0,
    DeploymentStatus.CANARY: 1.0,
    DeploymentStatus.ACTIVE: 2.0,
    DeploymentStatus.STABLE: 3.0,
    DeploymentStatus.ROLLING_BACK: 4.0,
    DeploymentStatus.ROLLED_BACK: 5.0,
    DeploymentStatus.FAILED: 6.0,
}


class RollbackReason(StrEnum):
    """Operational anomaly triggering an automated or manual rollback."""

    ERROR_RATE_BREACH = "error_rate_breach"
    LATENCY_P95_BREACH = "latency_p95_breach"
    HEALTH_CHECK_FAILURE = "health_check_failure"
    CRITICAL_EXCEPTION_SPIKE = "critical_exception_spike"
    MANUAL_OVERRIDE = "manual_override"


@dataclass
class EvaluationContext:
    """Runtime context passed to evaluate a feature flag for a request."""

    user_id: str | None = None
    roles: set[str] = field(default_factory=set)
    environment: str = "production"
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.roles, (list, tuple)):
            self.roles = set(self.roles)


@dataclass
class EvaluationResult:
    """Immutable outcome of evaluating a feature flag with full audit metadata."""

    flag_name: str
    is_enabled: bool
    value: Any
    variant: str | None
    reason: EvaluationReason
    evaluated_at: datetime.datetime
    context_summary: dict[str, Any]


@dataclass
class FeatureFlagDefinition:
    """Schema and configuration governing a single feature flag."""

    name: str
    flag_type: FeatureFlagType = FeatureFlagType.BOOLEAN
    enabled: bool = True
    percentage: float = 100.0
    target_users: set[str] = field(default_factory=set)
    target_roles: set[str] = field(default_factory=set)
    variants: dict[str, Any] = field(default_factory=dict)
    default_variant: str | None = None
    kill_switched: bool = False
    fallback_value: Any = False
    description: str = ""
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    updated_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    def __post_init__(self) -> None:
        if self.percentage < 0.0 or self.percentage > 100.0:
            raise ValueError(
                f"Feature flag percentage must be between 0.0 and 100.0, got {self.percentage}"
            )
        if isinstance(self.target_users, (list, tuple)):
            self.target_users = set(self.target_users)
        if isinstance(self.target_roles, (list, tuple)):
            self.target_roles = set(self.target_roles)


@dataclass(frozen=True)
class RollbackThresholds:
    """SLO and operational breach thresholds for automatic rollback triggers."""

    max_error_rate_pct: float = 2.0  # > 2% HTTP 5xx triggers rollback
    max_p95_latency_ms: float = 800.0  # > 800ms p95 latency triggers rollback
    max_consecutive_health_failures: int = 3
    max_critical_errors: int = 5
    evaluation_window_seconds: int = 300


@dataclass
class HealthSnapshot:
    """Point-in-time telemetry snapshot evaluated against rollback thresholds."""

    error_rate_pct: float
    p95_latency_ms: float
    consecutive_health_failures: int = 0
    critical_errors_count: int = 0
    recorded_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


@dataclass
class RollbackAssessment:
    """Decision outcome of assessing system health against rollback thresholds."""

    should_rollback: bool
    reason: RollbackReason | None
    details: str
    breached_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeploymentRecord:
    """Historical and operational state record for a software deployment."""

    deployment_id: str
    version: str
    previous_version: str
    git_commit: str
    status: DeploymentStatus
    canary_percentage: float
    thresholds: RollbackThresholds
    started_at: datetime.datetime
    updated_at: datetime.datetime
    associated_flags: list[str] = field(default_factory=list)
    rollback_reason: RollbackReason | None = None
    rollback_details: str | None = None
    rollback_at: datetime.datetime | None = None


class FeatureFlagRegistry:
    """Thread-safe registry for feature flags with deterministic canary rollout support."""

    def __init__(self, max_audit_entries: int = 1000) -> None:
        self._lock = threading.Lock()
        self._flags: dict[str, FeatureFlagDefinition] = {}
        self._audit_history: collections.deque[EvaluationResult] = collections.deque(
            maxlen=max_audit_entries
        )

    def register_flag(self, flag: FeatureFlagDefinition) -> None:
        """Register or replace a feature flag definition."""
        with self._lock:
            flag.updated_at = datetime.datetime.now(datetime.UTC)
            self._flags[flag.name] = flag
            is_active = 1.0 if (flag.enabled and not flag.kill_switched) else 0.0
            FEATURE_FLAG_ACTIVE_GAUGE.set(is_active, labels={"flag_name": flag.name})

    def get_flag(self, name: str) -> FeatureFlagDefinition | None:
        """Retrieve a registered flag definition by name."""
        with self._lock:
            return self._flags.get(name)

    def set_kill_switch(self, name: str, kill: bool) -> bool:
        """Instantly kill-switch a flag (or restore it) across all evaluations."""
        with self._lock:
            flag = self._flags.get(name)
            if not flag:
                return False
            flag.kill_switched = kill
            flag.updated_at = datetime.datetime.now(datetime.UTC)
            is_active = 1.0 if (flag.enabled and not flag.kill_switched) else 0.0
            FEATURE_FLAG_ACTIVE_GAUGE.set(is_active, labels={"flag_name": name})
            return True

    def set_percentage(self, name: str, percentage: float) -> bool:
        """Update the rollout percentage for canary advancement."""
        if percentage < 0.0 or percentage > 100.0:
            raise ValueError(f"Percentage must be in [0.0, 100.0], got {percentage}")
        with self._lock:
            flag = self._flags.get(name)
            if not flag:
                return False
            flag.percentage = percentage
            flag.updated_at = datetime.datetime.now(datetime.UTC)
            return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Toggle master enabled state of a feature flag."""
        with self._lock:
            flag = self._flags.get(name)
            if not flag:
                return False
            flag.enabled = enabled
            flag.updated_at = datetime.datetime.now(datetime.UTC)
            is_active = 1.0 if (flag.enabled and not flag.kill_switched) else 0.0
            FEATURE_FLAG_ACTIVE_GAUGE.set(is_active, labels={"flag_name": name})
            return True

    def _hash_to_percentage(self, flag_name: str, identifier: str) -> float:
        """Calculate a deterministic bucket percentage [0.0, 100.0) from identifier."""
        data = f"{flag_name}:{identifier}".encode()
        digest = hashlib.sha256(data).hexdigest()
        # Take first 8 chars for 32-bit unsigned int
        val = int(digest[:8], 16)
        return float(val % 10000) / 100.0

    def evaluate(self, name: str, context: EvaluationContext | None = None) -> EvaluationResult:
        """Evaluate a feature flag against runtime context and record audit/metrics."""
        now = datetime.datetime.now(datetime.UTC)
        ctx = context or EvaluationContext()
        ctx_summary = {
            "user_id": ctx.user_id,
            "roles": list(ctx.roles),
            "environment": ctx.environment,
        }

        with self._lock:
            flag = self._flags.get(name)

        if not flag:
            res = EvaluationResult(
                flag_name=name,
                is_enabled=False,
                value=False,
                variant=None,
                reason=EvaluationReason.FLAG_NOT_FOUND,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # 1. Kill Switch Check (immediate override)
        if flag.kill_switched:
            res = EvaluationResult(
                flag_name=name,
                is_enabled=False,
                value=flag.fallback_value,
                variant=flag.default_variant,
                reason=EvaluationReason.KILL_SWITCH,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # 2. Master Enabled Check
        if not flag.enabled:
            res = EvaluationResult(
                flag_name=name,
                is_enabled=False,
                value=flag.fallback_value,
                variant=flag.default_variant,
                reason=EvaluationReason.FLAG_DISABLED,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # 3. Targeted Users Check
        if ctx.user_id and ctx.user_id in flag.target_users:
            val = flag.variants.get("enabled", True) if flag.variants else True
            res = EvaluationResult(
                flag_name=name,
                is_enabled=True,
                value=val,
                variant="enabled",
                reason=EvaluationReason.USER_TARGETED,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # 4. Targeted Roles Check
        if ctx.roles and flag.target_roles and (ctx.roles & flag.target_roles):
            val = flag.variants.get("enabled", True) if flag.variants else True
            res = EvaluationResult(
                flag_name=name,
                is_enabled=True,
                value=val,
                variant="enabled",
                reason=EvaluationReason.ROLE_TARGETED,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # If flag is TARGETED only and neither matched
        if flag.flag_type == FeatureFlagType.TARGETED:
            res = EvaluationResult(
                flag_name=name,
                is_enabled=False,
                value=flag.fallback_value,
                variant=flag.default_variant,
                reason=EvaluationReason.PERCENTAGE_ROLLOUT_MISMATCH,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # 5. Percentage-Based Rollout Check
        if flag.flag_type == FeatureFlagType.PERCENTAGE:
            if flag.percentage >= 100.0:
                is_on = True
                reason = EvaluationReason.PERCENTAGE_ROLLOUT_MATCH
            elif flag.percentage <= 0.0:
                is_on = False
                reason = EvaluationReason.PERCENTAGE_ROLLOUT_MISMATCH
            else:
                id_to_hash = (
                    ctx.user_id
                    or str(ctx.attributes.get("client_ip", ""))
                    or str(ctx.attributes.get("session_id", "anonymous"))
                )
                bucket = self._hash_to_percentage(flag.name, id_to_hash)
                if bucket < flag.percentage:
                    is_on = True
                    reason = EvaluationReason.PERCENTAGE_ROLLOUT_MATCH
                else:
                    is_on = False
                    reason = EvaluationReason.PERCENTAGE_ROLLOUT_MISMATCH

            val = True if is_on else flag.fallback_value
            res = EvaluationResult(
                flag_name=name,
                is_enabled=is_on,
                value=val,
                variant="enabled" if is_on else flag.default_variant,
                reason=reason,
                evaluated_at=now,
                context_summary=ctx_summary,
            )
            self._record_result(res)
            return res

        # 6. Multivariate Flag Check
        if flag.flag_type == FeatureFlagType.MULTIVARIATE:
            id_to_hash = ctx.user_id or str(ctx.attributes.get("session_id", "default"))
            bucket = self._hash_to_percentage(flag.name, id_to_hash)
            if flag.variants:
                keys = sorted(flag.variants.keys())
                step = 100.0 / len(keys)
                idx = min(int(bucket / step), len(keys) - 1)
                selected_key = keys[idx]
                val = flag.variants[selected_key]
                res = EvaluationResult(
                    flag_name=name,
                    is_enabled=True,
                    value=val,
                    variant=selected_key,
                    reason=EvaluationReason.VARIANT_MATCH,
                    evaluated_at=now,
                    context_summary=ctx_summary,
                )
            else:
                res = EvaluationResult(
                    flag_name=name,
                    is_enabled=True,
                    value=flag.fallback_value,
                    variant=flag.default_variant,
                    reason=EvaluationReason.DEFAULT_VARIANT,
                    evaluated_at=now,
                    context_summary=ctx_summary,
                )
            self._record_result(res)
            return res

        # 7. Standard Boolean Flag (Global enabled)
        res = EvaluationResult(
            flag_name=name,
            is_enabled=True,
            value=True,
            variant="enabled",
            reason=EvaluationReason.GLOBAL_ENABLED,
            evaluated_at=now,
            context_summary=ctx_summary,
        )
        self._record_result(res)
        return res

    def is_enabled(self, name: str, context: EvaluationContext | None = None) -> bool:
        """Shorthand evaluation returning boolean is_enabled status."""
        return self.evaluate(name, context).is_enabled

    def _record_result(self, result: EvaluationResult) -> None:
        """Internal helper to log audit history and bump Prometheus counters."""
        with self._lock:
            self._audit_history.append(result)

        FEATURE_FLAG_EVALUATIONS_TOTAL.inc(
            labels={
                "flag_name": result.flag_name,
                "result": "enabled" if result.is_enabled else "disabled",
                "reason": result.reason.value,
            }
        )

    def get_audit_history(self, limit: int = 50) -> list[EvaluationResult]:
        """Return the most recent flag evaluation audit events."""
        with self._lock:
            entries = list(self._audit_history)
            return entries[-limit:]

    def clear(self) -> None:
        """Clear all registered flags and history (used in test fixtures)."""
        with self._lock:
            self._flags.clear()
            self._audit_history.clear()


RollbackHandler = Callable[[DeploymentRecord, RollbackReason, str], None]


class RollbackTriggerCoordinator:
    """Coordinates canary promotions, automated rollback evaluations, and safe fallback."""

    def __init__(
        self,
        flag_registry: FeatureFlagRegistry | None = None,
        default_thresholds: RollbackThresholds | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.flag_registry = flag_registry or FEATURE_FLAG_REGISTRY
        self.default_thresholds = default_thresholds or RollbackThresholds()
        self._deployments: dict[str, DeploymentRecord] = {}
        self._active_deployment_id: str | None = None
        self._handlers: list[RollbackHandler] = []

    def add_rollback_handler(self, handler: RollbackHandler) -> None:
        """Register a callback invoked when a rollback is executed."""
        with self._lock:
            self._handlers.append(handler)

    def register_deployment(
        self,
        deployment_id: str,
        version: str,
        git_commit: str,
        previous_version: str = "",
        associated_flags: list[str] | None = None,
        thresholds: RollbackThresholds | None = None,
        status: DeploymentStatus = DeploymentStatus.CANARY,
        canary_percentage: float = 10.0,
    ) -> DeploymentRecord:
        """Register a new production deployment or canary release."""
        now = datetime.datetime.now(datetime.UTC)
        record = DeploymentRecord(
            deployment_id=deployment_id,
            version=version,
            previous_version=previous_version,
            git_commit=git_commit,
            status=status,
            canary_percentage=canary_percentage,
            thresholds=thresholds or self.default_thresholds,
            started_at=now,
            updated_at=now,
            associated_flags=associated_flags or [],
        )

        with self._lock:
            self._deployments[deployment_id] = record
            self._active_deployment_id = deployment_id

            DEPLOYMENT_STATUS_GAUGE.set(
                _STATUS_NUMERIC_CODES[status], labels={"deployment_id": deployment_id}
            )
            DEPLOYMENT_CANARY_WEIGHT_GAUGE.set(
                canary_percentage, labels={"deployment_id": deployment_id}
            )

        # Synchronize associated feature flags percentage
        for flag_name in record.associated_flags:
            self.flag_registry.set_percentage(flag_name, canary_percentage)

        return record

    def advance_canary(self, deployment_id: str, new_percentage: float) -> DeploymentRecord:
        """Advance canary rollout traffic percentage for an active deployment."""
        if new_percentage < 0.0 or new_percentage > 100.0:
            raise ValueError(f"Percentage must be in [0.0, 100.0], got {new_percentage}")

        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                raise KeyError(f"Deployment {deployment_id} not found")
            if record.status in (DeploymentStatus.ROLLED_BACK, DeploymentStatus.FAILED):
                raise RuntimeError(
                    f"Cannot advance canary for deployment in terminal status {record.status}"
                )

            record.canary_percentage = new_percentage
            record.updated_at = datetime.datetime.now(datetime.UTC)
            if new_percentage >= 100.0:
                record.status = DeploymentStatus.ACTIVE
            else:
                record.status = DeploymentStatus.CANARY

            DEPLOYMENT_STATUS_GAUGE.set(
                _STATUS_NUMERIC_CODES[record.status],
                labels={"deployment_id": deployment_id},
            )
            DEPLOYMENT_CANARY_WEIGHT_GAUGE.set(
                new_percentage, labels={"deployment_id": deployment_id}
            )

        for flag_name in record.associated_flags:
            self.flag_registry.set_percentage(flag_name, new_percentage)

        return record

    def promote_to_stable(self, deployment_id: str) -> DeploymentRecord:
        """Promote deployment to stable after successful observation period."""
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                raise KeyError(f"Deployment {deployment_id} not found")

            record.status = DeploymentStatus.STABLE
            record.canary_percentage = 100.0
            record.updated_at = datetime.datetime.now(datetime.UTC)

            DEPLOYMENT_STATUS_GAUGE.set(
                _STATUS_NUMERIC_CODES[DeploymentStatus.STABLE],
                labels={"deployment_id": deployment_id},
            )
            DEPLOYMENT_CANARY_WEIGHT_GAUGE.set(100.0, labels={"deployment_id": deployment_id})

        for flag_name in record.associated_flags:
            self.flag_registry.set_percentage(flag_name, 100.0)

        return record

    def assess_health(self, deployment_id: str, snapshot: HealthSnapshot) -> RollbackAssessment:
        """Evaluate incoming health snapshot against deployment rollback thresholds."""
        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                raise KeyError(f"Deployment {deployment_id} not found")
            thresholds = record.thresholds

        # 1. Error Rate Check
        if snapshot.error_rate_pct > thresholds.max_error_rate_pct:
            return RollbackAssessment(
                should_rollback=True,
                reason=RollbackReason.ERROR_RATE_BREACH,
                details=(
                    f"Error rate {snapshot.error_rate_pct:.2f}% breached threshold "
                    f"{thresholds.max_error_rate_pct:.2f}%"
                ),
                breached_metrics={"error_rate_pct": snapshot.error_rate_pct},
            )

        # 2. P95 Latency Check
        if snapshot.p95_latency_ms > thresholds.max_p95_latency_ms:
            return RollbackAssessment(
                should_rollback=True,
                reason=RollbackReason.LATENCY_P95_BREACH,
                details=(
                    f"p95 latency {snapshot.p95_latency_ms:.1f}ms breached threshold "
                    f"{thresholds.max_p95_latency_ms:.1f}ms"
                ),
                breached_metrics={"p95_latency_ms": snapshot.p95_latency_ms},
            )

        # 3. Consecutive Health Check Failures
        if snapshot.consecutive_health_failures >= thresholds.max_consecutive_health_failures:
            return RollbackAssessment(
                should_rollback=True,
                reason=RollbackReason.HEALTH_CHECK_FAILURE,
                details=(
                    f"Consecutive health check failures ({snapshot.consecutive_health_failures}) "
                    f"breached threshold ({thresholds.max_consecutive_health_failures})"
                ),
                breached_metrics={"consecutive_failures": snapshot.consecutive_health_failures},
            )

        # 4. Critical Error Count Spike
        if snapshot.critical_errors_count >= thresholds.max_critical_errors:
            return RollbackAssessment(
                should_rollback=True,
                reason=RollbackReason.CRITICAL_EXCEPTION_SPIKE,
                details=(
                    f"Critical errors spike ({snapshot.critical_errors_count}) "
                    f"breached threshold ({thresholds.max_critical_errors})"
                ),
                breached_metrics={"critical_errors_count": snapshot.critical_errors_count},
            )

        return RollbackAssessment(
            should_rollback=False,
            reason=None,
            details="All deployment health metrics within SLO thresholds",
            breached_metrics={},
        )

    def trigger_rollback(
        self,
        deployment_id: str,
        reason: RollbackReason,
        details: str,
        auto_kill_flags: bool = True,
    ) -> DeploymentRecord:
        """Execute an automated or manual rollback of the specified deployment."""
        now = datetime.datetime.now(datetime.UTC)

        with self._lock:
            record = self._deployments.get(deployment_id)
            if not record:
                raise KeyError(f"Deployment {deployment_id} not found")

            record.status = DeploymentStatus.ROLLING_BACK
            record.rollback_reason = reason
            record.rollback_details = details
            record.rollback_at = now
            record.updated_at = now

            DEPLOYMENT_STATUS_GAUGE.set(
                _STATUS_NUMERIC_CODES[DeploymentStatus.ROLLING_BACK],
                labels={"deployment_id": deployment_id},
            )

            handlers_to_run = list(self._handlers)

        # Run registered rollback handlers outside lock
        for handler in handlers_to_run:
            try:
                handler(record, reason, details)
            except Exception:  # noqa: BLE001
                pass

        # If configured, instantly trigger kill switches for associated feature flags
        if auto_kill_flags:
            for flag_name in record.associated_flags:
                self.flag_registry.set_kill_switch(flag_name, kill=True)

        with self._lock:
            record.status = DeploymentStatus.ROLLED_BACK
            record.canary_percentage = 0.0
            record.updated_at = datetime.datetime.now(datetime.UTC)

            DEPLOYMENT_STATUS_GAUGE.set(
                _STATUS_NUMERIC_CODES[DeploymentStatus.ROLLED_BACK],
                labels={"deployment_id": deployment_id},
            )
            DEPLOYMENT_CANARY_WEIGHT_GAUGE.set(0.0, labels={"deployment_id": deployment_id})

        DEPLOYMENT_ROLLBACK_TRIGGERS_TOTAL.inc(
            labels={"deployment_id": deployment_id, "reason": reason.value}
        )

        return record

    def check_and_rollback_if_unhealthy(
        self, deployment_id: str, snapshot: HealthSnapshot
    ) -> tuple[bool, DeploymentRecord | None]:
        """Convenience method: assess health snapshot and trigger rollback if breached."""
        assessment = self.assess_health(deployment_id, snapshot)
        if assessment.should_rollback and assessment.reason:
            rec = self.trigger_rollback(
                deployment_id=deployment_id,
                reason=assessment.reason,
                details=assessment.details,
            )
            return True, rec
        with self._lock:
            return False, self._deployments.get(deployment_id)

    def get_deployment(self, deployment_id: str) -> DeploymentRecord | None:
        """Retrieve deployment record by id."""
        with self._lock:
            return self._deployments.get(deployment_id)

    def get_active_deployment(self) -> DeploymentRecord | None:
        """Retrieve the currently active or canary deployment record."""
        with self._lock:
            if not self._active_deployment_id:
                return None
            return self._deployments.get(self._active_deployment_id)

    def clear(self) -> None:
        """Clear deployments and handlers (test fixture helper)."""
        with self._lock:
            self._deployments.clear()
            self._active_deployment_id = None
            self._handlers.clear()


# Default global instances for application-wide access
FEATURE_FLAG_REGISTRY = FeatureFlagRegistry()
ROLLBACK_COORDINATOR = RollbackTriggerCoordinator(flag_registry=FEATURE_FLAG_REGISTRY)
