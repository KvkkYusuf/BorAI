"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 2 (Antigravity kuralları + Git + CI) test paketi.

Test kapsamı:
- RoadmapStepTwoGatekeeper 6/6 tam uyum doğrulaması.
- AGENTS.md çekirdek kuralları, token verimliliği ve 5 satırlık plan standardı.
- .agent/rules/ (5 kural) ve .agent/workflows/ (4 iş akışı) modüler yapısı.
- Git deposu ve .gitignore sır (secret/.env/venv) izolasyonu.
- GitHub Actions CI iş akışı (gitleaks, lockfile, sahte paket, scanner, ruff, mypy, pytest).
- Bağımlılık sabitleme (pyproject.toml + uv.lock).
- Antigravity kurulum dokümantasyonu ve token bütçe ilkeleri.
- Telemetri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP2_DISCLAIMER,
    ROADMAP_STEP2_CHECKS_TOTAL,
    ROADMAP_STEP2_PASSED_TOTAL,
    ROADMAP_STEP2_STATUS,
    RoadmapStepTwoGatekeeper,
    RoadmapStepTwoStatus,
    generate_roadmap_step2_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


def test_roadmap_step2_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 2 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepTwoStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step2_agents_core_rules(workspace_root: Path) -> None:
    """AGENTS.md çekirdek kural dosyasının içeriğini ve ilkelerini denetler."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    agents_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S2-01-AGENTS-CORE-RULES"
    )
    assert agents_check.passed is True
    assert agents_check.details.get("has_token") is True
    assert agents_check.details.get("has_red_lines") is True
    assert agents_check.details.get("has_plan_rule") is True
    assert agents_check.details.get("has_honest_rule") is True


def test_roadmap_step2_agent_topic_rules_and_workflows(workspace_root: Path) -> None:
    """.agent altındaki tüm konu kuralları ve iş akışlarının eksiksiz olduğunu doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    rules_check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S2-02-AGENT-TOPIC-RULES-AND-WORKFLOWS"
    )
    assert rules_check.passed is True
    assert len(rules_check.details.get("rules_found", [])) == 5
    assert len(rules_check.details.get("workflows_found", [])) == 4


def test_roadmap_step2_git_and_ignore_configuration(workspace_root: Path) -> None:
    """Git deposu ve .gitignore sır koruma ayarlarını doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    git_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S2-03-GIT-SECURITY-AND-IGNORE"
    )
    assert git_check.passed is True
    assert git_check.details.get("has_git") is True
    assert git_check.details.get("has_env_ignore") is True
    assert git_check.details.get("has_venv_ignore") is True


def test_roadmap_step2_ci_pipeline_configuration(workspace_root: Path) -> None:
    """GitHub Actions CI iş akışı adımlarının eksiksiz tanımlandığını doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    ci_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S2-04-CI-WORKFLOW-VALIDATION"
    )
    assert ci_check.passed is True
    assert ci_check.details.get("has_gitleaks") is True
    assert ci_check.details.get("has_lock_check") is True
    assert ci_check.details.get("has_dep_audit") is True
    assert ci_check.details.get("has_security_scanners") is True
    assert ci_check.details.get("has_ruff") is True
    assert ci_check.details.get("has_mypy") is True
    assert ci_check.details.get("has_pytest") is True


def test_roadmap_step2_dependency_locking(workspace_root: Path) -> None:
    """pyproject.toml ve uv.lock bağımlılık kilitlemesini doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    lock_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S2-05-DEPENDENCY-LOCKING"
    )
    assert lock_check.passed is True
    assert lock_check.details.get("has_pyproject") is True
    assert lock_check.details.get("has_lock") is True


def test_roadmap_step2_antigravity_setup_docs(workspace_root: Path) -> None:
    """Antigravity kurulum rehberi ve token politikası dokümantasyonunu doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    docs_check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S2-06-ANTIGRAVITY-SETUP-DOCS"
    )
    assert docs_check.passed is True
    assert docs_check.details.get("has_setup") is True
    assert docs_check.details.get("has_token") is True


def test_roadmap_step2_markdown_report_generation(workspace_root: Path) -> None:
    """Markdown denetim raporunun eksiksiz ve geçerli üretildiğini doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    report_md = generate_roadmap_step2_report(result)

    assert "# FAZ-18 Yol Haritası Uygulama Sırası — Adım 2" in report_md
    assert "PASS" in report_md
    assert "ROADMAP-S2-01-AGENTS-CORE-RULES" in report_md
    assert "ROADMAP-S2-06-ANTIGRAVITY-SETUP-DOCS" in report_md
    assert DEFAULT_ROADMAP_STEP2_DISCLAIMER in report_md


def test_roadmap_step2_failure_on_empty_workspace(tmp_path: Path) -> None:
    """Boş bir dizinde çalıştırıldığında kapının FAIL verdiğini doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(
        workspace_root=tmp_path,
        disclaimer=DEFAULT_ROADMAP_STEP2_DISCLAIMER,
    )
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepTwoStatus.FAIL
    assert result.passed_checks == 0
    assert result.failed_checks == 6
    assert result.to_dict()["status"] == "FAIL"


def test_roadmap_step2_telemetry_metrics(workspace_root: Path) -> None:
    """Prometheus telemetri sayaç ve göstergelerinin güncellendiğini doğrular."""
    gatekeeper = RoadmapStepTwoGatekeeper(workspace_root=workspace_root)
    initial_checks = ROADMAP_STEP2_CHECKS_TOTAL.get()
    initial_passed = ROADMAP_STEP2_PASSED_TOTAL.get()

    result = gatekeeper.evaluate()

    assert ROADMAP_STEP2_STATUS.get() == 1.0
    assert ROADMAP_STEP2_CHECKS_TOTAL.get() == initial_checks + result.total_checks
    assert ROADMAP_STEP2_PASSED_TOTAL.get() == initial_passed + result.passed_checks
