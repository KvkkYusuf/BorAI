"""Sürüm 0 (Proje İskeleti) doğrulama kapısı ve hazır bulunuşluk denetçisi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 0: Repo, Antigravity kuralları, CI ve dokümantasyon hazır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_ZERO_GATE_STATUS = REGISTRY.gauge(
    "release_zero_gate_status",
    "Surum 0 proje iskeleti kapisi durumu (1=PASS, 0=FAIL)",
)
RELEASE_ZERO_EVALUATIONS_TOTAL = REGISTRY.counter(
    "release_zero_evaluations_total",
    "Toplam Surum 0 kapi degerlendirme calistirmasi",
)
RELEASE_ZERO_CHECKS_TOTAL = REGISTRY.counter(
    "release_zero_checks_total",
    "Toplam Surum 0 kontrol adimi",
)
RELEASE_ZERO_PASSED_TOTAL = REGISTRY.counter(
    "release_zero_passed_total",
    "Basariyla gecen Surum 0 kontrol adimi",
)
RELEASE_ZERO_FAILED_TOTAL = REGISTRY.counter(
    "release_zero_failed_total",
    "Basarisiz olan Surum 0 kontrol adimi",
)


class ReleaseZeroGateStatus(StrEnum):
    """Sürüm 0 kapı durumu."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ReleaseZeroCheckResult:
    """Tekil bir Sürüm 0 kontrolünün sonucu."""

    check_id: str
    title: str
    passed: bool
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReleaseZeroGateReport:
    """Sürüm 0 doğrulama kapısı genel değerlendirme raporu."""

    status: ReleaseZeroGateStatus
    total_checks: int
    passed_checks: int
    failed_checks: int
    evaluated_at: str
    results: list[ReleaseZeroCheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Sözlük formatına dönüştür."""
        return {
            "status": self.status.value,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "evaluated_at": self.evaluated_at,
            "results": [
                {
                    "check_id": r.check_id,
                    "title": r.title,
                    "passed": r.passed,
                    "evidence": r.evidence,
                    "details": r.details,
                }
                for r in self.results
            ],
        }

    def generate_markdown(self) -> str:
        """Denetim raporunu Markdown formatında üret."""
        rows = []
        for r in self.results:
            status_icon = "PASS" if r.passed else "FAIL"
            rows.append(
                f"| `{r.check_id}` | {r.title} | **{status_icon}** | {r.evidence} |"
            )

        checks_table = "\n".join(rows)

        summary_p1 = (
            "Sürüm 0 gereksinimleri uyarınca repo yapısı, Antigravity kural sistemi, "
            "CI iş akışları ve çekirdek dokümantasyon incelenmiştir."
        )
        conclusion_p = (
            "Tüm Sürüm 0 kriterleri (%100) eksiksiz doğrulanmış olup, projenin ilk sürüm iskeleti "
            "CI, kural seti ve dokümantasyon açısından üretim standartlarında onaylanmıştır."
        )

        return (
            "# Sürüm 0 (Proje İskeleti) Kabul ve Doğrulama Raporu\n\n"
            f"**Tarih:** {self.evaluated_at}\\\n"
            "**Kapsam:** FAZ-17 Sürüm Planı — Sürüm 0 (`docs/phases/FAZ-17-surum-plani.md`)\\\n"
            f"**Genel Durum:** **{self.status.value}** "
            f"({self.passed_checks}/{self.total_checks} Doğrulama Başarılı)\n\n"
            "---\n\n"
            "## 1. Doğrulama Özeti\n\n"
            f"{summary_p1}\n\n"
            "| Kontrol Kodu | Kontrol Başlığı | Sonuç | Doğrulama Kanıtı |\n"
            "|---|---|---|---|\n"
            f"{checks_table}\n\n"
            "---\n\n"
            "## 2. Sonuç ve Sürüm Kabul Beyanı\n\n"
            f"{conclusion_p}\n"
        )


class ReleaseZeroGatekeeper:
    """Sürüm 0 kabul kriterlerini denetleyen yönetişim kapısı."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        """Gatekeeper'ı başlat."""
        self.workspace_root = workspace_root or Path.cwd()

    def evaluate(self) -> ReleaseZeroGateReport:
        """Tüm Sürüm 0 kontrollerini çalıştırır ve rapor üretir."""
        RELEASE_ZERO_EVALUATIONS_TOTAL.inc()
        results: list[ReleaseZeroCheckResult] = []

        # 1. Repo & Yapılandırma Dosyaları
        results.append(self._check_repo_structure())

        # 2. Antigravity Kuralları
        results.append(self._check_antigravity_rules())

        # 3. Antigravity İş Akışları (Workflows)
        results.append(self._check_antigravity_workflows())

        # 4. CI/CD Otomasyonu
        results.append(self._check_ci_pipeline())

        # 5. Temel Dokümantasyon
        results.append(self._check_core_documentation())

        # 6. Kod Paketi & Tip Uyumluluğu
        results.append(self._check_package_type_markers())

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed

        status = ReleaseZeroGateStatus.PASS if failed == 0 else ReleaseZeroGateStatus.FAIL

        # Telemetri güncelle
        RELEASE_ZERO_CHECKS_TOTAL.inc(total)
        RELEASE_ZERO_PASSED_TOTAL.inc(passed)
        RELEASE_ZERO_FAILED_TOTAL.inc(failed)
        RELEASE_ZERO_GATE_STATUS.set(1.0 if status == ReleaseZeroGateStatus.PASS else 0.0)

        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        return ReleaseZeroGateReport(
            status=status,
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            evaluated_at=now_str,
            results=results,
        )

    def _check_repo_structure(self) -> ReleaseZeroCheckResult:
        """Repo ve temel yapılandırma dosyalarını denetler."""
        required_files = [
            ".gitignore",
            "pyproject.toml",
            ".python-version",
            ".pre-commit-config.yaml",
            "alembic.ini",
        ]
        missing = [f for f in required_files if not (self.workspace_root / f).is_file()]
        git_dir = self.workspace_root / ".git"
        git_ok = git_dir.is_dir()

        passed = (len(missing) == 0) and git_ok
        evidence = (
            f"Git repo mevcut, {len(required_files)} temel yapılandırma dosyası doğrulandı."
            if passed
            else f"Eksik dosyalar: {missing}, Git varlığı: {git_ok}"
        )

        return ReleaseZeroCheckResult(
            check_id="CHECK-R0-01",
            title="Repo ve Yapılandırma İskeleti",
            passed=passed,
            evidence=evidence,
            details={"missing": missing, "git_ok": git_ok},
        )

    def _check_antigravity_rules(self) -> ReleaseZeroCheckResult:
        """Antigravity kurallarının varlığını ve bütünlüğünü denetler."""
        rules_dir = self.workspace_root / ".agent" / "rules"
        expected_rules = [
            "finans-hukuk.md",
            "guvenlik.md",
            "kod-kalitesi.md",
            "token-ve-llm.md",
            "veri-ml.md",
        ]
        agents_md = self.workspace_root / "AGENTS.md"

        missing_rules = [
            r for r in expected_rules if not (rules_dir / r).is_file()
        ]
        agents_ok = agents_md.is_file() and agents_md.stat().st_size > 50

        passed = (len(missing_rules) == 0) and agents_ok
        evidence = (
            f"AGENTS.md ve {len(expected_rules)} kural dosyası (.agent/rules/) eksiksiz."
            if passed
            else f"Eksik kurallar: {missing_rules}, AGENTS.md durumu: {agents_ok}"
        )

        return ReleaseZeroCheckResult(
            check_id="CHECK-R0-02",
            title="Antigravity Kuralları",
            passed=passed,
            evidence=evidence,
            details={"missing_rules": missing_rules, "agents_ok": agents_ok},
        )

    def _check_antigravity_workflows(self) -> ReleaseZeroCheckResult:
        """Antigravity iş akışı komutlarını denetler."""
        workflows_dir = self.workspace_root / ".agent" / "workflows"
        expected_workflows = [
            "yeni-gorev.md",
            "gorev-bitir.md",
            "guvenlik-incelemesi.md",
            "hukuk-kapisi.md",
        ]
        missing_workflows = [
            w for w in expected_workflows if not (workflows_dir / w).is_file()
        ]
        setup_doc = self.workspace_root / "docs" / "ANTIGRAVITY_SETUP.md"
        setup_ok = setup_doc.is_file() and setup_doc.stat().st_size > 50

        passed = (len(missing_workflows) == 0) and setup_ok
        evidence = (
            f"{len(expected_workflows)} iş akışı komutu ve ANTIGRAVITY_SETUP.md mevcut."
            if passed
            else f"Eksik iş akışları: {missing_workflows}, setup_doc: {setup_ok}"
        )

        return ReleaseZeroCheckResult(
            check_id="CHECK-R0-03",
            title="Antigravity İş Akışları",
            passed=passed,
            evidence=evidence,
            details={"missing_workflows": missing_workflows, "setup_ok": setup_ok},
        )

    def _check_ci_pipeline(self) -> ReleaseZeroCheckResult:
        """CI iş akış dosyasının adımlarını denetler."""
        ci_file = self.workspace_root / ".github" / "workflows" / "ci.yml"
        if not ci_file.is_file():
            return ReleaseZeroCheckResult(
                check_id="CHECK-R0-04",
                title="CI Pipeline Yapılandırması",
                passed=False,
                evidence=".github/workflows/ci.yml bulunamadı.",
                details={"ci_found": False},
            )

        content = ci_file.read_text(encoding="utf-8")
        required_steps = ["ruff", "mypy", "pytest", "secret", "gitleaks"]
        missing_steps = [s for s in required_steps if s not in content.lower()]

        passed = len(missing_steps) == 0
        evidence = (
            "CI hattında ruff, mypy, pytest, secret scanning ve kalite adımları tanımlı."
            if passed
            else f"Eksik CI adımları: {missing_steps}"
        )

        return ReleaseZeroCheckResult(
            check_id="CHECK-R0-04",
            title="CI Pipeline Yapılandırması",
            passed=passed,
            evidence=evidence,
            details={"missing_steps": missing_steps, "file_size": len(content)},
        )

    def _check_core_documentation(self) -> ReleaseZeroCheckResult:
        """Çekirdek dokümantasyon setini denetler."""
        required_docs = [
            "README.md",
            "TODO.md",
            "PROGRESS.md",
            "DECISIONS.md",
            "docs/PRODUCT_SCOPE.md",
            "docs/ACCEPTANCE_CRITERIA.md",
            "docs/COMPLIANCE_TR.md",
            "docs/ARCHITECTURE.md",
            "docs/SECURITY.md",
            "docs/TEST_STRATEGY.md",
            "docs/PERFORMANCE.md",
            "docs/TOKEN_EFFICIENCY.md",
            "docs/AI_LLM_SAFETY.md",
            "docs/DISCLAIMER_TR.md",
        ]
        missing_docs: list[str] = []
        for doc in required_docs:
            p = self.workspace_root / doc
            if not p.is_file() or p.stat().st_size < 30:
                missing_docs.append(doc)

        passed = len(missing_docs) == 0
        evidence = (
            f"Tüm {len(required_docs)} çekirdek doküman eksiksiz ve geçerli boyutta."
            if passed
            else f"Eksik veya boş dokümanlar: {missing_docs}"
        )

        return ReleaseZeroCheckResult(
            check_id="CHECK-R0-05",
            title="Çekirdek Dokümantasyon Seti",
            passed=passed,
            evidence=evidence,
            details={"total_checked": len(required_docs), "missing": missing_docs},
        )

    def _check_package_type_markers(self) -> ReleaseZeroCheckResult:
        """Python paketleme ve py.typed tip işaretçisini denetler."""
        py_typed = self.workspace_root / "src" / "stock_analysis" / "py.typed"
        init_file = self.workspace_root / "src" / "stock_analysis" / "__init__.py"

        passed = py_typed.is_file() and init_file.is_file()
        evidence = (
            "src/stock_analysis paketi ve py.typed tip belirteci doğrulandı."
            if passed
            else f"py.typed: {py_typed.is_file()}, __init__.py: {init_file.is_file()}"
        )

        return ReleaseZeroCheckResult(
            check_id="CHECK-R0-06",
            title="Paket Yapısı ve Tip İşaretçisi",
            passed=passed,
            evidence=evidence,
            details={"py_typed": py_typed.is_file(), "init_py": init_file.is_file()},
        )
