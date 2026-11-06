"""FastAPI dependency injection utilities for authentication, db sessions, and feature flags."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.auth.session import session_security_policy
from stock_analysis.config import Settings, get_settings
from stock_analysis.db.models import Session as UserSession
from stock_analysis.db.models import User
from stock_analysis.db.session import get_db
from stock_analysis.security.environments import EnvironmentGuard, EnvironmentIsolationError

bearer_scheme = HTTPBearer(auto_error=False)


class AuthenticatedUser:
    """Lightweight representation of an authenticated user session."""

    def __init__(
        self,
        user_id: int,
        email: str,
        role: str = "user",
        session_id: int | None = None,
        mfa_enabled: bool = False,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.session_id = session_id
        self.mfa_enabled = mfa_enabled


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: DBSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """Validate Bearer token or HttpOnly session cookie, and return active authenticated user.

    For cookie-authenticated requests, Double-Submit CSRF protection is strictly enforced.
    Raises 401 if missing, invalid, or expired.
    """
    token: str | None = None
    is_cookie_auth = False

    if credentials is not None and credentials.credentials:
        token = credentials.credentials.strip()
    elif session_security_policy.SESSION_COOKIE_NAME in request.cookies:
        token = request.cookies[session_security_policy.SESSION_COOKIE_NAME].strip()
        is_cookie_auth = True

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Geçerli bir oturum token'ı veya çerezi gereklidir.",
            },
        )

    # For cookie-based state-changing requests, validate CSRF token
    if is_cookie_auth:
        session_security_policy.validate_csrf(request)

    # Environment Isolation: Reject mock/test tokens in production environments
    try:
        EnvironmentGuard.validate_token_for_environment(token, settings.app_env)
    except EnvironmentIsolationError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "TEST_TOKEN_FORBIDDEN_IN_PRODUCTION",
                "message": str(err),
            },
        ) from err

    # Fast path: development / mock testing token (only permitted in dev/test)
    if token.startswith("test_token_"):
        try:
            user_id = int(token.split("_")[-1])
        except ValueError:
            user_id = 1
        return AuthenticatedUser(
            user_id=user_id,
            email=f"user_{user_id}@example.com",
            session_id=None,
            mfa_enabled=False,
        )

    # Hash token to lookup in database sessions
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)

    session_record = db.execute(
        select(UserSession).where(
            UserSession.session_token_hash == token_hash,
            UserSession.expires_at > now,
        )
    ).scalar_one_or_none()

    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Oturum suresi dolmus veya gecersiz token.",
            },
        )

    user = db.get(User, session_record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "USER_INACTIVE",
                "message": "Kullanici hesabi aktif degil.",
            },
        )

    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        role=user.role,
        session_id=session_record.id,
        mfa_enabled=user.mfa_enabled,
    )


def require_roles(*allowed_roles: str) -> Callable[..., AuthenticatedUser]:
    """Factory dependency enforcing Principle of Least Privilege for specific user roles.

    Raises 403 Forbidden if user's role is not within allowed_roles or violates
    environment boundaries.
    """

    def _role_checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> AuthenticatedUser:
        try:
            EnvironmentGuard.validate_role_for_environment(current_user.role, settings.app_env)
        except EnvironmentIsolationError as err:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN_INVALID_ENVIRONMENT_ROLE",
                    "message": str(err),
                },
            ) from err

        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN_INSUFFICIENT_PERMISSIONS",
                    "message": "Bu islemi gerceklestirmek icin yetkiniz bulunmamaktadir.",
                },
            )
        return current_user

    return _role_checker


def require_admin(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """Enforce Least Privilege and Admin MFA in production.

    1. Enforces that caller possesses 'admin' or 'super_admin' role (Least Privilege).
    2. In production (app_env == 'production'), strictly mandates that
       the administrator account has MFA (Multi-Factor Authentication) enabled.
    """
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN_ADMIN_REQUIRED",
                "message": "Bu islem icin yonetici (admin) yetkisi gereklidir.",
            },
        )

    # In production environments, administrators MUST have MFA enabled
    if settings.app_env.lower() == "production":
        user = db.get(User, current_user.user_id)
        is_mfa_active = user.mfa_enabled if user else current_user.mfa_enabled
        if not is_mfa_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ADMIN_MFA_REQUIRED",
                    "message": (
                        "Uretim ortaminda yonetici hesaplari icin "
                        "MFA (Iki Adimli Dogrulama) zorunludur."
                    ),
                },
            )

    return current_user


def require_permission(permission: str) -> Callable[..., AuthenticatedUser]:
    """Dependency enforcing Principle of Least Privilege for specific granular permissions.

    Validates that:
    1. Caller's role is recognized and permitted in active environment.
    2. Caller possesses the designated permission in ROLE_SCOPES.
    3. In production, privileged accounts mandate MFA if specified by RoleScope.
    """

    def _permission_checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
        db: DBSession = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> AuthenticatedUser:
        try:
            EnvironmentGuard.validate_role_for_environment(current_user.role, settings.app_env)
        except EnvironmentIsolationError as err:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN_INVALID_ENVIRONMENT_ROLE",
                    "message": str(err),
                },
            ) from err

        if not EnvironmentGuard.has_permission(current_user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN_INSUFFICIENT_PERMISSIONS",
                    "message": f"Bu işlem için '{permission}' yetkisi gereklidir.",
                },
            )

        if settings.app_env.lower() == "production" and EnvironmentGuard.requires_mfa_in_production(
            current_user.role
        ):
            user = db.get(User, current_user.user_id)
            is_mfa_active = user.mfa_enabled if user else current_user.mfa_enabled
            if not is_mfa_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "ADMIN_MFA_REQUIRED",
                        "message": (
                            "Üretim ortamında ayrıcalıklı hesaplar için "
                            "MFA (İki Adımlı Doğrulama) zorunludur."
                        ),
                    },
                )

        return current_user

    return _permission_checker


def require_forecast_enabled(settings: Settings = Depends(get_settings)) -> None:
    """Dependency verifying that the statistical ML forecast feature flag is enabled.

    Raises 503 Service Unavailable if flag is disabled.
    """
    if not settings.forecast_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "FORECAST_DISABLED",
                "message": "Model tahmin servisi su anda aktif degildir.",
            },
        )


def get_pagination(
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, int]:
    """Validate and normalize pagination parameters (page >= 1, 1 <= page_size <= 100)."""
    valid_page = max(1, page)
    valid_size = min(max(1, page_size), 100)
    return valid_page, valid_size
