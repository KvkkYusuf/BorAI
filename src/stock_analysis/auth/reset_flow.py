"""Secure, time-limited, single-use password reset flow."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar


class ResetFlowError(ValueError):
    """Raised when password reset operation fails validation."""


@dataclass(frozen=True)
class ResetTokenPayload:
    """Password reset token representation containing user identification and expiry."""

    user_id: int
    email: str
    token_hash: str
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_expired(self, current_time: datetime | None = None) -> bool:
        """Check whether the reset token has passed its expiration time."""
        now = current_time or datetime.now(UTC)
        return now >= self.expires_at


class PasswordResetManager:
    """Manages secure password reset tokens, rate limiting, and consumption."""

    DEFAULT_TTL_MINUTES: ClassVar[int] = 15
    MAX_REQUESTS_PER_WINDOW: ClassVar[int] = 3
    RATE_LIMIT_WINDOW_MINUTES: ClassVar[int] = 60

    def __init__(self, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> None:
        self.ttl = timedelta(minutes=ttl_minutes)
        # In-memory storage for pending tokens: token_hash -> ResetTokenPayload
        self._tokens: dict[str, ResetTokenPayload] = {}
        # Tracking request history for rate limiting: user_id -> list of request timestamps
        self._request_history: dict[int, list[datetime]] = {}

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Return SHA-256 hex digest of a raw token."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def mask_email(email: str) -> str:
        """Mask email address for audit logs (e.g. a***@x.com) as per security guidelines."""
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 1:
            masked_local = f"{local}***"
        else:
            masked_local = f"{local[0]}***"
        return f"{masked_local}@{domain}"

    def create_reset_token(self, user_id: int, email: str) -> tuple[str, ResetTokenPayload]:
        """Generate a cryptographically secure random token and store its digest.

        Returns:
            Tuple of (raw_token, token_payload).
            Only raw_token should be dispatched to the user.
        """
        now = datetime.now(UTC)
        self._check_rate_limit(user_id, now)

        raw_token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(raw_token)
        expires_at = now + self.ttl

        payload = ResetTokenPayload(
            user_id=user_id,
            email=email,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
        )
        self._tokens[token_hash] = payload

        # Record request timestamp for rate limiting
        self._request_history.setdefault(user_id, []).append(now)

        return raw_token, payload

    def verify_and_consume(self, raw_token: str) -> ResetTokenPayload:
        """Verify the raw token and consume it immediately (single-use).

        Raises:
            ResetFlowError if token is invalid, expired, or already used.
        """
        token_hash = self.hash_token(raw_token)

        payload = self._find_token(token_hash)
        if payload is None:
            msg = "Geçersiz veya daha önce kullanılmış sıfırlama kodu."
            raise ResetFlowError(msg)

        now = datetime.now(UTC)
        if payload.is_expired(now):
            # Clean up expired token
            self._tokens.pop(token_hash, None)
            msg = "Sıfırlama kodunun süresi dolmuş."
            raise ResetFlowError(msg)

        # Single-use: remove immediately
        self._tokens.pop(token_hash, None)
        return payload

    def revoke_all_for_user(self, user_id: int) -> int:
        """Revoke any active reset tokens for a given user."""
        to_remove = [h for h, payload in self._tokens.items() if payload.user_id == user_id]
        for h in to_remove:
            self._tokens.pop(h, None)
        return len(to_remove)

    def _find_token(self, token_hash: str) -> ResetTokenPayload | None:
        """Timing-safe lookup for a token hash."""
        for stored_hash, payload in self._tokens.items():
            if hmac.compare_digest(stored_hash, token_hash):
                return payload
        return None

    def _check_rate_limit(self, user_id: int, now: datetime) -> None:
        """Enforce rate limiting on reset token generation."""
        cutoff = now - timedelta(minutes=self.RATE_LIMIT_WINDOW_MINUTES)
        history = [ts for ts in self._request_history.get(user_id, []) if ts > cutoff]
        self._request_history[user_id] = history

        if len(history) >= self.MAX_REQUESTS_PER_WINDOW:
            msg = (
                f"Çok fazla sıfırlama talebi gönderildi. Lütfen "
                f"{self.RATE_LIMIT_WINDOW_MINUTES} dakika sonra tekrar deneyin."
            )
            raise ResetFlowError(msg)


# Module-level default manager instance
password_reset_manager = PasswordResetManager()
