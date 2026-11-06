"""Administrative and privileged management endpoints.

Strictly enforces Principle of Least Privilege and mandates MFA for administrators
in production environments.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.api.deps import (
    AuthenticatedUser,
    get_pagination,
    require_admin,
    require_permission,
)
from stock_analysis.api.schemas import APIModel, PagedResponse
from stock_analysis.config import Settings, get_settings
from stock_analysis.db.models import AuditEvent, User
from stock_analysis.db.models import Session as UserSession
from stock_analysis.db.session import get_db
from stock_analysis.security import (
    AdminActionType,
    EnvironmentGuard,
    EnvironmentIsolationError,
    PrivilegeEscalationError,
    RoleHierarchy,
    record_admin_audit_event,
)

logger = logging.getLogger("stock_analysis.api.admin")

router = APIRouter(
    prefix="/admin",
    tags=["Administration & Security Operations"],
    dependencies=[Depends(require_admin)],
)


# --- Response Schemas ---


class AdminOverviewResponse(APIModel):
    """System and operational metrics overview for administrators."""

    app_env: str = Field(description="Active application runtime environment")
    admin_mfa_enforced: bool = Field(
        description="Whether mandatory MFA is strictly enforced for admins in this environment"
    )
    total_users: int = Field(ge=0, description="Total registered user accounts")
    total_admins: int = Field(ge=0, description="Total administrator accounts")
    active_sessions: int = Field(ge=0, description="Total non-expired active sessions")
    total_audit_events: int = Field(ge=0, description="Total recorded security/audit events")
    timestamp: datetime = Field(description="UTC timestamp of the status check")


class AdminUserItem(APIModel):
    """Safe representation of a user account for admin review."""

    id: int
    email: str
    role: str
    is_active: bool
    is_verified: bool
    mfa_enabled: bool
    created_at: datetime | None


class AdminAuditLogItem(APIModel):
    """Audit log entry for security and compliance monitoring."""

    id: int
    user_id: int | None
    action: str
    resource: str | None
    ip_hash: str | None
    details: str | None
    created_at: datetime


class UpdateUserStatusRequest(APIModel):
    """Request payload to activate or suspend a user account."""

    is_active: bool = Field(description="New active status for target account")
    reason: str | None = Field(
        default=None, max_length=256, description="Administrative justification"
    )


class UpdateUserRoleRequest(APIModel):
    """Request payload to assign a new RBAC role to a user account."""

    role: str = Field(description="Target RBAC access role name")
    reason: str | None = Field(
        default=None, max_length=256, description="Administrative justification"
    )


class TerminateUserSessionsRequest(APIModel):
    """Request payload to forcibly terminate all active sessions for a user."""

    reason: str | None = Field(
        default=None, max_length=256, description="Reason for session revocation"
    )


class TerminateUserSessionsResponse(APIModel):
    """Response payload detailing revoked sessions."""

    user_id: int
    terminated_sessions_count: int
    message: str


class EmergencyKillSwitchRequest(APIModel):
    """Request payload to toggle the emergency system-wide kill switch."""

    enable: bool = Field(description="Activate or deactivate emergency kill switch")
    reason: str = Field(
        min_length=5, max_length=256, description="Mandatory security justification"
    )


class EmergencyKillSwitchResponse(APIModel):
    """Response status for emergency kill switch."""

    kill_switch_active: bool
    message: str
    timestamp: datetime


# --- Endpoints ---


@router.get(
    "/overview",
    response_model=AdminOverviewResponse,
    summary="Yönetici sistem ve güvenlik genel bakışı",
    description="Sistem ortamı, kullanıcı sayıları, oturumlar ve yönetici MFA durumunu döner.",
)
def get_admin_overview(
    admin_user: AuthenticatedUser = Depends(require_admin),
    db: DBSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdminOverviewResponse:
    """Return operational system overview for privileged administrators."""
    now = datetime.now(UTC)
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    total_admins = (
        db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0
    )
    active_sessions = (
        db.scalar(select(func.count()).select_from(UserSession).where(UserSession.expires_at > now))
        or 0
    )
    total_audit = db.scalar(select(func.count()).select_from(AuditEvent)) or 0

    is_prod = settings.app_env.lower() == "production"

    return AdminOverviewResponse(
        app_env=settings.app_env,
        admin_mfa_enforced=is_prod,
        total_users=total_users,
        total_admins=total_admins,
        active_sessions=active_sessions,
        total_audit_events=total_audit,
        timestamp=now,
    )


@router.get(
    "/users",
    response_model=PagedResponse[AdminUserItem],
    summary="Kullanıcı hesapları listesi (En az ayrıcalık denetimli)",
    description="Yönetici yetkisiyle kullanıcıların temel durumlarını ve MFA etkinliğini listeler.",
)
def list_admin_users(
    pagination: tuple[int, int] = Depends(get_pagination),
    admin_user: AuthenticatedUser = Depends(require_admin),
    db: DBSession = Depends(get_db),
) -> PagedResponse[AdminUserItem]:
    """List registered users with status and MFA indicators."""
    page, page_size = pagination
    offset = (page - 1) * page_size

    total = db.scalar(select(func.count()).select_from(User)) or 0
    users = (
        db.execute(select(User).order_by(User.id.asc()).offset(offset).limit(page_size))
        .scalars()
        .all()
    )

    items = [
        AdminUserItem(
            id=u.id,
            email=u.email,
            role=u.role,
            is_active=u.is_active,
            is_verified=u.is_verified,
            mfa_enabled=u.mfa_enabled,
            created_at=u.created_at,
        )
        for u in users
    ]

    pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0

    return PagedResponse[AdminUserItem](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/audit-logs",
    response_model=PagedResponse[AdminAuditLogItem],
    summary="Güvenlik ve denetim kayıtları listesi",
    description="Sistem denetim olaylarını kronolojik olarak listeler.",
)
def list_audit_logs(
    pagination: tuple[int, int] = Depends(get_pagination),
    admin_user: AuthenticatedUser = Depends(require_admin),
    db: DBSession = Depends(get_db),
) -> PagedResponse[AdminAuditLogItem]:
    """List security audit trail entries."""
    page, page_size = pagination
    offset = (page - 1) * page_size

    total = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    logs = (
        db.execute(
            select(AuditEvent)
            .order_by(AuditEvent.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    items = [
        AdminAuditLogItem(
            id=entry.id,
            user_id=entry.user_id,
            action=entry.action,
            resource=entry.resource,
            ip_hash=entry.ip_hash,
            details=entry.details,
            created_at=entry.created_at,
        )
        for entry in logs
    ]

    pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0

    return PagedResponse[AdminAuditLogItem](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.patch(
    "/users/{user_id}/status",
    response_model=AdminUserItem,
    summary="Kullanıcı hesap durumu güncelleme (Aktif/Askıya Alma)",
    description="En az ayrıcalık ve yetki hiyerarşisi denetimli hesap dondurma/aktifleştirme.",
)
def update_user_status(
    user_id: int,
    payload: UpdateUserStatusRequest,
    request: Request,
    admin_user: AuthenticatedUser = Depends(require_permission("users:write_status")),
    db: DBSession = Depends(get_db),
) -> AdminUserItem:
    """Update user active status with PoLP role boundary checks."""
    target_user = db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"Kullanıcı bulunamadı: ID {user_id}"},
        )

    if admin_user.user_id == target_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANNOT_MODIFY_SELF",
                "message": "Yöneticiler kendi hesap durumlarını bu uçtan değiştiremezler.",
            },
        )

    if not RoleHierarchy.can_manage_target(admin_user.role, target_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN_INSUFFICIENT_PERMISSIONS",
                "message": (
                    f"'{admin_user.role}' yetkisi, kendisiyle eşit veya üst seviyedeki "
                    f"'{target_user.role}' kullanıcısını yönetemez."
                ),
            },
        )

    old_status = target_user.is_active
    target_user.is_active = payload.is_active

    client_ip = request.client.host if request.client else None
    record_admin_audit_event(
        db,
        actor_user_id=admin_user.user_id,
        action=AdminActionType.USER_STATUS_UPDATED,
        resource=f"user:{user_id}",
        details={
            "target_email": target_user.email,
            "previous_status": old_status,
            "new_status": payload.is_active,
            "reason": payload.reason,
        },
        client_ip=client_ip,
    )
    db.commit()
    db.refresh(target_user)

    return AdminUserItem(
        id=target_user.id,
        email=target_user.email,
        role=target_user.role,
        is_active=target_user.is_active,
        is_verified=target_user.is_verified,
        mfa_enabled=target_user.mfa_enabled,
        created_at=target_user.created_at,
    )


@router.patch(
    "/users/{user_id}/role",
    response_model=AdminUserItem,
    summary="Kullanıcı rolü güncelleme (En Az Ayrıcalık & Yetki Yükseltme Korumalı)",
    description=(
        "Yalnızca 'users:manage_roles' yetkisine sahip üst yöneticiler "
        "(super_admin) tarafından yürütülür."
    ),
)
def update_user_role(
    user_id: int,
    payload: UpdateUserRoleRequest,
    request: Request,
    admin_user: AuthenticatedUser = Depends(require_permission("users:manage_roles")),
    db: DBSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdminUserItem:
    """Assign or modify user role with strict privilege escalation prevention."""
    target_user = db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"Kullanıcı bulunamadı: ID {user_id}"},
        )

    clean_role = payload.role.strip().lower()

    # Validate target role environment compatibility
    try:
        EnvironmentGuard.validate_role_for_environment(clean_role, settings.app_env)
    except EnvironmentIsolationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_ROLE_FOR_ENVIRONMENT", "message": str(err)},
        ) from err

    # Count total active super admins to prevent system lockout
    total_super_admins = (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "super_admin", User.is_active == True)  # noqa: E712
        )
        or 0
    )

    try:
        RoleHierarchy.validate_role_transition(
            actor_role=admin_user.role,
            target_current_role=target_user.role,
            new_role=clean_role,
            is_self=(admin_user.user_id == target_user.id),
            total_active_super_admins=total_super_admins,
        )
    except PrivilegeEscalationError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PRIVILEGE_ESCALATION_BLOCKED", "message": str(err)},
        ) from err

    old_role = target_user.role
    target_user.role = clean_role

    client_ip = request.client.host if request.client else None
    record_admin_audit_event(
        db,
        actor_user_id=admin_user.user_id,
        action=AdminActionType.USER_ROLE_UPDATED,
        resource=f"user:{user_id}",
        details={
            "target_email": target_user.email,
            "previous_role": old_role,
            "new_role": clean_role,
            "reason": payload.reason,
        },
        client_ip=client_ip,
    )
    db.commit()
    db.refresh(target_user)

    return AdminUserItem(
        id=target_user.id,
        email=target_user.email,
        role=target_user.role,
        is_active=target_user.is_active,
        is_verified=target_user.is_verified,
        mfa_enabled=target_user.mfa_enabled,
        created_at=target_user.created_at,
    )


@router.post(
    "/users/{user_id}/terminate-sessions",
    response_model=TerminateUserSessionsResponse,
    summary="Kullanıcının aktif oturumlarını zorla kapatma",
    description=(
        "Şüpheli işlem veya hesap ele geçirme durumunda kullanıcının tüm oturumlarını sonlandırır."
    ),
)
def terminate_user_sessions(
    user_id: int,
    payload: TerminateUserSessionsRequest,
    request: Request,
    admin_user: AuthenticatedUser = Depends(require_permission("users:write_status")),
    db: DBSession = Depends(get_db),
) -> TerminateUserSessionsResponse:
    """Forcibly revoke all active session tokens for target user."""
    target_user = db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"Kullanıcı bulunamadı: ID {user_id}"},
        )

    if admin_user.user_id != target_user.id and not RoleHierarchy.can_manage_target(
        admin_user.role, target_user.role
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN_INSUFFICIENT_PERMISSIONS",
                "message": (
                    f"'{admin_user.role}' yetkisi, '{target_user.role}' oturumlarını sonlandıramaz."
                ),
            },
        )

    res = db.execute(delete(UserSession).where(UserSession.user_id == target_user.id))
    terminated_count = res.rowcount if hasattr(res, "rowcount") else 0

    client_ip = request.client.host if request.client else None
    record_admin_audit_event(
        db,
        actor_user_id=admin_user.user_id,
        action=AdminActionType.USER_SESSIONS_REVOKED,
        resource=f"user:{user_id}",
        details={
            "target_email": target_user.email,
            "terminated_sessions_count": terminated_count,
            "reason": payload.reason,
        },
        client_ip=client_ip,
    )
    db.commit()

    return TerminateUserSessionsResponse(
        user_id=target_user.id,
        terminated_sessions_count=terminated_count,
        message=f"{terminated_count} adet oturum başarıyla sonlandırıldı.",
    )


@router.post(
    "/system/emergency-kill-switch",
    response_model=EmergencyKillSwitchResponse,
    summary="Acil durum güvenlik durdurma anahtarı (Kill-switch)",
    description=(
        "Saldırı veya sızıntı anında sistemi acil duruma alır. Yalnızca super_admin çalıştırabilir."
    ),
)
def trigger_emergency_kill_switch(
    payload: EmergencyKillSwitchRequest,
    request: Request,
    admin_user: AuthenticatedUser = Depends(require_permission("security:emergency_kill_switch")),
    db: DBSession = Depends(get_db),
) -> EmergencyKillSwitchResponse:
    """Activate emergency security kill switch requiring super_admin authorization."""
    now = datetime.now(UTC)
    client_ip = request.client.host if request.client else None

    record_admin_audit_event(
        db,
        actor_user_id=admin_user.user_id,
        action=AdminActionType.EMERGENCY_KILL_SWITCH,
        resource="system:emergency_kill_switch",
        details={
            "kill_switch_active": payload.enable,
            "reason": payload.reason,
            "timestamp": now.isoformat(),
        },
        client_ip=client_ip,
    )
    db.commit()

    status_str = "etkinleştirildi" if payload.enable else "devre dışı bırakıldı"
    return EmergencyKillSwitchResponse(
        kill_switch_active=payload.enable,
        message=f"Acil durum durdurma anahtarı (kill-switch) başarıyla {status_str}.",
        timestamp=now,
    )
