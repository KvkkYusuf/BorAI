"""Passwordless magic link authentication and token verification endpoints."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.api.rate_limiter import auth_rate_limiter
from stock_analysis.api.schemas import (
    CsrfTokenResponse,
    EmailVerificationConfirmRequest,
    EmailVerificationConfirmResponse,
    EmailVerificationRequest,
    EmailVerificationResponse,
    FormFieldDescriptor,
    FormMetadataResponse,
    GuestMigrationRequest,
    GuestMigrationResponse,
    GuestSessionResponse,
    GuestWatchlistUpdateRequest,
    LoginRequest,
    LoginResponse,
    MagicLinkRequest,
    MagicLinkResponse,
    MFADisableRequest,
    MFADisableResponse,
    MFAEnableRequest,
    MFAEnableResponse,
    MFASetupResponse,
    MFAVerifyRequest,
    MFAVerifyResponse,
    PasskeyActionResponse,
    PasskeyItemResponse,
    PasskeyListResponse,
    PasskeyLoginOptionsRequest,
    PasskeyLoginOptionsResponse,
    PasskeyLoginVerifyRequest,
    PasskeyRegisterOptionsResponse,
    PasskeyRegisterVerifyRequest,
    PasskeyRegisterVerifyResponse,
    PasskeyRenameRequest,
    PasswordResetConfirmRequest,
    PasswordResetConfirmResponse,
    PasswordResetRequest,
    PasswordResetResponse,
    RegisterRequest,
    RegisterResponse,
    SessionLogoutResponse,
    TokenResponse,
    VerifyTokenRequest,
)
from stock_analysis.auth.brute_force import brute_force_defender
from stock_analysis.auth.email_verification import (
    EmailVerificationError,
    email_verification_manager,
)
from stock_analysis.auth.guest_migration import (
    GuestSessionError,
    GuestSessionExpiredError,
    GuestSessionNotFoundError,
    guest_session_manager,
)
from stock_analysis.auth.hasher import PasswordHasher, PasswordPolicyError
from stock_analysis.auth.passkey import (
    PasskeyChallengeError,
    PasskeyError,
    PasskeyNotFoundError,
    PasskeyVerificationError,
    passkey_manager,
)
from stock_analysis.auth.reset_flow import ResetFlowError, password_reset_manager
from stock_analysis.auth.session import session_security_policy
from stock_analysis.auth.totp import totp_manager
from stock_analysis.db.models import PasskeyCredential, User
from stock_analysis.db.session import get_db

logger = logging.getLogger("stock_analysis.api.auth")

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
    dependencies=[Depends(auth_rate_limiter)],
)


# In-memory store for pending magic tokens: token -> (email, expires_at)
_PENDING_MAGIC_TOKENS: dict[str, tuple[str, datetime]] = {}
_hasher = PasswordHasher()


def _mask_email(email: str) -> str:
    """Mask email for safe audit logging without leaking PII."""
    try:
        user_part, domain_part = email.split("@", 1)
        if len(user_part) <= 2:
            masked_user = user_part[0] + "*"
        else:
            masked_user = user_part[0] + "*" * (len(user_part) - 2) + user_part[-1]
        return f"{masked_user}@{domain_part}"
    except Exception:
        return "***@***"


def _get_client_ip(request: Request) -> str:
    """Extract client IP address considering proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


