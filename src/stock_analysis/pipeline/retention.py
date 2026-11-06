"""Data retention tiers and raw market data backup policy enforcement.

Defines:
- Formal retention tiers (Immutable Raw EOD, Derived series, Audit logs, Cache).
- Retention evaluation logic and lifecycle rules.
- Raw market data backup policies with WORM (Write Once, Read Many) integrity.
- Deterministic backup manifest generation and SHA-256 tamper verification.
- RPO (Recovery Point Objective) and RTO (Recovery Time Objective) targets.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_analysis.pipeline.storage import RawBarRecord


class DataRetentionTier(StrEnum):
    """Categorized data lifecycle tiers with specific retention guarantees."""

    RAW_EOD = "raw_eod"  # Permanent historical immutable market bars (10+ years / indefinite)
    DERIVED_SERIES = "derived_series"  # Cleansed/adjusted datasets (recomputable on demand)
    AUDIT_LOGS = "audit_logs"  # Data integrity and provider audit reports (min 1-3 years)
    CACHE = "cache"  # Ephemeral responses and quotes (short TTL, e.g. minutes to 24h)


class RetentionPolicy(BaseModel):
    """Retention rule definition for a specific data tier."""

    model_config = ConfigDict(frozen=True)

    tier: DataRetentionTier
    retention_days: int | None = Field(
        default=None,
        description="Number of days to retain data. None means indefinite/permanent retention.",
    )
    is_immutable: bool = Field(
        default=False,
        description="Whether records in this tier are strictly append-only and immutable.",
    )
    can_prune: bool = Field(
        default=False,
        description="Whether automated pruning/eviction is allowed after retention expiry.",
    )

    @model_validator(mode="after")
    def validate_policy_consistency(self) -> Self:
        """Enforce that immutable raw data cannot be pruned or given arbitrary short retention."""
        if self.tier == DataRetentionTier.RAW_EOD:
            if not self.is_immutable:
                raise ValueError("RAW_EOD tier must be marked immutable.")
            if self.can_prune:
                raise ValueError("RAW_EOD tier cannot be configured for automated pruning.")
            if self.retention_days is not None and self.retention_days < 3650:
                raise ValueError(
                    "RAW_EOD regulatory retention period must be at least "
                    "3650 days (10 years) or indefinite."
                )
        return self

    def is_expired(self, created_at: datetime, as_of: datetime | None = None) -> bool:
        """Check whether data created at given timestamp has exceeded retention lifespan."""
        if self.retention_days is None:
            return False
        reference_time = as_of or datetime.now(UTC)
        expiry_threshold = created_at + timedelta(days=self.retention_days)
        return reference_time > expiry_threshold


def get_default_retention_policies() -> dict[DataRetentionTier, RetentionPolicy]:
    """Return established organizational retention policies across all tiers."""
    return {
        DataRetentionTier.RAW_EOD: RetentionPolicy(
            tier=DataRetentionTier.RAW_EOD,
            retention_days=None,  # Indefinite retention
            is_immutable=True,
            can_prune=False,
        ),
        DataRetentionTier.DERIVED_SERIES: RetentionPolicy(
            tier=DataRetentionTier.DERIVED_SERIES,
            retention_days=365,  # 1 year warm retention; recomputable on demand
            is_immutable=False,
            can_prune=True,
        ),
        DataRetentionTier.AUDIT_LOGS: RetentionPolicy(
            tier=DataRetentionTier.AUDIT_LOGS,
            retention_days=1095,  # 3 years regulatory audit compliance
            is_immutable=True,
            can_prune=False,
        ),
        DataRetentionTier.CACHE: RetentionPolicy(
            tier=DataRetentionTier.CACHE,
            retention_days=1,  # 24 hours max
            is_immutable=False,
            can_prune=True,
        ),
    }


class RawDataBackupPolicy(BaseModel):
    """Disaster recovery and backup execution standards for immutable raw data."""

    model_config = ConfigDict(frozen=True)

    backup_interval_hours: int = Field(
        default=24, ge=1, le=168, description="Backup cycle frequency (default: daily EOD)"
    )
    rpo_target_hours: int = Field(
        default=24,
        ge=1,
        description="Recovery Point Objective (maximum acceptable data loss window)",
    )
    rto_target_hours: int = Field(
        default=1, ge=1, description="Recovery Time Objective (maximum time to restore raw data)"
    )
    encryption_algorithm: str = Field(
        default="AES-256-GCM", description="Mandatory encryption cipher at rest"
    )
    worm_compliant: bool = Field(
        default=True, description="Write-Once-Read-Many enforcement on cold backup archives"
    )
    verification_frequency_days: int = Field(
        default=90, ge=1, description="Quarterly restore drill cycle in days"
    )


class BackupManifest(BaseModel):
    """Cryptographic manifest documenting a backup archive and verifying content integrity."""

    model_config = ConfigDict(frozen=True)

    manifest_id: str = Field(description="Unique identifier of the backup snapshot")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when snapshot was archived",
    )
    record_count: int = Field(ge=0, description="Total raw bar records in snapshot")
    symbols: tuple[str, ...] = Field(description="Sorted list of tickers included")
    earliest_date: date | None = Field(default=None, description="Oldest trade date in archive")
    latest_date: date | None = Field(default=None, description="Newest trade date in archive")
    manifest_checksum: str = Field(
        description="SHA-256 digest calculated over sorted records payload hashes"
    )
    is_encrypted: bool = Field(default=True, description="Whether backup payload is encrypted")
    backup_policy: RawDataBackupPolicy = Field(default_factory=RawDataBackupPolicy)


def create_raw_backup_manifest(
    records: Sequence[RawBarRecord],
    manifest_id: str,
    is_encrypted: bool = True,
    policy: RawDataBackupPolicy | None = None,
    created_at: datetime | None = None,
) -> BackupManifest:
    """Generate a tamper-evident BackupManifest from a sequence of raw bar records."""
    if not records:
        hasher = hashlib.sha256(b"EMPTY_BACKUP")
        return BackupManifest(
            manifest_id=manifest_id,
            created_at=created_at or datetime.now(UTC),
            record_count=0,
            symbols=(),
            earliest_date=None,
            latest_date=None,
            manifest_checksum=hasher.hexdigest(),
            is_encrypted=is_encrypted,
            backup_policy=policy or RawDataBackupPolicy(),
        )

    # Sort records deterministically by symbol, trade_date, payload_hash
    sorted_records = sorted(
        records, key=lambda r: (r.symbol.upper(), r.trade_date, r.raw_payload_hash)
    )

    hasher = hashlib.sha256()
    for rec in sorted_records:
        entry_sig = f"{rec.symbol.upper()}:{rec.trade_date.isoformat()}:{rec.raw_payload_hash}"
        hasher.update(entry_sig.encode("utf-8"))

    symbols = tuple(sorted({r.symbol.upper() for r in records}))
    dates = [r.trade_date for r in records]

    return BackupManifest(
        manifest_id=manifest_id,
        created_at=created_at or datetime.now(UTC),
        record_count=len(records),
        symbols=symbols,
        earliest_date=min(dates),
        latest_date=max(dates),
        manifest_checksum=hasher.hexdigest(),
        is_encrypted=is_encrypted,
        backup_policy=policy or RawDataBackupPolicy(),
    )


def verify_raw_backup_integrity(
    records: Sequence[RawBarRecord],
    manifest: BackupManifest,
) -> bool:
    """Verify that records correspond exactly to the checksum in BackupManifest."""
    expected_manifest = create_raw_backup_manifest(
        records=records,
        manifest_id=manifest.manifest_id,
        is_encrypted=manifest.is_encrypted,
        policy=manifest.backup_policy,
        created_at=manifest.created_at,
    )
    return expected_manifest.manifest_checksum == manifest.manifest_checksum
