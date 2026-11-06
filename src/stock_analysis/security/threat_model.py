"""Threat modeling and supply chain security controls.

Provides STRIDE threat catalog, registry verification, and dependency auditing
to detect supply chain risks and AI-hallucinated packages.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ThreatCategory(StrEnum):
    """Primary threat categories aligned with FAZ-13 specifications."""

    ACCOUNT_TAKEOVER = "account_takeover"
    DATA_LEAKAGE = "data_leakage"
    API_ABUSE = "api_abuse"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    SUPPLY_CHAIN = "supply_chain"
    TAMPERING = "tampering"
    DENIAL_OF_SERVICE = "denial_of_service"


class STRIDEMapping(StrEnum):
    """STRIDE threat classification mapping."""

    SPOOFING = "Spoofing"
    TAMPERING = "Tampering"
    REPUDIATION = "Repudiation"
    INFORMATION_DISCLOSURE = "Information Disclosure"
    DENIAL_OF_SERVICE = "Denial of Service"
    ELEVATION_OF_PRIVILEGE = "Elevation of Privilege"


class SeverityLevel(StrEnum):
    """Threat severity levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class MitigationStatus(StrEnum):
    """Status of defensive mitigation implementation."""

    IMPLEMENTED = "IMPLEMENTED"
    PLANNED = "PLANNED"
    IN_REVIEW = "IN_REVIEW"


@dataclass(frozen=True)
class ThreatEntry:
    """Individual security threat specification."""

    threat_id: str
    name: str
    category: ThreatCategory
    stride: STRIDEMapping
    severity: SeverityLevel
    description: str
    attack_vector: str
    impact: str
    mitigations: tuple[str, ...]
    status: MitigationStatus
    verification_component: str


class ThreatModelRegistry:
    """Registry managing architecture threats, coverage guarantees, and export."""

    def __init__(self) -> None:
        self._threats: dict[str, ThreatEntry] = {}

    def register(self, threat: ThreatEntry) -> None:
        """Register a new threat entry with unique ID validation."""
        if threat.threat_id in self._threats:
            raise ValueError(f"Threat ID '{threat.threat_id}' already registered.")
        self._threats[threat.threat_id] = threat

    def get(self, threat_id: str) -> ThreatEntry:
        """Retrieve threat entry by ID."""
        if threat_id not in self._threats:
            raise KeyError(f"Threat '{threat_id}' not found in registry.")
        return self._threats[threat_id]

    def all_threats(self) -> list[ThreatEntry]:
        """Return all registered threats."""
        return list(self._threats.values())

    def filter_by_category(self, category: ThreatCategory) -> list[ThreatEntry]:
        """Return threats filtered by category."""
        return [t for t in self._threats.values() if t.category == category]

    def filter_by_severity(self, severity: SeverityLevel) -> list[ThreatEntry]:
        """Return threats filtered by severity."""
        return [t for t in self._threats.values() if t.severity == severity]

    def validate_required_coverage(self) -> dict[ThreatCategory, int]:
        """Validate that all mandatory FAZ-13 threat categories are addressed.

        Required categories:
        - ACCOUNT_TAKEOVER
        - DATA_LEAKAGE
        - API_ABUSE
        - PRIVILEGE_ESCALATION
        - SUPPLY_CHAIN
        """
        required = [
            ThreatCategory.ACCOUNT_TAKEOVER,
            ThreatCategory.DATA_LEAKAGE,
            ThreatCategory.API_ABUSE,
            ThreatCategory.PRIVILEGE_ESCALATION,
            ThreatCategory.SUPPLY_CHAIN,
        ]

        counts: dict[ThreatCategory, int] = {}
        missing: list[str] = []

        for cat in required:
            matching = self.filter_by_category(cat)
            counts[cat] = len(matching)
            if not matching:
                missing.append(cat.value)
            else:
                implemented = [t for t in matching if t.status == MitigationStatus.IMPLEMENTED]
                if not implemented:
                    missing.append(f"{cat.value} (no implemented mitigation)")

        if missing:
            raise ValueError(
                f"Threat model coverage incomplete. Deficiencies: {', '.join(missing)}"
            )

        return counts

    def generate_markdown_table(self) -> str:
        """Generate a GitHub-flavored Markdown table for docs/SECURITY.md."""
        header = (
            "| Tehdit Kodu | Kategori | STRIDE | Şiddet | Tehdit & Saldırı Vektörü | "
            "Alınan Önlemler | Durum | Doğrulama Bileşeni |"
        )
        lines = [header, "|---|---|---|---|---|---|---|---|"]
        for t in self.all_threats():
            mitigations_str = "<br>".join(f"• {m}" for m in t.mitigations)
            lines.append(
                f"| `{t.threat_id}` | {t.category.value} | {t.stride.value} "
                f"| **{t.severity.value}** | **{t.name}**: {t.attack_vector} "
                f"| {mitigations_str} | {t.status.value} "
                f"| `{t.verification_component}` |"
            )
        return "\n".join(lines)