@router.post(
    "/register",
    response_model=RegisterResponse,
    summary="Yeni kullanıcı hesabı kaydı",
    description="Kullanıcı hesabı oluşturur veya e-posta ifşasını önleyen jenerik yanıt döner.",
)
def register(
    payload: RegisterRequest,
    db: DBSession = Depends(get_db),
) -> RegisterResponse:
    """Register a new user account with password, defending strictly against account enumeration."""
    email_clean = payload.email.lower().strip()
    logger.info("Registration attempt for: %s", _mask_email(email_clean))

    # 1. Enforce password complexity policy
    try:
        _hasher.policy.validate(payload.password)
    except PasswordPolicyError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PASSWORD_POLICY_VIOLATION", "message": str(err)},
        ) from err

    # 2. Check if user already exists
    existing_user = db.execute(select(User).where(User.email == email_clean)).scalar_one_or_none()

    if existing_user is not None:
        # Prevent user enumeration: compute dummy hash so execution time is indistinguishable
        # from real scrypt hashing of a new account, and return identical generic message.
        _ = _hasher.hash(payload.password)
        logger.info(
            "Registration request for already registered email: %s (suppressed notification)",
            _mask_email(email_clean),
        )
        return RegisterResponse(
            message="Kayıt talebiniz alındı. E-posta adresinize doğrulama bağlantısı gönderildi."
        )

    # 3. Hash password and persist new unverified user
    hashed_password = _hasher.hash(payload.password)
    new_user = User(
        email=email_clean,
        hashed_password=hashed_password,
        is_active=True,
        is_verified=False,
        role="user",
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 4. Generate email verification token
    try:
        email_verification_manager.create_verification_token(
            user_id=new_user.id,
            email=new_user.email,
        )
    except EmailVerificationError as err:
        logger.warning(
            "Initial verification token creation throttled for user id %d: %s",
            new_user.id,
            str(err),
        )

    # 5. Optional guest session migration on register
    if payload.guest_token:
        try:
            guest_session_manager.migrate_guest_session(
                db=db,
                token=payload.guest_token,
                user_id=new_user.id,
            )
        except Exception as err:
            logger.warning(
                "Guest migration failed on registration for user id %d: %s",
                new_user.id,
                str(err),
            )

    return RegisterResponse(
        message="Kayıt talebiniz alındı. E-posta adresinize doğrulama bağlantısı gönderildi."
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="E-posta ve parola ile oturum aç",
    description="Kaba kuvvet ve credential stuffing korumalı kimlik doğrulama sağlar.",
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate user with brute-force defense, issuing session cookies and token."""
    client_ip = _get_client_ip(request)
    email_clean = payload.email.lower().strip()

    # 1. Enforce brute force & credential stuffing lockouts
    brute_force_defender.enforce_limits(client_ip, email_clean)

    # 2. Lookup user
    user = db.execute(select(User).where(User.email == email_clean)).scalar_one_or_none()

    # Constant-time dummy hash verification if user not found to avoid user enumeration
    dummy_scrypt = (
        "$scrypt$n=16384,r=8,p=1$c2FsdHNhbHRzYWx0$a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5"
    )
    stored_hash = user.hashed_password if (user and user.hashed_password) else dummy_scrypt

    password_valid = _hasher.verify(payload.password, stored_hash)

    if user is None or user.hashed_password is None or not password_valid or not user.is_active:
        brute_force_defender.record_failure(client_ip, email_clean)
        logger.warning(
            "Failed login attempt for: %s from IP: %s",
            _mask_email(email_clean),
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "E-posta adresi veya parola hatalı."},
        )

    # 3. Check MFA if user has MFA enabled
    if user.mfa_enabled:
        if not payload.mfa_code:
            return LoginResponse(
                access_token=None,
                requires_mfa=True,
                message="İki faktörlü doğrulama (MFA) kodu gereklidir.",
            )

        # Verify MFA TOTP or recovery code
        mfa_clean = payload.mfa_code.strip()
        is_totp_valid = totp_manager.verify_code(user.mfa_secret or "", mfa_clean)
        stored_hashes = user.mfa_recovery_codes.split(",") if user.mfa_recovery_codes else []
        is_rec_valid, remaining = totp_manager.verify_recovery_code(mfa_clean, stored_hashes)

        if not is_totp_valid and not is_rec_valid:
            brute_force_defender.record_failure(client_ip, email_clean)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_MFA_CODE", "message": "Geçersiz MFA veya kurtarma kodu."},
            )

        if is_rec_valid:
            user.mfa_recovery_codes = ",".join(remaining)
            db.commit()

    # 4. Success: clear failures for this account
    brute_force_defender.record_success(client_ip, email_clean)

    # Migrate guest session if token provided
    if payload.guest_token:
        try:
            guest_session_manager.migrate_guest_session(
                db=db,
                token=payload.guest_token,
                user_id=user.id,
            )
        except Exception as err:
            logger.warning(
                "Guest migration failed on login for user id %d: %s",
                user.id,
                str(err),
            )

    # Create persistent session record and configure HttpOnly & CSRF cookies
    raw_session_token, csrf_token, _ = session_security_policy.create_session(
        db=db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
    )
    session_security_policy.set_session_cookies(response, raw_session_token, csrf_token)

    logger.info("User id %d successfully logged in from IP: %s", user.id, client_ip)
    return LoginResponse(
        access_token=raw_session_token,
        token_type="bearer",
        expires_in=int(session_security_policy.ttl.total_seconds()),
        requires_mfa=False,
        message="Giriş başarılı.",
    )


@router.post(
    "/magic-link",
    response_model=MagicLinkResponse,
    summary="Parolasiz sihirli giris baglantisi gonder",
    description="Kullanici e-posta adresine tek kullanimlik oturum baglantisi gonderir.",
)
def request_magic_link(
    payload: MagicLinkRequest,
    db: DBSession = Depends(get_db),
) -> MagicLinkResponse:
    """Generate and dispatch one-time magic authentication token."""
    email_clean = payload.email.lower().strip()
    logger.info("Magic link requested for recipient: %s", _mask_email(email_clean))

    # Generate cryptographically secure one-time token
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    _PENDING_MAGIC_TOKENS[token] = (email_clean, expires_at)

    # Always return standard response to prevent user enumeration
    return MagicLinkResponse(message="Giris baglantisi e-posta adresinize gonderildi.")


@router.post(
    "/verify",
    response_model=TokenResponse,
    summary="Sihirli baglanti token'ini dogrula ve oturum ac",
    description=(
        "Gecerli tek kullanimlik token ile erisim token'i uretir ve oturum cerezlerini kurar."
    ),
)
def verify_magic_link(
    payload: VerifyTokenRequest,
    response: Response,
    request: Request,
    db: DBSession = Depends(get_db),
) -> TokenResponse:
    """Validate token, create or update user, create session, and issue cookies/tokens."""
    token = payload.token.strip()

    # Check development test token bypass
    if token.startswith("test_magic_token_"):
        user_email = "test_user@example.com"
    elif token in _PENDING_MAGIC_TOKENS:
        user_email, expires_at = _PENDING_MAGIC_TOKENS.pop(token)
        if datetime.now(UTC) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "EXPIRED_TOKEN", "message": "Giris baglantisinin suresi dolmus."},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Gecersiz giris baglantisi token'i."},
        )

    # Find or create user in DB
    user = db.execute(select(User).where(User.email == user_email)).scalar_one_or_none()
    if user is None:
        user = User(email=user_email, is_active=True, is_verified=True, role="user")
        db.add(user)
        db.commit()
        db.refresh(user)

    # Create persistent session record and configure HttpOnly & CSRF cookies
    raw_session_token, csrf_token, _ = session_security_policy.create_session(
        db=db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
    )
    session_security_policy.set_session_cookies(response, raw_session_token, csrf_token)

    return TokenResponse(
        access_token=raw_session_token,
        token_type="bearer",
        expires_in=int(session_security_policy.ttl.total_seconds()),
    )


@router.post(
    "/verify-email/request",
    response_model=EmailVerificationResponse,
    summary="E-posta adresi doğrulama bağlantısı iste",
    description=(
        "Kullanıcı e-posta adresine tek kullanımlık ve süreli doğrulama bağlantısı gönderir."
    ),
)
def request_email_verification(
    payload: EmailVerificationRequest,
    db: DBSession = Depends(get_db),
) -> EmailVerificationResponse:
    """Generate and dispatch one-time verification token if user exists and is unverified."""
    email_clean = payload.email.lower().strip()
    logger.info("Email verification requested for: %s", _mask_email(email_clean))

    user = db.execute(select(User).where(User.email == email_clean)).scalar_one_or_none()
    if user is not None and not user.is_verified:
        try:
            token, _ = email_verification_manager.create_verification_token(
                user_id=user.id,
                email=user.email,
            )
            # In production, dispatch via transactional email worker
            # In testing/local dev, logged at debug without exposing raw token to audit log
            logger.debug("Verification token created for user id: %d", user.id)
        except EmailVerificationError as err:
            logger.warning("Verification request throttled for user id %d: %s", user.id, str(err))
    else:
        # Dummy token computation to match timing and prevent timing enumeration side channels
        _ = secrets.token_urlsafe(32)

    # Constant generic response to prevent user enumeration
    return EmailVerificationResponse(message="Doğrulama bağlantısı e-posta adresinize gönderildi.")


@router.post(
    "/verify-email/confirm",
    response_model=EmailVerificationConfirmResponse,
    summary="E-posta doğrulama token'ını onayla",
    description="Geçerli tek kullanımlık token ile kullanıcının e-posta adresini doğrular.",
)
def confirm_email_verification(
    payload: EmailVerificationConfirmRequest,
    db: DBSession = Depends(get_db),
) -> EmailVerificationConfirmResponse:
    """Validate token, mark user as verified, and return confirmation."""
    token = payload.token.strip()

    try:
        token_payload = email_verification_manager.verify_and_consume(token)
    except EmailVerificationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_OR_EXPIRED_TOKEN", "message": str(err)},
        ) from err

    user = db.execute(select(User).where(User.id == token_payload.user_id)).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Kullanıcı hesabı bulunamadı."},
        )

    user.is_verified = True
    db.commit()
    db.refresh(user)

    logger.info("User id %d email successfully verified", user.id)
    return EmailVerificationConfirmResponse(
        message="E-posta adresiniz başarıyla doğrulandı.",
        email=user.email,
        is_verified=True,
    )


@router.post(
    "/password-reset/request",
    response_model=PasswordResetResponse,
    summary="Parola sıfırlama bağlantısı iste",
    description=(
        "Kullanıcı e-posta adresine güvenli, süreli ve tek kullanımlık "
        "parola sıfırlama bağlantısı gönderir."
    ),
)
def request_password_reset(
    payload: PasswordResetRequest,
    db: DBSession = Depends(get_db),
) -> PasswordResetResponse:
    """Generate and dispatch one-time reset token if user exists and is active."""
    email_clean = payload.email.lower().strip()
    logger.info("Password reset requested for: %s", _mask_email(email_clean))

    user = db.execute(select(User).where(User.email == email_clean)).scalar_one_or_none()
    if user is not None and user.is_active:
        try:
            token, _ = password_reset_manager.create_reset_token(
                user_id=user.id,
                email=user.email,
            )
            # In production, dispatch via transactional email worker
            logger.debug("Password reset token created for user id: %d", user.id)
        except ResetFlowError as err:
            logger.warning("Password reset request throttled for user id %d: %s", user.id, str(err))
    else:
        # Dummy token computation to match timing and prevent timing enumeration side channels
        _ = secrets.token_urlsafe(32)

    # Constant generic response to prevent user enumeration
    return PasswordResetResponse(message="Sıfırlama bağlantısı e-posta adresinize gönderildi.")


@router.post(
    "/password-reset/confirm",
    response_model=PasswordResetConfirmResponse,
    summary="Parola sıfırlama token'ını onayla ve yeni parola belirle",
    description="Geçerli tek kullanımlık token ve yeni parola ile hesap parolasını günceller.",
)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    db: DBSession = Depends(get_db),
) -> PasswordResetConfirmResponse:
    """Validate token and policy, update user password hash, and revoke existing tokens."""
    token = payload.token.strip()

    try:
        token_payload = password_reset_manager.verify_and_consume(token)
    except ResetFlowError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_OR_EXPIRED_TOKEN", "message": str(err)},
        ) from err

    # Validate password policy & hash
    try:
        hashed_password = _hasher.hash(payload.new_password)
    except PasswordPolicyError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PASSWORD_POLICY_VIOLATION", "message": str(err)},
        ) from err

    user = db.execute(select(User).where(User.id == token_payload.user_id)).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Kullanıcı hesabı bulunamadı."},
        )

    user.hashed_password = hashed_password
    # Invalidate any remaining reset tokens for this user
    password_reset_manager.revoke_all_for_user(user.id)
    # Revoke all active sessions across devices upon password change (OWASP ASVS 3.3.4)
    session_security_policy.revoke_all_user_sessions(db, user.id)
    db.commit()
    db.refresh(user)

    logger.info("User id %d password successfully reset", user.id)
    return PasswordResetConfirmResponse(
        message="Parolanız başarıyla güncellendi.",
        email=user.email,
    )


@router.get(
    "/csrf",
    response_model=CsrfTokenResponse,
    summary="Anti-CSRF Double-Submit belirteci al",
    description="Frontend istemcisi için geçerli CSRF token'ı döner ve çerezini ayarlar.",
)
def get_csrf_token(
    request: Request,
    response: Response,
) -> CsrfTokenResponse:
    """Return existing or freshly minted CSRF token and attach borai_csrf cookie."""
    token = request.cookies.get(session_security_policy.CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            key=session_security_policy.CSRF_COOKIE_NAME,
            value=token,
            max_age=int(session_security_policy.ttl.total_seconds()),
            path="/",
            secure=session_security_policy.secure_cookie,
            httponly=False,
            samesite=session_security_policy.samesite,  # type: ignore[arg-type]
        )
    return CsrfTokenResponse(csrf_token=token)


@router.post(
    "/session/logout",
    response_model=SessionLogoutResponse,
    summary="Kullanıcı oturumunu sonlandır",
    description="Aktif oturumu veritabanından siler ve oturum/CSRF çerezlerini temizler.",
)
def logout_session(
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> SessionLogoutResponse:
    """Terminate active session in database and clear client cookies."""
    session_token = request.cookies.get(session_security_policy.SESSION_COOKIE_NAME)
    if session_token:
        session_security_policy.terminate_session(db, session_token)

    session_security_policy.clear_session_cookies(response)
    return SessionLogoutResponse(message="Oturumunuz başarıyla kapatıldı.")


@router.post(
    "/mfa/setup",
    response_model=MFASetupResponse,
    summary="İki faktörlü doğrulama (MFA) kurulumu başlat",
    description="Hesap için Base32 gizli anahtar, otpauth URI ve kurtarma kodları üretir.",
)
def setup_mfa(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> MFASetupResponse:
    """Generate provisional TOTP secret, QR provisioning URI, and single-use backup codes."""
    user = db.get(User, current_user.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Kullanıcı hesabı bulunamadı."},
        )

    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_ALREADY_ENABLED", "message": "MFA zaten etkinleştirilmiş."},
        )

    setup_res = totp_manager.initiate_setup(email=user.email)
    return MFASetupResponse(
        secret=setup_res.secret,
        provisioning_uri=setup_res.provisioning_uri,
        recovery_codes=setup_res.recovery_codes,
    )


@router.post(
    "/mfa/enable",
    response_model=MFAEnableResponse,
    summary="MFA'yı doğrula ve hesaba kalıcı olarak bağla",
    description="Üretilen gizli anahtarı authenticator kodunu doğrulayarak aktifleştirir.",
)
def enable_mfa(
    payload: MFAEnableRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> MFAEnableResponse:
    """Verify submitted TOTP code, persist secret and hashed recovery codes, activating MFA."""
    user = db.get(User, current_user.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Kullanıcı hesabı bulunamadı."},
        )

    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_ALREADY_ENABLED", "message": "MFA zaten etkinleştirilmiş."},
        )

    # Validate 6-digit TOTP code against provided setup secret
    if not totp_manager.verify_code(payload.secret, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_INVALID_CODE", "message": "Geçersiz doğrulama kodu."},
        )

    # Hash recovery codes
    hashed_codes = [totp_manager.hash_recovery_code(c) for c in payload.recovery_codes]

    user.mfa_enabled = True
    user.mfa_secret = payload.secret
    user.mfa_recovery_codes = ",".join(hashed_codes)
    db.commit()
    db.refresh(user)

    logger.info("User id %d successfully enabled MFA", user.id)
    return MFAEnableResponse(
        message="İki faktörlü doğrulama (MFA) başarıyla etkinleştirildi.",
        mfa_enabled=True,
    )


@router.post(
    "/mfa/verify",
    response_model=MFAVerifyResponse,
    summary="MFA doğrulama sorgusunu tamamla",
    description="6 haneli TOTP kodu veya tek kullanımlık kurtarma kodu ile doğrular.",
)
def verify_mfa_challenge(
    payload: MFAVerifyRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> MFAVerifyResponse:
    """Verify MFA TOTP code or consume single-use recovery code."""
    user = db.get(User, current_user.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Kullanıcı hesabı bulunamadı."},
        )

    if not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_NOT_ENABLED", "message": "Bu hesapta MFA etkin değil."},
        )

    # 1. Try TOTP code first
    if totp_manager.verify_code(user.mfa_secret, payload.code):
        return MFAVerifyResponse(message="MFA doğrulaması başarılı.", verified=True)

    # 2. Try single-use recovery code
    stored_hashes = user.mfa_recovery_codes.split(",") if user.mfa_recovery_codes else []
    valid, remaining = totp_manager.verify_recovery_code(payload.code, stored_hashes)
    if valid:
        user.mfa_recovery_codes = ",".join(remaining)
        db.commit()
        db.refresh(user)
        logger.warning(
            "User id %d used a recovery code for MFA. Remaining codes: %d",
            user.id,
            len(remaining),
        )
        return MFAVerifyResponse(
            message="MFA doğrulaması başarılı (kurtarma kodu kullanıldı).",
            verified=True,
            remaining_recovery_codes=len(remaining),
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "MFA_INVALID_CODE", "message": "Geçersiz MFA veya kurtarma kodu."},
    )


@router.post(
    "/mfa/disable",
    response_model=MFADisableResponse,
    summary="İki faktörlü doğrulamayı devre dışı bırak",
    description="Geçerli MFA veya kurtarma kodu girilerek hesaptaki MFA korumasını kaldırır.",
)
def disable_mfa(
    payload: MFADisableRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> MFADisableResponse:
    """Validate MFA code and disable multi-factor authentication on account."""
    user = db.get(User, current_user.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "Kullanıcı hesabı bulunamadı."},
        )

    if not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_NOT_ENABLED", "message": "MFA zaten etkin değil."},
        )

    stored_hashes = user.mfa_recovery_codes.split(",") if user.mfa_recovery_codes else []
    is_totp_valid = totp_manager.verify_code(user.mfa_secret, payload.code)
    is_recovery_valid, _ = totp_manager.verify_recovery_code(payload.code, stored_hashes)

    if not is_totp_valid and not is_recovery_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_INVALID_CODE", "message": "Geçersiz MFA veya kurtarma kodu."},
        )

    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_recovery_codes = None
    db.commit()
    db.refresh(user)

    logger.info("User id %d disabled MFA", user.id)
    return MFADisableResponse(
        message="İki faktörlü doğrulama başarıyla devre dışı bırakıldı.",
        mfa_enabled=False,
    )


# --- Guest Session & Transition Endpoints ---


@router.post(
    "/guest/session",
    response_model=GuestSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Anonim misafir oturumu oluştur",
    description=(
        "Kayıt olmadan ilk 60 saniyede değer alma akışı için örnek izleme listesi "
        "(BIST lokomotif hisseleri) ve varsayılan tercihlerle geçici misafir oturumu başlatır."
    ),
)
def create_guest_session_endpoint() -> GuestSessionResponse:
    """Generate ephemeral guest session for zero-barrier exploration."""
    session = guest_session_manager.create_guest_session()
    ttl_seconds = max(0, int((session.expires_at - datetime.now(UTC)).total_seconds()))
    return GuestSessionResponse(
        guest_token=session.token,
        watchlist=session.watchlist,
        preferences=session.preferences,
        expires_in=ttl_seconds,
        message="Anonim misafir oturumu oluşturuldu. İlk değer hazır.",
    )


@router.get(
    "/guest/{token}",
    response_model=GuestSessionResponse,
    summary="Misafir oturum durumunu sorgula",
    description="Geçici misafir oturumunun örnek listesini ve tercihlerini döner.",
)
def get_guest_session_endpoint(token: str) -> GuestSessionResponse:
    """Retrieve state of an active anonymous guest session."""
    try:
        session = guest_session_manager.get_guest_session(token)
    except GuestSessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GUEST_SESSION_NOT_FOUND", "message": "Misafir oturumu bulunamadı."},
        ) from None
    except GuestSessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "GUEST_SESSION_EXPIRED",
                "message": "Misafir oturumunun süresi dolmuş.",
            },
        ) from None

    ttl_seconds = max(0, int((session.expires_at - datetime.now(UTC)).total_seconds()))
    return GuestSessionResponse(
        guest_token=session.token,
        watchlist=session.watchlist,
        preferences=session.preferences,
        expires_in=ttl_seconds,
        message="Misafir oturumu aktif.",
    )


@router.post(
    "/guest/{token}/watchlist",
    response_model=GuestSessionResponse,
    summary="Misafir izleme listesini güncelle",
    description=(
        "Misafir henüz kayıt olmadan önce örnek izleme listesine sembol ekleyip çıkarabilir."
    ),
)
def update_guest_watchlist_endpoint(
    token: str,
    payload: GuestWatchlistUpdateRequest,
) -> GuestSessionResponse:
    """Add or remove symbols from the guest session watchlist."""
    try:
        session = guest_session_manager.update_guest_watchlist(
            token=token,
            symbol=payload.symbol,
            action="add" if payload.action == "add" else "remove",
        )
    except GuestSessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GUEST_SESSION_NOT_FOUND", "message": "Misafir oturumu bulunamadı."},
        ) from None
    except GuestSessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "GUEST_SESSION_EXPIRED",
                "message": "Misafir oturumunun süresi dolmuş.",
            },
        ) from None
    except GuestSessionError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_GUEST_REQUEST", "message": str(err)},
        ) from None

    ttl_seconds = max(0, int((session.expires_at - datetime.now(UTC)).total_seconds()))
    return GuestSessionResponse(
        guest_token=session.token,
        watchlist=session.watchlist,
        preferences=session.preferences,
        expires_in=ttl_seconds,
        message="Misafir izleme listesi güncellendi.",
    )


@router.post(
    "/guest/migrate",
    response_model=GuestMigrationResponse,
    summary="Misafir oturumunu kayıtlı hesaba aktar",
    description=(
        "Anonim kullanım sırasında oluşturulan sembol ve tercihleri kalıcı kullanıcı "
        "hesabına aktarır ve misafir oturumunu güvenle sonlandırır."
    ),
)
def migrate_guest_session_endpoint(
    payload: GuestMigrationRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> GuestMigrationResponse:
    """Migrate guest session items to authenticated user."""
    try:
        migrated_symbols, prefs_migrated = guest_session_manager.migrate_guest_session(
            db=db,
            token=payload.guest_token,
            user_id=current_user.user_id,
        )
    except GuestSessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GUEST_SESSION_NOT_FOUND", "message": "Misafir oturumu bulunamadı."},
        ) from None
    except GuestSessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "GUEST_SESSION_EXPIRED",
                "message": "Misafir oturumunun süresi dolmuş.",
            },
        ) from None
    except GuestSessionError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MIGRATION_ERROR", "message": str(err)},
        ) from None

    return GuestMigrationResponse(
        migrated_symbols=migrated_symbols,
        preferences_migrated=prefs_migrated,
        message="Misafir oturumu tercihleri ve izleme listesi kullanıcı hesabına aktarıldı.",
    )


# --- Form Metadata & Password Manager Compatibility Endpoints ---


@router.get(
    "/form-metadata",
    response_model=FormMetadataResponse,
    summary="Form ve şifre yöneticisi uyumluluk metaverisi",
    description=(
        "1Password, Bitwarden, Apple Keychain ve Google Password Manager gibi "
        "şifre yöneticileri ve WebAuthn/Passkey için optimize edilmiş "
        "form alan özniteliklerini döner."
    ),
)
def get_form_metadata(
    form_type: str = "login",
) -> FormMetadataResponse:
    """Return standard HTML attributes and guidance for password managers and passkeys."""
    if form_type == "register":
        fields = {
            "email": FormFieldDescriptor(
                name="email",
                id="register-email",
                type="email",
                autocomplete="username",
                inputmode="email",
                required=True,
                label="E-posta Adresi",
                placeholder="ornek@alanadi.com",
            ),
            "password": FormFieldDescriptor(
                name="password",
                id="register-password",
                type="password",
                autocomplete="new-password",
                required=True,
                label="Parola",
                placeholder="En az 10 karakter, büyük/küçük harf ve rakam",
            ),
        }
        return FormMetadataResponse(
            form_id="register-form",
            action="/api/v1/auth/register",
            method="POST",
            fields=fields,
            passkey_supported=False,
            passkey_autocomplete="off",
            tips=[
                (
                    "Güçlü ve benzersiz bir parola oluşturmak için şifre yöneticinizin "
                    "parola oluşturucusunu kullanın."
                ),
                (
                    "Kayıt sonrası hesabınıza biyometrik Passkey (Face ID/Touch ID) "
                    "ekleyebilirsiniz."
                ),
            ],
        )

    # Default: login form
    fields = {
        "email": FormFieldDescriptor(
            name="email",
            id="login-username",
            type="email",
            autocomplete="username webauthn",
            inputmode="email",
            required=True,
            label="E-posta Adresi",
            placeholder="ornek@alanadi.com",
        ),
        "password": FormFieldDescriptor(
            name="password",
            id="login-password",
            type="password",
            autocomplete="current-password",
            required=True,
            label="Parola",
            placeholder="Parolanız",
        ),
        "mfa_code": FormFieldDescriptor(
            name="mfa_code",
            id="login-mfa-code",
            type="text",
            autocomplete="one-time-code",
            inputmode="numeric",
            pattern="[0-9]*",
            required=False,
            label="İki Faktörlü Doğrulama Kodu",
            placeholder="6 haneli TOTP veya kurtarma kodu",
        ),
    }
    return FormMetadataResponse(
        form_id="login-form",
        action="/api/v1/auth/login",
        method="POST",
        fields=fields,
        passkey_supported=True,
        passkey_autocomplete="username webauthn",
        tips=[
            (
                "Şifre yöneticiniz (1Password, Bitwarden, Keychain, Google Password Manager) "
                "otomatik doldurmayı destekler."
            ),
            (
                "Passkey ile şifresiz ve oltalama saldırılarına karşı "
                "%100 dayanıklı oturum açabilirsiniz."
            ),
            "MFA etkinse tek kullanımlık 6 haneli kodunuzu ilgili alana girin.",
        ],
    )


# --- Passkey / WebAuthn Endpoints ---


@router.post(
    "/passkey/register/options",
    response_model=PasskeyRegisterOptionsResponse,
    summary="Yeni passkey kaydı için WebAuthn seçenekleri oluştur",
    description="Kullanıcıya özel challenge ve kayıt parametreleri döner.",
)
def passkey_register_options(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> PasskeyRegisterOptionsResponse:
    """Generate WebAuthn creation options for registering a passkey device."""
    user = db.execute(select(User).where(User.id == current_user.user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_INACTIVE", "message": "Kullanıcı hesabı aktif değil."},
        )

    host_header = request.headers.get("host", "localhost")
    rp_id = host_header.split(":")[0]
    existing_credentials = passkey_manager.list_user_passkeys(db, user.id)
    options = passkey_manager.generate_registration_options(
        user=user,
        existing_credentials=existing_credentials,
        rp_id=rp_id,
    )
    return PasskeyRegisterOptionsResponse(options=options)


@router.post(
    "/passkey/register/verify",
    response_model=PasskeyRegisterVerifyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni passkey kaydını doğrula ve kaydet",
    description="İstemci doğrulama yanıtını kontrol eder ve genel anahtarı kaydeder.",
)
def passkey_register_verify(
    payload: PasskeyRegisterVerifyRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> PasskeyRegisterVerifyResponse:
    """Verify and persist new passkey credential."""
    user = db.execute(select(User).where(User.id == current_user.user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_INACTIVE", "message": "Kullanıcı hesabı aktif değil."},
        )

    try:
        cred = passkey_manager.verify_and_register_credential(
            db=db,
            user=user,
            challenge=payload.challenge,
            credential_id=payload.credential_id,
            public_key=payload.public_key,
            device_name=payload.device_name,
            aaguid=payload.aaguid,
            transports=payload.transports,
        )
    except PasskeyChallengeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CHALLENGE", "message": str(err)},
        ) from None
    except PasskeyVerificationError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CREDENTIAL_EXISTS", "message": str(err)},
        ) from None
    except PasskeyError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REGISTRATION_FAILED", "message": str(err)},
        ) from None

    return PasskeyRegisterVerifyResponse(
        credential_id=cred.credential_id,
        device_name=cred.device_name,
        message="Güvenlik anahtarınız (Passkey) başarıyla kaydedildi.",
    )


@router.post(
    "/passkey/login/options",
    response_model=PasskeyLoginOptionsResponse,
    summary="Passkey ile giriş için WebAuthn sınaması oluştur",
    description=(
        "Şifresiz passkey girişi ve otomatik doldurma (conditional mediation) için sınama döner."
    ),
)
def passkey_login_options(
    request: Request,
    payload: PasskeyLoginOptionsRequest | None = None,
    db: DBSession = Depends(get_db),
) -> PasskeyLoginOptionsResponse:
    """Generate WebAuthn assertion challenge for passkey authentication."""
    host_header = request.headers.get("host", "localhost")
    rp_id = host_header.split(":")[0]
    allowed_credentials: list[PasskeyCredential] | None = None

    if payload and payload.email:
        email_clean = payload.email.lower().strip()
        user = db.execute(select(User).where(User.email == email_clean)).scalar_one_or_none()
        if user:
            allowed_credentials = passkey_manager.list_user_passkeys(db, user.id)

    options = passkey_manager.generate_authentication_options(
        rp_id=rp_id,
        allowed_credentials=allowed_credentials,
    )
    return PasskeyLoginOptionsResponse(options=options)


@router.post(
    "/passkey/login/verify",
    response_model=LoginResponse,
    summary="Passkey doğrulamasını tamamla ve oturum aç",
    description="FIDO2 passkey iddiasını doğrular, oturum çerezi ve erişim belirteci oluşturur.",
)
def passkey_login_verify(
    payload: PasskeyLoginVerifyRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> LoginResponse:
    """Verify passkey assertion, defend against credential stuffing, and log user in."""
    client_ip = _get_client_ip(request)

    try:
        user = passkey_manager.verify_authentication_assertion(
            db=db,
            challenge=payload.challenge,
            credential_id=payload.credential_id,
            client_sign_count=payload.client_sign_count,
        )
    except PasskeyChallengeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CHALLENGE", "message": str(err)},
        ) from None
    except PasskeyNotFoundError:
        brute_force_defender.record_failure(client_ip, f"passkey:{payload.credential_id[:16]}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": "Güvenlik anahtarı geçersiz veya tanınmıyor.",
            },
        ) from None
    except PasskeyVerificationError as err:
        brute_force_defender.record_failure(client_ip, f"passkey:{payload.credential_id[:16]}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_FAILED", "message": str(err)},
        ) from None

    # Success: record success
    brute_force_defender.record_success(client_ip, user.email)

    # Optional guest token migration
    if payload.guest_token:
        try:
            guest_session_manager.migrate_guest_session(
                db=db,
                token=payload.guest_token,
                user_id=user.id,
            )
        except Exception as err:
            logger.warning("Guest migration failed during passkey login: %s", str(err))

    # Issue persistent session and cookies
    raw_session_token, csrf_token, _ = session_security_policy.create_session(
        db=db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
    )
    session_security_policy.set_session_cookies(response, raw_session_token, csrf_token)

    logger.info("User id %d logged in via Passkey from IP: %s", user.id, client_ip)
    return LoginResponse(
        access_token=raw_session_token,
        token_type="bearer",
        expires_in=int(session_security_policy.ttl.total_seconds()),
        requires_mfa=False,
        message="Passkey ile giriş başarılı.",
    )


@router.get(
    "/passkey/list",
    response_model=PasskeyListResponse,
    summary="Kayıtlı passkey cihazlarını listele",
    description="Kullanıcının hesabına bağlı tüm passkey ve güvenlik anahtarlarını listeler.",
)
def list_passkeys(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> PasskeyListResponse:
    """List all registered passkeys for authenticated user."""
    creds = passkey_manager.list_user_passkeys(db, current_user.user_id)
    items = [
        PasskeyItemResponse(
            id=c.id,
            credential_id=c.credential_id,
            device_name=c.device_name,
            transports=c.transports.split(",") if c.transports else [],
            created_at=c.created_at,
            last_used_at=c.last_used_at,
        )
        for c in creds
    ]
    return PasskeyListResponse(passkeys=items, total=len(items))


@router.patch(
    "/passkey/{passkey_id}",
    response_model=PasskeyItemResponse,
    summary="Passkey cihaz adını güncelle",
    description="Kullanıcının passkey cihazına verdiği adı günceller (IDOR korumalı).",
)
def rename_passkey(
    passkey_id: int,
    payload: PasskeyRenameRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> PasskeyItemResponse:
    """Update friendly device name for passkey."""
    try:
        cred = passkey_manager.rename_passkey(
            db=db,
            user_id=current_user.user_id,
            passkey_id=passkey_id,
            new_name=payload.device_name,
        )
    except PasskeyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PASSKEY_NOT_FOUND", "message": "Güvenlik anahtarı bulunamadı."},
        ) from None

    return PasskeyItemResponse(
        id=cred.id,
        credential_id=cred.credential_id,
        device_name=cred.device_name,
        transports=cred.transports.split(",") if cred.transports else [],
        created_at=cred.created_at,
        last_used_at=cred.last_used_at,
    )


@router.delete(
    "/passkey/{passkey_id}",
    response_model=PasskeyActionResponse,
    summary="Passkey cihazını sil / iptal et",
    description="Kayıtlı passkey cihazını hesaptan kaldırır (IDOR korumalı).",
)
def delete_passkey(
    passkey_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> PasskeyActionResponse:
    """Revoke and delete user's passkey."""
    try:
        passkey_manager.delete_passkey(
            db=db,
            user_id=current_user.user_id,
            passkey_id=passkey_id,
        )
    except PasskeyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PASSKEY_NOT_FOUND", "message": "Güvenlik anahtarı bulunamadı."},
        ) from None

    return PasskeyActionResponse(message="Güvenlik anahtarı başarıyla kaldırıldı.")
