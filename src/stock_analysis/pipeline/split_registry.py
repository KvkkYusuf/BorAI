"""Data versioning and temporal split registry module.

Provides immutable persistence, cryptographic auditing, and reproducibility
verification for time series training/validation/test splits (FAZ-10):
- Tracks dataset versions, content checksums (SHA-256), and provenance metadata.
- Persists chronological train/val/test split boundaries, embargo gaps, and counts.
- Verifies split reproducibility against raw data before training PyTorch models.
- Rejects conflicting or silent version overwrites to ensure reproducible experiments.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.kaggle_verifier import TemporalSplitConfig
from stock_analysis.pipeline.time_series_splitter import (
    SplitDatasetResult,
    TimeSeriesSplitter,
)


class VersionConflictError(ValueError):
    """Raised when registering an identical version tag with conflicting content checksums."""


class DataReproducibilityError(ValueError):
    """Raised when a dataset split cannot be deterministically reproduced from recorded metadata."""


class SplitMethod(StrEnum):
    """Method used for temporal partitioning."""

    RATIO = "ratio"
    CONFIG = "config"


class DataVersionRecord(BaseModel):
    """Immutable metadata record for a specific version of a time series dataset."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    version_tag: str
    content_sha256: str
    source_uri: str
    record_count: int
    symbols: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class SplitRegistryRecord(BaseModel):
    """Immutable metadata record describing a chronological train/val/test partition."""

    model_config = ConfigDict(frozen=True)

    split_id: str
    dataset_id: str
    dataset_version: str
    split_method: SplitMethod = SplitMethod.RATIO
    train_ratio: float | None = None
    val_ratio: float | None = None
    test_ratio: float | None = None
    config: TemporalSplitConfig | None = None
    embargo_days: int = 0
    train_range: tuple[date, date] | None = None
    val_range: tuple[date, date] | None = None
    test_range: tuple[date, date] | None = None
    train_count: int = 0
    val_count: int = 0
    test_count: int = 0
    split_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    partitions_summary: list[dict[str, Any]] = Field(default_factory=list)


