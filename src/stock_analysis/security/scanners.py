"""Security scanning suite for dependencies, containers, and source code (SAST).

Provides:
- DependencyScanner: Verifies lockfile integrity, checks known vulnerable patterns, and generates
  CycloneDX v1.5 standard SBOMs.
- ContainerSecurityScanner: Evaluates Dockerfile against CIS Docker Benchmarks and container rules.
- CodeSecurityScanner: AST-based static application security testing (SAST) for dangerous functions,
  injections, and hardcoded secrets.
- SecurityScannerSuite: Unified orchestrator for multi-vector security verification and CI/CD.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ==============================================================================
# 1. Dependency Scanner & SBOM Generator
# ==============================================================================


@dataclass(frozen=True)
class DependencyScanIssue:
    """Represents a security issue found in dependencies."""

    package_name: str
    current_version: str
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    issue_type: str  # 'vulnerability', 'unpinned_dependency', 'suspicious_package'
    description: str
    recommendation: str
    cve_id: str | None = None


@dataclass
class DependencyScanReport:
    """Results from dependency security scan."""

    total_packages_checked: int
    issues: list[DependencyScanIssue] = field(default_factory=list)
    lockfile_valid: bool = True
    sbom_generated: bool = False
    sbom_data: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        """Returns True if no CRITICAL or HIGH issues exist and lockfile is valid."""
        if not self.lockfile_valid:
            return False
        return not any(i.severity in ("CRITICAL", "HIGH") for i in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "HIGH")

    @property
    def medium_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "MEDIUM")

    @property
    def low_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "LOW")


class DependencyScanner:
    """Scans project dependency manifests and lockfiles for security risks."""

    # Known vulnerable patterns or CVE database for automated verification
    KNOWN_VULNERABLE_PACKAGES: dict[str, list[dict[str, str]]] = {
        "urllib3": [
            {
                "max_version": "1.26.17",
                "cve": "CVE-2023-43804",
                "severity": "HIGH",
                "description": "Cookie leak on cross-origin redirects",
            }
        ],
        "jinja2": [
            {
                "max_version": "3.1.2",
                "cve": "CVE-2024-22195",
                "severity": "MEDIUM",
                "description": "HTML attribute injection risk in xmlattr filter",
            }
        ],
        "requests": [
            {
                "max_version": "2.31.0",
                "cve": "CVE-2024-35195",
                "severity": "MEDIUM",
                "description": "Leaked session credentials across HTTPS redirect",
            }
        ],
        "aiohttp": [
            {
                "max_version": "3.8.5",
                "cve": "CVE-2023-47627",
                "severity": "HIGH",
                "description": "HTTP request smuggling via improper header parsing",
            }
        ],
    }

    # Banned / suspicious typo-squatting packages
    BANNED_TYPO_PACKAGES: set[str] = {
        "fast-api",
        "reqeusts",
        "torch-gpu-helper",
        "pydantic-v3",
        "python-date-util",
    }

    def scan_dependencies(
        self,
        pyproject_path: Path | str,
        lockfile_path: Path | str | None = None,
    ) -> DependencyScanReport:
        """Inspects pyproject.toml and lockfile for vulnerabilities and unpinned specs."""
        pyproj_p = Path(pyproject_path)
        issues: list[DependencyScanIssue] = []
        packages_checked = 0
        lockfile_valid = True

        if not pyproj_p.exists():
            return DependencyScanReport(
                total_packages_checked=0,
                issues=[
                    DependencyScanIssue(
                        package_name="pyproject.toml",
                        current_version="none",
                        severity="CRITICAL",
                        issue_type="missing_manifest",
                        description=f"Manifest not found at {pyproj_p}",
                        recommendation="Ensure pyproject.toml exists in repository root.",
                    )
                ],
                lockfile_valid=False,
            )

        content = pyproj_p.read_text(encoding="utf-8")
        dep_lines: list[str] = []
        in_deps_section = False

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("dependencies = ["):
                in_deps_section = True
                continue
            if in_deps_section:
                if stripped.startswith("]"):
                    in_deps_section = False
                    continue
                clean = stripped.strip('",')
                if clean and not clean.startswith("#"):
                    dep_lines.append(clean)

        for dep_spec in dep_lines:
            packages_checked += 1
            match = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)$", dep_spec)
            if not match:
                continue
            pkg_name = match.group(1).lower().strip()
            spec = match.group(2).strip()

            if pkg_name in self.BANNED_TYPO_PACKAGES:
                issues.append(
                    DependencyScanIssue(
                        package_name=pkg_name,
                        current_version=spec or "unspecified",
                        severity="CRITICAL",
                        issue_type="suspicious_package",
                        description=(f"Package '{pkg_name}' matches a known malicious package."),
                        recommendation=(
                            f"Remove '{pkg_name}' immediately and verify repository integrity."
                        ),
                    )
                )

            if not spec:
                issues.append(
                    DependencyScanIssue(
                        package_name=pkg_name,
                        current_version="unpinned",
                        severity="MEDIUM",
                        issue_type="unpinned_dependency",
                        description=f"Dependency '{pkg_name}' has no version constraint.",
                        recommendation=f"Pin '{pkg_name}' with minimum version, e.g. '>=1.0.0'.",
                    )
                )

            if pkg_name in self.KNOWN_VULNERABLE_PACKAGES:
                for vuln in self.KNOWN_VULNERABLE_PACKAGES[pkg_name]:
                    issues.append(
                        DependencyScanIssue(
                            package_name=pkg_name,
                            current_version=spec,
                            severity=vuln["severity"],
                            issue_type="vulnerability",
                            cve_id=vuln["cve"],
                            description=f"{vuln['cve']}: {vuln['description']}",
                            recommendation=(
                                f"Update '{pkg_name}' to version > {vuln['max_version']}."
                            ),
                        )
                    )

        if lockfile_path is not None:
            lock_p = Path(lockfile_path)
            if not lock_p.exists():
                lockfile_valid = False
                issues.append(
                    DependencyScanIssue(
                        package_name="lockfile",
                        current_version="missing",
                        severity="HIGH",
                        issue_type="missing_lockfile",
                        description=f"Lockfile not found at {lock_p}",
                        recommendation="Generate and commit uv.lock using 'uv lock'.",
                    )
                )
            else:
                lock_text = lock_p.read_text(encoding="utf-8")
                if "version = 1" not in lock_text and "version = 2" not in lock_text:
                    lockfile_valid = False
                    issues.append(
                        DependencyScanIssue(
                            package_name="lockfile",
                            current_version="invalid",
                            severity="HIGH",
                            issue_type="corrupt_lockfile",
                            description="Lockfile header format is corrupt or unrecognized.",
                            recommendation="Regenerate uv.lock with 'uv lock'.",
                        )
                    )

        sbom_data = self.generate_sbom(pyproj_p, lockfile_path)

        return DependencyScanReport(
            total_packages_checked=packages_checked,
            issues=issues,
            lockfile_valid=lockfile_valid,
            sbom_generated=bool(sbom_data),
            sbom_data=sbom_data,
        )

    def generate_sbom(
        self,
        pyproject_path: Path | str,
        lockfile_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Generates a Software Bill of Materials (SBOM) adhering to CycloneDX v1.5 JSON."""
        pyproj_p = Path(pyproject_path)
        components: list[dict[str, Any]] = []

        if pyproj_p.exists():
            content = pyproj_p.read_text(encoding="utf-8")
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("dependencies = ["):
                    in_deps = True
                    continue
                if in_deps:
                    if stripped.startswith("]"):
                        in_deps = False
                        continue
                    clean = stripped.strip('",')
                    if clean and not clean.startswith("#"):
                        match = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)$", clean)
                        if match:
                            pkg_name = match.group(1).lower()
                            ver_constraint = match.group(2).strip()
                            components.append(
                                {
                                    "type": "library",
                                    "name": pkg_name,
                                    "version": ver_constraint or "latest",
                                    "purl": f"pkg:pypi/{pkg_name}",
                                    "scope": "required",
                                }
                            )

        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "metadata": {
                "component": {
                    "type": "application",
                    "name": "stock-analysis",
                    "version": "0.1.0",
                },
                "tools": [{"vendor": "BorAı", "name": "SecurityScannerSuite", "version": "1.0.0"}],
            },
            "components": components,
        }


