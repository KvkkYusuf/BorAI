"""End-to-end test suite for authentication and account deletion lifecycle (FAZ-16).

Verifies:
1. Email Verification: Cryptographic single-use tokens, replay prevention, expiry rejection,
   user activation, and anti-enumeration protection.
2. Password Reset: Strict 15-minute TTL, scrypt hashing, single-use token consumption,
   and immediate invalidation of all active user sessions across devices.
3. KVKK Account Deletion: Hard-purge of personal data, cascading cleanup of records,
   Law No. 5651 audit log retention with anonymization, and TombstoneRegistry tracking.
4. Pre-Deployment Gatekeeper: AccountLifecycleGatekeeper evaluation and REST API endpoints.
"""

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
from stock_analysis.auth.email_verification import (
    EmailVerificationError,
    EmailVerificationManager,
    email_verification_manager,
)
from stock_analysis.auth.hasher import PasswordHasher, PasswordPolicyError
from stock_analysis.auth.reset_flow import (
    PasswordResetManager,
    ResetFlowError,
    password_reset_manager,
)
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
from stock_analysis.security.account_lifecycle_gatekeeper import (
    AccountLifecycleGatekeeper,
    VerificationGateStatus,
)
from stock_analysis.security.data_retention import (
    DEFAULT_TOMBSTONE_REGISTRY,
    reset_default_tombstone_registry,
)

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


@pytest.fixture
def db_session(setup_db: None) -> Generator[DBSession, None, None]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def reset_state() -> None:
    reset_all_rate_limiters()
    email_verification_manager.reset()
    password_reset_manager._tokens.clear()
    password_reset_manager._request_history.clear()
    reset_default_tombstone_registry()


class TestEmailVerificationLifecycle:
    """Tests for email verification token mechanics and end-to-end API flows."""

    def test_token_creation_and_hashing(self) -> None:
        manager = EmailVerificationManager(ttl_hours=24)
        raw_token, payload = manager.create_verification_token(
            user_id=10, email="trader@example.com"
        )
        assert len(raw_token) >= 32
        assert payload.user_id == 10
        assert payload.email == "trader@example.com"
        assert payload.token_hash == manager.hash_token(raw_token)
        assert not payload.is_expired()

    def test_token_expiry_enforcement(self) -> None:
        manager = EmailVerificationManager(ttl_hours=1)
        raw_token, payload = manager.create_verification_token(
            user_id=11, email="expire@example.com"
        )
        token_hash = manager.hash_token(raw_token)

        # Force token expiry
        old_payload = manager._tokens[token_hash]
        object.__setattr__(
            old_payload,
            "expires_at",
            datetime.now(UTC) - timedelta(seconds=10),
        )

        with pytest.raises(EmailVerificationError, match="süresi dolmuş"):
            manager.verify_and_consume(raw_token)

    def test_single_use_replay_prevention(self) -> None:
        manager = EmailVerificationManager()
        raw_token, _ = manager.create_verification_token(user_id=12, email="single@example.com")

        # First consumption succeeds
        consumed = manager.verify_and_consume(raw_token)
        assert consumed.user_id == 12

        # Second consumption fails
        with pytest.raises(EmailVerificationError, match="Geçersiz veya daha önce kullanılmış"):
            manager.verify_and_consume(raw_token)

    def test_rate_limiting_throttles_excessive_requests(self) -> None:
        manager = EmailVerificationManager()
        for _ in range(EmailVerificationManager.MAX_REQUESTS_PER_WINDOW):
            manager.create_verification_token(user_id=99, email="burst@example.com")

        with pytest.raises(EmailVerificationError, match="Çok fazla doğrulama talebi"):
            manager.create_verification_token(user_id=99, email="burst@example.com")

    def test_api_e2e_email_verification_flow(
        self, client: TestClient, db_session: DBSession
    ) -> None:
        # 1. Create unverified user
        hasher = PasswordHasher()
        user = User(
            email="unverified_trader@example.com",
            hashed_password=hasher.hash("ValidPass123!@"),
            is_active=True,
            is_verified=False,
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        # 2. Request verification token
        req_res = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": "unverified_trader@example.com"},
        )
        assert req_res.status_code == 200
        assert "gönderildi" in req_res.json()["message"]

        # Create token directly to obtain raw value for confirmation
        raw_token, _ = email_verification_manager.create_verification_token(user.id, user.email)

        # 3. Confirm verification
        conf_res = client.post(
            "/api/v1/auth/verify-email/confirm",
            json={"token": raw_token},
        )
        assert conf_res.status_code == 200
        assert conf_res.json()["is_verified"] is True

        db_session.refresh(user)
        assert user.is_verified is True

        # 4. Replay attempt fails with HTTP 400
        replay_res = client.post(
            "/api/v1/auth/verify-email/confirm",
            json={"token": raw_token},
        )
        assert replay_res.status_code == 400

        # 5. Anti-enumeration: Request for non-existent user returns identical 200 message
        ghost_res = client.post(
            "/api/v1/auth/verify-email/request",
            json={"email": "non_existent_ghost@example.com"},
        )
        assert ghost_res.status_code == 200
        assert ghost_res.json()["message"] == req_res.json()["message"]


