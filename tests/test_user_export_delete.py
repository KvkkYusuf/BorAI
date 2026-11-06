"""Tests for KVKK data export and account deletion flows with statutory audit trail retention."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.session import session_security_policy
from stock_analysis.db.base import Base
from stock_analysis.db.models import (
    AlertRule,
    AuditEvent,
    Instrument,
    User,
    UserPreference,
    Watchlist,
    WatchlistItem,
)
from stock_analysis.db.models import (
    Session as UserSession,
)
from stock_analysis.db.session import get_db

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def setup_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


class TestUserExportAndDeleteFlow:
    """Test suite for GET /user/export and DELETE /user/account & /user/me."""

    @pytest.fixture(autouse=True)
    def clean_data(self, db_session: DBSession) -> None:
        reset_all_rate_limiters()
        db_session.query(AuditEvent).delete()
        db_session.query(WatchlistItem).delete()
        db_session.query(Watchlist).delete()
        db_session.query(AlertRule).delete()
        db_session.query(UserPreference).delete()
        db_session.query(UserSession).delete()
        db_session.query(User).delete()
        db_session.commit()

    @pytest.fixture
    def db_session(self, setup_db: None) -> Generator[DBSession, None, None]:
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
        yield TestClient(app)
        app.dependency_overrides.clear()

    def _create_authenticated_user(
        self,
        db_session: DBSession,
        email: str = "kvkk_user@example.com",
    ) -> tuple[User, str, str]:
        user = User(
            email=email,
            hashed_password="dummy_hashed_pw",
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.flush()

        # Add preferences
        pref = UserPreference(
            user_id=user.id,
            email_notifications=True,
            timezone="Europe/Istanbul",
            theme="dark",
            language="tr",
            alert_frequency="immediate",
            weekly_digest=True,
        )
        db_session.add(pref)

        # Add instrument and watchlist
        inst = db_session.execute(
            select(Instrument).where(Instrument.symbol == "THYAO")
        ).scalar_one_or_none()
        if inst is None:
            inst = Instrument(
                symbol="THYAO",
                name="Turk Hava Yollari",
                market="BIST",
                currency="TRY",
            )
            db_session.add(inst)
            db_session.flush()

        wl = Watchlist(user_id=user.id, name="Varsayilan Takip")
        db_session.add(wl)
        db_session.flush()

        item = WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id)
        db_session.add(item)

        # Add alert rule
        alert = AlertRule(
            user_id=user.id,
            instrument_id=inst.id,
            condition_type="price_above",
            threshold=320.0,
            is_active=True,
        )
        db_session.add(alert)

        # Create session using session_security_policy
        raw_token, csrf_token, user_session = session_security_policy.create_session(
            db_session,
            user_id=user.id,
            user_agent="pytest-client",
        )
        return user, raw_token, csrf_token

    def test_export_user_data_success_and_audit(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """KVKK data export returns all user entities and logs compliance audit trail."""
        user, raw_token, csrf_token = self._create_authenticated_user(db_session)
        client.cookies.set(session_security_policy.SESSION_COOKIE_NAME, raw_token)
        client.cookies.set(session_security_policy.CSRF_COOKIE_NAME, csrf_token)
        headers = {session_security_policy.CSRF_HEADER_NAME: csrf_token}

        response = client.get("/api/v1/user/export", headers=headers)
        assert response.status_code == 200
        payload = response.json()

        assert payload["user_id"] == user.id
        assert payload["email"] == user.email
        assert "THYAO" in payload["watchlists"]
        assert len(payload["alerts"]) == 1
        assert payload["alerts"][0]["symbol"] == "THYAO"
        assert payload["preferences"]["theme"] == "dark"
        assert payload["preferences"]["timezone"] == "Europe/Istanbul"
        assert "disclaimer" in payload

        # Check audit event was created
        audit_records = (
            db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "DATA_EXPORTED",
                    AuditEvent.user_id == user.id,
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_records) == 1
        assert "KVKK taşınabilirlik" in (audit_records[0].details or "")

    def test_delete_account_clears_cookies_and_preserves_retention(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """Account deletion purges personal data, clears cookies, and retains audit logs."""
        user, raw_token, csrf_token = self._create_authenticated_user(db_session)
        client.cookies.set(session_security_policy.SESSION_COOKIE_NAME, raw_token)
        client.cookies.set(session_security_policy.CSRF_COOKIE_NAME, csrf_token)
        headers = {session_security_policy.CSRF_HEADER_NAME: csrf_token}

        user_id = user.id
        response = client.delete("/api/v1/user/account", headers=headers)
        assert response.status_code == 200
        assert "kalici olarak silindi" in response.json()["message"]

        # Verify session cookies were instructed to clear (empty value or Max-Age=0)
        set_cookie_headers = response.headers.get("set-cookie", "")
        assert f"{session_security_policy.SESSION_COOKIE_NAME}=" in set_cookie_headers

        # Verify user record is completely removed from DB
        deleted_user = db_session.get(User, user_id)
        assert deleted_user is None

        # Verify associated sessions, watchlists, alerts, and preferences are wiped (cascade)
        sessions_left = (
            db_session.execute(select(UserSession).where(UserSession.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(sessions_left) == 0

        prefs_left = (
            db_session.execute(select(UserPreference).where(UserPreference.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(prefs_left) == 0

        watchlists_left = (
            db_session.execute(select(Watchlist).where(Watchlist.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(watchlists_left) == 0

        alerts_left = (
            db_session.execute(select(AlertRule).where(AlertRule.user_id == user_id))
            .scalars()
            .all()
        )
        assert len(alerts_left) == 0

        # Statutory audit event (5651 & KVKK) must exist with user_id = None
        deletion_audits = (
            db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "ACCOUNT_DELETED",
                    AuditEvent.resource == f"user:{user_id}",
                )
            )
            .scalars()
            .all()
        )
        assert len(deletion_audits) == 1
        assert deletion_audits[0].user_id is None
        assert "KVKK silme talebiyle" in (deletion_audits[0].details or "")

        # Verify subsequent request is 401 Unauthorized
        res_after = client.get("/api/v1/user/preferences", headers=headers)
        assert res_after.status_code == 401

    def test_delete_account_me_alias(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """Endpoint /user/me works as an alias for account deletion."""
        user, raw_token, csrf_token = self._create_authenticated_user(
            db_session, email="me_delete@example.com"
        )
        client.cookies.set(session_security_policy.SESSION_COOKIE_NAME, raw_token)
        client.cookies.set(session_security_policy.CSRF_COOKIE_NAME, csrf_token)
        headers = {session_security_policy.CSRF_HEADER_NAME: csrf_token}

        response = client.delete("/api/v1/user/me", headers=headers)
        assert response.status_code == 200
        assert "kalici olarak silindi" in response.json()["message"]

        # Confirm user is deleted
        assert db_session.get(User, user.id) is None
