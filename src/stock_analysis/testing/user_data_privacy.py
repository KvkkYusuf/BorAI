"""Test data privacy isolation, synthetic user data generation, and PII guard.

Guarantees that test fixtures, mock datasets, and automated test suites NEVER
use real user data (real emails, real Turkish citizen IDs / TCKNs, real phone numbers,
real payment details, or real personal identities).

Standards applied:
- RFC 2606 / RFC 6761 (Reserved Top Level DNS Names & Example Domains)
- KVKK m. 7 / m. 12 (Personal Data Protection and Minimization)
- ISO/IEC 27001:2022 A.8.33 (Test information protection)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Allowed domains strictly adhering to RFC 2606 / RFC 6761 reserved test zones
ALLOWED_TEST_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "test.com",
        "test.local",
        "test.invalid",
        "localhost",
        "mock.internal",
    }
)

# Reserved fictional/test phone prefixes (ITU-T E.164 and fictitious exchange standards)
RESERVED_TEST_PHONE_PREFIXES: tuple[str, ...] = (
    "+905550000000",
    "+90555000",
    "+9055501",  # North American 555-01XX equivalent adapted for test isolation
    "+905000000000",
    "05550000000",
    "5550000000",
    "+900000000000",
)

# Disallowed public email providers that indicate real user email leakage
DISALLOWED_REAL_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "yahoo.com",
        "outlook.com",
        "icloud.com",
        "yandex.com",
        "yandex.com.tr",
        "proton.me",
        "protonmail.com",
        "superonline.com",
        "turkcell.com.tr",
        "vodafone.com.tr",
        "turk.net",
        "ttmail.com",
        "mynet.com",
        "live.com",
        "msn.com",
        "aol.com",
        "mail.com",
    }
)

# Common Turkish names pool for safe, synthetic generation
SYNTHETIC_FIRST_NAMES: tuple[str, ...] = (
    "Bora",
    "Ayşe",
    "Mehmet",
    "Zeynep",
    "Ahmet",
    "Fatma",
    "Mustafa",
    "Elif",
    "Can",
    "Deniz",
    "Emre",
    "Selin",
    "Burak",
    "Gamze",
    "Kerem",
)

SYNTHETIC_LAST_NAMES: tuple[str, ...] = (
    "Testoğlu",
    "Demir (Test)",
    "Yılmaz (Örnek)",
    "Kaya (Sentetik)",
    "Çelik (Simülasyon)",
    "Öztürk (Mock)",
    "Aydın (Test)",
    "Özdemir (Lab)",
    "Arslan (Deneme)",
    "Doğan (Test)",
)


class PrivacyViolationError(ValueError):
    """Raised when real user personal data or unverified PII is detected in test fixtures."""


@dataclass(frozen=True)
class TestDataPrivacyIssue:
    """Represents a detected PII or real user data leak in test assets."""

    file_or_source: str
    line_number: int | None
    issue_type: str  # 'REAL_EMAIL_DOMAIN', 'REAL_PHONE_NUMBER', 'REAL_TCKN', 'REAL_PAN'
    matched_value_masked: str
    description: str
    severity: str = "CRITICAL"


@dataclass(frozen=True)
class SyntheticTestUser:
    """Immutable, mathematically safe synthetic user model for test fixtures."""

    user_id: int
    email: str
    full_name: str
    phone_number: str
    role: str
    is_active: bool
    is_verified: bool
    hashed_password: str
    synthetic_tckn: str
    metadata: dict[str, Any] = field(default_factory=dict)


def is_allowed_test_email(email: str) -> bool:
    """Check if an email address strictly uses an RFC 2606 reserved test domain."""
    if "@" not in email:
        return False
    parts = email.strip().lower().split("@")
    if len(parts) != 2:
        return False
    domain = parts[1]
    # Exact match or subdomain of allowed domain (e.g. sub.example.com)
    for allowed in ALLOWED_TEST_EMAIL_DOMAINS:
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def is_valid_tckn_checksum(tckn: str) -> bool:
    """Validate 11-digit Republic of Turkey Citizen Identification Number checksum.

    Formula:
    - 11 digits, cannot start with 0.
    - d10 = ((d1+d3+d5+d7+d9)*7 - (d2+d4+d6+d8)) % 10
    - d11 = (sum(d1..d10)) % 10
    """
    if not tckn.isdigit() or len(tckn) != 11 or tckn[0] == "0":
        return False
    digits = [int(ch) for ch in tckn]
    odd_sum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even_sum = digits[1] + digits[3] + digits[5] + digits[7]
    digit_10 = ((odd_sum * 7) - even_sum) % 10
    if digits[9] != digit_10:
        return False
    digit_11 = sum(digits[:10]) % 10
    return digits[10] == digit_11


def generate_synthetic_tckn(seed_index: int = 1) -> str:
    """Deterministically generate an algorithmically valid synthetic TCKN.

    Starts with 9 (reserved testing series) to guarantee separation from living citizens.
    """
    first_9 = f"9{seed_index:08d}"[:9]
    digits = [int(c) for c in first_9]
    odd_sum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even_sum = digits[1] + digits[3] + digits[5] + digits[7]
    d10 = ((odd_sum * 7) - even_sum) % 10
    digits.append(d10)
    d11 = sum(digits[:10]) % 10
    digits.append(d11)
    return "".join(str(d) for d in digits)


def is_luhn_valid_credit_card(card_number: str) -> bool:
    """Validate 13-19 digit card number using the Luhn mod 10 formula."""
    clean = re.sub(r"[\s-]", "", card_number)
    if not clean.isdigit() or not (13 <= len(clean) <= 19):
        return False
    digits = [int(c) for c in clean]
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += (doubled - 9) if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


class SyntheticUserDataFactory:
    """Deterministic factory creating safe, mock test identities."""

    @staticmethod
    def create_test_user(
        user_id: int = 1,
        role: str = "USER",
        email_prefix: str | None = None,
        full_name: str | None = None,
        is_verified: bool = True,
        is_active: bool = True,
    ) -> SyntheticTestUser:
        """Create a single isolated synthetic test user with RFC 2606 domain."""
        prefix = email_prefix or f"user{user_id}"
        email = f"{prefix.lower()}@example.com"
        name_idx = user_id % len(SYNTHETIC_FIRST_NAMES)
        surname_idx = user_id % len(SYNTHETIC_LAST_NAMES)
        name = full_name or (
            f"{SYNTHETIC_FIRST_NAMES[name_idx]} {SYNTHETIC_LAST_NAMES[surname_idx]}"
        )
        phone = f"+90555000{user_id:04d}"[-13:]
        tckn = generate_synthetic_tckn(user_id)
        mock_password_hash = (
            f"$argon2id$v=19$m=65536,t=3,p=4$synthetic_salt_test_{user_id}$mock_hash"
        )

        return SyntheticTestUser(
            user_id=user_id,
            email=email,
            full_name=name,
            phone_number=phone,
            role=role,
            is_active=is_active,
            is_verified=is_verified,
            hashed_password=mock_password_hash,
            synthetic_tckn=tckn,
            metadata={"environment": "test_isolated", "synthetic": True},
        )

    @staticmethod
    def create_synthetic_batch(count: int = 10, role: str = "USER") -> list[SyntheticTestUser]:
        """Generate a sequential batch of isolated test users."""
        return [
            SyntheticUserDataFactory.create_test_user(user_id=i, role=role)
            for i in range(1, count + 1)
        ]


class TestDataPrivacyAuditor:
    """Security auditor for static and runtime test data inspection.

    Scans strings, AST nodes, dictionary payloads, and Python test files
    for real personal identifiable information.
    """

    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

    # Real Turkish mobile prefixes (0530-0539, 0540-0549, 0551-0554, 0559, 0505-0507)
    REAL_TURKISH_MOBILE_PATTERN = re.compile(
        r"(?:\+90|0090|0)?\s*(53\d|54\d|50\d|55[1-4|9])\s*(\d{3})\s*(\d{2})\s*(\d{2})\b"
    )

    TCKN_PATTERN = re.compile(r"\b([1-9]\d{10})\b")

    CARD_PATTERN = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b|\b\d{16}\b")

    def audit_text(
        self, text: str, source_identifier: str = "<memory>"
    ) -> list[TestDataPrivacyIssue]:
        """Audit raw text content for potential PII or non-synthetic test values."""
        issues: list[TestDataPrivacyIssue] = []

        lines = text.splitlines()
        # Check if entire file is exempted as a dedicated security redaction test suite
        if lines and any("# pragma: allow-pii-test-suite" in line_item for line_item in lines[:10]):
            return []

        for line_no, line in enumerate(lines, start=1):
            if "# pragma: allow-pii-test" in line or "# no-pii-check" in line:
                continue

            # 1. Email check
            for match in self.EMAIL_PATTERN.finditer(line):
                full_email = match.group(0).lower()
                domain = match.group(1).lower()

                # Check if it is a disallowed real public domain
                if domain in DISALLOWED_REAL_EMAIL_DOMAINS:
                    issues.append(
                        TestDataPrivacyIssue(
                            file_or_source=source_identifier,
                            line_number=line_no,
                            issue_type="REAL_EMAIL_DOMAIN",
                            matched_value_masked=f"{full_email[:2]}***@{domain}",
                            description=(
                                f"Public production email domain '{domain}' found in test data. "
                                "Must use @example.com or RFC 2606 reserved domains."
                            ),
                            severity="CRITICAL",
                        )
                    )
                elif not is_allowed_test_email(full_email):
                    # Flag any non-RFC-2606 domain if not a standard test mock domain
                    if not (domain.endswith(".example") or domain.endswith(".test")):
                        issues.append(
                            TestDataPrivacyIssue(
                                file_or_source=source_identifier,
                                line_number=line_no,
                                issue_type="UNVERIFIED_EMAIL_DOMAIN",
                                matched_value_masked=f"{full_email[:2]}***@{domain}",
                                description=(
                                    f"Unverified email domain '{domain}' in test data. "
                                    "Only RFC 2606 allowed."
                                ),
                                severity="HIGH",
                            )
                        )

            # 2. Turkish Phone Number check
            for phone_match in self.REAL_TURKISH_MOBILE_PATTERN.finditer(line):
                matched_raw = phone_match.group(0)
                clean_phone = re.sub(r"[\s+-]", "", matched_raw)
                # Ensure it is not in the reserved fictional test prefix
                is_reserved = any(
                    clean_phone.startswith(prefix.replace("+", ""))
                    for prefix in RESERVED_TEST_PHONE_PREFIXES
                )
                if not is_reserved:
                    issues.append(
                        TestDataPrivacyIssue(
                            file_or_source=source_identifier,
                            line_number=line_no,
                            issue_type="REAL_PHONE_NUMBER",
                            matched_value_masked=f"{clean_phone[:4]}****{clean_phone[-2:]}",
                            description=(
                                "Real active Turkish mobile operator prefix detected in test data. "
                                "Use synthetic test prefix +905550000000."
                            ),
                            severity="CRITICAL",
                        )
                    )

            # 3. TCKN Check
            for tckn_match in self.TCKN_PATTERN.finditer(line):
                raw_tckn = tckn_match.group(1)
                # Exclude obvious monotonic dummies like 11111111111 if checksum invalid
                if is_valid_tckn_checksum(raw_tckn):
                    # Check if it starts with 9 (synthetic test range)
                    if not raw_tckn.startswith("9"):
                        issues.append(
                            TestDataPrivacyIssue(
                                file_or_source=source_identifier,
                                line_number=line_no,
                                issue_type="REAL_TCKN",
                                matched_value_masked=f"{raw_tckn[:3]}*****{raw_tckn[-2:]}",
                                description=(
                                    "Algorithmically valid citizen identification number (TCKN) "
                                    "detected in test data. Only use 9-series synthetic numbers."
                                ),
                                severity="CRITICAL",
                            )
                        )

            # 4. Credit Card / PAN Check
            for card_match in self.CARD_PATTERN.finditer(line):
                raw_card = card_match.group(0)
                if is_luhn_valid_credit_card(raw_card):
                    # If valid Luhn card that isn't standard 4111 1111 1111 1111 dummy test card
                    clean_card = re.sub(r"[\s-]", "", raw_card)
                    if clean_card != "4111111111111111":
                        issues.append(
                            TestDataPrivacyIssue(
                                file_or_source=source_identifier,
                                line_number=line_no,
                                issue_type="REAL_PAN",
                                matched_value_masked=f"{clean_card[:4]}********{clean_card[-4:]}",
                                description=(
                                    "Valid credit card / PAN with valid Luhn checksum "
                                    "detected in test fixtures."
                                ),
                                severity="CRITICAL",
                            )
                        )

        return issues

    def audit_payload(self, payload: Any, path: str = "root") -> list[TestDataPrivacyIssue]:
        """Recursively inspect dictionaries, lists, and model payloads for real user data."""
        issues: list[TestDataPrivacyIssue] = []

        if isinstance(payload, str):
            issues.extend(self.audit_text(payload, source_identifier=path))
        elif isinstance(payload, dict):
            for k, v in payload.items():
                issues.extend(self.audit_payload(v, path=f"{path}.{k}"))
        elif isinstance(payload, (list, tuple, set)):
            for idx, item in enumerate(payload):
                issues.extend(self.audit_payload(item, path=f"{path}[{idx}]"))
        elif hasattr(payload, "__dict__"):
            issues.extend(self.audit_payload(payload.__dict__, path=path))

        return issues

    def audit_file(self, file_path: Path) -> list[TestDataPrivacyIssue]:
        """Audit a single test source file or fixture."""
        if not file_path.is_file():
            return []
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return []
        return self.audit_text(content, source_identifier=str(file_path))

    def audit_directory(
        self, directory: Path, pattern: str = "test_*.py"
    ) -> dict[str, list[TestDataPrivacyIssue]]:
        """Audit an entire directory of test files."""
        results: dict[str, list[TestDataPrivacyIssue]] = {}
        for test_file in sorted(directory.glob(pattern)):
            issues = self.audit_file(test_file)
            if issues:
                results[test_file.name] = issues
        return results


def assert_safe_test_data(data: Any) -> None:
    """Assertion helper for pytest test cases and fixtures."""
    auditor = TestDataPrivacyAuditor()
    issues = auditor.audit_payload(data)
    if issues:
        descriptions = "; ".join(
            f"[{issue.issue_type} in {issue.file_or_source}] {issue.description}"
            for issue in issues
        )
        raise PrivacyViolationError(f"Test data privacy violation detected: {descriptions}")
