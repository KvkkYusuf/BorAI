"""Tests for threat modeling, coverage guarantees, and supply chain dependency auditing."""

from pathlib import Path

import pytest

from stock_analysis.security.threat_model import (
    MitigationStatus,
    PackageDependencyAuditor,
    SeverityLevel,
    STRIDEMapping,
    SupplyChainSecurityError,
    ThreatCategory,
    ThreatEntry,
    ThreatModelRegistry,
    get_default_threat_registry,
)


def test_default_threat_registry_coverage() -> None:
    """Verify that default registry meets all FAZ-13 mandatory categories."""
    registry = get_default_threat_registry()
    coverage = registry.validate_required_coverage()

    # Verify all mandatory categories are represented with at least 1 implemented mitigation
    assert coverage[ThreatCategory.ACCOUNT_TAKEOVER] >= 2
    assert coverage[ThreatCategory.DATA_LEAKAGE] >= 2
    assert coverage[ThreatCategory.API_ABUSE] >= 2
    assert coverage[ThreatCategory.PRIVILEGE_ESCALATION] >= 2
    assert coverage[ThreatCategory.SUPPLY_CHAIN] >= 2

    # Verify total threats count
    all_threats = registry.all_threats()
    assert len(all_threats) >= 10

    # Ensure every threat has a valid verification component and STRIDE mapping
    for threat in all_threats:
        assert threat.threat_id.startswith("THR-")
        assert len(threat.mitigations) > 0
        assert threat.verification_component != ""
        assert threat.status == MitigationStatus.IMPLEMENTED


def test_threat_registry_queries_and_filters() -> None:
    """Test registry lookups, filters, and duplicate prevention."""
    registry = get_default_threat_registry()

    # Specific ID lookup
    entry = registry.get("THR-ATO-01")
    assert entry.name == "Brute-Force & Credential Stuffing"
    assert entry.category == ThreatCategory.ACCOUNT_TAKEOVER
    assert entry.stride == STRIDEMapping.SPOOFING

    # Non-existent ID lookup
    with pytest.raises(KeyError, match="not found"):
        registry.get("THR-NON-EXISTENT")

    # Duplicate registration prevention
    with pytest.raises(ValueError, match="already registered"):
        registry.register(entry)

    # Filter by category
    ato_threats = registry.filter_by_category(ThreatCategory.ACCOUNT_TAKEOVER)
    assert len(ato_threats) >= 2
    assert all(t.category == ThreatCategory.ACCOUNT_TAKEOVER for t in ato_threats)

    # Filter by severity
    critical_threats = registry.filter_by_severity(SeverityLevel.CRITICAL)
    assert len(critical_threats) >= 3
    assert all(t.severity == SeverityLevel.CRITICAL for t in critical_threats)


def test_threat_registry_coverage_validation_failure() -> None:
    """Test that registry properly detects missing mandatory categories or unmitigated risks."""
    custom_registry = ThreatModelRegistry()

    # Only one category registered
    custom_registry.register(
        ThreatEntry(
            threat_id="THR-CUSTOM-01",
            name="Custom ATO Threat",
            category=ThreatCategory.ACCOUNT_TAKEOVER,
            stride=STRIDEMapping.SPOOFING,
            severity=SeverityLevel.HIGH,
            description="desc",
            attack_vector="vector",
            impact="impact",
            mitigations=("mitigation 1",),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="test.auth",
        )
    )

    with pytest.raises(ValueError, match="Threat model coverage incomplete"):
        custom_registry.validate_required_coverage()

    # If category is present but only has PLANNED mitigation
    custom_registry.register(
        ThreatEntry(
            threat_id="THR-CUSTOM-02",
            name="Custom Leak Threat",
            category=ThreatCategory.DATA_LEAKAGE,
            stride=STRIDEMapping.INFORMATION_DISCLOSURE,
            severity=SeverityLevel.HIGH,
            description="desc",
            attack_vector="vector",
            impact="impact",
            mitigations=("mitigation 1",),
            status=MitigationStatus.PLANNED,
            verification_component="test.leak",
        )
    )

    with pytest.raises(ValueError, match="no implemented mitigation"):
        custom_registry.validate_required_coverage()


def test_markdown_table_generation() -> None:
    """Verify that generated markdown table includes all required columns and threats."""
    registry = get_default_threat_registry()
    table = registry.generate_markdown_table()

    assert "| Tehdit Kodu | Kategori | STRIDE |" in table
    assert "`THR-ATO-01`" in table
    assert "`THR-SUP-01`" in table
    assert "src.stock_analysis.security.threat_model" in table


