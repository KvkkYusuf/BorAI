"""Tests for user account preferences and notification settings."""

from collections.abc import Generator

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
from stock_analysis.db.models import User, UserPreference
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


class TestUserPreferencesAPI:
    """Test suite for GET/PUT /user/preferences, validation, and KVKK export integration."""

    @pytest.fixture(autouse=True)
    def clean_limiters_and_records(self, db_session: DBSession) -> None:
        reset_all_rate_limiters()
        db_session.query(UserPreference).delete()
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
    def user(self, db_session: DBSession) -> User:
        u = db_session.query(User).filter(User.email == "pref_user@example.com").first()
        if not u:
            u = User(
                email="pref_user@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db_session.add(u)
            db_session.commit()
            db_session.refresh(u)
        return u

    @pytest.fixture
    def auth_headers(self, db_session: DBSession, user: User) -> dict[str, str]:
        raw_token, _, _ = session_security_policy.create_session(
            db=db_session,
            user_id=user.id,
        )
        return {"Authorization": f"Bearer {raw_token}"}

    def test_get_default_preferences(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """User accessing preferences for the first time gets correct defaults."""
        response = client.get("/api/v1/user/preferences", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email_notifications"] is True
        assert data["timezone"] == "Europe/Istanbul"
        assert data["theme"] == "system"
        assert data["language"] == "tr"
        assert data["alert_frequency"] == "immediate"
        assert data["weekly_digest"] is False

    def test_update_preferences_valid(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Updating preferences with valid options modifies and persists settings."""
        payload = {
            "email_notifications": False,
            "timezone": "Europe/London",
            "theme": "dark",
            "language": "en",
            "alert_frequency": "daily_digest",
            "weekly_digest": True,
        }
        response = client.put("/api/v1/user/preferences", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email_notifications"] is False
        assert data["timezone"] == "Europe/London"
        assert data["theme"] == "dark"
        assert data["language"] == "en"
        assert data["alert_frequency"] == "daily_digest"
        assert data["weekly_digest"] is True

        # Verify GET returns updated values
        get_res = client.get("/api/v1/user/preferences", headers=auth_headers)
        assert get_res.status_code == 200
        assert get_res.json()["theme"] == "dark"
        assert get_res.json()["weekly_digest"] is True

    def test_update_partial_preferences(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Partial update leaves other existing values intact."""
        # Initial set
        client.put("/api/v1/user/preferences", json={"theme": "light"}, headers=auth_headers)

        # Update only timezone
        res = client.put(
            "/api/v1/user/preferences",
            json={"timezone": "America/New_York"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["timezone"] == "America/New_York"
        assert data["theme"] == "light"

    def test_update_preferences_invalid_timezone(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Invalid IANA timezone name returns 422 error."""
        response = client.put(
            "/api/v1/user/preferences",
            json={"timezone": "Mars/Olympus_Mons"},
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert "Geçersiz saat dilimi" in response.text

    def test_update_preferences_invalid_theme(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Unsupported theme returns 422 error."""
        response = client.put(
            "/api/v1/user/preferences",
            json={"theme": "neon_cyberpunk"},
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert "Geçersiz tema" in response.text

    def test_update_preferences_invalid_language(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Unsupported language code returns 422 error."""
        response = client.put(
            "/api/v1/user/preferences",
            json={"language": "es"},
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert "Geçersiz dil" in response.text

    def test_update_preferences_invalid_alert_frequency(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Unsupported alert frequency returns 422 error."""
        response = client.put(
            "/api/v1/user/preferences",
            json={"alert_frequency": "yearly"},
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert "Geçersiz uyarı sıklığı" in response.text

    def test_preferences_unauthenticated(self, client: TestClient) -> None:
        """Unauthorized request returns 401."""
        response = client.get("/api/v1/user/preferences")
        assert response.status_code == 401

    def test_export_includes_preferences(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """KVKK /user/export returns user preferences bundle."""
        client.put("/api/v1/user/preferences", json={"theme": "dark"}, headers=auth_headers)
        response = client.get("/api/v1/user/export", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "preferences" in data
        assert data["preferences"]["theme"] == "dark"

    def test_cascade_delete_preferences(
        self, client: TestClient, db_session: DBSession, user: User, auth_headers: dict[str, str]
    ) -> None:
        """Deleting the user account cascades and removes preferences row."""
        # Ensure preference exists
        client.get("/api/v1/user/preferences", headers=auth_headers)
        pref = db_session.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        assert pref is not None

        # Hard delete user via API
        del_res = client.delete("/api/v1/user/me", headers=auth_headers)
        assert del_res.status_code == 200

        # Check DB
        pref_after = (
            db_session.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        )
        assert pref_after is None
