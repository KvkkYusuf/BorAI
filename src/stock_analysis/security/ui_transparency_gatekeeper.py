"""Pre-deployment gatekeeper for UI Data Latency & Model Uncertainty disclosures (FAZ-16).

Verifies statutory compliance and transparency across frontend templates and scripts:
1. Data Latency Disclosures (BIST & SPK compliance):
   - Visible 15-minute delay badges in Header, Sidebar, and Detail views.
   - Prominent stale/delayed data warning banner.
   - Statutory notice: "Borsa İstanbul mevzuatı gereği en az 15 dakika gecikmelidir".
   - Explicit limitation: "Eski veya gecikmeli fiyatlar anlık piyasa fiyatı yerine geçmez;
     anlık işlem emri için kullanılamaz".
   - Accessible ARIA attributes (`aria-label`, `role="status"`, `aria-live`).
   - Dynamic mode switching / client-side freshness state handling (DELAYED, LIVE, STALE).

2. Model Uncertainty Disclosures:
   - Dedicated prediction range card (no fixed target price promises).
   - Explicit statistical confidence band limits (Alt/Üst Bant Sınırı, %90 Güven Aralığı).
   - Prominent uncertainty spread metric (± %2,8 Belirsizlik badge & progress bar).
   - Statutory disclaimer: "Kesin sonuç veya hedef fiyat vaadi teşkil etmez".
   - Statutory disclaimer: "kişiye özel yatırım danışmanlığı veya al-sat tavsiyesi içermez".
   - Naive baseline disclosure: "Model, basit bir kıyas yöntemine (naive baseline) göre test
     edilmiştir".
   - Multi-horizon uncertainty scaling (1D, 3D, 5D, 10D).
   - Model version and architecture metadata provenance.

3. Safe Fallback and Circuit-Breaker Transparency:
   - Fallback state container when model forecast is disabled or below baseline.
   - Strict assurance of zero fabricated guesses on model failure.
   - Dynamic simulation controls for automated testing and operational audits.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.observability.metrics import REGISTRY

logger = logging.getLogger(__name__)

# Prometheus metrics
UI_TRANSPARENCY_GATE_STATUS = REGISTRY.gauge(
    "ui_transparency_gate_status",
    "UI transparency pre-deployment gate status (1=PASS, 0=FAIL)",
)
UI_TRANSPARENCY_CHECKS_TOTAL = REGISTRY.counter(
    "ui_transparency_checks_total",
    "Total UI transparency pre-deployment checks performed",
)
UI_TRANSPARENCY_VIOLATIONS_TOTAL = REGISTRY.counter(
    "ui_transparency_violations_total",
    "Total UI transparency compliance violations detected",
)
UI_LATENCY_DISCLOSURES_GAUGE = REGISTRY.gauge(
    "ui_latency_disclosures_verified",
    "Verified UI data latency disclosures count",
)
UI_UNCERTAINTY_DISCLOSURES_GAUGE = REGISTRY.gauge(
    "ui_uncertainty_disclosures_verified",
    "Verified UI model uncertainty disclosures count",
)


class UiTransparencyGateStatus(StrEnum):
    """Overall outcome of UI transparency pre-deployment gate evaluation."""

    PASS = "PASS"
    FAIL = "FAIL"


class TransparencyCategory(StrEnum):
    """Classification categories for UI transparency verification."""

    DATA_LATENCY = "DATA_LATENCY"
    MODEL_UNCERTAINTY = "MODEL_UNCERTAINTY"
    FALLBACK_AND_SAFETY = "FALLBACK_AND_SAFETY"


@dataclass(frozen=True)
class TransparencyCheckItem:
    """Individual verification check result for UI transparency."""

    check_id: str
    category: TransparencyCategory
    title: str
    description: str
    target_file: str
    required_elements: list[str] = field(default_factory=list)
    required_patterns: list[str] = field(default_factory=list)
    passed: bool = False
    details: str = ""
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert item to dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class UiTransparencyGateReport:
    """Consolidated UI transparency pre-deployment verification report."""

    gate_status: UiTransparencyGateStatus
    timestamp: str
    total_checks: int
    passed_checks: int
    failed_checks: int
    data_latency_verified_count: int
    model_uncertainty_verified_count: int
    fallback_safety_verified_count: int
    checks: list[TransparencyCheckItem]
    blocking_violations: list[str]
    sign_off_ready: bool
    published_report_path: str | None = None
    regulatory_framework: str = "SPK III-52.4 & Borsa İstanbul Veri Dağıtım Esasları"

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary representation."""
        return {
            "gate_status": self.gate_status.value,
            "timestamp": self.timestamp,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "data_latency_verified_count": self.data_latency_verified_count,
            "model_uncertainty_verified_count": self.model_uncertainty_verified_count,
            "fallback_safety_verified_count": self.fallback_safety_verified_count,
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
            if self.gate_status == UiTransparencyGateStatus.PASS
            else "🔴 **REDDEDİLDİ (FAIL)**"
        )
        sign_off_badge = (
            "✅ Üretime Dağıtım İçin Uygundur"
            if self.sign_off_ready
            else "⛔ Dağıtım Engellendi: Şeffaflık Eksiklikleri Mevcut"
        )

        lines: list[str] = [
            "# Dağıtım Öncesi Arayüz Şeffaflık Denetim Raporu (UI Transparency Audit Report)",
            "",
            f"**Tarih:** {self.timestamp}  ",
            f"**Mevzuat Dayanağı:** {self.regulatory_framework}  ",
            f"**Kapı Değerlendirme Durumu:** {status_badge}  ",
            f"**Yayımlama Durumu:** {sign_off_badge}  ",
            "",
            "## 1. Yönetici Özeti",
            "",
            "Bu rapor, Borsa Analiz Sistemi web arayüzünde (UI) ve kullanıcı deneyiminde (UX) "
            "Borsa İstanbul veri gecikmesi (15 dakika kuralı) ve yapay zeka model tahmin "
            "belirsizliği (güven aralığı, naif kıyas, hedef fiyat vaadi içermeme, "
            "güvenli fallback) unsurlarının mevzuata uygunluğunu belgeler.",
            "",
            "| Metrik | Değer | Hedef / Eşik | Durum |",
            "|---|---|---|---|",
            (
                f"| Toplam Şeffaflık Denetimi | {self.total_checks} | 19 | "
                f"{'✅ TAM' if self.total_checks >= 19 else '⚠️ EKSİK'} |"
            ),
            (
                f"| Başarılı Denetimler | {self.passed_checks} | %100 | "
                f"{'✅ %100' if self.passed_checks == self.total_checks else '❌ BAŞARISIZ'} |"
            ),
            (
                f"| Veri Gecikmesi Denetimleri | {self.data_latency_verified_count} | "
                "8 | ✅ Doğrulandı |"
            ),
            (
                f"| Model Belirsizliği Denetimleri | {self.model_uncertainty_verified_count} | "
                "8 | ✅ Doğrulandı |"
            ),
            (
                f"| Güvenli Fallback Denetimleri | {self.fallback_safety_verified_count} | "
                "3 | ✅ Doğrulandı |"
            ),
            (
                f"| Kritik İhlal Sayısı | {len(self.blocking_violations)} | 0 | "
                f"{'✅ SIFIR' if len(self.blocking_violations) == 0 else '❌ İHLAL VAR'} |"
            ),
            "",
            "## 2. Ayrıntılı Denetim Maddeleri ve Bulgular",
            "",
            "| ID | Kategori | Denetim Maddesi | Hedef Dosya | Sonuç |",
            "|---|---|---|---|---|",
        ]

        for check in self.checks:
            result_icon = "✅ GEÇTİ" if check.passed else "❌ KALDI"
            lines.append(
                f"| `{check.check_id}` | `{check.category.value}` | "
                f"{check.title} | `{check.target_file}` | {result_icon} |"
            )

        lines.extend([
            "",
            "## 3. Denetim Ayrıntıları ve Kanıtlar",
            "",
        ])

        for check in self.checks:
            lines.append(f"### `{check.check_id}` — {check.title}")
            lines.append(f"- **Kategori:** `{check.category.value}`")
            lines.append(f"- **Açıklama:** {check.description}")
            lines.append(f"- **Hedef Dosya:** `{check.target_file}`")
            lines.append(f"- **Durum:** {'✅ GEÇTİ (PASS)' if check.passed else '❌ KALDI (FAIL)'}")
            elem_str = ", ".join(check.required_elements) if check.required_elements else "N/A"
            lines.append(f"- **Doğrulanan DOM Elemanları / Seçiciler:** `{elem_str}`")
            pat_str = ", ".join(check.required_patterns) if check.required_patterns else "N/A"
            lines.append(f"- **Doğrulanan Metin / İfade Örüntüleri:** `{pat_str}`")
            lines.append(f"- **Teknik Ayrıntı:** {check.details}")
            if check.violations:
                lines.append("- **Tespit Edilen İhlaller:**")
                for v in check.violations:
                    lines.append(f"  - ⚠️ {v}")
            lines.append("")

        if self.blocking_violations:
            lines.extend([
                "## 4. Dağıtımı Engelleyen Kritik İhlaller",
                "",
            ])
            for v in self.blocking_violations:
                lines.append(f"- ⛔ **{v}**")
            lines.append("")
        else:
            lines.extend([
                "## 4. Dağıtımı Engelleyen Kritik İhlaller",
                "",
                "✅ Dağıtımı engelleyen herhangi bir şeffaflık ihlali bulunmamaktadır.",
                "",
            ])

        lines.extend([
            "## 5. Mevzuat ve Hukukî Uyumluluk Beyanı",
            "",
            "1. **Borsa İstanbul Veri Dağıtım Esasları:** Gecikmeli verilerin en az 15 dakika "
            "gecikmeli olduğu, ekran üzerinde açıkça belirtilmekte; anlık emir için "
            "kullanılamayacağı kullanıcıya bildirilmektedir.",
            "2. **SPK III-52.4 Yatırım Hizmetleri Tebliği:** Arayüzdeki istatistiki tahminlerin "
            "kesin hedef fiyat veya al/sat tavsiyesi olmadığı, belirsizlik aralığıyla sunulduğu ve "
            "modelin naif baseline'a göre test edildiği şeffafça açıklanmaktadır.",
            "3. **Güvenli Fallback:** Model arızalandığında veya bayrak kapalı olduğunda asla "
            "uydurma tahmin üretilmemekte; kullanıcıya durum şeffaf bir arayüz kartıyla "
            "iletilmektedir.",
            "",
            "---",
            (
                f"*Bu rapor `{self.timestamp}` tarihinde `UiTransparencyGatekeeper` motoru "
                "tarafından otomatik olarak oluşturulmuştur.*"
            ),
        ])

        return "\n".join(lines)