def test_parse_package_name() -> None:
    """Test PEP 508 dependency specification parsing."""
    auditor = PackageDependencyAuditor()

    assert auditor.parse_package_name("fastapi>=0.115.0") == "fastapi"
    assert auditor.parse_package_name("uvicorn[standard]>=0.32.0") == "uvicorn"
    assert auditor.parse_package_name("pytest-asyncio>=0.24.0") == "pytest-asyncio"
    assert auditor.parse_package_name("pydantic-settings>=2.5.0") == "pydantic-settings"
    assert auditor.parse_package_name("torch>=2.5.0") == "torch"
    assert auditor.parse_package_name("pandas-stubs>=2.2.0") == "pandas-stubs"


def test_audit_actual_project_dependencies() -> None:
    """Audit project's actual pyproject.toml and uv.lock files."""
    repo_root = Path(__file__).parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    lockfile_path = repo_root / "uv.lock"

    assert pyproject_path.exists(), f"pyproject.toml missing at {pyproject_path}"
    assert lockfile_path.exists(), f"uv.lock missing at {lockfile_path}"

    auditor = PackageDependencyAuditor()
    result = auditor.audit_pyproject(
        pyproject_path=pyproject_path,
        lockfile_path=lockfile_path,
    )

    assert result.is_valid, f"Project audit failed with issues: {result.flagged_issues}"
    assert result.total_dependencies >= 10
    assert "fastapi" in result.inspected_packages
    assert "torch" in result.inspected_packages
    assert "pytest" in result.inspected_packages
    assert len(result.suspicious_packages) == 0


def test_detect_hallucinated_and_typosquatted_packages(tmp_path: Path) -> None:
    """Test detection of fake or AI-hallucinated packages."""
    fake_pyproject = tmp_path / "pyproject.toml"
    fake_pyproject.write_text(
        """
[project]
name = "test-pkg"
version = "0.1.0"
dependencies = [
    "fastapi>=0.115.0",
    "fast-api-core>=1.0.0",
    "reqeusts>=2.31.0",
    "torch-gpu-helper>=0.1.0",
    "invalid$$name>=1.0",
]
""",
        encoding="utf-8",
    )

    auditor = PackageDependencyAuditor()
    result = auditor.audit_pyproject(pyproject_path=fake_pyproject)

    assert not result.is_valid
    assert len(result.suspicious_packages) >= 3
    assert any("fast-api-core" in s for s in result.suspicious_packages)
    assert any("reqeusts" in s for s in result.suspicious_packages)
    assert any("torch-gpu-helper" in s for s in result.suspicious_packages)
    assert any("Invalid package naming syntax" in issue for issue in result.flagged_issues)


def test_audit_pyproject_file_not_found(tmp_path: Path) -> None:
    """Verify appropriate error on missing pyproject file."""
    auditor = PackageDependencyAuditor()
    missing_path = tmp_path / "non_existent.toml"

    with pytest.raises(FileNotFoundError):
        auditor.audit_pyproject(pyproject_path=missing_path)


def test_audit_pyproject_corrupted_file(tmp_path: Path) -> None:
    """Verify appropriate error on corrupted pyproject toml."""
    corrupt_path = tmp_path / "corrupt.toml"
    corrupt_path.write_text("invalid toml [[[]", encoding="utf-8")

    auditor = PackageDependencyAuditor()
    with pytest.raises(SupplyChainSecurityError, match="Failed to parse"):
        auditor.audit_pyproject(pyproject_path=corrupt_path)


def test_audit_with_strict_allowlist(tmp_path: Path) -> None:
    """Verify allowlist enforcement when specified."""
    fake_pyproject = tmp_path / "pyproject.toml"
    fake_pyproject.write_text(
        """
[project]
name = "test-pkg"
version = "0.1.0"
dependencies = [
    "fastapi>=0.115.0",
    "unapproved-lib>=1.0.0",
]
""",
        encoding="utf-8",
    )

    auditor = PackageDependencyAuditor(allowed_packages={"fastapi"})
    result = auditor.audit_pyproject(pyproject_path=fake_pyproject)

    assert not result.is_valid
    assert any("not in approved allowlist" in issue for issue in result.flagged_issues)
    assert "unapproved-lib" in result.suspicious_packages
