"""Data Retention and Deletion Policy Enforcement and Post-Restore Sanitization.

Implements KVKK Article 7, Regulation on Deletion, Destruction or Anonymization
of Personal Data, and Law No. 5651 statutory audit retention standards.
Enforces data lifecycle pruning, tombstone tracking for hard-deleted personal data,
and automatic post-restore sanitization to prevent deleted user resurrection
when restoring cryptographic database backups.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.db.models import (
    AuditEvent,
    User,
)
from stock_analysis.db.models import (
    Session as UserSession,
)

logger = logging.getLogger("stock_analysis.security.data_retention")


class RetentionCategory(StrEnum):
    """Data categories governing distinct statutory and operational retention periods."""

    USER_ACCOUNT = "user_account"
    USER_SESSION = "user_session"
    AUTH_TOKENS = "auth_tokens"
    AUDIT_LOGS = "audit_logs"
    MARKET_DATA = "market_data"
    DATABASE_BACKUPS = "database_backups"


@dataclass(frozen=True)
class RetentionPeriodRule:
    """Retention rule defining max lifespan, statutory basis, and pruning policy."""

    category: RetentionCategory
    retention_days: int | None
    statutory_basis: str
    can_prune: bool
    is_immutable: bool
    description: str


# Canonical retention matrix adhering to KVKK, Law No. 5651, and SPK Capital Markets regulations
DEFAULT_RETENTION_RULES: dict[RetentionCategory, RetentionPeriodRule] = {
    RetentionCategory.USER_ACCOUNT: RetentionPeriodRule(
        category=RetentionCategory.USER_ACCOUNT,
        retention_days=0,  # Hard-deleted immediately upon user request (KVKK Art. 7)
        statutory_basis="KVKK Madde 7, 11 (Unutturulma Hakkı & Kalıcı Silme)",
        can_prune=True,
        is_immutable=False,
        description="Kullanıcı hesabı ve profil verileri silme anında kalıcı olarak silinir.",
    ),
    RetentionCategory.USER_SESSION: RetentionPeriodRule(
        category=RetentionCategory.USER_SESSION,
        retention_days=30,  # Expired or inactive sessions purged after 30 days
        statutory_basis="KVKK Asgari Veri İlkesi & Güvenlik Politikası",
        can_prune=True,
        is_immutable=False,
        description="Süresi dolmuş veya terk edilmiş oturum çerezleri 30 gün içinde budanır.",
    ),
    RetentionCategory.AUTH_TOKENS: RetentionPeriodRule(
        category=RetentionCategory.AUTH_TOKENS,
        retention_days=7,  # Used or expired reset/verification tokens purged after 7 days
        statutory_basis="Bilgi Güvenliği Politikası",
        can_prune=True,
        is_immutable=False,
        description="Tek kullanımlık şifre sıfırlama ve doğrulama belirteçleri 7 günde temizlenir.",
    ),
    RetentionCategory.AUDIT_LOGS: RetentionPeriodRule(
        category=RetentionCategory.AUDIT_LOGS,
        retention_days=730,  # 2 years mandatory retention under Law No. 5651
        statutory_basis="5651 Sayılı Kanun Madde 5, 9 (Erişim Kütükleri Saklama Zorunluluğu)",
        can_prune=False,  # Protected against early deletion during the 2-year statutory period
        is_immutable=True,
        description="Güvenlik ve erişim logları 5651 gereği en az 2 yıl silinmeden saklanır.",
    ),
    RetentionCategory.MARKET_DATA: RetentionPeriodRule(
        category=RetentionCategory.MARKET_DATA,
        retention_days=3650,  # 10 years for capital markets regulatory inspections
        statutory_basis="6362 Sayılı SPK Kanunu ve BIST Piyasa Düzenlemeleri",
        can_prune=False,
        is_immutable=True,
        description="Ham borsa OHLCV serileri ve kurumsal işlemler 10 yıl değişmez saklanır.",
    ),
    RetentionCategory.DATABASE_BACKUPS: RetentionPeriodRule(
        category=RetentionCategory.DATABASE_BACKUPS,
        retention_days=90,  # Rotated and destroyed after 90 days
        statutory_basis="Felaket Kurtarma ve İş Sürekliliği Politikası (RPO/RTO)",
        can_prune=True,
        is_immutable=True,
        description="Şifreli veritabanı anlık görüntüleri 90 gün saklanır ve döngüsel silinir.",
    ),
}


# ---------------------------------------------------------------------------
# Tombstone: Tracking Deleted Entities for Backup Sanitization
# ---------------------------------------------------------------------------
class DeletedUserTombstone(BaseModel):
    """Cryptographically hashed tombstone representing an unutturulma / deleted identity.

    Because cold/encrypted database backups cannot be mutated retroactively without
    destroying backup cryptographic chain integrity, tombstones guarantee that if an older
    backup is ever restored, the deleted users are immediately recognized and purged.
    """

    user_id: int = Field(description="Database user identifier that was deleted")
    email_sha256: str = Field(
        description="SHA-256 digest of normalized email for audit correlation"
    )
    deleted_at: datetime = Field(description="Timestamp when user requested deletion in UTC")
    reason: str = Field(
        default="KVKK_USER_REQUEST", description="Statutory or operational deletion reason"
    )
    tombstone_signature: str = Field(
        default="",
        description="Tamper-evident HMAC or SHA-256 integrity seal over tombstone attributes",
    )

    @classmethod
    def create(
        cls,
        user_id: int,
        email: str,
        reason: str = "KVKK_USER_REQUEST",
        deleted_at: datetime | None = None,
    ) -> DeletedUserTombstone:
        """Create a new tombstone record with hashed email and deterministic signature."""
        dt = deleted_at or datetime.now(UTC)
        normalized_email = email.strip().lower()
        email_hash = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()

        # Compute deterministic integrity seal
        seal_content = f"{user_id}:{email_hash}:{dt.isoformat()}:{reason}"
        sig = hashlib.sha256(seal_content.encode("utf-8")).hexdigest()

        return cls(
            user_id=user_id,
            email_sha256=email_hash,
            deleted_at=dt,
            reason=reason,
            tombstone_signature=sig,
        )

    def verify_integrity(self) -> bool:
        """Verify that the tombstone has not been tampered with."""
        seal_content = (
            f"{self.user_id}:{self.email_sha256}:{self.deleted_at.isoformat()}:{self.reason}"
        )
        expected_sig = hashlib.sha256(seal_content.encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.tombstone_signature, expected_sig)


class TombstoneRegistry:
    """Persistent or in-memory registry of deleted identities (Tombstones).

    Maintains append-only tombstone records used during backup restoration to purge
    re-introduced personal data.
    """

    def __init__(self, storage_path: Path | str | None = None) -> None:
        self._storage_path = Path(storage_path) if storage_path else None
        self._tombstones: dict[int, DeletedUserTombstone] = {}
        if self._storage_path and self._storage_path.exists():
            self._load_from_disk()

    def add_tombstone(self, tombstone: DeletedUserTombstone) -> None:
        """Add a tombstone to the registry and persist if path is configured."""
        if not tombstone.verify_integrity():
            raise ValueError("Tombstone imza bütünlüğü doğrulanamadı, kayıt reddedildi.")
        self._tombstones[tombstone.user_id] = tombstone
        if self._storage_path:
            self._save_to_disk()

    def record_deletion(
        self,
        user_id: int,
        email: str,
        reason: str = "KVKK_USER_REQUEST",
        deleted_at: datetime | None = None,
    ) -> DeletedUserTombstone:
        """Convenience method to construct and record a deletion tombstone."""
        tombstone = DeletedUserTombstone.create(
            user_id=user_id,
            email=email,
            reason=reason,
            deleted_at=deleted_at,
        )
        self.add_tombstone(tombstone)
        return tombstone

    def is_deleted(self, user_id: int) -> bool:
        """Check if user_id is in tombstone registry."""
        return user_id in self._tombstones

    def get_tombstone(self, user_id: int) -> DeletedUserTombstone | None:
        """Retrieve tombstone for given user_id."""
        return self._tombstones.get(user_id)

    def list_tombstones(self) -> list[DeletedUserTombstone]:
        """Return all active tombstones sorted by deletion date."""
        return sorted(self._tombstones.values(), key=lambda t: t.deleted_at)

    def count(self) -> int:
        """Return total count of tombstones."""
        return len(self._tombstones)

    def _save_to_disk(self) -> None:
        """Atomically persist tombstones to JSON file."""
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [t.model_dump(mode="json") for t in self._tombstones.values()]
        temp_path = self._storage_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)

    def _load_from_disk(self) -> None:
        """Load tombstones from disk with integrity check."""
        if not self._storage_path or not self._storage_path.exists():
            return
        content = self._storage_path.read_text(encoding="utf-8")
        if not content.strip():
            return
        items = json.loads(content)
        for item in items:
            t = DeletedUserTombstone.model_validate(item)
            if t.verify_integrity():
                self._tombstones[t.user_id] = t
            else:
                logger.warning("Corrupt tombstone in storage skipped: user_id %d", t.user_id)


# ---------------------------------------------------------------------------
# Post-Restore Sanitizer (Yedeklerde Silme Etkisi ve Geri Yükleme Temizliği)
# ---------------------------------------------------------------------------
class PostRestoreSanitizationReport(BaseModel):
    """Detailed audit report generated following post-restore sanitization."""

    restored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tombstones_checked: int
    purged_user_ids: list[int]
    purged_sessions_count: int
    purged_watchlists_count: int
    purged_alerts_count: int
    status: str = "COMPLETED"
    notes: str = (
        "KVKK ve yasal silme talepleri dogrultusunda yedekten geri gelen "
        "silinmis kullanicilar kalici olarak temizlendi (Hard Purge)."
    )


class PostRestoreSanitizer:
    """Sanitizes restored databases against the TombstoneRegistry.

    Addresses the core regulatory requirement (KVKK Kurul Kararı & Rehber):
    'Yedekleme sistemlerinin salt-okunur ve şifreli yapısı nedeniyle anında silinemeyen
    kişisel veriler, yedekten geri yükleme yapıldığında derhal tespit edilerek temizlenmelidir.'
    """

    def __init__(self, tombstone_registry: TombstoneRegistry) -> None:
        self.tombstone_registry = tombstone_registry

    def sanitize_database(
        self,
        db: DBSession,
        auto_commit: bool = True,
    ) -> PostRestoreSanitizationReport:
        """Inspects restored database, identifies resurrecting tombstoned users, and purges them."""
        tombstones = self.tombstone_registry.list_tombstones()
        tombstone_user_ids = {t.user_id for t in tombstones}

        if not tombstone_user_ids:
            return PostRestoreSanitizationReport(
                tombstones_checked=0,
                purged_user_ids=[],
                purged_sessions_count=0,
                purged_watchlists_count=0,
                purged_alerts_count=0,
                status="NOOP_EMPTY_REGISTRY",
            )

        # Find any users in restored DB matching tombstoned user IDs
        users_stmt = select(User).where(User.id.in_(tombstone_user_ids))
        resurrected_users = db.scalars(users_stmt).all()

        purged_ids: list[int] = []
        purged_sessions = 0
        purged_watchlists = 0
        purged_alerts = 0

        for user in resurrected_users:
            u_id = user.id
            purged_ids.append(u_id)

            # Count cascades before deletion
            purged_sessions += len(user.sessions)
            purged_watchlists += len(user.watchlists)
            purged_alerts += len(user.alert_rules)

            # Add post-restore audit event
            tombstone = self.tombstone_registry.get_tombstone(u_id)
            del_date = tombstone.deleted_at.isoformat() if tombstone else "unknown"
            audit = AuditEvent(
                user_id=None,
                action="POST_RESTORE_PURGE",
                resource=f"user:{u_id}",
                details=(
                    f"KVKK Yedekleme Temizligi: {del_date} tarihinde silinmis olan kullanici "
                    "yedekten geri yukleme sonrasinda post-restore sanitization ile "
                    "kalici olarak temizlendi."
                ),
            )
            db.add(audit)
            db.delete(user)

        if auto_commit:
            db.commit()

        logger.info(
            "Post-restore sanitization completed: %d resurrected users purged from database.",
            len(purged_ids),
        )

        return PostRestoreSanitizationReport(
            tombstones_checked=len(tombstone_user_ids),
            purged_user_ids=purged_ids,
            purged_sessions_count=purged_sessions,
            purged_watchlists_count=purged_watchlists,
            purged_alerts_count=purged_alerts,
            status="COMPLETED",
        )


# ---------------------------------------------------------------------------
# Periodic Retention and Pruning Engine (Veri Saklama ve Budama Motoru)
# ---------------------------------------------------------------------------
class PruneReport(BaseModel):
    """Execution report detailing deleted records across categories."""

    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expired_sessions_purged: int = 0
    expired_audit_logs_purged: int = 0
    statutory_audit_protected: int = 0
    duration_ms: float = 0.0
    status: str = "SUCCESS"


class DataRetentionPruner:
    """Executes statutory and operational pruning according to DEFAULT_RETENTION_RULES."""

    def __init__(
        self,
        rules: dict[RetentionCategory, RetentionPeriodRule] | None = None,
    ) -> None:
        self.rules = rules or DEFAULT_RETENTION_RULES

    def prune_expired_sessions(
        self,
        db: DBSession,
        as_of: datetime | None = None,
        auto_commit: bool = True,
    ) -> int:
        """Purge sessions that have exceeded their retention period or expired.

        Rule: Sessions expired longer than 30 days are hard-deleted.
        """
        now = as_of or datetime.now(UTC)
        session_rule = self.rules[RetentionCategory.USER_SESSION]
        retention_days = session_rule.retention_days or 30
        cutoff = now - timedelta(days=retention_days)

        # Delete sessions where expires_at < now AND created_at < cutoff
        stmt = delete(UserSession).where(
            (UserSession.expires_at < now) & (UserSession.created_at < cutoff)
        )
        result = db.execute(stmt)
        raw_count = getattr(result, "rowcount", 0)
        count = int(raw_count) if raw_count is not None and raw_count >= 0 else 0

        if auto_commit and count > 0:
            db.commit()

        logger.info("Pruned %d expired sessions older than %s", count, cutoff.isoformat())
        return count

    def prune_expired_audit_logs(
        self,
        db: DBSession,
        as_of: datetime | None = None,
        auto_commit: bool = True,
    ) -> tuple[int, int]:
        """Prunes audit logs older than the mandatory 2-year retention period under Law 5651.

        Returns (purged_count, protected_count).
        Strictly prevents deleting any audit logs still within the statutory retention window.
        """
        now = as_of or datetime.now(UTC)
        audit_rule = self.rules[RetentionCategory.AUDIT_LOGS]
        retention_days = audit_rule.retention_days or 730
        cutoff = now - timedelta(days=retention_days)

        # Count logs that MUST be protected
        protected_count = (
            db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.created_at >= cutoff)) or 0
        )

        # Only delete logs that are strictly older than the 2-year cutoff
        stmt = delete(AuditEvent).where(AuditEvent.created_at < cutoff)
        result = db.execute(stmt)
        raw_purged = getattr(result, "rowcount", 0)
        purged_count = int(raw_purged) if raw_purged is not None and raw_purged >= 0 else 0

        if auto_commit and purged_count > 0:
            db.commit()

        logger.info(
            "Audit pruning: %d logs older than 2 years purged, %d statutory logs protected.",
            purged_count,
            protected_count,
        )
        return purged_count, protected_count

    def execute_all(
        self,
        db: DBSession,
        as_of: datetime | None = None,
        auto_commit: bool = True,
    ) -> PruneReport:
        """Run all statutory and operational retention prune jobs and produce an audit report."""
        import time

        start = time.perf_counter()
        now = as_of or datetime.now(UTC)

        purged_sessions = self.prune_expired_sessions(db, as_of=now, auto_commit=False)
        purged_audits, protected_audits = self.prune_expired_audit_logs(
            db, as_of=now, auto_commit=False
        )

        if auto_commit:
            db.commit()

        elapsed = (time.perf_counter() - start) * 1000.0
        return PruneReport(
            executed_at=now,
            expired_sessions_purged=purged_sessions,
            expired_audit_logs_purged=purged_audits,
            statutory_audit_protected=protected_audits,
            duration_ms=round(elapsed, 2),
            status="SUCCESS",
        )


# Global default tombstone registry instance
DEFAULT_TOMBSTONE_REGISTRY = TombstoneRegistry()


def get_default_tombstone_registry() -> TombstoneRegistry:
    """Return the global shared TombstoneRegistry instance."""
    return DEFAULT_TOMBSTONE_REGISTRY


def reset_default_tombstone_registry() -> None:
    """Clear in-memory tombstones from the shared registry (for testing)."""
    DEFAULT_TOMBSTONE_REGISTRY._tombstones.clear()
