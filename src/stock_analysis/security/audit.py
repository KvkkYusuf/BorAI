"""Administrative RBAC and Audit Logging Controls.

Implements Principle of Least Privilege (PoLP), privilege escalation defenses,
and immutable, sanitized audit trails for all administrative mutations.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session as DBSession

from stock_analysis.db.models import AuditEvent
from stock_analysis.security.environments import AccessRole

# Keywords indicating sensitive data that must NEVER appear in audit details
SENSITIVE_KEYWORD_PATTERNS = re.compile(
    r"(password|token|secret|api_key|auth|cookie|credential|hash|bearer)",
    re.IGNORECASE,
)

# Standard regex for identifying full email addresses in strings
EMAIL_PATTERN = re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")


class AdminActionType(StrEnum):
    """Canonical administrative action types recorded in audit events."""

    USER_ROLE_UPDATED = "USER_ROLE_UPDATED"
    USER_STATUS_UPDATED = "USER_STATUS_UPDATED"
    USER_SESSIONS_REVOKED = "USER_SESSIONS_REVOKED"
    SYSTEM_MAINTENANCE_TOGGLED = "SYSTEM_MAINTENANCE_TOGGLED"
    EMERGENCY_KILL_SWITCH = "EMERGENCY_KILL_SWITCH"
    SECURITY_SECRET_ROTATED = "SECURITY_SECRET_ROTATED"
    ADMIN_SETTINGS_UPDATED = "ADMIN_SETTINGS_UPDATED"


class PrivilegeEscalationError(ValueError):
    """Raised when an operation violates Least Privilege or attempts privilege escalation."""


# Numerical hierarchy ranks for AccessRole (higher number = greater authority)
ROLE_RANKS: dict[AccessRole, int] = {
    AccessRole.GUEST: 0,
    AccessRole.USER: 10,
    AccessRole.ANALYST: 20,
    AccessRole.COMPLIANCE_OFFICER: 30,
    AccessRole.ML_ENGINEER: 30,
    AccessRole.ADMIN: 40,
    AccessRole.SUPER_ADMIN: 50,
}


class RoleHierarchy:
    """Manages role rank relationships and enforces privilege escalation barriers."""

    @staticmethod
    def get_rank(role: AccessRole | str) -> int:
        """Return the numerical rank for a given role (defaults to 0 for unknown)."""
        try:
            parsed = role if isinstance(role, AccessRole) else AccessRole.from_string(role)
        except ValueError:
            return 0
        return ROLE_RANKS.get(parsed, 0)

    @staticmethod
    def can_manage_target(actor_role: AccessRole | str, target_role: AccessRole | str) -> bool:
        """Check if actor is permitted to manage target account.

        Rule:
        - SUPER_ADMIN can manage any account.
        - Other roles (including ADMIN) can only manage accounts whose rank is
          strictly LOWER than their own (e.g. admin cannot touch another admin or super_admin).
        """
        actor = (
            actor_role if isinstance(actor_role, AccessRole) else AccessRole.from_string(actor_role)
        )
        target = (
            target_role
            if isinstance(target_role, AccessRole)
            else AccessRole.from_string(target_role)
        )

        if actor == AccessRole.SUPER_ADMIN:
            return True

        return RoleHierarchy.get_rank(actor) > RoleHierarchy.get_rank(target)

    @staticmethod
    def can_assign_role(actor_role: AccessRole | str, new_role: AccessRole | str) -> bool:
        """Check if actor is permitted to assign the specified new role.

        Rule:
        - SUPER_ADMIN can assign any legitimate role.
        - ADMIN can only assign roles strictly LOWER than ADMIN (GUEST, USER, ANALYST).
          ADMIN cannot assign COMPLIANCE_OFFICER or ML_ENGINEER (requires SUPER_ADMIN),
          and CANNOT assign ADMIN or SUPER_ADMIN (privilege escalation prevention).
        """
        actor = (
            actor_role if isinstance(actor_role, AccessRole) else AccessRole.from_string(actor_role)
        )
        new_target = (
            new_role if isinstance(new_role, AccessRole) else AccessRole.from_string(new_role)
        )

        if actor == AccessRole.SUPER_ADMIN:
            return True

        if actor == AccessRole.ADMIN:
            # Admin can only manage standard application tiers
            return new_target in (AccessRole.GUEST, AccessRole.USER, AccessRole.ANALYST)

        return False

    @staticmethod
    def validate_role_transition(
        actor_role: AccessRole | str,
        target_current_role: AccessRole | str,
        new_role: AccessRole | str,
        *,
        is_self: bool = False,
        total_active_super_admins: int = 1,
    ) -> None:
        """Validate an intended role transition against PoLP and anti-escalation rules.

        Raises PrivilegeEscalationError if the operation is unlawful.
        """
        if is_self:
            raise PrivilegeEscalationError(
                "Yöneticiler kendi hesaplarının rolünü doğrudan değiştiremezler."
            )

        actor = (
            actor_role if isinstance(actor_role, AccessRole) else AccessRole.from_string(actor_role)
        )
        target_curr = (
            target_current_role
            if isinstance(target_current_role, AccessRole)
            else AccessRole.from_string(target_current_role)
        )
        target_new = (
            new_role if isinstance(new_role, AccessRole) else AccessRole.from_string(new_role)
        )

        # 1. Target rank check
        if not RoleHierarchy.can_manage_target(actor, target_curr):
            raise PrivilegeEscalationError(
                f"Yetki ihlali: '{actor.value}' rolü, kendisiyle eşit veya daha yüksek "
                f"yetkideki '{target_curr.value}' hesabını değiştiremez."
            )

        # 2. Assignable role rank check
        if not RoleHierarchy.can_assign_role(actor, target_new):
            raise PrivilegeEscalationError(
                f"Yetki yükseltme engeli: '{actor.value}' rolü, '{target_new.value}' "
                "rolü atama yetkisine sahip değildir."
            )

        # 3. Last remaining super_admin lockout prevention
        if (
            target_curr == AccessRole.SUPER_ADMIN
            and target_new != AccessRole.SUPER_ADMIN
            and total_active_super_admins <= 1
        ):
            raise PrivilegeEscalationError(
                "Sistem kilitlenme engeli: Sistemdeki son aktif kök yönetici (super_admin) "
                "rolü düşürülemez."
            )


def mask_email(email_str: str) -> str:
    """Mask email address for privacy preserving audit logs (e.g. u***@domain.com)."""

    def _replace(match: re.Match[str]) -> str:
        local, domain = match.group(1), match.group(2)
        masked_local = f"{local[0]}***" if len(local) > 0 else "***"
        return f"{masked_local}@{domain}"

    return EMAIL_PATTERN.sub(_replace, email_str)


def sanitize_audit_details(details: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub sensitive secrets, passwords, tokens and mask emails."""
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if SENSITIVE_KEYWORD_PATTERNS.search(key):
            clean[key] = "[REDACTED]"
            continue

        if isinstance(value, str):
            clean[key] = mask_email(value)
        elif isinstance(value, dict):
            clean[key] = sanitize_audit_details(value)
        elif isinstance(value, list):
            clean_list: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    clean_list.append(mask_email(item))
                elif isinstance(item, dict):
                    clean_list.append(sanitize_audit_details(item))
                else:
                    clean_list.append(item)
            clean[key] = clean_list
        else:
            clean[key] = value

    return clean


def hash_client_ip(client_ip: str | None, salt: str = "audit_ip_salt") -> str | None:
    """Hash client IP address with SHA-256 for non-identifying telemetry."""
    if not client_ip or not client_ip.strip():
        return None
    normalized = client_ip.strip().lower()
    return hashlib.sha256(f"{salt}:{normalized}".encode()).hexdigest()[:64]


def record_admin_audit_event(
    db: DBSession,
    *,
    actor_user_id: int,
    action: str | AdminActionType,
    resource: str,
    details: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> AuditEvent:
    """Persist an immutable, sanitized security audit event in the database.

    Enforces:
    - Actor identification
    - Action and target resource specification
    - Secret and PII redaction/masking
    - Pseudonymized IP hashing
    """
    sanitized_dict = sanitize_audit_details(details) if details else {}
    serialized_details = json.dumps(sanitized_dict, sort_keys=True, ensure_ascii=False)
    ip_hash = hash_client_ip(client_ip)

    event = AuditEvent(
        user_id=actor_user_id,
        action=str(action.value if isinstance(action, AdminActionType) else action),
        resource=resource[:128] if resource else None,
        ip_hash=ip_hash,
        details=serialized_details,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    db.flush()
    return event
