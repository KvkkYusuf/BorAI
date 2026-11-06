"""OWASP Top 10 (Web) and OWASP API Security Top 10 controls.

Implements defense-in-depth security validations, BOLA/IDOR enforcement,
mass-assignment protection, unrestricted resource consumption guards,
SSRF prevention, and security control registry.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse


class OwaspTop10Code(StrEnum):
    """OWASP Top 10 2021 standard identifiers."""

    A01_BROKEN_ACCESS_CONTROL = "OWASP-A01"
    A02_CRYPTOGRAPHIC_FAILURES = "OWASP-A02"
    A03_INJECTION = "OWASP-A03"
    A04_INSECURE_DESIGN = "OWASP-A04"
    A05_SECURITY_MISCONFIGURATION = "OWASP-A05"
    A06_VULNERABLE_AND_OUTDATED_COMPONENTS = "OWASP-A06"
    A07_IDENTIFICATION_AND_AUTH_FAILURES = "OWASP-A07"
    A08_SOFTWARE_AND_DATA_INTEGRITY_FAILURES = "OWASP-A08"
    A09_SECURITY_LOGGING_AND_MONITORING_FAILURES = "OWASP-A09"
    A10_SERVER_SIDE_REQUEST_FORGERY = "OWASP-A10"


class ApiSecurityTop10Code(StrEnum):
    """OWASP API Security Top 10 2023 standard identifiers."""

    API1_BOLA = "API-1"
    API2_BROKEN_AUTHENTICATION = "API-2"
    API3_BOPLA = "API-3"
    API4_UNRESTRICTED_RESOURCE_CONSUMPTION = "API-4"
    API5_BFLA = "API-5"
    API6_UNRESTRICTED_ACCESS_SENSITIVE_FLOWS = "API-6"
    API7_SSRF = "API-7"
    API8_SECURITY_MISCONFIGURATION = "API-8"
    API9_IMPROPER_INVENTORY_MANAGEMENT = "API-9"
    API10_UNSAFE_CONSUMPTION_OF_APIS = "API-10"


class SecurityControlStatus(StrEnum):
    """Enforcement status of individual security controls."""

    ENFORCED = "ENFORCED"
    MONITORED = "MONITORED"
    IN_PLACE = "IN_PLACE"


class OwaspSecurityError(Exception):
    """Base exception for all OWASP/API security violations."""


class BolaAccessDeniedError(OwaspSecurityError, PermissionError):
    """Raised when an object-level authorization (BOLA / IDOR) check fails."""


class MassAssignmentError(OwaspSecurityError, ValueError):
    """Raised when request payload contains unauthorized or forbidden object properties."""


class ResourceLimitExceededError(OwaspSecurityError, ValueError):
    """Raised when API consumers exceed pagination, payload or batch resource limits."""


class InjectionDetectedError(OwaspSecurityError, ValueError):
    """Raised when untrusted input contains injection patterns (SQLi, command injection, XSS)."""


class SsrfViolationError(OwaspSecurityError, ValueError):
    """Raised when outbound request destination violates SSRF protection rules."""


class ApiInventoryViolationError(OwaspSecurityError, ValueError):
    """Raised when request path targets unapproved, unversioned, or shadow endpoints."""


@dataclass(frozen=True)
class OwaspControl:
    """Specification of an OWASP or API Security Top 10 control."""

    code: str
    title: str
    standard: str
    description: str
    mitigation: str
    component_reference: str
    status: SecurityControlStatus = SecurityControlStatus.ENFORCED


@dataclass(frozen=True)
class OwaspComplianceSummary:
    """Summary of system compliance against OWASP and API Top 10 standards."""

    total_controls: int
    enforced_count: int
    coverage_ratio: float
    controls: list[OwaspControl]


# ---------------------------------------------------------------------------
# Active Enforcers
# ---------------------------------------------------------------------------


class BolaOwnershipGuard:
    """Enforces Broken Object Level Authorization (BOLA/IDOR) verification."""

    @staticmethod
    def verify_ownership(
        subject_id: int | str,
        owner_id: int | str,
        is_admin: bool = False,
        resource_type: str = "resource",
    ) -> None:
        """Verify that subject_id owns owner_id or has administrator privileges.

        Raises:
            BolaAccessDeniedError: If subject is not owner and not an administrator.
        """
        if is_admin:
            return

        if str(subject_id) != str(owner_id):
            raise BolaAccessDeniedError(
                f"Erisim reddedildi: '{resource_type}' kaynagina erisim yetkisi bulunmuyor."
            )


class ObjectPropertyGuard:
    """Guards against Broken Object Property Level Authorization (BOPLA / Mass Assignment)."""

    FORBIDDEN_INBOUND_FIELDS: frozenset[str] = frozenset(
        {
            "role",
            "is_admin",
            "is_superuser",
            "permissions",
            "password_hash",
            "verified",
            "is_active",
            "tenant_id",
            "created_at",
            "updated_at",
            "id",
        }
    )

    SENSITIVE_OUTBOUND_FIELDS: frozenset[str] = frozenset(
        {
            "password",
            "password_hash",
            "secret_key",
            "session_secret",
            "token",
            "access_token",
            "refresh_token",
            "recovery_token",
            "internal_id",
        }
    )

    @classmethod
    def sanitize_inbound_payload(
        cls,
        payload: dict[str, Any],
        allowed_fields: set[str] | frozenset[str],
        forbidden_fields: set[str] | frozenset[str] | None = None,
        strict_reject: bool = True,
    ) -> dict[str, Any]:
        """Validate and filter inbound request body.

        Args:
            payload: Incoming client dictionary.
            allowed_fields: Whitelist of permissible fields.
            forbidden_fields: Blacklist of prohibited privilege fields.
            strict_reject: If True, raises MassAssignmentError when forbidden fields are present.

        Returns:
            Filtered dictionary containing only allowed keys.
        """
        prohibited = forbidden_fields or cls.FORBIDDEN_INBOUND_FIELDS

        for key in payload:
            if key in prohibited:
                if strict_reject:
                    raise MassAssignmentError(
                        f"Yetkisiz alan enjeksiyonu tespit edildi: '{key}' alani degistirilemez."
                    )

        return {k: v for k, v in payload.items() if k in allowed_fields}

    @classmethod
    def sanitize_outbound_payload(
        cls,
        payload: dict[str, Any],
        sensitive_fields: set[str] | frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Strip sensitive, private, or internal fields from API response payloads."""
        redact_keys = sensitive_fields or cls.SENSITIVE_OUTBOUND_FIELDS
        sanitized: dict[str, Any] = {}

        for k, v in payload.items():
            if k in redact_keys:
                continue
            if isinstance(v, dict):
                sanitized[k] = cls.sanitize_outbound_payload(v, sensitive_fields=redact_keys)
            elif isinstance(v, list):
                sanitized[k] = [
                    cls.sanitize_outbound_payload(item, sensitive_fields=redact_keys)
                    if isinstance(item, dict)
                    else item
                    for item in v
                ]
            else:
                sanitized[k] = v

        return sanitized


