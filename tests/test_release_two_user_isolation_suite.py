"""Sürüm 2 İzleme Listeleri ve Ayarlar İzolasyonu Kabul Kapısı Test Paketi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 2 — Hesap ve kişiselleştirme:
  - İzleme listeleri ve ayarlar kullanıcılar arasında izole.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.db.base import Base
from stock_analysis.db.models import Instrument, User, UserPreference, Watchlist, WatchlistItem
from stock_analysis.db.session import get_db
from stock_analysis.release.release_two_user_isolation import (
    RELEASE_TWO_USER_ISOLATION_STATUS,
    ReleaseTwoUIGateStatus,
    ReleaseTwoUserIsolationGatekeeper,
    UserIsolationVerificationResult,
    generate_markdown_report,
)


@pytest.fixture
def isolated_db_session() -> Generator[DBSession, None, None]:
    """Saf in-memory SQLite veritabanı oturumu fixture'ı."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


class TestReleaseTwoUserIsolationSuite:
    """Sürüm 2 İzleme Listeleri ve Ayarlar İzolasyon Kapısı testleri."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseTwoUserIsolationGatekeeper()
        now = datetime.now(UTC)

        result: UserIsolationVerificationResult = gatekeeper.verify_release_two_user_isolation(
            now=now
        )

        assert result.status == ReleaseTwoUIGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_TWO_USER_ISOLATION_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary

    def test_watchlist_data_cross_tenant_isolation(self, isolated_db_session: DBSession) -> None:
        """Farklı kullanıcıların izleme listelerinin birbirinden tamamen izole olduğunu doğrular."""
        db = isolated_db_session

        # 2 kullanıcı ve hisseler
        user1 = User(email="yatirimci1@finans.com", hashed_password="pwd1", is_active=True)
        user2 = User(email="yatirimci2@finans.com", hashed_password="pwd2", is_active=True)
        db.add_all([user1, user2])
        db.flush()

        inst1 = Instrument(symbol="THYAO", name="Türk Hava Yolları", is_active=True)
        inst2 = Instrument(symbol="GARAN", name="Garanti BBVA", is_active=True)
        inst3 = Instrument(symbol="BIMAS", name="BİM Mağazalar", is_active=True)
        db.add_all([inst1, inst2, inst3])
        db.flush()

        wl1 = Watchlist(user_id=user1.id, name="Favorilerim")
        wl2 = Watchlist(user_id=user2.id, name="Takip")
        db.add_all([wl1, wl2])
        db.flush()

        # user1 -> THYAO, GARAN
        db.add_all(
            [
                WatchlistItem(watchlist_id=wl1.id, instrument_id=inst1.id),
                WatchlistItem(watchlist_id=wl1.id, instrument_id=inst2.id),
            ]
        )
        # user2 -> BIMAS
        db.add(WatchlistItem(watchlist_id=wl2.id, instrument_id=inst3.id))
        db.commit()

        # User 1 sorgusu
        symbols_1 = list(
            db.scalars(
                select(Instrument.symbol)
                .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(Watchlist.user_id == user1.id)
            ).all()
        )

        # User 2 sorgusu
        symbols_2 = list(
            db.scalars(
                select(Instrument.symbol)
                .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(Watchlist.user_id == user2.id)
            ).all()
        )

        assert set(symbols_1) == {"THYAO", "GARAN"}
        assert set(symbols_2) == {"BIMAS"}
        assert len(set(symbols_1).intersection(set(symbols_2))) == 0

    def test_user_preferences_isolation_and_mutation(self, isolated_db_session: DBSession) -> None:
        """Kullanıcı tercihlerinin bağımsızlığını ve mutasyon güvenliğini doğrular."""
        db = isolated_db_session

        u1 = User(email="denetim1@ornek.com", hashed_password="pwd", is_active=True)
        u2 = User(email="denetim2@ornek.com", hashed_password="pwd", is_active=True)
        db.add_all([u1, u2])
        db.flush()

        p1 = UserPreference(user_id=u1.id, theme="dark", language="tr", email_notifications=True)
        p2 = UserPreference(user_id=u2.id, theme="light", language="en", email_notifications=False)
        db.add_all([p1, p2])
        db.commit()

        # Mutasyon: U1 temasını günceller
        p1.theme = "high-contrast"
        p1.weekly_digest = True
        db.commit()

        db.refresh(p1)
        db.refresh(p2)

        # U2 hiçbir şekilde etkilenmemelidir
        assert p1.theme == "high-contrast"
        assert p1.weekly_digest is True
        assert p2.theme == "light"
        assert p2.language == "en"
        assert p2.email_notifications is False
        assert p2.weekly_digest is False

    def test_api_endpoints_tenant_scoping_and_idor_protection(
        self, isolated_db_session: DBSession
    ) -> None:
        """FastAPI /api/v1/user/watchlist ve /preferences uçlarında tenant izolasyonunu doğrular."""
        db = isolated_db_session

        # 2 kullanıcı ve enstrüman
        u1 = User(email="api_u1@ornek.com", hashed_password="pwd", is_active=True)
        u2 = User(email="api_u2@ornek.com", hashed_password="pwd", is_active=True)
        db.add_all([u1, u2])
        db.flush()

        inst = Instrument(symbol="THYAO", name="THY", is_active=True)
        db.add(inst)
        db.commit()

        app = create_app()
        # Override get_db
        app.dependency_overrides[get_db] = lambda: db

        try:
            client = TestClient(app)

            # 1. User 1 bağlamında THYAO ekleyelim
            auth_u1 = AuthenticatedUser(user_id=u1.id, email=u1.email, role="user")
            app.dependency_overrides[get_current_user] = lambda: auth_u1

            res_add = client.post("/api/v1/user/watchlist/THYAO")
            assert res_add.status_code == 201
            assert res_add.json()["symbol"] == "THYAO"

            # User 1 listesinde THYAO olmalı
            res_wl1 = client.get("/api/v1/user/watchlist")
            assert res_wl1.status_code == 200
            assert "THYAO" in res_wl1.json()["watchlist"]

            # 2. User 2 bağlamına geçelim
            auth_u2 = AuthenticatedUser(user_id=u2.id, email=u2.email, role="user")
            app.dependency_overrides[get_current_user] = lambda: auth_u2

            # User 2 listesi BOŞ olmalı
            res_wl2 = client.get("/api/v1/user/watchlist")
            assert res_wl2.status_code == 200
            assert res_wl2.json()["watchlist"] == []

            # User 2 ayarını güncelleyelim
            res_pref2 = client.put(
                "/api/v1/user/preferences",
                json={"theme": "dark", "language": "en", "email_notifications": False},
            )
            assert res_pref2.status_code == 200
            assert res_pref2.json()["theme"] == "dark"

            # Tekrar User 1 bağlamına dönelim
            app.dependency_overrides[get_current_user] = lambda: auth_u1
            res_pref1 = client.get("/api/v1/user/preferences")
            assert res_pref1.status_code == 200
            # User 1'in varsayılan teması "system", User 2'nin yaptığı ayar User 1'e sızmamış olmalı
            assert res_pref1.json()["theme"] == "system"

        finally:
            app.dependency_overrides.clear()

    def test_watchlist_spk_compliance_no_financial_columns(self) -> None:
        """İzleme listesinde hiçbir portföy/maliyet/kâr verisi tutulmadığını SPK gereği doğrular."""
        gatekeeper = ReleaseTwoUserIsolationGatekeeper()
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        db = session_factory()

        user = User(email="spk_test@ornek.com", hashed_password="pwd", is_active=True)
        db.add(user)
        db.commit()

        check = gatekeeper._check_spk_compliance(db, user)
        assert check.passed is True
        assert "SPK uyumluluğu tam" in check.details

    def test_cascade_delete_clean_boundary(self, isolated_db_session: DBSession) -> None:
        """Kullanıcı silindiğinde kendi verisi silinirken diğerinin korunduğunu doğrular."""
        gatekeeper = ReleaseTwoUserIsolationGatekeeper()
        db = isolated_db_session

        u1 = User(email="silinecek@ornek.com", hashed_password="pwd", is_active=True)
        u2 = User(email="kalacak@ornek.com", hashed_password="pwd", is_active=True)
        db.add_all([u1, u2])
        db.flush()

        inst = Instrument(symbol="ASELS", name="Aselsan", is_active=True)
        db.add(inst)
        db.flush()

        wl1 = Watchlist(user_id=u1.id, name="W1")
        wl2 = Watchlist(user_id=u2.id, name="W2")
        db.add_all([wl1, wl2])
        db.flush()

        db.add_all(
            [
                WatchlistItem(watchlist_id=wl1.id, instrument_id=inst.id),
                WatchlistItem(watchlist_id=wl2.id, instrument_id=inst.id),
                UserPreference(user_id=u1.id),
                UserPreference(user_id=u2.id),
            ]
        )
        db.commit()

        check = gatekeeper._check_cascade_cleanup(db, u1, u2)
        assert check.passed is True
        assert "Kaskat silme korundu" in check.details

    def test_markdown_report_generation(self, tmp_path: Path) -> None:
        """Resmi denetim raporunun markdown olarak başarıyla üretildiğini doğrular."""
        gatekeeper = ReleaseTwoUserIsolationGatekeeper()
        result = gatekeeper.verify_release_two_user_isolation()

        report_file = tmp_path / "RELEASE_2_USER_ISOLATION_REPORT.md"
        generated_path = generate_markdown_report(result, report_file)

        assert generated_path.exists()
        content = generated_path.read_text(encoding="utf-8")
        assert "Sürüm 2 — İzleme Listeleri ve Ayarlar İzolasyonu" in content
        assert "CHECK_ISOLATION_WATCHLIST_DATA" in content
        assert "CHECK_ISOLATION_PREFERENCES_DATA" in content
        assert "CHECK_ISOLATION_IDOR_BOLA" in content
        assert "CHECK_ISOLATION_SPK_COMPLIANCE" in content
