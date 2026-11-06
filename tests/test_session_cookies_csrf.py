"""Tests for session cookies (HttpOnly, Secure, SameSite), CSRF protection, and lifetime policy."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.session import SessionSecurityPolicy, session_security_policy
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


class TestSessionSecurityPolicy:
    """Unit tests for session mechanics, cookies, and CSRF policy."""

    @pytest.fixture
    def db_session(self, setup_db: None) -> Generator[DBSession, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    @pytest.fixture
    def user(self, db_session: DBSession) -> User:
        user = db_session.query(User).filter(User.email == "policy_test@example.com").first()
        if not user:
            user = User(
                email="policy_test@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        return user

    def test_create_and_resolve_session(self, db_session: DBSession, user: User) -> None:
        policy = SessionSecurityPolicy(ttl_days=14)
        raw_token, csrf_token, record = policy.create_session(db_session, user.id)

        assert raw_token.startswith("sess_")
        assert len(csrf_token) > 20
        assert record.user_id == user.id
        exp = record.expires_at
        record_expires = exp.replace(tzinfo=UTC) if exp.tzinfo is None else exp
        assert record_expires > datetime.now(UTC)

        # Resolve valid session
        resolved = policy.resolve_session(db_session, raw_token)
        assert resolved is not None
        assert resolved.id == user.id

    def test_expired_session_fails(self, db_session: DBSession, user: User) -> None:
        policy = SessionSecurityPolicy(ttl_days=14)
        raw_token, _, record = policy.create_session(db_session, user.id)

        # Force expire in DB
        record.expires_at = datetime.now(UTC) - timedelta(days=1)
        db_session.commit()

        resolved = policy.resolve_session(db_session, raw_token)
        assert resolved is None

    def test_terminate_session(self, db_session: DBSession, user: User) -> None:
        policy = SessionSecurityPolicy()
        raw_token, _, _ = policy.create_session(db_session, user.id)

        assert policy.terminate_session(db_session, raw_token) is True
        assert policy.resolve_session(db_session, raw_token) is None
        assert policy.terminate_session(db_session, raw_token) is False

    def test_cookie_attributes(self) -> None:
        policy = SessionSecurityPolicy(secure_cookie=True, samesite="lax")
        resp = Response()
        policy.set_session_cookies(resp, "raw_token_xyz", "csrf_token_xyz")

        # Inspect headers for cookie flags
        cookie_headers = resp.headers.getlist("set-cookie")
        assert len(cookie_headers) == 2

        session_cookie = next(c for c in cookie_headers if "borai_session" in c)
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=lax" in session_cookie

        csrf_cookie = next(c for c in cookie_headers if "borai_csrf" in c)
        assert "HttpOnly" not in csrf_cookie  # Must be readable by client JS
        assert "Secure" in csrf_cookie
        assert "SameSite=lax" in csrf_cookie


class TestSessionAndCSRFAPI:
    """Integration tests for HttpOnly session cookie auth and Double-Submit CSRF."""

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
        reset_all_rate_limiters()
        app = create_app()

        def override_get_db() -> Generator[DBSession, None, None]:
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    @pytest.fixture
    def test_user(self, db_session: DBSession) -> User:
        user = db_session.query(User).filter(User.email == "session_api@example.com").first()
        if not user:
            user = User(
                email="session_api@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        return user

    def test_verify_endpoint_sets_session_cookies(self, client: TestClient) -> None:
        res = client.post("/api/v1/auth/verify", json={"token": "test_magic_token_sample"})
        assert res.status_code == 200
        assert "borai_session" in res.cookies
        assert "borai_csrf" in res.cookies

    def test_cookie_auth_read_safe_endpoint(
        self, client: TestClient, db_session: DBSession, test_user: User
    ) -> None:
        raw_token, csrf_token, _ = session_security_policy.create_session(db_session, test_user.id)
        client.cookies.set("borai_session", raw_token)

        # GET request succeeds without needing CSRF header
        res = client.get("/api/v1/user/watchlist")
        assert res.status_code == 200
        assert "watchlist" in res.json()

    def test_cookie_auth_mutation_requires_valid_csrf(
        self, client: TestClient, db_session: DBSession, test_user: User
    ) -> None:
        raw_token, csrf_token, _ = session_security_policy.create_session(db_session, test_user.id)
        client.cookies.set("borai_session", raw_token)
        client.cookies.set("borai_csrf", csrf_token)

        # 1. State-changing POST without CSRF header fails with 403
        res_missing = client.post("/api/v1/user/watchlist/THYAO")
        assert res_missing.status_code == 403
        assert res_missing.json()["error"]["code"] == "CSRF_MISSING"

        # 2. State-changing POST with wrong CSRF header fails with 403
        res_invalid = client.post(
            "/api/v1/user/watchlist/THYAO",
            headers={"x-csrf-token": "wrong_token_value"},
        )
        assert res_invalid.status_code == 403
        assert res_invalid.json()["error"]["code"] == "CSRF_INVALID"

        # 3. State-changing POST with matching CSRF header succeeds
        res_valid = client.post(
            "/api/v1/user/watchlist/THYAO",
            headers={"x-csrf-token": csrf_token},
        )
        assert res_valid.status_code == 201
        assert res_valid.json()["symbol"] == "THYAO"

    def test_csrf_endpoint(self, client: TestClient) -> None:
        res = client.get("/api/v1/auth/csrf")
        assert res.status_code == 200
        token = res.json()["csrf_token"]
        assert len(token) > 20
        assert client.cookies.get("borai_csrf") == token

    def test_session_logout(
        self, client: TestClient, db_session: DBSession, test_user: User
    ) -> None:
        raw_token, csrf_token, _ = session_security_policy.create_session(db_session, test_user.id)
        client.cookies.set("borai_session", raw_token)
        client.cookies.set("borai_csrf", csrf_token)

        # Logout
        res_logout = client.post("/api/v1/auth/session/logout")
        assert res_logout.status_code == 200
        assert "Oturumunuz başarıyla kapatıldı" in res_logout.json()["message"]

        # Session in DB is removed
        assert (
            db_session.query(UserSession)
            .filter(UserSession.session_token_hash == session_security_policy.hash_token(raw_token))
            .first()
            is None
        )