class ResourceConsumptionGuard:
    """Enforces API4: Unrestricted Resource Consumption limits."""

    DEFAULT_MAX_PAGE_LIMIT: int = 100
    DEFAULT_MAX_PAYLOAD_BYTES: int = 1_048_576  # 1 MB
    DEFAULT_MAX_BATCH_SIZE: int = 50

    @classmethod
    def validate_pagination(
        cls,
        limit: int,
        offset: int,
        max_limit: int | None = None,
        default_limit: int = 20,
    ) -> tuple[int, int]:
        """Validate and cap pagination parameters.

        Raises:
            ResourceLimitExceededError: If limit or offset values are invalid.
        """
        ceiling = max_limit or cls.DEFAULT_MAX_PAGE_LIMIT

        if offset < 0:
            raise ResourceLimitExceededError("Sayfalama offset degeri negatif olamaz.")
        if limit <= 0:
            limit = default_limit
        if limit > ceiling:
            raise ResourceLimitExceededError(
                f"Istenen sayfa boyutu ({limit}) izin verilen azami siniri ({ceiling}) asamaz."
            )

        return limit, offset

    @classmethod
    def validate_payload_size(
        cls,
        content_length_bytes: int,
        max_bytes: int | None = None,
    ) -> None:
        """Validate request body size in bytes."""
        cap = max_bytes or cls.DEFAULT_MAX_PAYLOAD_BYTES
        if content_length_bytes > cap:
            raise ResourceLimitExceededError(
                f"Istek govdesi boyutu ({content_length_bytes} bayt) "
                f"azami izin verilen siniri ({cap} bayt) asiyor."
            )

    @classmethod
    def validate_batch_size(
        cls,
        items_count: int,
        max_items: int | None = None,
    ) -> None:
        """Validate batch operation size."""
        cap = max_items or cls.DEFAULT_MAX_BATCH_SIZE
        if items_count > cap:
            raise ResourceLimitExceededError(
                f"Toplu islem adedi ({items_count}) azami izin verilen siniri ({cap}) asiyor."
            )


