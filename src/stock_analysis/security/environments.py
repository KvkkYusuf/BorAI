"""Environment isolation and access role segregation module.

Enforces strict separation between production, staging, testing, and development
environments. Prevents test tokens, mock data providers, and insecure configurations
from operating in production environments, and establishes explicit access roles (RBAC).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stock_analysis.config import Settings


class AppEnvironment(StrEnum):
    """Supported application execution environments."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"

    @classmethod
    def from_string(cls, env_name: str | None) -> AppEnvironment:
        """Parse case-insensitive environment string to AppEnvironment enum."""
        if not env_name:
            return cls.DEVELOPMENT
        cleaned = env_name.strip().lower()
        mapping: dict[str, AppEnvironment] = {
            "dev": cls.DEVELOPMENT,
            "development": cls.DEVELOPMENT,
            "test": cls.TESTING,
            "testing": cls.TESTING,
            "pytest": cls.TESTING,
            "stage": cls.STAGING,
            "staging": cls.STAGING,
            "prod": cls.PRODUCTION,
            "production": cls.PRODUCTION,
        }
        if cleaned in mapping:
            return mapping[cleaned]
        raise ValueError(
            f"Geçersiz ortam adı: '{env_name}'. İzin verilenler: "
            f"{', '.join(sorted({e.value for e in cls}))}"
        )

    @property
    def is_production(self) -> bool:
        """Return True if environment is live production."""
        return self == AppEnvironment.PRODUCTION

    @property
    def is_staging(self) -> bool:
        """Return True if environment is staging/pre-production."""
        return self == AppEnvironment.STAGING

    @property
    def is_production_like(self) -> bool:
        """Return True if environment requires production-grade security (prod or stage)."""
        return self in (AppEnvironment.PRODUCTION, AppEnvironment.STAGING)

    @property
    def is_testing(self) -> bool:
        """Return True if environment is automated test execution."""
        return self == AppEnvironment.TESTING

    @property
    def is_development(self) -> bool:
        """Return True if environment is local developer workstation."""
        return self == AppEnvironment.DEVELOPMENT


class AccessRole(StrEnum):
    """Standard access roles enforcing Principle of Least Privilege (PoLP)."""

    GUEST = "guest"
    USER = "user"
    ANALYST = "analyst"
    COMPLIANCE_OFFICER = "compliance_officer"
    ML_ENGINEER = "ml_engineer"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

    @classmethod
    def from_string(cls, role_name: str | None) -> AccessRole:
        """Parse role string to AccessRole enum."""
        if not role_name:
            return cls.GUEST
        cleaned = role_name.strip().lower()
        try:
            return cls(cleaned)
        except ValueError as err:
            allowed = ", ".join(sorted([r.value for r in cls]))
            raise ValueError(f"Geçersiz rol: '{role_name}'. İzin verilenler: {allowed}") from err


class EnvironmentIsolationError(ValueError):
    """Raised when an operation, token, or configuration violates environment boundaries."""


@dataclass(frozen=True)
class RoleScope:
    """Permissions and capabilities associated with an AccessRole."""

    role: AccessRole
    description: str
    permissions: frozenset[str]
    allowed_in_production: bool = True
    requires_mfa_in_production: bool = False


