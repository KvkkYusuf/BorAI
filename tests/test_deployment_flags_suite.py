"""Comprehensive test suite for feature flags and production deployment rollback coordination."""

import threading

import pytest

from stock_analysis.observability.deployment_flags import (
    DeploymentRecord,
    DeploymentStatus,
    EvaluationContext,
    EvaluationReason,
    FeatureFlagDefinition,
    FeatureFlagRegistry,
    FeatureFlagType,
    HealthSnapshot,
    RollbackReason,
    RollbackThresholds,
    RollbackTriggerCoordinator,
)
from stock_analysis.observability.metrics import (
    FEATURE_FLAG_ACTIVE_GAUGE,
    FEATURE_FLAG_EVALUATIONS_TOTAL,
)


@pytest.fixture
def flag_registry() -> FeatureFlagRegistry:
    """Provide a fresh FeatureFlagRegistry for tests."""
    reg = FeatureFlagRegistry()
    reg.clear()
    return reg


@pytest.fixture
def rollback_coordinator(flag_registry: FeatureFlagRegistry) -> RollbackTriggerCoordinator:
    """Provide a fresh RollbackTriggerCoordinator wired with the test registry."""
    coordinator = RollbackTriggerCoordinator(flag_registry=flag_registry)
    coordinator.clear()
    return coordinator


# ============================================================================
# 1. Feature Flag Registry & Evaluation Tests
# ============================================================================


def test_boolean_flag_lifecycle(flag_registry: FeatureFlagRegistry) -> None:
    """Test registration, enablement, evaluation, and disabling of a boolean flag."""
    flag = FeatureFlagDefinition(
        name="new_chart_engine",
        flag_type=FeatureFlagType.BOOLEAN,
        enabled=True,
        description="Enable WebGL stock chart rendering engine",
    )
    flag_registry.register_flag(flag)

    # Initially enabled
    res = flag_registry.evaluate("new_chart_engine")
    assert res.is_enabled is True
    assert res.reason == EvaluationReason.GLOBAL_ENABLED
    assert res.value is True

    # Disable flag
    flag_registry.set_enabled("new_chart_engine", False)
    res_disabled = flag_registry.evaluate("new_chart_engine")
    assert res_disabled.is_enabled is False
    assert res_disabled.reason == EvaluationReason.FLAG_DISABLED


def test_flag_not_found_returns_safe_fallback(flag_registry: FeatureFlagRegistry) -> None:
    """Test that querying an unregistered flag returns safe fallback with FLAG_NOT_FOUND."""
    res = flag_registry.evaluate("non_existent_feature_xyz")
    assert res.is_enabled is False
    assert res.reason == EvaluationReason.FLAG_NOT_FOUND
    assert res.value is False


def test_kill_switch_immediate_override(flag_registry: FeatureFlagRegistry) -> None:
    """Test that triggering kill switch instantly disables flag regardless of other rules."""
    flag = FeatureFlagDefinition(
        name="experimental_llm_inference",
        flag_type=FeatureFlagType.BOOLEAN,
        enabled=True,
        fallback_value="heuristic_fallback",
        description="LLM explanation engine with emergency kill switch",
    )
    flag_registry.register_flag(flag)

    # Active before kill switch
    assert flag_registry.is_enabled("experimental_llm_inference") is True

    # Activate kill switch
    ok = flag_registry.set_kill_switch("experimental_llm_inference", kill=True)
    assert ok is True

    res = flag_registry.evaluate("experimental_llm_inference")
    assert res.is_enabled is False
    assert res.reason == EvaluationReason.KILL_SWITCH
    assert res.value == "heuristic_fallback"

    # Restore kill switch
    flag_registry.set_kill_switch("experimental_llm_inference", kill=False)
    assert flag_registry.is_enabled("experimental_llm_inference") is True


def test_targeted_users_and_roles(flag_registry: FeatureFlagRegistry) -> None:
    """Test user_id and role-based targeted evaluation."""
    flag = FeatureFlagDefinition(
        name="beta_backtesting_v2",
        flag_type=FeatureFlagType.TARGETED,
        enabled=True,
        target_users={"usr_vip_001", "usr_vip_002"},
        target_roles={"beta_tester", "quant_analyst"},
        description="Backtesting engine v2 for internal and VIP testers",
    )
    flag_registry.register_flag(flag)

    # 1. Matching target user
    ctx_user = EvaluationContext(user_id="usr_vip_001")
    res_user = flag_registry.evaluate("beta_backtesting_v2", ctx_user)
    assert res_user.is_enabled is True
    assert res_user.reason == EvaluationReason.USER_TARGETED

    # 2. Matching target role
    ctx_role = EvaluationContext(user_id="usr_normal_001", roles={"beta_tester"})
    res_role = flag_registry.evaluate("beta_backtesting_v2", ctx_role)
    assert res_role.is_enabled is True
    assert res_role.reason == EvaluationReason.ROLE_TARGETED

    # 3. Non-matching user and role
    ctx_other = EvaluationContext(user_id="usr_normal_999", roles={"standard_user"})
    res_other = flag_registry.evaluate("beta_backtesting_v2", ctx_other)
    assert res_other.is_enabled is False
    assert res_other.reason == EvaluationReason.PERCENTAGE_ROLLOUT_MISMATCH


