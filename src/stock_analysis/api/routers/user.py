"""User preferences, watchlist, alert management, and KVKK privacy endpoints.

Adheres strictly to SPK anti-personalization rules (no portfolio, buy price, or lot metrics).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.api.rate_limiter import destructive_rate_limiter, user_rate_limiter
from stock_analysis.api.schemas import (
    AlertCreatedResponse,
    AlertCreateRequest,
    AlertItem,
    AlertsListResponse,
    DeleteAccountResponse,
    TerminateSessionsResponse,
    UserExportResponse,
    UserPreferencesResponse,
    UserPreferencesUpdateRequest,
    UserSessionItem,
    UserSessionsListResponse,
    WatchlistActionResponse,
    WatchlistResponse,
)
from stock_analysis.auth.session import session_security_policy
from stock_analysis.db.models import (
    AlertRule,
    AuditEvent,
    Instrument,
    User,
    UserPreference,
    Watchlist,
    WatchlistItem,
)
from stock_analysis.db.models import (
    Session as UserSession,
)
from stock_analysis.db.session import get_db
from stock_analysis.security.data_retention import DEFAULT_TOMBSTONE_REGISTRY

logger = logging.getLogger("stock_analysis.api.user")

router = APIRouter(
    prefix="/user",
    tags=["User & Watchlist & Alerts"],
    dependencies=[Depends(user_rate_limiter)],
)

MAX_ALERTS_PER_USER = 5


@router.get(
    "/preferences",
    response_model=UserPreferencesResponse,
    summary="Kullanıcı hesap ve bildirim ayarlarını getir",
    description="E-posta bildirim, saat dilimi, tema, dil ve uyarı sıklığı tercihlerini döner.",
)
def get_user_preferences(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> UserPreferencesResponse:
    """Return user preferences, creating default settings row if not yet existing."""
    pref = db.execute(
        select(UserPreference).where(UserPreference.user_id == current_user.user_id)
    ).scalar_one_or_none()

    if pref is None:
        pref = UserPreference(user_id=current_user.user_id)
        db.add(pref)
        db.commit()
        db.refresh(pref)

    return UserPreferencesResponse(
        email_notifications=pref.email_notifications,
        timezone=pref.timezone,
        theme=pref.theme,
        language=pref.language,
        alert_frequency=pref.alert_frequency,
        weekly_digest=pref.weekly_digest,
    )


@router.put(
    "/preferences",
    response_model=UserPreferencesResponse,
    summary="Kullanıcı hesap ve bildirim ayarlarını güncelle",
    description="E-posta bildirim, saat dilimi, tema, dil ve uyarı sıklığı tercihlerini günceller.",
)
def update_user_preferences(
    payload: UserPreferencesUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> UserPreferencesResponse:
    """Update user preferences with validated settings."""
    pref = db.execute(
        select(UserPreference).where(UserPreference.user_id == current_user.user_id)
    ).scalar_one_or_none()

    if pref is None:
        pref = UserPreference(user_id=current_user.user_id)
        db.add(pref)
        db.flush()

    if payload.email_notifications is not None:
        pref.email_notifications = payload.email_notifications
    if payload.timezone is not None:
        pref.timezone = payload.timezone
    if payload.theme is not None:
        pref.theme = payload.theme
    if payload.language is not None:
        pref.language = payload.language
    if payload.alert_frequency is not None:
        pref.alert_frequency = payload.alert_frequency
    if payload.weekly_digest is not None:
        pref.weekly_digest = payload.weekly_digest

    db.commit()
    db.refresh(pref)

    return UserPreferencesResponse(
        email_notifications=pref.email_notifications,
        timezone=pref.timezone,
        theme=pref.theme,
        language=pref.language,
        alert_frequency=pref.alert_frequency,
        weekly_digest=pref.weekly_digest,
    )


@router.get(
    "/watchlist",
    response_model=WatchlistResponse,
    summary="Kullanici izleme listesi",
    description="Takip edilen hisse sembollerini listeler. Portfoy veya maliyet icermez.",
)
def get_watchlist(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> WatchlistResponse:
    """Return list of ticker symbols tracked by the user."""
    # Find user watchlist
    wl = db.execute(
        select(Watchlist).where(Watchlist.user_id == current_user.user_id)
    ).scalar_one_or_none()

    if wl is None:
        return WatchlistResponse(watchlist=[])

    items_stmt = (
        select(Instrument.symbol)
        .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
        .where(WatchlistItem.watchlist_id == wl.id)
        .order_by(WatchlistItem.added_at.desc())
    )
    symbols = db.execute(items_stmt).scalars().all()
    return WatchlistResponse(watchlist=list(symbols))


@router.post(
    "/watchlist/{symbol}",
    response_model=WatchlistActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Izleme listesine sembol ekle",
    description="Secilen sembolu kullanicinin izleme listesine kaydeder.",
)
def add_to_watchlist(
    symbol: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> WatchlistActionResponse:
    """Add a symbol to user's tracking watchlist."""
    cleaned_symbol = symbol.strip().upper()

    # Verify or ensure instrument exists in DB
    inst = db.execute(
        select(Instrument).where(Instrument.symbol == cleaned_symbol)
    ).scalar_one_or_none()

    if inst is None:
        inst = Instrument(
            symbol=cleaned_symbol,
            name=f"{cleaned_symbol} Ortakligi",
            market="BIST",
            currency="TRY",
            is_active=True,
        )
        db.add(inst)
        db.flush()

    # Ensure watchlist exists
    wl = db.execute(
        select(Watchlist).where(Watchlist.user_id == current_user.user_id)
    ).scalar_one_or_none()

    if wl is None:
        wl = Watchlist(user_id=current_user.user_id, name="Varsayilan Takip")
        db.add(wl)
        db.flush()

    # Check if item already exists
    existing = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == wl.id,
            WatchlistItem.instrument_id == inst.id,
        )
    ).scalar_one_or_none()

    if existing is None:
        item = WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id)
        db.add(item)
        db.commit()

    return WatchlistActionResponse(
        message="Sembol izleme listesine eklendi.",
        symbol=cleaned_symbol,
    )


