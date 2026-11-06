"""Repository-wide secret auditing and pre-deployment gatekeeper.

Provides high-entropy analysis, pattern detection (Private Keys, AWS, GitHub tokens,
Slack webhooks, Stripe keys, Database URIs with credentials, JWTs, etc.),
sensitive file detection (.env, .pem, .key), and pre-deployment gate enforcement
with Prometheus telemetry for production safety.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.observability.metrics import REGISTRY

# Observability Telemetry Metrics
SECRET_SCAN_STATUS_GAUGE = REGISTRY.gauge(
    "secret_scan_gate_status",
    "Current status of the repository secret scanning gate (1=PASS, 0=FAIL)",
)
SECRET_SCAN_FILES_SCANNED = REGISTRY.counter(
    "secret_scan_files_scanned_total",
    "Total count of files scanned for hardcoded secrets",
)
SECRET_SCAN_VIOLATIONS_TOTAL = REGISTRY.counter(
    "secret_scan_violations_detected_total",
    "Total count of secret violations detected across all scans",
)


class SecretSeverity(StrEnum):
    """Severity classification of detected secret findings."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SecretGateStatus(StrEnum):
    """Outcome of pre-deployment secret gate evaluation."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class SecretFinding:
    """Represents a hardcoded secret or sensitive file detected during scan."""

    rule_id: str
    rule_name: str
    severity: SecretSeverity
    file_path: str
    line_number: int | None
    description: str
    masked_snippet: str
    entropy_score: float = 0.0


@dataclass
class SecretAuditReport:
    """Consolidated audit report for repository secret scanning."""

    started_at: datetime
    completed_at: datetime
    total_files_scanned: int
    total_bytes_scanned: int
    findings: list[SecretFinding] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Returns True if no CRITICAL or HIGH severity secret findings are present."""
        return not any(
            f.severity in (SecretSeverity.CRITICAL, SecretSeverity.HIGH)
            for f in self.findings
        )

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SecretSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SecretSeverity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SecretSeverity.MEDIUM)

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SecretSeverity.LOW)

    def to_dict(self) -> dict[str, Any]:
        """Serializes audit report to structured dictionary."""
        return {
            "status": SecretGateStatus.PASS.value if self.passed else SecretGateStatus.FAIL.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_ms": round((self.completed_at - self.started_at).total_seconds() * 1000, 2),
            "total_files_scanned": self.total_files_scanned,
            "total_bytes_scanned": self.total_bytes_scanned,
            "findings_count": {
                "critical": self.critical_count,
                "high": self.high_count,
                "medium": self.medium_count,
                "low": self.low_count,
                "total": len(self.findings),
            },
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "rule_name": f.rule_name,
                    "severity": f.severity.value,
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "description": f.description,
                    "masked_snippet": f.masked_snippet,
                    "entropy": round(f.entropy_score, 2),
                }
                for f in self.findings
            ],
            "passed": self.passed,
        }


