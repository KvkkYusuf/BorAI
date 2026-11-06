"""Unit and compliance tests for Kaggle and external dataset verification.

Covers FAZ-10 requirements:
- License & provenance verification.
- Enforcing LOCAL_EXPERIMENT_ONLY for ambiguous/unknown licenses (v2 rule).
- Rejection of proprietary/unauthorized datasets.
- Survivorship bias detection (delisted symbol tracking).
- Look-ahead bias prevention via chronological temporal splits.
- Currency and market consistency checks.
- Dataset card markdown rendering.
"""

from datetime import date

import pytest

from stock_analysis.pipeline.kaggle_verifier import (
    CorporateActionAdjustment,
    DatasetCard,
    DatasetLicenseType,
    DatasetUsageScope,
    KaggleDatasetVerifier,
    TemporalSplitConfig,
)


@pytest.fixture
def base_valid_dataset_card() -> DatasetCard:
    """Fixture providing a compliant BIST dataset card."""
    return DatasetCard(
        dataset_id="bist-daily-stock-historical-data",
        title="Borsa Istanbul Daily OHLCV & Corporate Actions",
        version="2025.1",
        publisher="BorAI Open Data Project",
        source_url="https://www.kaggle.com/datasets/borai/bist-daily-stock-historical-data",
        provenance_chain=[
            "Kamuyu Aydinlatma Platformu (KAP)",
            "Borsa Istanbul Gunluk Bulten",
            "Kaggle Community Dataset",
        ],
        license_type=DatasetLicenseType.CC_BY_4_0,
        license_url="https://creativecommons.org/licenses/by/4.0/",
        target_market="Borsa Istanbul (BIST)",
        currency="TRY",
        base_timezone="Europe/Istanbul",
        date_range_start=date(2010, 1, 4),
        date_range_end=date(2025, 1, 3),
        symbol_count=542,
        total_bars=1_450_000,
        corporate_action_handling=CorporateActionAdjustment.DUAL_STREAM,
        includes_delisted_symbols=True,
        delisted_symbols_count=48,
        survivorship_bias_note=(
            "Includes historical delisted equity symbols across observation window."
        ),
        usage_scope=DatasetUsageScope.PRODUCTION_ALLOWED,
        notes="Strictly tested and verified against official KAP corporate actions.",
    )


def test_approved_open_license_production_ready(base_valid_dataset_card: DatasetCard) -> None:
    verifier = KaggleDatasetVerifier(expected_currency="TRY")
    report = verifier.verify_dataset_card(base_valid_dataset_card)

    assert report.is_valid is True
    assert report.is_production_ready is True
    assert report.effective_scope == DatasetUsageScope.PRODUCTION_ALLOWED
    assert report.survivorship_risk_level == "LOW"
    assert report.look_ahead_risk_level == "NONE"
    assert len(report.errors) == 0
    assert any("Approved open/commercial license verified" in p for p in report.passed_checks)


def test_ambiguous_license_restricted_to_local_experiment(
    base_valid_dataset_card: DatasetCard,
) -> None:
    """Per v2 rule: Ambiguous/unknown licenses must be restricted to LOCAL_EXPERIMENT_ONLY."""
    card_dict = base_valid_dataset_card.model_dump()
    card_dict["license_type"] = DatasetLicenseType.AMBIGUOUS_UNKNOWN
    ambiguous_card = DatasetCard(**card_dict)

    verifier = KaggleDatasetVerifier()
    report = verifier.verify_dataset_card(ambiguous_card)

    assert report.is_valid is True
    assert report.is_production_ready is False
    assert report.effective_scope == DatasetUsageScope.LOCAL_EXPERIMENT_ONLY
    assert any("restricted to LOCAL_EXPERIMENT_ONLY" in w for w in report.warnings)