def test_deterministic_percentage_canary_rollout(flag_registry: FeatureFlagRegistry) -> None:
    """Test deterministic percentage hashing ensuring consistent user bucketing."""
    flag = FeatureFlagDefinition(
        name="canary_search_v2",
        flag_type=FeatureFlagType.PERCENTAGE,
        enabled=True,
        percentage=30.0,  # 30% rollout
        description="Canary vector search rollout",
    )
    flag_registry.register_flag(flag)

    # Consistency test: same user always gets the exact same evaluation result
    user_id = "user_deterministic_test_123"
    ctx = EvaluationContext(user_id=user_id)

    eval_1 = flag_registry.evaluate("canary_search_v2", ctx)
    eval_2 = flag_registry.evaluate("canary_search_v2", ctx)
    eval_3 = flag_registry.evaluate("canary_search_v2", ctx)

    assert eval_1.is_enabled == eval_2.is_enabled == eval_3.is_enabled
    assert eval_1.reason == eval_2.reason == eval_3.reason

    # Boundary conditions: 0% and 100%
    flag_registry.set_percentage("canary_search_v2", 0.0)
    assert flag_registry.is_enabled("canary_search_v2", ctx) is False

    flag_registry.set_percentage("canary_search_v2", 100.0)
    assert flag_registry.is_enabled("canary_search_v2", ctx) is True


def test_multivariate_flag_evaluation(flag_registry: FeatureFlagRegistry) -> None:
    """Test multivariate flag distributing users into configured variant buckets."""
    flag = FeatureFlagDefinition(
        name="order_summary_theme",
        flag_type=FeatureFlagType.MULTIVARIATE,
        enabled=True,
        variants={"dark_compact": "#111", "glassmorphic": "#222", "contrast": "#fff"},
        default_variant="dark_compact",
    )
    flag_registry.register_flag(flag)

    ctx = EvaluationContext(user_id="user_variant_test_01")
    res = flag_registry.evaluate("order_summary_theme", ctx)

    assert res.is_enabled is True
    assert res.reason == EvaluationReason.VARIANT_MATCH
    assert res.variant in ("dark_compact", "glassmorphic", "contrast")
    assert res.value in ("#111", "#222", "#fff")


def test_audit_history_and_metrics_emission(flag_registry: FeatureFlagRegistry) -> None:
    """Test evaluation audit history collection and Prometheus metrics updates."""
    flag = FeatureFlagDefinition(name="metric_audit_flag", enabled=True)
    flag_registry.register_flag(flag)

    # Initial active gauge should be 1.0
    assert FEATURE_FLAG_ACTIVE_GAUGE.get(labels={"flag_name": "metric_audit_flag"}) == 1.0

    eval_count_before = FEATURE_FLAG_EVALUATIONS_TOTAL.get(
        labels={"flag_name": "metric_audit_flag", "result": "enabled", "reason": "global_enabled"}
    )

    flag_registry.evaluate("metric_audit_flag", EvaluationContext(user_id="u1"))
    flag_registry.evaluate("metric_audit_flag", EvaluationContext(user_id="u2"))

    eval_count_after = FEATURE_FLAG_EVALUATIONS_TOTAL.get(
        labels={"flag_name": "metric_audit_flag", "result": "enabled", "reason": "global_enabled"}
    )
    assert eval_count_after == eval_count_before + 2.0

    history = flag_registry.get_audit_history(limit=10)
    assert len(history) >= 2
    assert history[-1].flag_name == "metric_audit_flag"


def test_concurrent_feature_flag_evaluation(flag_registry: FeatureFlagRegistry) -> None:
    """Verify thread-safety under heavy concurrent flag evaluations."""
    flag = FeatureFlagDefinition(
        name="concurrency_flag",
        flag_type=FeatureFlagType.PERCENTAGE,
        percentage=50.0,
    )
    flag_registry.register_flag(flag)

    results: list[bool] = []
    lock = threading.Lock()

    def worker(worker_id: int) -> None:
        for i in range(50):
            res = flag_registry.is_enabled(
                "concurrency_flag",
                EvaluationContext(user_id=f"worker_{worker_id}_req_{i}"),
            )
            with lock:
                results.append(res)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 250


