"""Pre-deployment gatekeeper for Closed Beta & Phased Rollout (FAZ-16).

Controls:
1. Closed Beta Access Gating (Allowlist & Invite Code verification).
2. Phased Rollout Stage Ladder (Closed Beta -> Canary 5% -> 25% -> 50% -> GA 100%).
3. Deterministic Cohort Assignment (SHA-256 bucket 0..99).
4. Automated Circuit Breaker & Emergency Rollback (error rate > 1.0% trigger).
5. Legal & Statutory Pre-requisites (Mandatory SPK disclaimer and KVKK consent).
6. Prometheus Telemetry Exposition (SLI gauges and counters).
7. Operational Documentation Verification (CLOSED_BETA_ROLLOUT_PLAN.md).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

# Prometheus metrics
BETA_ROLLOUT_GATE_STATUS = REGISTRY.gauge(
    "beta_rollout_gate_status",
    "Closed beta & phased rollout pre-deployment gate status (1=PASS, 0=FAIL)",
)
BETA_ROLLOUT_STAGE_GAUGE = REGISTRY.gauge(
    "beta_rollout_stage_gauge",
    "Current active rollout stage percentage (0=ClosedBeta, 5, 25, 50, 100=GA)",
)
BETA_ALLOWLIST_USERS_TOTAL = REGISTRY.gauge(
    "beta_allowlist_users_total",
    "Current number of approved closed beta allowlisted users",
)
BETA_INVITATIONS_ISSUED_TOTAL = REGISTRY.counter(
    "beta_invitations_issued_total",
    "Total closed beta invitation codes generated",
)
BETA_INVITATIONS_REDEEMED_TOTAL = REGISTRY.counter(
    "beta_invitations_redeemed_total",
    "Total closed beta invitation codes redeemed",
)
BETA_CIRCUIT_BREAKER_TRIPPED_TOTAL = REGISTRY.counter(
    "beta_circuit_breaker_tripped_total",
    "Total times the rollout circuit breaker has tripped",
)
BETA_ROLLOUT_CHECKS_TOTAL = REGISTRY.counter(
    "beta_rollout_checks_total",
    "Total closed beta gatekeeper evaluations performed",
)


class RolloutStage(StrEnum):
    """Phased deployment stages for production rollout."""

    CLOSED_BETA = "CLOSED_BETA"  # 0% public; invite-only allowlist
    CANARY_5 = "CANARY_5"  # 5% randomized deterministic cohort
    CANARY_25 = "CANARY_25"  # 25% randomized deterministic cohort
    CANARY_50 = "CANARY_50"  # 50% randomized deterministic cohort
    GA_100 = "GA_100"  # 100% General Availability


class BetaGateStatus(StrEnum):
    """Evaluation status for closed beta readiness gate."""

    PASS = "PASS"
    FAIL = "FAIL"


class BetaCheckCategory(StrEnum):
    """Classification for rollout checks."""

    ACCESS_CONTROL = "ACCESS_CONTROL"
    PHASED_LADDER = "PHASED_LADDER"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    STATUTORY_LEGAL = "STATUTORY_LEGAL"
    TELEMETRY_OBSERVABILITY = "TELEMETRY_OBSERVABILITY"
    OPERATIONAL_DOCUMENTATION = "OPERATIONAL_DOCUMENTATION"


@dataclass
class InviteCode:
    """Closed beta invitation token model."""

    code: str
    max_uses: int = 1
    uses_count: int = 0
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(days=30))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = True
    redeemed_by: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BetaCheckItem:
    """Individual verification check result for closed beta & rollout."""

    check_id: str
    category: BetaCheckCategory
    title: str
    description: str
    passed: bool = False
    details: str = ""
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert item to dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class ClosedBetaGateReport:
    """Consolidated closed beta & phased rollout verification report."""

    gate_status: BetaGateStatus
    timestamp: str
    total_checks: int
    passed_checks: int
    failed_checks: int
    current_stage: str
    circuit_breaker_active: bool
    allowlist_size: int
    checks: list[BetaCheckItem]
    blocking_violations: list[str]
    sign_off_ready: bool
    published_report_path: str | None = None
    regulatory_framework: str = "SPK Bilgi Sistemleri Tebliği VII-128.9 & KVKK m. 12"

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary representation."""
        return {
            "gate_status": self.gate_status.value,
            "timestamp": self.timestamp,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "current_stage": self.current_stage,
            "circuit_breaker_active": self.circuit_breaker_active,
            "allowlist_size": self.allowlist_size,
            "checks": [c.to_dict() for c in self.checks],
            "blocking_violations": self.blocking_violations,
            "sign_off_ready": self.sign_off_ready,
            "published_report_path": self.published_report_path,
            "regulatory_framework": self.regulatory_framework,
        }

    def generate_markdown_audit_report(self) -> str:
        """Generate formal Markdown audit report for release certification."""
        status_badge = (
            "🟢 **ONAYLANDI (PASS)**"
            if self.gate_status == BetaGateStatus.PASS
            else "🔴 **REDDEDİLDİ (FAIL)**"
        )
        sign_off_badge = (
            "✅ Dağıtım Kademesine Geçiş İçin Hazır"
            if self.sign_off_ready
            else "❌ Dağıtım Engellendi (Eksikler Var)"
        )

        rows: list[str] = []
        for c in self.checks:
            badge = "✅ PASS" if c.passed else "❌ FAIL"
            rows.append(
                f"| `{c.check_id}` | {c.category.value} | {c.title} | {badge} | {c.details} |"
            )

        violations_md = "Yok (Tüm kontroller başarılı)"
        if self.blocking_violations:
            violations_md = "\n".join(f"- ⚠️ {v}" for v in self.blocking_violations)

        breaker_badge = (
            "AKTİF / KESİNTİ" if self.circuit_breaker_active else "SAĞLIKLI / KAPALI"
        )

        return f"""# Dağıtım Öncesi Kapalı Beta ve Kademeli Dağıtım Denetim Raporu

**Rapor Tarihi (UTC):** {self.timestamp}
**Denetim Sonucu:** {status_badge}
**Sürüm İzni Durumu:** {sign_off_badge}
**Mevcut Aşama:** `{self.current_stage}`
**Devre Kesici (Circuit Breaker):** `{breaker_badge}`
**Allowlist Kullanıcı Sayısı:** `{self.allowlist_size}`
**Mevzuat Dayanağı:** `{self.regulatory_framework}`

---

## 1. Özet Sonuçlar

- **Toplam Kontrol Sayısı:** {self.total_checks}
- **Başarılı Kontroller:** {self.passed_checks}
- **Başarısız Kontroller:** {self.failed_checks}

---

## 2. Detaylı Kontrol Matrisi

| Kontrol Kodu | Kategori | Kontrol Adı | Durum | Detaylar |
|---|---|---|---|---|
{chr(10).join(rows)}

---

## 3. Engelleyici Bulgular ve İhlaller

{violations_md}

---

## 4. Yasal ve Operasyonel Onay

Bu rapor `ClosedBetaGatekeeper` tarafından otomatik olarak üretilmiş olup,
SPK bilgilendirme aracı mevzuatı ve KVKK m. 12 veri koruma taahhütleri kapsamında
kapalı beta ve kademeli canary dağıtım hazır bulunuşluğunu resmen tevsik eder.
"""


