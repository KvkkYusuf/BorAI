"""Tests for critical flow coverage targets and multidimensional test quality assurance.

Guarantees:
- Minimum coverage targets are defined for all critical application flows.
- The auditor rejects hollow coverage (high line coverage with zero/sparse assertions).
- Branch coverage, boundary tests, and negative paths are strictly verified.
- Static AST inspection accurately gauges assertion density on existing test suites.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.testing.coverage_quality import (
    DEFAULT_FLOW_TARGETS,
    CoverageQualityAuditor,
    CriticalFlow,
    FlowCoverageTarget,
    FlowTestMetrics,
    QualityVerdict,
)


@pytest.mark.unit
def test_all_critical_flows_have_default_targets() -> None:
    """Verify all critical flows are mapped with rigorous quality targets."""
    for flow in CriticalFlow:
        assert flow in DEFAULT_FLOW_TARGETS
        target = DEFAULT_FLOW_TARGETS[flow]
        assert target.flow == flow
        assert target.min_line_coverage_pct >= 85.0
        assert target.min_branch_coverage_pct >= 75.0
        assert target.min_assertion_density >= 1.5
        assert target.min_negative_case_ratio_pct >= 20.0
        assert len(target.description) > 10


@pytest.mark.unit
def test_audit_flow_passed() -> None:
    """Verify audit_flow returns PASSED when all criteria are satisfied."""
    auditor = CoverageQualityAuditor()
    metrics = FlowTestMetrics(
        flow=CriticalFlow.CORE_INDICATORS_AND_TRANSFORMS,
        total_lines=500,
        covered_lines=465,  # 93.0% (target: 90.0%)
        total_branches=80,
        covered_branches=72,  # 90.0% (target: 85.0%)
        total_tests=20,
        total_assertions=50,  # 2.5 assertions/test (target: 2.0)
        negative_or_edge_tests=6,  # 30.0% (target: 25.0%)
    )

    result = auditor.audit_flow(metrics)
    assert result.verdict == QualityVerdict.PASSED
    assert result.is_acceptable is True
    assert len(result.issues) == 0


@pytest.mark.unit
def test_audit_flow_rejects_low_line_coverage() -> None:
    """Verify audit_flow rejects flows failing to meet line coverage minimums."""
    auditor = CoverageQualityAuditor()
    metrics = FlowTestMetrics(
        flow=CriticalFlow.AUTH_SESSION_AND_SECURITY,
        total_lines=1000,
        covered_lines=850,  # 85.0% (target: 95.0%)
        total_branches=200,
        covered_branches=190,  # 95.0%
        total_tests=40,
        total_assertions=120,  # 3.0 assertions/test
        negative_or_edge_tests=15,  # 37.5%
    )

    result = auditor.audit_flow(metrics)
    assert result.verdict == QualityVerdict.REJECTED_LOW_LINE_COVERAGE
    assert result.is_acceptable is False
    assert any("Satır kapsaması yetersiz" in issue for issue in result.issues)


@pytest.mark.unit
def test_audit_flow_rejects_low_branch_coverage() -> None:
    """Verify audit_flow rejects high line coverage when branch coverage is inadequate."""
    auditor = CoverageQualityAuditor()
    metrics = FlowTestMetrics(
        flow=CriticalFlow.MODEL_GOVERNANCE_AND_FALLBACK,
        total_lines=400,
        covered_lines=380,  # 95.0% (target: 90.0%)
        total_branches=100,
        covered_branches=70,  # 70.0% (target: 85.0%)
        total_tests=15,
        total_assertions=40,
        negative_or_edge_tests=5,
    )

    result = auditor.audit_flow(metrics)
    assert result.verdict == QualityVerdict.REJECTED_LOW_BRANCH_COVERAGE
    assert result.is_acceptable is False
    assert any("Dal kapsaması yetersiz" in issue for issue in result.issues)


@pytest.mark.unit
def test_audit_flow_rejects_hollow_coverage_fallacy() -> None:
    """CRITICAL TEST: Verify high line coverage is rejected if tests lack assertions.

    Guarantees line coverage alone is never accepted as proof of quality.
    """
    auditor = CoverageQualityAuditor()
    # High line coverage (98%) but zero assertions (test merely executes code without verifying)
    metrics = FlowTestMetrics(
        flow=CriticalFlow.KVKK_PRIVACY_AND_PURGE,
        total_lines=300,
        covered_lines=294,  # 98.0% (target: 95.0%)
        total_branches=50,
        covered_branches=48,  # 96.0% (target: 90.0%)
        total_tests=10,
        total_assertions=2,  # 0.2 assertions/test (density < 1.0)
        negative_or_edge_tests=3,
    )

    result = auditor.audit_flow(metrics)
    assert result.verdict == QualityVerdict.REJECTED_HOLLOW_COVERAGE
    assert result.is_acceptable is False
    assert any("YALANCI KAPSAMA TESPİTİ" in issue for issue in result.issues)
    assert any("assert" in rec for rec in result.recommendations)


@pytest.mark.unit
def test_audit_flow_rejects_lack_of_negative_and_edge_paths() -> None:
    """Verify test suites that only test the happy path are rejected."""
    auditor = CoverageQualityAuditor()
    metrics = FlowTestMetrics(
        flow=CriticalFlow.ALERTS_AND_NOTIFICATIONS,
        total_lines=600,
        covered_lines=570,  # 95.0% (target: 88.0%)
        total_branches=100,
        covered_branches=90,  # 90.0% (target: 82.0%)
        total_tests=20,
        total_assertions=50,  # 2.5 assertions/test
        negative_or_edge_tests=1,  # 5.0% (target: 25.0%)
    )

    result = auditor.audit_flow(metrics)
    assert result.verdict == QualityVerdict.REJECTED_LACK_OF_NEGATIVE_PATHS
    assert result.is_acceptable is False
    assert any("Negatif/hata testleri yetersiz" in issue for issue in result.issues)


@pytest.mark.unit
def test_audit_flow_warning_suboptimal_density() -> None:
    """Verify warning when assertion density is between 1.0 and target threshold."""
    auditor = CoverageQualityAuditor()
    # Target density is 2.5 for AUTH_SESSION_AND_SECURITY
    metrics = FlowTestMetrics(
        flow=CriticalFlow.AUTH_SESSION_AND_SECURITY,
        total_lines=400,
        covered_lines=390,  # 97.5%
        total_branches=80,
        covered_branches=76,  # 95.0%
        total_tests=20,
        total_assertions=30,  # 1.5 assertions/test (>= 1.0 but < 2.5)
        negative_or_edge_tests=7,  # 35.0%
    )

    result = auditor.audit_flow(metrics)
    assert result.verdict == QualityVerdict.WARNING_SUBOPTIMAL_DENSITY
    assert result.is_acceptable is True  # Acceptable with warning
    assert any("İddia yoğunluğu suboptimal" in rec for rec in result.recommendations)


@pytest.mark.unit
def test_audit_system_consolidated_report() -> None:
    """Verify audit_system produces multi-flow report and summary markdown."""
    auditor = CoverageQualityAuditor()
    flows_metrics = [
        FlowTestMetrics(
            flow=CriticalFlow.CORE_INDICATORS_AND_TRANSFORMS,
            total_lines=500,
            covered_lines=470,
            total_branches=80,
            covered_branches=72,
            total_tests=25,
            total_assertions=65,
            negative_or_edge_tests=8,
        ),
        FlowTestMetrics(
            flow=CriticalFlow.API_ROUTING_AND_GATEWAY,
            total_lines=400,
            covered_lines=360,
            total_branches=60,
            covered_branches=50,
            total_tests=15,
            total_assertions=30,
            negative_or_edge_tests=4,
        ),
    ]

    report = auditor.audit_system(flows_metrics)
    assert report.total_flows_evaluated == 2
    assert report.all_passed is True
    assert report.average_line_coverage_pct > 88.0
    assert report.hollow_coverage_count == 0
    assert "Kritik Akışlar Kalite ve Kapsama Denetim Raporu" in report.summary_markdown
    assert "core_indicators_and_transforms" in report.summary_markdown
    assert "api_routing_and_gateway" in report.summary_markdown


@pytest.mark.unit
def test_custom_target_override() -> None:
    """Verify custom targets override defaults when provided."""
    custom_target = FlowCoverageTarget(
        flow=CriticalFlow.DATA_PIPELINE_AND_TIMESERIES,
        min_line_coverage_pct=99.0,
        min_branch_coverage_pct=95.0,
        min_assertion_density=4.0,
        min_negative_case_ratio_pct=40.0,
        description="Özel katı hedef.",
    )
    auditor = CoverageQualityAuditor(
        custom_targets={CriticalFlow.DATA_PIPELINE_AND_TIMESERIES: custom_target}
    )
    target = auditor.get_target(CriticalFlow.DATA_PIPELINE_AND_TIMESERIES)
    assert target.min_line_coverage_pct == 99.0
    assert target.min_assertion_density == 4.0


@pytest.mark.unit
def test_analyze_test_file_ast_on_actual_test_suite() -> None:
    """Verify AST inspector accurately counts tests, assertions, and pytest.raises."""
    test_file = Path(__file__).parent / "test_test_data_privacy.py"
    if not test_file.exists():
        pytest.skip("test_test_data_privacy.py not found")

    stats = CoverageQualityAuditor.analyze_test_file_ast(test_file)
    assert stats["test_functions"] >= 10
    assert stats["assert_statements"] >= 20
    assert stats["pytest_raises_contexts"] >= 1
    assert stats["total_assertions"] > stats["test_functions"]

    # Verify assertion density is high (> 2.0 assertions/test)
    density = stats["total_assertions"] / stats["test_functions"]
    assert density >= 2.0


@pytest.mark.unit
def test_analyze_test_file_ast_handles_missing_file() -> None:
    """Verify AST inspector gracefully handles non-existent or invalid files."""
    stats = CoverageQualityAuditor.analyze_test_file_ast("non_existent_file.py")
    assert stats["test_functions"] == 0
    assert stats["total_assertions"] == 0
