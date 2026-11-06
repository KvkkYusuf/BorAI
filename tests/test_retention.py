"""Tests for data retention tiers, lifecycle expiry, and raw backup integrity verification."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from stock_analysis.pipeline.retention import (
    DataRetentionTier,
    RawDataBackupPolicy,
    RetentionPolicy,
    create_raw_backup_manifest,
    get_default_retention_policies,
    verify_raw_backup_integrity,
)
from stock_analysis.pipeline.storage import RawBarRecord
from stock_analysis.providers.base import MarketBarRecord


def _make_sample_raw_bar(
    trade_date: date,
    symbol: str = "THYAO",
    close_price: str = "100.00",
) -> RawBarRecord:
    close_dec = Decimal(close_price)
    open_dec = Decimal("100.00")
    high_dec = max(open_dec, close_dec) + Decimal("5.00")
    low_dec = min(open_dec, close_dec) - Decimal("5.00")
    bar = MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        raw_open=open_dec,
        raw_high=high_dec,
        raw_low=low_dec,
        raw_close=close_dec,
        adj_open=open_dec,
        adj_high=high_dec,
        adj_low=low_dec,
        adj_close=close_dec,
        volume=10000,
        turnover_try=Decimal("1000000.00"),
        source_name="bist_feed",
    )
    return RawBarRecord.from_market_bar(bar)


class TestRetentionPolicy:
    """Tests for retention policy configuration, defaults, and expiry evaluation."""

    def test_default_retention_policies(self) -> None:
        policies = get_default_retention_policies()
        assert len(policies) == 4

        raw_policy = policies[DataRetentionTier.RAW_EOD]
        assert raw_policy.is_immutable is True
        assert raw_policy.can_prune is False
        assert raw_policy.retention_days is None

        cache_policy = policies[DataRetentionTier.CACHE]
        assert cache_policy.can_prune is True
        assert cache_policy.retention_days == 1

        derived_policy = policies[DataRetentionTier.DERIVED_SERIES]
        assert derived_policy.retention_days == 365
        assert derived_policy.can_prune is True

        audit_policy = policies[DataRetentionTier.AUDIT_LOGS]
        assert audit_policy.retention_days == 1095
        assert audit_policy.is_immutable is True

    def test_raw_eod_policy_enforcement_prevents_pruning(self) -> None:
        err_msg = "RAW_EOD tier cannot be configured for automated pruning"
        with pytest.raises(ValidationError, match=err_msg):
            RetentionPolicy(
                tier=DataRetentionTier.RAW_EOD,
                is_immutable=True,
                can_prune=True,
            )

    def test_raw_eod_policy_must_be_immutable(self) -> None:
        with pytest.raises(ValidationError, match="RAW_EOD tier must be marked immutable"):
            RetentionPolicy(
                tier=DataRetentionTier.RAW_EOD,
                is_immutable=False,
                can_prune=False,
            )

    def test_raw_eod_policy_minimum_retention_days(self) -> None:
        err_msg = "regulatory retention period must be at least 3650 days"
        with pytest.raises(ValidationError, match=err_msg):
            RetentionPolicy(
                tier=DataRetentionTier.RAW_EOD,
                is_immutable=True,
                can_prune=False,
                retention_days=100,
            )

    def test_retention_expiry_evaluation(self) -> None:
        policy = RetentionPolicy(
            tier=DataRetentionTier.CACHE,
            retention_days=1,
            is_immutable=False,
            can_prune=True,
        )
        now = datetime.now(UTC)

        fresh_time = now - timedelta(hours=12)
        assert policy.is_expired(fresh_time, as_of=now) is False

        expired_time = now - timedelta(hours=26)
        assert policy.is_expired(expired_time, as_of=now) is True

    def test_indefinite_retention_never_expires(self) -> None:
        policy = RetentionPolicy(
            tier=DataRetentionTier.RAW_EOD,
            retention_days=None,
            is_immutable=True,
            can_prune=False,
        )
        ancient_time = datetime(1990, 1, 1, tzinfo=UTC)
        assert policy.is_expired(ancient_time) is False


class TestRawDataBackupPolicyAndManifest:
    """Tests for backup manifest generation, integrity checking, and tamper detection."""

    def test_empty_backup_manifest(self) -> None:
        manifest = create_raw_backup_manifest(records=[], manifest_id="bkp_empty")
        assert manifest.record_count == 0
        assert manifest.symbols == ()
        assert manifest.earliest_date is None
        assert manifest.latest_date is None
        assert len(manifest.manifest_checksum) == 64
        assert verify_raw_backup_integrity([], manifest) is True

    def test_manifest_creation_and_integrity_verification(self) -> None:
        records = [
            _make_sample_raw_bar(date(2026, 1, 5), "THYAO", "100.00"),
            _make_sample_raw_bar(date(2026, 1, 6), "THYAO", "102.50"),
            _make_sample_raw_bar(date(2026, 1, 5), "ASELS", "65.00"),
        ]

        manifest = create_raw_backup_manifest(
            records=records,
            manifest_id="bkp_2026_01",
            is_encrypted=True,
        )

        assert manifest.manifest_id == "bkp_2026_01"
        assert manifest.record_count == 3
        assert manifest.symbols == ("ASELS", "THYAO")
        assert manifest.earliest_date == date(2026, 1, 5)
        assert manifest.latest_date == date(2026, 1, 6)
        assert manifest.is_encrypted is True
        assert verify_raw_backup_integrity(records, manifest) is True

    def test_manifest_order_insensitivity(self) -> None:
        rec1 = _make_sample_raw_bar(date(2026, 1, 5), "THYAO")
        rec2 = _make_sample_raw_bar(date(2026, 1, 6), "THYAO")
        rec3 = _make_sample_raw_bar(date(2026, 1, 5), "BIMAS")

        m1 = create_raw_backup_manifest([rec1, rec2, rec3], "m1")
        m2 = create_raw_backup_manifest([rec3, rec1, rec2], "m1")

        assert m1.manifest_checksum == m2.manifest_checksum

    def test_tamper_detection(self) -> None:
        records = [
            _make_sample_raw_bar(date(2026, 1, 5), "THYAO", "100.00"),
            _make_sample_raw_bar(date(2026, 1, 6), "THYAO", "102.50"),
        ]
        manifest = create_raw_backup_manifest(records, "bkp_tamper_test")

        # Tampered records: modified close price
        tampered_records = [
            _make_sample_raw_bar(date(2026, 1, 5), "THYAO", "100.00"),
            _make_sample_raw_bar(date(2026, 1, 6), "THYAO", "103.00"),  # altered price
        ]

        assert verify_raw_backup_integrity(tampered_records, manifest) is False

    def test_missing_records_detection(self) -> None:
        records = [
            _make_sample_raw_bar(date(2026, 1, 5), "THYAO", "100.00"),
            _make_sample_raw_bar(date(2026, 1, 6), "THYAO", "102.50"),
        ]
        manifest = create_raw_backup_manifest(records, "bkp_missing_test")

        missing_records = records[:1]
        assert verify_raw_backup_integrity(missing_records, manifest) is False

    def test_custom_backup_policy_bounds(self) -> None:
        policy = RawDataBackupPolicy(
            backup_interval_hours=12,
            rpo_target_hours=12,
            rto_target_hours=2,
            encryption_algorithm="AES-256-GCM",
            worm_compliant=True,
            verification_frequency_days=30,
        )
        assert policy.backup_interval_hours == 12
        assert policy.verification_frequency_days == 30

        with pytest.raises(ValidationError):
            RawDataBackupPolicy(backup_interval_hours=0)
