"""Tests for data versioning and temporal split registry module."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from stock_analysis.pipeline.kaggle_verifier import TemporalSplitConfig
from stock_analysis.pipeline.split_registry import (
    DataReproducibilityError,
    DataVersionRecord,
    SplitMethod,
    SplitRegistry,
    VersionConflictError,
)


def _create_dummy_bars(
    start_date: date = date(2023, 1, 1),
    days: int = 100,
    symbol: str = "THYAO",
) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for i in range(days):
        cur_dt = start_date + timedelta(days=i)
        price = 100.0 + (i * 0.5)
        bars.append(
            {
                "symbol": symbol,
                "date": cur_dt,
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.2,
                "volume": 1000.0 + i * 10,
            }
        )
    return bars


def test_compute_content_hash_deterministic() -> None:
    bars1 = _create_dummy_bars(days=20)
    bars2 = _create_dummy_bars(days=20)

    hash1 = SplitRegistry.compute_content_hash(bars1)
    hash2 = SplitRegistry.compute_content_hash(bars2)
    assert hash1 == hash2
    assert len(hash1) == 64

    bars_diff = _create_dummy_bars(days=21)
    hash3 = SplitRegistry.compute_content_hash(bars_diff)
    assert hash1 != hash3


def test_register_version_success_and_conflict() -> None:
    registry = SplitRegistry()
    bars = _create_dummy_bars(days=30)
    content_hash = SplitRegistry.compute_content_hash(bars)

    ver = DataVersionRecord(
        dataset_id="bist30-kaggle-daily",
        version_tag="v1.0.0",
        content_sha256=content_hash,
        source_uri="data/raw/kaggle/bist30_2020_2025.csv",
        record_count=len(bars),
        symbols=["THYAO"],
        start_date=bars[0]["date"],  # type: ignore[arg-type]
        end_date=bars[-1]["date"],  # type: ignore[arg-type]
    )

    registered = registry.register_version(ver)
    assert registered.version_tag == "v1.0.0"
    assert registry.get_version("bist30-kaggle-daily", "v1.0.0") == ver

    # Idempotent re-registration of exact same content succeeds
    re_registered = registry.register_version(ver)
    assert re_registered == ver

    # Conflict with different hash raises VersionConflictError
    conflicting_ver = DataVersionRecord(
        dataset_id="bist30-kaggle-daily",
        version_tag="v1.0.0",
        content_sha256="deadbeef" * 8,
        source_uri="data/raw/kaggle/bist30_2020_2025.csv",
        record_count=len(bars),
        symbols=["THYAO"],
    )
    with pytest.raises(VersionConflictError, match="already exists with different checksum"):
        registry.register_version(conflicting_ver)

    # With allow_overwrite=True, it succeeds
    overwritten = registry.register_version(conflicting_ver, allow_overwrite=True)
    assert overwritten.content_sha256 == "deadbeef" * 8


def test_create_and_register_ratio_split_and_verify(tmp_path: Path) -> None:
    reg_file = tmp_path / "splits_registry.json"
    registry = SplitRegistry(registry_file=reg_file)

    bars = _create_dummy_bars(days=100)

    split_res, split_rec = registry.create_and_register_ratio_split(
        dataset_id="bist30-kaggle-daily",
        version_tag="v1.0.0",
        records=bars,
        split_id="split_bist30_ratio_v1",
        train_ratio=0.7,
        val_ratio=0.15,
        test_ratio=0.15,
        embargo_days=2,
    )

    assert split_res.train_count > 0
    assert split_res.val_count > 0
    assert split_res.test_count > 0
    assert split_rec.split_id == "split_bist30_ratio_v1"
    assert split_rec.split_method == SplitMethod.RATIO
    assert split_rec.embargo_days == 2
    assert split_rec.split_hash != ""

    # Check persistence
    assert reg_file.exists()
    registry_reloaded = SplitRegistry(registry_file=reg_file)
    loaded_split = registry_reloaded.get_split("split_bist30_ratio_v1")
    assert loaded_split is not None
    assert loaded_split.train_count == split_rec.train_count
    assert loaded_split.train_range == split_rec.train_range

    # Verify reproducibility passes on identical records
    assert registry_reloaded.verify_reproducibility(bars, loaded_split) is True


def test_create_and_register_config_split_and_verify(tmp_path: Path) -> None:
    reg_file = tmp_path / "splits_config_registry.json"
    registry = SplitRegistry(registry_file=reg_file)

    bars = _create_dummy_bars(days=100)
    config = TemporalSplitConfig(
        train_start=date(2023, 1, 1),
        train_end=date(2023, 2, 10),
        val_start=date(2023, 2, 15),
        val_end=date(2023, 3, 5),
        test_start=date(2023, 3, 10),
        test_end=date(2023, 4, 10),
    )

    split_res, split_rec = registry.create_and_register_config_split(
        dataset_id="bist30-kaggle-daily",
        version_tag="v1.0.0",
        records=bars,
        split_id="split_bist30_config_v1",
        config=config,
        embargo_days=2,
    )

    assert split_res.train_count > 0
    assert split_res.val_count > 0
    assert split_res.test_count > 0
    assert split_rec.split_method == SplitMethod.CONFIG

    # Check persistence and reload
    registry_reloaded = SplitRegistry(registry_file=reg_file)
    loaded_split = registry_reloaded.get_split("split_bist30_config_v1")
    assert loaded_split is not None
    assert registry_reloaded.verify_reproducibility(bars, loaded_split) is True


def test_reproducibility_failure_on_mutated_data(tmp_path: Path) -> None:
    reg_file = tmp_path / "splits_registry.json"
    registry = SplitRegistry(registry_file=reg_file)

    bars = _create_dummy_bars(days=100)

    _, split_rec = registry.create_and_register_ratio_split(
        dataset_id="bist30-kaggle-daily",
        version_tag="v1.0.0",
        records=bars,
        split_id="split_bist30_v1",
        train_ratio=0.7,
        val_ratio=0.15,
        test_ratio=0.15,
        embargo_days=1,
    )

    # Missing 10 records: reproducibility must fail
    tampered_bars = bars[:90]
    with pytest.raises(DataReproducibilityError, match="Reproducibility failure"):
        registry.verify_reproducibility(tampered_bars, split_rec)


def test_list_splits_and_manifest() -> None:
    registry = SplitRegistry()
    bars = _create_dummy_bars(days=50)

    ver = DataVersionRecord(
        dataset_id="dataset_a",
        version_tag="v1",
        content_sha256="abc",
        source_uri="uri://a",
        record_count=50,
    )
    registry.register_version(ver)

    registry.create_and_register_ratio_split(
        dataset_id="dataset_a",
        version_tag="v1",
        records=bars,
        split_id="split_a1",
        train_ratio=0.8,
        val_ratio=0.1,
        test_ratio=0.1,
        embargo_days=0,
    )

    manifest = registry.export_manifest()
    assert manifest["version_count"] == 1
    assert manifest["split_count"] == 1
    assert "dataset_a" in manifest["datasets"]
    assert "split_a1" in manifest["splits"]

    # Filter splits
    splits_a = registry.list_splits(dataset_id="dataset_a")
    assert len(splits_a) == 1
    splits_b = registry.list_splits(dataset_id="dataset_b")
    assert len(splits_b) == 0