def _serialize_date_or_dt(obj: Any) -> Any:
    """Helper to convert dates and datetimes to ISO strings for JSON serialization."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return [_serialize_date_or_dt(item) for item in obj]
    if isinstance(obj, list):
        return [_serialize_date_or_dt(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize_date_or_dt(v) for k, v in obj.items()}
    return obj


class SplitRegistry:
    """Persistent storage and verification registry for dataset versions and time series splits."""

    def __init__(self, registry_file: Path | str | None = None) -> None:
        self.registry_file: Path | None = Path(registry_file) if registry_file else None
        self._versions: dict[str, list[DataVersionRecord]] = {}
        self._splits: dict[str, SplitRegistryRecord] = {}

        if self.registry_file and self.registry_file.exists():
            self._load_from_disk()

    @staticmethod
    def compute_content_hash(records: Sequence[Any]) -> str:
        """Computes a deterministic SHA-256 digest over sequential records."""
        hasher = hashlib.sha256()
        for item in records:
            if hasattr(item, "symbol") and hasattr(item, "timestamp"):
                token = f"{item.symbol}:{item.timestamp.isoformat()}"
                if hasattr(item, "close_price"):
                    token += f":{item.close_price}"
                elif hasattr(item, "close"):
                    token += f":{item.close}"
            elif isinstance(item, dict):
                token = json.dumps(item, sort_keys=True, default=str)
            else:
                token = str(item)
            hasher.update(token.encode("utf-8"))
        return hasher.hexdigest()

    @staticmethod
    def compute_split_hash(
        dataset_id: str,
        version_tag: str,
        train_range: tuple[date, date] | None,
        val_range: tuple[date, date] | None,
        test_range: tuple[date, date] | None,
        embargo_days: int,
    ) -> str:
        """Computes a cryptographic fingerprint of partition boundaries to ensure integrity."""
        train_payload = (
            [train_range[0].isoformat(), train_range[1].isoformat()] if train_range else None
        )
        val_payload = [val_range[0].isoformat(), val_range[1].isoformat()] if val_range else None
        test_payload = (
            [test_range[0].isoformat(), test_range[1].isoformat()] if test_range else None
        )
        payload = {
            "dataset_id": dataset_id,
            "version_tag": version_tag,
            "train_range": train_payload,
            "val_range": val_payload,
            "test_range": test_payload,
            "embargo_days": embargo_days,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def register_version(
        self,
        record: DataVersionRecord,
        *,
        allow_overwrite: bool = False,
    ) -> DataVersionRecord:
        """Registers a new dataset version record, enforcing checksum immutability."""
        existing_list = self._versions.setdefault(record.dataset_id, [])
        for i, existing in enumerate(existing_list):
            if existing.version_tag == record.version_tag:
                if existing.content_sha256 != record.content_sha256 and not allow_overwrite:
                    msg = (
                        f"Version tag '{record.version_tag}' for dataset '{record.dataset_id}' "
                        f"already exists with different checksum "
                        f"({existing.content_sha256} vs {record.content_sha256})."
                    )
                    raise VersionConflictError(msg)
                if allow_overwrite:
                    existing_list[i] = record
                    self._save_to_disk()
                    return record
                return existing

        existing_list.append(record)
        self._save_to_disk()
        return record

    def get_version(self, dataset_id: str, version_tag: str) -> DataVersionRecord | None:
        """Retrieves a specific version record for a dataset."""
        versions = self._versions.get(dataset_id, [])
        for rec in versions:
            if rec.version_tag == version_tag:
                return rec
        return None

    def list_versions(self, dataset_id: str) -> list[DataVersionRecord]:
        """Lists all registered versions for a dataset in registration order."""
        return list(self._versions.get(dataset_id, []))

    def register_split(self, split_record: SplitRegistryRecord) -> SplitRegistryRecord:
        """Registers a chronological split record."""
        self._splits[split_record.split_id] = split_record
        self._save_to_disk()
        return split_record

    def get_split(self, split_id: str) -> SplitRegistryRecord | None:
        """Retrieves a split record by split ID."""
        return self._splits.get(split_id)

    def list_splits(self, dataset_id: str | None = None) -> list[SplitRegistryRecord]:
        """Lists registered splits, optionally filtered by dataset ID."""
        if dataset_id is None:
            return list(self._splits.values())
        return [s for s in self._splits.values() if s.dataset_id == dataset_id]

    def create_and_register_ratio_split(
        self,
        dataset_id: str,
        version_tag: str,
        records: Sequence[Any],
        split_id: str,
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        embargo_days: int = 0,
    ) -> tuple[SplitDatasetResult, SplitRegistryRecord]:
        """Partitions records chronologically by ratio, hashes boundaries, and persists record."""
        _train, _val, _test, split_result = TimeSeriesSplitter.split_by_ratios(
            records=records,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            test_ratio=test_ratio,
            embargo_days=embargo_days,
        )

        split_hash = self.compute_split_hash(
            dataset_id=dataset_id,
            version_tag=version_tag,
            train_range=split_result.train_range,
            val_range=split_result.val_range,
            test_range=split_result.test_range,
            embargo_days=embargo_days,
        )

        partitions_json: list[dict[str, Any]] = [
            _serialize_date_or_dt(m.model_dump()) for m in split_result.partitions_summary
        ]

        split_rec = SplitRegistryRecord(
            split_id=split_id,
            dataset_id=dataset_id,
            dataset_version=version_tag,
            split_method=SplitMethod.RATIO,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            test_ratio=test_ratio,
            embargo_days=embargo_days,
            train_range=split_result.train_range,
            val_range=split_result.val_range,
            test_range=split_result.test_range,
            train_count=split_result.train_count,
            val_count=split_result.val_count,
            test_count=split_result.test_count,
            split_hash=split_hash,
            partitions_summary=partitions_json,
        )

        self.register_split(split_rec)
        return split_result, split_rec

    def create_and_register_config_split(
        self,
        dataset_id: str,
        version_tag: str,
        records: Sequence[Any],
        split_id: str,
        config: TemporalSplitConfig,
        embargo_days: int = 0,
    ) -> tuple[SplitDatasetResult, SplitRegistryRecord]:
        """Partitions records chronologically by date config, hashes, and persists."""
        _train, _val, _test, split_result = TimeSeriesSplitter.split_by_config(
            records=records,
            config=config,
            embargo_days=embargo_days,
        )

        split_hash = self.compute_split_hash(
            dataset_id=dataset_id,
            version_tag=version_tag,
            train_range=split_result.train_range,
            val_range=split_result.val_range,
            test_range=split_result.test_range,
            embargo_days=embargo_days,
        )

        partitions_json: list[dict[str, Any]] = [
            _serialize_date_or_dt(m.model_dump()) for m in split_result.partitions_summary
        ]

        split_rec = SplitRegistryRecord(
            split_id=split_id,
            dataset_id=dataset_id,
            dataset_version=version_tag,
            split_method=SplitMethod.CONFIG,
            config=config,
            embargo_days=embargo_days,
            train_range=split_result.train_range,
            val_range=split_result.val_range,
            test_range=split_result.test_range,
            train_count=split_result.train_count,
            val_count=split_result.val_count,
            test_count=split_result.test_count,
            split_hash=split_hash,
            partitions_summary=partitions_json,
        )

        self.register_split(split_rec)
        return split_result, split_rec

    def verify_reproducibility(
        self,
        records: Sequence[Any],
        split_record: SplitRegistryRecord,
    ) -> bool:
        """Verifies that running partition on the given dataset reproduces the recorded boundaries.

        Raises:
            DataReproducibilityError: If boundaries, record counts, or split hash deviate.
        """
        if split_record.split_method == SplitMethod.CONFIG and split_record.config is not None:
            _tr, _val, _te, reproduced = TimeSeriesSplitter.split_by_config(
                records=records,
                config=split_record.config,
                embargo_days=split_record.embargo_days,
            )
        else:
            _tr, _val, _te, reproduced = TimeSeriesSplitter.split_by_ratios(
                records=records,
                train_ratio=split_record.train_ratio or 0.70,
                val_ratio=split_record.val_ratio or 0.15,
                test_ratio=split_record.test_ratio or 0.15,
                embargo_days=split_record.embargo_days,
            )

        if reproduced.train_count != split_record.train_count:
            msg = (
                f"Reproducibility failure on train_count: expected {split_record.train_count}, "
                f"got {reproduced.train_count}."
            )
            raise DataReproducibilityError(msg)

        if reproduced.val_count != split_record.val_count:
            msg = (
                f"Reproducibility failure on val_count: expected {split_record.val_count}, "
                f"got {reproduced.val_count}."
            )
            raise DataReproducibilityError(msg)

        if reproduced.test_count != split_record.test_count:
            msg = (
                f"Reproducibility failure on test_count: expected {split_record.test_count}, "
                f"got {reproduced.test_count}."
            )
            raise DataReproducibilityError(msg)

        if reproduced.train_range != split_record.train_range:
            msg = (
                f"Reproducibility failure on train_range: expected {split_record.train_range}, "
                f"got {reproduced.train_range}."
            )
            raise DataReproducibilityError(msg)

        if reproduced.val_range != split_record.val_range:
            msg = (
                f"Reproducibility failure on val_range: expected {split_record.val_range}, "
                f"got {reproduced.val_range}."
            )
            raise DataReproducibilityError(msg)

        if reproduced.test_range != split_record.test_range:
            msg = (
                f"Reproducibility failure on test_range: expected {split_record.test_range}, "
                f"got {reproduced.test_range}."
            )
            raise DataReproducibilityError(msg)

        calculated_hash = self.compute_split_hash(
            dataset_id=split_record.dataset_id,
            version_tag=split_record.dataset_version,
            train_range=reproduced.train_range,
            val_range=reproduced.val_range,
            test_range=reproduced.test_range,
            embargo_days=split_record.embargo_days,
        )
        if calculated_hash != split_record.split_hash:
            msg = (
                f"Reproducibility failure on split_hash: expected {split_record.split_hash}, "
                f"got {calculated_hash}."
            )
            raise DataReproducibilityError(msg)

        return True

    def export_manifest(self) -> dict[str, Any]:
        """Exports full manifest of registered datasets, versions, and splits."""
        return {
            "version_count": sum(len(v) for v in self._versions.values()),
            "split_count": len(self._splits),
            "datasets": {
                dataset_id: [_serialize_date_or_dt(v.model_dump()) for v in version_list]
                for dataset_id, version_list in self._versions.items()
            },
            "splits": {
                split_id: _serialize_date_or_dt(split_rec.model_dump())
                for split_id, split_rec in self._splits.items()
            },
        }

    def _save_to_disk(self) -> None:
        """Atomically saves the registry state to disk as JSON if registry_file is configured."""
        if not self.registry_file:
            return

        manifest = self.export_manifest()
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.registry_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp_file.replace(self.registry_file)

    def _load_from_disk(self) -> None:
        """Loads registered versions and splits from disk."""
        if not self.registry_file or not self.registry_file.exists():
            return

        data = json.loads(self.registry_file.read_text(encoding="utf-8"))

        self._versions.clear()
        for dataset_id, v_list in data.get("datasets", {}).items():
            self._versions[dataset_id] = [DataVersionRecord.model_validate(v) for v in v_list]

        self._splits.clear()
        for split_id, s_dict in data.get("splits", {}).items():
            for field in ["train_range", "val_range", "test_range"]:
                val = s_dict.get(field)
                if val and isinstance(val, list) and len(val) == 2:
                    s_dict[field] = (date.fromisoformat(val[0]), date.fromisoformat(val[1]))
            self._splits[split_id] = SplitRegistryRecord.model_validate(s_dict)