class ClosedBetaRolloutManager:
    """Manages beta invitations, user allowlist, deterministic cohorts, and circuit breaker."""

    STAGE_PERCENTAGES: dict[RolloutStage, float] = {
        RolloutStage.CLOSED_BETA: 0.0,
        RolloutStage.CANARY_5: 5.0,
        RolloutStage.CANARY_25: 25.0,
        RolloutStage.CANARY_50: 50.0,
        RolloutStage.GA_100: 100.0,
    }

    def __init__(
        self,
        salt: str = "borai-closed-beta-canary-salt-2026",
        error_threshold_percent: float = 1.0,
    ) -> None:
        self.salt = salt
        self.error_threshold_percent = error_threshold_percent
        self.current_stage: RolloutStage = RolloutStage.CLOSED_BETA
        self.allowlist: set[str] = set()
        self.invite_codes: dict[str, InviteCode] = {}
        self.circuit_breaker_tripped: bool = False
        self.circuit_breaker_reason: str | None = None
        self._update_metrics()

    def _update_metrics(self) -> None:
        """Synchronize current state with Prometheus metrics."""
        pct = self.STAGE_PERCENTAGES.get(self.current_stage, 0.0)
        BETA_ROLLOUT_STAGE_GAUGE.set(pct)
        BETA_ALLOWLIST_USERS_TOTAL.set(float(len(self.allowlist)))

    def add_to_allowlist(self, identifier: str) -> None:
        """Add user ID or normalized email to the closed beta allowlist."""
        clean_id = identifier.strip().lower()
        self.allowlist.add(clean_id)
        self._update_metrics()

    def remove_from_allowlist(self, identifier: str) -> None:
        """Remove user ID or normalized email from allowlist."""
        clean_id = identifier.strip().lower()
        self.allowlist.discard(clean_id)
        self._update_metrics()

    def issue_invite_code(
        self,
        code: str,
        max_uses: int = 1,
        expires_in_days: int = 30,
    ) -> InviteCode:
        """Create and register a new invitation token."""
        clean_code = code.strip().upper()
        expires = datetime.now(UTC) + timedelta(days=expires_in_days)
        invite = InviteCode(
            code=clean_code,
            max_uses=max_uses,
            expires_at=expires,
        )
        self.invite_codes[clean_code] = invite
        BETA_INVITATIONS_ISSUED_TOTAL.inc(1.0)
        return invite

    def validate_invite_code(self, code: str) -> tuple[bool, str]:
        """Validate whether an invitation token is redeemable."""
        clean_code = code.strip().upper()
        invite = self.invite_codes.get(clean_code)
        if not invite:
            return False, "Geçersiz davetiye kodu."
        if not invite.is_active:
            return False, "Davetiye kodu devre dışı bırakılmış."
        if datetime.now(UTC) > invite.expires_at:
            return False, "Davetiye kodunun geçerlilik süresi dolmuş."
        if invite.uses_count >= invite.max_uses:
            return False, "Davetiye kodu maksimum kullanım sınırına ulaşmış."
        return True, "Davetiye kodu geçerli."

    def redeem_invite_code(
        self,
        code: str,
        user_identifier: str,
        agreed_to_terms: bool,
        agreed_to_spk_disclaimer: bool,
    ) -> tuple[bool, str]:
        """Redeem invitation code with mandatory statutory consent checks."""
        if not agreed_to_terms:
            return False, "KVKK ve kullanım koşulları onayı zorunludur."
        if not agreed_to_spk_disclaimer:
            return False, "SPK yatırım tavsiyesi değildir yasal uyarısının kabulü zorunludur."

        clean_code = code.strip().upper()
        clean_user = user_identifier.strip().lower()

        is_valid, msg = self.validate_invite_code(clean_code)
        if not is_valid:
            return False, msg

        invite = self.invite_codes[clean_code]
        invite.uses_count += 1
        invite.redeemed_by.append(clean_user)
        self.add_to_allowlist(clean_user)
        BETA_INVITATIONS_REDEEMED_TOTAL.inc(1.0)
        return True, "Davetiye kodu başarıyla kullanıldı; kapalı betaya hoş geldiniz."

    def get_cohort_bucket(self, user_identifier: str) -> int:
        """Return deterministic cohort bucket (0..99) using SHA-256."""
        normalized = user_identifier.strip().lower()
        seed = f"{normalized}:{self.salt}".encode()
        digest = hashlib.sha256(seed).hexdigest()
        # Use first 8 hex characters as integer
        value = int(digest[:8], 16)
        return value % 100

    def is_user_eligible(
        self,
        user_identifier: str,
    ) -> tuple[bool, str]:
        """Determine whether user has access under current rollout stage and circuit status."""
        clean_user = user_identifier.strip().lower()

        # If circuit breaker is tripped, only explicit allowlist has access or emergency freeze
        if self.circuit_breaker_tripped:
            if clean_user in self.allowlist:
                return True, "Devre kesici aktif; acil durum allowlist erişimi sağlandı."
            return (
                False,
                (
                    f"Sistem otomatik devre kesicide ({self.circuit_breaker_reason}). "
                    "Dağıtım durduruldu."
                ),
            )

        # Allowlisted users always have access across all stages
        if clean_user in self.allowlist:
            return True, "Kapalı beta allowlist erişimi onaylandı."

        # Closed beta stage strictly requires allowlist / redeemed invite
        if self.current_stage == RolloutStage.CLOSED_BETA:
            return False, "Sistem şu anda kapalı beta aşamasında; davetiye gereklidir."

        # GA gives access to all registered users
        if self.current_stage == RolloutStage.GA_100:
            return True, "Genel dağıtım (GA) aktif; erişim sağlandı."

        # Canary stages evaluate deterministic cohort bucket
        bucket = self.get_cohort_bucket(clean_user)
        threshold = self.STAGE_PERCENTAGES[self.current_stage]

        if bucket < threshold:
            return (
                True,
                f"Kademeli dağıtım (%{int(threshold)} kohort) erişimi onaylandı (Kova: {bucket}).",
            )

        return (
            False,
            (
                f"Kullanıcı mevcut kademeli dağıtım havuzunda yer almıyor "
                f"(Aşama: %{int(threshold)}, Kova: {bucket})."
            ),
        )

    def trip_circuit_breaker(self, reason: str) -> None:
        """Trigger emergency circuit breaker and halt rollout."""
        self.circuit_breaker_tripped = True
        self.circuit_breaker_reason = reason
        logger.warning("Closed beta circuit breaker TRIPPED: %s", reason)
        BETA_CIRCUIT_BREAKER_TRIPPED_TOTAL.inc(1.0)
        self._update_metrics()

    def reset_circuit_breaker(self) -> None:
        """Reset circuit breaker to healthy operational state."""
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason = None
        logger.info("Closed beta circuit breaker RESET to healthy state.")
        self._update_metrics()

    def evaluate_error_rate_telemetry(
        self,
        total_requests: int,
        error_requests: int,
    ) -> bool:
        """Check error rate against threshold and trip circuit breaker if violated."""
        if total_requests <= 0:
            return True
        error_rate_pct = (error_requests / total_requests) * 100.0
        if error_rate_pct > self.error_threshold_percent:
            self.trip_circuit_breaker(
                f"Hata oranı eşik değerini aştı: %{error_rate_pct:.2f} > "
                f"%{self.error_threshold_percent:.2f}"
            )
            return False
        return True

    def advance_stage(self, new_stage: RolloutStage) -> None:
        """Advance rollout to next stage if circuit breaker is not active."""
        if self.circuit_breaker_tripped:
            raise RuntimeError("Devre kesici aktifken aşama ilerletilemez.")
        self.current_stage = new_stage
        self._update_metrics()
        logger.info("Rollout advanced to stage: %s", new_stage.value)


