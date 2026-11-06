"""Tests for Data Retention, Deletion Policy, Tombstones, and Post-Restore Sanitization.

Validates KVKK Article 7, Law No. 5651 statutory audit retention protections,
expired session pruning, tombstone integrity, and automatic purging of resurrected
users following database backup restoration.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.db.base import Base
from stock_analysis.db.models import (
    AlertRule,
    AuditEvent,
    Instrument,
    User,
    Watchlist,
    WatchlistItem,
)
from stock_analysis.db.models import (
    Session as UserSession,
)
from stock_analysis.security.data_retention import (
    DEFAULT_RETENTION_RULES,
    DataRetentionPruner,
    DeletedUserTombstone,
    PostRestoreSanitizer,
    RetentionCategory,
    TombstoneRegistry,
)


@pytest.fixture
def in_memory_db() -> Generator[DBSession, None, None]:
    """Fixture providing an in-memory SQLite database populated with ORM schema."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with DBSession(engine) as session:
        yield session


class TestRetentionRulesMatrix:
    """Verify default retention rules conform to statutory Turkish and capital markets laws."""

    def test_retention_rules_categories_complete(self) -> None:
        rules = DEFAULT_RETENTION_RULES
        assert RetentionCategory.USER_ACCOUNT in rules
        assert RetentionCategory.USER_SESSION in rules
        assert RetentionCategory.AUTH_TOKENS in rules
        assert RetentionCategory.AUDIT_LOGS in rules
        assert RetentionCategory.MARKET_DATA in rules
        assert RetentionCategory.DATABASE_BACKUPS in rules

    def test_user_account_retention_is_zero_immediate_purge(self) -> None:
        rule = DEFAULT_RETENTION_RULES[RetentionCategory.USER_ACCOUNT]
        assert rule.retention_days == 0
        assert rule.can_prune is True
        assert "KVKK" in rule.statutory_basis

    def test_audit_logs_statutory_two_year_retention(self) -> None:
        rule = DEFAULT_RETENTION_RULES[RetentionCategory.AUDIT_LOGS]
        assert rule.retention_days == 730  # 2 years
        assert rule.is_immutable is True
        assert "5651" in rule.statutory_basis

    def test_market_data_ten_year_retention(self) -> None:
        rule = DEFAULT_RETENTION_RULES[RetentionCategory.MARKET_DATA]
        assert rule.retention_days == 3650
        assert rule.is_immutable is True
        assert "SPK" in rule.statutory_basis


class TestDeletedUserTombstone:
    """Test creation, email SHA-256 masking, and cryptographic seal verification."""

    def test_tombstone_creation_and_integrity_verification(self) -> None:
        tombstone = DeletedUserTombstone.create(
            user_id=42,
            email="investor@example.com",
            reason="KVKK_USER_REQUEST",
        )
        assert tombstone.user_id == 42
        assert "@" not in tombstone.email_sha256
        assert len(tombstone.email_sha256) == 64
        expected_hash = hashlib.sha256(b"investor@example.com").hexdigest()
        assert tombstone.email_sha256 == expected_hash
        assert tombstone.verify_integrity() is True

    def test_tombstone_tamper_detection(self) -> None:
        tombstone = DeletedUserTombstone.create(
            user_id=101,
            email="victim@example.com",
        )
        assert tombstone.verify_integrity() is True

        # Tampering with user_id or hash invalidates integrity
        tampered = tombstone.model_copy(update={"user_id": 999})
        assert tampered.verify_integrity() is False


