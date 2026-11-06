"""Comprehensive Security Test Suite (FAZ-14).

Verifies the 4 critical security pillars:
1. Erişim Kontrolü (Access Control & RBAC & BOLA/IDOR):
   - Role hierarchy rank relationships (Guest < User < Analyst < Admin < SuperAdmin)
   - Privilege escalation defenses (strict rank boundaries, self-promotion block)
   - BOLA / IDOR object-level ownership checks (BolaOwnershipGuard)
   - Administrative audit log sanitization (PII masking, IP hashing, secret redaction)

2. Oturum Yönetimi (Session Management & Cookies & CSRF):
   - Secure cookie attributes (HttpOnly=True, Secure=True, SameSite="lax")
   - Double-Submit CSRF cookie & header synchronization (x-csrf-token)
   - CSRF rejection on missing, mismatched, or altered tokens on mutation requests
   - Safe HTTP methods (GET, HEAD, OPTIONS) bypassing CSRF checks
   - Session invalidation & cookie expiration on logout
   - Cryptographic session token generation (raw sess_... vs SHA-256 in DB)

3. Hız Sınırı (Rate Limiting & DoS Defense):
   - Sliding window rate limiting enforcement (SlidingWindowRateLimiter)
   - HTTP 429 Too Many Requests and Retry-After header verification
   - Client isolation (independent quota per IP / user token)
   - Tiered rate limits (stricter quota for anonymous vs authenticated users)
   - Window slide and reset behavior

4. Girdi Doğrulama & Enjeksiyon Koruması (Input Validation & Injection Defense):
   - SQL Injection detection (OR 1=1, UNION SELECT, DROP TABLE, comment tricks)
   - Cross-Site Scripting (XSS) detection (<script>, onerror=, javascript:)
   - Server-Side Request Forgery (SSRF) defense (loopback, link-local metadata, private IPs)
   - File Upload Guard (path traversal, dangerous executable extensions, MIME spoofing)
   - Mass Assignment / BOPLA protection (stripping/blocking is_admin, role, password_hash)
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from stock_analysis.api.rate_limiter import (
    SlidingWindowRateLimiter,
    TieredRateLimiter,
    reset_all_rate_limiters,
)
from stock_analysis.auth.session import SessionSecurityPolicy
from stock_analysis.db.base import Base
from stock_analysis.db.models import User
from stock_analysis.security.audit import (
    AdminActionType,
    PrivilegeEscalationError,
    RoleHierarchy,
    hash_client_ip,
    record_admin_audit_event,
    sanitize_audit_details,
)
from stock_analysis.security.environments import AccessRole
from stock_analysis.security.owasp import (
    BolaAccessDeniedError,
    BolaOwnershipGuard,
    InjectionDetectedError,
    InjectionGuard,
    MassAssignmentError,
    ObjectPropertyGuard,
    ResourceConsumptionGuard,
    ResourceLimitExceededError,
    SsrfProtectionGuard,
    SsrfViolationError,
)
from stock_analysis.security.upload_guard import (
    DangerousFileExtensionError,
    ExecutablePayloadDetectedError,
    FileUploadGuard,
    PathTraversalDetectedError,
)

pytestmark = pytest.mark.security

# In-memory SQLite for session & DB verification
TEST_DB_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def setup_security_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(setup_security_db: None) -> Generator[DBSession, None, None]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def test_user(db_session: DBSession) -> User:
    user = db_session.query(User).filter(User.email == "sec_test_user@example.com").first()
    if not user:
        user = User(
            email="sec_test_user@example.com",
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


class DummyClientRequest:
    """Mock request carrying client network headers for rate limiter testing."""

    def __init__(self, ip: str = "127.0.0.1", auth_token: str | None = None) -> None:
        self.headers: dict[str, str] = {"X-Forwarded-For": ip}
        if auth_token:
            self.headers["Authorization"] = f"Bearer {auth_token}"
        self.client = None


# ==============================================================================
# 1. Erişim Kontrolü (Access Control, RBAC, BOLA/IDOR)
# ==============================================================================


class TestAccessControlSecurity:
    """Tests for role hierarchy, privilege escalation, BOLA/IDOR, and audit trails."""

    def test_role_hierarchy_ranks(self) -> None:
        """Verify strict rank hierarchy: Guest < User < Analyst < Admin < SuperAdmin."""
        assert RoleHierarchy.get_rank(AccessRole.GUEST) == 0
        assert RoleHierarchy.get_rank(AccessRole.USER) == 10
        assert RoleHierarchy.get_rank(AccessRole.ANALYST) == 20
        assert RoleHierarchy.get_rank(AccessRole.COMPLIANCE_OFFICER) == 30
        assert RoleHierarchy.get_rank(AccessRole.ML_ENGINEER) == 30
        assert RoleHierarchy.get_rank(AccessRole.ADMIN) == 40
        assert RoleHierarchy.get_rank(AccessRole.SUPER_ADMIN) == 50

    def test_role_hierarchy_management_permissions(self) -> None:
        """Actors can only manage roles strictly below their own rank."""
        # Admin (rank 40) can manage User (rank 10) and Analyst (rank 20)
        assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.USER) is True
        assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.ANALYST) is True

        # Admin cannot manage peer Admin or Super Admin
        assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.ADMIN) is False
        assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.SUPER_ADMIN) is False

        # User cannot manage anybody
        assert RoleHierarchy.can_manage_target(AccessRole.USER, AccessRole.USER) is False
        assert RoleHierarchy.can_manage_target(AccessRole.USER, AccessRole.ADMIN) is False

    def test_privilege_escalation_forbidden(self) -> None:
        """Assigning roles equal or higher than actor's rank must raise PrivilegeEscalationError."""
        # Admin cannot assign SuperAdmin or Admin
        assert RoleHierarchy.can_assign_role(AccessRole.ADMIN, AccessRole.SUPER_ADMIN) is False
        assert RoleHierarchy.can_assign_role(AccessRole.ADMIN, AccessRole.ADMIN) is False

        with pytest.raises(PrivilegeEscalationError, match="Yetki yükseltme engeli"):
            RoleHierarchy.validate_role_transition(
                actor_role=AccessRole.ADMIN,
                target_current_role=AccessRole.USER,
                new_role=AccessRole.SUPER_ADMIN,
            )

        with pytest.raises(PrivilegeEscalationError, match="Yetki yükseltme engeli"):
            RoleHierarchy.validate_role_transition(
                actor_role=AccessRole.ADMIN,
                target_current_role=AccessRole.USER,
                new_role=AccessRole.ADMIN,
            )

        # Standard User cannot change roles
        with pytest.raises(PrivilegeEscalationError, match="Yetki ihlali"):
            RoleHierarchy.validate_role_transition(
                actor_role=AccessRole.USER,
                target_current_role=AccessRole.USER,
                new_role=AccessRole.ANALYST,
            )

    def test_self_role_modification_forbidden(self) -> None:
        """Administrators cannot directly mutate their own role (self-promotion block)."""
        with pytest.raises(PrivilegeEscalationError, match="kendi hesaplarının rolünü"):
            RoleHierarchy.validate_role_transition(
                actor_role=AccessRole.ADMIN,
                target_current_role=AccessRole.ADMIN,
                new_role=AccessRole.SUPER_ADMIN,
                is_self=True,
            )

    def test_super_admin_can_assign_valid_roles(self) -> None:
        """Super admin has top rank and can transition lower roles without error."""
        assert RoleHierarchy.can_assign_role(AccessRole.SUPER_ADMIN, AccessRole.ADMIN) is True
        assert RoleHierarchy.can_assign_role(AccessRole.SUPER_ADMIN, AccessRole.ANALYST) is True
        assert RoleHierarchy.can_assign_role(AccessRole.SUPER_ADMIN, AccessRole.USER) is True

        RoleHierarchy.validate_role_transition(
            actor_role=AccessRole.SUPER_ADMIN,
            target_current_role=AccessRole.USER,
            new_role=AccessRole.ADMIN,
        )

    def test_bola_ownership_guard_enforcement(self) -> None:
        """Mismatched subject and owner IDs must raise BolaAccessDeniedError."""
        subject_id = 42
        other_user_id = 99

        # Accessing someone else's resource without admin status must fail
        with pytest.raises(BolaAccessDeniedError, match="erisim yetkisi bulunmuyor"):
            BolaOwnershipGuard.verify_ownership(
                subject_id=subject_id,
                owner_id=other_user_id,
                is_admin=False,
                resource_type="watchlist",
            )

        # Owner accessing own resource must succeed
        BolaOwnershipGuard.verify_ownership(
            subject_id=subject_id,
            owner_id=subject_id,
            is_admin=False,
            resource_type="watchlist",
        )

        # Admin accessing other user's resource is allowed (for administrative review)
        BolaOwnershipGuard.verify_ownership(
            subject_id=subject_id,
            owner_id=other_user_id,
            is_admin=True,
            resource_type="watchlist",
        )

    def test_audit_event_sanitization(self, db_session: DBSession) -> None:
        """Administrative audit trail must sanitize passwords, tokens, and PII."""
        raw_details = {
            "target_email": "sensitive_user@bank.com.tr",
            "temporary_password": "PlainSecretPassword123!",
            "access_token": "bearer_secret_xyz_987",
            "action_reason": "Account role adjustment by compliance team",
        }

        sanitized = sanitize_audit_details(raw_details)
        assert sanitized["temporary_password"] == "[REDACTED]"
        assert sanitized["access_token"] == "[REDACTED]"
        assert "@" in sanitized["target_email"]
        assert "sensitive_user" not in sanitized["target_email"]  # Masked e.g. s***r@bank.com.tr

        # Client IP must be hashed with zero raw IP persistence
        raw_ip = "192.168.1.50"
        hashed_ip = hash_client_ip(raw_ip)
        assert hashed_ip is not None
        assert hashed_ip != raw_ip
        assert len(hashed_ip) == 64  # SHA-256 hex digest

        # Record audit event successfully
        event = record_admin_audit_event(
            db=db_session,
            actor_user_id=1,
            action=AdminActionType.USER_ROLE_UPDATED,
            resource="/admin/users/42/role",
            details=raw_details,
            client_ip=raw_ip,
        )
        assert event.id is not None
        assert event.details is not None
        assert "PlainSecretPassword123!" not in event.details