class RepoSecretAuditor:
    """Scans repository files, configuration, and documentation for leaked credentials.

    Uses high-entropy calculation and curated regular expression patterns.
    Enforces strict allowlisting for test fixtures, example files, and documentation mocks.
    """

    # Prohibited files that should never exist or be committed
    FORBIDDEN_FILE_PATTERNS: list[tuple[str, str, SecretSeverity]] = [
        (r"^\.env(\..+)?$", "Unencrypted environment file committed", SecretSeverity.CRITICAL),
        (r".*\.pem$", "Raw unencrypted PEM certificate or key", SecretSeverity.CRITICAL),
        (r".*\.key$", "Raw unencrypted private key file", SecretSeverity.CRITICAL),
        (r"^id_rsa.*$", "SSH private key file committed", SecretSeverity.CRITICAL),
        (r"^id_ed25519.*$", "OpenSSH private key file committed", SecretSeverity.CRITICAL),
        (r"^credentials\.json$", "Raw credentials json file", SecretSeverity.HIGH),
        (r".*_secret.*\.json$", "Secret json credential file", SecretSeverity.HIGH),
    ]

    # Secret signature regex rules
    SECRET_REGEX_RULES: list[dict[str, Any]] = [
        {
            "id": "SEC-PRIVKEY-001",
            "name": "Private Key Header",
            "severity": SecretSeverity.CRITICAL,
            "pattern": re.compile(
                r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
                re.MULTILINE,
            ),
            "description": "Cryptographic private key header found in plain text.",
        },
        {
            "id": "SEC-AWS-001",
            "name": "AWS Access Key ID",
            "severity": SecretSeverity.CRITICAL,
            "pattern": re.compile(
                r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"
            ),
            "description": "AWS 20-character Access Key ID detected.",
        },
        {
            "id": "SEC-GITHUB-001",
            "name": "GitHub Token",
            "severity": SecretSeverity.CRITICAL,
            "pattern": re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}"),
            "description": "GitHub Personal Access Token or OAuth token detected.",
        },
        {
            "id": "SEC-STRIPE-001",
            "name": "Stripe Live Secret Key",
            "severity": SecretSeverity.CRITICAL,
            "pattern": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
            "description": "Stripe live production secret key detected.",
        },
        {
            "id": "SEC-SLACK-001",
            "name": "Slack Webhook URL",
            "severity": SecretSeverity.HIGH,
            "pattern": re.compile(r"https://hooks\.slack\.com/services/T[0-9A-Z_]+/B[0-9A-Z_]+/[0-9a-zA-Z_]+"),
            "description": "Slack incoming webhook URL detected.",
        },
        {
            "id": "SEC-GOOGLE-001",
            "name": "Google API Key",
            "severity": SecretSeverity.HIGH,
            "pattern": re.compile(r"AIza[0-9A-Za-z\\-_]{35}"),
            "description": "Google cloud or maps API key detected.",
        },
        {
            "id": "SEC-DB-URI-001",
            "name": "Database URI With Embedded Credentials",
            "severity": SecretSeverity.CRITICAL,
            "pattern": re.compile(
                r"(?:postgres|postgresql|mysql|mongodb|redis)://[a-zA-Z0-9_]+:(?![\s\"'\$])[^\s\"'@]{6,}@[a-zA-Z0-9_.\-]+",
                re.IGNORECASE,
            ),
            "description": "Database connection string containing raw username and password.",
        },
        {
            "id": "SEC-BEARER-001",
            "name": "Bearer Token Assignment",
            "severity": SecretSeverity.HIGH,
            "pattern": re.compile(r"""(?i)bearer\s+['"]?[a-zA-Z0-9_\-\.]{40,}['"]?"""),
            "description": "Long bearer token authentication string found.",
        },
        {
            "id": "SEC-GENERIC-ASSIGN-001",
            "name": "Hardcoded Credential Assignment",
            "severity": SecretSeverity.HIGH,
            "pattern": re.compile(
                r"""(?i)(?:api_key|secret_key|private_key|auth_token|client_secret)\s*[:=]\s*['"]([a-zA-Z0-9_\-\.]{24,})['"]"""
            ),
            "description": "Generic API key or secret variable assigned literal value.",
        },
    ]

    # Directories and files always ignored during repository scan
    DEFAULT_IGNORE_DIRS: set[str] = {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".idea",
        ".vscode",
        ".coverage",
        "htmlcov",
        "dist",
        "build",
        ".dvc",
    }

    # Allowlisted dummy tokens used strictly in unit tests or documentation templates
    SAFE_SAMPLE_ALLOWLIST: set[str] = {
        "your_api_key_here",
        "your_secret_key_here",
        "akiaiofodnn7example",
        "sk_live_dummy_for_testing",
        "sk_live_992102910291029102",
        "change_me_in_production",
        "mock_db_password_12345",
        "test_secret_key_32chars_min_len",
        "test_jwt_secret_value_for_unit",
        "[redacted]",
        "***[redacted]***",
    }

    def __init__(
        self,
        repo_root: Path | str | None = None,
        custom_ignore_dirs: set[str] | None = None,
        min_entropy: float = 4.2,
        scan_tests: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.scan_tests = scan_tests
        base_ignore = set(self.DEFAULT_IGNORE_DIRS)
        if not self.scan_tests:
            base_ignore.add("tests")
        self.ignore_dirs = base_ignore | (custom_ignore_dirs or set())
        self.min_entropy = min_entropy

    @staticmethod
    def calculate_shannon_entropy(data: str) -> float:
        """Calculates Shannon entropy in bits per character for string data."""
        if not data:
            return 0.0
        entropy = 0.0
        length = len(data)
        freq: dict[str, int] = {}
        for char in data:
            freq[char] = freq.get(char, 0) + 1
        for count in freq.values():
            p_x = count / length
            entropy -= p_x * math.log2(p_x)
        return entropy

    @staticmethod
    def mask_snippet(raw: str, prefix_len: int = 4, suffix_len: int = 4) -> str:
        """Masks sensitive string to safely present in reports."""
        clean = raw.strip()
        if len(clean) <= (prefix_len + suffix_len + 2):
            return "***[REDACTED]***"
        return f"{clean[:prefix_len]}...[REDACTED]...{clean[-suffix_len:]}"

    def is_allowlisted_sample(self, candidate: str) -> bool:
        """Determines if candidate string matches known harmless test/docs dummy."""
        cand_lower = candidate.lower()
        if "[redacted]" in cand_lower or "***" in cand_lower:
            return True
        for sample in self.SAFE_SAMPLE_ALLOWLIST:
            if sample in cand_lower:
                return True
        # Common test prefixes
        if any(cand_lower.startswith(prefix) for prefix in ("test_", "dummy_", "fake_", "mock_")):
            return True
        return False

    def is_safe_db_uri(self, uri: str) -> bool:
        """Checks if a matched database URI is a documented example or safe local default."""
        uri_lower = uri.lower()
        if "[redacted]" in uri_lower or "***" in uri_lower:
            return True
        # Local development placeholders
        if any(host in uri_lower for host in ("@localhost", "@127.0.0.1", "@host")):
            if any(
                dummy in uri_lower
                for dummy in (
                    ":password@",
                    ":pass@",
                    ":changeme@",
                    ":test",
                    ":dummy",
                    ":admin@",
                    ":postgres@",
                )
            ):
                return True
        return False

    def scan_file_content(self, file_path: Path, content: str) -> list[SecretFinding]:
        """Scans string content of a file against all secret rules and entropy checks."""
        findings: list[SecretFinding] = []
        is_test_or_doc_file = (
            "test" in file_path.as_posix().lower()
            or file_path.name.endswith(".example")
            or "docs" in file_path.as_posix().lower()
        )

        lines = content.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            for rule in self.SECRET_REGEX_RULES:
                match = rule["pattern"].search(stripped)
                if match:
                    matched_text = match.group(0)

                    # In test or doc files, verify if it's an explicit test dummy
                    if self.is_allowlisted_sample(matched_text):
                        continue

                    if rule["id"] == "SEC-DB-URI-001" and self.is_safe_db_uri(matched_text):
                        continue

                    # If file is .example or documentation, allow harmless samples
                    if is_test_or_doc_file and self.is_allowlisted_sample(matched_text):
                        continue

                    entropy = self.calculate_shannon_entropy(matched_text)

                    # For generic assignments, require sufficient length and entropy
                    if rule["id"] == "SEC-GENERIC-ASSIGN-001":
                        extracted_val = match.group(1) if match.groups() else matched_text
                        if self.is_allowlisted_sample(extracted_val):
                            continue
                        if len(extracted_val) < 20 or entropy < self.min_entropy:
                            continue

                    findings.append(
                        SecretFinding(
                            rule_id=rule["id"],
                            rule_name=rule["name"],
                            severity=rule["severity"],
                            file_path=str(file_path),
                            line_number=line_idx,
                            description=rule["description"],
                            masked_snippet=self.mask_snippet(matched_text),
                            entropy_score=entropy,
                        )
                    )

        return findings

    def check_forbidden_filename(self, file_path: Path) -> SecretFinding | None:
        """Checks if a file path violates forbidden credential storage policies."""
        file_name = file_path.name
        # Allow .env.example or template files
        if file_name.endswith(".example") or file_name.endswith(".template"):
            return None

        for pattern, desc, severity in self.FORBIDDEN_FILE_PATTERNS:
            if re.search(pattern, file_name, re.IGNORECASE):
                return SecretFinding(
                    rule_id="SEC-FORBIDDEN-FILE-001",
                    rule_name="Forbidden Credential File",
                    severity=severity,
                    file_path=str(file_path),
                    line_number=None,
                    description=desc,
                    masked_snippet=f"File: {file_name}",
                    entropy_score=0.0,
                )
        return None

    def scan_repository(self, target_root: Path | str | None = None) -> SecretAuditReport:
        """Performs full filesystem scan of the repository tree."""
        root = Path(target_root) if target_root else self.repo_root
        started_at = datetime.now(UTC)
        findings: list[SecretFinding] = []
        skipped_paths: list[str] = []
        total_files = 0
        total_bytes = 0

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directory trees in-place
            dirnames[:] = [d for d in dirnames if d not in self.ignore_dirs]

            for fname in filenames:
                file_path = Path(dirpath) / fname
                try:
                    rel_path = file_path.relative_to(root)
                except ValueError:
                    rel_path = file_path

                # Check forbidden filename
                forbidden_finding = self.check_forbidden_filename(file_path)
                if forbidden_finding:
                    findings.append(forbidden_finding)

                # Only scan text / config / script files
                if file_path.suffix in (
                    ".pyc",
                    ".lock",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".ico",
                    ".svg",
                    ".woff",
                    ".woff2",
                    ".tar",
                    ".gz",
                    ".zip",
                    ".db",
                    ".sqlite",
                    ".parquet",
                ):
                    skipped_paths.append(str(rel_path))
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    total_files += 1
                    total_bytes += len(content.encode("utf-8"))
                    file_findings = self.scan_file_content(rel_path, content)
                    findings.extend(file_findings)
                except Exception as exc:
                    skipped_paths.append(f"{rel_path} (error: {exc})")

        completed_at = datetime.now(UTC)

        report = SecretAuditReport(
            started_at=started_at,
            completed_at=completed_at,
            total_files_scanned=total_files,
            total_bytes_scanned=total_bytes,
            findings=findings,
            skipped_paths=skipped_paths,
        )

        # Update telemetry
        SECRET_SCAN_FILES_SCANNED.inc(total_files)
        SECRET_SCAN_STATUS_GAUGE.set(1.0 if report.passed else 0.0)
        for f in findings:
            SECRET_SCAN_VIOLATIONS_TOTAL.inc(
                1.0, labels={"severity": f.severity.value, "rule_id": f.rule_id}
            )

        return report


class SecretScanGatekeeper:
    """Pre-deployment verification gate ensuring clean repository state."""

    def __init__(self, auditor: RepoSecretAuditor | None = None) -> None:
        self.auditor = auditor or RepoSecretAuditor()

    def evaluate_gate(
        self, target_root: Path | str | None = None
    ) -> tuple[SecretGateStatus, SecretAuditReport]:
        """Evaluates whether the repository passes pre-deployment secret gate."""
        report = self.auditor.scan_repository(target_root)
        status = SecretGateStatus.PASS if report.passed else SecretGateStatus.FAIL
        return status, report