# ============================================================================
# 2. Production Deployment & Automated Rollback Tests
# ============================================================================


def test_deployment_registration_and_canary_advancement(
    rollback_coordinator: RollbackTriggerCoordinator,
    flag_registry: FeatureFlagRegistry,
) -> None:
    """Test deployment registration, canary traffic steps, and promotion to stable."""
    # Register an associated feature flag
    flag = FeatureFlagDefinition(
        name="order_matching_v2",
        flag_type=FeatureFlagType.PERCENTAGE,
        percentage=10.0,
    )
    flag_registry.register_flag(flag)

    dep = rollback_coordinator.register_deployment(
        deployment_id="dep-2026-10-28-01",
        version="v2.4.0",
        previous_version="v2.3.9",
        git_commit="a1b2c3d4",
        associated_flags=["order_matching_v2"],
        canary_percentage=10.0,
    )

    assert dep.deployment_id == "dep-2026-10-28-01"
    assert dep.status == DeploymentStatus.CANARY
    assert dep.canary_percentage == 10.0
    flag_record = flag_registry.get_flag("order_matching_v2")
    assert flag_record is not None
    assert flag_record.percentage == 10.0

    # Advance canary from 10% -> 50%
    dep_advanced = rollback_coordinator.advance_canary("dep-2026-10-28-01", 50.0)
    assert dep_advanced.canary_percentage == 50.0
    assert dep_advanced.status == DeploymentStatus.CANARY
    flag_record = flag_registry.get_flag("order_matching_v2")
    assert flag_record is not None
    assert flag_record.percentage == 50.0

    # Promote to stable (100%)
    dep_stable = rollback_coordinator.promote_to_stable("dep-2026-10-28-01")
    assert dep_stable.status == DeploymentStatus.STABLE
    assert dep_stable.canary_percentage == 100.0
    flag_record = flag_registry.get_flag("order_matching_v2")
    assert flag_record is not None
    assert flag_record.percentage == 100.0


def test_rollback_assessment_within_thresholds(
    rollback_coordinator: RollbackTriggerCoordinator,
) -> None:
    """Verify that a healthy telemetry snapshot passes assessment without rollback."""
    dep = rollback_coordinator.register_deployment(
        deployment_id="dep-healthy",
        version="v1.0.1",
        git_commit="abcdef12",
        thresholds=RollbackThresholds(max_error_rate_pct=2.0, max_p95_latency_ms=800.0),
    )

    healthy_snapshot = HealthSnapshot(
        error_rate_pct=0.4,
        p95_latency_ms=280.0,
        consecutive_health_failures=0,
        critical_errors_count=0,
    )

    assessment = rollback_coordinator.assess_health(dep.deployment_id, healthy_snapshot)
    assert assessment.should_rollback is False
    assert assessment.reason is None


def test_automated_rollback_on_error_rate_breach(
    rollback_coordinator: RollbackTriggerCoordinator,
    flag_registry: FeatureFlagRegistry,
) -> None:
    """Test automated rollback when HTTP 5xx error rate breaches maximum threshold."""
    flag = FeatureFlagDefinition(name="payment_gateway_v2", enabled=True)
    flag_registry.register_flag(flag)

    dep = rollback_coordinator.register_deployment(
        deployment_id="dep-error-breach",
        version="v3.0.0",
        git_commit="beef1234",
        associated_flags=["payment_gateway_v2"],
        thresholds=RollbackThresholds(max_error_rate_pct=1.5),
    )

    # Telemetry snapshot breaching error rate (3.8% > 1.5%)
    unhealthy_snapshot = HealthSnapshot(
        error_rate_pct=3.8,
        p95_latency_ms=300.0,
    )

    triggered, updated_dep = rollback_coordinator.check_and_rollback_if_unhealthy(
        dep.deployment_id, unhealthy_snapshot
    )

    assert triggered is True
    assert updated_dep is not None
    assert updated_dep.status == DeploymentStatus.ROLLED_BACK
    assert updated_dep.rollback_reason == RollbackReason.ERROR_RATE_BREACH
    assert updated_dep.canary_percentage == 0.0

    # Associated feature flag kill switch must have been instantly triggered
    flag_record = flag_registry.get_flag("payment_gateway_v2")
    assert flag_record is not None
    assert flag_record.kill_switched is True
    assert flag_registry.is_enabled("payment_gateway_v2") is False


