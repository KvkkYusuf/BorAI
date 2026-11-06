"""Tests for Admin MFA enforcement and Principle of Least Privilege (RBAC)."""

import hashlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.deps import require_roles
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.config import Settings, get_settings
from stock_analysis.db.base import Base
from stock_analysis.db.models import AuditEvent, User
from stock_analysis.db.models import Session as UserSession
from stock_analysis.db.session import get_db

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db() -> Generator[None, None, None]:
    """Recreate test database schema for each test run."""
    Base.metadata.create_all(bind=test_engine)
    reset_all_rate_limiters()
    yield
    Base.metadata.drop_all(bind=test_engine)


def create_user_and_token(
    db: DBSession,
    email: str,
    role: str = "user",
    mfa_enabled: bool = False,
    is_active: bool = True,
) -> tuple[User, str]:
    """Helper to seed user and active session token."""
    user = User(
        email=email,
        role=role,
        mfa_enabled=mfa_enabled,
        is_active=is_active,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = f"sess_token_{user.id}_{role}_{int(mfa_enabled)}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    session_rec = UserSession(
        user_id=user.id,
        session_token_hash=token_hash,
        ip_hash="test_ip",
        user_agent="pytest-client",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    db.add(session_rec)
    db.commit()

    return user, raw_token


class TestAdminMfaAndLeastPrivilege:
    """Test suite ensuring admin access requires proper roles and production MFA."""

    def test_unauthenticated_request_rejected(self) -> None:
        """Calling admin endpoint without authentication returns 401."""
        app = create_app()

        def override_db() -> Generator[DBSession, None, None]:
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        response = client.get("/api/v1/admin/overview")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_least_privilege_regular_user_forbidden(self) -> None:
        """Regular user with role='user' is rejected from admin endpoints with 403."""
        app = create_app()

        def override_db() -> Generator[DBSession, None, None]:
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        db = TestingSessionLocal()
        _, user_token = create_user_and_token(db, "standard@example.com", role="user")
        db.close()

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {user_token}"}

        endpoints = [
            "/api/v1/admin/overview",
            "/api/v1/admin/users",
            "/api/v1/admin/audit-logs",
        ]
        for ep in endpoints:
            response = client.get(ep, headers=headers)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN_ADMIN_REQUIRED"

    def test_admin_in_development_without_mfa_allowed(self) -> None:
        """In development environment, admin without MFA can access overview."""
        app = create_app()

        def override_db() -> Generator[DBSession, None, None]:
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_settings() -> Settings:
            return Settings(app_env="development")  # type: ignore[call-arg]

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = override_settings

        db = TestingSessionLocal()
        _, admin_token = create_user_and_token(
            db, "devadmin@example.com", role="admin", mfa_enabled=False
        )
        db.close()

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.get("/api/v1/admin/overview", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["app_env"] == "development"
        assert data["admin_mfa_enforced"] is False
        assert data["total_admins"] == 1

    def test_admin_in_production_without_mfa_strictly_blocked(self) -> None:
        """In production environment, admin without MFA is strictly blocked with 403."""
        app = create_app()

        def override_db() -> Generator[DBSession, None, None]:
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_settings() -> Settings:
            return Settings(app_env="production")  # type: ignore[call-arg]

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = override_settings

        db = TestingSessionLocal()
        _, admin_token = create_user_and_token(
            db, "prodadmin_nomfa@example.com", role="admin", mfa_enabled=False
        )
        db.close()

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.get("/api/v1/admin/overview", headers=headers)
        assert response.status_code == 403
        data = response.json()
        assert data["error"]["code"] == "ADMIN_MFA_REQUIRED"

        # Also blocked for user listing
        response_users = client.get("/api/v1/admin/users", headers=headers)
        assert response_users.status_code == 403
        assert response_users.json()["error"]["code"] == "ADMIN_MFA_REQUIRED"

    def test_admin_in_production_with_mfa_allowed(self) -> None:
        """In production environment, admin with MFA enabled has full access."""
        app = create_app()

        def override_db() -> Generator[DBSession, None, None]:
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_settings() -> Settings:
            return Settings(app_env="production")  # type: ignore[call-arg]

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = override_settings

        db = TestingSessionLocal()
        _, admin_token = create_user_and_token(
            db, "prodadmin_mfa@example.com", role="admin", mfa_enabled=True
        )
        # Add an audit event
        audit = AuditEvent(
            action="admin_login",
            resource="auth",
            details="Admin MFA session opened",
        )
        db.add(audit)
        db.commit()
        db.close()

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.get("/api/v1/admin/overview", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["app_env"] == "production"
        assert data["admin_mfa_enforced"] is True
        assert data["total_admins"] == 1
        assert data["total_audit_events"] == 1

        # Check users list
        users_resp = client.get("/api/v1/admin/users", headers=headers)
        assert users_resp.status_code == 200
        users_data = users_resp.json()
        assert users_data["total"] >= 1
        assert users_data["items"][0]["email"] == "prodadmin_mfa@example.com"
        assert users_data["items"][0]["mfa_enabled"] is True

        # Check audit logs
        logs_resp = client.get("/api/v1/admin/audit-logs", headers=headers)
        assert logs_resp.status_code == 200
        logs_data = logs_resp.json()
        assert logs_data["total"] == 1
        assert logs_data["items"][0]["action"] == "admin_login"

    def test_require_roles_helper_custom_restriction(self) -> None:
        """Test require_roles dependency factory with multiple roles."""
        app = create_app()

        # Add a custom route requiring role 'auditor' or 'admin'
        @app.get(
            "/api/v1/audit-special",
            dependencies=[
                pytest.importorskip("fastapi").Depends(require_roles("auditor", "admin"))
            ],
        )
        def special_audit_endpoint() -> dict[str, str]:
            return {"status": "authorized"}

        def override_db() -> Generator[DBSession, None, None]:
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db

        db = TestingSessionLocal()
        _, user_token = create_user_and_token(db, "plain@example.com", role="user")
        _, auditor_token = create_user_and_token(db, "auditor@example.com", role="auditor")
        db.close()

        client = TestClient(app)

        # Standard user is forbidden
        res_user = client.get(
            "/api/v1/audit-special", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert res_user.status_code == 403
        assert res_user.json()["error"]["code"] == "FORBIDDEN_INSUFFICIENT_PERMISSIONS"

        # Auditor role is authorized
        res_auditor = client.get(
            "/api/v1/audit-special", headers={"Authorization": f"Bearer {auditor_token}"}
        )
        assert res_auditor.status_code == 200
        assert res_auditor.json() == {"status": "authorized"}