class InjectionGuard:
    """Guards against SQLi, Command Injection, and XSS patterns in untrusted input."""

    _SQL_INJECTION_PATTERNS = [
        re.compile(r"(\bOR\b|\bAND\b)\s+['\"]?1['\"]?\s*=\s*['\"]?1['\"]?", re.IGNORECASE),
        re.compile(r"\bUNION\s+(ALL\s+)?SELECT\b", re.IGNORECASE),
        re.compile(r"(--|/\*|\*/|;\s*DROP\s+TABLE|;\s*DELETE\s+FROM)", re.IGNORECASE),
        re.compile(r"\bEXEC(\s+|\()+", re.IGNORECASE),
    ]

    _SHELL_INJECTION_PATTERNS = [
        re.compile(r"[;&|`$]\s*(curl|wget|bash|sh|rm|cat|nc|python)\b", re.IGNORECASE),
        re.compile(r"\$\([^\)]+\)"),
        re.compile(r"`[^`]+`"),
    ]

    _XSS_PATTERNS = [
        re.compile(r"<\s*script\b[^>]*>", re.IGNORECASE),
        re.compile(r"javascript\s*:", re.IGNORECASE),
        re.compile(r"\bon(error|load|click|mouseover)\s*=", re.IGNORECASE),
        re.compile(r"<\s*iframe\b[^>]*>", re.IGNORECASE),
    ]

    @classmethod
    def assert_safe_sql_param(cls, value: str, field_name: str = "input") -> None:
        """Verify that value does not contain obvious SQL injection fragments."""
        for pattern in cls._SQL_INJECTION_PATTERNS:
            if pattern.search(value):
                raise InjectionDetectedError(
                    f"Guvenlik ihlali: '{field_name}' alaninda "
                    "zararli SQL enjeksiyon deseni tespit edildi."
                )

    @classmethod
    def assert_safe_shell_param(cls, value: str, field_name: str = "input") -> None:
        """Verify that value does not contain command injection fragments."""
        for pattern in cls._SHELL_INJECTION_PATTERNS:
            if pattern.search(value):
                raise InjectionDetectedError(
                    f"Guvenlik ihlali: '{field_name}' alaninda "
                    "komut calistirma deseni tespit edildi."
                )

    @classmethod
    def assert_safe_xss_content(cls, value: str, field_name: str = "input") -> None:
        """Verify that value does not contain cross-site scripting (XSS) payload."""
        for pattern in cls._XSS_PATTERNS:
            if pattern.search(value):
                raise InjectionDetectedError(
                    f"Guvenlik ihlali: '{field_name}' alaninda "
                    "XSS komut dosyasi deseni tespit edildi."
                )

    @classmethod
    def scan_input(cls, value: str, field_name: str = "input") -> None:
        """Run all injection scanners on untrusted string input."""
        cls.assert_safe_sql_param(value, field_name=field_name)
        cls.assert_safe_shell_param(value, field_name=field_name)
        cls.assert_safe_xss_content(value, field_name=field_name)


