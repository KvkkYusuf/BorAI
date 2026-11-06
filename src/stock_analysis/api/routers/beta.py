"""API endpoints for closed beta invitation gating and rollout status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from stock_analysis.api.schemas import (
    BetaInviteRedeemRequest,
    BetaInviteRedeemResponse,
    BetaInviteValidateRequest,
    BetaInviteValidateResponse,
    BetaRolloutStatusResponse,
)
from stock_analysis.security.closed_beta_gatekeeper import (
    CLOSED_BETA_MANAGER,
    ClosedBetaRolloutManager,
)

router = APIRouter(prefix="/beta", tags=["Closed Beta & Rollout"])


def get_beta_manager() -> ClosedBetaRolloutManager:
    """Dependency helper returning beta manager singleton."""
    return CLOSED_BETA_MANAGER


@router.get(
    "/status",
    response_model=BetaRolloutStatusResponse,
    summary="Mevcut kapalı beta ve kademeli dağıtım durumu",
    description=(
        "Aktif dağıtım aşamasını (CLOSED_BETA, CANARY_5, vb.), kamuya açık oranını, "
        "allowlist büyüklüğünü ve devre kesici (circuit breaker) durumunu döndürür."
    ),
)
def get_rollout_status() -> dict[str, Any]:
    """Return current rollout stage, traffic allocation, and circuit status."""
    manager = get_beta_manager()
    pct = manager.STAGE_PERCENTAGES.get(manager.current_stage, 0.0)
    return {
        "current_stage": manager.current_stage.value,
        "stage_percentage": pct,
        "allowlist_size": len(manager.allowlist),
        "circuit_breaker_active": manager.circuit_breaker_tripped,
        "circuit_breaker_reason": manager.circuit_breaker_reason,
    }


@router.post(
    "/validate-invite",
    response_model=BetaInviteValidateResponse,
    summary="Kapalı beta davetiye kodu doğrulama",
    description="Girilen davetiye kodunun geçerli, aktif ve kotası dolmamış olduğunu kontrol eder.",
)
def validate_invite(payload: BetaInviteValidateRequest) -> dict[str, Any]:
    """Validate closed beta invitation token validity."""
    manager = get_beta_manager()
    is_valid, message = manager.validate_invite_code(payload.code)
    return {
        "code": payload.code.strip().upper(),
        "is_valid": is_valid,
        "message": message,
    }


@router.post(
    "/redeem-invite",
    response_model=BetaInviteRedeemResponse,
    status_code=status.HTTP_200_OK,
    summary="Kapalı beta davetiye kodunu kullan ve kaydol",
    description=(
        "Davetiye kodunu kullanarak kullanıcıyı allowlist'e ekler. "
        "SPK yasal uyarısı ve KVKK kullanım şartlarının kabulü zorunludur."
    ),
)
def redeem_invite(payload: BetaInviteRedeemRequest) -> dict[str, Any]:
    """Redeem closed beta invitation with mandatory legal acknowledgment."""
    manager = get_beta_manager()
    success, message = manager.redeem_invite_code(
        code=payload.code,
        user_identifier=payload.user_identifier,
        agreed_to_terms=payload.agreed_to_terms,
        agreed_to_spk_disclaimer=payload.agreed_to_spk_disclaimer,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVITE_REDEMPTION_FAILED", "message": message},
        )
    return {
        "success": True,
        "message": message,
        "user_identifier": payload.user_identifier.strip().lower(),
    }


@router.get(
    "/check-eligibility",
    summary="Kullanıcının mevcut aşamada erişim hakkını kontrol et",
    description=(
        "Kullanıcı kimliğine göre mevcut dağıtım aşamasında erişim izni olup olmadığını bildirir."
    ),
)
def check_user_eligibility(
    user_id: str = Query(..., min_length=1, max_length=128, description="User identifier or email"),
) -> dict[str, Any]:
    """Check whether specified user is eligible for access."""
    manager = get_beta_manager()
    eligible, reason = manager.is_user_eligible(user_id)
    return {
        "user_id": user_id.strip().lower(),
        "eligible": eligible,
        "current_stage": manager.current_stage.value,
        "reason": reason,
    }
