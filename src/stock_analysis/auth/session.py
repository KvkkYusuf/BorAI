"""Session cookie management, lifetime policy, and Double-Submit CSRF protection."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.db.models import Session as UserSession
from stock_analysis.db.models import User


class SessionSecurityPolicy:
    """Security configurations for session cookies, expiration, and CSRF protection."""

    SESSION_COOKIE_NAME: ClassVar[str] = "borai_session"
    CSRF_COOKIE_NAME: ClassVar[str] = "borai_csrf"
    CSRF_HEADER_NAME: ClassVar[str] = "x-csrf-token"

    DEFAULT_TTL_DAYS: ClassVar[int] = 14
    MAX_TTL_DAYS: ClassVar[int] = 30

    def __init__(
        self,
        *,
        ttl_days: int = DEFAULT_TTL_DAYS,
        secure_cookie: bool = True,
        samesite: str = "lax",
    ) -> None:
        self.ttl = timedelta(days=min(ttl_days, self.MAX_TTL_DAYS))
        self.secure_cookie = secure_cookie
        self.samesite = samesite

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Return SHA-256 hex digest of a raw token."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def create_session(
        self,
        db: DBSession,
        user_id: int,
        ip_hash: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, str, UserSession]:
        """Generate cryptographically secure session token and CSRF token, storing session in DB.

        Returns:
            Tuple of (raw_session_token, csrf_token, session_record).
        """
        raw_session_token = f"sess_{secrets.token_urlsafe(32)}"
        csrf_token = secrets.token_urlsafe(32)
        session_hash = self.hash_token(raw_session_token)

        now = datetime.now(UTC)
        expires_at = now + self.ttl

        session_record = UserSession(
            user_id=user_id,
            session_token_hash=session_hash,
            provider="local",
            expires_at=expires_at,
            ip_hash=ip_hash,
            user_agent=user_agent,
            created_at=now,
        )
        db.add(session_record)
        db.commit()
        db.refresh(session_record)

        return raw_session_token, csrf_token, session_record

    def set_session_cookies(
        self,
        response: Response,
        raw_session_token: str,
        csrf_token: str,
    ) -> None:
        """Attach HttpOnly session cookie and Double-Submit CSRF cookie to response."""
        max_age = int(self.ttl.total_seconds())

        # Session cookie: strictly HttpOnly, Secure, SameSite=lax
        response.set_cookie(
            key=self.SESSION_COOKIE_NAME,
            value=raw_session_token,
            max_age=max_age,
            expires=max_age,
            path="/",
            domain=None,
            secure=self.secure_cookie,
            httponly=True,
            samesite=self.samesite,  # type: ignore[arg-type]
        )

        # CSRF cookie: readable by frontend JS for header attachment, Secure, SameSite=lax
        response.set_cookie(
            key=self.CSRF_COOKIE_NAME,
            value=csrf_token,
            max_age=max_age,
            expires=max_age,
            path="/",
            domain=None,
            secure=self.secure_cookie,
            httponly=False,
            samesite=self.samesite,  # type: ignore[arg-type]
        )

    def clear_session_cookies(self, response: Response) -> None:
        """Expire and remove session and CSRF cookies."""
        response.delete_cookie(
            key=self.SESSION_COOKIE_NAME,
            path="/",
            secure=self.secure_cookie,
            httponly=True,
            samesite=self.samesite,  # type: ignore[arg-type]
        )
        response.delete_cookie(
            key=self.CSRF_COOKIE_NAME,
            path="/",
            secure=self.secure_cookie,
            httponly=False,
            samesite=self.samesite,  # type: ignore[arg-type]
        )

    def validate_csrf(self, request: Request) -> None:
        """Validate Double-Submit CSRF token for cookie-authenticated state-changing requests.

        Raises:
            HTTPException 403 if CSRF validation fails.
        """
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return

        header_token = request.headers.get(self.CSRF_HEADER_NAME)
        cookie_token = request.cookies.get(self.CSRF_COOKIE_NAME)

        if not header_token or not cookie_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "CSRF_MISSING",
                    "message": "CSRF belirteci eksik. Lütfen sayfayı yenileyip tekrar deneyin.",
                },
            )

        if not hmac.compare_digest(header_token, cookie_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "CSRF_INVALID",
                    "message": "Geçersiz CSRF belirteci.",
                },
            )

    def resolve_session(
        self,
        db: DBSession,
        raw_session_token: str,
    ) -> User | None:
        """Look up user for given active session token; returns None if invalid or expired."""
        session_hash = self.hash_token(raw_session_token)
        now = datetime.now(UTC)

        session_record = db.execute(
            select(UserSession).where(
                UserSession.session_token_hash == session_hash,
                UserSession.expires_at > now,
            )
        ).scalar_one_or_none()

        if session_record is None:
            return None

        user = db.get(User, session_record.user_id)
        if user is None or not user.is_active:
            return None

        return user

    def terminate_session(self, db: DBSession, raw_session_token: str) -> bool:
        """Delete session record from database upon logout."""
        session_hash = self.hash_token(raw_session_token)
        session_record = db.execute(
            select(UserSession).where(UserSession.session_token_hash == session_hash)
        ).scalar_one_or_none()

        if session_record is not None:
            db.delete(session_record)
            db.commit()
            return True
        return False

    def revoke_all_user_sessions(self, db: DBSession, user_id: int) -> int:
        """Revoke all active sessions for a user (e.g. on password reset or account suspension)."""
        stmt = select(UserSession).where(UserSession.user_id == user_id)
        sessions = db.execute(stmt).scalars().all()
        count = len(sessions)
        for s in sessions:
            db.delete(s)
        db.commit()
        return count


# Module-level default policy instance
session_security_policy = SessionSecurityPolicy()