class SsrfProtectionGuard:
    """Enforces SSRF prevention on external HTTP fetches, webhooks, and provider URLs."""

    ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

    PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
        ipaddress.ip_network("127.0.0.0/8"),  # Loopback
        ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918 Private
        ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918 Private
        ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918 Private
        ipaddress.ip_network("169.254.0.0/16"),  # Link-Local / Cloud Metadata
        ipaddress.ip_network("0.0.0.0/8"),  # Current network
        ipaddress.ip_network("::1/128"),  # IPv6 Loopback
        ipaddress.ip_network("fc00::/7"),  # IPv6 Unique Local
        ipaddress.ip_network("fe80::/10"),  # IPv6 Link-Local
    ]

    @classmethod
    def validate_target_url(
        cls,
        url: str,
        allowed_domains: set[str] | frozenset[str] | None = None,
        allow_private: bool = False,
    ) -> str:
        """Validate target destination URL against SSRF vulnerabilities.

        Raises:
            SsrfViolationError: If URL uses forbidden scheme, private/metadata IP,
                or unapproved domain.
        """
        if not url or not isinstance(url, str):
            raise SsrfViolationError("Gecersiz bos hedef URL.")

        parsed = urlparse(url.strip())
        if not parsed.scheme or parsed.scheme.lower() not in cls.ALLOWED_SCHEMES:
            raise SsrfViolationError(
                f"Yasakli URL semasi '{parsed.scheme}'. Yalnizca HTTP/HTTPS izin verilir."
            )

        hostname = parsed.hostname
        if not hostname:
            raise SsrfViolationError("Hedef URL gecerli bir ana makine adi (hostname) icermelidir.")

        hostname_lower = hostname.lower()

        # Check explicit loopback hostnames
        if hostname_lower in {"localhost", "metadata.google.internal"}:
            if not allow_private:
                raise SsrfViolationError(
                    f"SSRF engellendi: '{hostname}' yerel/dahili ana makinesine erisim yasaktir."
                )

        # Check IP address targets
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        try:
            ip = ipaddress.ip_address(hostname_lower)
        except ValueError:
            # Hostname is a domain name, not a raw IP address
            ip = None

        if ip is not None and not allow_private:
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise SsrfViolationError(
                    f"SSRF engellendi: Dahili/ozel IP adresi '{ip}' erisime kapalidir."
                )
            for net in cls.PRIVATE_NETWORKS:
                if ip in net:
                    raise SsrfViolationError(
                        f"SSRF engellendi: Dahili/ozel IP adresi '{ip}' erisime kapalidir."
                    )

        # Check domain allowlist if specified
        if allowed_domains:
            domain_match = any(
                hostname_lower == domain.lower() or hostname_lower.endswith("." + domain.lower())
                for domain in allowed_domains
            )
            if not domain_match:
                raise SsrfViolationError(
                    f"SSRF engellendi: '{hostname_lower}' "
                    "izin verilen alan adlari listesinde (allowlist) yer almiyor."
                )

        return url


class ApiInventoryGuard:
    """Enforces API9: Improper Inventory Management controls (no shadow/unversioned routes)."""

    ALLOWED_PREFIXES: tuple[str, ...] = (
        "/api/v1/",
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/alerts/unsubscribe",
        "/alerts/consent",
    )

    SHADOW_ROUTE_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"^/(v0|v2|beta|debug|internal|shadow|test|dev)/", re.IGNORECASE),
        re.compile(r"^/admin-unprotected/", re.IGNORECASE),
    ]

    @classmethod
    def validate_route(cls, path: str) -> None:
        """Verify that an endpoint path belongs to the official approved API inventory."""
        normalized = "/" + path.strip("/")
        if path.endswith("/"):
            normalized += "/"

        for pattern in cls.SHADOW_ROUTE_PATTERNS:
            if pattern.search(normalized):
                raise ApiInventoryViolationError(
                    f"Yetkisiz envanter rotasi tespit edildi: '{path}' kayitsiz veya sahte API ucu."
                )

        # Ensure path begins with an approved prefix
        is_approved = any(
            normalized == prefix.rstrip("/") or normalized.startswith(prefix)
            for prefix in cls.ALLOWED_PREFIXES
        )

        if not is_approved:
            raise ApiInventoryViolationError(
                f"Kayitsiz API rotasi: '{path}' resmi API envanterinde tanimli degildir."
            )


# ---------------------------------------------------------------------------
# Control Catalog & Registry
# ---------------------------------------------------------------------------


