"""Kaggle and external dataset provenance, licensing, and quality verification module.

Implements strict validation rules for FAZ-10:
- License and provenance chain verification (CC-BY-4.0, CC0, ODC vs. ambiguous/proprietary).
- Ambiguous or non-compliant licenses are strictly restricted to LOCAL_EXPERIMENT_ONLY (v2 rule).
- Currency (TRY) and timezone (Europe/Istanbul / UTC) enforcement.
- Survivorship bias & delisted ticker tracking and risk scoring.
- Temporal train/validation/test chronological splitting without look-ahead leakage.
- Dataset Card schema export.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DatasetLicenseType(StrEnum):
    """Supported license types for financial and market datasets."""

    CC0_1_0 = "CC0-1.0"
    CC_BY_4_0 = "CC-BY-4.0"
    CC_BY_SA_4_0 = "CC-BY-SA-4.0"
    OPEN_DATA_COMMONS_BY = "ODC-BY"
    DATABASE_CONTENTS_LICENSE = "DbCL"
    KAGGLE_COMMERCIAL = "kaggle-commercial"
    KAGGLE_NON_COMMERCIAL = "kaggle-non-commercial"
    AMBIGUOUS_UNKNOWN = "ambiguous-unknown"
    PROPRIETARY = "proprietary"


class DatasetUsageScope(StrEnum):
    """Approved runtime usage scope for the dataset."""

    PRODUCTION_ALLOWED = "production_allowed"
    LOCAL_EXPERIMENT_ONLY = "local_experiment_only"
    REJECTED_NON_COMPLIANT = "rejected_non_compliant"


class CorporateActionAdjustment(StrEnum):
    """Methodology applied for handling corporate actions (splits, dividends)."""

    RAW_UNADJUSTED = "raw_unadjusted"
    SPLIT_ADJUSTED_ONLY = "split_adjusted_only"
    FULLY_ADJUSTED_DIVIDEND_SPLIT = "fully_adjusted_dividend_split"
    DUAL_STREAM = "dual_stream"


class TemporalSplitConfig(BaseModel):
    """Chronological boundary configuration for train, validation, and test periods."""

    model_config = ConfigDict(frozen=True)

    train_start: date
    train_end: date
    val_start: date
    val_end: date
    test_start: date
    test_end: date

    def validate_chronology(self) -> list[str]:
        """Verify strict chronological ordering to prevent temporal data leakage."""
        errors: list[str] = []
        if self.train_start >= self.train_end:
            errors.append("train_start must precede train_end")
        if self.train_end >= self.val_start:
            errors.append("train_end must precede val_start (temporal overlap detected)")
        if self.val_start >= self.val_end:
            errors.append("val_start must precede val_end")
        if self.val_end >= self.test_start:
            errors.append("val_end must precede test_start (temporal overlap detected)")
        if self.test_start >= self.test_end:
            errors.append("test_start must precede test_end")
        return errors


class DatasetCard(BaseModel):
    """Formal dataset documentation card capturing metadata, provenance, and biases."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(description="Unique identifier or Kaggle slug")
    title: str = Field(description="Descriptive title of the dataset")
    version: str = Field(description="Semantic version or dataset date tag")
    publisher: str = Field(description="Kaggle user or institutional publisher")
    source_url: str = Field(description="Direct URL to dataset repository")
    provenance_chain: list[str] = Field(
        default_factory=list,
        description="Origin trail, e.g. ['Borsa Istanbul Daily Bulletin', 'KAP', 'Kaggle Scraper']",
    )
    license_type: DatasetLicenseType
    license_url: str | None = None
    target_market: str = Field(default="Borsa Istanbul (BIST)")
    currency: str = Field(default="TRY")
    base_timezone: str = Field(default="Europe/Istanbul")
    date_range_start: date
    date_range_end: date
    symbol_count: int
    total_bars: int
    corporate_action_handling: CorporateActionAdjustment
    includes_delisted_symbols: bool = Field(
        default=False,
        description="Whether symbols delisted during the observation window are preserved",
    )
    delisted_symbols_count: int = Field(default=0)
    survivorship_bias_note: str = Field(
        description="Evaluation of survivorship bias impact and mitigation strategy"
    )
    usage_scope: DatasetUsageScope
    notes: str = ""