class TestPasswordResetLifecycle:
    """Tests for password reset token TTL, scrypt hashing, and session revocation."""

    def test_strict_15_min_ttl_enforcement(self) -> None:
        manager = PasswordResetManager(ttl_minutes=15)
        raw_token, payload = manager.create_reset_token(user_id=20, email="secure@example.com")
        duration = (payload.expires_at - payload.created_at).total_seconds()
        assert duration <= 15 * 60 + 2

        # Expired token rejection
        token_hash = manager.hash_token(raw_token)
        old_payload = manager._tokens[token_hash]
        object.__setattr__(
            old_payload,
            "expires_at",
            datetime.now(UTC) - timedelta(minutes=1),
        )
        with pytest.raises(ResetFlowError, match="süresi dolmuş"):
            manager.verify_and_consume(raw_token)

    def test_scrypt_password_policy_and_verification(self) -> None:
        hasher = PasswordHasher()
        old_pwd = "OldPassword2025!*"

        h = hasher.hash(old_pwd)
        assert hasher.verify(old_pwd, h)
        assert not hasher.verify("WrongPassword", h)

        # Rejects weak password
        with pytest.raises(PasswordPolicyError):
            hasher.hash("weak")

    def test_api_e2e_password_reset_and_session_revocation(
        self, client: TestClient, db_session: DBSession
    ) -> None:
        hasher = PasswordHasher()
        user = User(
            email="reset_target_trader@example.com",
            hashed_password=hasher.hash("OldPassword123!*"),
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        # 1. Log in to establish 2 active sessions (e.g. desktop and mobile)
        s1_raw, _, _ = session_security_policy.create_session(
            db_session, user.id, user_agent="DesktopBrowser"
        )
        s2_raw, _, _ = session_security_policy.create_session(
            db_session, user.id, user_agent="MobileApp"
        )

        # Verify sessions are active
        assert session_security_policy.resolve_session(db_session, s1_raw) is not None
        assert session_security_policy.resolve_session(db_session, s2_raw) is not None

        # 2. Request reset
        req_res = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": user.email},
        )
        assert req_res.status_code == 200

        # Generate token directly to get raw value
        raw_reset_token, _ = password_reset_manager.create_reset_token(user.id, user.email)

        # 3. Confirm password reset with new strong password
        new_pwd = "NewUpdatedPassword2026!#"
        conf_res = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw_reset_token, "new_password": new_pwd},
        )
        assert conf_res.status_code == 200
        assert "başarıyla güncellendi" in conf_res.json()["message"]

        # 4. Check that all active sessions were revoked immediately (OWASP ASVS 3.3.4)
        db_session.expire_all()
        assert session_security_policy.resolve_session(db_session, s1_raw) is None
        assert session_security_policy.resolve_session(db_session, s2_raw) is None

        # 5. Check login with old password fails
        old_login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "OldPassword123!*"},
        )
        assert old_login.status_code == 401

        # 6. Check login with new password succeeds
        new_login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": new_pwd},
        )
        assert new_login.status_code == 200

        # 7. Check reset token cannot be reused
        reused_res = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw_reset_token, "new_password": "AnotherNewPass123!#"},
        )
        assert reused_res.status_code == 400