# ==============================================================================
# 2. Container Security Scanner (CIS Docker & Best Practices)
# ==============================================================================


@dataclass(frozen=True)
class ContainerScanIssue:
    """Represents a Dockerfile / Container configuration security issue."""

    rule_id: str
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    instruction: str
    description: str
    recommendation: str
    line_number: int | None = None


@dataclass
class ContainerScanReport:
    """Results from container security scan."""

    target: str
    rules_checked: int
    issues: list[ContainerScanIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Returns True if no CRITICAL or HIGH issues are detected."""
        return not any(i.severity in ("CRITICAL", "HIGH") for i in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "HIGH")

    @property
    def medium_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "MEDIUM")

    @property
    def low_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "LOW")


class ContainerSecurityScanner:
    """Scans Dockerfiles and Container configs for rootless execution, pinning, and hardening."""

    RULES = [
        "CIS-DI-001: Non-root user directive present",
        "CIS-DI-002: Base image immutable version tag",
        "CIS-DI-003: No sensitive files copied into image",
        "CIS-DI-004: Multi-stage build separation",
        "CIS-DI-005: HEALTHCHECK directive present",
        "CIS-DI-006: Package cache cleanup",
        "CIS-DI-007: Dangerous commands or pipes to shell",
    ]

    SENSITIVE_FILE_PATTERNS = [
        r"\.env($|\s)",
        r"\.key($|\s)",
        r"\.pem($|\s)",
        r"id_rsa",
        r"credentials\.json",
        r"secret.*\.json",
    ]

    def scan_dockerfile_content(
        self, content: str, filename: str = "Dockerfile"
    ) -> ContainerScanReport:
        """Evaluates Dockerfile instructions against container security rules."""
        issues: list[ContainerScanIssue] = []
        lines = content.splitlines()

        has_user = False
        user_is_root = False
        from_count = 0
        has_healthcheck = False
        has_apt_clean = False
        has_apt_get = False

        for idx, line in enumerate(lines, start=1):
            raw_line = line.strip()
            if not raw_line or raw_line.startswith("#"):
                continue

            if raw_line.upper().startswith("FROM "):
                from_count += 1
                parts = raw_line.split()
                if len(parts) >= 2:
                    image_ref = parts[1]
                    if ":" not in image_ref or image_ref.endswith(":latest"):
                        issues.append(
                            ContainerScanIssue(
                                rule_id="CIS-DI-002",
                                severity="HIGH",
                                line_number=idx,
                                instruction=raw_line,
                                description=(
                                    f"Base image '{image_ref}' uses latest or unpinned tag."
                                ),
                                recommendation=(
                                    "Pin base image to specific version (e.g. "
                                    "'python:3.12-slim-bookworm')."
                                ),
                            )
                        )

            if raw_line.upper().startswith("USER "):
                has_user = True
                user_val = raw_line.split(maxsplit=1)[1].strip()
                if user_val in ("root", "0", "0:0"):
                    user_is_root = True

            if raw_line.upper().startswith("COPY ") or raw_line.upper().startswith("ADD "):
                for pattern in self.SENSITIVE_FILE_PATTERNS:
                    if re.search(pattern, raw_line, re.IGNORECASE):
                        issues.append(
                            ContainerScanIssue(
                                rule_id="CIS-DI-003",
                                severity="CRITICAL",
                                line_number=idx,
                                instruction=raw_line,
                                description="Sensitive file or key copied directly into image.",
                                recommendation=(
                                    "Inject secrets at runtime via secret managers or environment."
                                ),
                            )
                        )

            if raw_line.upper().startswith("HEALTHCHECK "):
                has_healthcheck = True

            if "apt-get" in raw_line:
                has_apt_get = True
            if "rm -rf /var/lib/apt/lists" in raw_line or "apt-get clean" in raw_line:
                has_apt_clean = True

            if re.search(r"curl\s+.*\|\s*(sh|bash)", raw_line) or re.search(
                r"wget\s+.*\|\s*(sh|bash)", raw_line
            ):
                issues.append(
                    ContainerScanIssue(
                        rule_id="CIS-DI-007",
                        severity="CRITICAL",
                        line_number=idx,
                        instruction=raw_line,
                        description="Piping curl or wget directly into shell execution.",
                        recommendation="Download script, verify SHA-256 hash, then execute.",
                    )
                )

            if "sudo " in raw_line:
                issues.append(
                    ContainerScanIssue(
                        rule_id="CIS-DI-007",
                        severity="HIGH",
                        line_number=idx,
                        instruction=raw_line,
                        description="Usage of 'sudo' inside container build.",
                        recommendation="Avoid installing or invoking sudo in container.",
                    )
                )

        if not has_user or user_is_root:
            issues.append(
                ContainerScanIssue(
                    rule_id="CIS-DI-001",
                    severity="HIGH",
                    instruction="USER <unprivileged_user>",
                    description="Container runs as root user by default.",
                    recommendation="Add 'USER appuser' or 'USER 10001:10001' before ENTRYPOINT.",
                )
            )

        if from_count < 2:
            issues.append(
                ContainerScanIssue(
                    rule_id="CIS-DI-004",
                    severity="LOW",
                    instruction="FROM ... AS builder",
                    description="Single-stage Dockerfile builds runtime in compiler stage.",
                    recommendation="Adopt multi-stage build (builder + runtime) to minimize size.",
                )
            )

        if not has_healthcheck:
            issues.append(
                ContainerScanIssue(
                    rule_id="CIS-DI-005",
                    severity="MEDIUM",
                    instruction="HEALTHCHECK",
                    description="Container image does not define a HEALTHCHECK directive.",
                    recommendation="Add 'HEALTHCHECK ... CMD ...' for container liveness check.",
                )
            )

        if has_apt_get and not has_apt_clean:
            issues.append(
                ContainerScanIssue(
                    rule_id="CIS-DI-006",
                    severity="LOW",
                    instruction="apt-get update",
                    description="Apt package lists are not cleaned up, inflating image size.",
                    recommendation="Append '&& rm -rf /var/lib/apt/lists/*' to apt-get install.",
                )
            )

        return ContainerScanReport(
            target=filename,
            rules_checked=len(self.RULES),
            issues=issues,
        )

    def scan_file(self, dockerfile_path: Path | str) -> ContainerScanReport:
        """Reads and scans a Dockerfile from filesystem."""
        path = Path(dockerfile_path)
        if not path.exists():
            return ContainerScanReport(
                target=str(path),
                rules_checked=len(self.RULES),
                issues=[
                    ContainerScanIssue(
                        rule_id="CIS-DI-000",
                        severity="HIGH",
                        instruction="Dockerfile",
                        description=f"Dockerfile not found at {path}",
                        recommendation="Provide a valid Dockerfile for containerized deployment.",
                    )
                ],
            )
        content = path.read_text(encoding="utf-8")
        return self.scan_dockerfile_content(content, filename=str(path))


# ==============================================================================
# 3. Static Application Security Testing (SAST) Code Scanner
# ==============================================================================


@dataclass(frozen=True)
class CodeScanIssue:
    """Represents a source code security vulnerability."""

    rule_id: str
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    file_path: str
    line_number: int
    code_snippet: str
    description: str
    recommendation: str


@dataclass
class CodeScanReport:
    """Results from SAST code scan."""

    files_scanned: int
    lines_scanned: int
    issues: list[CodeScanIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Returns True if no CRITICAL or HIGH issues exist."""
        return not any(i.severity in ("CRITICAL", "HIGH") for i in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "HIGH")

    @property
    def medium_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "MEDIUM")

    @property
    def low_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "LOW")