# ==============================================================================
# 2. Oturum Yönetimi (Session Management, Cookies, CSRF)
# ==============================================================================


class TestSessionManagementSecurity:
    """Tests for secure cookie flags, CSRF tokens, session invalidation, and hashing."""

    def test_session_cookie_security_flags(self) -> None:
        """Session cookie must strictly enforce HttpOnly, Secure, and SameSite."""
        policy = SessionSecurityPolicy(ttl_days=14, secure_cookie=True, samesite="lax")
        response = Response()
        policy.set_session_cookies(response, "sess_token_12345", "csrf_token_67890")

        cookie_headers = response.headers.getlist("set-cookie")
        session_cookie = next((c for c in cookie_headers if "borai_session" in c), None)
        assert session_cookie is not None
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=lax" in session_cookie

    def test_csrf_cookie_readable_by_frontend(self) -> None:
        """CSRF cookie must be readable by JavaScript (HttpOnly=False) for header inclusion."""
        policy = SessionSecurityPolicy(ttl_days=14, secure_cookie=True, samesite="lax")
        response = Response()
        policy.set_session_cookies(response, "sess_token_12345", "csrf_token_67890")

        cookie_headers = response.headers.getlist("set-cookie")
        csrf_cookie = next((c for c in cookie_headers if "borai_csrf" in c), None)
        assert csrf_cookie is not None
        assert "HttpOnly" not in csrf_cookie  # Must NOT be HttpOnly so frontend can read it
        assert "Secure" in csrf_cookie
        assert "SameSite=lax" in csrf_cookie

    def test_double_submit_csrf_validation_passes(self) -> None:
        """Matching CSRF cookie and x-csrf-token header must pass on mutation request."""
        policy = SessionSecurityPolicy()
        csrf_token = "valid_csrf_token_abcdef123456"

        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/user/preferences",
            "headers": [
                (b"cookie", f"borai_csrf={csrf_token}".encode()),
                (b"x-csrf-token", csrf_token.encode()),
            ],
        }
        request = Request(scope)
        # Should not raise any exception
        policy.validate_csrf(request)

    def test_double_submit_csrf_missing_header_rejected(self) -> None:
        """POST request missing x-csrf-token header must be rejected with HTTP 403."""
        policy = SessionSecurityPolicy()
        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/user/preferences",
            "headers": [
                (b"cookie", b"borai_csrf=valid_token"),
            ],
        }
        request = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            policy.validate_csrf(request)
        assert exc_info.value.status_code == 403
        assert "CSRF" in str(exc_info.value.detail)

    def test_double_submit_csrf_mismatch_rejected(self) -> None:
        """Mismatched cookie and header CSRF tokens must be rejected with HTTP 403."""
        policy = SessionSecurityPolicy()
        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/user/preferences",
            "headers": [
                (b"cookie", b"borai_csrf=cookie_token_abc"),
                (b"x-csrf-token", b"attacker_token_xyz"),
            ],
        }
        request = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            policy.validate_csrf(request)
        assert exc_info.value.status_code == 403

    def test_safe_http_methods_skip_csrf(self) -> None:
        """GET, HEAD, and OPTIONS requests must bypass CSRF verification."""
        policy = SessionSecurityPolicy()
        for method in ("GET", "HEAD", "OPTIONS"):
            scope: dict[str, Any] = {
                "type": "http",
                "method": method,
                "path": "/api/v1/market/symbols",
                "headers": [],
            }
            request = Request(scope)
            # Must not raise
            policy.validate_csrf(request)

    def test_session_invalidation_and_cookie_clearing(self) -> None:
        """Logout must expire and clear both session and CSRF cookies."""
        policy = SessionSecurityPolicy()
        response = Response()
        policy.clear_session_cookies(response)

        cookie_headers = response.headers.getlist("set-cookie")
        assert len(cookie_headers) == 2
        for cookie in cookie_headers:
            assert "max-age=0" in cookie.lower() or "expires=" in cookie.lower()

    def test_session_token_hashing(self, db_session: DBSession, test_user: User) -> None:
        """Raw session tokens must never be persisted in plain text in database."""
        policy = SessionSecurityPolicy()
        raw_token, csrf_token, record = policy.create_session(db_session, test_user.id)

        assert raw_token.startswith("sess_")
        assert record.session_token_hash != raw_token
        # Must match SHA-256 digest
        assert record.session_token_hash == policy.hash_token(raw_token)


