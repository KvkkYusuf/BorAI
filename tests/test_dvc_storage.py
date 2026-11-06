"""Tests for DVC dataset versioning, object storage, and Git raw data leak guard (FAZ-10)."""

from pathlib import Path

import pytest

from stock_analysis.pipeline.dvc_storage import (
    ChecksumMismatchError,
    DvcDatasetManager,
    DvcFileOutRecord,
    DvcPointer,
    GitRawDataLeakageError,
    GitRawDataLeakGuard,
    InMemoryObjectStorage,
    LocalObjectStorage,
)


class TestDvcPointer:
    """Test DVC pointer structure, serialization, and deserialization."""

    def test_dvc_pointer_to_yaml_and_load(self, tmp_path: Path) -> None:
        out = DvcFileOutRecord(
            md5="e10adc3949ba59abbe56e057f20f883e",
            size=12345,
            path="bist_eod_2025.parquet",
            hash="md5",
        )
        pointer = DvcPointer(
            outs=[out],
            remote="s3://bist-lake/datasets/raw/",
            meta={"dataset_id": "bist-daily-raw", "symbols": ["THYAO", "GARAN"]},
        )

        yaml_content = pointer.to_yaml()
        assert "bist_eod_2025.parquet" in yaml_content
        assert "e10adc3949ba59abbe56e057f20f883e" in yaml_content
        assert "s3://bist-lake/datasets/raw/" in yaml_content

        target_file = tmp_path / "bist_eod_2025.parquet.dvc"
        pointer.save(target_file)
        assert target_file.exists()

        loaded = DvcPointer.load(target_file)
        assert len(loaded.outs) == 1
        assert loaded.outs[0].md5 == "e10adc3949ba59abbe56e057f20f883e"
        assert loaded.outs[0].size == 12345
        assert loaded.outs[0].path == "bist_eod_2025.parquet"
        assert loaded.remote == "s3://bist-lake/datasets/raw/"
        assert loaded.meta.get("dataset_id") == "bist-daily-raw"
        assert loaded.meta.get("symbols") == ["THYAO", "GARAN"]

    def test_dvc_pointer_load_missing_file_raises(self, tmp_path: Path) -> None:
        missing_path = tmp_path / "non_existent.dvc"
        with pytest.raises(FileNotFoundError, match="DVC pointer file not found"):
            DvcPointer.load(missing_path)

    def test_dvc_pointer_invalid_yaml_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid DVC YAML format"):
            DvcPointer.from_yaml("just a string, not a dictionary")


