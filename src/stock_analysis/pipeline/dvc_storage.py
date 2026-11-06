"""DVC dataset versioning, object storage management, and Git raw data leak guard.

Enforces FAZ-10 data science discipline and legal/compliance policies:
- Prevents committing heavy, licensed, or proprietary raw market data into Git.
- Tracks raw datasets via lightweight DVC pointers (.dvc) referencing content hashes.
- Connects dataset versioning to object storage remotes (S3, MinIO, or local cache).
- Provides pre-commit/CI Git guards ensuring zero raw data leakage.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class GitRawDataLeakageError(ValueError):
    """Raised when raw dataset files are detected in Git tracking or staging."""


class ChecksumMismatchError(ValueError):
    """Raised when downloaded dataset artifact hash does not match DVC pointer record."""


class DvcFileOutRecord(BaseModel):
    """Represents an output file tracked by a DVC pointer."""

    model_config = ConfigDict(frozen=True)

    md5: str = Field(description="Content MD5 hash matching DVC standards")
    size: int = Field(ge=0, description="File size in bytes")
    path: str = Field(description="Relative path of the tracked target file")
    hash: str = Field(default="md5", description="Hash algorithm identifier")


class DvcPointer(BaseModel):
    """Represents a .dvc pointer file structure."""

    model_config = ConfigDict(frozen=True)

    outs: list[DvcFileOutRecord] = Field(
        default_factory=list, description="List of tracked output artifacts"
    )
    remote: str | None = Field(default=None, description="Identifier or URL of the remote storage")
    meta: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata (e.g., dataset_id, version, symbols)"
    )

    def to_yaml(self) -> str:
        """Serialize pointer to standard DVC YAML format."""
        data: dict[str, Any] = {
            "outs": [out.model_dump() for out in self.outs],
        }
        if self.remote is not None:
            data["remote"] = self.remote
        if self.meta:
            data["meta"] = self.meta
        return str(yaml.dump(data, sort_keys=False))

    def save(self, path: Path | str) -> None:
        """Save pointer to .dvc file on disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def from_yaml(cls, content: str) -> Self:
        """Parse DVC pointer from YAML string."""
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict):
            msg = "Invalid DVC YAML format: expected mapping dictionary."
            raise ValueError(msg)
        outs_raw = raw.get("outs", [])
        outs = [DvcFileOutRecord(**item) for item in outs_raw]
        return cls(
            outs=outs,
            remote=raw.get("remote"),
            meta=raw.get("meta", {}),
        )

    @classmethod
    def load(cls, path: Path | str) -> Self:
        """Load DVC pointer from .dvc file on disk."""
        target = Path(path)
        if not target.exists():
            msg = f"DVC pointer file not found: {target}"
            raise FileNotFoundError(msg)
        return cls.from_yaml(target.read_text(encoding="utf-8"))


class ObjectStorageBackend(ABC):
    """Abstract interface for object storage / remote blob storage systems."""

    @abstractmethod
    def upload(self, key: str, data: bytes) -> str:
        """Upload raw bytes to storage and return storage URI or identifier."""

    @abstractmethod
    def download(self, key: str) -> bytes:
        """Download raw bytes from storage identified by key."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if object exists in storage."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete object from storage; return True if deleted, False if not found."""


class InMemoryObjectStorage(ObjectStorageBackend):
    """In-memory object storage for fast tests and ephemeral tracking."""

    def __init__(self) -> None:
        self._storage: dict[str, bytes] = {}

    def upload(self, key: str, data: bytes) -> str:
        self._storage[key] = bytes(data)
        return f"mem://{key}"

    def download(self, key: str) -> bytes:
        if key not in self._storage:
            msg = f"Key not found in in-memory storage: {key}"
            raise KeyError(msg)
        return bytes(self._storage[key])

    def exists(self, key: str) -> bool:
        return key in self._storage

    def delete(self, key: str) -> bool:
        if key in self._storage:
            del self._storage[key]
            return True
        return False

    @property
    def stored_keys(self) -> list[str]:
        return sorted(self._storage.keys())


