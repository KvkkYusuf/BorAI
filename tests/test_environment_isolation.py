"""Tests for Environment Isolation and Access Role Segregation (PoLP & RBAC)."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from stock_analysis.api.deps import AuthenticatedUser, get_current_user, require_roles
from stock_analysis.config import Settings
from stock_analysis.security.environments import (
    ROLE_SCOPES,
    TEST_ONLY_ROLES,
    AccessRole,
    AppEnvironment,
    EnvironmentGuard,
    EnvironmentIsolationError,
)


class TestAppEnvironment:
    """Test suite verifying AppEnvironment enumeration and helper properties."""

    @pytest.mark.parametrize(
        ("raw_input", "expected"),
        [
            ("development", AppEnvironment.DEVELOPMENT),
            ("dev", AppEnvironment.DEVELOPMENT),
            ("DEV", AppEnvironment.DEVELOPMENT),
            ("testing", AppEnvironment.TESTING),
            ("test", AppEnvironment.TESTING),
            ("pytest", AppEnvironment.TESTING),
            ("staging", AppEnvironment.STAGING),
            ("stage", AppEnvironment.STAGING),
            ("production", AppEnvironment.PRODUCTION),
            ("prod", AppEnvironment.PRODUCTION),
            ("PROD", AppEnvironment.PRODUCTION),
            (None, AppEnvironment.DEVELOPMENT),
            ("", AppEnvironment.DEVELOPMENT),
        ],
    )
    def test_from_string_valid_aliases(
        self,
        raw_input: str | None,
        expected: AppEnvironment,
    ) -> None:
        assert AppEnvironment.from_string(raw_input) == expected

    def test_from_string_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Geçersiz ortam adı"):
            AppEnvironment.from_string("sandbox_unknown")

    def test_environment_predicate_properties(self) -> None:
        prod = AppEnvironment.PRODUCTION
        stage = AppEnvironment.STAGING
        dev = AppEnvironment.DEVELOPMENT
        test = AppEnvironment.TESTING

        assert prod.is_production is True
        assert prod.is_production_like is True
        assert prod.is_testing is False

        assert stage.is_staging is True
        assert stage.is_production_like is True
        assert stage.is_production is False

        assert dev.is_development is True
        assert dev.is_production_like is False

        assert test.is_testing is True
        assert test.is_production_like is False


class TestAccessRoleAndScopes:
    """Test suite verifying AccessRole enumeration and least privilege scopes."""

    def test_all_canonical_roles_defined(self) -> None:
        expected_roles = {
            "guest",
            "user",
            "analyst",
            "compliance_officer",
            "ml_engineer",
            "admin",
            "super_admin",
        }
        actual_roles = {r.value for r in AccessRole}
        assert actual_roles == expected_roles

    def test_role_from_string_case_insensitive(self) -> None:
        assert AccessRole.from_string("ADMIN") == AccessRole.ADMIN
        assert AccessRole.from_string("compliance_officer") == AccessRole.COMPLIANCE_OFFICER
        assert AccessRole.from_string(None) == AccessRole.GUEST

    def test_role_from_string_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Geçersiz rol"):
            AccessRole.from_string("super_hacker")

    def test_role_scopes_complete_and_populated(self) -> None:
        for role in AccessRole:
            assert role in ROLE_SCOPES
            scope = ROLE_SCOPES[role]
            assert scope.role == role
            assert len(scope.permissions) > 0
            assert len(scope.description) > 5

    def test_mfa_requirement_in_production(self) -> None:
        # High privilege roles must mandate MFA in production
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.ADMIN) is True
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.SUPER_ADMIN) is True
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.COMPLIANCE_OFFICER) is True
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.ML_ENGINEER) is True

        # Standard / analyst / guest do not require MFA
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.USER) is False
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.ANALYST) is False
        assert EnvironmentGuard.requires_mfa_in_production(AccessRole.GUEST) is False

    def test_permission_queries(self) -> None:
        assert EnvironmentGuard.has_permission(AccessRole.USER, "watchlists:read_own") is True
        assert EnvironmentGuard.has_permission(AccessRole.USER, "security:rotate_secrets") is False
        assert (
            EnvironmentGuard.has_permission(AccessRole.SUPER_ADMIN, "security:rotate_secrets")
            is True
        )
        assert (
            EnvironmentGuard.has_permission(
                AccessRole.COMPLIANCE_OFFICER, "model_governance:approve_training"
            )
            is True
        )
        assert EnvironmentGuard.has_permission("invalid_role", "any_perm") is False


class TestEnvironmentGuardTokenAndRoleValidation:
    """Test suite verifying token and role boundary checks across environments."""

    def test_test_token_allowed_in_development_and_testing(self) -> None:
        EnvironmentGuard.validate_token_for_environment("test_token_1", AppEnvironment.DEVELOPMENT)
        EnvironmentGuard.validate_token_for_environment("test_token_42", "testing")
        # Regular token also allowed
        EnvironmentGuard.validate_token_for_environment("sess_real_token_123", "development")

    def test_test_token_rejected_in_production_and_staging(self) -> None:
        with pytest.raises(EnvironmentIsolationError, match="Test token'ları"):
            EnvironmentGuard.validate_token_for_environment(
                "test_token_1", AppEnvironment.PRODUCTION
            )

        with pytest.raises(EnvironmentIsolationError, match="Test token'ları"):
            EnvironmentGuard.validate_token_for_environment("test_token_99", "prod")

        with pytest.raises(EnvironmentIsolationError, match="Test token'ları"):
            EnvironmentGuard.validate_token_for_environment("test_token_5", AppEnvironment.STAGING)

    def test_real_token_allowed_in_production(self) -> None:
        # Standard token passes in production
        EnvironmentGuard.validate_token_for_environment(
            "c8f49a21b4709d3e819b", AppEnvironment.PRODUCTION
        )

    def test_test_only_roles_rejected_in_production(self) -> None:
        for test_role in TEST_ONLY_ROLES:
            with pytest.raises(EnvironmentIsolationError, match="test/geliştirme rolü"):
                EnvironmentGuard.validate_role_for_environment(test_role, AppEnvironment.PRODUCTION)

    def test_unregistered_roles_rejected_in_production(self) -> None:
        with pytest.raises(EnvironmentIsolationError, match="Geçersiz üretim rolü"):
            EnvironmentGuard.validate_role_for_environment(
                "custom_bypass_role", AppEnvironment.PRODUCTION
            )

    def test_valid_roles_accepted_in_production(self) -> None:
        for valid_role in AccessRole:
            EnvironmentGuard.validate_role_for_environment(valid_role, AppEnvironment.PRODUCTION)
            EnvironmentGuard.validate_role_for_environment(valid_role.value, "prod")


class TestProductionSettingsAudit:
    """Test suite auditing settings for production hardening."""

    def test_audit_ignored_for_non_production_environments(self) -> None:
        dev_settings = Settings(
            APP_ENV="development",
            SESSION_SECRET="changeme",
            MARKET_DATA_PROVIDER="mock",
            MARKET_DATA_API_KEY="changeme",
            DATABASE_URL="postgresql://user:password@localhost:5432/borsa_dev",
            CORS_ALLOWED_ORIGINS=["http://localhost:3000"],
        )
        violations = EnvironmentGuard.audit_production_settings(dev_settings)
        assert len(violations) == 0

    def test_audit_flags_insecure_defaults_in_production(self) -> None:
        insecure_prod_settings = Settings(
            APP_ENV="production",
            SESSION_SECRET="changeme",
            MARKET_DATA_PROVIDER="mock",
            MARKET_DATA_API_KEY="changeme",
            DATABASE_URL="postgresql://user:password@localhost:5432/borsa_dev",
            CORS_ALLOWED_ORIGINS=["http://localhost:3000", "*"],
        )
        violations = EnvironmentGuard.audit_production_settings(insecure_prod_settings)
        assert len(violations) >= 4

        violation_codes = {v.code for v in violations}
        assert "INSECURE_SESSION_SECRET" in violation_codes
        assert "MOCK_PROVIDER_IN_PRODUCTION" in violation_codes
        assert "INSECURE_DATABASE_URL" in violation_codes
        assert "INSECURE_CORS_ORIGIN" in violation_codes

        with pytest.raises(
            EnvironmentIsolationError,
            match="Üretim ortamı güvenlik denetimi başarısız",
        ):
            EnvironmentGuard.assert_production_hardened(insecure_prod_settings)

    def test_audit_passes_for_hardened_production_settings(self) -> None:
        hardened_settings = Settings(
            APP_ENV="production",
            SESSION_SECRET="kR8#mN9$vL2@qP5!xZ4^wY7*bT1&hU3(jC6)sE0-fA",
            MARKET_DATA_PROVIDER="bist_official_direct",
            MARKET_DATA_API_KEY="bist_live_prod_key_77a94b",
            DATABASE_URL="postgresql://prod_user:HardenedSecret123@db.internal:5432/borsa_prod",
            CORS_ALLOWED_ORIGINS=["https://app.borsaanaliz.com", "https://borsaanaliz.com"],
        )
        violations = EnvironmentGuard.audit_production_settings(hardened_settings)
        assert violations == []
        EnvironmentGuard.assert_production_hardened(hardened_settings)


class TestFastApiEnvironmentIsolationIntegration:
    """Integration tests verifying token and role isolation via FastAPI test client."""

    @pytest.fixture
    def app(self) -> FastAPI:
        from unittest.mock import MagicMock

        from stock_analysis.db.session import get_db

        test_app = FastAPI()
        test_app.dependency_overrides[get_db] = lambda: MagicMock()

        @test_app.get("/api/v1/profile")
        def get_profile(
            user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
        ) -> dict[str, object]:
            return {"user_id": user.user_id, "email": user.email, "role": user.role}

        analyst_checker = require_roles("analyst", "admin")

        @test_app.get("/api/v1/analyst-only")
        def get_analyst_data(
            user: AuthenticatedUser = Depends(analyst_checker),  # noqa: B008
        ) -> dict[str, object]:
            return {"authorized_role": user.role}

        return test_app

    def test_test_token_succeeds_in_development(self, app: FastAPI) -> None:
        from stock_analysis.config import get_settings

        dev_settings = Settings(APP_ENV="development")
        app.dependency_overrides[get_settings] = lambda: dev_settings

        client = TestClient(app)
        response = client.get("/api/v1/profile", headers={"Authorization": "Bearer test_token_42"})

        assert response.status_code == 200
        assert response.json()["user_id"] == 42
        assert response.json()["email"] == "user_42@example.com"

    def test_test_token_strictly_rejected_in_production(self, app: FastAPI) -> None:
        from stock_analysis.config import get_settings

        prod_settings = Settings(APP_ENV="production")
        app.dependency_overrides[get_settings] = lambda: prod_settings

        client = TestClient(app)
        response = client.get("/api/v1/profile", headers={"Authorization": "Bearer test_token_42"})

        assert response.status_code == 401
        err = response.json()["detail"]
        assert err["code"] == "TEST_TOKEN_FORBIDDEN_IN_PRODUCTION"
        assert "Test token'ları" in err["message"]

    def test_require_roles_rejects_test_only_role_in_production(self, app: FastAPI) -> None:
        from stock_analysis.config import get_settings

        prod_settings = Settings(APP_ENV="production")
        app.dependency_overrides[get_settings] = lambda: prod_settings

        # Override get_current_user to return a mock user with a test-only role
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            user_id=1, email="dev@example.com", role="dev_admin"
        )

        client = TestClient(app)
        auth_hdr = {"Authorization": "Bearer real_session_prod_token"}
        response = client.get("/api/v1/analyst-only", headers=auth_hdr)

        assert response.status_code == 403
        err = response.json()["detail"]
        assert err["code"] == "FORBIDDEN_INVALID_ENVIRONMENT_ROLE"
        assert "dev_admin" in err["message"]