class TestObjectStorageBackends:
    """Test InMemoryObjectStorage and LocalObjectStorage backends."""

    def test_in_memory_storage(self) -> None:
        backend = InMemoryObjectStorage()
        assert not backend.exists("sample_key")

        uri = backend.upload("sample_key", b"sample raw market content")
        assert uri == "mem://sample_key"
        assert backend.exists("sample_key")
        assert backend.download("sample_key") == b"sample raw market content"
        assert backend.stored_keys == ["sample_key"]

        assert backend.delete("sample_key") is True
        assert not backend.exists("sample_key")
        assert backend.delete("sample_key") is False

        with pytest.raises(KeyError, match="Key not found"):
            backend.download("sample_key")

    def test_local_object_storage(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "blob_store"
        backend = LocalObjectStorage(base_dir)

        test_key = "a1b2c3d4e5f67890"
        data = b"raw parquet binary content with headers and compression"
        uri = backend.upload(test_key, data)

        assert uri.startswith("file://")
        # Should be stored under base_dir / a1 / b2c3d4e5f67890
        expected_subpath = base_dir / "a1" / "b2c3d4e5f67890"
        assert expected_subpath.exists()
        assert backend.exists(test_key)
        assert backend.download(test_key) == data

        assert backend.delete(test_key) is True
        assert not backend.exists(test_key)
        assert backend.delete(test_key) is False

        with pytest.raises(FileNotFoundError, match="Key not found"):
            backend.download(test_key)


class TestDvcDatasetManager:
    """Test dataset tracking, pulling, and integrity verification."""

    def test_track_and_pull_dataset(self, tmp_path: Path) -> None:
        manager = DvcDatasetManager()
        backend = InMemoryObjectStorage()

        raw_file = tmp_path / "bist_30_sample.parquet"
        content = b"PARQUET_MAGIC_BYTES_1234567890_BIST_DATA"
        raw_file.write_bytes(content)

        # 1. Track raw dataset
        pointer = manager.track_dataset(
            file_path=raw_file,
            remote_backend=backend,
            meta={"provider": "kaggle", "license": "CC-BY-4.0"},
        )

        assert len(pointer.outs) == 1
        assert pointer.outs[0].size == len(content)
        assert pointer.outs[0].path == "bist_30_sample.parquet"
        assert pointer.meta.get("provider") == "kaggle"
        assert "sha256" in pointer.meta

        dvc_file = tmp_path / "bist_30_sample.parquet.dvc"
        assert dvc_file.exists()

        # 2. Verify integrity of existing file
        assert manager.verify_integrity(pointer, raw_file) is True

        # 3. Pull dataset to a new location
        restored_file = tmp_path / "pulled_dataset" / "bist_30_restored.parquet"
        pulled_path = manager.pull_dataset(
            pointer=dvc_file,
            remote_backend=backend,
            target_path=restored_file,
        )

        assert pulled_path.exists()
        assert pulled_path.read_bytes() == content
        assert manager.verify_integrity(pointer, pulled_path) is True

    def test_track_missing_file_raises(self, tmp_path: Path) -> None:
        manager = DvcDatasetManager()
        backend = InMemoryObjectStorage()
        missing = tmp_path / "missing.parquet"

        with pytest.raises(FileNotFoundError, match="Target dataset file does not exist"):
            manager.track_dataset(missing, backend)

    def test_pull_detects_hash_corruption(self, tmp_path: Path) -> None:
        manager = DvcDatasetManager()
        backend = InMemoryObjectStorage()

        raw_file = tmp_path / "data.csv"
        raw_file.write_bytes(b"date,symbol,price\n2025-01-01,THYAO,300.0\n")

        pointer = manager.track_dataset(raw_file, backend)
        key = pointer.outs[0].md5

        # Tamper with storage content directly
        backend.upload(key, b"CORRUPTED_MODIFIED_DATA")

        dest = tmp_path / "restored.csv"
        with pytest.raises(ChecksumMismatchError, match="Dataset corruption detected"):
            manager.pull_dataset(pointer, backend, dest)

    def test_verify_integrity_tampered_or_missing(self, tmp_path: Path) -> None:
        manager = DvcDatasetManager()
        backend = InMemoryObjectStorage()

        raw_file = tmp_path / "source.bin"
        raw_file.write_bytes(b"initial bytes")
        pointer = manager.track_dataset(raw_file, backend)

        # Alter local file
        raw_file.write_bytes(b"tampered bytes")
        assert manager.verify_integrity(pointer, raw_file) is False

        # Missing file
        non_existent = tmp_path / "phantom.bin"
        assert manager.verify_integrity(pointer, non_existent) is False


class TestGitRawDataLeakGuard:
    """Test detection and blocking of raw market datasets from Git staging."""

    def test_forbidden_raw_extensions(self) -> None:
        forbidden_samples = [
            "ml/datasets/raw/bist_100.parquet",
            "data/eod_historical.csv",
            "research/market_archive.arrow",
            "backtest/ticks.feather",
            "cache/bist.h5",
            "database/prices.sqlite",
            "datasets/archive.tar.gz",
            "datasets/raw/sample.bin",
        ]
        for path in forbidden_samples:
            assert GitRawDataLeakGuard.is_forbidden(path) is True, f"Failed for {path}"

    def test_allowed_dvc_pointers_and_code(self) -> None:
        allowed_samples = [
            "ml/datasets/raw/bist_100.parquet.dvc",
            "data/raw/eod.csv.dvc",
            ".gitignore",
            ".gitkeep",
            "README.md",
            "docs/DATASET_CARD_KAGGLE.md",
            "src/stock_analysis/pipeline/dvc_storage.py",
            "config/settings.json",
            "dvc.yaml",
        ]
        for path in allowed_samples:
            assert GitRawDataLeakGuard.is_forbidden(path) is False, f"False positive for {path}"

    def test_scan_staged_files_finds_violations(self) -> None:
        staged = [
            "src/stock_analysis/pipeline/dvc_storage.py",
            "ml/datasets/raw/bist_eod.parquet.dvc",
            "ml/datasets/raw/leaked_bist_eod.parquet",
            "docs/phases/FAZ-10-kaggle-veri.md",
            "data/untracked_prices.csv",
        ]
        violations = GitRawDataLeakGuard.scan_staged_files(staged)
        assert len(violations) == 2
        assert "ml/datasets/raw/leaked_bist_eod.parquet" in violations
        assert "data/untracked_prices.csv" in violations

    def test_assert_no_raw_data_raises_on_violations(self) -> None:
        staged = [
            "README.md",
            "ml/datasets/raw/secret_bars.parquet",
        ]
        with pytest.raises(GitRawDataLeakageError, match="Git raw data policy violation"):
            GitRawDataLeakGuard.assert_no_raw_data_in_git(staged)

    def test_assert_no_raw_data_passes_clean_staged_list(self) -> None:
        staged = [
            "src/pipeline/dvc_storage.py",
            "tests/test_dvc_storage.py",
            "ml/datasets/bist.parquet.dvc",
            ".gitignore",
        ]
        # Should not raise
        GitRawDataLeakGuard.assert_no_raw_data_in_git(staged)