class CodeSecurityScanner:
    """AST-based static security analyzer for Python source code."""

    HARDCODED_SECRET_PATTERNS = [
        re.compile(
            r"""(?i)(password|secret|api_key|access_token|private_key)\s*=\s*"""
            r"""['\"][a-zA-Z0-9_\-\.]{8,}['\"]"""
        ),
        re.compile(r"""(?i)(bearer\s+[a-zA-Z0-9_\-\.]{20,})"""),
    ]

    def scan_source(self, code: str, file_path: str = "<source>") -> CodeScanReport:
        """Analyzes Python code AST and regex for security vulnerabilities."""
        issues: list[CodeScanIssue] = []
        lines = code.splitlines()
        lines_scanned = len(lines)

        try:
            tree = ast.parse(code, filename=file_path)
        except SyntaxError as e:
            return CodeScanReport(
                files_scanned=1,
                lines_scanned=lines_scanned,
                issues=[
                    CodeScanIssue(
                        rule_id="SAST-SYNTAX",
                        severity="MEDIUM",
                        file_path=file_path,
                        line_number=e.lineno or 1,
                        code_snippet=str(e),
                        description="Syntax error encountered during AST parsing.",
                        recommendation="Fix Python syntax error.",
                    )
                ],
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ("eval", "exec", "__import__"):
                        snippet = (
                            lines[node.lineno - 1] if node.lineno <= len(lines) else node.func.id
                        )
                        issues.append(
                            CodeScanIssue(
                                rule_id="SAST-001",
                                severity="CRITICAL",
                                file_path=file_path,
                                line_number=node.lineno,
                                code_snippet=snippet,
                                description=(
                                    f"Direct call to '{node.func.id}()' allows arbitrary execution."
                                ),
                                recommendation=(
                                    "Use safe parsers (ast.literal_eval, json.loads) instead."
                                ),
                            )
                        )

                if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "Popen",
                    "call",
                    "run",
                    "check_call",
                    "check_output",
                ):
                    for kw in node.keywords:
                        if (
                            kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            snippet = (
                                lines[node.lineno - 1]
                                if node.lineno <= len(lines)
                                else "shell=True"
                            )
                            issues.append(
                                CodeScanIssue(
                                    rule_id="SAST-003",
                                    severity="HIGH",
                                    file_path=file_path,
                                    line_number=node.lineno,
                                    code_snippet=snippet,
                                    description=(
                                        "subprocess with shell=True invites command injection."
                                    ),
                                    recommendation="Pass arguments as list with shell=False.",
                                )
                            )

                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr == "system"
                ):
                    snippet = lines[node.lineno - 1] if node.lineno <= len(lines) else "os.system()"
                    issues.append(
                        CodeScanIssue(
                            rule_id="SAST-003",
                            severity="HIGH",
                            file_path=file_path,
                            line_number=node.lineno,
                            code_snippet=snippet,
                            description="os.system() is vulnerable to command injection.",
                            recommendation="Replace with subprocess.run(['cmd', 'arg']).",
                        )
                    )

                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "pickle"
                    and node.func.attr in ("loads", "load")
                ):
                    snippet = (
                        lines[node.lineno - 1] if node.lineno <= len(lines) else "pickle.loads()"
                    )
                    issues.append(
                        CodeScanIssue(
                            rule_id="SAST-002",
                            severity="CRITICAL",
                            file_path=file_path,
                            line_number=node.lineno,
                            code_snippet=snippet,
                            description="Unsafe pickle deserialization allows remote execution.",
                            recommendation="Use safetensors or json/parquet format.",
                        )
                    )

                if isinstance(node.func, ast.Attribute) and node.func.attr in ("md5", "sha1"):
                    is_insecure = True
                    for kw in node.keywords:
                        if (
                            kw.arg in ("used_for_security", "usedforsecurity")
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is False
                        ):
                            is_insecure = False
                    if is_insecure:
                        snippet = (
                            lines[node.lineno - 1] if node.lineno <= len(lines) else node.func.attr
                        )
                        issues.append(
                            CodeScanIssue(
                                rule_id="SAST-004",
                                severity="MEDIUM",
                                file_path=file_path,
                                line_number=node.lineno,
                                code_snippet=snippet,
                                description=f"Weak hash '{node.func.attr}()' detected.",
                                recommendation=(
                                    "Use sha256 or argon2id, or pass used_for_security=False."
                                ),
                            )
                        )

        if not file_path.startswith("test") and "test_" not in file_path:
            for idx, line in enumerate(lines, start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if any(x in stripped.lower() for x in ("change_in_production", "your_secret")):
                    continue
                for pattern in self.HARDCODED_SECRET_PATTERNS:
                    if pattern.search(stripped):
                        issues.append(
                            CodeScanIssue(
                                rule_id="SAST-005",
                                severity="HIGH",
                                file_path=file_path,
                                line_number=idx,
                                code_snippet=stripped[:80],
                                description="Potential hardcoded secret detected in source code.",
                                recommendation="Extract secret into env or SecretManager.",
                            )
                        )

        return CodeScanReport(
            files_scanned=1,
            lines_scanned=lines_scanned,
            issues=issues,
        )

    def scan_directory(
        self,
        directory_path: Path | str,
        exclude_dirs: list[str] | None = None,
    ) -> CodeScanReport:
        """Recursively scans all .py files in a directory."""
        dir_p = Path(directory_path)
        excludes = set(
            exclude_dirs
            or [
                ".venv",
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                "site-packages",
            ]
        )

        total_files = 0
        total_lines = 0
        all_issues: list[CodeScanIssue] = []

        for py_file in dir_p.rglob("*.py"):
            if any(part in excludes for part in py_file.parts):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
                rep = self.scan_source(content, file_path=str(py_file))
                total_files += rep.files_scanned
                total_lines += rep.lines_scanned
                all_issues.extend(rep.issues)
            except Exception:
                continue

        return CodeScanReport(
            files_scanned=total_files,
            lines_scanned=total_lines,
            issues=all_issues,
        )


# ==============================================================================
# 4. Unified Security Scanner Suite
# ==============================================================================


@dataclass
class SecuritySuiteReport:
    """Aggregated report across all security scanning facets."""

    dependency_report: DependencyScanReport
    container_report: ContainerScanReport | None
    code_report: CodeScanReport

    @property
    def total_critical(self) -> int:
        count = self.dependency_report.critical_count + self.code_report.critical_count
        if self.container_report:
            count += self.container_report.critical_count
        return count

    @property
    def total_high(self) -> int:
        count = self.dependency_report.high_count + self.code_report.high_count
        if self.container_report:
            count += self.container_report.high_count
        return count

    @property
    def total_medium(self) -> int:
        count = self.dependency_report.medium_count + self.code_report.medium_count
        if self.container_report:
            count += self.container_report.medium_count
        return count

    @property
    def total_low(self) -> int:
        count = self.dependency_report.low_count + self.code_report.low_count
        if self.container_report:
            count += self.container_report.low_count
        return count

    @property
    def passed(self) -> bool:
        """Passes if no CRITICAL or HIGH issues exist across all reports."""
        dep_ok = self.dependency_report.passed
        code_ok = self.code_report.passed
        cont_ok = self.container_report.passed if self.container_report else True
        return dep_ok and code_ok and cont_ok

    @property
    def exit_code(self) -> int:
        """Returns 0 for success/passing, 1 for security gate failure."""
        return 0 if self.passed else 1

    def to_summary_dict(self) -> dict[str, Any]:
        """Provides a structured dictionary summary for CI artifacts and logs."""
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "summary": {
                "critical": self.total_critical,
                "high": self.total_high,
                "medium": self.total_medium,
                "low": self.total_low,
            },
            "dependency_scan": {
                "packages_checked": self.dependency_report.total_packages_checked,
                "lockfile_valid": self.dependency_report.lockfile_valid,
                "sbom_generated": self.dependency_report.sbom_generated,
                "issues_count": len(self.dependency_report.issues),
            },
            "container_scan": {
                "rules_checked": self.container_report.rules_checked
                if self.container_report
                else 0,
                "issues_count": len(self.container_report.issues) if self.container_report else 0,
            }
            if self.container_report
            else None,
            "code_scan": {
                "files_scanned": self.code_report.files_scanned,
                "lines_scanned": self.code_report.lines_scanned,
                "issues_count": len(self.code_report.issues),
            },
        }