# ==============================================================================
# 3. Hız Sınırı (Rate Limiting & DoS Defense)
# ==============================================================================


class TestRateLimitingSecurity:
    """Tests for sliding window rate limiting, 429 status, and client isolation."""

    def setup_method(self) -> None:
        reset_all_rate_limiters()

    def test_sliding_window_allows_within_limit(self) -> None:
        """Requests up to threshold within the window must be permitted."""
        limiter = SlidingWindowRateLimiter(times=3, seconds=60)
        req = DummyClientRequest(ip="192.168.1.100")

        # 3 requests must succeed
        for _ in range(3):
            limiter(req)  # type: ignore[arg-type]

    def test_sliding_window_blocks_excess_with_429(self) -> None:
        """Excess requests exceeding limit within window must raise HTTP 429."""
        limiter = SlidingWindowRateLimiter(times=3, seconds=60)
        req = DummyClientRequest(ip="192.168.1.101")

        for _ in range(3):
            limiter(req)  # type: ignore[arg-type]

        # 4th request must fail with 429
        with pytest.raises(HTTPException) as exc_info:
            limiter(req)  # type: ignore[arg-type]

        assert exc_info.value.status_code == 429
        headers = exc_info.value.headers
        assert headers is not None
        assert "Retry-After" in headers
        retry_after = int(headers["Retry-After"])
        assert 0 < retry_after <= 60

    def test_client_isolation_in_rate_limiter(self) -> None:
        """Rate limit exhaustion on Client A must not impact Client B."""
        limiter = SlidingWindowRateLimiter(times=2, seconds=60)
        req_a = DummyClientRequest(ip="10.0.0.1")
        req_b = DummyClientRequest(ip="10.0.0.2")

        # Exhaust Client A
        limiter(req_a)  # type: ignore[arg-type]
        limiter(req_a)  # type: ignore[arg-type]
        with pytest.raises(HTTPException):
            limiter(req_a)  # type: ignore[arg-type]

        # Client B must remain unaffected
        limiter(req_b)  # type: ignore[arg-type]
        limiter(req_b)  # type: ignore[arg-type]

    def test_tiered_rate_limiter_anon_vs_auth(self) -> None:
        """Tiered limiter must enforce stricter quota for anonymous requests."""
        tiered = TieredRateLimiter(anon_times=2, anon_seconds=60, auth_times=5, auth_seconds=60)
        req_anon = DummyClientRequest(ip="172.16.0.5")
        req_auth = DummyClientRequest(ip="172.16.0.5", auth_token="user_token_secret_123")

        # Anonymous exhausts after 2 requests
        tiered.anon_limiter(req_anon)  # type: ignore[arg-type]
        tiered.anon_limiter(req_anon)  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as exc_anon:
            tiered.anon_limiter(req_anon)  # type: ignore[arg-type]
        assert exc_anon.value.status_code == 429

        # Authenticated user has higher quota (5 calls allowed)
        for _ in range(5):
            tiered.auth_limiter(req_auth)  # type: ignore[arg-type]

        with pytest.raises(HTTPException) as exc_auth:
            tiered.auth_limiter(req_auth)  # type: ignore[arg-type]
        assert exc_auth.value.status_code == 429