class OwaspControlRegistry:
    """Registry documenting full adherence to OWASP Top 10 and API Security Top 10 standards."""

    def __init__(self) -> None:
        self._controls: dict[str, OwaspControl] = {}
        self._register_default_controls()

    def register_control(self, control: OwaspControl) -> None:
        """Register a validated security control."""
        self._controls[control.code] = control

    def get_control(self, code: str) -> OwaspControl:
        """Retrieve control specification by identifier."""
        return self._controls[code]

    def list_all(self) -> list[OwaspControl]:
        """List all registered controls."""
        return list(self._controls.values())

    def list_owasp_top_10(self) -> list[OwaspControl]:
        """Filter controls belonging to OWASP Top 10."""
        return [c for c in self._controls.values() if c.code.startswith("OWASP-")]

    def list_api_security_top_10(self) -> list[OwaspControl]:
        """Filter controls belonging to OWASP API Security Top 10."""
        return [c for c in self._controls.values() if c.code.startswith("API-")]

    def evaluate_compliance(self) -> OwaspComplianceSummary:
        """Compute system compliance metrics."""
        all_controls = self.list_all()
        enforced = [c for c in all_controls if c.status == SecurityControlStatus.ENFORCED]
        total = len(all_controls)
        ratio = (len(enforced) / total) if total > 0 else 0.0

        return OwaspComplianceSummary(
            total_controls=total,
            enforced_count=len(enforced),
            coverage_ratio=ratio,
            controls=all_controls,
        )

    def _register_default_controls(self) -> None:
        # --- OWASP Top 10 (Web) Controls ---
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A01_BROKEN_ACCESS_CONTROL,
                title="Broken Access Control",
                standard="OWASP Top 10:2021",
                description="Yetkisiz nesne, fonksiyon ve idari erisimlerin engellenmesi.",
                mitigation=(
                    "Sunucu tarafli RBAC hiyerarsisi (RoleHierarchy), "
                    "BOLA/IDOR sahiplik denetimi ve yetki yukseltme korumasi."
                ),
                component_reference=(
                    "src.stock_analysis.security.audit, "
                    "src.stock_analysis.security.owasp.BolaOwnershipGuard"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A02_CRYPTOGRAPHIC_FAILURES,
                title="Cryptographic Failures",
                standard="OWASP Top 10:2021",
                description="Hassas veri, parola ve sir iletiminde zayif kriptografi riskleri.",
                mitigation=(
                    "Argon2id parola ozetleme, TLS zorunlulugu, "
                    "SecretManager ile 90 gunluk rotasyon ve HMAC imzalama."
                ),
                component_reference=(
                    "src.stock_analysis.security.secrets, src.stock_analysis.security.headers"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A03_INJECTION,
                title="Injection",
                standard="OWASP Top 10:2021",
                description="SQL, komut, LDAP veya XSS enjeksiyon saldirilari.",
                mitigation=(
                    "Parametreli SQLAlchemy/ORM sorgulari, Pydantic sema dogrulama, "
                    "InjectionGuard girdi tarayicilari."
                ),
                component_reference=(
                    "src.stock_analysis.security.owasp.InjectionGuard, "
                    "src.stock_analysis.api.sanitizer"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A04_INSECURE_DESIGN,
                title="Insecure Design",
                standard="OWASP Top 10:2021",
                description="Tasarim asamasi guvenlik aciklari ve sinirlandirilmamis riskler.",
                mitigation=(
                    "STRIDE tehdit modellemesi, model yonetisim yasam dongusu, "
                    "naif baseline kapisi, yasal uyari zorunlulugu."
                ),
                component_reference=(
                    "src.stock_analysis.security.threat_model, src.stock_analysis.models.governance"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A05_SECURITY_MISCONFIGURATION,
                title="Security Misconfiguration",
                standard="OWASP Top 10:2021",
                description=(
                    "Hatali basliklar, acik portlar, varsayilan parolalar veya "
                    "stack trace sizintilari."
                ),
                mitigation=(
                    "Katı CSP ve HSTS basliklari, EnvironmentGuard uretim sertlestirme denetimi, "
                    "genel hata arindirici."
                ),
                component_reference=(
                    "src.stock_analysis.security.headers, src.stock_analysis.security.environments"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A06_VULNERABLE_AND_OUTDATED_COMPONENTS,
                title="Vulnerable and Outdated Components",
                standard="OWASP Top 10:2021",
                description="Guvencesiz veya yapay zeka tarafindan uydurulmus tedarik paketleri.",
                mitigation=(
                    "PackageDependencyAuditor ile sahte paket tespiti, "
                    "uv.lock SHA-256 kilit denetimi ve CI guvenlik taramasi."
                ),
                component_reference="src.stock_analysis.security.threat_model.PackageDependencyAuditor",
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A07_IDENTIFICATION_AND_AUTH_FAILURES,
                title="Identification and Authentication Failures",
                standard="OWASP Top 10:2021",
                description="Kaba kuvvet (brute-force), zayif parola ve oturum ele gecirme.",
                mitigation=(
                    "Progressive lockout, IP karantinasi, HttpOnly Secure SameSite "
                    "cerezleri ve kriptografik oturumlar."
                ),
                component_reference=(
                    "src.stock_analysis.auth.brute_force, src.stock_analysis.auth.session"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A08_SOFTWARE_AND_DATA_INTEGRITY_FAILURES,
                title="Software and Data Integrity Failures",
                standard="OWASP Top 10:2021",
                description="Guvenilmeyen model agirliklari, keyfi kod deserialization.",
                mitigation=(
                    "PyTorch weights_only=True, sifir bagimlilikli Safetensors, "
                    "SHA-256 butunluk kontrolu ve imza dogrulama."
                ),
                component_reference="src.stock_analysis.models.security",
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A09_SECURITY_LOGGING_AND_MONITORING_FAILURES,
                title="Security Logging and Monitoring Failures",
                standard="OWASP Top 10:2021",
                description="Guvenlik olaylarinin kaydedilmemesi veya loglarda hassas veri.",
                mitigation=(
                    "Degismez idari denetim izi (record_admin_audit_event), "
                    "IP ozetleme (hash_client_ip), KVKK e-posta maskelemesi."
                ),
                component_reference=(
                    "src.stock_analysis.security.audit, src.stock_analysis.api.sanitizer"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=OwaspTop10Code.A10_SERVER_SIDE_REQUEST_FORGERY,
                title="Server-Side Request Forgery (SSRF)",
                standard="OWASP Top 10:2021",
                description="Sunucunun dahili aglara veya metadata servislerine yetkisiz istegi.",
                mitigation=(
                    "SsrfProtectionGuard ile ozel/dahili IP bloklama, "
                    "yalnizca HTTP/HTTPS izin listesi ve alan adi filtreleme."
                ),
                component_reference="src.stock_analysis.security.owasp.SsrfProtectionGuard",
            )
        )

        # --- OWASP API Security Top 10 (2023) Controls ---
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API1_BOLA,
                title="Broken Object Level Authorization (BOLA)",
                standard="OWASP API Security Top 10:2023",
                description="API parametrelerindeki kaynak kimliklerinin manipule edilmesi.",
                mitigation=(
                    "BolaOwnershipGuard.verify_ownership ile sunucu tarafli "
                    "kullanici sahipligi dogrulamasi."
                ),
                component_reference="src.stock_analysis.security.owasp.BolaOwnershipGuard",
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API2_BROKEN_AUTHENTICATION,
                title="Broken Authentication",
                standard="OWASP API Security Top 10:2023",
                description="API anahtari ve token guvensizlikleri, tahmin edilebilir belirtecler.",
                mitigation=(
                    "Geliskin token yonetimi, test_token engeli (EnvironmentGuard), "
                    "zayif kimlik tespiti."
                ),
                component_reference=(
                    "src.stock_analysis.security.environments, src.stock_analysis.auth"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API3_BOPLA,
                title="Broken Object Property Level Authorization (BOPLA)",
                standard="OWASP API Security Top 10:2023",
                description="Toplu atama (mass assignment) ile yetkili alanlarin degistirilmesi.",
                mitigation=(
                    "ObjectPropertyGuard ile gelen payloadlarda yetki alanlarinin "
                    "engellenmesi ve yanitlarda sir maskeleme."
                ),
                component_reference="src.stock_analysis.security.owasp.ObjectPropertyGuard",
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API4_UNRESTRICTED_RESOURCE_CONSUMPTION,
                title="Unrestricted Resource Consumption",
                standard="OWASP API Security Top 10:2023",
                description=(
                    "Oran sinirlamasinin olmamasi, sinirsiz sayfalama veya "
                    "asiri buyuk payloadlar ile DoS."
                ),
                mitigation=(
                    "ResourceConsumptionGuard ile sayfalama tavani (max 100), "
                    "1MB payload siniri ve kayan pencere oran sinirlamasi."
                ),
                component_reference=(
                    "src.stock_analysis.security.owasp.ResourceConsumptionGuard, "
                    "src.stock_analysis.alerts.rate_limiter"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API5_BFLA,
                title="Broken Function Level Authorization (BFLA)",
                standard="OWASP API Security Top 10:2023",
                description="Idari fonksiyonlarin yetkisiz kullanicilarca cagrilabilmesi.",
                mitigation=(
                    "RoleHierarchy ile kademeli yetki denetimi, super_admin "
                    "acil kapatma anahtari ve idari rota izolasyonu."
                ),
                component_reference="src.stock_analysis.security.audit.RoleHierarchy",
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API6_UNRESTRICTED_ACCESS_SENSITIVE_FLOWS,
                title="Unrestricted Access to Sensitive Business Flows",
                standard="OWASP API Security Top 10:2023",
                description="Otomatik botlarin pazar tarayicilarini veya alarmlari suistimali.",
                mitigation=(
                    "Sembol bazli cooldown, gunluk bildirim tavanlari, "
                    "token-bucket algoritmasi ve insan onay adimlari."
                ),
                component_reference=(
                    "src.stock_analysis.alerts.rate_limiter, src.stock_analysis.models.governance"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API7_SSRF,
                title="Server Side Request Forgery",
                standard="OWASP API Security Top 10:2023",
                description="Webhook veya dis veri saglayici entegrasyonlarinda SSRF saldirilari.",
                mitigation=(
                    "SsrfProtectionGuard ile ozel ag tecriti, "
                    "allowlist alan adi filtrelemesi ve sema zorunlulugu."
                ),
                component_reference="src.stock_analysis.security.owasp.SsrfProtectionGuard",
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API8_SECURITY_MISCONFIGURATION,
                title="Security Misconfiguration",
                standard="OWASP API Security Top 10:2023",
                description="Gereksiz HTTP metodlari, CORS zafiyetleri ve acik hata mesajlari.",
                mitigation=(
                    "CORS allowlist, SecurityHeadersMiddleware, safe_error_message arindiricilari."
                ),
                component_reference=(
                    "src.stock_analysis.security.headers, src.stock_analysis.api.sanitizer"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API9_IMPROPER_INVENTORY_MANAGEMENT,
                title="Improper Inventory Management",
                standard="OWASP API Security Top 10:2023",
                description="Eski/golge (shadow/deprecated) API surumlerinin acik birakilmasi.",
                mitigation=(
                    "ApiInventoryGuard ile onayli /api/v1 rotalari denetimi, "
                    "golge rota engelleme ve versiyon yonetimi."
                ),
                component_reference=(
                    "src.stock_analysis.security.owasp.ApiInventoryGuard, "
                    "src.stock_analysis.api.versioning"
                ),
            )
        )
        self.register_control(
            OwaspControl(
                code=ApiSecurityTop10Code.API10_UNSAFE_CONSUMPTION_OF_APIS,
                title="Unsafe Consumption of APIs",
                standard="OWASP API Security Top 10:2023",
                description="Dis piyasa veri saglayicilarindan gelen veriye guvenilmesi.",
                mitigation=(
                    "Saglayici yanitlarinda Pydantic sema dogrulamasi, "
                    "veri gecikmesi bayragi (15 dk gecikmeli) ve guvenli fallback."
                ),
                component_reference=(
                    "src.stock_analysis.pipeline.validation, src.stock_analysis.models.fallback"
                ),
            )
        )


_DEFAULT_OWASP_REGISTRY: OwaspControlRegistry | None = None


def get_default_owasp_registry() -> OwaspControlRegistry:
    """Retrieve singleton instance of OWASP security control registry."""
    global _DEFAULT_OWASP_REGISTRY
    if _DEFAULT_OWASP_REGISTRY is None:
        _DEFAULT_OWASP_REGISTRY = OwaspControlRegistry()
    return _DEFAULT_OWASP_REGISTRY


def reset_default_owasp_registry() -> None:
    """Reset the global OWASP registry (useful for testing)."""
    global _DEFAULT_OWASP_REGISTRY
    _DEFAULT_OWASP_REGISTRY = None
