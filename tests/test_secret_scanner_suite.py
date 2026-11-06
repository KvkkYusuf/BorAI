"""Comprehensive test suite for repository-wide secret scanning and pre-deployment gate."""

from pathlib import Path

from stock_analysis.security.secret_auditor import (
    RepoSecretAuditor,
    SecretGateStatus,
    SecretScanGatekeeper,
    SecretSeverity,
)


class TestRepoSecretAuditor:
    """Tests for RepoSecretAuditor pattern detection, entropy checks, and allowlists."""

    def test_calculate_shannon_entropy(self) -> None:
        auditor = RepoSecretAuditor()
        # Empty string
        assert auditor.calculate_shannon_entropy("") == 0.0
        # Low entropy (uniform characters)
        assert auditor.calculate_shannon_entropy("aaaaaaaaaa") == 0.0
        # High entropy (random hex/base64 string)
        high_ent = auditor.calculate_shannon_entropy("4f8a9c2b1d3e7f6a5b8c9d0e1f2a3b4c")
        assert high_ent > 3.5

    def test_mask_snippet(self) -> None:
        auditor = RepoSecretAuditor()
        short_secret = "secret"
        assert auditor.mask_snippet(short_secret) == "***[REDACTED]***"

        long_secret = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        masked = auditor.mask_snippet(long_secret, prefix_len=4, suffix_len=4)
        assert masked.startswith("ghp_")
        assert masked.endswith("wxyz")
        assert "[REDACTED]" in masked

    def test_detect_rsa_private_key_header(self, tmp_path: Path) -> None:
        fake_key_file = tmp_path / "server_config.py"
        fake_key_file.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----",
            encoding="utf-8",
        )
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(
            fake_key_file, fake_key_file.read_text(encoding="utf-8")
        )
        assert len(findings) >= 1
        assert any(f.rule_id == "SEC-PRIVKEY-001" for f in findings)
        assert any(f.severity == SecretSeverity.CRITICAL for f in findings)

    def test_detect_aws_access_key(self, tmp_path: Path) -> None:
        file = tmp_path / "aws_helper.py"
        file.write_text("aws_key = 'AKIAIOSFODNN7EXAMPLE'", encoding="utf-8")
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(file, file.read_text(encoding="utf-8"))
        assert len(findings) == 1
        assert findings[0].rule_id == "SEC-AWS-001"
        assert findings[0].severity == SecretSeverity.CRITICAL

    def test_detect_github_token(self, tmp_path: Path) -> None:
        file = tmp_path / "ci_sync.py"
        fake_token = "ghp_" + "4B7X8Y9Z0A1B2C3D4E5F6G7H8I9J0K1L2M3N"
        file.write_text(f"token = '{fake_token}'", encoding="utf-8")
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(file, file.read_text(encoding="utf-8"))
        assert len(findings) == 1
        assert findings[0].rule_id == "SEC-GITHUB-001"

    def test_detect_stripe_live_secret(self, tmp_path: Path) -> None:
        file = tmp_path / "billing.py"
        fake_stripe = "sk_" + "live_" + "51AbCdEfGhIjKlMnOpQrStUvWxYz12345"
        file.write_text(
            f"stripe_key = '{fake_stripe}'", encoding="utf-8"
        )
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(file, file.read_text(encoding="utf-8"))
        assert len(findings) == 1
        assert findings[0].rule_id == "SEC-STRIPE-001"

    def test_detect_slack_webhook(self, tmp_path: Path) -> None:
        file = tmp_path / "notifier.py"
        fake_slack = "https://" + "hooks." + "slack.com/services/T00000000/B00000000/" + ("X" * 24)
        file.write_text(
            f"url = '{fake_slack}'",
            encoding="utf-8",
        )
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(file, file.read_text(encoding="utf-8"))
        assert len(findings) == 1
        assert findings[0].rule_id == "SEC-SLACK-001"
        assert findings[0].severity == SecretSeverity.HIGH

    def test_detect_database_uri_with_credentials(self, tmp_path: Path) -> None:
        file = tmp_path / "db.py"
        file.write_text(
            "db_uri = 'postgres://admin:SuperSecretP@ss99@db.prod.internal:5432/stocks'",
            encoding="utf-8",
        )
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(file, file.read_text(encoding="utf-8"))
        assert len(findings) == 1
        assert findings[0].rule_id == "SEC-DB-URI-001"
        assert findings[0].severity == SecretSeverity.CRITICAL

    def test_detect_forbidden_filenames(self, tmp_path: Path) -> None:
        auditor = RepoSecretAuditor()

        # Prohibited .env file
        dot_env = tmp_path / ".env"
        finding = auditor.check_forbidden_filename(dot_env)
        assert finding is not None
        assert finding.rule_id == "SEC-FORBIDDEN-FILE-001"

        # Allowed .env.example
        env_example = tmp_path / ".env.example"
        assert auditor.check_forbidden_filename(env_example) is None

        # Prohibited raw key files
        pem_file = tmp_path / "id_rsa"
        assert auditor.check_forbidden_filename(pem_file) is not None

        key_file = tmp_path / "server.key"
        assert auditor.check_forbidden_filename(key_file) is not None

    def test_allowlisted_test_dummy_secrets(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_auth_mock.py"
        test_file.write_text("api_key = 'test_secret_key_32chars_min_len'", encoding="utf-8")
        auditor = RepoSecretAuditor()
        findings = auditor.scan_file_content(test_file, test_file.read_text(encoding="utf-8"))
        assert len(findings) == 0


class TestSecretScanGatekeeper:
    """Tests for pre-deployment gatekeeper enforcement and report serialization."""

    def test_gatekeeper_blocks_leaked_credentials(self, tmp_path: Path) -> None:
        leak_file = tmp_path / "leaked_secret.py"
        leak_file.write_text("aws_key = 'AKIA1111111111111111'", encoding="utf-8")

        gatekeeper = SecretScanGatekeeper(RepoSecretAuditor(repo_root=tmp_path))
        status, report = gatekeeper.evaluate_gate(target_root=tmp_path)

        assert status == SecretGateStatus.FAIL
        assert report.passed is False
        assert report.critical_count >= 1

        summary = report.to_dict()
        assert summary["status"] == "FAIL"
        assert summary["findings_count"]["critical"] >= 1

    def test_gatekeeper_passes_clean_repository(self, tmp_path: Path) -> None:
        clean_file = tmp_path / "clean_code.py"
        clean_file.write_text(
            """
import os

def get_config():
    return os.environ.get("DATABASE_URL", "sqlite:///:memory:")
""",
            encoding="utf-8",
        )
        gatekeeper = SecretScanGatekeeper(RepoSecretAuditor(repo_root=tmp_path))
        status, report = gatekeeper.evaluate_gate(target_root=tmp_path)

        assert status == SecretGateStatus.PASS
        assert report.passed is True
        assert report.critical_count == 0
        assert report.high_count == 0

    def test_actual_repository_is_clean_of_secrets(self) -> None:
        """Audits the actual project workspace to prove zero hardcoded secrets exist."""
        gatekeeper = SecretScanGatekeeper(RepoSecretAuditor())
        status, report = gatekeeper.evaluate_gate()

        # Print findings for diagnostics if any fail
        if not report.passed:
            details = [
                f"{f.file_path}:{f.line_number} -> {f.rule_name} ({f.severity})"
                for f in report.findings
            ]
            msg = "Repository secret scan failed with findings:\n" + "\n".join(details)
            raise AssertionError(msg)

        assert status == SecretGateStatus.PASS
        assert report.passed is True
        assert report.critical_count == 0
        assert report.high_count == 0
        assert report.total_files_scanned > 200