def test_automated_rollback_on_p95_latency_breach(
    rollback_coordinator: RollbackTriggerCoordinator,
) -> None:
    """Test automated rollback when p95 response time degrades beyond SLA target."""
    dep = rollback_coordinator.register_deployment(
        deployment_id="dep-latency-breach",
        version="v3.1.0",
        git_commit="9876feed",
        thresholds=RollbackThresholds(max_p95_latency_ms=800.0),
    )

    slow_snapshot = HealthSnapshot(
        error_rate_pct=0.1,
        p95_latency_ms=1250.0,  # 1250ms > 800ms
    )

    triggered, updated_dep = rollback_coordinator.check_and_rollback_if_unhealthy(
        dep.deployment_id, slow_snapshot
    )

    assert triggered is True
    assert updated_dep is not None
    assert updated_dep.rollback_reason == RollbackReason.LATENCY_P95_BREACH
    assert updated_dep.status == DeploymentStatus.ROLLED_BACK


def test_automated_rollback_on_health_check_failures(
    rollback_coordinator: RollbackTriggerCoordinator,
) -> None:
    """Test automated rollback after consecutive health check ping failures."""
    dep = rollback_coordinator.register_deployment(
        deployment_id="dep-health-failure",
        version="v3.2.0",
        git_commit="11223344",
        thresholds=RollbackThresholds(max_consecutive_health_failures=3),
    )

    failed_health_snapshot = HealthSnapshot(
        error_rate_pct=0.0,
        p95_latency_ms=100.0,
        consecutive_health_failures=3,
    )

    triggered, updated_dep = rollback_coordinator.check_and_rollback_if_unhealthy(
        dep.deployment_id, failed_health_snapshot
    )

    assert triggered is True
    assert updated_dep is not None
    assert updated_dep.rollback_reason == RollbackReason.HEALTH_CHECK_FAILURE


def test_rollback_callback_handler_invocation(
    rollback_coordinator: RollbackTriggerCoordinator,
) -> None:
    """Verify registered rollback handlers are invoked during rollback execution."""
    callback_invocations: list[tuple[str, RollbackReason, str]] = []

    def on_rollback(dep: DeploymentRecord, reason: RollbackReason, details: str) -> None:
        callback_invocations.append((dep.deployment_id, reason, details))

    rollback_coordinator.add_rollback_handler(on_rollback)

    rollback_coordinator.register_deployment(
        deployment_id="dep-callback-test",
        version="v3.3.0",
        git_commit="55667788",
    )

    rollback_coordinator.trigger_rollback(
        deployment_id="dep-callback-test",
        reason=RollbackReason.MANUAL_OVERRIDE,
        details="Operator manual rollback initiated via admin console",
    )

    assert len(callback_invocations) == 1
    dep_id, r_reason, r_details = callback_invocations[0]
    assert dep_id == "dep-callback-test"
    assert r_reason == RollbackReason.MANUAL_OVERRIDE
    assert "Operator manual rollback" in r_details


def test_cannot_advance_canary_on_rolled_back_deployment(
    rollback_coordinator: RollbackTriggerCoordinator,
) -> None:
    """Verify attempting to advance canary on a rolled back deployment raises RuntimeError."""
    dep = rollback_coordinator.register_deployment(
        deployment_id="dep-terminal-state",
        version="v4.0.0",
        git_commit="9900aabb",
    )
    rollback_coordinator.trigger_rollback(
        dep.deployment_id, RollbackReason.MANUAL_OVERRIDE, "Emergency abort"
    )

    with pytest.raises(RuntimeError, match="terminal status"):
        rollback_coordinator.advance_canary(dep.deployment_id, 25.0)


def test_invalid_input_validation(
    rollback_coordinator: RollbackTriggerCoordinator,
    flag_registry: FeatureFlagRegistry,
) -> None:
    """Test input validations for invalid percentages, missing deployments, and flags."""
    # Invalid percentage in FeatureFlagDefinition
    with pytest.raises(ValueError, match="must be between 0.0 and 100.0"):
        FeatureFlagDefinition(name="bad_flag", percentage=150.0)

    # Invalid percentage in set_percentage
    flag = FeatureFlagDefinition(name="test_flag", percentage=50.0)
    flag_registry.register_flag(flag)
    with pytest.raises(ValueError, match="Percentage must be in"):
        flag_registry.set_percentage("test_flag", -5.0)

    # Non-existent deployment
    with pytest.raises(KeyError, match="not found"):
        rollback_coordinator.advance_canary("dep-does-not-exist", 20.0)
