"""Unit and Integration Tests for Logging and Error Tracking Sanitization."""
# pragma: allow-pii-test-suite

from __future__ import annotations

import io
import logging
import sys
from typing import Any

import pytest

from stock_analysis.security.logging_sanitizer import (
    ErrorTrackingSanitizer,
    SanitizedLogFormatter,
    SensitiveDataFilter,
    SensitiveDataLeakError,
    configure_secure_logging,
    sanitize_data_structure,
    sanitize_text,
)


class TestTextSanitization:
    """Test string-level scrubbing of credentials, tokens, and PII."""

    def test_scrubs_password_and_secret_assignments(self) -> None:
        raw = "User login failed: password='superSecretPassword123' and api_key=sk_live_998877"
        sanitized = sanitize_text(raw)
        assert "superSecretPassword123" not in sanitized
        assert "sk_live_998877" not in sanitized
        assert "password='[REDACTED]'" in sanitized
        assert "api_key=[REDACTED]" in sanitized

    def test_scrubs_bearer_and_jwt_tokens(self) -> None:
        jwt_token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        raw = f"Incoming request with header Bearer {jwt_token}"
        sanitized = sanitize_text(raw)
        assert jwt_token not in sanitized
        assert "[REDACTED]" in sanitized or "[JWT_TOKEN_REDACTED]" in sanitized

    def test_scrubs_database_connection_url(self) -> None:
        db_url = "postgresql+asyncpg://admin:superSecretDbPass123@db.prod.internal:5432/borsa_prod"
        sanitized = sanitize_text(f"Connecting to database: {db_url}")
        assert "superSecretDbPass123" not in sanitized
        assert "postgresql+asyncpg://admin:[REDACTED]@db.prod.internal:5432/borsa_prod" in sanitized

    def test_scrubs_http_basic_auth_url(self) -> None:
        url = "https://api_user:super_secret_token@provider.marketdata.com/v1/feed"
        sanitized = sanitize_text(f"Fetching from {url}")
        assert "super_secret_token" not in sanitized
        assert "https://api_user:[REDACTED]@provider.marketdata.com/v1/feed" in sanitized

    def test_scrubs_private_key_blocks(self) -> None:
        key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y31L91z...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        raw = f"Loaded private key:\n{key}"
        sanitized = sanitize_text(raw)
        assert "MIIEowIBAAKCAQEA0Y31L91z" not in sanitized
        assert "[REDACTED_PRIVATE_KEY]" in sanitized

    def test_masks_emails_according_to_kvkk(self) -> None:
        raw = "User email is ahmet.yilmaz@borsa-analiz.com and backup is ayse@test.org"
        sanitized = sanitize_text(raw)
        assert "ahmet.yilmaz@borsa-analiz.com" not in sanitized
        assert "ayse@test.org" not in sanitized
        assert "a***@borsa-analiz.com" in sanitized
        assert "a***@test.org" in sanitized

    def test_masks_turkish_phone_numbers(self) -> None:
        raw = "Notification sent to +90 532 987 65 43 or 0544 111 22 33"
        sanitized = sanitize_text(raw)
        assert "987 65 43" not in sanitized
        assert "111 22 33" not in sanitized
        assert "+90 532 *** **43" in sanitized
        assert "+90 544 *** **33" in sanitized

    def test_masks_turkish_tckn(self) -> None:
        raw = "Verified identity with TCKN: 12345678901 and kimlik_no=98765432109"
        sanitized = sanitize_text(raw)
        assert "12345678901" not in sanitized
        assert "98765432109" not in sanitized
        assert "***********" in sanitized


