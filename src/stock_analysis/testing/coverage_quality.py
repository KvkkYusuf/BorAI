"""Quality assurance metrics and coverage target auditor for critical flows.

Enforces minimum coverage thresholds while explicitly rejecting the fallacy
that high line coverage alone equates to software quality:
- Differentiates line coverage from branch coverage.
- Monitors assertion density (detects hollow/assertion-free test suites).
- Enforces boundary condition and negative path verification.
- Audits critical architectural flows with tiered SLA targets.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class CriticalFlow(StrEnum):
    """Critical application flows requiring dedicated coverage and quality targets."""

    CORE_INDICATORS_AND_TRANSFORMS = "core_indicators_and_transforms"
    AUTH_SESSION_AND_SECURITY = "auth_session_and_security"
    DATA_PIPELINE_AND_TIMESERIES = "data_pipeline_and_timeseries"
    ALERTS_AND_NOTIFICATIONS = "alerts_and_notifications"
    MODEL_GOVERNANCE_AND_FALLBACK = "model_governance_and_fallback"
    KVKK_PRIVACY_AND_PURGE = "kvkk_privacy_and_purge"
    API_ROUTING_AND_GATEWAY = "api_routing_and_gateway"


class QualityVerdict(StrEnum):
    """Evaluation verdict for test coverage and multi-dimensional quality."""

    PASSED = "PASSED"
    REJECTED_LOW_LINE_COVERAGE = "REJECTED_LOW_LINE_COVERAGE"
    REJECTED_LOW_BRANCH_COVERAGE = "REJECTED_LOW_BRANCH_COVERAGE"
    REJECTED_HOLLOW_COVERAGE = "REJECTED_HOLLOW_COVERAGE"
    REJECTED_LACK_OF_NEGATIVE_PATHS = "REJECTED_LACK_OF_NEGATIVE_PATHS"
    WARNING_SUBOPTIMAL_DENSITY = "WARNING_SUBOPTIMAL_DENSITY"


@dataclass(frozen=True)
class FlowCoverageTarget:
    """Configured coverage and test quality targets for a critical flow."""

    flow: CriticalFlow
    min_line_coverage_pct: float
    min_branch_coverage_pct: float
    min_assertion_density: float  # Assertions per test function
    min_negative_case_ratio_pct: float  # Percentage of negative/exception/edge tests
    description: str


# Canonical targets defined in docs/TEST_STRATEGY.md
DEFAULT_FLOW_TARGETS: dict[CriticalFlow, FlowCoverageTarget] = {
    CriticalFlow.CORE_INDICATORS_AND_TRANSFORMS: FlowCoverageTarget(
        flow=CriticalFlow.CORE_INDICATORS_AND_TRANSFORMS,
        min_line_coverage_pct=90.0,
        min_branch_coverage_pct=85.0,
        min_assertion_density=2.0,
        min_negative_case_ratio_pct=25.0,
        description="Teknik indikatörler, fiyat dönüşümleri, split ve temettü düzeltmeleri.",
    ),
    CriticalFlow.AUTH_SESSION_AND_SECURITY: FlowCoverageTarget(
        flow=CriticalFlow.AUTH_SESSION_AND_SECURITY,
        min_line_coverage_pct=95.0,
        min_branch_coverage_pct=90.0,
        min_assertion_density=2.5,
        min_negative_case_ratio_pct=30.0,
        description="Kimlik denetimi, oturum yaşam döngüsü, RBAC, CSRF, BOLA/IDOR savunması.",
    ),
    CriticalFlow.DATA_PIPELINE_AND_TIMESERIES: FlowCoverageTarget(
        flow=CriticalFlow.DATA_PIPELINE_AND_TIMESERIES,
        min_line_coverage_pct=88.0,
        min_branch_coverage_pct=82.0,
        min_assertion_density=1.8,
        min_negative_case_ratio_pct=20.0,
        description="Veri hattı, sağlayıcı adaptörleri, eksik veri ve takvim yönetimi.",
    ),
    CriticalFlow.ALERTS_AND_NOTIFICATIONS: FlowCoverageTarget(
        flow=CriticalFlow.ALERTS_AND_NOTIFICATIONS,
        min_line_coverage_pct=88.0,
        min_branch_coverage_pct=82.0,
        min_assertion_density=2.0,
        min_negative_case_ratio_pct=25.0,
        description="Uyarı motoru, kota denetimi, e-posta şablon maskelemesi ve SPK uyarıları.",
    ),
    CriticalFlow.MODEL_GOVERNANCE_AND_FALLBACK: FlowCoverageTarget(
        flow=CriticalFlow.MODEL_GOVERNANCE_AND_FALLBACK,
        min_line_coverage_pct=90.0,
        min_branch_coverage_pct=85.0,
        min_assertion_density=2.0,
        min_negative_case_ratio_pct=25.0,
        description="Model kayıt defteri, naif baseline kıyası, devre kesici ve güvenli fallback.",
    ),
    CriticalFlow.KVKK_PRIVACY_AND_PURGE: FlowCoverageTarget(
        flow=CriticalFlow.KVKK_PRIVACY_AND_PURGE,
        min_line_coverage_pct=95.0,
        min_branch_coverage_pct=90.0,
        min_assertion_density=2.5,
        min_negative_case_ratio_pct=30.0,
        description="KVKK veri ihracı, hesap silme, PII koruma kalkanı ve mezar taşı kayıtları.",
    ),
    CriticalFlow.API_ROUTING_AND_GATEWAY: FlowCoverageTarget(
        flow=CriticalFlow.API_ROUTING_AND_GATEWAY,
        min_line_coverage_pct=85.0,
        min_branch_coverage_pct=78.0,
        min_assertion_density=1.8,
        min_negative_case_ratio_pct=20.0,
        description="FastAPI rotaları, şema doğrulama, hız sınırı, hata sanitizasyonu.",
    ),
}


@dataclass
class FlowTestMetrics:
    """Observed test execution and coverage metrics for a critical flow."""

    flow: CriticalFlow
    total_lines: int
    covered_lines: int
    total_branches: int = 0
    covered_branches: int = 0
    total_tests: int = 0
    total_assertions: int = 0
    negative_or_edge_tests: int = 0

    @property
    def line_coverage_pct(self) -> float:
        if self.total_lines <= 0:
            return 100.0
        return round((self.covered_lines / self.total_lines) * 100.0, 2)

    @property
    def branch_coverage_pct(self) -> float:
        if self.total_branches <= 0:
            return 100.0
        return round((self.covered_branches / self.total_branches) * 100.0, 2)

    @property
    def assertion_density(self) -> float:
        if self.total_tests <= 0:
            return 0.0
        return round(self.total_assertions / self.total_tests, 2)

    @property
    def negative_case_ratio_pct(self) -> float:
        if self.total_tests <= 0:
            return 0.0
        return round((self.negative_or_edge_tests / self.total_tests) * 100.0, 2)


@dataclass
class FlowQualityAuditResult:
    """Outcome of quality and coverage target audit for a specific flow."""

    flow: CriticalFlow
    verdict: QualityVerdict
    target: FlowCoverageTarget
    metrics: FlowTestMetrics
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    @property
    def is_acceptable(self) -> bool:
        return self.verdict in (QualityVerdict.PASSED, QualityVerdict.WARNING_SUBOPTIMAL_DENSITY)


@dataclass
class OverallQualityAuditReport:
    """Consolidated audit report across all critical architecture flows."""

    results: list[FlowQualityAuditResult]
    total_flows_evaluated: int
    all_passed: bool
    average_line_coverage_pct: float
    average_branch_coverage_pct: float
    average_assertion_density: float
    hollow_coverage_count: int
    summary_markdown: str


class CoverageQualityAuditor:
    """Audits critical flow test suites against line, branch, and assertion quality standards."""

    def __init__(
        self,
        custom_targets: dict[CriticalFlow, FlowCoverageTarget] | None = None,
    ) -> None:
        self._targets: dict[CriticalFlow, FlowCoverageTarget] = dict(DEFAULT_FLOW_TARGETS)
        if custom_targets:
            self._targets.update(custom_targets)

    def get_target(self, flow: CriticalFlow) -> FlowCoverageTarget:
        """Fetch target configuration for flow or return fallback."""
        return self._targets.get(
            flow,
            FlowCoverageTarget(
                flow=flow,
                min_line_coverage_pct=85.0,
                min_branch_coverage_pct=80.0,
                min_assertion_density=1.8,
                min_negative_case_ratio_pct=20.0,
                description=f"Genel varsayılan hedef ({flow}).",
            ),
        )

    def audit_flow(
        self,
        metrics: FlowTestMetrics,
        target: FlowCoverageTarget | None = None,
    ) -> FlowQualityAuditResult:
        """Audit a single flow against coverage targets and assertion quality principles."""
        flow_target = target or self.get_target(metrics.flow)
        issues: list[str] = []
        recommendations: list[str] = []

        line_cov = metrics.line_coverage_pct
        branch_cov = metrics.branch_coverage_pct
        density = metrics.assertion_density
        neg_ratio = metrics.negative_case_ratio_pct

        # 1. Line coverage check
        line_failed = line_cov < flow_target.min_line_coverage_pct
        if line_failed:
            issues.append(
                f"Satır kapsaması yetersiz: %{line_cov} < "
                f"hedef %{flow_target.min_line_coverage_pct}"
            )
            recommendations.append("Kapsanmayan temel iş mantığı dallarına birim testleri ekleyin.")

        # 2. Branch coverage check
        branch_failed = branch_cov < flow_target.min_branch_coverage_pct
        if branch_failed:
            issues.append(
                f"Dal kapsaması yetersiz: %{branch_cov} < "
                f"hedef %{flow_target.min_branch_coverage_pct}"
            )
            recommendations.append("Koşullu if/else bloklarının her iki yönünü de test edin.")

        # 3. Hollow coverage detection (CRITICAL: High line coverage but hollow/no assertions)
        hollow_coverage = False
        if metrics.total_tests > 0 and density < 1.0:
            hollow_coverage = True
            issues.append(
                f"YALANCI KAPSAMA TESPİTİ: Test başına ortalama {density} iddia düşüyor. "
                f"Yüksek satır kapsaması (%{line_cov}) testin doğrulama yaptığını kanıtlamaz!"
            )
            recommendations.append(
                "Test fonksiyonlarına açık `assert` ve durum doğrulamaları ekleyin."
            )
        elif metrics.total_tests > 0 and density < flow_target.min_assertion_density:
            recommendations.append(
                f"İddia yoğunluğu suboptimal ({density} < {flow_target.min_assertion_density}); "
                "daha kapsamlı kontroller önerilir."
            )

        # 4. Negative / Edge cases check
        lacks_negative = False
        if metrics.total_tests > 0 and neg_ratio < flow_target.min_negative_case_ratio_pct:
            lacks_negative = True
            issues.append(
                f"Negatif/hata testleri yetersiz: %{neg_ratio} < "
                f"hedef %{flow_target.min_negative_case_ratio_pct}"
            )
            recommendations.append(
                "Hatalı girdiler, sınır değerler ve istisnaları (pytest.raises) test edin."
            )

        # Determine verdict based on quality priorities
        if hollow_coverage:
            verdict = QualityVerdict.REJECTED_HOLLOW_COVERAGE
        elif line_failed:
            verdict = QualityVerdict.REJECTED_LOW_LINE_COVERAGE
        elif branch_failed:
            verdict = QualityVerdict.REJECTED_LOW_BRANCH_COVERAGE
        elif lacks_negative:
            verdict = QualityVerdict.REJECTED_LACK_OF_NEGATIVE_PATHS
        elif density < flow_target.min_assertion_density:
            verdict = QualityVerdict.WARNING_SUBOPTIMAL_DENSITY
        else:
            verdict = QualityVerdict.PASSED

        return FlowQualityAuditResult(
            flow=metrics.flow,
            verdict=verdict,
            target=flow_target,
            metrics=metrics,
            issues=issues,
            recommendations=recommendations,
        )

    def audit_system(
        self,
        metrics_list: Sequence[FlowTestMetrics],
    ) -> OverallQualityAuditReport:
        """Consolidate quality audits across multiple critical flows."""
        results: list[FlowQualityAuditResult] = []
        total_line_cov = 0.0
        total_branch_cov = 0.0
        total_density = 0.0
        hollow_count = 0

        for m in metrics_list:
            res = self.audit_flow(m)
            results.append(res)
            total_line_cov += m.line_coverage_pct
            total_branch_cov += m.branch_coverage_pct
            total_density += m.assertion_density
            if res.verdict == QualityVerdict.REJECTED_HOLLOW_COVERAGE:
                hollow_count += 1

        count = len(metrics_list) if metrics_list else 1
        avg_line = round(total_line_cov / count, 2)
        avg_branch = round(total_branch_cov / count, 2)
        avg_density = round(total_density / count, 2)

        all_passed = all(r.is_acceptable for r in results)

        # Render markdown summary
        rows = []
        for r in results:
            status_icon = "✅" if r.is_acceptable else "❌"
            rows.append(
                f"| {status_icon} `{r.flow.value}` | "
                f"%{r.metrics.line_coverage_pct} (Hedef: %{r.target.min_line_coverage_pct}) | "
                f"%{r.metrics.branch_coverage_pct} (Hedef: %{r.target.min_branch_coverage_pct}) | "
                f"{r.metrics.assertion_density} (Hedef: {r.target.min_assertion_density}) | "
                f"%{r.metrics.negative_case_ratio_pct} | `{r.verdict.value}` |"
            )

        header = (
            "| Kritik Akış | Satır Kapsaması | Dal Kapsaması | "
            "İddia Yoğunluğu | Negatif Test Oranı | Sonuç Kararı |\n"
            "|---|---|---|---|---|---|\n"
        )
        table = header + "\n".join(rows)

        md = (
            f"### Kritik Akışlar Kalite ve Kapsama Denetim Raporu\n\n"
            f"- **Değerlendirilen Akış Sayısı:** {len(results)}\n"
            f"- **Sistem Ortalama Satır Kapsaması:** %{avg_line}\n"
            f"- **Sistem Ortalama Dal Kapsaması:** %{avg_branch}\n"
            f"- **Ortalama İddia Yoğunluğu:** {avg_density} iddia/test\n"
            f"- **Yalancı Kapsama (Hollow Coverage) Sayısı:** {hollow_count}\n"
            f"- **Genel Kabul Durumu:** {'ONAYLANDI' if all_passed else 'REDDEDİLDİ'}\n\n"
            f"{table}\n"
        )

        return OverallQualityAuditReport(
            results=results,
            total_flows_evaluated=len(results),
            all_passed=all_passed,
            average_line_coverage_pct=avg_line,
            average_branch_coverage_pct=avg_branch,
            average_assertion_density=avg_density,
            hollow_coverage_count=hollow_count,
            summary_markdown=md,
        )

    @staticmethod
    def analyze_test_file_ast(file_path: Path | str) -> dict[str, int]:
        """Static AST inspection of a test file to compute assertion density.

        Extracts:
        - `test_functions`: Total test function definitions
        - `assert_statements`: Total `assert` expressions
        - `pytest_raises_contexts`: Total `pytest.raises` exception testing blocks
        - `total_assertions`: Sum of asserts and raises checks
        """
        path = Path(file_path)
        if not path.is_file():
            return {
                "test_functions": 0,
                "assert_statements": 0,
                "pytest_raises_contexts": 0,
                "total_assertions": 0,
            }

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            return {
                "test_functions": 0,
                "assert_statements": 0,
                "pytest_raises_contexts": 0,
                "total_assertions": 0,
            }

        test_functions = 0
        assert_statements = 0
        pytest_raises_contexts = 0

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_") or node.name.endswith("_test"):
                    test_functions += 1
            elif isinstance(node, ast.Assert):
                assert_statements += 1
            elif isinstance(node, ast.With):
                for item in node.items:
                    expr = item.context_expr
                    if isinstance(expr, ast.Call):
                        func = expr.func
                        # Match pytest.raises(...)
                        if isinstance(func, ast.Attribute) and func.attr == "raises":
                            pytest_raises_contexts += 1
                        elif isinstance(func, ast.Name) and func.id == "raises":
                            pytest_raises_contexts += 1

        total_assertions = assert_statements + pytest_raises_contexts
        return {
            "test_functions": test_functions,
            "assert_statements": assert_statements,
            "pytest_raises_contexts": pytest_raises_contexts,
            "total_assertions": total_assertions,
        }
