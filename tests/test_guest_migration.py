"""Tests for anonymous guest session and migration to registered accounts."""

from __future__ import annotations

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
from stock_analysis.auth.guest_migration import (
    guest_session_manager,
)
from stock_analysis.auth.hasher import PasswordHasher
from stock_analysis.auth.session import session_security_policy
from stock_analysis.db.base import Base
from stock_analysis.db.models import Instrument, User, UserPreference, Watchlist, WatchlistItem
from stock_analysis.db.session import get_db

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
hasher = PasswordHasher()


class TestGuestMigration:
    """Comprehensive test suite for guest session lifecycle and user migration."""

    @classmethod
    def setup_class(cls) -> None:
        Base.metadata.create_all(bind=test_engine)

    @classmethod
    def teardown_class(cls) -> None:
        Base.metadata.drop_all(bind=test_engine)

    @pytest.fixture(autouse=True)
    def clean_slate(self) -> Generator[None, None, None]:
        reset_all_rate_limiters()
        guest_session_manager._sessions.clear()
        yield
        reset_all_rate_limiters()
        guest_session_manager._sessions.clear()

    @pytest.fixture
    def db_session(self) -> Generator[DBSession, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    @pytest.fixture
    def client(self, db_session: DBSession) -> Generator[TestClient, None, None]:
        app = create_app()

        def override_get_db() -> Generator[DBSession, None, None]:
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    @pytest.fixture
    def auth_user(self, db_session: DBSession) -> tuple[User, str]:
        import secrets

        rand_id = secrets.token_hex(4)
        user = User(
            email=f"test_guest_{rand_id}@example.com",
            hashed_password=hasher.hash("ValidPassword123!"),
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        token, csrf, _ = session_security_policy.create_session(
            db=db_session,
            user_id=user.id,
            user_agent="pytest-agent",
        )
        return user, token

    def test_create_guest_session(self, client: TestClient) -> None:
        """Test generating ephemeral guest session with initial sample value."""
        response = client.post("/api/v1/auth/guest/session")
        assert response.status_code == 201
        data = response.json()

        assert "guest_token" in data
        assert len(data["guest_token"]) > 20
        assert "THYAO" in data["watchlist"]
        assert "ASELS" in data["watchlist"]
        assert data["preferences"]["theme"] == "system"
        assert data["expires_in"] > 0

    def test_get_guest_session_state(self, client: TestClient) -> None:
        """Test retrieving an active guest session."""
        session = guest_session_manager.create_guest_session()
        response = client.get(f"/api/v1/auth/guest/{session.token}")
        assert response.status_code == 200
        data = response.json()

        assert data["guest_token"] == session.token
        assert data["watchlist"] == session.watchlist
        assert data["preferences"] == session.preferences

    def test_update_guest_watchlist(self, client: TestClient) -> None:
        """Test adding and removing ticker symbols in guest mode."""
        session = guest_session_manager.create_guest_session()

        # Add symbol
        add_resp = client.post(
            f"/api/v1/auth/guest/{session.token}/watchlist",
            json={"symbol": "SISE", "action": "add"},
        )
        assert add_resp.status_code == 200
        assert "SISE" in add_resp.json()["watchlist"]

        # Remove symbol
        rem_resp = client.post(
            f"/api/v1/auth/guest/{session.token}/watchlist",
            json={"symbol": "THYAO", "action": "remove"},
        )
        assert rem_resp.status_code == 200
        assert "THYAO" not in rem_resp.json()["watchlist"]
        assert "SISE" in rem_resp.json()["watchlist"]

    def test_guest_session_not_found_and_expired(self, client: TestClient) -> None:
        """Test error handling for non-existent and expired guest sessions."""
        # Non existent
        res_404 = client.get("/api/v1/auth/guest/non_existent_token_12345")
        assert res_404.status_code == 404
        assert res_404.json()["error"]["code"] == "GUEST_SESSION_NOT_FOUND"

        # Expired session
        session = guest_session_manager.create_guest_session()
        session.expires_at = datetime.now(UTC) - timedelta(minutes=5)

        res_410 = client.get(f"/api/v1/auth/guest/{session.token}")
        assert res_410.status_code == 410
        assert res_410.json()["error"]["code"] == "GUEST_SESSION_EXPIRED"

    def test_migrate_guest_session_to_user(
        self,
        client: TestClient,
        auth_user: tuple[User, str],
        db_session: DBSession,
    ) -> None:
        """Test migrating guest session symbols and preferences to authenticated user."""
        user, token = auth_user

        # Create guest session with specific symbols
        session = guest_session_manager.create_guest_session(
            sample_watchlist=["BIMAS", "TUPRS"],
            custom_preferences={"theme": "dark", "language": "tr"},
        )

        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/api/v1/auth/guest/migrate",
            headers=headers,
            json={"guest_token": session.token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "BIMAS" in data["migrated_symbols"]
        assert "TUPRS" in data["migrated_symbols"]
        assert data["preferences_migrated"] is True

        # Verify user watchlist in DB
        user_wl = db_session.execute(
            select(Watchlist).where(Watchlist.user_id == user.id)
        ).scalar_one_or_none()
        assert user_wl is not None

        items = (
            db_session.execute(
                select(Instrument.symbol)
                .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
                .where(WatchlistItem.watchlist_id == user_wl.id)
            )
            .scalars()
            .all()
        )
        assert "BIMAS" in items
        assert "TUPRS" in items

        # Verify user preferences updated
        pref = db_session.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        ).scalar_one_or_none()
        assert pref is not None
        assert pref.theme == "dark"

        # Guest session must be invalidated (single-use bridge)
        post_check = client.get(f"/api/v1/auth/guest/{session.token}")
        assert post_check.status_code == 404

    def test_migrate_guest_session_deduplication(
        self,
        client: TestClient,
        auth_user: tuple[User, str],
        db_session: DBSession,
    ) -> None:
        """Test that migrating existing symbols does not create duplicate items."""
        user, token = auth_user

        # Pre-populate user watchlist with BIMAS
        wl = Watchlist(user_id=user.id, name="Test WL")
        db_session.add(wl)
        db_session.flush()

        inst = db_session.execute(
            select(Instrument).where(Instrument.symbol == "BIMAS")
        ).scalar_one_or_none()
        if inst is None:
            inst = Instrument(
                symbol="BIMAS", name="BIM", market="BIST", currency="TRY", is_active=True
            )
            db_session.add(inst)
            db_session.flush()

        db_session.add(WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id))
        db_session.commit()

        # Guest session also has BIMAS and KCHOL
        session = guest_session_manager.create_guest_session(sample_watchlist=["BIMAS", "KCHOL"])

        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/api/v1/auth/guest/migrate",
            headers=headers,
            json={"guest_token": session.token},
        )
        assert response.status_code == 200
        migrated = response.json()["migrated_symbols"]
        assert "KCHOL" in migrated
        assert "BIMAS" not in migrated  # already existed, not duplicated

        # Check DB count
        count = len(
            db_session.execute(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id))
            .scalars()
            .all()
        )
        assert count == 2

    def test_guest_migration_on_register(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """Test that registering a user with a guest_token migrates items automatically."""
        session = guest_session_manager.create_guest_session(sample_watchlist=["AKBNK", "SAHOL"])

        reg_payload = {
            "email": "auto_migrated_user@example.com",
            "password": "SecurePassword123!",
            "guest_token": session.token,
        }
        response = client.post("/api/v1/auth/register", json=reg_payload)
        assert response.status_code == 200

        # User created in DB
        new_user = db_session.execute(
            select(User).where(User.email == "auto_migrated_user@example.com")
        ).scalar_one_or_none()
        assert new_user is not None

        # Check watchlist migrated
        user_wl = db_session.execute(
            select(Watchlist).where(Watchlist.user_id == new_user.id)
        ).scalar_one_or_none()
        assert user_wl is not None

        items = (
            db_session.execute(
                select(Instrument.symbol)
                .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
                .where(WatchlistItem.watchlist_id == user_wl.id)
            )
            .scalars()
            .all()
        )
        assert "AKBNK" in items
        assert "SAHOL" in items

    def test_guest_migration_on_login(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """Test that logging into an existing account with guest_token migrates items."""
        user = User(
            email="login_guest_user@example.com",
            hashed_password=hasher.hash("ValidPassword123!"),
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.commit()

        session = guest_session_manager.create_guest_session(sample_watchlist=["ENKAI"])

        login_payload = {
            "email": "login_guest_user@example.com",
            "password": "ValidPassword123!",
            "guest_token": session.token,
        }
        response = client.post("/api/v1/auth/login", json=login_payload)
        assert response.status_code == 200

        # Check watchlist migrated
        user_wl = db_session.execute(
            select(Watchlist).where(Watchlist.user_id == user.id)
        ).scalar_one_or_none()
        assert user_wl is not None

        items = (
            db_session.execute(
                select(Instrument.symbol)
                .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
                .where(WatchlistItem.watchlist_id == user_wl.id)
            )
            .scalars()
            .all()
        )
        assert "ENKAI" in items