class SecurityScannerSuite:
    """Orchestrates comprehensive multi-vector security scans."""

    def __init__(
        self,
        dependency_scanner: DependencyScanner | None = None,
        container_scanner: ContainerSecurityScanner | None = None,
        code_scanner: CodeSecurityScanner | None = None,
    ) -> None:
        self.dep_scanner = dependency_scanner or DependencyScanner()
        self.container_scanner = container_scanner or ContainerSecurityScanner()
        self.code_scanner = code_scanner or CodeSecurityScanner()

    def run_all(
        self,
        pyproject_path: Path | str = "pyproject.toml",
        lockfile_path: Path | str | None = "uv.lock",
        dockerfile_path: Path | str | None = "Dockerfile",
        source_dir: Path | str = "src",
    ) -> SecuritySuiteReport:
        """Executes dependency, container, and SAST code scans."""
        dep_report = self.dep_scanner.scan_dependencies(pyproject_path, lockfile_path)

        cont_report = None
        if dockerfile_path and Path(dockerfile_path).exists():
            cont_report = self.container_scanner.scan_file(dockerfile_path)

        code_report = self.code_scanner.scan_directory(source_dir)

        return SecuritySuiteReport(
            dependency_report=dep_report,
            container_report=cont_report,
            code_report=code_report,
        )


def main() -> int:
    """CLI entrypoint for CI and local verification."""
    suite = SecurityScannerSuite()
    report = suite.run_all()
    summary = report.to_summary_dict()
    print(json.dumps(summary, indent=2))
    return report.exit_code


if __name__ == "__main__":
    import sys

    sys.exit(main())
