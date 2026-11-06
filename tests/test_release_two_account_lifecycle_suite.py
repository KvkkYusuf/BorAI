"""Sürüm 2 Hesap Dışa Aktarma ve Silme Süreçleri Kabul Kapısı Test Paketi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 2 — Hesap ve kişiselleştirme:
  - Hesap dışa aktarma/silme süreçleri test edildi.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.schemas import AlertItem, UserExportResponse, UserPreferencesResponse
from stock_analysis.auth.session import SessionSecurityPolicy
from stock_analysis.db.base import Base
from stock_analysis.db.models import (
    AuditEvent,
    Instrument,
    User,
    UserPreference,
    Watchlist,
)
from stock_analysis.release.release_two_account_lifecycle import (
    RELEASE_TWO_ACCOUNT_LIFECYCLE_STATUS,
    AccountLifecycleVerificationResult,
    ReleaseTwoAccountLifecycleGatekeeper,
    ReleaseTwoALGateStatus,
)
from stock_analysis.security.data_retention import (
    DEFAULT_TOMBSTONE_REGISTRY,
)


class TestReleaseTwoAccountLifecycleSuite:
    """Sürüm 2 Hesap Dışa Aktarma ve Silme Kabul Kapısı testleri."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseTwoAccountLifecycleGatekeeper()
        now = datetime.now(UTC)

        result: AccountLifecycleVerificationResult = (
            gatekeeper.verify_release_two_account_lifecycle(now=now)
        )

        assert result.status == ReleaseTwoALGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_TWO_ACCOUNT_LIFECYCLE_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary

    def test_kvkk_export_payload_structure(self) -> None:
        """KVKK taşınabilirlik dışa aktarma şemasının eksiksiz ve mevzuata uygunluğunu test eder."""
        now = datetime.now(UTC)
        export_payload = UserExportResponse(
            user_id=42,
            email="test.ihrac@finans.local",
            created_at=now,
            preferences=UserPreferencesResponse(
                theme="dark",
                language="tr",
                alert_frequency="immediate",
            ),
            watchlists=["THYAO", "BIMAS"],
            alerts=[
                AlertItem(
                    alert_id="1",
                    symbol="THYAO",
                    condition_type="price_above",
                    target_value=310.0,
                    channel="EMAIL",
                    is_active=True,
                    created_at=now,
                )
            ],
        )

        dumped = export_payload.model_dump()
        assert dumped["user_id"] == 42
        assert dumped["email"] == "test.ihrac@finans.local"
        assert dumped["preferences"]["theme"] == "dark"
        assert dumped["watchlists"] == ["THYAO", "BIMAS"]
        assert len(dumped["alerts"]) == 1
        assert "yatirim tavsiyesi" in dumped["disclaimer"].lower()

    def test_hard_deletion_and_cascade_purge_in_db(self) -> None:
        """Hesap silindiğinde veritabanından kalıcı silinmeyi ve kaskat temizliği test eder."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db: DBSession = session_factory()

        try:
            # User A (silinecek) ve User B (korunacak)
            u_a = User(email="silinecek_a@test.com", is_active=True, is_verified=True, role="user")
            u_b = User(email="korunacak_b@test.com", is_active=True, is_verified=True, role="user")
            db.add_all([u_a, u_b])
            db.commit()
            db.refresh(u_a)
            db.refresh(u_b)

            inst = Instrument(symbol="GARAN", name="Garanti", is_active=True)
            db.add(inst)
            db.commit()

            # Kayıtlar
            wl_a = Watchlist(user_id=u_a.id, name="A List")
            wl_b = Watchlist(user_id=u_b.id, name="B List")
            pref_a = UserPreference(user_id=u_a.id, theme="dark")
            pref_b = UserPreference(user_id=u_b.id, theme="light")
            db.add_all([wl_a, wl_b, pref_a, pref_b])
            db.commit()

            policy = SessionSecurityPolicy()
            token_a, _, _ = policy.create_session(db, u_a.id, "ip_a", "Agent-A")
            token_b, _, _ = policy.create_session(db, u_b.id, "ip_b", "Agent-B")

            # Silme işlemi öncesi varlık doğrulaması
            assert db.get(User, u_a.id) is not None
            assert policy.resolve_session(db, token_a) is not None

            # Hard delete
            db.delete(u_a)
            db.commit()
            db.expire_all()

            # User A ve bağlı tüm kayıtlar silinmiş olmalı
            assert db.get(User, u_a.id) is None
            assert (
                db.execute(select(Watchlist).where(Watchlist.user_id == u_a.id)).scalars().all()
                == []
            )
            assert (
                db.execute(select(UserPreference).where(UserPreference.user_id == u_a.id))
                .scalars()
                .all()
                == []
            )
            assert policy.resolve_session(db, token_a) is None

            # User B ve bağlı kayıtlar dokunulmamış olmalı
            assert db.get(User, u_b.id) is not None
            assert (
                len(
                    db.execute(select(Watchlist).where(Watchlist.user_id == u_b.id)).scalars().all()
                )
                == 1
            )
            assert policy.resolve_session(db, token_b) is not None
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)

    def test_statutory_audit_retention_law_5651(self) -> None:
        """5651 Sayılı Kanun gereği kullanıcı silinse dahi log kaydının user_id=None
        olarak kaldığını test eder."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db: DBSession = session_factory()

        try:
            user = User(
                email="denetim_user@test.com", is_active=True, is_verified=True, role="user"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            uid = user.id

            audit_log = AuditEvent(
                user_id=uid,
                action="USER_LOGIN",
                resource=f"user:{uid}",
                ip_hash="hash_123",
                details="Giris yapildi",
            )
            db.add(audit_log)
            db.commit()
            db.refresh(audit_log)
            audit_id = audit_log.id

            # Kullanıcı siliniyor
            db.delete(user)
            db.commit()
            db.expire_all()

            # AuditEvent kaydı yerinde durmalı ve user_id=None olmalı
            preserved = db.get(AuditEvent, audit_id)
            assert preserved is not None
            assert preserved.user_id is None
            assert preserved.action == "USER_LOGIN"
            assert preserved.ip_hash == "hash_123"
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)

    def test_tombstone_registry_pii_hashing(self) -> None:
        """Tombstone kaydının PII barındırmaksızın SHA-256 hash ile çalıştığını test eder."""
        user_id = 999
        email = "tombstone_deneme@borsa.local"

        entry = DEFAULT_TOMBSTONE_REGISTRY.record_deletion(
            user_id=user_id,
            email=email,
            reason="KVKK Article 7",
        )

        assert entry.user_id == user_id
        assert entry.email_sha256 != email
        assert len(entry.email_sha256) == 64
        assert DEFAULT_TOMBSTONE_REGISTRY.is_deleted(user_id=user_id) is True
        fetched = DEFAULT_TOMBSTONE_REGISTRY.get_tombstone(user_id=user_id)
        assert fetched is not None
        assert fetched.email_sha256 == entry.email_sha256

    def test_generate_audit_report(self, tmp_path: Path) -> None:
        """Denetim raporu Markdown üretimini ve içeriğini test eder."""
        gatekeeper = ReleaseTwoAccountLifecycleGatekeeper()
        result = gatekeeper.verify_release_two_account_lifecycle()

        report_file = tmp_path / "RELEASE_2_ACCOUNT_LIFECYCLE_REPORT.md"
        saved_path = gatekeeper.generate_audit_report(result, report_path=report_file)

        assert saved_path.exists()
        content = saved_path.read_text(encoding="utf-8")
        assert "# Sürüm 2 Kabul Raporu: Hesap Dışa Aktarma ve Silme Süreçleri" in content
        assert "REL2-AL-01-KVKK-DATA-EXPORT" in content
        assert "REL2-AL-02-HARD-ACCOUNT-DELETION" in content
        assert "REL2-AL-03-CASCADE-PURGE-ISOLATION" in content
        assert "REL2-AL-04-STATUTORY-AUDIT-RETENTION" in content
        assert "REL2-AL-05-SESSION-AND-COOKIE-CLEARING" in content
        assert "REL2-AL-06-TOMBSTONE-REGISTRY-HASHING" in content
        assert "KVKK Madde 11 (Veri Taşınabilirliği)" in content
        assert "5651 Sayılı Kanun" in content
