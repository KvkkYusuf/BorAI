"""Secret management and key rotation architecture.

Provides secure abstraction for managing sensitive credentials, API keys, and session secrets
with multi-version tracking, automatic rotation interval auditing, zero-leakage redaction,
and dual-key grace periods to prevent service disruption during key rollovers.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

logger = logging.getLogger("stock_analysis.security.secrets")


class SecretStatus(StrEnum):
    """Lifecycle status of a secret version."""

    ACTIVE = "ACTIVE"
    ROTATING = "ROTATING"
    RETIRED = "RETIRED"
    REVOKED = "REVOKED"


class SecretCompromisedError(Exception):
    """Raised when attempting to use or validate against a revoked or compromised secret."""


class SecretNotFoundError(Exception):
    """Raised when a requested secret or version cannot be located in the manager."""


class SecretValue:
    """Wrapper that encapsulates sensitive values preventing accidental leakage in logs/traces.

    Implements timing-attack resistant comparison and masked representation.
    """

    def __init__(self, raw_value: str) -> None:
        if not isinstance(raw_value, str):
            raise TypeError("Secret value must be a string")
        self._raw_value: str = raw_value

    def get_secret_value(self) -> str:
        """Expose the raw secret value to authorized callers."""
        return self._raw_value

    def masked_display(self, prefix_len: int = 3, suffix_len: int = 3) -> str:
        """Return a masked representation for auditing purposes."""
        val = self._raw_value
        if len(val) <= (prefix_len + suffix_len):
            return "***"
        return f"{val[:prefix_len]}...{val[-suffix_len:]}"

    def equals(self, other: str | SecretValue) -> bool:
        """Timing-attack safe comparison against another value."""
        target = other.get_secret_value() if isinstance(other, SecretValue) else str(other)
        return hmac.compare_digest(self._raw_value, target)

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def __str__(self) -> str:
        return "[REDACTED]"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str | SecretValue):
            return self.equals(other)
        return False

    def __hash__(self) -> int:
        return hash(self._raw_value)


@dataclass(frozen=True)
class SecretMetadata:
    """Audit and lifecycle metadata for a secret version."""

    name: str
    version: int
    created_at: datetime
    expires_at: datetime | None
    rotation_interval_days: int = 90
    grace_period_days: int = 7
    status: SecretStatus = SecretStatus.ACTIVE
    compromised: bool = False
    description: str = ""

    def is_grace_period_valid(self, as_of: datetime | None = None) -> bool:
        """Check if a ROTATING/RETIRED key is still within its acceptable grace window."""
        if self.status == SecretStatus.REVOKED or self.compromised:
            return False
        if self.status == SecretStatus.ACTIVE:
            return True
        if self.expires_at is None:
            return False
        current_time = as_of or datetime.now(UTC)
        grace_deadline = self.expires_at + timedelta(days=self.grace_period_days)
        return current_time <= grace_deadline


@dataclass(frozen=True)
class SecretVersionEntry:
    """A secret version paired with its metadata and protected value."""

    metadata: SecretMetadata
    value: SecretValue


@dataclass
class RotationCheckResult:
    """Outcome of a periodic secret rotation audit."""

    name: str
    current_version: int
    is_due: bool
    days_until_due: float
    status: SecretStatus
    message: str


def generate_cryptographic_secret(length_bytes: int = 32, prefix: str = "") -> str:
    """Generate a high-entropy URL-safe cryptographic secret string."""
    token = secrets.token_urlsafe(length_bytes)
    return f"{prefix}{token}" if prefix else token


class SecretManager:
    """Multi-version in-memory and environment-backed Secret Manager.

    Supports graceful key rotation, automatic lifecycle transitions, and
    two-phase validation for zero-downtime key rollovers.
    """

    def __init__(
        self,
        default_rotation_interval_days: int = 90,
        default_grace_period_days: int = 7,
    ) -> None:
        self.default_rotation_interval_days = default_rotation_interval_days
        self.default_grace_period_days = default_grace_period_days
        self._secrets: dict[str, list[SecretVersionEntry]] = {}

    def set_secret(
        self,
        name: str,
        value: str | SecretValue,
        rotation_interval_days: int | None = None,
        grace_period_days: int | None = None,
        description: str = "",
        as_of: datetime | None = None,
    ) -> SecretVersionEntry:
        """Store a new secret or initial version."""
        now = as_of or datetime.now(UTC)
        interval = rotation_interval_days or self.default_rotation_interval_days
        grace = grace_period_days or self.default_grace_period_days

        sec_val = value if isinstance(value, SecretValue) else SecretValue(value)
        meta = SecretMetadata(
            name=name,
            version=1,
            created_at=now,
            expires_at=now + timedelta(days=interval),
            rotation_interval_days=interval,
            grace_period_days=grace,
            status=SecretStatus.ACTIVE,
            compromised=False,
            description=description,
        )
        entry = SecretVersionEntry(metadata=meta, value=sec_val)
        self._secrets[name] = [entry]
        logger.info("Secret '%s' initialized with version 1", name)
        return entry

    def get_entry(self, name: str, version: int | None = None) -> SecretVersionEntry:
        """Fetch a specific version or the active version of a secret."""
        if name not in self._secrets or not self._secrets[name]:
            raise SecretNotFoundError(f"Secret '{name}' not found")

        versions = self._secrets[name]
        if version is not None:
            for entry in versions:
                if entry.metadata.version == version:
                    return entry
            raise SecretNotFoundError(f"Secret '{name}' version {version} not found")

        # Find latest active version
        for entry in reversed(versions):
            if entry.metadata.status == SecretStatus.ACTIVE:
                return entry

        # Fallback to latest non-revoked version
        for entry in reversed(versions):
            if entry.metadata.status != SecretStatus.REVOKED:
                return entry

        # If all versions are revoked, return the latest version entry
        return versions[-1]

    def get_secret(self, name: str, version: int | None = None) -> SecretValue:
        """Fetch the protected SecretValue for a secret."""
        entry = self.get_entry(name, version=version)
        if entry.metadata.status == SecretStatus.REVOKED:
            raise SecretCompromisedError(
                f"Secret '{name}' version {entry.metadata.version} has been revoked"
            )
        return entry.value

    def get_raw_value(self, name: str, version: int | None = None) -> str:
        """Convenience method to retrieve the raw string value."""
        return self.get_secret(name, version=version).get_secret_value()

    def rotate_secret(
        self,
        name: str,
        new_value: str | SecretValue | None = None,
        as_of: datetime | None = None,
    ) -> SecretVersionEntry:
        """Rotate a secret to a new version, transitioning current key to ROTATING state.

        The existing ACTIVE key is marked as ROTATING with its grace period active,
        ensuring ongoing sessions or in-flight API requests are not abruptly rejected.
        """
        now = as_of or datetime.now(UTC)
        if name not in self._secrets or not self._secrets[name]:
            raise SecretNotFoundError(f"Cannot rotate nonexistent secret '{name}'")

        history = self._secrets[name]
        latest_entry = history[-1]
        new_version_num = latest_entry.metadata.version + 1

        # Transition prior active keys to ROTATING
        updated_history: list[SecretVersionEntry] = []
        for entry in history:
            if entry.metadata.status == SecretStatus.ACTIVE:
                new_meta = SecretMetadata(
                    name=entry.metadata.name,
                    version=entry.metadata.version,
                    created_at=entry.metadata.created_at,
                    expires_at=now,  # Active validity ends now
                    rotation_interval_days=entry.metadata.rotation_interval_days,
                    grace_period_days=entry.metadata.grace_period_days,
                    status=SecretStatus.ROTATING,
                    compromised=entry.metadata.compromised,
                    description=entry.metadata.description,
                )
                updated_history.append(SecretVersionEntry(metadata=new_meta, value=entry.value))
            else:
                updated_history.append(entry)

        # Generate new value if not explicitly provided
        val_to_store: SecretValue
        if new_value is None:
            generated = generate_cryptographic_secret(32)
            val_to_store = SecretValue(generated)
        elif isinstance(new_value, SecretValue):
            val_to_store = new_value
        else:
            val_to_store = SecretValue(new_value)

        interval = latest_entry.metadata.rotation_interval_days
        grace = latest_entry.metadata.grace_period_days
        new_entry = SecretVersionEntry(
            metadata=SecretMetadata(
                name=name,
                version=new_version_num,
                created_at=now,
                expires_at=now + timedelta(days=interval),
                rotation_interval_days=interval,
                grace_period_days=grace,
                status=SecretStatus.ACTIVE,
                compromised=False,
                description=latest_entry.metadata.description,
            ),
            value=val_to_store,
        )
        updated_history.append(new_entry)
        self._secrets[name] = updated_history
        logger.info("Secret '%s' successfully rotated to version %d", name, new_version_num)
        return new_entry

    def revoke_secret(
        self,
        name: str,
        version: int | None = None,
        reason: str = "Compromised or retired immediately",
    ) -> None:
        """Immediately revoke a secret version or all versions if version is None."""
        if name not in self._secrets:
            raise SecretNotFoundError(f"Secret '{name}' not found")

        updated_history: list[SecretVersionEntry] = []
        for entry in self._secrets[name]:
            if version is None or entry.metadata.version == version:
                revoked_meta = SecretMetadata(
                    name=entry.metadata.name,
                    version=entry.metadata.version,
                    created_at=entry.metadata.created_at,
                    expires_at=entry.metadata.expires_at,
                    rotation_interval_days=entry.metadata.rotation_interval_days,
                    grace_period_days=0,
                    status=SecretStatus.REVOKED,
                    compromised=True,
                    description=f"{entry.metadata.description} [REVOKED: {reason}]".strip(),
                )
                updated_history.append(SecretVersionEntry(metadata=revoked_meta, value=entry.value))
            else:
                updated_history.append(entry)

        self._secrets[name] = updated_history
        logger.warning("Secret '%s' (version %s) revoked. Reason: %s", name, str(version), reason)

    def validate_with_grace_period(
        self,
        name: str,
        validator_fn: Callable[[str], bool],
        as_of: datetime | None = None,
    ) -> bool:
        """Validate an incoming credential/signature against current active and grace-period keys.

        First attempts validation against the primary ACTIVE secret. If that fails,
        evaluates against any prior versions that are in ROTATING status within the
        allowed grace window.
        """
        if name not in self._secrets:
            return False

        now = as_of or datetime.now(UTC)
        versions = self._secrets[name]

        # 1. Check primary active key
        for entry in reversed(versions):
            if entry.metadata.status == SecretStatus.ACTIVE and not entry.metadata.compromised:
                if validator_fn(entry.value.get_secret_value()):
                    return True

        # 2. Check grace-period keys
        for entry in reversed(versions):
            if entry.metadata.status == SecretStatus.ROTATING:
                if entry.metadata.is_grace_period_valid(as_of=now):
                    if validator_fn(entry.value.get_secret_value()):
                        logger.info(
                            "Validation succeeded using grace-period key '%s' (v%d)",
                            name,
                            entry.metadata.version,
                        )
                        return True

        return False

    def check_rotation_status(
        self, name: str, as_of: datetime | None = None
    ) -> RotationCheckResult:
        """Inspect if a secret is due for scheduled rotation."""
        active_entry = self.get_entry(name)
        now = as_of or datetime.now(UTC)

        if active_entry.metadata.expires_at is None:
            return RotationCheckResult(
                name=name,
                current_version=active_entry.metadata.version,
                is_due=False,
                days_until_due=9999.0,
                status=active_entry.metadata.status,
                message="No expiration configured",
            )

        delta = active_entry.metadata.expires_at - now
        days_left = delta.total_seconds() / 86400.0
        is_due = days_left <= 0

        msg = (
            f"Rotation is overdue by {abs(days_left):.1f} days"
            if is_due
            else f"Rotation due in {days_left:.1f} days"
        )

        return RotationCheckResult(
            name=name,
            current_version=active_entry.metadata.version,
            is_due=is_due,
            days_until_due=days_left,
            status=active_entry.metadata.status,
            message=msg,
        )

    def audit_rotations(self, as_of: datetime | None = None) -> list[RotationCheckResult]:
        """Perform a system-wide audit of all registered secrets."""
        results: list[RotationCheckResult] = []
        for name in sorted(self._secrets.keys()):
            results.append(self.check_rotation_status(name, as_of=as_of))
        return results

    def list_secrets(self) -> list[str]:
        """List all secret identifier names."""
        return sorted(self._secrets.keys())

    def list_versions(self, name: str) -> list[SecretMetadata]:
        """List metadata for all versions of a given secret."""
        if name not in self._secrets:
            raise SecretNotFoundError(f"Secret '{name}' not found")
        return [e.metadata for e in self._secrets[name]]

    def load_from_environment(self, env_mappings: dict[str, str]) -> None:
        """Populate or update secrets from environment variables.

        Args:
            env_mappings: Mapping of SecretName -> EnvironmentVariableName
        """
        for sec_name, env_var in env_mappings.items():
            val = os.getenv(env_var)
            if val is not None:
                if sec_name in self._secrets:
                    current_raw = self.get_raw_value(sec_name)
                    if current_raw != val:
                        self.rotate_secret(sec_name, new_value=val)
                else:
                    self.set_secret(sec_name, value=val, description=f"Loaded from env {env_var}")


# Global default instance
_default_secret_manager: SecretManager | None = None


def get_default_secret_manager() -> SecretManager:
    """Return or initialize the singleton SecretManager with core application secrets."""
    global _default_secret_manager
    if _default_secret_manager is None:
        mgr = SecretManager()
        # Seed core secrets from environment with secure defaults
        mappings = {
            "session_secret": os.getenv("SESSION_SECRET", "default-dev-session-secret-change-me"),
            "market_data_api_key": os.getenv("MARKET_DATA_API_KEY", "default-dev-market-key"),
            "email_provider_api_key": os.getenv("EMAIL_PROVIDER_API_KEY", "default-dev-email-key"),
            "llm_api_key": os.getenv("LLM_API_KEY", "default-dev-llm-key"),
        }
        for name, value in mappings.items():
            mgr.set_secret(name, value=value, description=f"Core application secret: {name}")
        _default_secret_manager = mgr
    return _default_secret_manager


def reset_default_secret_manager() -> None:
    """Reset the default singleton instance (useful for test isolation)."""
    global _default_secret_manager
    _default_secret_manager = None