# ==============================================================================
# 4. Girdi Doğrulama & Enjeksiyon Koruması (Input Validation & Injection Defense)
# ==============================================================================


class TestInputValidationSecurity:
    """Tests for SQLi, XSS, SSRF, File Upload, and Mass Assignment defenses."""

    def test_sql_injection_rejection(self) -> None:
        """InjectionGuard must detect and reject standard and stacked SQL injection patterns."""
        malicious_inputs = [
            "' OR '1'='1",
            "1; DROP TABLE users; --",
            "' UNION SELECT id, password_hash, email FROM users --",
            "admin'--",
            "THYAO' OR 1=1 --",
            "EXEC xp_cmdshell('dir')",
        ]

        for payload in malicious_inputs:
            with pytest.raises(InjectionDetectedError, match="SQL enjeksiyon"):
                InjectionGuard.assert_safe_sql_param(payload, field_name="search_query")

    def test_sql_safe_input_accepted(self) -> None:
        """Benign queries must not trigger false positives in SQL injection detector."""
        safe_inputs = [
            "THYAO",
            "GARAN",
            "BIST 100",
            "kapanis > 50",
            "Hisse senedi hacim analizi 2026",
        ]
        for valid in safe_inputs:
            InjectionGuard.assert_safe_sql_param(valid, field_name="search_query")

    def test_xss_injection_rejection(self) -> None:
        """InjectionGuard must reject cross-site scripting tags and event handler injections."""
        malicious_xss = [
            "<script>alert('XSS')</script>",
            "<img src='x' onerror='alert(document.cookie)'>",
            "<a href='javascript:stealTokens()'>click me</a>",
            "<iframe src='http://evil.com'></iframe>",
            "<svg onload=alert(1)>",
        ]

        for payload in malicious_xss:
            with pytest.raises(InjectionDetectedError, match="XSS"):
                InjectionGuard.assert_safe_xss_content(payload, field_name="user_comment")

    def test_ssrf_protection_blocks_internal_and_metadata(self) -> None:
        """SsrfProtectionGuard must reject loopback, AWS metadata, and private IP ranges."""
        dangerous_urls = [
            "http://127.0.0.1:8000/admin",
            "http://localhost:5000/metrics",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/internal/secrets",
            "http://192.168.1.1/router/config",
            "http://172.16.0.1/backend",
            "file:///etc/passwd",
            "ftp://fileserver/backup.zip",
        ]

        for url in dangerous_urls:
            with pytest.raises(SsrfViolationError):
                SsrfProtectionGuard.validate_target_url(url)

    def test_ssrf_protection_permits_public_https(self) -> None:
        """Public legitimate domains with HTTPS must be allowed for webhooks/data ingestion."""
        valid_urls = [
            "https://api.borsa.gov.tr/v1/eod",
            "https://data.kap.gov.tr/announcements",
            "https://api.github.com/repos/borai/status",
        ]
        for url in valid_urls:
            # Must not raise
            SsrfProtectionGuard.validate_target_url(url)

    def test_file_upload_guard_path_traversal(self) -> None:
        """FileUploadGuard must detect and block directory traversal attempts."""
        traversal_filenames = [
            "../../etc/passwd.csv",
            "..\\..\\windows\\system32\\cmd.exe",
            "uploads/../../../secret.json",
        ]

        guard = FileUploadGuard()
        valid_csv_bytes = b"symbol,price,volume\nTHYAO,300.5,1000000\n"

        for bad_filename in traversal_filenames:
            with pytest.raises(PathTraversalDetectedError):
                guard.validate_upload(
                    content=valid_csv_bytes,
                    filename=bad_filename,
                    content_type="text/csv",
                )

    def test_file_upload_guard_executable_extensions(self) -> None:
        """FileUploadGuard must reject executable and script extensions."""
        dangerous_files = [
            "exploit.exe",
            "malware.sh",
            "webshell.php",
            "script.py",
            "installer.bat",
            "payload.vbs",
        ]

        guard = FileUploadGuard()
        dummy_bytes = b"plain text payload"

        for bad_file in dangerous_files:
            with pytest.raises(DangerousFileExtensionError):
                guard.validate_upload(
                    content=dummy_bytes,
                    filename=bad_file,
                    content_type="text/plain",
                )

    def test_file_upload_guard_mime_spoofing(self) -> None:
        """Uploading ELF or PE executable binaries disguised as .csv must be blocked."""
        guard = FileUploadGuard()

        # Linux ELF magic bytes: \x7fELF
        fake_csv_elf = b"\x7fELF" + b"\x00" * 64
        with pytest.raises(ExecutablePayloadDetectedError):
            guard.validate_upload(
                content=fake_csv_elf,
                filename="dataset.csv",
                content_type="text/csv",
            )

        # Windows PE magic bytes: MZ
        fake_csv_pe = b"MZ" + b"\x00" * 64
        with pytest.raises(ExecutablePayloadDetectedError):
            guard.validate_upload(
                content=fake_csv_pe,
                filename="portfolio.csv",
                content_type="text/csv",
            )

    def test_mass_assignment_protection(self) -> None:
        """ObjectPropertyGuard must strip or reject forbidden internal role/admin fields."""
        payload: dict[str, Any] = {
            "name": "Ahmet Yilmaz",
            "theme": "dark",
            "is_admin": True,
            "role": "super_admin",
            "is_superuser": True,
            "password_hash": "$scrypt$attacker",
        }

        # Filtering inbound payload strips forbidden keys when strict_reject=False
        filtered = ObjectPropertyGuard.sanitize_inbound_payload(
            payload=payload,
            allowed_fields={"name", "theme"},
            strict_reject=False,
        )
        assert "is_admin" not in filtered
        assert "role" not in filtered
        assert "password_hash" not in filtered
        assert filtered == {"name": "Ahmet Yilmaz", "theme": "dark"}

        # Strict mode raises MassAssignmentError
        with pytest.raises(MassAssignmentError, match="Yetkisiz alan"):
            ObjectPropertyGuard.sanitize_inbound_payload(
                payload=payload,
                allowed_fields={"name", "theme"},
                strict_reject=True,
            )

    def test_sensitive_outbound_payload_sanitization(self) -> None:
        """ObjectPropertyGuard must redact sensitive fields from API responses."""
        outbound_data: dict[str, Any] = {
            "user_id": 42,
            "email": "user@example.com",
            "password_hash": "$scrypt$private_hash",
            "secret_key": "raw_secret_value",
            "profile": {
                "theme": "dark",
                "session_secret": "session_private_secret",
            },
        }

        sanitized = ObjectPropertyGuard.sanitize_outbound_payload(outbound_data)
        assert sanitized["user_id"] == 42
        assert sanitized["email"] == "user@example.com"
        assert "password_hash" not in sanitized
        assert "secret_key" not in sanitized
        assert "session_secret" not in sanitized["profile"]
        assert sanitized["profile"]["theme"] == "dark"

    def test_resource_consumption_guard_limits(self) -> None:
        """ResourceConsumptionGuard must reject excessive page sizes and payload bytes."""
        with pytest.raises(ResourceLimitExceededError, match="azami siniri"):
            ResourceConsumptionGuard.validate_pagination(limit=1000, offset=0, max_limit=100)

        with pytest.raises(ResourceLimitExceededError, match="negatif olamaz"):
            ResourceConsumptionGuard.validate_pagination(limit=20, offset=-5)

        with pytest.raises(ResourceLimitExceededError, match="azami izin verilen siniri"):
            ResourceConsumptionGuard.validate_payload_size(
                content_length_bytes=5 * 1024 * 1024,
                max_bytes=1024 * 1024,
            )