def test_proprietary_license_rejected(base_valid_dataset_card: DatasetCard) -> None:
    """Proprietary unlicensed data must be rejected from the system."""
    card_dict = base_valid_dataset_card.model_dump()
    card_dict["license_type"] = DatasetLicenseType.PROPRIETARY
    proprietary_card = DatasetCard(**card_dict)

    verifier = KaggleDatasetVerifier()
    report = verifier.verify_dataset_card(proprietary_card)

    assert report.is_valid is False
    assert report.is_production_ready is False
    assert report.effective_scope == DatasetUsageScope.REJECTED_NON_COMPLIANT
    assert any("strictly proprietary" in e for e in report.errors)


def test_survivorship_bias_warning_when_delisted_excluded(
    base_valid_dataset_card: DatasetCard,
) -> None:
    """Excluding delisted tickers should elevate survivorship risk to HIGH."""
    card_dict = base_valid_dataset_card.model_dump()
    card_dict["includes_delisted_symbols"] = False
    card_dict["delisted_symbols_count"] = 0
    card_dict["survivorship_bias_note"] = "Only currently active symbols included."
    survivor_card = DatasetCard(**card_dict)

    verifier = KaggleDatasetVerifier()
    report = verifier.verify_dataset_card(survivor_card)

    assert report.is_valid is True
    assert report.survivorship_risk_level == "HIGH"
    assert any("Survivorship bias alert" in w for w in report.warnings)


def test_currency_and_date_range_validation(base_valid_dataset_card: DatasetCard) -> None:
    """Verify detection of currency mismatch and inverted date ranges."""
    card_dict = base_valid_dataset_card.model_dump()
    card_dict["currency"] = "USD"
    card_dict["date_range_start"] = date(2025, 1, 1)
    card_dict["date_range_end"] = date(2020, 1, 1)
    invalid_card = DatasetCard(**card_dict)

    verifier = KaggleDatasetVerifier(expected_currency="TRY")
    report = verifier.verify_dataset_card(invalid_card)

    assert report.is_valid is False
    assert any("Currency mismatch" in e for e in report.errors)
    assert any("start date must be strictly before end date" in e for e in report.errors)


def test_temporal_split_chronology_and_lookahead_bias(base_valid_dataset_card: DatasetCard) -> None:
    """Verify chronological split validation and detection of look-ahead leakage."""
    valid_split = TemporalSplitConfig(
        train_start=date(2010, 1, 4),
        train_end=date(2021, 12, 31),
        val_start=date(2022, 1, 3),
        val_end=date(2023, 6, 30),
        test_start=date(2023, 7, 3),
        test_end=date(2024, 12, 31),
    )

    verifier = KaggleDatasetVerifier()
    report = verifier.verify_dataset_card(base_valid_dataset_card, split_config=valid_split)

    assert report.is_valid is True
    assert report.look_ahead_risk_level == "NONE"
    assert any("zero look-ahead overlap" in p for p in report.passed_checks)

    # Overlapping train/val boundaries
    overlapping_split = TemporalSplitConfig(
        train_start=date(2010, 1, 4),
        train_end=date(2022, 5, 1),  # Overlaps val_start
        val_start=date(2022, 1, 3),
        val_end=date(2023, 6, 30),
        test_start=date(2023, 7, 3),
        test_end=date(2024, 12, 31),
    )
    overlap_report = verifier.verify_dataset_card(
        base_valid_dataset_card, split_config=overlapping_split
    )
    assert overlap_report.is_valid is False
    assert overlap_report.look_ahead_risk_level == "CRITICAL"
    assert any("temporal overlap detected" in e for e in overlap_report.errors)


def test_generate_markdown_dataset_card(base_valid_dataset_card: DatasetCard) -> None:
    verifier = KaggleDatasetVerifier()
    report = verifier.verify_dataset_card(base_valid_dataset_card)
    md = verifier.generate_markdown_dataset_card(base_valid_dataset_card, report)

    assert "# Veri Kartı (Dataset Card):" in md
    assert "bist-daily-stock-historical-data" in md
    assert "CC-BY-4.0" in md
    assert "production_allowed" in md
    assert "Hayatta Kalma Yanlılığı (Survivorship Bias) Riski:" in md
