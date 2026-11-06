"""Tests for active user session management, IDOR prevention, and session termination."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.session import session_security_policy
from stock_analysis.db.base import Base
from stock_analysis.db.models import Session as UserSession
from stock_analysis.db.models import User
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


class TestUserSessionManagementAPI:
    """Integration tests for user session listing, IDOR defense, and termination."""

    @pytest.fixture(autouse=True)
    def clean_limiters_and_sessions(self, db_session: DBSession) -> None:
        reset_all_rate_limiters()
        db_session.query(UserSession).delete()
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
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    @pytest.fixture
    def primary_user(self, db_session: DBSession) -> User:
        user = db_session.query(User).filter(User.email == "session_owner@example.com").first()
        if not user:
            user = User(
                email="session_owner@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        return user

    @pytest.fixture
    def other_user(self, db_session: DBSession) -> User:
        user = db_session.query(User).filter(User.email == "other_owner@example.com").first()
        if not user:
            user = User(
                email="other_owner@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        return user

    def test_list_sessions_marks_current_and_filters_others(
        self,
        client: TestClient,
        db_session: DBSession,
        primary_user: User,
        other_user: User,
    ) -> None:
        # Create 2 sessions for primary user (one current, one other device)
        t_current, _, rec_current = session_security_policy.create_session(
            db_session, primary_user.id, user_agent="Mozilla/Current"
        )
        t_other, _, rec_other = session_security_policy.create_session(
            db_session, primary_user.id, user_agent="Mobile/iPhone"
        )

        # Create 1 expired session for primary user
        _, _, rec_expired = session_security_policy.create_session(
            db_session, primary_user.id, user_agent="Expired/Browser"
        )
        rec_expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        db_session.commit()

        # Create 1 session for completely different user
        _, _, rec_victim = session_security_policy.create_session(
            db_session, other_user.id, user_agent="OtherUser/Device"
        )

        # Send request with primary user's t_current
        res = client.get(
            "/api/v1/user/sessions",
            headers={"Authorization": f"Bearer {t_current}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 2  # exactly 2 active sessions for primary user

        session_ids = [s["id"] for s in data["sessions"]]
        assert rec_current.id in session_ids
        assert rec_other.id in session_ids
        assert rec_expired.id not in session_ids
        assert rec_victim.id not in session_ids

        current_item = next(s for s in data["sessions"] if s["id"] == rec_current.id)
        assert current_item["is_current"] is True
        assert current_item["user_agent"] == "Mozilla/Current"

        other_item = next(s for s in data["sessions"] if s["id"] == rec_other.id)
        assert other_item["is_current"] is False

    def test_terminate_other_sessions(
        self,
        client: TestClient,
        db_session: DBSession,
        primary_user: User,
    ) -> None:
        t_current, _, rec_current = session_security_policy.create_session(
            db_session, primary_user.id
        )
        t_device1, _, rec_device1 = session_security_policy.create_session(
            db_session, primary_user.id
        )
        t_device2, _, rec_device2 = session_security_policy.create_session(
            db_session, primary_user.id
        )

        res = client.delete(
            "/api/v1/user/sessions/other",
            headers={"Authorization": f"Bearer {t_current}"},
        )
        assert res.status_code == 200
        assert res.json()["terminated_count"] == 2

        # Current session still exists
        db_session.expire_all()
        assert db_session.get(UserSession, rec_current.id) is not None
        # Other sessions deleted
        assert db_session.get(UserSession, rec_device1.id) is None
        assert db_session.get(UserSession, rec_device2.id) is None

    def test_terminate_specific_session_idor_prevention(
        self,
        client: TestClient,
        db_session: DBSession,
        primary_user: User,
        other_user: User,
    ) -> None:
        t_primary, _, rec_primary = session_security_policy.create_session(
            db_session, primary_user.id
        )
        t_victim, _, rec_victim = session_security_policy.create_session(db_session, other_user.id)

        # Primary user tries to terminate victim user's session -> 404 IDOR protected
        res_idor = client.delete(
            f"/api/v1/user/sessions/{rec_victim.id}",
            headers={"Authorization": f"Bearer {t_primary}"},
        )
        assert res_idor.status_code == 404
        assert res_idor.json()["error"]["code"] == "SESSION_NOT_FOUND"

        # Victim session was NOT deleted
        db_session.expire_all()
        assert db_session.get(UserSession, rec_victim.id) is not None

        # Primary user successfully terminates their own session
        res_valid = client.delete(
            f"/api/v1/user/sessions/{rec_primary.id}",
            headers={"Authorization": f"Bearer {t_primary}"},
        )
        assert res_valid.status_code == 200
        assert res_valid.json()["terminated_count"] == 1

        db_session.expire_all()
        assert db_session.get(UserSession, rec_primary.id) is None

    def test_terminate_all_sessions(
        self,
        client: TestClient,
        db_session: DBSession,
        primary_user: User,
    ) -> None:
        t1, _, r1 = session_security_policy.create_session(db_session, primary_user.id)
        t2, _, r2 = session_security_policy.create_session(db_session, primary_user.id)

        res = client.delete(
            "/api/v1/user/sessions",
            headers={"Authorization": f"Bearer {t1}"},
        )
        assert res.status_code == 200
        assert res.json()["terminated_count"] >= 2

        db_session.expire_all()
        assert db_session.get(UserSession, r1.id) is None
        assert db_session.get(UserSession, r2.id) is None