class SupplyChainSecurityError(Exception):
    """Raised when dependency or supply chain integrity validation fails."""


@dataclass
class SupplyChainAuditResult:
    """Outcome of package dependency security auditing."""

    is_valid: bool
    total_dependencies: int
    inspected_packages: list[str] = field(default_factory=list)
    flagged_issues: list[str] = field(default_factory=list)
    suspicious_packages: list[str] = field(default_factory=list)


class PackageDependencyAuditor:
    """Audits project configuration files to detect hallucinated or unsafe packages."""

    PACKAGE_NAME_REGEX = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.IGNORECASE)

    SUSPICIOUS_PATTERNS = [
        re.compile(r"^fast-api", re.IGNORECASE),
        re.compile(r"^fastapi-jwt-auth-v2", re.IGNORECASE),
        re.compile(r"^torch-gpu", re.IGNORECASE),
        re.compile(r"^pytorch-cuda", re.IGNORECASE),
        re.compile(r"^pydantic-v3", re.IGNORECASE),
        re.compile(r"^sqlalchemy2-stubs", re.IGNORECASE),
        re.compile(r"^reqeusts", re.IGNORECASE),
        re.compile(r"^bist-api-official", re.IGNORECASE),
    ]

    def __init__(self, allowed_packages: set[str] | None = None) -> None:
        self.allowed_packages = allowed_packages

    @staticmethod
    def parse_package_name(spec: str) -> str:
        """Extract base package name from PEP 508 requirement specification."""
        clean = re.sub(r"\[.*?\]", "", spec).strip()
        parts = re.split(r"[<>=~!~;@\s]", clean, maxsplit=1)
        pkg = parts[0].strip() if parts else clean
        return pkg.lower()

    def audit_pyproject(
        self,
        pyproject_path: Path,
        lockfile_path: Path | None = None,
    ) -> SupplyChainAuditResult:
        """Audit pyproject.toml dependencies and cross-check against lockfile."""
        if not pyproject_path.exists():
            raise FileNotFoundError(f"pyproject.toml not found at: {pyproject_path}")

        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            raise SupplyChainSecurityError(f"Failed to parse pyproject.toml: {e}") from e

        project_section: dict[str, Any] = data.get("project", {})
        raw_deps: list[str] = project_section.get("dependencies", [])

        dep_groups: dict[str, list[str]] = data.get("dependency-groups", {})
        for group_deps in dep_groups.values():
            if isinstance(group_deps, list):
                raw_deps.extend(group_deps)

        inspected: list[str] = []
        issues: list[str] = []
        suspicious: list[str] = []

        for dep_spec in raw_deps:
            pkg_name = self.parse_package_name(dep_spec)
            inspected.append(pkg_name)

            if not self.PACKAGE_NAME_REGEX.match(pkg_name):
                issues.append(f"Invalid package naming syntax: '{pkg_name}'")

            for pattern in self.SUSPICIOUS_PATTERNS:
                if pattern.search(pkg_name):
                    msg = (
                        f"Potential hallucinated package detected: '{pkg_name}' "
                        f"(matches {pattern.pattern})"
                    )
                    issues.append(msg)
                    suspicious.append(pkg_name)

            if self.allowed_packages and pkg_name not in self.allowed_packages:
                msg = f"Package '{pkg_name}' is not in approved allowlist."
                issues.append(msg)
                suspicious.append(pkg_name)

        if lockfile_path is not None:
            if not lockfile_path.exists():
                issues.append(f"Expected lockfile missing: {lockfile_path}")
            else:
                try:
                    with open(lockfile_path, "rb") as f:
                        lock_data = tomllib.load(f)
                    lock_packages = [
                        p.get("name", "").lower()
                        for p in lock_data.get("package", [])
                        if isinstance(p, dict)
                    ]
                    for pkg in inspected:
                        if pkg not in lock_packages and pkg != "torch":
                            issues.append(
                                f"Direct dependency '{pkg}' missing from lockfile "
                                f"({lockfile_path.name})"
                            )
                except Exception as e:
                    issues.append(f"Error reading lockfile {lockfile_path}: {e}")

        is_valid = len(issues) == 0
        return SupplyChainAuditResult(
            is_valid=is_valid,
            total_dependencies=len(inspected),
            inspected_packages=inspected,
            flagged_issues=issues,
            suspicious_packages=suspicious,
        )


