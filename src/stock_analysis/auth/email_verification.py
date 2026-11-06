"""Secure, time-limited, single-use email verification flow."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar


class EmailVerificationError(ValueError):
    """Raised when email verification token validation fails."""


@dataclass(frozen=True)
class EmailVerificationTokenPayload:
    """Email verification token representation containing user identification and expiry."""

    user_id: int
    email: str
    token_hash: str
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_expired(self, current_time: datetime | None = None) -> bool:
        """Check whether the verification token has passed its expiration time."""
        now = current_time or datetime.now(UTC)
        return now >= self.expires_at


class EmailVerificationManager:
    """Manages cryptographic email verification tokens, rate limiting, and consumption."""

    DEFAULT_TTL_HOURS: ClassVar[int] = 24
    MAX_REQUESTS_PER_WINDOW: ClassVar[int] = 5
    RATE_LIMIT_WINDOW_MINUTES: ClassVar[int] = 60

    def __init__(self, ttl_hours: int = DEFAULT_TTL_HOURS) -> None:
        self.ttl = timedelta(hours=ttl_hours)
        # In-memory storage for pending tokens: token_hash -> EmailVerificationTokenPayload
        self._tokens: dict[str, EmailVerificationTokenPayload] = {}
        # Tracking request history for rate limiting: user_id -> list of request timestamps
        self._request_history: dict[int, list[datetime]] = {}

    def reset(self) -> None:
        """Reset internal in-memory token store and rate limit history (primarily for tests)."""
        self._tokens.clear()
        self._request_history.clear()

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Return SHA-256 hex digest of a raw token."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def mask_email(email: str) -> str:
        """Mask email address for audit logs (e.g. a***@x.com) to prevent PII leakage."""
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 1:
            masked_local = f"{local}***"
        else:
            masked_local = f"{local[0]}***"
        return f"{masked_local}@{domain}"

    def create_verification_token(
        self,
        user_id: int,
        email: str,
        ttl_hours: int | None = None,
    ) -> tuple[str, EmailVerificationTokenPayload]:
        """Generate a cryptographically secure random token and store its digest.

        Returns:
            Tuple of (raw_token, token_payload).
            Only raw_token should be dispatched to the user.
        """
        now = datetime.now(UTC)
        self._check_rate_limit(user_id, now)

        raw_token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(raw_token)
        ttl = timedelta(hours=ttl_hours) if ttl_hours is not None else self.ttl
        expires_at = now + ttl

        payload = EmailVerificationTokenPayload(
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

    def verify_and_consume(self, raw_token: str) -> EmailVerificationTokenPayload:
        """Verify the raw token and consume it immediately (single-use).

        Raises:
            EmailVerificationError if token is invalid, expired, or already used.
        """
        token_hash = self.hash_token(raw_token)

        payload = self._find_token(token_hash)
        if payload is None:
            msg = "Geçersiz veya daha önce kullanılmış doğrulama bağlantısı."
            raise EmailVerificationError(msg)

        now = datetime.now(UTC)
        if payload.is_expired(now):
            self._tokens.pop(token_hash, None)
            msg = "Doğrulama bağlantısının süresi dolmuş."
            raise EmailVerificationError(msg)

        # Single-use: remove immediately
        self._tokens.pop(token_hash, None)
        return payload

    def revoke_all_for_user(self, user_id: int) -> int:
        """Revoke any active verification tokens for a given user."""
        to_remove = [h for h, payload in self._tokens.items() if payload.user_id == user_id]
        for h in to_remove:
            self._tokens.pop(h, None)
        return len(to_remove)

    def _find_token(self, token_hash: str) -> EmailVerificationTokenPayload | None:
        """Timing-safe lookup for a token hash."""
        for stored_hash, payload in self._tokens.items():
            if hmac.compare_digest(stored_hash, token_hash):
                return payload
        return None

    def _check_rate_limit(self, user_id: int, now: datetime) -> None:
        """Enforce rate limiting on verification token generation."""
        cutoff = now - timedelta(minutes=self.RATE_LIMIT_WINDOW_MINUTES)
        history = [ts for ts in self._request_history.get(user_id, []) if ts > cutoff]
        self._request_history[user_id] = history

        if len(history) >= self.MAX_REQUESTS_PER_WINDOW:
            msg = (
                f"Çok fazla doğrulama talebi gönderildi. Lütfen "
                f"{self.RATE_LIMIT_WINDOW_MINUTES} dakika sonra tekrar deneyin."
            )
            raise EmailVerificationError(msg)


# Module-level default manager instance
email_verification_manager = EmailVerificationManager()