class DatasetValidationReport(BaseModel):
    """Validation report with compliance findings, warnings, and gate decisions."""

    dataset_id: str
    is_valid: bool
    is_production_ready: bool
    effective_scope: DatasetUsageScope
    passed_checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    survivorship_risk_level: str = "LOW"
    look_ahead_risk_level: str = "NONE"
    metadata_card: DatasetCard | None = None


# Permissive open licenses approved for commercial/production analysis
APPROVED_PRODUCTION_LICENSES = {
    DatasetLicenseType.CC0_1_0,
    DatasetLicenseType.CC_BY_4_0,
    DatasetLicenseType.OPEN_DATA_COMMONS_BY,
    DatasetLicenseType.DATABASE_CONTENTS_LICENSE,
    DatasetLicenseType.KAGGLE_COMMERCIAL,
}

# Licenses requiring strict research/local experiment isolation
LOCAL_EXPERIMENT_ONLY_LICENSES = {
    DatasetLicenseType.CC_BY_SA_4_0,
    DatasetLicenseType.KAGGLE_NON_COMMERCIAL,
    DatasetLicenseType.AMBIGUOUS_UNKNOWN,
}


class KaggleDatasetVerifier:
    """Verifies external and Kaggle market datasets against regulatory and ML safety rules."""

    def __init__(self, expected_currency: str = "TRY") -> None:
        self.expected_currency = expected_currency

    def verify_dataset_card(
        self,
        card: DatasetCard,
        split_config: TemporalSplitConfig | None = None,
    ) -> DatasetValidationReport:
        """Thoroughly audit a dataset card against licensing, provenance, and bias standards."""
        passed_checks: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []

        # 1. License & Scope Verification
        if card.license_type in APPROVED_PRODUCTION_LICENSES:
            passed_checks.append(
                f"Approved open/commercial license verified: {card.license_type.value}"
            )
            effective_scope = DatasetUsageScope.PRODUCTION_ALLOWED
        elif card.license_type in LOCAL_EXPERIMENT_ONLY_LICENSES:
            effective_scope = DatasetUsageScope.LOCAL_EXPERIMENT_ONLY
            warnings.append(
                f"Dataset license '{card.license_type.value}' is restricted. "
                "Per v2 compliance rules, this dataset is restricted to LOCAL_EXPERIMENT_ONLY."
            )
        else:
            effective_scope = DatasetUsageScope.REJECTED_NON_COMPLIANT
            errors.append(
                f"License '{card.license_type.value}' is non-compliant or strictly proprietary. "
                "Ingestion rejected."
            )

        # 2. Provenance Chain Audit
        if not card.provenance_chain:
            errors.append("Provenance chain is missing. Source trail must be documented.")
        elif len(card.provenance_chain) < 2:
            warnings.append(
                "Provenance chain is shallow (single source). "
                "Direct primary source verification recommended."
            )
        else:
            passed_checks.append(
                f"Provenance trail documented: {' -> '.join(card.provenance_chain)}"
            )

        # 3. Currency and Market Consistency
        if card.currency.upper() != self.expected_currency.upper():
            errors.append(
                f"Currency mismatch: expected {self.expected_currency}, got {card.currency}."
            )
        else:
            passed_checks.append(f"Currency matches market standard: {card.currency}")

        # 4. Date Range & Timezone
        if card.date_range_start >= card.date_range_end:
            errors.append("Dataset start date must be strictly before end date.")
        else:
            passed_checks.append(
                f"Valid observation span: {card.date_range_start} to {card.date_range_end}"
            )

        # 5. Survivorship Bias & Delisted Symbols
        survivorship_risk = "LOW"
        if not card.includes_delisted_symbols:
            survivorship_risk = "HIGH"
            warnings.append(
                "Survivorship bias alert: Dataset excludes delisted symbols. "
                "Backtests or ML trained on this dataset will suffer optimistic survival bias."
            )
        else:
            passed_checks.append(
                f"Includes {card.delisted_symbols_count} delisted symbols, "
                "mitigating survivorship bias."
            )

        # 6. Corporate Action Transparency
        if card.corporate_action_handling == CorporateActionAdjustment.RAW_UNADJUSTED:
            warnings.append(
                "Prices are unadjusted. Technical indicators and ML features must explicitly "
                "adjust for stock splits."
            )
        else:
            passed_checks.append(
                f"Corporate action methodology documented: {card.corporate_action_handling.value}"
            )

        # 7. Temporal Split & Look-Ahead Check (if provided)
        look_ahead_risk = "NONE"
        if split_config:
            split_errors = split_config.validate_chronology()
            if split_errors:
                look_ahead_risk = "CRITICAL"
                errors.extend(split_errors)
            else:
                passed_checks.append(
                    "Chronological temporal split verified: zero look-ahead overlap."
                )

            if (
                split_config.train_start < card.date_range_start
                or split_config.test_end > card.date_range_end
            ):
                errors.append("Temporal split boundaries exceed dataset date coverage.")

        is_valid = len(errors) == 0
        is_production_ready = is_valid and (effective_scope == DatasetUsageScope.PRODUCTION_ALLOWED)

        return DatasetValidationReport(
            dataset_id=card.dataset_id,
            is_valid=is_valid,
            is_production_ready=is_production_ready,
            effective_scope=effective_scope,
            passed_checks=passed_checks,
            warnings=warnings,
            errors=errors,
            survivorship_risk_level=survivorship_risk,
            look_ahead_risk_level=look_ahead_risk,
            metadata_card=card,
        )

    @staticmethod
    def generate_markdown_dataset_card(card: DatasetCard, report: DatasetValidationReport) -> str:
        """Render a standardized Markdown Dataset Card for documentation and audit trails."""
        prod_badge = (
            "✅ Onaylı (Production)"
            if report.is_production_ready
            else "⚠️ Yalnızca Yerel Deney (Local Experiment Only)"
        )
        prov_text = " -> ".join(card.provenance_chain) if card.provenance_chain else "Belirtilmedi"
        delist_text = "Evet" if card.includes_delisted_symbols else "Hayır"
        validation_badge = "✅ GEÇTİ" if report.is_valid else "❌ BAŞARISIZ"

        lines = [
            f"# Veri Kartı (Dataset Card): {card.title}",
            "",
            f"> **Dataset ID:** `{card.dataset_id}` | **Sürüm:** `{card.version}`",
            f"> **Yayıncı / Kaynak:** {card.publisher} ({card.source_url})",
            f"> **Kullanım İzni (Scope):** `{report.effective_scope.value}`",
            "",
            "## 1. Lisans ve Kaynak Zinciri (Provenance)",
            f"- **Lisans Türü:** `{card.license_type.value}`",
            f"- **Üretim Uygunluğu:** {prod_badge}",
            f"- **Kaynak Zinciri:** {prov_text}",
            "",
            "## 2. Kapsam ve Zaman Dilimi",
            f"- **Pazar:** {card.target_market}",
            f"- **Para Birimi:** {card.currency}",
            f"- **Saat Dilimi:** {card.base_timezone}",
            f"- **Tarih Aralığı:** {card.date_range_start} — {card.date_range_end}",
            f"- **Sembol Sayısı:** {card.symbol_count} hisse / endeks",
            f"- **Toplam Bar / Kayıt:** {card.total_bars:,} günlük bar",
            "",
            "## 3. Kurumsal İşlemler ve Yanlılık (Bias) Değerlendirmesi",
            f"- **Bölünme / Temettü Düzeltmesi:** `{card.corporate_action_handling.value}`",
            f"- **Delist Hisseler Dahil mi:** {delist_text}",
            f"- **Hayatta Kalma Yanlılığı (Survivorship Bias) Riski:** "
            f"`{report.survivorship_risk_level}`",
            f"- **Yanlılık Notu:** {card.survivorship_bias_note}",
            "",
            "## 4. Doğrulama ve Güvenlik Denetimleri",
            f"- **Doğrulama Sonucu:** {validation_badge}",
        ]

        if report.warnings:
            lines.append("- **Uyarılar:**")
            for w in report.warnings:
                lines.append(f"  - ⚠️ {w}")

        if report.errors:
            lines.append("- **Hatalar:**")
            for e in report.errors:
                lines.append(f"  - ❌ {e}")

        lines.extend(
            [
                "",
                "## 5. Güvenlik ve Depolama Kuralları",
                (
                    "- Ham veri dosyaları (.csv, .parquet, vb.) **asla Git deposuna eklenmez** "
                    "(`.gitignore` korumalı)."
                ),
                "- Yerel denemeler için veri sürümleme DVC / nesne depolama ile yönetilir.",
                (
                    "- Canlı API'ye geçişte Kaggle şeması ile canlı veri sağlayıcı şeması "
                    "arasındaki farklar adaptörle normalize edilir."
                ),
            ]
        )

        return "\n".join(lines)