class TestDataStructureSanitization:
    """Test recursive scrubbing of nested structures and dict keys."""

    def test_recursively_redacts_sensitive_keys(self) -> None:
        payload: dict[str, Any] = {
            "user_id": 42,
            "profile": {
                "email": "investor@example.com",
                "password": "ClearTextPassword!",
                "metadata": {
                    "api_key": "sec_key_12345",
                    "auth_token": "token_abc",
                },
            },
            "tags": ["finance", "vip"],
        }
        clean = sanitize_data_structure(payload)

        assert clean["user_id"] == 42
        assert clean["profile"]["email"] == "i***@example.com"
        assert clean["profile"]["password"] == "[REDACTED]"
        assert clean["profile"]["metadata"]["api_key"] == "[REDACTED]"
        assert clean["profile"]["metadata"]["auth_token"] == "[REDACTED]"
        assert clean["tags"] == ["finance", "vip"]

    def test_scrubs_nested_lists_and_tuples(self) -> None:
        data = [
            ("user1", "user1@domain.com", {"token": "secret_abc"}),
            ("user2", "user2@domain.com", {"token": "secret_def"}),
        ]
        clean = sanitize_data_structure(data)
        assert clean[0][1] == "u***@domain.com"
        assert clean[0][2]["token"] == "[REDACTED]"
        assert clean[1][1] == "u***@domain.com"
        assert clean[1][2]["token"] == "[REDACTED]"


