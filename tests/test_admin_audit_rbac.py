"""Tests for Least Privilege, RBAC role hierarchy, and administrative audit logging."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.models import AuditEvent, User
from stock_analysis.db.models import Session as UserSession
from stock_analysis.db.session import get_db
from stock_analysis.security.audit import (
    AdminActionType,
    PrivilegeEscalationError,
    RoleHierarchy,
    hash_client_ip,
    mask_email,
    sanitize_audit_details,
)
from stock_analysis.security.environments import AccessRole

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db() -> Generator[None, None, None]:
    """Recreate clean database schema before each test."""
    Base.metadata.create_all(bind=test_engine)
    reset_all_rate_limiters()
    yield
    Base.metadata.drop_all(bind=test_engine)


def create_user_session(
    db: DBSession,
    email: str,
    role: str = "user",
    mfa_enabled: bool = False,
    is_active: bool = True,
) -> tuple[User, str]:
    """Helper to seed a test user and an active session token."""
    user = User(
        email=email,
        role=role,
        mfa_enabled=mfa_enabled,
        is_active=is_active,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = f"sess_token_{user.id}_{role}_{int(mfa_enabled)}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    session = UserSession(
        user_id=user.id,
        session_token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    db.add(session)
    db.commit()

    return user, raw_token


# ==============================================================================
# 1. Unit Tests: RoleHierarchy & Anti-Privilege Escalation
# ==============================================================================


def test_role_hierarchy_ranks() -> None:
    """Validate numerical rank relationships between standard roles."""
    assert RoleHierarchy.get_rank(AccessRole.GUEST) == 0
    assert RoleHierarchy.get_rank(AccessRole.USER) == 10
    assert RoleHierarchy.get_rank(AccessRole.ANALYST) == 20
    assert RoleHierarchy.get_rank(AccessRole.COMPLIANCE_OFFICER) == 30
    assert RoleHierarchy.get_rank(AccessRole.ML_ENGINEER) == 30
    assert RoleHierarchy.get_rank(AccessRole.ADMIN) == 40
    assert RoleHierarchy.get_rank(AccessRole.SUPER_ADMIN) == 50


def test_can_manage_target_rules() -> None:
    """Validate that admin cannot manage peers or superiors, but super_admin can."""
    # Admin can manage standard tiers
    assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.USER) is True
    assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.ANALYST) is True

    # Admin CANNOT manage another Admin or Super Admin
    assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.ADMIN) is False
    assert RoleHierarchy.can_manage_target(AccessRole.ADMIN, AccessRole.SUPER_ADMIN) is False

    # Super Admin can manage anyone
    assert RoleHierarchy.can_manage_target(AccessRole.SUPER_ADMIN, AccessRole.ADMIN) is True
    assert RoleHierarchy.can_manage_target(AccessRole.SUPER_ADMIN, AccessRole.USER) is True


def test_can_assign_role_rules() -> None:
    """Validate role assignment boundaries to prevent privilege escalation."""
    # Admin can only assign lower user tiers
    assert RoleHierarchy.can_assign_role(AccessRole.ADMIN, AccessRole.USER) is True
    assert RoleHierarchy.can_assign_role(AccessRole.ADMIN, AccessRole.ANALYST) is True
    assert RoleHierarchy.can_assign_role(AccessRole.ADMIN, AccessRole.ADMIN) is False
    assert RoleHierarchy.can_assign_role(AccessRole.ADMIN, AccessRole.SUPER_ADMIN) is False

    # Super Admin can assign any role
    assert RoleHierarchy.can_assign_role(AccessRole.SUPER_ADMIN, AccessRole.ADMIN) is True
    assert RoleHierarchy.can_assign_role(AccessRole.SUPER_ADMIN, AccessRole.SUPER_ADMIN) is True


def test_validate_role_transition_violations() -> None:
    """Verify exceptions raised on unlawful role transition attempts."""
    # Self-modification
    with pytest.raises(PrivilegeEscalationError, match="kendi hesaplarının rolünü"):
        RoleHierarchy.validate_role_transition(
            actor_role=AccessRole.ADMIN,
            target_current_role=AccessRole.ADMIN,
            new_role=AccessRole.SUPER_ADMIN,
            is_self=True,
        )

    # Admin targeting another Admin
    with pytest.raises(PrivilegeEscalationError, match="kendisiyle eşit veya daha yüksek"):
        RoleHierarchy.validate_role_transition(
            actor_role=AccessRole.ADMIN,
            target_current_role=AccessRole.ADMIN,
            new_role=AccessRole.USER,
        )

    # Admin attempting to assign Admin role
    with pytest.raises(PrivilegeEscalationError, match="Yetki yükseltme engeli"):
        RoleHierarchy.validate_role_transition(
            actor_role=AccessRole.ADMIN,
            target_current_role=AccessRole.USER,
            new_role=AccessRole.ADMIN,
        )

    # Super admin attempting to demote last active super admin
    with pytest.raises(PrivilegeEscalationError, match="son aktif kök yönetici"):
        RoleHierarchy.validate_role_transition(
            actor_role=AccessRole.SUPER_ADMIN,
            target_current_role=AccessRole.SUPER_ADMIN,
            new_role=AccessRole.ADMIN,
            total_active_super_admins=1,
        )


# ==============================================================================
# 2. Unit Tests: Data Sanitization & IP Hashing
# ==============================================================================


def test_sanitize_audit_details() -> None:
    """Ensure passwords, tokens, API keys are redacted and emails masked."""
    raw = {
        "user_email": "alice.investor@example.com",
        "nested": {
            "token": "secret_jwt_token_12345",
            "api_key": "live_provider_key_xyz",
            "notes": "Sent to bob.analyst@finance.com",
        },
        "clean_field": "operation_success",
        "credentials": "supersecretpassword",
    }
    sanitized = sanitize_audit_details(raw)

    assert sanitized["user_email"] == "a***@example.com"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["notes"] == "Sent to b***@finance.com"
    assert sanitized["clean_field"] == "operation_success"
    assert sanitized["credentials"] == "[REDACTED]"


def test_mask_email_utility() -> None:
    """Test individual email masking strings."""
    assert mask_email("johndoe@test.com") == "j***@test.com"
    assert mask_email("plain text without email") == "plain text without email"


def test_hash_client_ip() -> None:
    """Test client IP hashing utility."""
    assert hash_client_ip(None) is None
    assert hash_client_ip("") is None

    h1 = hash_client_ip("192.168.1.50")
    h2 = hash_client_ip("192.168.1.50")
    h3 = hash_client_ip("10.0.0.1")

    assert h1 is not None
    assert len(h1) == 64
    assert h1 == h2
    assert h1 != h3


# ==============================================================================
# 3. API Integration Tests: Administrative Operations & RBAC
# ==============================================================================


def test_admin_update_user_status_success_and_audit() -> None:
    """Standard admin can deactivate a standard user; audit trail is persisted."""
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    admin_user, admin_token = create_user_session(db, "admin@borsa.local", role="admin")
    target_user, _ = create_user_session(db, "target@borsa.local", role="user", is_active=True)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Deactivate user
    resp = client.patch(
        f"/api/v1/admin/users/{target_user.id}/status",
        headers=headers,
        json={"is_active": False, "reason": "Suspicious rapid login attempts"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False

    # Verify DB update
    db.expire_all()
    refreshed_user = db.get(User, target_user.id)
    assert refreshed_user is not None
    assert refreshed_user.is_active is False

    # Verify AuditEvent recorded
    audit = db.execute(
        select(AuditEvent).where(
            AuditEvent.action == AdminActionType.USER_STATUS_UPDATED,
            AuditEvent.resource == f"user:{target_user.id}",
        )
    ).scalar_one_or_none()

    assert audit is not None
    assert audit.user_id == admin_user.id
    details = json.loads(audit.details or "{}")
    assert details["previous_status"] is True
    assert details["new_status"] is False
    assert details["target_email"] == "t***@borsa.local"
    assert details["reason"] == "Suspicious rapid login attempts"


def test_admin_cannot_deactivate_peer_admin_or_self() -> None:
    """Admin cannot deactivate peer admin (403) or self (400)."""
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    admin1, token1 = create_user_session(db, "admin1@borsa.local", role="admin")
    admin2, _ = create_user_session(db, "admin2@borsa.local", role="admin")

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token1}"}

    # Attempt to deactivate peer admin
    resp = client.patch(
        f"/api/v1/admin/users/{admin2.id}/status",
        headers=headers,
        json={"is_active": False, "reason": "Attempt peer freeze"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN_INSUFFICIENT_PERMISSIONS"

    # Attempt to deactivate self
    resp_self = client.patch(
        f"/api/v1/admin/users/{admin1.id}/status",
        headers=headers,
        json={"is_active": False},
    )
    assert resp_self.status_code == 400
    assert resp_self.json()["error"]["code"] == "CANNOT_MODIFY_SELF"


def test_role_assignment_requires_super_admin() -> None:
    """Standard admin cannot assign roles (403); super_admin can assign roles (200)."""
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    admin_user, admin_token = create_user_session(db, "admin@borsa.local", role="admin")
    super_admin, super_token = create_user_session(db, "super@borsa.local", role="super_admin")
    target_user, _ = create_user_session(db, "member@borsa.local", role="user")

    client = TestClient(app)

    # 1. Standard admin attempts to change role -> 403 Forbidden
    resp_admin = client.patch(
        f"/api/v1/admin/users/{target_user.id}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "analyst", "reason": "Promotion"},
    )
    assert resp_admin.status_code == 403
    assert resp_admin.json()["error"]["code"] == "FORBIDDEN_INSUFFICIENT_PERMISSIONS"

    # 2. Super admin changes role to analyst -> 200 OK
    resp_super = client.patch(
        f"/api/v1/admin/users/{target_user.id}/role",
        headers={"Authorization": f"Bearer {super_token}"},
        json={"role": "analyst", "reason": "Institutional promotion"},
    )
    assert resp_super.status_code == 200
    assert resp_super.json()["role"] == "analyst"

    # Verify audit event
    audit = db.execute(
        select(AuditEvent).where(
            AuditEvent.action == AdminActionType.USER_ROLE_UPDATED,
            AuditEvent.resource == f"user:{target_user.id}",
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.user_id == super_admin.id
    details = json.loads(audit.details or "{}")
    assert details["previous_role"] == "user"
    assert details["new_role"] == "analyst"


def test_terminate_user_sessions_flow() -> None:
    """Admin can revoke all active sessions of a managed user."""
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    admin_user, admin_token = create_user_session(db, "admin@borsa.local", role="admin")
    target_user, _ = create_user_session(db, "victim@borsa.local", role="user")

    # Add second session for victim
    s2 = UserSession(
        user_id=target_user.id,
        session_token_hash="dummy_hash_2",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(s2)
    db.commit()

    # Pre-check: 2 active sessions
    active_count = db.scalar(select(UserSession).where(UserSession.user_id == target_user.id))
    assert active_count is not None

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/admin/users/{target_user.id}/terminate-sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"reason": "Compromised device report"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["terminated_sessions_count"] == 2

    # Verify sessions removed from DB
    remaining = (
        db.execute(select(UserSession).where(UserSession.user_id == target_user.id)).scalars().all()
    )
    assert len(remaining) == 0

    # Verify audit trail
    audit = db.execute(
        select(AuditEvent).where(
            AuditEvent.action == AdminActionType.USER_SESSIONS_REVOKED,
            AuditEvent.resource == f"user:{target_user.id}",
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.user_id == admin_user.id


def test_emergency_kill_switch_requires_super_admin() -> None:
    """Only super_admin can trigger emergency kill-switch; admin is rejected."""
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    admin_user, admin_token = create_user_session(db, "admin@borsa.local", role="admin")
    super_admin, super_token = create_user_session(db, "super@borsa.local", role="super_admin")

    client = TestClient(app)

    # Admin attempt -> 403 Forbidden
    resp_admin = client.post(
        "/api/v1/admin/system/emergency-kill-switch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"enable": True, "reason": "Detected intrusion attempt"},
    )
    assert resp_admin.status_code == 403
    assert resp_admin.json()["error"]["code"] == "FORBIDDEN_INSUFFICIENT_PERMISSIONS"

    # Super Admin attempt -> 200 OK
    resp_super = client.post(
        "/api/v1/admin/system/emergency-kill-switch",
        headers={"Authorization": f"Bearer {super_token}"},
        json={"enable": True, "reason": "Emergency security lock down"},
    )
    assert resp_super.status_code == 200
    assert resp_super.json()["kill_switch_active"] is True

    # Check audit log
    audit = db.execute(
        select(AuditEvent).where(AuditEvent.action == AdminActionType.EMERGENCY_KILL_SWITCH)
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.user_id == super_admin.id
    details = json.loads(audit.details or "{}")
    assert details["kill_switch_active"] is True
    assert details["reason"] == "Emergency security lock down"