class TestAccountDeletionLifecycle:
    """Tests for KVKK hard deletion, cascades, tombstone registry, and 5651 audit retention."""

    def test_api_e2e_account_deletion_with_cascades_and_tombstone(
        self, client: TestClient, db_session: DBSession
    ) -> None:
        hasher = PasswordHasher()
        pwd = "DeleteMePassword123!#"
        user = User(
            email="kvkk_target_user@example.com",
            hashed_password=hasher.hash(pwd),
            is_active=True,
            is_verified=True,
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        uid = user.id
        u_email = user.email

        # Create session
        raw_token, csrf_token, _ = session_security_policy.create_session(db_session, uid)

        # Create related records (Instrument, Watchlist, Alert, Preference)
        inst = Instrument(symbol="GARAN", name="Garanti BBVA", market="BIST", is_active=True)
        db_session.add(inst)
        db_session.commit()
        db_session.refresh(inst)

        wl = Watchlist(user_id=uid, name="Takip 1")
        db_session.add(wl)
        db_session.commit()
        db_session.refresh(wl)

        w_item = WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id)
        db_session.add(w_item)

        rule = AlertRule(
            user_id=uid,
            instrument_id=inst.id,
            condition_type="price_above",
            threshold=150.0,
            is_active=True,
        )
        db_session.add(rule)

        pref = UserPreference(user_id=uid, theme="light", language="tr")
        db_session.add(pref)

        # Prior audit trail under 5651 Law
        prior_audit = AuditEvent(
            user_id=uid,
            action="LOGIN_SUCCESS",
            resource=f"user:{uid}",
            details="Başarılı kullanıcı girişi yapıldı.",
        )
        db_session.add(prior_audit)
        db_session.commit()
        prior_audit_id = prior_audit.id

        # Authenticate client
        client.cookies.set(session_security_policy.SESSION_COOKIE_NAME, raw_token)
        client.cookies.set(session_security_policy.CSRF_COOKIE_NAME, csrf_token)

        # 1. Export data under KVKK portability
        export_res = client.get(
            "/api/v1/user/export",
            headers={session_security_policy.CSRF_HEADER_NAME: csrf_token},
        )
        assert export_res.status_code == 200
        export_json = export_res.json()
        assert export_json["email"] == u_email
        assert len(export_json["alerts"]) == 1

        # 2. Hard delete account
        del_res = client.delete(
            "/api/v1/user/account",
            headers={session_security_policy.CSRF_HEADER_NAME: csrf_token},
        )
        assert del_res.status_code == 200
        assert "kalici olarak silindi" in del_res.json()["message"].lower()

        # 3. Verify user and all cascading child records are deleted
        db_session.expire_all()
        assert db_session.get(User, uid) is None
        assert (
            db_session.execute(select(UserSession).where(UserSession.user_id == uid))
            .scalars()
            .all()
            == []
        )
        assert (
            db_session.execute(select(Watchlist).where(Watchlist.user_id == uid)).scalars().all()
            == []
        )
        assert (
            db_session.execute(select(AlertRule).where(AlertRule.user_id == uid)).scalars().all()
            == []
        )
        assert (
            db_session.execute(select(UserPreference).where(UserPreference.user_id == uid))
            .scalars()
            .all()
            == []
        )

        # 4. Verify Law No. 5651 audit log is retained with anonymized user_id=None
        retained_audit = db_session.get(AuditEvent, prior_audit_id)
        assert retained_audit is not None
        assert retained_audit.user_id is None

        del_audit = db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "ACCOUNT_DELETED")
        ).scalar_one_or_none()
        assert del_audit is not None
        assert del_audit.user_id is None

        # 5. Verify Tombstone is registered
        assert DEFAULT_TOMBSTONE_REGISTRY.is_deleted(user_id=uid)

        # 6. Verify subsequent requests with old session return 401 Unauthorized
        subseq_res = client.get(
            "/api/v1/user/preferences",
            headers={session_security_policy.CSRF_HEADER_NAME: csrf_token},
        )
        assert subseq_res.status_code == 401


class TestAccountLifecycleGatekeeperAndApi:
    """Tests for the pre-deployment gatekeeper coordinator and REST API endpoints."""

    def test_gatekeeper_evaluate_gate(self) -> None:
        gatekeeper = AccountLifecycleGatekeeper()
        report = gatekeeper.evaluate_gate()

        assert report.gate_status == VerificationGateStatus.PASS
        assert report.is_signoff_ready is True
        assert report.checks_evaluated >= 10
        assert report.checks_passed == report.checks_evaluated
        assert report.checks_failed == 0
        assert len(report.blocking_reasons) == 0

        # Markdown report generation
        md = report.generate_markdown_audit_report()
        assert (
            "# Dagitim Oncesi E-posta Dogrulama, Parola Sifirlama ve Hesap Silme Guvenlik Raporu"
            in md
        )
        assert "GECTI (PASS)" in md
        assert "CHK-EMAIL-001" in md
        assert "CHK-PASS-003" in md
        assert "CHK-DEL-001" in md

    def test_api_verify_endpoint(self, client: TestClient) -> None:
        res = client.get("/api/v1/pre-deployment/account-lifecycle/verify")
        assert res.status_code == 200
        data = res.json()
        assert data["gate_status"] == "PASS"
        assert data["is_signoff_ready"] is True
        assert data["checks_evaluated"] >= 10
        assert data["checks_failed"] == 0

    def test_api_report_endpoint(self, client: TestClient) -> None:
        res = client.get("/api/v1/pre-deployment/account-lifecycle/report")
        assert res.status_code == 200
        assert "text/markdown" in res.headers["content-type"]
        assert "GECTI (PASS)" in res.text