class TestLoggingFilterAndFormatter:
    """Test standard logging integration with SensitiveDataFilter and SanitizedLogFormatter."""

    def test_filter_scrubs_log_record_msg(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test.filter.msg")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.addFilter(SensitiveDataFilter())

        logger.info("Admin updated password='superSecret123' for user john.doe@example.com")
        output = stream.getvalue()

        assert "superSecret123" not in output
        assert "john.doe@example.com" not in output
        assert "password='[REDACTED]'" in output
        assert "j***@example.com" in output

    def test_filter_scrubs_interpolated_args(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test.filter.args")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.addFilter(SensitiveDataFilter())

        email = "alice@example.com"
        token = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc"
        logger.info("Authentication request for %s with token %s", email, token)
        output = stream.getvalue()

        assert "alice@example.com" not in output
        assert "a***@example.com" in output
        assert "Bearer [REDACTED]" in output

    def test_filter_scrubs_extra_dict(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s | %(extra_data)s"))

        logger = logging.getLogger("test.filter.extra")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.addFilter(SensitiveDataFilter())

        logger.info(
            "Security event",
            extra={
                "extra_data": {
                    "secret": "mySecret123",
                    "email": "target@domain.com",
                    "safe_field": 100,
                }
            },
        )
        output = stream.getvalue()

        assert "mySecret123" not in output
        assert "target@domain.com" not in output
        assert "[REDACTED]" in output
        assert "t***@domain.com" in output

    def test_formatter_sanitizes_tracebacks(self) -> None:
        formatter = SanitizedLogFormatter(fmt="%(levelname)s: %(message)s")

        try:
            db_conn = "postgresql://user:leaked_db_pass@host:5432/db"
            raise ValueError(f"Database connection failed with string {db_conn}")
        except ValueError:
            record = logging.LogRecord(
                name="test.exc",
                level=logging.ERROR,
                pathname="test.py",
                lineno=42,
                msg="An error occurred",
                args=(),
                exc_info=sys.exc_info(),
            )

        formatted = formatter.format(record)
        assert "leaked_db_pass" not in formatted
        assert "postgresql://user:[REDACTED]@host:5432/db" in formatted


class TestErrorTrackingSanitizer:
    """Test scrubbing and verification of error monitoring event payloads."""

    @pytest.fixture
    def sample_event(self) -> dict[str, Any]:
        return {
            "event_id": "c9a62629b34a42b9921",
            "timestamp": "2026-10-25T10:00:00Z",
            "request": {
                "url": "https://api.borsa.com/v1/auth/login",
                "method": "POST",
                "headers": {
                    "Host": "api.borsa.com",
                    "Authorization": "Bearer raw_jwt_or_secret_token_12345",
                    "Cookie": "session_id=s_1234567890abcdef; theme=dark",
                    "X-Api-Key": "live_key_998877",
                    "User-Agent": "Mozilla/5.0",
                },
                "query_params": {
                    "access_token": "token_in_query_param",
                    "format": "json",
                },
                "data": {
                    "email": "trader@borsa.com",
                    "password": "PlainTextPassword456",
                    "remember_me": True,
                },
            },
            "user": {
                "id": "usr_99",
                "email": "trader@borsa.com",
                "ip_address": "198.51.100.42",
            },
            "breadcrumbs": [
                {
                    "category": "auth",
                    "message": "User trader@borsa.com initiated login with secret=abc12345",
                }
            ],
            "exception": {
                "values": [
                    {
                        "type": "DatabaseError",
                        "value": "Failed connecting to postgresql://admin:secretPassword99@db:5432/borsa",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "app.py",
                                    "lineno": 105,
                                    "vars": {
                                        "user_email": "trader@borsa.com",
                                        "api_key": "very_secret_key",
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        }

    def test_sanitize_event_scrubs_all_sensitive_fields(self, sample_event: dict[str, Any]) -> None:
        sanitizer = ErrorTrackingSanitizer(ip_salt="test_salt_123")
        clean_event = sanitizer.sanitize_event(sample_event)

        # 1. Request headers
        headers = clean_event["request"]["headers"]
        assert headers["Authorization"] == "[REDACTED]"
        assert headers["Cookie"] == "[REDACTED]"
        assert headers["X-Api-Key"] == "[REDACTED]"
        assert headers["User-Agent"] == "Mozilla/5.0"

        # 2. Query params
        query_params = clean_event["request"]["query_params"]
        assert query_params["access_token"] == "[REDACTED]"
        assert query_params["format"] == "json"

        # 3. Request body
        data = clean_event["request"]["data"]
        assert data["email"] == "t***@borsa.com"
        assert data["password"] == "[REDACTED]"
        assert data["remember_me"] is True

        # 4. User info
        user_info = clean_event["user"]
        assert user_info["email"] == "t***@borsa.com"
        assert user_info["ip_address"] != "198.51.100.42"
        assert len(user_info["ip_address"]) == 64  # SHA-256 hash length

        # 5. Breadcrumbs
        crumb = clean_event["breadcrumbs"][0]
        assert "trader@borsa.com" not in crumb["message"]
        assert "t***@borsa.com" in crumb["message"]
        assert "abc12345" not in crumb["message"]
        assert "secret=[REDACTED]" in crumb["message"]

        # 6. Exception & Stacktrace vars
        exc_val = clean_event["exception"]["values"][0]
        assert "secretPassword99" not in exc_val["value"]
        assert "postgresql://admin:[REDACTED]@db:5432/borsa" in exc_val["value"]
        frame_vars = exc_val["stacktrace"]["frames"][0]["vars"]
        assert frame_vars["user_email"] == "t***@borsa.com"
        assert frame_vars["api_key"] == "[REDACTED]"

    def test_verify_clean_event_validates_scrubbed_payload(
        self, sample_event: dict[str, Any]
    ) -> None:
        sanitizer = ErrorTrackingSanitizer()

        # Raw event should fail verification
        is_clean_raw, violations_raw = sanitizer.verify_clean_event(sample_event)
        assert not is_clean_raw
        assert len(violations_raw) > 0

        # Sanitized event should pass verification
        clean_event = sanitizer.sanitize_event(sample_event)
        is_clean_scrubbed, violations_scrubbed = sanitizer.verify_clean_event(clean_event)
        assert is_clean_scrubbed
        assert violations_scrubbed == []

        # Calling assert_clean_event should not raise
        sanitizer.assert_clean_event(clean_event)

    def test_assert_clean_event_raises_on_leak(self) -> None:
        sanitizer = ErrorTrackingSanitizer()
        leaky_event = {
            "user": {"email": "plain.leak@example.com"},
            "extra": {"db_password": "unmasked_password"},
        }
        with pytest.raises(SensitiveDataLeakError) as exc_info:
            sanitizer.assert_clean_event(leaky_event)

        assert "Sensitive leaks" in str(exc_info.value) or "sensitive leaks" in str(exc_info.value)


class TestConfigureSecureLogging:
    """Test global logging configuration helper."""

    def test_configures_loggers_with_filter(self) -> None:
        flt = configure_secure_logging(root_level=logging.DEBUG, attach_to_root=True)
        assert isinstance(flt, SensitiveDataFilter)

        root = logging.getLogger()
        assert any(isinstance(f, SensitiveDataFilter) for f in root.filters)

        sa_logger = logging.getLogger("stock_analysis")
        assert any(isinstance(f, SensitiveDataFilter) for f in sa_logger.filters)
