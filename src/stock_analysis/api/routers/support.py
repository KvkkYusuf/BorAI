"""User support, feedback channel, and customer ticketing endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from stock_analysis.api.schemas import (
    SupportChannelInfoResponse,
    SupportGateResponse,
    SupportTicketCreateRequest,
    SupportTicketResponse,
)
from stock_analysis.support.feedback_service import (
    FeedbackCategory,
    get_support_service,
)

router = APIRouter(prefix="/support", tags=["Support & Feedback"])


@router.post(
    "/tickets",
    response_model=SupportTicketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni destek talebi veya geri bildirim oluştur",
    description=(
        "Kullanıcıların hata bildirimi, özellik önerisi, veri problemi veya "
        "genel geri bildirim iletmelerini sağlar. Hız sınırı ve koruma uygulanır."
    ),
)
def create_support_ticket(
    payload: SupportTicketCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """Create a new user feedback or support ticket."""
    service = get_support_service()

    # Validate category enum
    try:
        category = FeedbackCategory(payload.category.lower())
    except ValueError:
        valid_cats = [c.value for c in FeedbackCategory]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CATEGORY",
                "message": (
                    f"Geçersiz kategori '{payload.category}'. Desteklenen: {valid_cats}"
                ),
            },
        ) from None

    # Client IP identification for rate limiting
    client_ip = request.client.host if request.client else None

    try:
        ticket = service.create_ticket(
            email=payload.email,
            category=category,
            subject=payload.subject,
            message=payload.message,
            client_metadata=payload.client_metadata,
            client_ip=client_ip,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMIT_EXCEEDED",
                "message": str(e),
            },
        ) from None

    return ticket.to_dict()


@router.get(
    "/tickets/{ticket_id}",
    response_model=SupportTicketResponse,
    summary="Destek talebi durum sorgulama",
    description="Referans takip kodu ile bir destek talebinin durumunu ve SLA süresini sorgular.",
)
def get_support_ticket(ticket_id: str) -> dict[str, Any]:
    """Retrieve details and status for a previously submitted ticket."""
    service = get_support_service()
    ticket = service.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "TICKET_NOT_FOUND",
                "message": f"'{ticket_id}' kodlu destek talebi sistemde bulunamadı.",
            },
        )
    return ticket.to_dict()


@router.get(
    "/channels",
    response_model=SupportChannelInfoResponse,
    summary="Resmi destek kanalları, çalışma saatleri ve SLA bilgisi",
    description=(
        "Kullanıcı destek e-postası, güvenlik bildirim kanalı, SLA süreleri ve "
        "SPK yasal uyarısını sunar."
    ),
)
def get_support_channels() -> dict[str, Any]:
    """Return official contact methods, SLA hours, and customer support rules."""
    service = get_support_service()
    return service.get_channel_info()


@router.get(
    "/gate",
    response_model=SupportGateResponse,
    summary="Dağıtım öncesi kullanıcı destek kanalı hazır bulunuşluk kontrolü",
    description="Destek ve geri bildirim altyapısının yayına hazır olup olmadığını doğrular.",
)
def get_support_readiness_gate() -> dict[str, Any]:
    """Execute pre-deployment support channel verification gate."""
    service = get_support_service()
    return service.verify_readiness_gate()
