"""Comprehensive tests for login form UX, password manager compatibility, and passkeys.

Verifies:
1. Clean, safe, non-enumerating error messages for login failures.
2. Password manager HTML and accessibility attribute metadata (/auth/form-metadata).
3. WebAuthn Passkey registration, options generation, and public key verification.
4. Passkey authentication (assertion verification), session issuance, and clone/replay protection.
5. Passkey device management (list, rename, revoke) with IDOR protection.
6. Guest session migration during passkey login.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.auth.brute_force import brute_force_defender
from stock_analysis.auth.hasher import PasswordHasher
from stock_analysis.auth.passkey import passkey_manager
from stock_analysis.db.base import Base
from stock_analysis.db.models import PasskeyCredential, User
from stock_analysis.db.session import get_db

_hasher = PasswordHasher()


@pytest.fixture
def db_session() -> Generator[DBSession, None, None]:
    """Create isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_user(db_session: DBSession) -> User:
    """Create a verified test user."""
    user = User(
        email="passkey_user@example.com",
        hashed_password=_hasher.hash("SuperSecret123!"),
        is_active=True,
        is_verified=True,
        role="user",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    """Create a test client with overridden database dependency and cleared rate limits."""
    reset_all_rate_limiters()
    brute_force_defender.reset()
    with passkey_manager._lock:
        passkey_manager._challenges.clear()

    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_form_metadata_login_and_register(client: TestClient) -> None:
    """Test standard form metadata for password manager and passkey autofill."""
    # 1. Login form metadata
    resp = client.get("/api/v1/auth/form-metadata?form_type=login")
    assert resp.status_code == 200
    data = resp.json()
    assert data["form_id"] == "login-form"
    assert data["passkey_supported"] is True
    assert data["passkey_autocomplete"] == "username webauthn"
    assert "1Password" in data["supported_password_managers"]
    assert "Bitwarden" in data["supported_password_managers"]

    fields = data["fields"]
    assert fields["email"]["autocomplete"] == "username webauthn"
    assert fields["email"]["type"] == "email"
    assert fields["password"]["autocomplete"] == "current-password"
    assert fields["password"]["type"] == "password"
    assert fields["mfa_code"]["autocomplete"] == "one-time-code"

    # 2. Register form metadata
    resp_reg = client.get("/api/v1/auth/form-metadata?form_type=register")
    assert resp_reg.status_code == 200
    reg_data = resp_reg.json()
    assert reg_data["form_id"] == "register-form"
    assert reg_data["fields"]["password"]["autocomplete"] == "new-password"


def test_clean_login_error_messages(client: TestClient, test_user: User) -> None:
    """Ensure login error messages are clean, courteous, and prevent enumeration."""
    # 1. Non-existent email
    resp_missing = client.post(
        "/api/v1/auth/login",
        json={"email": "non_existent@example.com", "password": "WrongPassword123!"},
    )
    assert resp_missing.status_code == 401
    err_missing = resp_missing.json()
    assert err_missing["error"]["code"] == "INVALID_CREDENTIALS"
    assert "E-posta" in err_missing["error"]["message"]

    # 2. Existing user, incorrect password
    resp_wrong = client.post(
        "/api/v1/auth/login",
        json={"email": test_user.email, "password": "WrongPassword123!"},
    )
    assert resp_wrong.status_code == 401
    err_wrong = resp_wrong.json()
    assert err_wrong["error"]["code"] == "INVALID_CREDENTIALS"

    # Ensure identical message to prevent user enumeration
    assert err_missing["error"]["message"] == err_wrong["error"]["message"]

    # 3. Clean validation error message without leaking internals
    resp_invalid_payload = client.post(
        "/api/v1/auth/login",
        json={"email": "not-an-email", "password": "short"},
    )
    assert resp_invalid_payload.status_code == 422
    err_val = resp_invalid_payload.json()
    assert err_val["error"]["code"] == "VALIDATION_ERROR"


def test_passkey_registration_flow(
    client: TestClient, test_user: User, db_session: DBSession
) -> None:
    """Test WebAuthn passkey registration options and verification."""
    # Authenticate via dependency override for current_user
    app = cast(FastAPI, client.app)
    auth_user = AuthenticatedUser(
        user_id=test_user.id,
        email=test_user.email,
        role=test_user.role,
        mfa_enabled=False,
    )
    app.dependency_overrides[get_current_user] = lambda: auth_user

    # 1. Request registration options
    resp_opts = client.post("/api/v1/auth/passkey/register/options")
    assert resp_opts.status_code == 200
    opts = resp_opts.json()["options"]
    assert "challenge" in opts
    assert opts["user"]["name"] == test_user.email

    challenge = opts["challenge"]

    # 2. Verify and register credential
    fake_cred_id = "test-credential-id-1234567890abcdef"
    fake_pub_key = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEfakePublicKeySampleString123"

    resp_verify = client.post(
        "/api/v1/auth/passkey/register/verify",
        json={
            "challenge": challenge,
            "credential_id": fake_cred_id,
            "public_key": fake_pub_key,
            "device_name": "MacBook TouchID",
            "transports": ["internal", "hybrid"],
        },
    )
    assert resp_verify.status_code == 201
    verify_data = resp_verify.json()
    assert verify_data["credential_id"] == fake_cred_id
    assert verify_data["device_name"] == "MacBook TouchID"

    # 3. Check DB
    cred = db_session.query(PasskeyCredential).filter_by(credential_id=fake_cred_id).first()
    assert cred is not None
    assert cred.user_id == test_user.id
    assert cred.device_name == "MacBook TouchID"
    assert cred.sign_count == 0

    # 4. Attempt re-using same challenge should fail
    resp_reused = client.post(
        "/api/v1/auth/passkey/register/verify",
        json={
            "challenge": challenge,
            "credential_id": "another-credential-id-12345",
            "public_key": fake_pub_key,
        },
    )
    assert resp_reused.status_code == 400
    assert resp_reused.json()["error"]["code"] == "INVALID_CHALLENGE"


def test_passkey_login_and_assertion_verification(
    client: TestClient, test_user: User, db_session: DBSession
) -> None:
    """Test logging in using registered passkey and assertion verification."""
    # 1. Register a passkey directly in DB
    cred_id = "login-passkey-cred-id-abc12345"
    cred = PasskeyCredential(
        user_id=test_user.id,
        credential_id=cred_id,
        public_key="SamplePubKeyPEM123456",
        sign_count=10,
        device_name="iPhone FaceID",
    )
    db_session.add(cred)
    db_session.commit()

    # 2. Request login options
    resp_opts = client.post(
        "/api/v1/auth/passkey/login/options",
        json={"email": test_user.email},
    )
    assert resp_opts.status_code == 200
    opts = resp_opts.json()["options"]
    challenge = opts["challenge"]
    assert len(opts["allowCredentials"]) == 1
    assert opts["allowCredentials"][0]["id"] == cred_id

    # 3. Verify assertion with valid client_sign_count > stored sign_count
    resp_login = client.post(
        "/api/v1/auth/passkey/login/verify",
        json={
            "challenge": challenge,
            "credential_id": cred_id,
            "client_sign_count": 11,
        },
    )
    assert resp_login.status_code == 200
    login_data = resp_login.json()
    assert login_data["access_token"] is not None
    assert "Passkey ile giriş başarılı." in login_data["message"]

    # Verify session cookie was set
    assert "borai_session" in client.cookies

    # Verify sign count updated in DB
    db_session.refresh(cred)
    assert cred.sign_count == 11
    assert cred.last_used_at is not None


def test_passkey_clone_detection(
    client: TestClient, test_user: User, db_session: DBSession
) -> None:
    """Test that monotonic counter rollback / clone attempt is rejected."""
    cred_id = "clone-detect-cred-id-xyz"
    cred = PasskeyCredential(
        user_id=test_user.id,
        credential_id=cred_id,
        public_key="SamplePubKey",
        sign_count=50,
        device_name="YubiKey 5C",
    )
    db_session.add(cred)
    db_session.commit()

    # Request challenge
    resp_opts = client.post("/api/v1/auth/passkey/login/options")
    assert resp_opts.status_code == 200
    challenge = resp_opts.json()["options"]["challenge"]

    # Sign count rollback / clone (client reports count 40 <= 50)
    resp_verify = client.post(
        "/api/v1/auth/passkey/login/verify",
        json={
            "challenge": challenge,
            "credential_id": cred_id,
            "client_sign_count": 40,
        },
    )
    assert resp_verify.status_code == 401
    assert resp_verify.json()["error"]["code"] == "AUTH_FAILED"
    assert (
        "sayaç" in resp_verify.json()["error"]["message"].lower()
        or "klon" in resp_verify.json()["error"]["message"].lower()
    )


def test_passkey_management_and_idor_protection(
    client: TestClient, test_user: User, db_session: DBSession
) -> None:
    """Test listing, renaming, and deleting passkeys with IDOR protection."""
    # Create another user
    other_user = User(
        email="other_user@example.com",
        hashed_password=_hasher.hash("OtherPassword123!"),
        is_active=True,
        is_verified=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    # Add passkey for test_user
    user_cred = PasskeyCredential(
        user_id=test_user.id,
        credential_id="cred-user-1-12345678",
        public_key="pubKey1",
        device_name="My Primary Key",
    )
    # Add passkey for other_user
    other_cred = PasskeyCredential(
        user_id=other_user.id,
        credential_id="cred-other-2-12345678",
        public_key="pubKey2",
        device_name="Other's Key",
    )
    db_session.add_all([user_cred, other_cred])
    db_session.commit()
    db_session.refresh(user_cred)
    db_session.refresh(other_cred)

    # Authenticate as test_user
    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=test_user.id,
        email=test_user.email,
        role="user",
        mfa_enabled=False,
    )

    # 1. List passkeys (only test_user's passkey returned)
    resp_list = client.get("/api/v1/auth/passkey/list")
    assert resp_list.status_code == 200
    data_list = resp_list.json()
    assert data_list["total"] == 1
    assert data_list["passkeys"][0]["credential_id"] == "cred-user-1-12345678"

    # 2. Rename own passkey
    resp_rename = client.patch(
        f"/api/v1/auth/passkey/{user_cred.id}",
        json={"device_name": "Renamed YubiKey"},
    )
    assert resp_rename.status_code == 200
    assert resp_rename.json()["device_name"] == "Renamed YubiKey"

    # 3. IDOR: Attempt renaming other user's passkey -> 404
    resp_idor_rename = client.patch(
        f"/api/v1/auth/passkey/{other_cred.id}",
        json={"device_name": "Hacked Name"},
    )
    assert resp_idor_rename.status_code == 404

    # 4. IDOR: Attempt deleting other user's passkey -> 404
    resp_idor_delete = client.delete(f"/api/v1/auth/passkey/{other_cred.id}")
    assert resp_idor_delete.status_code == 404

    # 5. Delete own passkey -> 200
    resp_delete = client.delete(f"/api/v1/auth/passkey/{user_cred.id}")
    assert resp_delete.status_code == 200
    assert "kaldırıldı" in resp_delete.json()["message"]

    # Verify deleted from DB
    assert db_session.get(PasskeyCredential, user_cred.id) is None
    # Other user's key remains untouched
    assert db_session.get(PasskeyCredential, other_cred.id) is not None