class TestTombstoneRegistry:
    """Test registry indexing, persistence to disk, and loading."""

    def test_registry_in_memory_operations(self) -> None:
        registry = TombstoneRegistry()
        assert registry.count() == 0

        t1 = registry.record_deletion(user_id=1, email="user1@example.com")
        t2 = registry.record_deletion(user_id=2, email="user2@example.com")

        assert registry.count() == 2
        assert registry.is_deleted(1) is True
        assert registry.is_deleted(2) is True
        assert registry.is_deleted(3) is False

        fetched = registry.get_tombstone(1)
        assert fetched is not None
        assert fetched.user_id == 1
        assert fetched.email_sha256 == t1.email_sha256

        fetched2 = registry.get_tombstone(2)
        assert fetched2 is not None
        assert fetched2.email_sha256 == t2.email_sha256

    def test_registry_disk_persistence(self, tmp_path: Path) -> None:
        file_path = tmp_path / "tombstones" / "registry.json"
        registry = TombstoneRegistry(storage_path=file_path)

        registry.record_deletion(user_id=55, email="persisted@example.com")
        assert file_path.exists()

        # Reload from disk into a fresh instance
        loaded_registry = TombstoneRegistry(storage_path=file_path)
        assert loaded_registry.count() == 1
        assert loaded_registry.is_deleted(55) is True
        t = loaded_registry.get_tombstone(55)
        assert t is not None
        assert t.verify_integrity() is True

    def test_registry_rejects_tampered_tombstone(self) -> None:
        registry = TombstoneRegistry()
        t = DeletedUserTombstone.create(user_id=77, email="valid@example.com")
        bad_t = t.model_copy(update={"tombstone_signature": "bad_sig"})

        with pytest.raises(ValueError, match="bütünlüğü doğrulanamadı"):
            registry.add_tombstone(bad_t)


class TestPostRestoreSanitizer:
    """Test sanitization of restored database to prevent resurrection of deleted users."""

    def test_post_restore_sanitizes_resurrected_users(self, in_memory_db: DBSession) -> None:
        db = in_memory_db

        # 1. Setup users in restored database
        # User 10: Was deleted prior to backup restoration (in tombstone registry)
        # User 20: Legitimate active user (NOT in tombstone registry)
        user_deleted = User(
            id=10,
            email="deleted_prior@example.com",
            hashed_password="scrypt$dummy",
            role="user",
        )
        user_active = User(
            id=20,
            email="still_active@example.com",
            hashed_password="scrypt$dummy",
            role="user",
        )
        db.add_all([user_deleted, user_active])
        db.flush()

        # Add sessions, watchlist, alert for the user who should be purged
        session = UserSession(
            user_id=10,
            session_token_hash="token_hash_10",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        inst = Instrument(symbol="THYAO", name="Turk Hava Yollari")
        db.add_all([session, inst])
        db.flush()

        watchlist = Watchlist(user_id=10, name="My List")
        db.add(watchlist)
        db.flush()

        item = WatchlistItem(watchlist_id=watchlist.id, instrument_id=inst.id)
        alert = AlertRule(
            user_id=10,
            instrument_id=inst.id,
            condition_type="RSI_OVERSOLD",
            threshold=30.0,
        )
        db.add_all([item, alert])
        db.commit()

        # 2. Build tombstone registry marking user 10 as deleted
        registry = TombstoneRegistry()
        registry.record_deletion(
            user_id=10,
            email="deleted_prior@example.com",
            reason="KVKK_USER_REQUEST",
            deleted_at=datetime.now(UTC) - timedelta(days=2),
        )

        # 3. Execute post-restore sanitization
        sanitizer = PostRestoreSanitizer(tombstone_registry=registry)
        report = sanitizer.sanitize_database(db)

        assert report.status == "COMPLETED"
        assert report.purged_user_ids == [10]
        assert report.purged_sessions_count == 1
        assert report.purged_watchlists_count == 1
        assert report.purged_alerts_count == 1

        # 4. Verify user 10 is gone from DB, but user 20 is completely preserved
        assert db.get(User, 10) is None
        assert db.get(User, 20) is not None

        # Verify audit trail contains POST_RESTORE_PURGE record
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "POST_RESTORE_PURGE"))
        assert audit is not None
        assert "user:10" in (audit.resource or "")
        assert audit.details is not None
        assert "KVKK Yedekleme Temizligi" in audit.details

    def test_post_restore_noop_when_registry_empty(self, in_memory_db: DBSession) -> None:
        db = in_memory_db
        user = User(id=1, email="user@example.com", hashed_password="dummy")
        db.add(user)
        db.commit()

        registry = TombstoneRegistry()
        sanitizer = PostRestoreSanitizer(registry)
        report = sanitizer.sanitize_database(db)

        assert report.status == "NOOP_EMPTY_REGISTRY"
        assert db.get(User, 1) is not None


