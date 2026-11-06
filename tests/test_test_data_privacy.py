"""Tests for test data privacy isolation, synthetic identity generation, and PII guard.

Guarantees that test fixtures and suites strictly avoid real user data:
- No real public email addresses (RFC 2606 @example.com enforced)
- No real active mobile subscriber numbers
- No real citizen IDs (TCKNs) or valid payment cards
- Full audit of all repository test suites
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.testing.user_data_privacy import (
    ALLOWED_TEST_EMAIL_DOMAINS,
    PrivacyViolationError,
    SyntheticUserDataFactory,
    TestDataPrivacyAuditor,
    assert_safe_test_data,
    generate_synthetic_tckn,
    is_allowed_test_email,
    is_luhn_valid_credit_card,
    is_valid_tckn_checksum,
)


@pytest.mark.unit
def test_synthetic_user_data_factory_defaults() -> None:
    """Verify SyntheticUserDataFactory creates strictly synthetic user models."""
    user = SyntheticUserDataFactory.create_test_user(user_id=101, role="ANALYST")

    assert user.user_id == 101
    assert user.role == "ANALYST"
    assert is_allowed_test_email(user.email)
    assert user.email.endswith("@example.com")
    assert user.phone_number.startswith("+90555000")
    assert user.synthetic_tckn.startswith("9")
    assert is_valid_tckn_checksum(user.synthetic_tckn)
    assert user.metadata["synthetic"] is True
    assert "mock_hash" in user.hashed_password


@pytest.mark.unit
def test_synthetic_user_batch_generation() -> None:
    """Verify batch synthetic user generation yields distinct, valid identities."""
    users = SyntheticUserDataFactory.create_synthetic_batch(count=5)
    assert len(users) == 5

    user_ids = {u.user_id for u in users}
    emails = {u.email for u in users}
    phones = {u.phone_number for u in users}
    tckns = {u.synthetic_tckn for u in users}

    assert len(user_ids) == 5
    assert len(emails) == 5
    assert len(phones) == 5
    assert len(tckns) == 5

    for u in users:
        assert is_allowed_test_email(u.email)
        assert u.synthetic_tckn.startswith("9")
        assert is_valid_tckn_checksum(u.synthetic_tckn)


@pytest.mark.unit
def test_is_allowed_test_email() -> None:
    """Verify RFC 2606 domain validation strictly separates test and real domains."""
    for domain in ALLOWED_TEST_EMAIL_DOMAINS:
        assert is_allowed_test_email(f"trader@{domain}")
        assert is_allowed_test_email(f"sub.user@{domain}")

    # Real public / commercial domains must be rejected
    assert not is_allowed_test_email("trader@gmail.com")
    assert not is_allowed_test_email("investor@yahoo.com")
    assert not is_allowed_test_email("ceo@superonline.com")
    assert not is_allowed_test_email("invalid_string_without_at")


@pytest.mark.unit
def test_tckn_validation_and_synthetic_generation() -> None:
    """Verify TCKN checksum algorithm and synthetic series generation."""
    # Synthetic generator test
    tckn1 = generate_synthetic_tckn(1)
    tckn2 = generate_synthetic_tckn(2)

    assert len(tckn1) == 11
    assert tckn1.startswith("9")
    assert is_valid_tckn_checksum(tckn1)
    assert is_valid_tckn_checksum(tckn2)
    assert tckn1 != tckn2

    # Invalid TCKNs
    assert not is_valid_tckn_checksum("01234567890")  # starts with 0
    assert not is_valid_tckn_checksum("1234567890")  # 10 digits
    assert not is_valid_tckn_checksum("99999999999")  # invalid checksum


@pytest.mark.unit
def test_luhn_credit_card_detection() -> None:
    """Verify Luhn checksum detects valid PANs."""
    # Standard dummy test card
    assert is_luhn_valid_credit_card("4111 1111 1111 1111")
    # Invalid card
    assert not is_luhn_valid_credit_card("4111 1111 1111 1112")
    assert not is_luhn_valid_credit_card("12345")


@pytest.mark.security
def test_auditor_detects_real_email_leaks() -> None:
    """Verify TestDataPrivacyAuditor flags real public email domains in test text."""
    auditor = TestDataPrivacyAuditor()

    safe_text = "User created with email trader@example.com and alert rule price_above"
    issues = auditor.audit_text(safe_text)
    assert len(issues) == 0

    leaked_text = "Payload contains victim user email: real.person@gmail.com for notification"
    issues = auditor.audit_text(leaked_text)
    assert len(issues) == 1
    assert issues[0].issue_type == "REAL_EMAIL_DOMAIN"
    assert issues[0].severity == "CRITICAL"


@pytest.mark.security
def test_auditor_detects_real_turkish_mobile_numbers() -> None:
    """Verify TestDataPrivacyAuditor flags real active operator mobile numbers."""
    auditor = TestDataPrivacyAuditor()

    # Safe test phone number
    safe_text = "Contact phone: +90 555 000 0001"
    assert len(auditor.audit_text(safe_text)) == 0

    # Real operator mobile numbers
    real_phones = [
        "Customer phone 0532 123 45 67 registered",
        "SMS target: +90 542 987 65 43",
        "Direct line: 0505 555 12 34",
    ]
    for text in real_phones:
        issues = auditor.audit_text(text)
        assert len(issues) >= 1
        assert any(i.issue_type == "REAL_PHONE_NUMBER" for i in issues)


@pytest.mark.security
def test_auditor_detects_real_tckn() -> None:
    """Verify TestDataPrivacyAuditor flags valid TCKNs not in the 9-series synthetic pool."""
    auditor = TestDataPrivacyAuditor()

    # Safe synthetic TCKN starting with 9
    safe_tckn = generate_synthetic_tckn(42)
    assert len(auditor.audit_text(f"Verified identity: {safe_tckn}")) == 0

    # Known algorithmically valid TCKN not in 9-series (e.g. starting with 1)
    # Let's compute a valid 1-series TCKN
    first_9 = "100000001"
    digits = [int(c) for c in first_9]
    odd_sum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even_sum = digits[1] + digits[3] + digits[5] + digits[7]
    d10 = ((odd_sum * 7) - even_sum) % 10
    digits.append(d10)
    d11 = sum(digits[:10]) % 10
    digits.append(d11)
    real_style_tckn = "".join(str(d) for d in digits)

    issues = auditor.audit_text(f"Customer TCKN is {real_style_tckn}")
    assert len(issues) == 1
    assert issues[0].issue_type == "REAL_TCKN"


@pytest.mark.security
def test_assert_safe_test_data_runtime_guard() -> None:
    """Verify assert_safe_test_data raises PrivacyViolationError on dangerous payloads."""
    safe_payload = {
        "user_id": 1,
        "email": "investor@example.com",
        "phone": "+905550000001",
        "profile": {"name": "Test User", "symbol": "THYAO"},
    }
    # Should not raise
    assert_safe_test_data(safe_payload)

    dangerous_payload = {
        "user_id": 2,
        "email": "victim@hotmail.com",
        "profile": {"phone": "0533 111 22 33"},
    }
    with pytest.raises(PrivacyViolationError, match="Test data privacy violation detected"):
        assert_safe_test_data(dangerous_payload)


@pytest.mark.security
def test_audit_all_repository_test_files() -> None:
    """Meta-test: Scans all test_*.py files in tests/ directory to ensure zero real PII leaks."""
    tests_dir = Path(__file__).parent
    auditor = TestDataPrivacyAuditor()

    critical_violations: list[str] = []

    for test_file in sorted(tests_dir.glob("test_*.py")):
        # Skip this test file itself as it deliberately contains test string patterns for assertion
        if test_file.name == "test_test_data_privacy.py":
            continue

        issues = auditor.audit_file(test_file)
        # Filter for critical issues (real email domain or real phone or real TCKN)
        critical_issues = [i for i in issues if i.severity == "CRITICAL"]
        if critical_issues:
            for issue in critical_issues:
                critical_violations.append(
                    f"{test_file.name}:{issue.line_number} [{issue.issue_type}] {issue.description}"
                )

    assert not critical_violations, (
        "Real user data or PII detected in tests directory:\n" + "\n".join(critical_violations)
    )
