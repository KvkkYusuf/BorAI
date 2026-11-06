"""Tests for email verification flow, security policies, token management, and API endpoints."""

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
from stock_analysis.auth.email_verification import (
    EmailVerificationError,
    EmailVerificationManager,
    EmailVerificationTokenPayload,
    email_verification_manager,
)
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


class TestEmailVerificationManager:
    """Unit tests for EmailVerificationManager cryptographic mechanics."""

    def test_create_and_verify_token(self) -> None:
        manager = EmailVerificationManager(ttl_hours=24)
        raw_token, payload = manager.create_verification_token(user_id=1, email="test@example.com")

        assert isinstance(raw_token, str)
        assert len(raw_token) > 20
        assert payload.user_id == 1
        assert payload.email == "test@example.com"
        assert payload.token_hash == manager.hash_token(raw_token)
        assert not payload.is_expired()

        # Consume token
        consumed = manager.verify_and_consume(raw_token)
        assert consumed.user_id == 1
        assert consumed.email == "test@example.com"

        # Verify single-use: should fail on second attempt
        with pytest.raises(EmailVerificationError, match="Geçersiz veya daha önce kullanılmış"):
            manager.verify_and_consume(raw_token)

    def test_invalid_token_fails(self) -> None:
        manager = EmailVerificationManager()
        with pytest.raises(EmailVerificationError, match="Geçersiz veya daha önce kullanılmış"):
            manager.verify_and_consume("completely_bogus_token_1234567890")

    def test_expired_token_fails(self) -> None:
        manager = EmailVerificationManager(ttl_hours=1)
        raw_token, payload = manager.create_verification_token(
            user_id=2, email="expired@example.com"
        )

        # Simulate time travel 2 hours into future
        future_time = datetime.now(UTC) + timedelta(hours=2)
        assert payload.is_expired(current_time=future_time)

        # Manually alter payload expiry to simulate expired state
        expired_payload = EmailVerificationTokenPayload(
            user_id=payload.user_id,
            email=payload.email,
            token_hash=payload.token_hash,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        manager._tokens[payload.token_hash] = expired_payload

        with pytest.raises(EmailVerificationError, match="süresi dolmuş"):
            manager.verify_and_consume(raw_token)

    def test_revoke_all_for_user(self) -> None:
        manager = EmailVerificationManager()
        t1, _ = manager.create_verification_token(user_id=5, email="user5@example.com")
        t2, _ = manager.create_verification_token(user_id=5, email="user5@example.com")
        t3, _ = manager.create_verification_token(user_id=6, email="user6@example.com")

        revoked = manager.revoke_all_for_user(5)
        assert revoked == 2

        with pytest.raises(EmailVerificationError):
            manager.verify_and_consume(t1)
        with pytest.raises(EmailVerificationError):
            manager.verify_and_consume(t2)

        # User 6 token should still work
        assert manager.verify_and_consume(t3).user_id == 6

    def test_rate_limiting(self) -> None:
        manager = EmailVerificationManager()
        user_id = 99
        email = "rate_limit@example.com"

        # Allowed up to MAX_REQUESTS_PER_WINDOW (5)
        for _ in range(manager.MAX_REQUESTS_PER_WINDOW):
            manager.create_verification_token(user_id, email)

        # 6th should exceed rate limit
        with pytest.raises(EmailVerificationError, match="Çok fazla doğrulama talebi"):
            manager.create_verification_token(user_id, email)

    def test_mask_email(self) -> None:
        assert EmailVerificationManager.mask_email("alice@example.com") == "a***@example.com"
        assert EmailVerificationManager.mask_email("b@example.com") == "b***@example.com"
        assert EmailVerificationManager.mask_email("invalid_email") == "***"


@pytest.fixture(scope="module")
def setup_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


class TestEmailVerificationAPI:
    """Integration tests for email verification API endpoints."""

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
        email_verification_manager.reset()
        app = create_app()

        def override_get_db() -> Generator[DBSession, None, None]:
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    @pytest.fixture
    def unverified_user(self, db_session: DBSession) -> User:
        user = db_session.query(User).filter(User.email == "unverified_test@example.com").first()
        if not user:
            user = User(
                email="unverified_test@example.com",
                is_active=True,
                is_verified=False,
                role="user",
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        else:
            user.is_verified = False
            db_session.commit()
            db_session.refresh(user)
        return user

    def test_request_verification_unverified_user(
        self, client: TestClient, unverified_user: User
    ) -> None:
        response = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": unverified_user.email},
        )
        assert response.status_code == 200
        data = response.json()
        assert "Doğrulama bağlantısı e-posta adresinize gönderildi" in data["message"]

    def test_request_verification_enumeration_protection(self, client: TestClient) -> None:
        # Non-existent email should return 200 with identical message
        response = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": "nobody_exists_here_987654321@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "Doğrulama bağlantısı e-posta adresinize gönderildi" in data["message"]

    def test_confirm_verification_success(
        self, client: TestClient, unverified_user: User, db_session: DBSession
    ) -> None:
        raw_token, _ = email_verification_manager.create_verification_token(
            user_id=unverified_user.id,
            email=unverified_user.email,
        )

        response = client.post(
            "/api/v1/auth/verify-email/confirm",
            json={"token": raw_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_verified"] is True
        assert data["email"] == unverified_user.email

        # Verify in DB
        db_session.expire_all()
        refreshed = db_session.query(User).filter(User.id == unverified_user.id).first()
        assert refreshed is not None
        assert refreshed.is_verified is True

    def test_confirm_verification_invalid_token(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/verify-email/confirm",
            json={"token": "invalid_test_verification_token_sample"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"