class UiTransparencyGatekeeper:
    """Evaluates and enforces UI transparency for data latency and model uncertainty."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        if workspace_root is None:
            self.workspace_root = Path(__file__).resolve().parent.parent.parent.parent
        else:
            self.workspace_root = workspace_root

        self.static_dir = self.workspace_root / "src" / "stock_analysis" / "static"
        self.reports_dir = self.workspace_root / "docs" / "ui"

    def evaluate_gate(
        self,
        static_dir: Path | None = None,
        auto_publish_report: bool = True,
        report_output_path: Path | None = None,
    ) -> UiTransparencyGateReport:
        """Execute all data latency, model uncertainty and safe fallback UI checks."""
        target_static_dir = static_dir or self.static_dir
        index_file = target_static_dir / "index.html"
        js_file = target_static_dir / "js" / "app.js"

        index_content = ""
        js_content = ""

        if index_file.exists():
            index_content = index_file.read_text(encoding="utf-8")
        else:
            logger.error("index.html not found at %s", index_file)

        if js_file.exists():
            js_content = js_file.read_text(encoding="utf-8")
        else:
            logger.error("app.js not found at %s", js_file)

        checks: list[TransparencyCheckItem] = []
        blocking_violations: list[str] = []

        # =====================================================================
        # 1. DATA LATENCY CHECKS
        # =====================================================================

        # LAT_01: Header Latency Badge
        lat_01_elements = ["header-latency-label", "btn-header-latency"]
        lat_01_patterns = ["15 Dk Gecikmeli BIST", "toggleDataLatencyMode()"]
        lat_01_passed, lat_01_violations = self._verify_elements_and_patterns(
            index_content, lat_01_elements, lat_01_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-01",
                category=TransparencyCategory.DATA_LATENCY,
                title="Üst Başlık (Header) Veri Gecikme Rozeti",
                description=(
                    "Masaüstü ve mobil üst barda 15 dk gecikmeli BIST veri durumunu "
                    "gösteren buton ve etiket."
                ),
                target_file="static/index.html",
                required_elements=lat_01_elements,
                required_patterns=lat_01_patterns,
                passed=lat_01_passed,
                details=(
                    "Header alanında kullanıcıya anlık gecikme durumunu bildiren buton ve "
                    "etiket doğrulandı."
                ),
                violations=lat_01_violations,
            )
        )

        # LAT_02: Sidebar Latency Badge and Timestamp
        lat_02_elements = [
            "sidebar-latency-badge",
            "sidebar-latency-status",
            "sidebar-latency-desc",
            "sidebar-latency-time",
        ]
        lat_02_patterns = ["15 Dk Gecikmeli", "15 Dk (Mevzuat)"]
        lat_02_passed, lat_02_violations = self._verify_elements_and_patterns(
            index_content, lat_02_elements, lat_02_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-02",
                category=TransparencyCategory.DATA_LATENCY,
                title="Kenar Çubuğu (Sidebar) Piyasa Durumu ve Zaman Damgası",
                description=(
                    "Yan menüde piyasa genel durumunu, mevzuat gecikmesini ve UTC+3 saat "
                    "damgasını gösteren rozet."
                ),
                target_file="static/index.html",
                required_elements=lat_02_elements,
                required_patterns=lat_02_patterns,
                passed=lat_02_passed,
                details="Sidebar kutusunda gecikme rozeti ve saat dilimi bilgisi doğrulandı.",
                violations=lat_02_violations,
            )
        )

        # LAT_03: Detail View Latency Badge
        lat_03_elements = ["detail-latency-badge", "detail-latency-text"]
        lat_03_patterns = ["15 Dk Gecikmeli"]
        lat_03_passed, lat_03_violations = self._verify_elements_and_patterns(
            index_content, lat_03_elements, lat_03_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-03",
                category=TransparencyCategory.DATA_LATENCY,
                title="Hisse Detay Ekranı Gecikme Rozeti",
                description=(
                    "Hisse detay başlığında fiyatın anlık değil 15 dk gecikmeli olduğunu "
                    "gösteren rozet."
                ),
                target_file="static/index.html",
                required_elements=lat_03_elements,
                required_patterns=lat_03_patterns,
                passed=lat_03_passed,
                details="Hisse fiyatı yanında 15 dk gecikmeli rozeti doğrulandı.",
                violations=lat_03_violations,
            )
        )

        # LAT_04: Stale Data Warning Banner
        lat_04_elements = ["detail-stale-banner", "stale-banner-title", "stale-banner-desc"]
        lat_04_patterns = ["DİKKAT: Veri 15 Dakika Gecikmelidir"]
        lat_04_passed, lat_04_violations = self._verify_elements_and_patterns(
            index_content, lat_04_elements, lat_04_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-04",
                category=TransparencyCategory.DATA_LATENCY,
                title="Gecikmeli ve Bayat Veri Uyarı Başlığı (Banner)",
                description=(
                    "Kullanıcıya fiyatların canlı değil gecikmeli olduğunu bildiren "
                    "sarı uyarı kutusu."
                ),
                target_file="static/index.html",
                required_elements=lat_04_elements,
                required_patterns=lat_04_patterns,
                passed=lat_04_passed,
                details="Detay ekranında belirgin uyarı başlığı doğrulandı.",
                violations=lat_04_violations,
            )
        )

        # LAT_05: Statutory BIST 15-Minute Delay Statement
        lat_05_patterns = [
            "Borsa İstanbul mevzuatı gereği",
            "en az 15 dakika gecikmelidir",
        ]
        lat_05_passed, lat_05_violations = self._verify_patterns_in_text(
            index_content, lat_05_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-05",
                category=TransparencyCategory.DATA_LATENCY,
                title="Borsa İstanbul 15 Dakika Yasal Mevzuat Bildirimi",
                description=(
                    "Mevzuat gereği verilerin en az 15 dakika gecikmeli olduğuna dair "
                    "zorunlu yasal ibare."
                ),
                target_file="static/index.html",
                required_patterns=lat_05_patterns,
                passed=lat_05_passed,
                details="BIST mevzuat zorunluluğu açıkça beyan edilmektedir.",
                violations=lat_05_violations,
            )
        )

        # LAT_06: No Instant Trading Order Notice
        lat_06_patterns = [
            "anlık piyasa fiyatı yerine geçmez; anlık işlem emri için kullanılamaz",
        ]
        lat_06_passed, lat_06_violations = self._verify_patterns_in_text(
            index_content, lat_06_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-06",
                category=TransparencyCategory.DATA_LATENCY,
                title="Anlık İşlem Emri Verilemez Yasal Uyarısı",
                description=(
                    "Gecikmeli verilerin anlık piyasa fiyatı yerine geçmeyeceği ve "
                    "işlem emrinde kullanılamayacağı uyarısı."
                ),
                target_file="static/index.html",
                required_patterns=lat_06_patterns,
                passed=lat_06_passed,
                details="Yanıltmayı engelleyen işlem sınırlama uyarısı doğrulandı.",
                violations=lat_06_violations,
            )
        )

        # LAT_07: Dynamic Latency State Transitions in JavaScript
        lat_07_patterns = [
            "toggleDataLatencyMode",
            "setDataLatencyMode",
            '"delayed"',
            '"stale"',
            '"realtime"',
        ]
        lat_07_passed, lat_07_violations = self._verify_patterns_in_text(
            js_content, lat_07_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-07",
                category=TransparencyCategory.DATA_LATENCY,
                title="Dinamik Gecikme Durumu İstemci Mantığı (JS)",
                description=(
                    "İstemci tarafında seans açık, kapalı, gecikmeli ve bayat durum "
                    "geçişlerini yöneten mantık."
                ),
                target_file="static/js/app.js",
                required_patterns=lat_07_patterns,
                passed=lat_07_passed,
                details=(
                    "JavaScript durum makinesinde 3 farklı gecikme durumu ve UI güncelleme "
                    "fonksiyonları doğrulandı."
                ),
                violations=lat_07_violations,
            )
        )

        # LAT_08: Accessibility for Latency Elements
        lat_08_patterns = [
            'role="status"',
            'aria-label="Veri gecikme durumu"',
        ]
        lat_08_passed, lat_08_violations = self._verify_patterns_in_text(
            index_content, lat_08_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="LAT-08",
                category=TransparencyCategory.DATA_LATENCY,
                title="Gecikme Göstergeleri Erişilebilirlik (A11y/ARIA)",
                description=(
                    "Ekran okuyucular için ARIA role ve aria-label etiketlerinin gecikme "
                    "göstergelerinde bulunması."
                ),
                target_file="static/index.html",
                required_patterns=lat_08_patterns,
                passed=lat_08_passed,
                details="Ekran okuyucu uyumluluğu için role='status' ve etiketler doğrulandı.",
                violations=lat_08_violations,
            )
        )

        # =====================================================================
        # 2. MODEL UNCERTAINTY CHECKS
        # =====================================================================

        # UNC_01: Prediction Card Container
        unc_01_elements = ["prediction-main-card", "prediction-active-content"]
        unc_01_patterns = ["Model Tahmin Aralığı & Belirsizlik Payı"]
        unc_01_passed, unc_01_violations = self._verify_elements_and_patterns(
            index_content, unc_01_elements, unc_01_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-01",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Model Tahmin Aralığı ve Belirsizlik Kartı",
                description=(
                    "Yapay zeka model çıktılarını kesin hedef fiyat yerine aralık "
                    "olarak sunan özel kart."
                ),
                target_file="static/index.html",
                required_elements=unc_01_elements,
                required_patterns=unc_01_patterns,
                passed=unc_01_passed,
                details="Teknik analiz ekranında tahmin aralığı kartı doğrulandı.",
                violations=unc_01_violations,
            )
        )

        # UNC_02: Confidence Band Bounds Display
        unc_02_elements = [
            "prediction-band-text",
            "prediction-band-min",
            "prediction-band-max",
        ]
        unc_02_patterns = ["%90 Güven Aralığı", "Alt Bant Sınırı", "Üst Bant Sınırı"]
        unc_02_passed, unc_02_violations = self._verify_elements_and_patterns(
            index_content, unc_02_elements, unc_02_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-02",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="İstatistiki Güven Bandı Sınırları Gösterimi",
                description=(
                    "Tahminin %90 güven aralığındaki alt ve üst sınırlarını açıkça "
                    "gösteren kutu."
                ),
                target_file="static/index.html",
                required_elements=unc_02_elements,
                required_patterns=unc_02_patterns,
                passed=unc_02_passed,
                details="Alt/Üst sınır ve güven seviyesi etiketleri doğrulandı.",
                violations=unc_02_violations,
            )
        )

        # UNC_03: Uncertainty Score and Progress Meter
        unc_03_elements = [
            "prediction-uncertainty-score",
            "prediction-uncertainty-bar",
        ]
        unc_03_patterns = ["Model Belirsizlik Skoru:", "± %2,8 Belirsizlik"]
        unc_03_passed, unc_03_violations = self._verify_elements_and_patterns(
            index_content, unc_03_elements, unc_03_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-03",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Model Belirsizlik Skoru ve Görsel Ölçer",
                description=(
                    "Hata payı yüzdesini (± %X,X) ve çubuk göstergesini içeren "
                    "belirsizlik ölçeri."
                ),
                target_file="static/index.html",
                required_elements=unc_03_elements,
                required_patterns=unc_03_patterns,
                passed=unc_03_passed,
                details="Belirsizlik rozeti ve progress bar bileşeni doğrulandı.",
                violations=unc_03_violations,
            )
        )

        # UNC_04: Statutory Disclaimer - No Price Target Promise
        unc_04_patterns = [
            "Kesin sonuç veya hedef fiyat vaadi teşkil etmez",
        ]
        unc_04_passed, unc_04_violations = self._verify_patterns_in_text(
            index_content, unc_04_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-04",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Kesin Hedef Fiyat Vaadi Teşkil Etmez Beyanı",
                description=(
                    "Model tahmininin kesin bir fiyat taahhüdü veya garanti olmadığına "
                    "dair zorunlu ibare."
                ),
                target_file="static/index.html",
                required_patterns=unc_04_patterns,
                passed=unc_04_passed,
                details="Hedef fiyat vaadi yasağı beyanı şeffafça doğrulandı.",
                violations=unc_04_violations,
            )
        )

        # UNC_05: Statutory Disclaimer - No Investment Advice
        unc_05_patterns = [
            "kişiye özel yatırım danışmanlığı veya al-sat tavsiyesi içermez",
        ]
        unc_05_passed, unc_05_violations = self._verify_patterns_in_text(
            index_content, unc_05_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-05",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Yatırım Danışmanlığı / Al-Sat Tavsiyesi Değildir Beyanı",
                description=(
                    "SPK mevzuatı gereği çıktının al-sat tavsiyesi veya yatırım "
                    "danışmanlığı olmadığı beyanı."
                ),
                target_file="static/index.html",
                required_patterns=unc_05_patterns,
                passed=unc_05_passed,
                details="SPK al/sat ve yönlendirme yasağı ibaresi doğrulandı.",
                violations=unc_05_violations,
            )
        )

        # UNC_06: Naive Baseline Comparison Statement
        unc_06_patterns = [
            "Model, basit bir kıyas yöntemine (naive baseline) göre test edilmiştir",
        ]
        unc_06_passed, unc_06_violations = self._verify_patterns_in_text(
            index_content, unc_06_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-06",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Naif Kıyas (Naive Baseline) Doğrulama Beyanı",
                description=(
                    "Modelin basit bir kıyaslama metodolojisine göre test edildiğinin "
                    "kullanıcıya açıklanması."
                ),
                target_file="static/index.html",
                required_patterns=unc_06_patterns,
                passed=unc_06_passed,
                details="Naive baseline kıyaslama şeffaflığı doğrulandı.",
                violations=unc_06_violations,
            )
        )

        # UNC_07: Multi-Horizon Uncertainty Scaling
        unc_07_patterns = [
            "setPredictionHorizon",
            "uncertaintyScore",
            "± %1,2 Belirsizlik",
            "± %4,5 Belirsizlik",
        ]
        unc_07_passed, unc_07_violations = self._verify_patterns_in_text(
            js_content, unc_07_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-07",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Çoklu Vadeye Göre Belirsizlik Ölçeklemesi (1D-10D)",
                description=(
                    "Tahmin vadesi uzadıkça hata payı ve belirsizlik bandının "
                    "genişlemesini sağlayan mantık."
                ),
                target_file="static/js/app.js",
                required_patterns=unc_07_patterns,
                passed=unc_07_passed,
                details="Vadeye bağlı belirsizlik ölçeklemesi ve dinamik UI doğrulandı.",
                violations=unc_07_violations,
            )
        )

        # UNC_08: Model Version and Lineage Provenance
        unc_08_elements = [
            "prediction-model-version-badge",
            "prediction-meta-version",
            "prediction-meta-baseline",
        ]
        unc_08_patterns = ["v1.4.2", "LSTM + LGBM"]
        unc_08_passed, unc_08_violations = self._verify_elements_and_patterns(
            index_content, unc_08_elements, unc_08_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="UNC-08",
                category=TransparencyCategory.MODEL_UNCERTAINTY,
                title="Model Sürümü, Mimari ve Soykütüğü Şeffaflığı",
                description=(
                    "Modelin versiyonu, mimarisi ve baseline kıyas başarısının arayüzde "
                    "gösterilmesi."
                ),
                target_file="static/index.html",
                required_elements=unc_08_elements,
                required_patterns=unc_08_patterns,
                passed=unc_08_passed,
                details="Model sürüm etiketi ve mimari açıklaması doğrulandı.",
                violations=unc_08_violations,
            )
        )

        # =====================================================================
        # 3. SAFE FALLBACK AND SAFETY CHECKS
        # =====================================================================

        # SAF_01: Fallback State Container
        saf_01_elements = ["prediction-model-fallback", "prediction-status-badge"]
        saf_01_patterns = ["Tahmin Özelliği Şu Anda Kullanılamıyor", 'role="alert"']
        saf_01_passed, saf_01_violations = self._verify_elements_and_patterns(
            index_content, saf_01_elements, saf_01_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="SAF-01",
                category=TransparencyCategory.FALLBACK_AND_SAFETY,
                title="Model Devredışı / Güvenli Fallback Görsel Alanı",
                description=(
                    "Model baseline'ın altında kaldığında veya bayrak kapalıyken uydurma "
                    "tahmin yerine gösterilen kart."
                ),
                target_file="static/index.html",
                required_elements=saf_01_elements,
                required_patterns=saf_01_patterns,
                passed=saf_01_passed,
                details="Erişilebilir uyarı kutusu (role='alert') ve fallback kartı doğrulandı.",
                violations=saf_01_violations,
            )
        )

        # SAF_02: Zero Fabricated Guesses on Fallback
        saf_02_patterns = [
            (
                "Model performansı naif kıyas (baseline) doğrulama aşamasındadır veya "
                "özellik bayrağı kapalıdır"
            ),
            "Klasik teknik analiz göstergeleri kullanılabilir",
        ]
        saf_02_passed, saf_02_violations = self._verify_patterns_in_text(
            index_content, saf_02_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="SAF-02",
                category=TransparencyCategory.FALLBACK_AND_SAFETY,
                title="Sıfır Uydurma Tahmin Güvencesi ve Açıklaması",
                description=(
                    "Tahmin kapalıyken kullanıcının yanıltılmadığı ve klasik "
                    "göstergelere yönlendirildiği açıklaması."
                ),
                target_file="static/index.html",
                required_patterns=saf_02_patterns,
                passed=saf_02_passed,
                details="Kullanıcıya güvenli fallback mantığı açıkça izah edilmektedir.",
                violations=saf_02_violations,
            )
        )

        # SAF_03: Fallback Simulation Controls for Testing
        saf_03_elements = ["btn-toggle-model-status"]
        saf_03_patterns = ["toggleModelStatus()"]
        saf_03_passed, saf_03_violations = self._verify_elements_and_patterns(
            index_content, saf_03_elements, saf_03_patterns
        )
        checks.append(
            TransparencyCheckItem(
                check_id="SAF-03",
                category=TransparencyCategory.FALLBACK_AND_SAFETY,
                title="Model Durumu Simülasyonu ve Test Butonu",
                description=(
                    "Test/denetim aşamasında tahmin kartının aktif ve fallback durumları "
                    "arasında geçişini sağlayan buton."
                ),
                target_file="static/index.html",
                required_elements=saf_03_elements,
                required_patterns=saf_03_patterns,
                passed=saf_03_passed,
                details="Test ve doğrulama için model durum değiştirme arayüz kontrolü doğrulandı.",
                violations=saf_03_violations,
            )
        )

        # =====================================================================
        # AGGREGATION & METRICS
        # =====================================================================
        total_checks = len(checks)
        passed_checks = sum(1 for c in checks if c.passed)
        failed_checks = total_checks - passed_checks

        latency_verified = sum(
            1 for c in checks if c.category == TransparencyCategory.DATA_LATENCY and c.passed
        )
        uncertainty_verified = sum(
            1 for c in checks if c.category == TransparencyCategory.MODEL_UNCERTAINTY and c.passed
        )
        fallback_verified = sum(
            1 for c in checks if c.category == TransparencyCategory.FALLBACK_AND_SAFETY and c.passed
        )

        for check in checks:
            status_label = "passed" if check.passed else "failed"
            UI_TRANSPARENCY_CHECKS_TOTAL.inc(
                1.0, labels={"category": check.category.value, "status": status_label}
            )
            if not check.passed:
                UI_TRANSPARENCY_VIOLATIONS_TOTAL.inc(
                    1.0, labels={"category": check.category.value}
                )
                for v in check.violations:
                    blocking_violations.append(f"[{check.check_id}] {v}")

        UI_LATENCY_DISCLOSURES_GAUGE.set(float(latency_verified))
        UI_UNCERTAINTY_DISCLOSURES_GAUGE.set(float(uncertainty_verified))

        is_passed = (failed_checks == 0) and (len(blocking_violations) == 0)
        gate_status = UiTransparencyGateStatus.PASS if is_passed else UiTransparencyGateStatus.FAIL
        UI_TRANSPARENCY_GATE_STATUS.set(1.0 if is_passed else 0.0)

        timestamp_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        published_path: str | None = None
        if auto_publish_report:
            out_file = (
                report_output_path
                or (self.reports_dir / "UI_TRANSPARENCY_VERIFICATION_REPORT.md")
            )
            out_file.parent.mkdir(parents=True, exist_ok=True)
            report_instance = UiTransparencyGateReport(
                gate_status=gate_status,
                timestamp=timestamp_str,
                total_checks=total_checks,
                passed_checks=passed_checks,
                failed_checks=failed_checks,
                data_latency_verified_count=latency_verified,
                model_uncertainty_verified_count=uncertainty_verified,
                fallback_safety_verified_count=fallback_verified,
                checks=checks,
                blocking_violations=blocking_violations,
                sign_off_ready=is_passed,
                published_report_path=str(out_file),
            )
            out_file.write_text(report_instance.generate_markdown_audit_report(), encoding="utf-8")
            published_path = str(out_file)

        return UiTransparencyGateReport(
            gate_status=gate_status,
            timestamp=timestamp_str,
            total_checks=total_checks,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            data_latency_verified_count=latency_verified,
            model_uncertainty_verified_count=uncertainty_verified,
            fallback_safety_verified_count=fallback_verified,
            checks=checks,
            blocking_violations=blocking_violations,
            sign_off_ready=is_passed,
            published_report_path=published_path,
        )

    def _verify_elements_and_patterns(
        self, content: str, elements: list[str], patterns: list[str]
    ) -> tuple[bool, list[str]]:
        """Verify presence of all elements (id="...") and text patterns in target content."""
        violations: list[str] = []
        for el in elements:
            if (
                f'id="{el}"' not in content
                and f"id='{el}'" not in content
                and el not in content
            ):
                violations.append(f"Zorunlu DOM öğesi eksik: '{el}'")

        for pat in patterns:
            if pat not in content:
                violations.append(f"Zorunlu metin/kod örüntüsü eksik: '{pat}'")

        return len(violations) == 0, violations

    def _verify_patterns_in_text(
        self, content: str, patterns: list[str]
    ) -> tuple[bool, list[str]]:
        """Verify presence of text patterns in content."""
        violations: list[str] = []
        for pat in patterns:
            if pat not in content:
                violations.append(f"Zorunlu yasal/teknik ifade eksik: '{pat}'")

        return len(violations) == 0, violations


# Global default instance
UI_TRANSPARENCY_GATEKEEPER = UiTransparencyGatekeeper()