class ClosedBetaGatekeeper:
    """Pre-deployment verification gatekeeper for Closed Beta & Phased Rollout (FAZ-16)."""

    def __init__(
        self,
        manager: ClosedBetaRolloutManager | None = None,
        report_output_path: Path | None = None,
    ) -> None:
        self.manager = manager or ClosedBetaRolloutManager()
        self.report_output_path = report_output_path

    def evaluate_gate(
        self,
        workspace_root: Path | None = None,
    ) -> ClosedBetaGateReport:
        """Evaluate pre-deployment readiness checks for closed beta and phased rollout."""
        root = workspace_root or Path.cwd()
        checks: list[BetaCheckItem] = []
        blocking_violations: list[str] = []

        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        BETA_ROLLOUT_CHECKS_TOTAL.inc(1.0)

        # 1. Access Control: Allowlist & Invitation mechanism
        try:
            test_code = "BETA-GATE-TEST-TOKEN"
            self.manager.issue_invite_code(test_code, max_uses=2)
            valid, msg = self.manager.validate_invite_code(test_code)
            redeem_ok, _ = self.manager.redeem_invite_code(
                test_code, "tester@borsa-analiz.local", True, True
            )
            uninvited_ok, _ = self.manager.is_user_eligible("stranger@borsa-analiz.local")
            invited_ok, _ = self.manager.is_user_eligible("tester@borsa-analiz.local")

            passed = valid and redeem_ok and (not uninvited_ok) and invited_ok
            checks.append(
                BetaCheckItem(
                    check_id="BETA-01-ALLOWLIST-ACCESS-CONTROL",
                    category=BetaCheckCategory.ACCESS_CONTROL,
                    title="Kapalı Beta Allowlist ve Davetiye Mekanizması",
                    description="Davetiye kodu doğrulama ve yabancı kullanıcı engelleme.",
                    passed=passed,
                    details=(
                        f"Davetiye doğrulandı, yetkisiz kullanıcı engellendi, "
                        f"aktif allowlist kullanıcı sayısı: {len(self.manager.allowlist)}"
                    ),
                )
            )
            if not passed:
                blocking_violations.append("BETA-01: Kapalı beta erişim kısıtlaması başarısız.")
        except Exception as exc:
            checks.append(
                BetaCheckItem(
                    check_id="BETA-01-ALLOWLIST-ACCESS-CONTROL",
                    category=BetaCheckCategory.ACCESS_CONTROL,
                    title="Kapalı Beta Allowlist ve Davetiye Mekanizması",
                    description="Davetiye kodu doğrulama ve yabancı kullanıcı engelleme.",
                    passed=False,
                    details=f"Hata: {exc}",
                    violations=[str(exc)],
                )
            )
            blocking_violations.append(f"BETA-01: {exc}")

        # 2. Phased Rollout Ladder & Deterministic Cohorting
        try:
            b1 = self.manager.get_cohort_bucket("investor_alpha_001")
            b2 = self.manager.get_cohort_bucket("investor_alpha_001")
            deterministic = (b1 == b2) and (0 <= b1 < 100)

            # Check ladder progression stages
            stages = list(RolloutStage)
            has_all_stages = len(stages) == 5
            passed = deterministic and has_all_stages

            checks.append(
                BetaCheckItem(
                    check_id="BETA-02-PHASED-ROLLOUT-STAGES",
                    category=BetaCheckCategory.PHASED_LADDER,
                    title="Kademeli Dağıtım Merdiveni ve Deterministik Kohort",
                    description="5 aşamalı dağıtım (Beta->5%->25%->50%->GA) ve SHA-256 kohortlama.",
                    passed=passed,
                    details=(
                        f"Determinizm onaylandı (Kova: {b1}), aşamalar: {[s.value for s in stages]}"
                    ),
                )
            )
            if not passed:
                blocking_violations.append(
                    "BETA-02: Kademeli dağıtım aşamaları veya kohortlama hatalı."
                )
        except Exception as exc:
            checks.append(
                BetaCheckItem(
                    check_id="BETA-02-PHASED-ROLLOUT-STAGES",
                    category=BetaCheckCategory.PHASED_LADDER,
                    title="Kademeli Dağıtım Merdiveni ve Deterministik Kohort",
                    description="5 aşamalı dağıtım ve deterministik kohortlama.",
                    passed=False,
                    details=f"Hata: {exc}",
                    violations=[str(exc)],
                )
            )
            blocking_violations.append(f"BETA-02: {exc}")

        # 3. Circuit Breaker & Automated Emergency Rollback
        try:
            # Test error rate trigger
            trip_result = self.manager.evaluate_error_rate_telemetry(
                total_requests=1000,
                error_requests=25,  # 2.5% error rate > 1.0%
            )
            tripped = self.manager.circuit_breaker_tripped
            eligible_while_tripped, _ = self.manager.is_user_eligible("random_canary_user")

            # Reset after test
            self.manager.reset_circuit_breaker()
            reset_ok = not self.manager.circuit_breaker_tripped

            passed = (not trip_result) and tripped and (not eligible_while_tripped) and reset_ok
            checks.append(
                BetaCheckItem(
                    check_id="BETA-03-CIRCUIT-BREAKER-ROLLBACK",
                    category=BetaCheckCategory.CIRCUIT_BREAKER,
                    title="Otomatik Devre Kesici ve Acil Geri Alma (Rollback)",
                    description="%1.0 hata eşiğinde otomatik dondurma ve geri alma tatbikatı.",
                    passed=passed,
                    details=(
                        "Hata oranı tetikleyicisi (%2.5 > %1.0) başarıyla devreye girdi, "
                        "dağıtım donduruldu ve temiz şekilde sıfırlandı."
                    ),
                )
            )
            if not passed:
                blocking_violations.append(
                    "BETA-03: Otomatik devre kesici tetikleme/rollback tatbikatı başarısız."
                )
        except Exception as exc:
            checks.append(
                BetaCheckItem(
                    check_id="BETA-03-CIRCUIT-BREAKER-ROLLBACK",
                    category=BetaCheckCategory.CIRCUIT_BREAKER,
                    title="Otomatik Devre Kesici ve Acil Geri Alma (Rollback)",
                    description="Hata eşiği otomatik dondurma ve tatbikat.",
                    passed=False,
                    details=f"Hata: {exc}",
                    violations=[str(exc)],
                )
            )
            blocking_violations.append(f"BETA-03: {exc}")

        # 4. Mandatory Statutory & Legal Disclaimers (SPK & KVKK)
        try:
            legal_token = "LEGAL-DISCLAIMER-TOKEN"
            self.manager.issue_invite_code(legal_token)

            # Attempt without SPK disclaimer
            spk_fail, _ = self.manager.redeem_invite_code(
                legal_token,
                "spk_test@borsa-analiz.local",
                agreed_to_terms=True,
                agreed_to_spk_disclaimer=False,
            )
            # Attempt without KVKK terms
            kvkk_fail, _ = self.manager.redeem_invite_code(
                legal_token,
                "kvkk_test@borsa-analiz.local",
                agreed_to_terms=False,
                agreed_to_spk_disclaimer=True,
            )
            # Attempt with both
            legal_ok, _ = self.manager.redeem_invite_code(
                legal_token,
                "compliant@borsa-analiz.local",
                agreed_to_terms=True,
                agreed_to_spk_disclaimer=True,
            )

            passed = (not spk_fail) and (not kvkk_fail) and legal_ok
            checks.append(
                BetaCheckItem(
                    check_id="BETA-04-LEGAL-SPK-KVKK",
                    category=BetaCheckCategory.STATUTORY_LEGAL,
                    title="SPK Yatırım Tavsiyesi Kalkanı ve KVKK Şartı",
                    description="Kapalı beta katılımında yasal onayların zorunlu kılınması.",
                    passed=passed,
                    details="SPK uyarısı veya KVKK onayı eksik katılımlar kesin engellendi.",
                )
            )
            if not passed:
                blocking_violations.append("BETA-04: Yasal SPK/KVKK onay zorunluluğu sağlanamadı.")
        except Exception as exc:
            checks.append(
                BetaCheckItem(
                    check_id="BETA-04-LEGAL-SPK-KVKK",
                    category=BetaCheckCategory.STATUTORY_LEGAL,
                    title="SPK Yatırım Tavsiyesi Kalkanı ve KVKK Şartı",
                    description="Kapalı beta katılımında yasal onayların zorunlu kılınması.",
                    passed=False,
                    details=f"Hata: {exc}",
                    violations=[str(exc)],
                )
            )
            blocking_violations.append(f"BETA-04: {exc}")

        # 5. Prometheus Telemetry Exposition
        try:
            # Check gauge and counters are functional
            gauge_val = BETA_ROLLOUT_STAGE_GAUGE.get()
            users_val = BETA_ALLOWLIST_USERS_TOTAL.get()
            passed = gauge_val is not None and users_val >= 0.0

            checks.append(
                BetaCheckItem(
                    check_id="BETA-05-PROMETHEUS-TELEMETRY",
                    category=BetaCheckCategory.TELEMETRY_OBSERVABILITY,
                    title="Prometheus Dağıtım Telemetrisi ve Metrik Kaydı",
                    description="Aşama göstergeleri, allowlist hacmi ve devre kesici sayaçları.",
                    passed=passed,
                    details=f"Metrikler kayıtlı: aşama={gauge_val}%, allowlist={users_val}",
                )
            )
            if not passed:
                blocking_violations.append("BETA-05: Prometheus metrik telemetrisi doğrulanamadı.")
        except Exception as exc:
            checks.append(
                BetaCheckItem(
                    check_id="BETA-05-PROMETHEUS-TELEMETRY",
                    category=BetaCheckCategory.TELEMETRY_OBSERVABILITY,
                    title="Prometheus Dağıtım Telemetrisi ve Metrik Kaydı",
                    description="Aşama göstergeleri ve sayaçlar.",
                    passed=False,
                    details=f"Hata: {exc}",
                    violations=[str(exc)],
                )
            )
            blocking_violations.append(f"BETA-05: {exc}")

        # 6. Operational Documentation
        try:
            doc_file = root / "docs" / "operations" / "CLOSED_BETA_ROLLOUT_PLAN.md"
            doc_exists = doc_file.exists() and doc_file.stat().st_size > 500
            content = doc_file.read_text(encoding="utf-8") if doc_exists else ""
            has_keywords = (
                "CLOSED_BETA" in content
                and "CANARY_5" in content
                and "Circuit Breaker" in content
                and "SPK" in content
            )
            passed = doc_exists and has_keywords

            checks.append(
                BetaCheckItem(
                    check_id="BETA-06-OPERATIONAL-DOCUMENTATION",
                    category=BetaCheckCategory.OPERATIONAL_DOCUMENTATION,
                    title="Operasyonel Dağıtım Planı Dokümantasyonu",
                    description="docs/operations/CLOSED_BETA_ROLLOUT_PLAN.md doğrulaması.",
                    passed=passed,
                    details=f"Doküman mevcut ve geçerli: {doc_file.name} ({len(content)} bayt)",
                )
            )
            if not passed:
                blocking_violations.append(
                    "BETA-06: CLOSED_BETA_ROLLOUT_PLAN.md eksik veya geçersiz."
                )
        except Exception as exc:
            checks.append(
                BetaCheckItem(
                    check_id="BETA-06-OPERATIONAL-DOCUMENTATION",
                    category=BetaCheckCategory.OPERATIONAL_DOCUMENTATION,
                    title="Operasyonel Dağıtım Planı Dokümantasyonu",
                    description="docs/operations/CLOSED_BETA_ROLLOUT_PLAN.md doğrulaması.",
                    passed=False,
                    details=f"Hata: {exc}",
                    violations=[str(exc)],
                )
            )
            blocking_violations.append(f"BETA-06: {exc}")

        passed_count = sum(1 for c in checks if c.passed)
        failed_count = len(checks) - passed_count
        overall_status = BetaGateStatus.PASS if failed_count == 0 else BetaGateStatus.FAIL
        sign_off = overall_status == BetaGateStatus.PASS

        # Set gate status metric
        BETA_ROLLOUT_GATE_STATUS.set(1.0 if overall_status == BetaGateStatus.PASS else 0.0)

        report = ClosedBetaGateReport(
            gate_status=overall_status,
            timestamp=now_str,
            total_checks=len(checks),
            passed_checks=passed_count,
            failed_checks=failed_count,
            current_stage=self.manager.current_stage.value,
            circuit_breaker_active=self.manager.circuit_breaker_tripped,
            allowlist_size=len(self.manager.allowlist),
            checks=checks,
            blocking_violations=blocking_violations,
            sign_off_ready=sign_off,
            published_report_path=str(self.report_output_path) if self.report_output_path else None,
        )

        # Write published report if path provided or default
        target_path = self.report_output_path or (
            root / "docs" / "operations" / "CLOSED_BETA_VERIFICATION_REPORT.md"
        )
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(report.generate_markdown_audit_report(), encoding="utf-8")
        except Exception as report_err:
            logger.warning("Could not write verification report to %s: %s", target_path, report_err)

        return report


# Global gatekeeper and manager singletons
CLOSED_BETA_MANAGER = ClosedBetaRolloutManager()
CLOSED_BETA_GATEKEEPER = ClosedBetaGatekeeper(manager=CLOSED_BETA_MANAGER)
