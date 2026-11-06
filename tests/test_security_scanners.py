"""Tests for Dependency, Container, and SAST Code Security Scanners."""

from pathlib import Path

from stock_analysis.security.scanners import (
    CodeSecurityScanner,
    ContainerSecurityScanner,
    DependencyScanner,
    SecurityScannerSuite,
)


class TestDependencyScanner:
    """Test suite for DependencyScanner and CycloneDX SBOM generation."""

    def test_scan_real_project_manifest(self) -> None:
        scanner = DependencyScanner()
        report = scanner.scan_dependencies(
            pyproject_path=Path("pyproject.toml"),
            lockfile_path=Path("uv.lock"),
        )
        assert report.total_packages_checked > 0
        assert report.lockfile_valid is True
        assert report.sbom_generated is True
        assert report.passed is True
        assert report.critical_count == 0
        assert report.high_count == 0

    def test_detect_typosquatted_banned_package(self, tmp_path: Path) -> None:
        malicious_manifest = tmp_path / "pyproject.toml"
        malicious_manifest.write_text(
            """[project]
dependencies = [
    "fast-api>=0.115.0",
    "reqeusts>=2.31.0",
]
""",
            encoding="utf-8",
        )
        scanner = DependencyScanner()
        report = scanner.scan_dependencies(malicious_manifest)
        assert report.passed is False
        assert report.critical_count >= 1
        suspicious_names = [i.package_name for i in report.issues if i.severity == "CRITICAL"]
        assert "fast-api" in suspicious_names or "reqeusts" in suspicious_names

    def test_detect_unpinned_dependency(self, tmp_path: Path) -> None:
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text(
            """[project]
dependencies = [
    "unpinned-library",
]
""",
            encoding="utf-8",
        )
        scanner = DependencyScanner()
        report = scanner.scan_dependencies(manifest)
        unpinned = [i for i in report.issues if i.issue_type == "unpinned_dependency"]
        assert len(unpinned) == 1
        assert unpinned[0].severity == "MEDIUM"

    def test_detect_known_vulnerable_package(self, tmp_path: Path) -> None:
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text(
            """[project]
dependencies = [
    "urllib3==1.26.15",
]
""",
            encoding="utf-8",
        )
        scanner = DependencyScanner()
        report = scanner.scan_dependencies(manifest)
        vuln_issues = [i for i in report.issues if i.cve_id == "CVE-2023-43804"]
        assert len(vuln_issues) == 1
        assert vuln_issues[0].severity == "HIGH"
        assert report.passed is False

    def test_missing_manifest_handling(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist.toml"
        scanner = DependencyScanner()
        report = scanner.scan_dependencies(non_existent)
        assert report.passed is False
        assert report.lockfile_valid is False
        assert report.critical_count == 1

    def test_generate_cyclonedx_sbom(self) -> None:
        scanner = DependencyScanner()
        sbom = scanner.generate_sbom("pyproject.toml", "uv.lock")
        assert sbom["bomFormat"] == "CycloneDX"
        assert sbom["specVersion"] == "1.5"
        assert sbom["metadata"]["component"]["name"] == "stock-analysis"
        components = sbom["components"]
        assert len(components) > 0
        comp_names = [c["name"] for c in components]
        assert "fastapi" in comp_names
        assert "torch" in comp_names


class TestContainerSecurityScanner:
    """Test suite for ContainerSecurityScanner and CIS Docker Benchmarks."""

    def test_scan_actual_project_dockerfile(self) -> None:
        scanner = ContainerSecurityScanner()
        report = scanner.scan_file("Dockerfile")
        assert report.rules_checked >= 7
        assert report.critical_count == 0
        assert report.high_count == 0
        assert report.passed is True

    def test_detect_root_user_violation(self) -> None:
        bad_dockerfile = """
FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
COPY . .
# Missing USER directive or running as root
CMD ["python", "app.py"]
"""
        scanner = ContainerSecurityScanner()
        report = scanner.scan_dockerfile_content(bad_dockerfile)
        assert report.passed is False
        assert any(i.rule_id == "CIS-DI-001" for i in report.issues)

    def test_detect_latest_tag_violation(self) -> None:
        bad_dockerfile = """
FROM python:latest
USER 10001
HEALTHCHECK CMD curl http://localhost/health
CMD ["python"]
"""
        scanner = ContainerSecurityScanner()
        report = scanner.scan_dockerfile_content(bad_dockerfile)
        assert report.passed is False
        assert any(i.rule_id == "CIS-DI-002" for i in report.issues)

    def test_detect_sensitive_credential_copy(self) -> None:
        bad_dockerfile = """
FROM python:3.12-slim-bookworm
COPY .env /app/.env
COPY id_rsa /root/.ssh/id_rsa
USER 10001
HEALTHCHECK CMD curl http://localhost/health
CMD ["python"]
"""
        scanner = ContainerSecurityScanner()
        report = scanner.scan_dockerfile_content(bad_dockerfile)
        assert report.critical_count >= 2
        assert report.passed is False
        crit_issues = [i for i in report.issues if i.rule_id == "CIS-DI-003"]
        assert len(crit_issues) == 2

    def test_detect_dangerous_pipe_to_shell(self) -> None:
        bad_dockerfile = """
FROM python:3.12-slim-bookworm
RUN curl -fsSL https://example.com/install.sh | bash
USER 10001
HEALTHCHECK CMD curl http://localhost/health
CMD ["python"]
"""
        scanner = ContainerSecurityScanner()
        report = scanner.scan_dockerfile_content(bad_dockerfile)
        assert report.critical_count >= 1
        assert any(i.rule_id == "CIS-DI-007" for i in report.issues)

    def test_missing_dockerfile_handled(self, tmp_path: Path) -> None:
        scanner = ContainerSecurityScanner()
        report = scanner.scan_file(tmp_path / "NonExistentDockerfile")
        assert report.passed is False
        assert report.high_count == 1


class TestCodeSecurityScanner:
    """Test suite for SAST AST-based CodeSecurityScanner."""

    def test_clean_source_code_passes(self) -> None:
        clean_code = """
import hashlib

def calculate_checksum(data: bytes) -> str:
    # Used for non-security data integrity
    return hashlib.md5(data, used_for_security=False).hexdigest()

def compute_total(a: int, b: int) -> int:
    return a + b
"""
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(clean_code, file_path="clean_file.py")
        assert report.passed is True
        assert len(report.issues) == 0

    def test_detect_eval_and_exec(self) -> None:
        vulnerable_code = """
def run_dynamic(user_input: str):
    eval(user_input)
    exec("import os")
"""
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(vulnerable_code, file_path="vuln.py")
        assert report.passed is False
        assert report.critical_count == 2
        assert all(i.rule_id == "SAST-001" for i in report.issues)

    def test_detect_unsafe_pickle_deserialization(self) -> None:
        vulnerable_code = """
import pickle

def load_payload(raw_bytes: bytes):
    return pickle.loads(raw_bytes)
"""
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(vulnerable_code, file_path="vuln_pickle.py")
        assert report.passed is False
        assert report.critical_count == 1
        assert report.issues[0].rule_id == "SAST-002"

    def test_detect_command_injection(self) -> None:
        vulnerable_code = """
import subprocess
import os

def run_cmd(arg: str):
    subprocess.Popen(f"echo {arg}", shell=True)
    os.system("ls -la")
"""
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(vulnerable_code, file_path="cmd_inject.py")
        assert report.high_count == 2
        assert all(i.rule_id == "SAST-003" for i in report.issues)

    def test_detect_weak_crypto_md5_without_security_flag(self) -> None:
        vulnerable_code = """
import hashlib

def create_hash(token: bytes):
    return hashlib.md5(token).hexdigest()
"""
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(vulnerable_code, file_path="crypto.py")
        assert report.medium_count == 1
        assert report.issues[0].rule_id == "SAST-004"

    def test_detect_hardcoded_secret(self) -> None:
        vulnerable_code = """
def connect():
    api_key = "sk_live_992102910291029102"
    return api_key
"""
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(vulnerable_code, file_path="api/client.py")
        assert report.high_count == 1
        assert report.issues[0].rule_id == "SAST-005"

    def test_syntax_error_handled_gracefully(self) -> None:
        broken_code = "def broken_syntax(:\n   pass"
        scanner = CodeSecurityScanner()
        report = scanner.scan_source(broken_code, file_path="broken.py")
        assert len(report.issues) == 1
        assert report.issues[0].rule_id == "SAST-SYNTAX"


class TestSecurityScannerSuite:
    """Test suite for SecurityScannerSuite orchestrator and integration."""

    def test_suite_runs_against_project_repository(self) -> None:
        suite = SecurityScannerSuite()
        report = suite.run_all(
            pyproject_path="pyproject.toml",
            lockfile_path="uv.lock",
            dockerfile_path="Dockerfile",
            source_dir="src",
        )
        assert report.dependency_report.passed is True
        assert report.container_report is not None
        assert report.container_report.passed is True
        assert report.code_report.passed is True
        assert report.total_critical == 0
        assert report.total_high == 0
        assert report.passed is True
        assert report.exit_code == 0

    def test_summary_dict_export(self) -> None:
        suite = SecurityScannerSuite()
        report = suite.run_all(
            pyproject_path="pyproject.toml",
            lockfile_path="uv.lock",
            dockerfile_path="Dockerfile",
            source_dir="src",
        )
        data = report.to_summary_dict()
        assert data["passed"] is True
        assert data["exit_code"] == 0
        assert data["summary"]["critical"] == 0
        assert "dependency_scan" in data
        assert "container_scan" in data
        assert "code_scan" in data
        assert data["dependency_scan"]["lockfile_valid"] is True
        assert data["dependency_scan"]["sbom_generated"] is True