@router.delete(
    "/watchlist/{symbol}",
    response_model=WatchlistActionResponse,
    summary="Izleme listesinden sembol cikar",
    description="Secilen sembolu kullanicinin izleme listesinden siler.",
)
def remove_from_watchlist(
    symbol: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> WatchlistActionResponse:
    """Remove a symbol from user's tracking watchlist."""
    cleaned_symbol = symbol.strip().upper()

    wl = db.execute(
        select(Watchlist).where(Watchlist.user_id == current_user.user_id)
    ).scalar_one_or_none()

    if wl is not None:
        inst = db.execute(
            select(Instrument).where(Instrument.symbol == cleaned_symbol)
        ).scalar_one_or_none()
        if inst is not None:
            item = db.execute(
                select(WatchlistItem).where(
                    WatchlistItem.watchlist_id == wl.id,
                    WatchlistItem.instrument_id == inst.id,
                )
            ).scalar_one_or_none()
            if item is not None:
                db.delete(item)
                db.commit()

    return WatchlistActionResponse(
        message="Sembol listeden cikarildi.",
        symbol=cleaned_symbol,
    )


@router.get(
    "/alerts",
    response_model=AlertsListResponse,
    summary="Kullanicinin aktif uyari kurallari",
    description="Kullanicinin tanimladigi aktif gun sonu uyari kurallarini listeler.",
)
def list_alerts(
    page: int = Query(default=1, ge=1, description="Sayfa numarasi"),
    page_size: int = Query(default=50, ge=1, le=100, description="Sayfa basina kayit"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> AlertsListResponse:
    """List all active alert rules belonging to the authenticated user with pagination."""
    clean_page = page if isinstance(page, int) else 1
    clean_page_size = page_size if isinstance(page_size, int) else 50

    count_stmt = select(func.count(AlertRule.id)).where(
        AlertRule.user_id == current_user.user_id, AlertRule.is_active.is_(True)
    )
    total_count = db.scalar(count_stmt) or 0

    rules_stmt = (
        select(AlertRule, Instrument.symbol)
        .join(Instrument, Instrument.id == AlertRule.instrument_id)
        .where(AlertRule.user_id == current_user.user_id, AlertRule.is_active.is_(True))
        .order_by(AlertRule.created_at.desc())
        .offset((clean_page - 1) * clean_page_size)
        .limit(clean_page_size)
    )
    rows = db.execute(rules_stmt).all()

    items = [
        AlertItem(
            alert_id=f"alt_{rule.id}",
            symbol=sym,
            condition_type=rule.condition_type,
            target_value=rule.threshold,
            channel="EMAIL",
            is_active=rule.is_active,
            created_at=rule.created_at,
        )
        for rule, sym in rows
    ]

    return AlertsListResponse(alerts=items, total=total_count)


@router.post(
    "/alerts",
    response_model=AlertCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni uyari kurali tanimla",
    description="Fiyat esigi veya gosterge kosulu icin yeni bildirim kurali olusturur.",
)
def create_alert(
    payload: AlertCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> AlertCreatedResponse:
    """Create a threshold alert rule enforcing quota limit."""
    cleaned_symbol = payload.symbol.strip().upper()

    # Enforce quota of max 5 active alerts
    count_stmt = select(func.count(AlertRule.id)).where(
        AlertRule.user_id == current_user.user_id,
        AlertRule.is_active.is_(True),
    )
    active_count = db.scalar(count_stmt) or 0
    if active_count >= MAX_ALERTS_PER_USER:
        quota_msg = f"En fazla {MAX_ALERTS_PER_USER} aktif uyari kurali tanimlanabilir."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "ALERT_QUOTA_EXCEEDED",
                "message": quota_msg,
            },
        )

    # Ensure instrument exists
    inst = db.execute(
        select(Instrument).where(Instrument.symbol == cleaned_symbol)
    ).scalar_one_or_none()

    if inst is None:
        inst = Instrument(
            symbol=cleaned_symbol,
            name=f"{cleaned_symbol} Ortakligi",
            market="BIST",
            currency="TRY",
            is_active=True,
        )
        db.add(inst)
        db.flush()

    rule = AlertRule(
        user_id=current_user.user_id,
        instrument_id=inst.id,
        condition_type=payload.condition_type,
        threshold=payload.target_value,
        is_active=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    return AlertCreatedResponse(alert_id=f"alt_{rule.id}", status="active")


@router.delete(
    "/alerts/{alert_id}",
    response_model=dict[str, str],
    summary="Uyari kuralini sil",
    description="Kullaniciya ait olan uyari kuralini IDOR korumali olarak kaldirir.",
)
def delete_alert(
    alert_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    """Delete alert rule verifying strict ownership."""
    raw_id_str = alert_id.replace("alt_", "")
    try:
        rule_pk = int(raw_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_ALERT_ID", "message": "Gecersiz uyari kimligi formati."},
        ) from None

    rule = db.execute(
        select(AlertRule).where(
            AlertRule.id == rule_pk,
            AlertRule.user_id == current_user.user_id,
        )
    ).scalar_one_or_none()

    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ALERT_NOT_FOUND", "message": "Uyari kurali bulunamadi."},
        )

    db.delete(rule)
    db.commit()
    return {"message": "Uyari kurali basariyla silindi."}


@router.get(
    "/export",
    response_model=UserExportResponse,
    dependencies=[Depends(destructive_rate_limiter)],
    summary="KVKK veri tasinabilirligi kapsami (Veri indirme)",
    description="KVKK uyarinca kullanici profil, takip ve uyari verilerini paket olarak sunar.",
)
def export_user_data(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> UserExportResponse:
    """Return all user data for KVKK portability and log data export event."""
    user = db.get(User, current_user.user_id)
    created_at = user.created_at if user is not None else datetime.now(UTC)

    # Get preferences
    pref_response = get_user_preferences(current_user=current_user, db=db)
    # Get watchlist symbols
    wl_response = get_watchlist(current_user=current_user, db=db)
    # Get alerts
    alerts_response = list_alerts(page=1, page_size=100, current_user=current_user, db=db)

    # Compliance audit log for data export (KVKK / GDPR portability trail)
    export_audit = AuditEvent(
        user_id=current_user.user_id,
        action="DATA_EXPORTED",
        resource=f"user:{current_user.user_id}",
        details="Kullanıcı kişisel verilerini KVKK taşınabilirlik hakkı kapsamında dışa aktardı.",
    )
    db.add(export_audit)
    db.commit()

    return UserExportResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        created_at=created_at,
        preferences=pref_response,
        watchlists=wl_response.watchlist,
        alerts=alerts_response.alerts,
    )


@router.delete(
    "/account",
    response_model=DeleteAccountResponse,
    dependencies=[Depends(destructive_rate_limiter)],
    summary="KVKK unutturulma hakki (Hesap ve veri kalici silme)",
    description=(
        "Kullanici hesabini ve bagli verileri kalici olarak siler. "
        "Yasal saklama geregi denetim kayitlari korunur."
    ),
)
@router.delete(
    "/me",
    response_model=DeleteAccountResponse,
    dependencies=[Depends(destructive_rate_limiter)],
    summary="KVKK unutturulma hakki (Hesap ve veri kalici silme)",
    description=(
        "Kullanici hesabini ve bagli verileri kalici olarak siler. "
        "Yasal saklama geregi denetim kayitlari korunur."
    ),
)
def delete_account(
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> DeleteAccountResponse:
    """Hard delete user account and personal data, clearing cookies and preserving audit trail."""
    user = db.get(User, current_user.user_id)
    if user is not None:
        user_email = user.email
        # Statutory retention requirement (5651 Sayılı Kanun & KVKK):
        # We record the deletion action. Since AuditEvent.user_id has ON DELETE SET NULL,
        # the audit log remains anonymized and accessible to legal compliance.
        audit = AuditEvent(
            user_id=None,
            action="ACCOUNT_DELETED",
            resource=f"user:{current_user.user_id}",
            details=(
                "Kullanıcı hesabı KVKK silme talebiyle kalıcı olarak silindi. "
                "Yasal saklama yükümlülüğü gereği işlem izi korundu."
            ),
        )
        db.add(audit)
        try:
            DEFAULT_TOMBSTONE_REGISTRY.record_deletion(
                user_id=current_user.user_id,
                email=user_email,
                reason="KVKK Article 7 Right to Erasure",
            )
        except Exception as err:
            logger.warning("Failed to record tombstone for user %d: %s", current_user.user_id, err)
        db.delete(user)
        db.commit()

    # Invalidate and clear auth cookies
    session_security_policy.clear_session_cookies(response)
    logger.info("User id %d hard-deleted account; session cookies cleared.", current_user.user_id)

    return DeleteAccountResponse(message="Hesabiniz ve tum verileriniz kalici olarak silindi.")


@router.get(
    "/sessions",
    response_model=UserSessionsListResponse,
    summary="Kullanıcının aktif oturumlarını listele",
    description="Kullanıcıya ait geçerli tüm açık oturumları ve mevcut oturum işaretini döner.",
)
def list_user_sessions(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> UserSessionsListResponse:
    """Return all non-expired sessions for the authenticated user."""
    now = datetime.now(UTC)
    stmt = (
        select(UserSession)
        .where(
            UserSession.user_id == current_user.user_id,
            UserSession.expires_at > now,
        )
        .order_by(UserSession.created_at.desc())
    )
    records = db.execute(stmt).scalars().all()

    items = [
        UserSessionItem(
            id=s.id,
            created_at=s.created_at,
            expires_at=s.expires_at,
            user_agent=s.user_agent,
            ip_hash=s.ip_hash,
            is_current=(s.id == current_user.session_id),
        )
        for s in records
    ]
    return UserSessionsListResponse(sessions=items, total=len(items))


@router.delete(
    "/sessions/other",
    response_model=TerminateSessionsResponse,
    summary="Mevcut oturum haricindeki diğer tüm oturumları kapat",
    description="Kullanıcının şu an kullandığı oturum hariç diğer aktif oturumlarını sonlandırır.",
)
def terminate_other_sessions(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> TerminateSessionsResponse:
    """Terminate all user sessions except the current active one."""
    stmt = select(UserSession).where(UserSession.user_id == current_user.user_id)
    if current_user.session_id is not None:
        stmt = stmt.where(UserSession.id != current_user.session_id)

    other_sessions = db.execute(stmt).scalars().all()
    count = len(other_sessions)
    for s in other_sessions:
        db.delete(s)
    db.commit()

    logger.info("User id %d terminated %d other active sessions", current_user.user_id, count)
    return TerminateSessionsResponse(
        message="Diğer aktif oturumlar başarıyla kapatıldı.",
        terminated_count=count,
    )


@router.delete(
    "/sessions/{session_id}",
    response_model=TerminateSessionsResponse,
    summary="Belirli bir oturumu sonlandır",
    description="IDOR korumalı şekilde seçilen oturumu kapatır.",
)
def terminate_session_by_id(
    session_id: int,
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> TerminateSessionsResponse:
    """Terminate a specific session belonging strictly to the authenticated user."""
    stmt = select(UserSession).where(
        UserSession.id == session_id,
        UserSession.user_id == current_user.user_id,
    )
    session_record = db.execute(stmt).scalar_one_or_none()
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Oturum bulunamadı."},
        )

    db.delete(session_record)
    db.commit()

    # If the user closed their own current session, clear their cookies
    if current_user.session_id == session_id:
        session_security_policy.clear_session_cookies(response)

    logger.info("User id %d terminated session id %d", current_user.user_id, session_id)
    return TerminateSessionsResponse(
        message="Oturum başarıyla kapatıldı.",
        terminated_count=1,
    )


@router.delete(
    "/sessions",
    response_model=TerminateSessionsResponse,
    summary="Kullanıcının tüm oturumlarını sonlandır",
    description="Kullanıcıya ait tüm aktif oturumları kapatır ve çerezleri temizler.",
)
def terminate_all_sessions(
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> TerminateSessionsResponse:
    """Terminate all sessions for the authenticated user and clear cookies."""
    stmt = select(UserSession).where(UserSession.user_id == current_user.user_id)
    sessions = db.execute(stmt).scalars().all()
    count = len(sessions)
    for s in sessions:
        db.delete(s)
    db.commit()

    session_security_policy.clear_session_cookies(response)
    logger.info("User id %d terminated all %d sessions", current_user.user_id, count)
    return TerminateSessionsResponse(
        message="Tüm oturumlar başarıyla kapatıldı.",
        terminated_count=count,
    )