# Canonical Role-Permission Matrix across all tiers
ROLE_SCOPES: dict[AccessRole, RoleScope] = {
    AccessRole.GUEST: RoleScope(
        role=AccessRole.GUEST,
        description="Anonim veya kimliği doğrulanmamış genel ziyaretçi.",
        permissions=frozenset(
            {
                "market_data:read_public",
                "disclaimers:read",
                "system_health:read",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=False,
    ),
    AccessRole.USER: RoleScope(
        role=AccessRole.USER,
        description="Kimliği doğrulanmış standart yatırımcı/araştırmacı hesabı.",
        permissions=frozenset(
            {
                "market_data:read_public",
                "market_data:read_eod",
                "disclaimers:read",
                "watchlists:read_own",
                "watchlists:write_own",
                "alerts:read_own",
                "alerts:write_own",
                "profile:read_own",
                "profile:write_own",
                "export:read_own",
                "delete_account:request_own",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=False,
    ),
    AccessRole.ANALYST: RoleScope(
        role=AccessRole.ANALYST,
        description="İleri düzey teknik analiz, göstergeler ve raporlama erişimi.",
        permissions=frozenset(
            {
                "market_data:read_public",
                "market_data:read_eod",
                "market_data:read_delayed",
                "disclaimers:read",
                "watchlists:read_own",
                "watchlists:write_own",
                "alerts:read_own",
                "alerts:write_own",
                "profile:read_own",
                "profile:write_own",
                "indicators:advanced_compute",
                "backtest:read_runs",
                "reports:export_institutional",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=False,
    ),
    AccessRole.COMPLIANCE_OFFICER: RoleScope(
        role=AccessRole.COMPLIANCE_OFFICER,
        description="SPK mevzuatı, yasal uyarılar, model yönetişimi ve KVKK denetçisi.",
        permissions=frozenset(
            {
                "disclaimers:read",
                "disclaimers:audit",
                "audit_logs:read_all",
                "model_governance:read_all",
                "model_governance:approve_training",
                "model_governance:approve_promotion",
                "alerts:audit_disclaimers",
                "kvkk:audit_consents",
                "security:view_threats",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=True,
    ),
    AccessRole.ML_ENGINEER: RoleScope(
        role=AccessRole.ML_ENGINEER,
        description="PyTorch modellerini eğitme, gölge modunu inceleme ve izleme uzmanı.",
        permissions=frozenset(
            {
                "market_data:read_eod",
                "models:view_metrics",
                "models:trigger_retraining",
                "models:view_shadow_mode",
                "models:request_promotion",
                "models:view_drift_alerts",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=True,
    ),
    AccessRole.ADMIN: RoleScope(
        role=AccessRole.ADMIN,
        description="Sistem yöneticisi; kullanıcı yönetimi ve genel operasyon.",
        permissions=frozenset(
            {
                "users:read_all",
                "users:write_status",
                "audit_logs:read_all",
                "system_health:read",
                "system_settings:read",
                "system_settings:write",
                "market_data:read_all",
                "models:view_all",
                "model_governance:read_all",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=True,
    ),
    AccessRole.SUPER_ADMIN: RoleScope(
        role=AccessRole.SUPER_ADMIN,
        description="Kök yönetici; acil durum kilidi, anahtar rotasyonu ve tam yetki.",
        permissions=frozenset(
            {
                "users:read_all",
                "users:write_status",
                "users:manage_roles",
                "audit_logs:read_all",
                "system_health:read",
                "system_settings:read",
                "system_settings:write",
                "market_data:read_all",
                "models:view_all",
                "model_governance:read_all",
                "security:rotate_secrets",
                "security:revoke_secrets",
                "security:emergency_kill_switch",
            }
        ),
        allowed_in_production=True,
        requires_mfa_in_production=True,
    ),
}

# Roles strictly forbidden in production (mock, test-only, or temporary dev roles)
TEST_ONLY_ROLES = frozenset(
    {
        "test_role",
        "mock_role",
        "dev_admin",
        "unverified_tester",
        "debugger",
    }
)

# Insecure values forbidden as secrets in production
INSECURE_SECRET_PATTERNS = frozenset(
    {
        "changeme",
        "changeme-generate-long-random",
        "secret",
        "default",
        "admin",
        "admin123",
        "password",
        "123456",
        "12345678",
        "root",
    }
)


@dataclass
class ProductionAuditViolation:
    """Individual security configuration violation for production deployment."""

    code: str
    parameter: str
    message: str
    remediation: str


class EnvironmentGuard:
    """Security guard verifying environment boundaries and role segregation."""

    @staticmethod
    def validate_token_for_environment(
        token: str,
        env: AppEnvironment | str,
    ) -> None:
        """Verify that token does not violate environment boundaries.

        Specifically, 'test_token_*' tokens are strictly prohibited in production and staging.
        Raises EnvironmentIsolationError if violated.
        """
        app_env = env if isinstance(env, AppEnvironment) else AppEnvironment.from_string(env)

        if app_env.is_production_like and token.startswith("test_token_"):
            raise EnvironmentIsolationError(
                f"Güvenlik ihlali: Test token'ları ('test_token_*') {app_env.value} "
                "ortamında kesinlikle kullanılamaz. Gerçek kimlik doğrulama gereklidir."
            )

    @staticmethod
    def validate_role_for_environment(
        role: AccessRole | str,
        env: AppEnvironment | str,
    ) -> None:
        """Verify role legitimacy for target environment.

        Raises EnvironmentIsolationError if an unregistered or test-only role
        is passed to production.
        """
        app_env = env if isinstance(env, AppEnvironment) else AppEnvironment.from_string(env)
        raw_role = (role.value if isinstance(role, AccessRole) else role).strip().lower()

        if app_env.is_production_like and raw_role in TEST_ONLY_ROLES:
            raise EnvironmentIsolationError(
                f"Yetki ihlali: '{raw_role}' test/geliştirme rolü {app_env.value} "
                "ortamında kullanılamaz."
            )

        # In production-like environments, role must be recognized within AccessRole
        if app_env.is_production_like:
            try:
                AccessRole.from_string(raw_role)
            except ValueError as err:
                raise EnvironmentIsolationError(
                    f"Geçersiz üretim rolü: '{raw_role}'. "
                    "Üretimde yalnızca resmi RBAC rolleri geçerlidir."
                ) from err

    @staticmethod
    def requires_mfa_in_production(role: AccessRole | str) -> bool:
        """Return True if given role mandates MFA in production environments."""
        try:
            parsed = role if isinstance(role, AccessRole) else AccessRole.from_string(role)
        except ValueError:
            return False
        scope = ROLE_SCOPES.get(parsed)
        return scope.requires_mfa_in_production if scope else False

    @staticmethod
    def get_role_permissions(role: AccessRole | str) -> frozenset[str]:
        """Return canonical set of permissions for a role."""
        try:
            parsed = role if isinstance(role, AccessRole) else AccessRole.from_string(role)
        except ValueError:
            return frozenset()
        scope = ROLE_SCOPES.get(parsed)
        return scope.permissions if scope else frozenset()

    @staticmethod
    def has_permission(role: AccessRole | str, permission: str) -> bool:
        """Check if role possesses specific permission."""
        perms = EnvironmentGuard.get_role_permissions(role)
        return permission in perms

    @classmethod
    def audit_production_settings(
        cls,
        settings: Settings,
    ) -> list[ProductionAuditViolation]:
        """Audit application settings against production hardening requirements.

        Returns list of violations (empty list if production-ready).
        """
        violations: list[ProductionAuditViolation] = []
        app_env = AppEnvironment.from_string(settings.app_env)

        if not app_env.is_production:
            return violations

        # 1. Session Secret Integrity
        raw_secret = getattr(settings, "session_secret", "")
        if not raw_secret or raw_secret.lower() in INSECURE_SECRET_PATTERNS or len(raw_secret) < 32:
            violations.append(
                ProductionAuditViolation(
                    code="INSECURE_SESSION_SECRET",
                    parameter="SESSION_SECRET",
                    message=(
                        "Üretim ortamında oturum sırrı varsayılan veya 32 karakterden kısa olamaz."
                    ),
                    remediation="Güçlü, kriptografik en az 32 karakterlik rastgele sır belirleyin.",
                )
            )

        # 2. Market Data Provider and API Key
        provider = getattr(settings, "market_data_provider", "mock").lower()
        if provider == "mock":
            violations.append(
                ProductionAuditViolation(
                    code="MOCK_PROVIDER_IN_PRODUCTION",
                    parameter="MARKET_DATA_PROVIDER",
                    message="Üretim ortamında 'mock' veri sağlayıcı kullanılamaz.",
                    remediation=(
                        "Resmi lisanslı veri sağlayıcısını "
                        "(örn: bist_official, matriks) yapılandırın."
                    ),
                )
            )

        provider_key = getattr(settings, "market_data_api_key", "").lower()
        if provider != "mock" and provider_key in INSECURE_SECRET_PATTERNS:
            violations.append(
                ProductionAuditViolation(
                    code="INSECURE_PROVIDER_API_KEY",
                    parameter="MARKET_DATA_API_KEY",
                    message=(
                        "Üretim veri sağlayıcı API anahtarı varsayılan değer ('changeme') olamaz."
                    ),
                    remediation=(
                        "Geçerli veri sağlayıcı API anahtarını secret manager'dan yükleyin."
                    ),
                )
            )

        # 3. Database URL Isolation
        db_url = getattr(settings, "database_url", "").lower()
        if "sqlite" in db_url or "borsa_dev" in db_url:
            violations.append(
                ProductionAuditViolation(
                    code="INSECURE_DATABASE_URL",
                    parameter="DATABASE_URL",
                    message=(
                        "Üretim veritabanı SQLite veya geliştirme veritabanı ('borsa_dev') olamaz."
                    ),
                    remediation=(
                        "İzole, yüksek erişilebilirlikli üretim "
                        "PostgreSQL veritabanı URL'si sağlayın."
                    ),
                )
            )

        # 4. CORS Allowlist Boundaries
        cors_origins: list[str] = getattr(settings, "cors_allowed_origins", [])
        for origin in cors_origins:
            norm = origin.strip().lower()
            if norm == "*" or "localhost" in norm or "127.0.0.1" in norm:
                violations.append(
                    ProductionAuditViolation(
                        code="INSECURE_CORS_ORIGIN",
                        parameter="CORS_ALLOWED_ORIGINS",
                        message=f"Üretim ortamında güvensiz CORS kökeni saptandı: '{origin}'.",
                        remediation=(
                            "Wildcard (*) ve localhost kökenlerini kaldırın, "
                            "yalnızca resmi alan adını verin."
                        ),
                    )
                )

        return violations

    @classmethod
    def assert_production_hardened(cls, settings: Settings) -> None:
        """Raise EnvironmentIsolationError if any production audit violation exists."""
        violations = cls.audit_production_settings(settings)
        if violations:
            details = "\n".join(f"- [{v.code}] {v.parameter}: {v.message}" for v in violations)
            raise EnvironmentIsolationError(
                f"Üretim ortamı güvenlik denetimi başarısız ({len(violations)} ihlal):\n{details}"
            )