def get_default_threat_registry() -> ThreatModelRegistry:
    """Construct and return the standardized threat model registry for the architecture."""
    registry = ThreatModelRegistry()

    threats = [
        # 1. Hesap Ele Geçirme (Account Takeover)
        ThreatEntry(
            threat_id="THR-ATO-01",
            name="Brute-Force & Credential Stuffing",
            category=ThreatCategory.ACCOUNT_TAKEOVER,
            stride=STRIDEMapping.SPOOFING,
            severity=SeverityLevel.HIGH,
            description="Otomatik kimlik denemeleriyle kullanıcı hesabını ele geçirme girişimi.",
            attack_vector="Login API ucuna yüksek frekanslı sahte kimlik ve parola denemeleri.",
            impact="Kullanıcı hesabının yetkisiz ele geçirilmesi ve kişisel verilere erişim.",
            mitigations=(
                "Argon2id parola özetleme ile donanım kaba kuvvet direnci",
                "IP ve hesap bazlı aşamalı kilitlenme (progressive lockout)",
                "Fail2ban tarzı şüpheli aktivite algılama ve IP karantinası",
                "MFA / Passkey (WebAuthn) desteği",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.auth.brute_force",
        ),
        ThreatEntry(
            threat_id="THR-ATO-02",
            name="Session Hijacking & Token Theft",
            category=ThreatCategory.ACCOUNT_TAKEOVER,
            stride=STRIDEMapping.SPOOFING,
            severity=SeverityLevel.HIGH,
            description="Oturum belirteçlerinin ağdan veya istemciden ele geçirilmesi.",
            attack_vector="XSS, MitM veya tarayıcı geçmişi üzerinden oturum çerezinin çalınması.",
            impact="Aktif kullanıcı oturumu üzerinden yetkisiz işlem yapılması.",
            mitigations=(
                "HttpOnly, Secure ve SameSite=Lax/Strict çerez bayrakları",
                "Kriptografik oturum kimliği (UUIDv4 + HMAC imzalama)",
                "Oturum süresi (TTL) ve açık çıkışta sunucu taraflı anında iptal",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.auth.session",
        ),
        # 2. Veri Sızıntısı (Data Leakage)
        ThreatEntry(
            threat_id="THR-LEAK-01",
            name="PII / Token / Secret Exposure in Logs",
            category=ThreatCategory.DATA_LEAKAGE,
            stride=STRIDEMapping.INFORMATION_DISCLOSURE,
            severity=SeverityLevel.CRITICAL,
            description="Log dosyalarına veya bildirimlere parola, token veya e-posta sızması.",
            attack_vector="Hata fırlatma veya debug sırasında nesnelerin doğrudan loga yazılması.",
            impact="KVKK / GDPR ihlali, sızan anahtarlarla sistem dışı servislerin ihlali.",
            mitigations=(
                "Log ve bildirimlerde e-posta ve isim maskeleme (a***@domain.com)",
                "Hata yanıtlarında stack trace, SQL ve sır ifşasını engelleyen yakalayıcı",
                "Gitleaks ve CI secret tarayıcı kontrolleri",
                "SensitiveDataLeakError fırlatan bildirim temizleyici motoru",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.alerts.email_template",
        ),
        ThreatEntry(
            threat_id="THR-LEAK-02",
            name="IDOR on Watchlists / Alerts",
            category=ThreatCategory.DATA_LEAKAGE,
            stride=STRIDEMapping.INFORMATION_DISCLOSURE,
            severity=SeverityLevel.HIGH,
            description="Kullanıcıların başkalarına ait izleme listesi/uyarıları sorgulaması.",
            attack_vector="API parametrelerindeki UUID/ID değiştirerek başka verileri çekme.",
            impact="Kullanıcı gizliliğinin ve yatırım/izleme tercihlerinin ifşası.",
            mitigations=(
                "Her veritabanı sorgusunda aktif kullanıcı kimliği ile sahiplik doğrulama",
                "Yetkisiz erişim denemelerinde 404/403 dönerek varlık ifşasını engelleme",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.api.watchlists",
        ),
        ThreatEntry(
            threat_id="THR-SEC-01",
            name="Stale Secrets & Key Compromise Without Rotation",
            category=ThreatCategory.DATA_LEAKAGE,
            stride=STRIDEMapping.INFORMATION_DISCLOSURE,
            severity=SeverityLevel.HIGH,
            description=(
                "Statik, uzun ömürlü sırların sızması veya döndürülmemesi "
                "nedeniyle yetkisiz sistem erişimi."
            ),
            attack_vector=(
                "Eski API anahtarlarının veya session sırlarının çalınması "
                "ve süresiz geçerli kalması."
            ),
            impact="Yetkisiz API kullanımı, veri sızıntısı ve kimlik taklidi.",
            mitigations=(
                "SecretManager ile çoklu versiyonlu sır takibi ve SecretValue maskelemesi",
                "Düzenli rotasyon periyodu (90 gün) ve otomatik denetim (audit_rotations)",
                "İki aşamalı grace-period ile kesintisiz anahtar rotasyonu ve acil revoke desteği",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.security.secrets",
        ),
        # 3. API Abuse (API Kötüye Kullanımı & DoS)
        ThreatEntry(
            threat_id="THR-API-01",
            name="API Abuse & Volumetric Endpoint Scraping",
            category=ThreatCategory.API_ABUSE,
            stride=STRIDEMapping.DENIAL_OF_SERVICE,
            severity=SeverityLevel.HIGH,
            description="API uç noktalarının botlar tarafından aşırı yüklenmesi veya kazınması.",
            attack_vector="Teknik analiz veya arama uç noktalarına paralel yoğun istekler.",
            impact="Hizmet kesintisi, veritabanı kilitlenmesi ve yasal veri kotasının tükenmesi.",
            mitigations=(
                "Kullanıcı ve IP bazlı kayan pencere oran sınırlaması (Rate Limiting)",
                "Bildirimlerde sembol ve kural bazlı cooldown ve sessiz saatler denetimi",
                "Hafıza/Redis tabanlı token-bucket hız denetleyicileri",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.alerts.rate_limiter",
        ),
        ThreatEntry(
            threat_id="THR-API-02",
            name="Market Provider Flooding & N+1 Latency Degradation",
            category=ThreatCategory.API_ABUSE,
            stride=STRIDEMapping.DENIAL_OF_SERVICE,
            severity=SeverityLevel.MEDIUM,
            description="Piyasa veri sağlayıcısına tekil sorgularla N+1 yük bindirilmesi.",
            attack_vector="Her sembol için ayrı ayrı sağlayıcı API'si çağrılarak kota aşımı.",
            impact="Borsa verilerinin güncellenememesi ve bayat veri sunulması.",
            mitigations=(
                "Toplu veri çekme mimarisi (UniverseScanner batch fetch)",
                "Yerel önbellekleme ve sağlayıcı gecikme bayrağı (15 dk gecikmeli/anlık)",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.alerts.scanner",
        ),
        # 4. Yetki Yükseltme (Privilege Escalation)
        ThreatEntry(
            threat_id="THR-ELEV-01",
            name="Admin Privilege Escalation & Role Bypassing",
            category=ThreatCategory.PRIVILEGE_ESCALATION,
            stride=STRIDEMapping.ELEVATION_OF_PRIVILEGE,
            severity=SeverityLevel.CRITICAL,
            description="Standart kullanıcının yönetici yetkilerine/onay kapılarına erişmesi.",
            attack_vector="Manipüle edilen rol parametreleri veya yetkisiz admin rotaları.",
            impact="Kötü amaçlı modelin yayına alınması, sistem parametrelerinin bozulması.",
            mitigations=(
                "Sunucu taraflı katı rol tabanlı erişim kontrolü (RBAC)",
                "İki aşamalı insan onayı ve model yaşam döngüsü doğrulaması",
                "Tüm kritik yönetici ve model işlemlerinin denetim kaydına yazılması",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.models.governance",
        ),
        ThreatEntry(
            threat_id="THR-ELEV-02",
            name="LLM Prompt Injection & Directives Override",
            category=ThreatCategory.PRIVILEGE_ESCALATION,
            stride=STRIDEMapping.TAMPERING,
            severity=SeverityLevel.HIGH,
            description="Kullanıcı girdisiyle kuralların delinerek tavsiye ürettirilmesi.",
            attack_vector="Haber başlığı veya analiz içine 'Kuralları unut' enjekte etme.",
            impact="SPK mevzuat ihlali, yanıltıcı finansal yönlendirme.",
            mitigations=(
                "Girdi-çıktı prompt izolasyonu ve XML-etiketli sınırlayıcılar",
                "35+ yasaklı al/sat/hedef fiyat ifadesini engelleyen sıfır tolerans",
                "Kural ihlalinde deterministik şablon fallback devreye alınması",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.models.llm_explainer",
        ),
        # 5. Tedarik Zinciri Saldırısı (Supply Chain & Agent Hallucinations)
        ThreatEntry(
            threat_id="THR-SUP-01",
            name="AI-Hallucinated / Typo-Squatted Package Injection",
            category=ThreatCategory.SUPPLY_CHAIN,
            stride=STRIDEMapping.TAMPERING,
            severity=SeverityLevel.CRITICAL,
            description="Yapay zeka asistanı tarafından hayali/zararlı paketin eklenmesi.",
            attack_vector="Kod tamamlama veya otomatik öneriyle pyproject.toml'a paket yazılması.",
            impact="Tedarik zinciri saldırısıyla geliştirici veya sunucuda zararlı kod çalışması.",
            mitigations=(
                "PackageDependencyAuditor ile otomatik isim ve şüpheli paket taraması",
                "uv.lock kilit dosyasında SHA-256 hash ve CI'da uv lock --check zorunluluğu",
                "Yeni bağımlılık eklemede zorunlu insan onayı kuralı",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.security.threat_model",
        ),
        ThreatEntry(
            threat_id="THR-SUP-02",
            name="Malicious Model Weights & Arbitrary Code Deserialization",
            category=ThreatCategory.SUPPLY_CHAIN,
            stride=STRIDEMapping.TAMPERING,
            severity=SeverityLevel.CRITICAL,
            description="PyTorch model dosyasından pickle exploit ile RCE tetiklenmesi.",
            attack_vector="Pickle __reduce__ exploit barındıran model ağırlıklarının yüklenmesi.",
            impact="Sunucu üzerinde yetkisiz komut çalıştırma ve tam ele geçirme.",
            mitigations=(
                "torch.load için weights_only=True zorunluluğu",
                "Güvenli Safetensors biçimi kullanımı",
                "Model dosyalarında SHA-256 bütünlük kontrolü ve DoS taraması",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.models.security",
        ),
        # 6. Ortam İzolasyonu ve Erişim Rolleri Ayrımı (Environment Isolation & RBAC)
        ThreatEntry(
            threat_id="THR-ENV-01",
            name="Environment Bleed & Test Credential Abuse in Production",
            category=ThreatCategory.PRIVILEGE_ESCALATION,
            stride=STRIDEMapping.ELEVATION_OF_PRIVILEGE,
            severity=SeverityLevel.CRITICAL,
            description=(
                "Test/mock token'larının veya geliştirme rollerinin "
                "üretim ortamında yetki aşımına yol açması."
            ),
            attack_vector=(
                "Üretim API uçlarına 'test_token_*' veya 'test_role' gibi "
                "sahte kimliklerle istek gönderme."
            ),
            impact="Kimlik doğrulama baypas edilerek yetkisiz verilere erişilmesi.",
            mitigations=(
                "EnvironmentGuard ile test_token_* belirteçlerinin engellenmesi",
                "Üretimde kayıt dışı veya test-only rollerin (dev_admin) reddedilmesi",
                "Yönetici ve mevzuat rollerinde üretimde zorunlu MFA denetimi",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.security.environments",
        ),
        ThreatEntry(
            threat_id="THR-ENV-02",
            name="Insecure Production Configuration & Mock Provider Leak",
            category=ThreatCategory.TAMPERING,
            stride=STRIDEMapping.TAMPERING,
            severity=SeverityLevel.HIGH,
            description=(
                "Üretim ortamında mock sağlayıcı veya güvensiz yapılandırma çalıştırılması."
            ),
            attack_vector=(
                "Üretim dağıtımında sahte sağlayıcı veya varsayılan sırların bırakılması."
            ),
            impact="Gerçek dışı veri sunumu, oturum taklidi ve veritabanı sızıntısı.",
            mitigations=(
                "EnvironmentGuard.audit_production_settings ile üretime hazır olma kontrolü",
                "Üretimde mock veri sağlayıcı, localhost CORS ve varsayılan sırların yasaklanması",
                "SQLite ve geliştirme veritabanı (borsa_dev) bağlantılarının engellenmesi",
            ),
            status=MitigationStatus.IMPLEMENTED,
            verification_component="src.stock_analysis.security.environments",
        ),
    ]

    for t in threats:
        registry.register(t)

    return registry
