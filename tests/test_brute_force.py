"""Tests for brute force and credential stuffing defense mechanisms and /auth/login endpoint."""

from collections.abc import Generator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.brute_force import BruteForceDefender, brute_force_defender
from stock_analysis.auth.hasher import PasswordHasher
from stock_analysis.auth.totp import totp_manager
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


class TestBruteForceDefenderMechanics:
    """Unit tests for BruteForceDefender algorithms."""

    def test_account_lockout_after_threshold(self) -> None:
        defender = BruteForceDefender(max_account_failures=3, lockout_duration_seconds=300)
        email = "victim@example.com"
        ip = "192.168.1.100"

        # 1st and 2nd failures
        defender.record_failure(ip, email)
        defender.record_failure(ip, email)
        assert defender.check_lockout(ip, email).is_locked is False

        # 3rd failure triggers account lockout
        defender.record_failure(ip, email)
        status_info = defender.check_lockout(ip, email)
        assert status_info.is_locked is True
        assert status_info.reason == "ACCOUNT_LOCKED"
        assert status_info.retry_after > 0

        # enforce_limits raises 429
        with pytest.raises(HTTPException) as exc_info:
            defender.enforce_limits(ip, email)
        assert exc_info.value.status_code == 429
        assert isinstance(exc_info.value.detail, dict)
        assert exc_info.value.detail["code"] == "ACCOUNT_TEMPORARILY_LOCKED"

    def test_record_success_clears_account_history(self) -> None:
        defender = BruteForceDefender(max_account_failures=3)
        email = "user@example.com"
        ip = "192.168.1.100"

        defender.record_failure(ip, email)
        defender.record_failure(ip, email)
        assert len(defender._failures[f"acc:{email}"]) == 2

        defender.record_success(ip, email)
        assert f"acc:{email}" not in defender._failures
        assert defender.check_lockout(ip, email).is_locked is False

    def test_ip_level_credential_stuffing_spray_lockout(self) -> None:
        defender = BruteForceDefender(max_account_failures=5, max_ip_failures=4)
        ip = "10.0.0.99"

        # Attacker tries 4 different accounts from same IP (1 attempt each)
        for i in range(4):
            defender.record_failure(ip, f"account_{i}@example.com")

        # Now any attempt from that IP is throttled
        status_info = defender.check_lockout(ip, "yet_another_user@example.com")
        assert status_info.is_locked is True
        assert status_info.reason == "IP_LOCKED"

        with pytest.raises(HTTPException) as exc_info:
            defender.enforce_limits(ip, "yet_another_user@example.com")
        assert exc_info.value.status_code == 429
        assert isinstance(exc_info.value.detail, dict)
        assert exc_info.value.detail["code"] == "IP_SPRAY_THROTTLED"

    def test_mask_email(self) -> None:
        assert BruteForceDefender.mask_email("alice@example.com") == "a***@example.com"
        assert BruteForceDefender.mask_email("x@test.com") == "x***@test.com"
        assert BruteForceDefender.mask_email("invalid") == "***"


@pytest.fixture(scope="module")
def setup_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


class TestLoginAndBruteForceAPI:
    """Integration tests for POST /api/v1/auth/login and anti-stuffing enforcement."""

    @pytest.fixture(autouse=True)
    def clean_defender(self) -> None:
        brute_force_defender.reset()
        reset_all_rate_limiters()

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
    def registered_user(self, db_session: DBSession) -> User:
        hasher = PasswordHasher()
        user = db_session.query(User).filter(User.email == "secure_login@example.com").first()
        if not user:
            user = User(
                email="secure_login@example.com",
                hashed_password=hasher.hash("Str0ng!P@ssword2026"),
                is_active=True,
                is_verified=True,
                role="user",
                mfa_enabled=False,
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        else:
            user.hashed_password = hasher.hash("Str0ng!P@ssword2026")
            user.mfa_enabled = False
            user.mfa_secret = None
            db_session.commit()
            db_session.refresh(user)
        return user

    def test_successful_login(self, client: TestClient, registered_user: User) -> None:
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "Str0ng!P@ssword2026",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["requires_mfa"] is False
        assert "access_token" in data
        assert "borai_session" in res.cookies
        assert "borai_csrf" in res.cookies

    def test_invalid_password_returns_401(self, client: TestClient, registered_user: User) -> None:
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "WrongPassword!2026",
            },
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_nonexistent_user_returns_401_no_enumeration(self, client: TestClient) -> None:
        res = client.post(
            "/api/v1/auth/login",
            json={
                "email": "nobody_exists_here_at_all@example.com",
                "password": "WrongPassword!2026",
            },
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_account_lockout_after_max_failed_attempts(
        self, client: TestClient, registered_user: User
    ) -> None:
        # Submit MAX_ACCOUNT_FAILURES (5) failed attempts
        for _ in range(brute_force_defender.max_account_failures):
            reset_all_rate_limiters()
            res = client.post(
                "/api/v1/auth/login",
                json={
                    "email": registered_user.email,
                    "password": "WrongPassword!2026",
                },
            )
            assert res.status_code == 401

        # Next attempt is blocked with 429 even with correct password
        reset_all_rate_limiters()
        blocked_res = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "Str0ng!P@ssword2026",
            },
        )
        assert blocked_res.status_code == 429
        assert blocked_res.json()["error"]["code"] == "ACCOUNT_TEMPORARILY_LOCKED"
        assert "Retry-After" in blocked_res.headers

    def test_mfa_login_challenge_flow(
        self, client: TestClient, registered_user: User, db_session: DBSession
    ) -> None:
        # Enable MFA on registered user
        secret = totp_manager.generate_secret()
        registered_user.mfa_enabled = True
        registered_user.mfa_secret = secret
        db_session.commit()

        # Step 1: Login without MFA code prompts for MFA
        res_step1 = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "Str0ng!P@ssword2026",
            },
        )
        assert res_step1.status_code == 200
        assert res_step1.json()["requires_mfa"] is True
        assert res_step1.json()["access_token"] is None

        # Step 2: Login with wrong MFA code -> 401
        reset_all_rate_limiters()
        res_wrong_mfa = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "Str0ng!P@ssword2026",
                "mfa_code": "000000",
            },
        )
        assert res_wrong_mfa.status_code == 401
        assert res_wrong_mfa.json()["error"]["code"] == "INVALID_MFA_CODE"

        # Step 3: Login with valid TOTP code -> 200 with access token
        reset_all_rate_limiters()
        code_now = totp_manager.generate_code(secret)
        res_valid_mfa = client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user.email,
                "password": "Str0ng!P@ssword2026",
                "mfa_code": code_now,
            },
        )
        assert res_valid_mfa.status_code == 200
        assert res_valid_mfa.json()["requires_mfa"] is False
        assert res_valid_mfa.json()["access_token"] is not None
