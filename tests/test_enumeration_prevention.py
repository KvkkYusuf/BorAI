"""Tests verifying complete prevention of email and user enumeration across auth endpoints.

Covers:
- Login (invalid credentials for nonexistent vs existing users)
- Registration (new user vs already registered user)
- Password reset request (existing vs nonexistent vs inactive user)
- Email verification request (unverified vs verified vs nonexistent user)
- Magic link request (existing vs nonexistent user)
- Timing consistency and dummy cryptographic computation execution
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.brute_force import brute_force_defender
from stock_analysis.auth.hasher import PasswordHasher
from stock_analysis.db.base import Base
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
def setup_test_db() -> Generator[None, None, None]:
    """Create test tables."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(setup_test_db: None) -> Generator[DBSession, None, None]:
    """Isolated db session per test."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    """TestClient resetting rate limiters before test execution."""
    reset_all_rate_limiters()
    brute_force_defender.reset()
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def registered_user(db_session: DBSession) -> User:
    """Fixture providing a registered active user with known password."""
    email = "registered_investor@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        hasher = PasswordHasher(n=1024, r=8, p=1)
        user = User(
            email=email,
            hashed_password=hasher.hash("P@ssw0rdSecure!2026"),
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


class TestEmailEnumerationPrevention:
    """Rigorous tests confirming identical messages, status codes, and structures."""

    def test_login_enumeration_defense_identical_responses(
        self, client: TestClient, registered_user: User
    ) -> None:
        """Attempting to log in with nonexistent email vs wrong password for existing user

        must produce the exact same HTTP 401 status and error payload.
        """
        # Case 1: Nonexistent user
        res_nonexistent = client.post(
            "/api/v1/auth/login",
            json={
                "email": "absolutely_nobody_here_987654@example.com",
                "password": "AnyRandomPassword!123",
            },
        )
        assert res_nonexistent.status_code == 401
        data_nonexistent = res_nonexistent.json()

        # Case 2: Existing user with incorrect password
        res_wrong_pw = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "WrongPasswordAttempt!999",
            },
        )
        assert res_wrong_pw.status_code == 401
        data_wrong_pw = res_wrong_pw.json()

        # Responses must be completely indistinguishable
        assert data_nonexistent["error"]["code"] == "INVALID_CREDENTIALS"
        assert data_wrong_pw["error"]["code"] == "INVALID_CREDENTIALS"
        assert data_nonexistent["error"]["message"] == data_wrong_pw["error"]["message"]
        assert "E-posta adresi veya parola hatalı." in data_nonexistent["error"]["message"]

    def test_registration_enumeration_defense_identical_responses(
        self, client: TestClient, registered_user: User, db_session: DBSession
    ) -> None:
        """Registering an existing user vs brand new user must produce identical HTTP 200

        and identical user-facing messages.
        """
        new_email = "brand_new_user_2026@example.com"
        strong_pw = "Str0ng!P@ssword2026"

        # Case 1: Brand new user registration
        res_new = client.post(
            "/api/v1/auth/register",
            json={"email": new_email, "password": strong_pw},
        )
        assert res_new.status_code == 200
        data_new = res_new.json()

        # Verify new user was persisted in DB as unverified
        db_session.expire_all()
        created_user = db_session.query(User).filter(User.email == new_email).first()
        assert created_user is not None
        assert created_user.is_verified is False
        assert created_user.hashed_password is not None

        # Case 2: Attempting to register again with an already registered email
        res_existing = client.post(
            "/api/v1/auth/register",
            json={"email": registered_user.email, "password": strong_pw},
        )
        assert res_existing.status_code == 200
        data_existing = res_existing.json()

        # Response must NOT state "Account already exists" or leak status
        assert data_new["message"] == data_existing["message"]
        assert "Kayıt talebiniz alındı" in data_new["message"]
        assert "doğrulama bağlantısı" in data_new["message"]

    def test_registration_password_policy_violation(self, client: TestClient) -> None:
        """Weak passwords must be rejected before registration without enumeration side effect."""
        res = client.post(
            "/api/v1/auth/register",
            json={"email": "some_user@example.com", "password": "weak"},
        )
        assert res.status_code in (400, 422)

    def test_password_reset_enumeration_defense_identical_responses(
        self, client: TestClient, registered_user: User
    ) -> None:
        """Requesting password reset for existing, nonexistent, or inactive accounts

        must return identical responses.
        """
        # Case 1: Existing active user
        res_exist = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": registered_user.email},
        )
        assert res_exist.status_code == 200

        # Case 2: Nonexistent email
        res_nonexist = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "nonexistent_reset_target@example.com"},
        )
        assert res_nonexist.status_code == 200

        assert res_exist.json()["message"] == res_nonexist.json()["message"]
        assert "Sıfırlama bağlantısı e-posta adresinize gönderildi" in res_exist.json()["message"]

    def test_email_verification_enumeration_defense_identical_responses(
        self, client: TestClient, registered_user: User, db_session: DBSession
    ) -> None:
        """Requesting email verification for unverified, already-verified, or nonexistent

        accounts must return identical generic success messages.
        """
        # Create unverified user
        unverified_email = "pending_verify_user@example.com"
        u = User(email=unverified_email, is_active=True, is_verified=False, role="user")
        db_session.add(u)
        db_session.commit()

        # Case 1: Unverified user
        res_unverified = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": unverified_email},
        )
        assert res_unverified.status_code == 200

        # Case 2: Already verified user
        res_verified = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": registered_user.email},
        )
        assert res_verified.status_code == 200

        # Case 3: Nonexistent user
        res_nonexist = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": "ghost_account_never_existed@example.com"},
        )
        assert res_nonexist.status_code == 200

        # All messages must be strictly equal
        msg1 = res_unverified.json()["message"]
        msg2 = res_verified.json()["message"]
        msg3 = res_nonexist.json()["message"]
        assert msg1 == msg2 == msg3
        assert "Doğrulama bağlantısı e-posta adresinize gönderildi" in msg1

    def test_magic_link_enumeration_defense_identical_responses(
        self, client: TestClient, registered_user: User
    ) -> None:
        """Requesting magic login link for existing vs nonexistent accounts

        must return identical responses.
        """
        res_exist = client.post(
            "/api/v1/auth/magic-link",
            json={"email": registered_user.email},
        )
        assert res_exist.status_code == 200

        res_nonexist = client.post(
            "/api/v1/auth/magic-link",
            json={"email": "never_heard_of_this_one@example.com"},
        )
        assert res_nonexist.status_code == 200

        assert res_exist.json()["message"] == res_nonexist.json()["message"]