class LocalObjectStorage(ObjectStorageBackend):
    """Local filesystem object storage simulating S3/MinIO/DVC content-addressable cache."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        # Standard content-addressable subdirectory hashing (first 2 chars / remainder)
        clean_key = key.replace("\\", "/").strip("/")
        if len(clean_key) >= 4 and not clean_key.startswith("."):
            prefix = clean_key[:2]
            rest = clean_key[2:]
            return self.base_dir / prefix / rest
        return self.base_dir / clean_key

    def upload(self, key: str, data: bytes) -> str:
        target = self._resolve_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"file://{target.as_posix()}"

    def download(self, key: str) -> bytes:
        target = self._resolve_path(key)
        if not target.exists():
            msg = f"Key not found in local storage: {key}"
            raise FileNotFoundError(msg)
        return target.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve_path(key).exists()

    def delete(self, key: str) -> bool:
        target = self._resolve_path(key)
        if target.exists():
            target.unlink()
            return True
        return False


class DvcDatasetManager:
    """Manages DVC pointer generation, storage synchronization, and hash integrity."""

    @staticmethod
    def compute_hashes(data: bytes) -> tuple[str, str]:
        """Compute (md5, sha256) hexadecimal checksums for given bytes."""
        md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        sha256 = hashlib.sha256(data).hexdigest()
        return md5, sha256

    def track_dataset(
        self,
        file_path: Path | str,
        remote_backend: ObjectStorageBackend,
        remote_key: str | None = None,
        dvc_file_path: Path | str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> DvcPointer:
        """Track a local raw dataset: compute hash, upload to remote, and write .dvc pointer."""
        path = Path(file_path).resolve()
        if not path.is_file():
            msg = f"Target dataset file does not exist: {path}"
            raise FileNotFoundError(msg)

        content = path.read_bytes()
        md5_hash, sha256_hash = self.compute_hashes(content)
        size = len(content)

        key = remote_key or md5_hash
        remote_uri = remote_backend.upload(key, content)

        out_record = DvcFileOutRecord(
            md5=md5_hash,
            size=size,
            path=path.name,
            hash="md5",
        )

        metadata: dict[str, Any] = {
            "sha256": sha256_hash,
            "original_filename": path.name,
            **(meta or {}),
        }

        pointer = DvcPointer(
            outs=[out_record],
            remote=remote_uri,
            meta=metadata,
        )

        dvc_target = Path(dvc_file_path) if dvc_file_path else path.with_name(f"{path.name}.dvc")
        pointer.save(dvc_target)
        return pointer

    def pull_dataset(
        self,
        pointer: DvcPointer | Path | str,
        remote_backend: ObjectStorageBackend,
        target_path: Path | str,
        remote_key: str | None = None,
    ) -> Path:
        """Download raw dataset from object storage, verify hash, and save locally."""
        if not isinstance(pointer, DvcPointer):
            dvc_ptr = DvcPointer.load(pointer)
        else:
            dvc_ptr = pointer

        if not dvc_ptr.outs:
            msg = "DVC pointer does not specify any output files."
            raise ValueError(msg)

        out = dvc_ptr.outs[0]
        key = remote_key or out.md5

        raw_bytes = remote_backend.download(key)
        computed_md5, _ = self.compute_hashes(raw_bytes)

        if computed_md5 != out.md5:
            msg = (
                f"Dataset corruption detected: computed MD5 ({computed_md5}) does not match "
                f"expected DVC pointer hash ({out.md5})."
            )
            raise ChecksumMismatchError(msg)

        target = Path(target_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_bytes)
        return target

    def verify_integrity(self, pointer: DvcPointer, local_file_path: Path | str) -> bool:
        """Verify that a local raw file matches the recorded DVC hash."""
        target = Path(local_file_path)
        if not target.is_file():
            return False
        if not pointer.outs:
            return False
        content = target.read_bytes()
        md5_hash, _ = self.compute_hashes(content)
        return md5_hash == pointer.outs[0].md5


class GitRawDataLeakGuard:
    """Pre-commit and CI guard that blocks raw data dumps from entering Git history."""

    RAW_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".csv",
            ".tsv",
            ".parquet",
            ".pq",
            ".feather",
            ".arrow",
            ".h5",
            ".hdf5",
            ".zip",
            ".tar",
            ".gz",
            ".7z",
            ".rar",
            ".db",
            ".sqlite",
        }
    )

    RAW_DIR_PATTERNS: tuple[str, ...] = (
        "ml/datasets/raw/",
        "data/raw/",
        "datasets/raw/",
        "raw_data/",
    )

    ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".dvc",
            ".gitignore",
            ".gitkeep",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".py",
        }
    )

    @classmethod
    def is_forbidden(cls, file_path: str | Path) -> bool:
        """Check if a file path represents forbidden raw dataset content for Git."""
        clean_path = str(file_path).replace("\\", "/")
        path_obj = Path(clean_path)

        # Explicitly allow pointer files and code/docs
        if path_obj.suffix.lower() in cls.ALLOWED_EXTENSIONS:
            return False

        # Forbidden extensions
        if path_obj.suffix.lower() in cls.RAW_EXTENSIONS:
            return True

        # Forbidden directories
        clean_lower = clean_path.lower()
        return any(pattern.lower() in clean_lower for pattern in cls.RAW_DIR_PATTERNS)

    @classmethod
    def scan_staged_files(cls, staged_files: Sequence[str | Path]) -> list[str]:
        """Scan a sequence of Git staged file paths and return all violating files."""
        return [str(p) for p in staged_files if cls.is_forbidden(p)]

    @classmethod
    def assert_no_raw_data_in_git(cls, staged_files: Sequence[str | Path]) -> None:
        """Assert that no raw data files are staged for Git commit.

        Raises:
            GitRawDataLeakageError: When one or more raw dataset files are detected.
        """
        violations = cls.scan_staged_files(staged_files)
        if violations:
            msg = (
                "Git raw data policy violation: Raw market/dataset files cannot be committed "
                "to Git. Use DVC pointers (.dvc) or object storage instead.\n"
                f"Violating files ({len(violations)}):\n"
                + "\n".join(f"  - {f}" for f in violations)
            )
            raise GitRawDataLeakageError(msg)
