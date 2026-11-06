"""Alerts notification consent and RFC 8058 one-click unsubscribe router."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session as DBSession

from stock_analysis.alerts.subscription import (
    InvalidUnsubscribeTokenError,
    default_consent_manager,
)
from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.api.rate_limiter import public_rate_limiter, user_rate_limiter
from stock_analysis.api.schemas import (
    ConsentResponse,
    ConsentUpdateRequest,
    UnsubscribeResponse,
)
from stock_analysis.db.session import get_db

logger = logging.getLogger("stock_analysis.api.alerts")

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts & Notifications"],
)


@router.post(
    "/unsubscribe",
    response_model=UnsubscribeResponse,
    dependencies=[Depends(public_rate_limiter)],
    summary="RFC 8058 tek tıkla e-posta abonelikten çıkma (POST)",
    description=(
        "E-posta istemcileri (Gmail, Apple Mail vb.) veya tarayıcılar tarafından "
        "RFC 8058 One-Click standardına uygun olarak oturumsuz çağrılır."
    ),
)
async def one_click_unsubscribe_post(
    request: Request,
    token: str = Query(..., description="Kriptografik abonelikten çıkma token'ı"),
    db: DBSession = Depends(get_db),
) -> UnsubscribeResponse:
    """Handle RFC 8058 POST One-Click unsubscribe."""
    client_ip = request.client.host if request.client else "unknown"

    try:
        result = default_consent_manager.process_one_click_unsubscribe(
            token=token,
            source_ip=client_ip,
            db=db,
        )
    except InvalidUnsubscribeTokenError as exc:
        logger.warning("Invalid unsubscribe token attempt from %s: %s", client_ip, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_TOKEN", "message": str(exc)},
        ) from exc

    return UnsubscribeResponse(
        success=result.success,
        scope=result.scope,
        masked_email=result.masked_email,
        message=result.message,
        timestamp=result.timestamp_utc,
    )


@router.get(
    "/unsubscribe",
    dependencies=[Depends(public_rate_limiter)],
    summary="Tek tıkla e-posta abonelikten çıkma bağlantısı (GET)",
    description="Kullanıcı e-postadaki abonelikten çıkma bağlantısına tıkladığında çalışır.",
)
def one_click_unsubscribe_get(
    request: Request,
    token: str = Query(..., description="Kriptografik abonelikten çıkma token'ı"),
    format: str = Query("json", description="Yanıt biçimi ('json' veya 'html')"),
    db: DBSession = Depends(get_db),
) -> Any:
    """Handle GET link-based unsubscribe from email body."""
    client_ip = request.client.host if request.client else "unknown"

    try:
        result = default_consent_manager.process_one_click_unsubscribe(
            token=token,
            source_ip=client_ip,
            db=db,
        )
    except InvalidUnsubscribeTokenError as exc:
        logger.warning("Invalid unsubscribe token GET attempt from %s: %s", client_ip, exc)
        if format.lower() == "html":
            error_html = (
                "<!DOCTYPE html><html lang='tr'><head><meta charset='UTF-8'>"
                "<title>Geçersiz Bağlantı</title></head>"
                "<body style='font-family:sans-serif;text-align:center;padding:50px;'>"
                "<h2 style='color:#ef4444;'>Abonelik İptal Edilemedi</h2>"
                f"<p>{str(exc)}</p>"
                "</body></html>"
            )
            return HTMLResponse(content=error_html, status_code=400)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_TOKEN", "message": str(exc)},
        ) from exc

    if format.lower() == "html":
        card_style = (
            "max-width:480px;margin:0 auto;background:#fff;"
            "padding:30px;border-radius:8px;border:1px solid #e2e8f0;"
        )
        body_style = "font-family:sans-serif;text-align:center;padding:50px;background:#f8fafc;"
        footer_style = "margin-top:24px;font-size:12px;color:#94a3b8;"
        success_html = (
            f"<!DOCTYPE html><html lang='tr'><head><meta charset='UTF-8'>"
            f"<title>Abonelik İptal Edildi</title></head>"
            f"<body style='{body_style}'>"
            f"<div style='{card_style}'>"
            f"<h2 style='color:#16a34a;margin-top:0;'>✓ Aboneliğiniz İptal Edildi</h2>"
            f"<p style='color:#334155;'>{result.message}</p>"
            f"<p style='color:#64748b;font-size:12px;'>E-posta: {result.masked_email}</p>"
            f"<p style='{footer_style}'>Tercihlerinizi hesap panelinden güncelleyebilirsiniz.</p>"
            f"</div></body></html>"
        )
        return HTMLResponse(content=success_html)

    return UnsubscribeResponse(
        success=result.success,
        scope=result.scope,
        masked_email=result.masked_email,
        message=result.message,
        timestamp=result.timestamp_utc,
    )


@router.post(
    "/consent",
    response_model=ConsentResponse,
    dependencies=[Depends(user_rate_limiter)],
    summary="Bildirim izni (opt-in / opt-out) güncelle",
    description="Giriş yapmış kullanıcının bildirim alma iznini günceller.",
)
def update_notification_consent(
    payload: ConsentUpdateRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> ConsentResponse:
    """Update notification consent with KVKK audit logging."""
    client_ip = request.client.host if request.client else "unknown"

    if payload.is_granted:
        default_consent_manager.grant_consent(
            user_id=current_user.user_id,
            channel=payload.channel,
            scope=payload.scope,
            source_ip=client_ip,
            db=db,
        )
        msg = f"'{payload.channel}' kanalı için bildirim izni başarıyla verildi."
    else:
        unsub_res = default_consent_manager.revoke_consent(
            user_id=current_user.user_id,
            channel=payload.channel,
            scope=payload.scope,
            reason=payload.reason or "user_profile_toggle",
            source_ip=client_ip,
            db=db,
        )
        msg = unsub_res.message

    return ConsentResponse(
        user_id=current_user.user_id,
        channel=payload.channel,
        is_granted=payload.is_granted,
        scope=payload.scope,
        message=msg,
        updated_at=datetime.now(UTC),
    )


@router.get(
    "/consent/status",
    response_model=ConsentResponse,
    dependencies=[Depends(user_rate_limiter)],
    summary="Kullanıcı bildirim izin durumunu sorgula",
)
def get_notification_consent_status(
    channel: str = Query("email", description="Sorgulanacak bildirim kanalı"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: DBSession = Depends(get_db),
) -> ConsentResponse:
    """Query current user notification permission status."""
    is_permitted = default_consent_manager.is_notification_permitted(
        user_id=current_user.user_id,
        channel=channel,
        db=db,
    )

    status_msg = (
        f"'{channel}' kanalı bildirimleri aktif."
        if is_permitted
        else f"'{channel}' kanalı bildirimleri kapalı / abonelik iptal edilmiş."
    )

    return ConsentResponse(
        user_id=current_user.user_id,
        channel=channel,
        is_granted=is_permitted,
        scope="all",
        message=status_msg,
        updated_at=datetime.now(UTC),
    )
