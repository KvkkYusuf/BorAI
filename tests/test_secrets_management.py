"""Unit and integration tests for SecretManager, key rotation, and secret masking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stock_analysis.security.secrets import (
    SecretCompromisedError,
    SecretManager,
    SecretNotFoundError,
    SecretStatus,
    SecretValue,
    generate_cryptographic_secret,
    get_default_secret_manager,
    reset_default_secret_manager,
)


class TestSecretValue:
    """Tests for SecretValue encapsulation, redaction, and timing-safe comparison."""

    def test_non_string_initialization_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Secret value must be a string"):
            SecretValue(12345)  # type: ignore[arg-type]

    def test_get_secret_value_exposes_raw_content(self) -> None:
        raw = "super-secret-api-token-987"
        sec = SecretValue(raw)
        assert sec.get_secret_value() == raw

    def test_repr_and_str_prevent_leakage(self) -> None:
        raw = "sensitive-jwt-signing-key-xyz"
        sec = SecretValue(raw)

        repr_out = repr(sec)
        str_out = str(sec)

        assert raw not in repr_out
        assert repr_out == "SecretValue(***)"
        assert raw not in str_out
        assert str_out == "[REDACTED]"

    def test_masked_display_behavior(self) -> None:
        sec = SecretValue("sk_live_1234567890abcdef")
        masked = sec.masked_display(prefix_len=7, suffix_len=4)
        assert masked == "sk_live...cdef"

        short_sec = SecretValue("short")
        assert short_sec.masked_display() == "***"

    def test_equals_and_timing_safe_comparison(self) -> None:
        sec1 = SecretValue("token-abc-123")
        sec2 = SecretValue("token-abc-123")
        sec_diff = SecretValue("token-diff-456")

        assert sec1.equals("token-abc-123")
        assert sec1.equals(sec2)
        assert not sec1.equals("wrong-token")
        assert not sec1.equals(sec_diff)
        assert sec1 == sec2
        assert sec1 != sec_diff
        assert sec1 == "token-abc-123"
        assert sec1 != 12345

    def test_hash_consistency(self) -> None:
        sec1 = SecretValue("same-token")
        sec2 = SecretValue("same-token")
        assert hash(sec1) == hash(sec2)
        s = {sec1}
        assert sec2 in s


class TestSecretManagerLifecycle:
    """Tests for SecretManager storage, multi-versioning, rotation, and revocation."""

    def test_set_and_get_secret(self) -> None:
        mgr = SecretManager()
        entry = mgr.set_secret("db_password", "postgres_secret_pass_123", rotation_interval_days=60)

        assert entry.metadata.name == "db_password"
        assert entry.metadata.version == 1
        assert entry.metadata.status == SecretStatus.ACTIVE
        assert entry.metadata.rotation_interval_days == 60
        assert entry.metadata.expires_at is not None

        retrieved = mgr.get_secret("db_password")
        assert retrieved.get_secret_value() == "postgres_secret_pass_123"
        assert mgr.get_raw_value("db_password") == "postgres_secret_pass_123"

    def test_get_nonexistent_secret_raises_error(self) -> None:
        mgr = SecretManager()
        with pytest.raises(SecretNotFoundError, match="Secret 'unknown_key' not found"):
            mgr.get_secret("unknown_key")

        with pytest.raises(SecretNotFoundError, match="Secret 'unknown_key' not found"):
            mgr.get_entry("unknown_key")

    def test_secret_rotation_creates_new_version_and_marks_old_rotating(self) -> None:
        mgr = SecretManager()
        mgr.set_secret("session_key", "old_session_key_v1")

        rotated_entry = mgr.rotate_secret("session_key", new_value="new_session_key_v2")

        assert rotated_entry.metadata.version == 2
        assert rotated_entry.metadata.status == SecretStatus.ACTIVE
        assert rotated_entry.value.get_secret_value() == "new_session_key_v2"

        # Check v1 status
        v1_entry = mgr.get_entry("session_key", version=1)
        assert v1_entry.metadata.status == SecretStatus.ROTATING
        assert v1_entry.value.get_secret_value() == "old_session_key_v1"

        # Default get_secret returns new active version (v2)
        assert mgr.get_raw_value("session_key") == "new_session_key_v2"

    def test_secret_rotation_auto_generates_value_when_none(self) -> None:
        mgr = SecretManager()
        mgr.set_secret("api_token", "initial_val")

        rotated = mgr.rotate_secret("api_token")
        assert rotated.metadata.version == 2
        assert len(rotated.value.get_secret_value()) >= 32
        assert rotated.value.get_secret_value() != "initial_val"

    def test_rotating_nonexistent_secret_raises_error(self) -> None:
        mgr = SecretManager()
        with pytest.raises(SecretNotFoundError, match="Cannot rotate nonexistent secret"):
            mgr.rotate_secret("ghost_key")

    def test_revocation_marks_secret_compromised_and_blocks_access(self) -> None:
        mgr = SecretManager()
        mgr.set_secret("jwt_key", "compromised_secret_value")

        mgr.revoke_secret("jwt_key", reason="Key leaked in public repo")

        entry = mgr.get_entry("jwt_key")
        assert entry.metadata.status == SecretStatus.REVOKED
        assert entry.metadata.compromised is True

        with pytest.raises(SecretCompromisedError, match="has been revoked"):
            mgr.get_secret("jwt_key")


class TestGracePeriodValidation:
    """Tests for zero-downtime key rollovers and grace period validation."""

    def test_validate_with_grace_period_accepts_active_and_rotating_keys(self) -> None:
        mgr = SecretManager()
        t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

        mgr.set_secret(
            "signing_secret",
            "key_v1",
            rotation_interval_days=30,
            grace_period_days=7,
            as_of=t0,
        )

        # Before rotation: key_v1 is valid
        assert mgr.validate_with_grace_period("signing_secret", lambda k: k == "key_v1", as_of=t0)
        assert not mgr.validate_with_grace_period(
            "signing_secret", lambda k: k == "key_v2", as_of=t0
        )

        # Rotate at t1 (+30 days)
        t1 = t0 + timedelta(days=30)
        mgr.rotate_secret("signing_secret", new_value="key_v2", as_of=t1)

        # Both new active (key_v2) and previous rotating (key_v1) are valid within grace period
        assert mgr.validate_with_grace_period("signing_secret", lambda k: k == "key_v2", as_of=t1)
        assert mgr.validate_with_grace_period("signing_secret", lambda k: k == "key_v1", as_of=t1)

        # After grace period expires (t1 + 8 days > 7 days grace)
        t_expired = t1 + timedelta(days=8)
        assert mgr.validate_with_grace_period(
            "signing_secret", lambda k: k == "key_v2", as_of=t_expired
        )
        # key_v1 must now be rejected
        assert not mgr.validate_with_grace_period(
            "signing_secret", lambda k: k == "key_v1", as_of=t_expired
        )

    def test_revoked_key_is_immediately_rejected_during_grace_period(self) -> None:
        mgr = SecretManager()
        mgr.set_secret("auth_secret", "secret_v1", grace_period_days=14)
        mgr.rotate_secret("auth_secret", new_value="secret_v2")

        # Immediately revoke v1
        mgr.revoke_secret("auth_secret", version=1, reason="Emergency revocation")

        # secret_v2 is active and valid
        assert mgr.validate_with_grace_period("auth_secret", lambda k: k == "secret_v2")
        # secret_v1 must be rejected immediately despite grace period
        assert not mgr.validate_with_grace_period("auth_secret", lambda k: k == "secret_v1")

    def test_validate_nonexistent_secret_returns_false(self) -> None:
        mgr = SecretManager()
        assert not mgr.validate_with_grace_period("missing_secret", lambda k: True)


class TestRotationAudit:
    """Tests for scheduled rotation auditing and status reporting."""

    def test_check_rotation_status_reports_due_and_overdue_correctly(self) -> None:
        mgr = SecretManager()
        base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        mgr.set_secret("market_api_key", "sec123", rotation_interval_days=90, as_of=base_time)

        # 30 days after creation: not due (60 days left)
        check1 = mgr.check_rotation_status("market_api_key", as_of=base_time + timedelta(days=30))
        assert not check1.is_due
        assert round(check1.days_until_due) == 60
        assert "Rotation due in 60.0 days" in check1.message

        # 95 days after creation: overdue by 5 days
        check2 = mgr.check_rotation_status("market_api_key", as_of=base_time + timedelta(days=95))
        assert check2.is_due
        assert round(check2.days_until_due) == -5
        assert "overdue by 5.0 days" in check2.message

    def test_audit_rotations_evaluates_all_registered_secrets(self) -> None:
        mgr = SecretManager()
        t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        mgr.set_secret("key_a", "val_a", rotation_interval_days=30, as_of=t0)
        mgr.set_secret("key_b", "val_b", rotation_interval_days=90, as_of=t0)

        # Check at day 45: key_a is due, key_b is not
        audits = mgr.audit_rotations(as_of=t0 + timedelta(days=45))
        assert len(audits) == 2
        due_map = {r.name: r.is_due for r in audits}
        assert due_map["key_a"] is True
        assert due_map["key_b"] is False


class TestEnvironmentIntegrationAndSingleton:
    """Tests for environment variable ingestion, helper utilities, and singleton management."""

    def test_load_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVICE_SECRET", "super-env-value-123")
        mgr = SecretManager()

        mgr.load_from_environment({"service_key": "TEST_SERVICE_SECRET"})
        assert mgr.get_raw_value("service_key") == "super-env-value-123"

        # Update env and reload: should trigger rotation
        monkeypatch.setenv("TEST_SERVICE_SECRET", "super-env-value-456")
        mgr.load_from_environment({"service_key": "TEST_SERVICE_SECRET"})

        assert mgr.get_raw_value("service_key") == "super-env-value-456"
        assert mgr.get_entry("service_key").metadata.version == 2
        assert mgr.get_entry("service_key", version=1).metadata.status == SecretStatus.ROTATING

    def test_generate_cryptographic_secret_entropy(self) -> None:
        token1 = generate_cryptographic_secret(32, prefix="app_sec_")
        token2 = generate_cryptographic_secret(32, prefix="app_sec_")

        assert token1.startswith("app_sec_")
        assert token2.startswith("app_sec_")
        assert token1 != token2
        assert len(token1) >= 40

    def test_default_secret_manager_singleton(self) -> None:
        reset_default_secret_manager()
        mgr1 = get_default_secret_manager()
        mgr2 = get_default_secret_manager()

        assert mgr1 is mgr2
        assert "session_secret" in mgr1.list_secrets()
        assert "market_data_api_key" in mgr1.list_secrets()

        reset_default_secret_manager()
        mgr3 = get_default_secret_manager()
        assert mgr3 is not mgr1