class TestDataRetentionPruner:
    """Test statutory pruning and Law 5651 protection."""

    def test_prune_expired_sessions(self, in_memory_db: DBSession) -> None:
        db = in_memory_db
        now = datetime.now(UTC)

        user = User(id=1, email="test@example.com", hashed_password="dummy")
        db.add(user)
        db.flush()

        # Session 1: Old and expired (created 40 days ago, expired 10 days ago) -> MUST PRUNE
        s_old = UserSession(
            user_id=1,
            session_token_hash="hash_old",
            created_at=now - timedelta(days=40),
            expires_at=now - timedelta(days=10),
        )
        # Session 2: Fresh and active -> MUST KEEP
        s_active = UserSession(
            user_id=1,
            session_token_hash="hash_active",
            created_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=5),
        )
        # Session 3: Expired recently (created 5 days ago, expired yesterday) -> KEEP until 30 days
        s_recent_expired = UserSession(
            user_id=1,
            session_token_hash="hash_recent",
            created_at=now - timedelta(days=5),
            expires_at=now - timedelta(days=1),
        )
        db.add_all([s_old, s_active, s_recent_expired])
        db.commit()

        pruner = DataRetentionPruner()
        pruned_count = pruner.prune_expired_sessions(db, as_of=now)

        assert pruned_count == 1
        remaining_sessions = db.scalars(select(UserSession)).all()
        remaining_hashes = {s.session_token_hash for s in remaining_sessions}
        assert "hash_old" not in remaining_hashes
        assert "hash_active" in remaining_hashes
        assert "hash_recent" in remaining_hashes

    def test_audit_logs_protected_within_two_years_and_pruned_after(
        self, in_memory_db: DBSession
    ) -> None:
        db = in_memory_db
        now = datetime.now(UTC)

        # Log 1: Created 3 years ago (1095 days) -> EXCEEDED 2-year retention -> PURGE
        old_audit = AuditEvent(
            action="LOGIN_ATTEMPT",
            resource="user:1",
            created_at=now - timedelta(days=1095),
        )
        # Log 2: Created 1 year ago (365 days) -> WITHIN 2 years (5651 mandatory) -> PROTECT
        statutory_audit_1 = AuditEvent(
            action="PASSWORD_CHANGED",
            resource="user:1",
            created_at=now - timedelta(days=365),
        )
        # Log 3: Created today -> WITHIN 2 years -> PROTECT
        statutory_audit_2 = AuditEvent(
            action="ACCOUNT_DELETED",
            resource="user:1",
            created_at=now - timedelta(hours=1),
        )
        db.add_all([old_audit, statutory_audit_1, statutory_audit_2])
        db.commit()

        pruner = DataRetentionPruner()
        purged_count, protected_count = pruner.prune_expired_audit_logs(db, as_of=now)

        assert purged_count == 1
        assert protected_count == 2

        # Verify only the 3-year-old audit was deleted
        remaining_audits = db.scalars(select(AuditEvent)).all()
        remaining_actions = {a.action for a in remaining_audits}
        assert "LOGIN_ATTEMPT" not in remaining_actions
        assert "PASSWORD_CHANGED" in remaining_actions
        assert "ACCOUNT_DELETED" in remaining_actions

    def test_execute_all_combined_prune(self, in_memory_db: DBSession) -> None:
        db = in_memory_db
        pruner = DataRetentionPruner()
        report = pruner.execute_all(db)
        assert report.status == "SUCCESS"
        assert report.duration_ms >= 0.0
