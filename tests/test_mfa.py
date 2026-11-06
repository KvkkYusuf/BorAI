"""Tests for RFC 6238 TOTP Multi-Factor Authentication and recovery codes."""

import time
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.totp import TOTPManager, totp_manager
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


class TestTOTPManagerMechanics:
    """Unit tests for RFC 6238 TOTP algorithms and recovery code hashing."""

    def test_secret_generation(self) -> None:
        secret = totp_manager.generate_secret()
        assert isinstance(secret, str)
        assert len(secret) == 32

    def test_code_generation_and_verification(self) -> None:
        manager = TOTPManager()
        secret = manager.generate_secret()

        now = time.time()
        code = manager.generate_code(secret, for_time=now)
        assert len(code) == 6
        assert code.isdigit()

        # Valid at current time
        assert manager.verify_code(secret, code, for_time=now)

        # Valid with clock drift within window (offset 30s)
        assert manager.verify_code(secret, code, window=1, for_time=now + 30)
        assert manager.verify_code(secret, code, window=1, for_time=now - 30)

        # Invalid outside drift window (offset 120s)
        assert not manager.verify_code(secret, code, window=1, for_time=now + 120)

        # Invalid code
        assert not manager.verify_code(secret, "999999" if code != "999999" else "000000")

    def test_provisioning_uri(self) -> None:
        secret = totp_manager.generate_secret()
        uri = totp_manager.generate_provisioning_uri(secret, "trader@borsai.com")
        assert uri.startswith("otpauth://totp/BoraI:trader@borsai.com")
        assert f"secret={secret}" in uri
        assert "issuer=BoraI" in uri
        assert "digits=6" in uri
        assert "period=30" in uri

    def test_recovery_code_hashing_and_consumption(self) -> None:
        manager = TOTPManager()
        codes = manager.generate_recovery_codes(count=4)
        assert len(codes) == 4

        hashed_codes = [manager.hash_recovery_code(c) for c in codes]

        # Use 1st code
        valid, remaining = manager.verify_recovery_code(codes[0], hashed_codes)
        assert valid is True
        assert len(remaining) == 3

        # Attempt to reuse 1st code must fail
        reuse_valid, _ = manager.verify_recovery_code(codes[0], remaining)
        assert reuse_valid is False

        # Bogus code fails
        bogus_valid, _ = manager.verify_recovery_code("BOGUS-CODE", remaining)
        assert bogus_valid is False


@pytest.fixture(scope="module")
def setup_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


class TestMFAAPI:
    """Integration tests for MFA setup, enablement, verification, and disabling."""

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
        user = db_session.query(User).filter(User.email == "mfa_tester@example.com").first()
        if not user:
            user = User(
                email="mfa_tester@example.com",
                is_active=True,
                is_verified=True,
                role="user",
                mfa_enabled=False,
            )
            db_session.add(user)
            db_session.commit()
            db_session.refresh(user)
        else:
            user.mfa_enabled = False
            user.mfa_secret = None
            user.mfa_recovery_codes = None
            db_session.commit()
            db_session.refresh(user)
        return user

    @pytest.fixture
    def client(self, db_session: DBSession, user: User) -> Generator[TestClient, None, None]:
        reset_all_rate_limiters()
        app = create_app()

        def override_get_db() -> Generator[DBSession, None, None]:
            yield db_session

        def override_get_current_user() -> AuthenticatedUser:
            return AuthenticatedUser(user_id=user.id, email=user.email, role=user.role)

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_current_user
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def test_full_mfa_lifecycle(
        self, client: TestClient, db_session: DBSession, user: User
    ) -> None:
        # 1. Setup MFA
        reset_all_rate_limiters()
        res_setup = client.post("/api/v1/auth/mfa/setup")
        assert res_setup.status_code == 200
        setup_data = res_setup.json()
        secret = setup_data["secret"]
        recovery_codes = setup_data["recovery_codes"]
        assert len(secret) == 32
        assert len(recovery_codes) == 8
        assert "otpauth://" in setup_data["provisioning_uri"]

        # 2. Enable MFA with invalid code -> 400
        reset_all_rate_limiters()
        res_enable_fail = client.post(
            "/api/v1/auth/mfa/enable",
            json={
                "secret": secret,
                "code": "000000",
                "recovery_codes": recovery_codes,
            },
        )
        assert res_enable_fail.status_code == 400

        # 3. Enable MFA with valid code -> 200
        valid_code = totp_manager.generate_code(secret)
        reset_all_rate_limiters()
        res_enable = client.post(
            "/api/v1/auth/mfa/enable",
            json={
                "secret": secret,
                "code": valid_code,
                "recovery_codes": recovery_codes,
            },
        )
        assert res_enable.status_code == 200
        assert res_enable.json()["mfa_enabled"] is True

        # Verify DB updated
        db_session.refresh(user)
        assert user.mfa_enabled is True
        assert user.mfa_secret == secret

        # 4. Setup again when already enabled -> 400
        reset_all_rate_limiters()
        res_setup_again = client.post("/api/v1/auth/mfa/setup")
        assert res_setup_again.status_code == 400
        assert res_setup_again.json()["error"]["code"] == "MFA_ALREADY_ENABLED"

        # 5. Verify challenge with valid TOTP code -> 200
        code_now = totp_manager.generate_code(secret)
        reset_all_rate_limiters()
        res_verify_totp = client.post("/api/v1/auth/mfa/verify", json={"code": code_now})
        assert res_verify_totp.status_code == 200
        assert res_verify_totp.json()["verified"] is True

        # 6. Verify challenge with invalid code -> 400
        reset_all_rate_limiters()
        res_verify_invalid = client.post("/api/v1/auth/mfa/verify", json={"code": "987654"})
        assert res_verify_invalid.status_code == 400
        assert res_verify_invalid.json()["error"]["code"] == "MFA_INVALID_CODE"

        # 7. Verify challenge using a recovery code -> 200
        rec_code = recovery_codes[0]
        reset_all_rate_limiters()
        res_verify_rec = client.post("/api/v1/auth/mfa/verify", json={"code": rec_code})
        assert res_verify_rec.status_code == 200
        assert res_verify_rec.json()["remaining_recovery_codes"] == 7

        # 8. Reusing the same recovery code -> 400
        reset_all_rate_limiters()
        res_verify_rec_reuse = client.post("/api/v1/auth/mfa/verify", json={"code": rec_code})
        assert res_verify_rec_reuse.status_code == 400
        assert res_verify_rec_reuse.json()["error"]["code"] == "MFA_INVALID_CODE"

        # 9. Disable MFA with valid code -> 200
        disable_code = totp_manager.generate_code(secret)
        reset_all_rate_limiters()
        res_disable = client.post("/api/v1/auth/mfa/disable", json={"code": disable_code})
        assert res_disable.status_code == 200
        assert res_disable.json()["mfa_enabled"] is False

        # Verify DB updated
        db_session.refresh(user)
        assert user.mfa_enabled is False
        assert user.mfa_secret is None

        # 10. Verify challenge when disabled -> 400
        reset_all_rate_limiters()
        res_verify_disabled = client.post("/api/v1/auth/mfa/verify", json={"code": disable_code})
        assert res_verify_disabled.status_code == 400
        assert res_verify_disabled.json()["error"]["code"] == "MFA_NOT_ENABLED"
