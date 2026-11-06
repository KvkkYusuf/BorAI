"""Unit tests for authentication, password security, reset flows, and identity providers."""

from datetime import UTC, datetime, timedelta

import pytest

from stock_analysis.auth.hasher import PasswordHasher, PasswordPolicy, PasswordPolicyError
from stock_analysis.auth.provider import ExternalIdentity, MockIdentityProvider
from stock_analysis.auth.reset_flow import (
    PasswordResetManager,
    ResetFlowError,
    ResetTokenPayload,
)
from stock_analysis.db.models import User


class TestPasswordPolicy:
    """Tests for password policy validation."""

    def test_valid_password(self) -> None:
        policy = PasswordPolicy()
        # Should not raise
        policy.validate("Str0ng!P@ssword2026")

    def test_too_short_password(self) -> None:
        policy = PasswordPolicy(min_length=10)
        with pytest.raises(PasswordPolicyError, match="en az 10 karakter"):
            policy.validate("Sh0rt!1")

    def test_missing_uppercase(self) -> None:
        policy = PasswordPolicy()
        with pytest.raises(PasswordPolicyError, match="büyük harf"):
            policy.validate("lowercase123!@#")

    def test_missing_lowercase(self) -> None:
        policy = PasswordPolicy()
        with pytest.raises(PasswordPolicyError, match="küçük harf"):
            policy.validate("UPPERCASE123!@#")

    def test_missing_digit(self) -> None:
        policy = PasswordPolicy()
        with pytest.raises(PasswordPolicyError, match="rakam"):
            policy.validate("NoDigitsHere!@#")

    def test_missing_special(self) -> None:
        policy = PasswordPolicy()
        with pytest.raises(PasswordPolicyError, match="özel karakter"):
            policy.validate("NoSpecialChars123")


class TestPasswordHasher:
    """Tests for scrypt-based password hashing."""

    @pytest.fixture
    def fast_hasher(self) -> PasswordHasher:
        # Using smaller n for fast unit testing while keeping structure identical
        return PasswordHasher(n=1024, r=8, p=1)

    def test_hash_and_verify_success(self, fast_hasher: PasswordHasher) -> None:
        pwd = "SecureP@ssw0rd123"
        hashed = fast_hasher.hash(pwd)
        assert hashed.startswith("$scrypt$")
        assert fast_hasher.verify(pwd, hashed) is True

    def test_verify_wrong_password_returns_false(self, fast_hasher: PasswordHasher) -> None:
        pwd = "SecureP@ssw0rd123"
        hashed = fast_hasher.hash(pwd)
        assert fast_hasher.verify("WrongP@ssw0rd999", hashed) is False

    def test_verify_invalid_hash_format(self, fast_hasher: PasswordHasher) -> None:
        assert fast_hasher.verify("any", "invalid_format") is False
        assert fast_hasher.verify("any", "$scrypt$bad$parts") is False

    def test_salt_uniqueness(self, fast_hasher: PasswordHasher) -> None:
        pwd = "SecureP@ssw0rd123"
        hash1 = fast_hasher.hash(pwd)
        hash2 = fast_hasher.hash(pwd)
        assert hash1 != hash2
        assert fast_hasher.verify(pwd, hash1) is True
        assert fast_hasher.verify(pwd, hash2) is True

    def test_needs_rehash(self) -> None:
        hasher1 = PasswordHasher(n=1024, r=8, p=1)
        hasher2 = PasswordHasher(n=2048, r=8, p=1)
        hash1 = hasher1.hash("SecureP@ssw0rd123")
        assert hasher1.needs_rehash(hash1) is False
        assert hasher2.needs_rehash(hash1) is True


class TestPasswordResetManager:
    """Tests for secure password reset flow."""

    def test_create_and_consume_token(self) -> None:
        mgr = PasswordResetManager(ttl_minutes=15)
        raw_token, payload = mgr.create_reset_token(user_id=42, email="trader@example.com")

        assert len(raw_token) > 20
        assert payload.user_id == 42
        assert payload.email == "trader@example.com"
        assert not payload.is_expired()

        # Successfully consume
        consumed = mgr.verify_and_consume(raw_token)
        assert consumed.user_id == 42

        # Second consumption must fail (single-use)
        with pytest.raises(ResetFlowError, match="Geçersiz veya daha önce kullanılmış"):
            mgr.verify_and_consume(raw_token)

    def test_expired_token(self) -> None:
        mgr = PasswordResetManager(ttl_minutes=15)
        raw_token, payload = mgr.create_reset_token(user_id=1, email="test@example.com")

        # Simulate expiration by setting past expiry
        expired_payload = ResetTokenPayload(
            user_id=payload.user_id,
            email=payload.email,
            token_hash=payload.token_hash,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        mgr._tokens[payload.token_hash] = expired_payload

        with pytest.raises(ResetFlowError, match="süresi dolmuş"):
            mgr.verify_and_consume(raw_token)

    def test_rate_limiting(self) -> None:
        mgr = PasswordResetManager(ttl_minutes=15)
        user_id = 99
        # Max is 3 requests per window
        for _ in range(3):
            mgr.create_reset_token(user_id=user_id, email="limit@example.com")

        with pytest.raises(ResetFlowError, match="Çok fazla sıfırlama talebi"):
            mgr.create_reset_token(user_id=user_id, email="limit@example.com")

    def test_revoke_all_for_user(self) -> None:
        mgr = PasswordResetManager()
        raw1, _ = mgr.create_reset_token(user_id=10, email="user10@example.com")
        raw2, _ = mgr.create_reset_token(user_id=10, email="user10@example.com")

        revoked_count = mgr.revoke_all_for_user(10)
        assert revoked_count == 2

        with pytest.raises(ResetFlowError):
            mgr.verify_and_consume(raw1)
        with pytest.raises(ResetFlowError):
            mgr.verify_and_consume(raw2)

    def test_mask_email(self) -> None:
        assert PasswordResetManager.mask_email("bora@example.com") == "b***@example.com"
        assert PasswordResetManager.mask_email("a@b.com") == "a***@b.com"
        assert PasswordResetManager.mask_email("invalid") == "***"


class TestIdentityProviderIntegration:
    """Tests for external identity provider adapter."""

    @pytest.mark.asyncio
    async def test_mock_identity_exchange(self) -> None:
        provider = MockIdentityProvider(provider_name="google_oauth")
        identity = ExternalIdentity(
            provider="google_oauth",
            provider_user_id="google-sub-12345",
            email="investor@example.com",
            is_verified=True,
            display_name="Borsa Yatırımcısı",
        )
        provider.register_test_identity("valid_auth_code_xyz", identity)

        exchanged = await provider.exchange_code_for_identity("valid_auth_code_xyz")
        assert exchanged.provider_user_id == "google-sub-12345"
        assert exchanged.email == "investor@example.com"
        assert exchanged.is_verified is True

        # External provider user doesn't require a local password hash
        user = User(
            email=exchanged.email,
            hashed_password=None,  # SSO user: no local password to manage
            is_active=True,
            is_verified=exchanged.is_verified,
        )
        assert user.hashed_password is None
        assert user.is_verified is True

    def test_authorization_url(self) -> None:
        provider = MockIdentityProvider("my_sso")
        url = provider.get_authorization_url(
            state="random_state", redirect_uri="http://localhost/callback"
        )
        assert "my_sso" in url
        assert "random_state" in url
