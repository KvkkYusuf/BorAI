"""Tests for password reset flow, security policies, token management, and API endpoints."""

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
from stock_analysis.auth.hasher import PasswordHasher
from stock_analysis.auth.reset_flow import (
    PasswordResetManager,
    ResetFlowError,
    ResetTokenPayload,
    password_reset_manager,
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


class TestPasswordResetManager:
    """Unit tests for PasswordResetManager mechanics."""

    def test_create_and_verify_token(self) -> None:
        manager = PasswordResetManager(ttl_minutes=15)
        raw_token, payload = manager.create_reset_token(user_id=1, email="user@example.com")

        assert isinstance(raw_token, str)
        assert len(raw_token) > 20
        assert payload.user_id == 1
        assert payload.email == "user@example.com"
        assert payload.token_hash == manager.hash_token(raw_token)
        assert not payload.is_expired()

        # Consume token
        consumed = manager.verify_and_consume(raw_token)
        assert consumed.user_id == 1
        assert consumed.email == "user@example.com"

        # Verify single-use: should fail on second attempt
        with pytest.raises(ResetFlowError, match="Geçersiz veya daha önce kullanılmış"):
            manager.verify_and_consume(raw_token)

    def test_invalid_token_fails(self) -> None:
        manager = PasswordResetManager()
        with pytest.raises(ResetFlowError, match="Geçersiz veya daha önce kullanılmış"):
            manager.verify_and_consume("invalid_reset_token_xyz_123456789")

    def test_expired_token_fails(self) -> None:
        manager = PasswordResetManager(ttl_minutes=15)
        raw_token, payload = manager.create_reset_token(user_id=2, email="expired@example.com")

        # Simulate time travel into future
        future_time = datetime.now(UTC) + timedelta(minutes=20)
        assert payload.is_expired(current_time=future_time)

        # Manually alter payload expiry to simulate expired state
        expired_payload = ResetTokenPayload(
            user_id=payload.user_id,
            email=payload.email,
            token_hash=payload.token_hash,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        manager._tokens[payload.token_hash] = expired_payload

        with pytest.raises(ResetFlowError, match="süresi dolmuş"):
            manager.verify_and_consume(raw_token)

    def test_revoke_all_for_user(self) -> None:
        manager = PasswordResetManager()
        t1, _ = manager.create_reset_token(user_id=10, email="user10@example.com")
        t2, _ = manager.create_reset_token(user_id=10, email="user10@example.com")
        t3, _ = manager.create_reset_token(user_id=20, email="user20@example.com")

        revoked = manager.revoke_all_for_user(10)
        assert revoked == 2

        with pytest.raises(ResetFlowError):
            manager.verify_and_consume(t1)
        with pytest.raises(ResetFlowError):
            manager.verify_and_consume(t2)

        # User 20 token remains valid
        assert manager.verify_and_consume(t3).user_id == 20

    def test_rate_limiting(self) -> None:
        manager = PasswordResetManager()
        user_id = 42
        email = "rate_limit@example.com"

        for _ in range(manager.MAX_REQUESTS_PER_WINDOW):
            manager.create_reset_token(user_id, email)

        with pytest.raises(ResetFlowError, match="Çok fazla sıfırlama talebi"):
            manager.create_reset_token(user_id, email)

    def test_mask_email(self) -> None:
        assert PasswordResetManager.mask_email("bob@example.com") == "b***@example.com"
        assert PasswordResetManager.mask_email("a@example.com") == "a***@example.com"
        assert PasswordResetManager.mask_email("invalid") == "***"


@pytest.fixture(scope="module")
def setup_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


class TestPasswordResetAPI:
    """Integration tests for password reset API endpoints."""

    @pytest.fixture
    def db_session(self, setup_db: None) -> Generator[DBSession, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    @pytest.fixture(autouse=True)
    def clean_manager_state(self) -> None:
        password_reset_manager._tokens.clear()
        password_reset_manager._request_history.clear()

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
    def active_user(self, db_session: DBSession) -> User:
        user = db_session.query(User).filter(User.email == "reset_user@example.com").first()
        if not user:
            user = User(
                email="reset_user@example.com",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        return user

    def test_request_reset_existing_user(self, client: TestClient, active_user: User) -> None:
        response = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": active_user.email},
        )
        assert response.status_code == 200
        data = response.json()
        assert "Sıfırlama bağlantısı e-posta adresinize gönderildi" in data["message"]

    def test_request_reset_nonexistent_user_no_enumeration(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "nonexistent_target_user@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "Sıfırlama bağlantısı e-posta adresinize gönderildi" in data["message"]

    def test_confirm_password_reset_success(
        self, client: TestClient, active_user: User, db_session: DBSession
    ) -> None:
        raw_token, _ = password_reset_manager.create_reset_token(
            user_id=active_user.id,
            email=active_user.email,
        )

        new_pass = "P@ssw0rdSecure!2026"
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": new_pass,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == active_user.email
        assert "Parolanız başarıyla güncellendi" in data["message"]

        # Verify DB updated and hash verifies
        db_session.expire_all()
        refreshed = db_session.query(User).filter(User.id == active_user.id).first()
        assert refreshed is not None
        assert refreshed.hashed_password is not None
        hasher = PasswordHasher()
        assert hasher.verify(new_pass, refreshed.hashed_password)

    def test_confirm_password_reset_weak_password_fails(
        self, client: TestClient, active_user: User
    ) -> None:
        raw_token, _ = password_reset_manager.create_reset_token(
            user_id=active_user.id,
            email=active_user.email,
        )

        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "weak",  # too short
            },
        )
        assert response.status_code in (400, 422)

    def test_confirm_password_reset_invalid_token(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": "completely_invalid_token_1234567890",
                "new_password": "ValidPassword!2026",
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    def test_confirm_password_reset_token_single_use(
        self, client: TestClient, active_user: User
    ) -> None:
        raw_token, _ = password_reset_manager.create_reset_token(
            user_id=active_user.id,
            email=active_user.email,
        )

        # First use succeeds
        res1 = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "FirstValidPass!2026",
            },
        )
        assert res1.status_code == 200

        # Second use with same token fails
        res2 = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "SecondValidPass!2026",
            },
        )
        assert res2.status_code == 400
        assert res2.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"
