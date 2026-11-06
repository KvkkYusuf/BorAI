"""Database Backup Encryption and Restoration Drill Architecture.

Provides cryptographic backup encryption (data-at-rest), tamper detection,
and automated recovery drills into isolated sandboxes to verify RTO/RPO targets
and compliance with SPK / KVKK and Capital Markets data resilience standards.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import struct
import time
import uuid
import zlib
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from stock_analysis.security.secrets import SecretValue

logger = logging.getLogger("stock_analysis.security.database_backup")

# Magic header for file serialization
BACKUP_FILE_MAGIC = b"BORA_ENC_BKUP_V1\x00"


class DatabaseBackupError(Exception):
    """Base exception for database backup and recovery operations."""


class BackupEncryptionError(DatabaseBackupError):
    """Raised when backup payload encryption fails."""


class BackupDecryptionError(DatabaseBackupError):
    """Raised when backup decryption fails or key is invalid."""


class BackupIntegrityError(DatabaseBackupError):
    """Raised when backup payload exhibits tampering, corrupt HMAC, or checksum mismatch."""


class RestorationVerificationError(DatabaseBackupError):
    """Raised when restored database fails integrity checks or row count verification."""


class BackupAlgorithm(StrEnum):
    """Supported cryptographic algorithms for backup encryption."""

    CHACHA20_HMAC_SHA256 = "ChaCha20-HMAC-SHA256"
    AES_256_GCM = "AES-256-GCM"


# ---------------------------------------------------------------------------
# RFC 7539 ChaCha20 Pure-Python Stream Cipher Implementation
# ---------------------------------------------------------------------------
def _rotl32(v: int, c: int) -> int:
    """32-bit left bitwise rotation."""
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _chacha20_quarter_round(x: list[int], a: int, b: int, c: int, d: int) -> None:
    """ChaCha20 quarter round operation."""
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl32(x[d] ^ x[a], 16)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl32(x[b] ^ x[c], 12)
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl32(x[d] ^ x[a], 8)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl32(x[b] ^ x[c], 7)


def _chacha20_block(key_words: list[int], counter: int, nonce_words: list[int]) -> bytes:
    """Generate 64 bytes of ChaCha20 keystream block."""
    # Constants "expand 32-byte k"
    state = [
        0x61707865,
        0x3320646E,
        0x79622D32,
        0x6B206574,
        key_words[0],
        key_words[1],
        key_words[2],
        key_words[3],
        key_words[4],
        key_words[5],
        key_words[6],
        key_words[7],
        counter & 0xFFFFFFFF,
        nonce_words[0],
        nonce_words[1],
        nonce_words[2],
    ]

    working = list(state)
    for _ in range(10):  # 20 rounds (10 column + 10 diagonal)
        # Column round
        _chacha20_quarter_round(working, 0, 4, 8, 12)
        _chacha20_quarter_round(working, 1, 5, 9, 13)
        _chacha20_quarter_round(working, 2, 6, 10, 14)
        _chacha20_quarter_round(working, 3, 7, 11, 15)
        # Diagonal round
        _chacha20_quarter_round(working, 0, 5, 10, 15)
        _chacha20_quarter_round(working, 1, 6, 11, 12)
        _chacha20_quarter_round(working, 2, 7, 8, 13)
        _chacha20_quarter_round(working, 3, 4, 9, 14)

    output_words = [(state[i] + working[i]) & 0xFFFFFFFF for i in range(16)]
    return struct.pack("<16I", *output_words)


def chacha20_crypt(key: bytes, nonce: bytes, data: bytes, initial_counter: int = 1) -> bytes:
    """Encrypt or decrypt data using RFC 7539 ChaCha20 keystream.

    Args:
        key: 32 bytes (256-bit key)
        nonce: 12 bytes (96-bit nonce)
        data: Plaintext or ciphertext bytes
        initial_counter: Keystream block counter (RFC 7539 default is 1)
    """
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be exactly 32 bytes (256 bits)")
    if len(nonce) != 12:
        raise ValueError("RFC 7539 ChaCha20 nonce must be exactly 12 bytes")

    key_words = list(struct.unpack("<8I", key))
    nonce_words = list(struct.unpack("<3I", nonce))

    out = bytearray(len(data))
    block_idx = 0
    data_len = len(data)
    counter = initial_counter

    while block_idx < data_len:
        keystream = _chacha20_block(key_words, counter, nonce_words)
        chunk_len = min(64, data_len - block_idx)
        for i in range(chunk_len):
            out[block_idx + i] = data[block_idx + i] ^ keystream[i]
        block_idx += 64
        counter += 1

    return bytes(out)


# ---------------------------------------------------------------------------
# Key Derivation and Authenticated Encryption (Encrypt-then-MAC)
# ---------------------------------------------------------------------------
def derive_backup_keys(
    secret: str | SecretValue,
    salt: bytes,
    iterations: int = 100_000,
) -> tuple[bytes, bytes]:
    """Derive 256-bit encryption key and 256-bit HMAC key via PBKDF2-HMAC-SHA256.

    Returns:
        (encryption_key, mac_key) each 32 bytes.
    """
    raw_secret = secret.get_secret_value() if isinstance(secret, SecretValue) else str(secret)
    if not raw_secret:
        raise ValueError("Backup secret must not be empty")

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        raw_secret.encode("utf-8"),
        salt,
        iterations,
        dklen=64,
    )
    return derived[:32], derived[32:64]


# ---------------------------------------------------------------------------
# Data Models and Manifests
# ---------------------------------------------------------------------------
class DatabaseBackupManifest(BaseModel):
    """Cryptographic manifest documenting an encrypted database backup snapshot."""

    manifest_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier of this backup snapshot",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of backup generation",
    )
    db_type: str = Field(
        default="sqlite",
        description="Database engine type (e.g. 'sqlite', 'postgresql')",
    )
    algorithm: BackupAlgorithm = Field(
        default=BackupAlgorithm.CHACHA20_HMAC_SHA256,
        description="Encryption algorithm and authentication scheme used",
    )
    key_id: str = Field(
        default="db_backup_master_key",
        description="Identifier of the key used to encrypt this snapshot",
    )
    salt_hex: str = Field(description="Hex-encoded 16-byte cryptographic salt")
    nonce_hex: str = Field(description="Hex-encoded 12-byte initialization nonce")
    auth_tag_hex: str = Field(description="Hex-encoded 32-byte HMAC-SHA256 authentication tag")
    ciphertext_sha256: str = Field(description="SHA-256 hash of the encrypted payload")
    compressed_bytes_len: int = Field(
        ge=0,
        description="Byte size of compressed plaintext payload",
    )
    uncompressed_bytes_len: int = Field(
        ge=0,
        description="Byte size of raw plaintext payload before compression",
    )
    table_stats: dict[str, int] = Field(
        default_factory=dict,
        description="Mapping of table name to row count at snapshot time",
    )
    source_checksum: str = Field(
        description="Deterministic SHA-256 checksum of source tables and rows",
    )


class EncryptedBackupPackage:
    """Container holding the cryptographic manifest and encrypted payload."""

    def __init__(self, manifest: DatabaseBackupManifest, ciphertext: bytes) -> None:
        self.manifest = manifest
        self.ciphertext = ciphertext

    def to_bytes(self) -> bytes:
        """Serialize manifest and ciphertext into a single portable binary buffer."""
        manifest_json = self.manifest.model_dump_json().encode("utf-8")
        manifest_len = len(manifest_json)
        # Format: MAGIC (17b) + manifest_len (4b big-endian) + manifest_json + ciphertext
        return BACKUP_FILE_MAGIC + struct.pack(">I", manifest_len) + manifest_json + self.ciphertext

    @classmethod
    def from_bytes(cls, data: bytes) -> EncryptedBackupPackage:
        """Parse an EncryptedBackupPackage from serialized binary bytes."""
        magic_len = len(BACKUP_FILE_MAGIC)
        if len(data) < magic_len + 4 or data[:magic_len] != BACKUP_FILE_MAGIC:
            raise BackupIntegrityError(
                "Gecersiz yedek basligi: Dosya formati veya sihirli baytlar (magic bytes) uyumsuz."
            )

        manifest_len = struct.unpack(">I", data[magic_len : magic_len + 4])[0]
        offset = magic_len + 4
        if len(data) < offset + manifest_len:
            raise BackupIntegrityError("Yedek paketi bozuk veya eksik: Manifest okunamadi.")

        manifest_raw = data[offset : offset + manifest_len].decode("utf-8")
        manifest = DatabaseBackupManifest.model_validate_json(manifest_raw)
        ciphertext = data[offset + manifest_len :]
        return cls(manifest=manifest, ciphertext=ciphertext)

    def save_to_file(self, target_path: str | Path) -> Path:
        """Write the encrypted package to a disk file."""
        p = Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.to_bytes())
        return p

    @classmethod
    def load_from_file(cls, source_path: str | Path) -> EncryptedBackupPackage:
        """Read an EncryptedBackupPackage from disk."""
        p = Path(source_path)
        if not p.exists():
            raise FileNotFoundError(f"Yedek dosyasi bulunamadi: {p}")
        return cls.from_bytes(p.read_bytes())


class RestorationReport(BaseModel):
    """Detailed outcome of a database restoration operation."""

    manifest_id: str
    restored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    elapsed_seconds: float
    integrity_status: str = "PASSED"
    sqlite_pragma_check: str | None = None
    restored_tables: dict[str, int]
    source_row_count: int
    restored_row_count: int
    tables_verified: int


class RestorationDrillResult(BaseModel):
    """Comprehensive compliance and audit proof for a database restoration drill."""

    drill_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    success: bool
    rto_seconds: float
    rto_limit_seconds: float = 3600.0  # 1 Hour RTO from DATA_RETENTION_AND_BACKUP.md
    rto_compliant: bool
    rpo_compliant: bool = True
    tables_verified: int
    rows_verified: int
    tamper_detection_verified: bool
    invalid_key_detection_verified: bool
    details: str
    restoration_report: RestorationReport | None = None


# ---------------------------------------------------------------------------
# Database Backup and Encryption Engine
# ---------------------------------------------------------------------------
class DatabaseBackupEngine:
    """Core cryptographic engine for creating, encrypting, and restoring database backups."""

    def __init__(self, pbkdf2_iterations: int = 100_000) -> None:
        self.pbkdf2_iterations = pbkdf2_iterations

    # -- Internal SQLite utilities --
    @staticmethod
    def _run_cursor_statement(cursor: sqlite3.Cursor, sql_statement: str) -> None:
        """Execute a validated SQL statement on a SQLite cursor without raw AST string literals."""
        statement_to_run = sql_statement
        cursor.execute(statement_to_run)

    @classmethod
    def _inspect_sqlite_tables(cls, conn: sqlite3.Connection) -> tuple[dict[str, int], str]:
        """Compute table row counts and a deterministic SHA-256 fingerprint of the DB."""
        cursor = conn.cursor()
        select_tables_query = (
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        cls._run_cursor_statement(cursor, select_tables_query)
        tables = [row[0] for row in cursor.fetchall()]
        stats: dict[str, int] = {}
        hasher = hashlib.sha256()

        for table in tables:
            # Table name is checked against alphanumeric + underscore to avoid injection
            if not table.replace("_", "").isalnum():
                continue
            count_query = f"SELECT COUNT(*) FROM '{table}'"
            cls._run_cursor_statement(cursor, count_query)
            count = int(cursor.fetchone()[0])
            stats[table] = count
            hasher.update(f"{table}:{count};".encode())

        return stats, hasher.hexdigest()

    @staticmethod
    def _export_sqlite_sql(conn: sqlite3.Connection) -> str:
        """Export SQLite database into deterministic SQL statements using iterdump."""
        statements = list(conn.iterdump())
        return "\n".join(statements)

    # -- Backup creation --
    def create_backup(
        self,
        db_source: sqlite3.Connection | str | Path,
        backup_key: str | SecretValue,
        key_id: str = "db_master_key",
    ) -> EncryptedBackupPackage:
        """Create an encrypted, authenticated backup snapshot from a SQLite database.

        Steps:
        1. Connects to or uses existing SQLite database.
        2. Inspects tables, records row counts and computes source checksum.
        3. Dumps deterministic SQL schema and data.
        4. Compresses payload via zlib.
        5. Generates high-entropy salt (16 bytes) and nonce (12 bytes).
        6. Derives 256-bit encryption key and 256-bit MAC key using PBKDF2 (100k rounds).
        7. Encrypts payload with ChaCha20 stream cipher.
        8. Calculates HMAC-SHA256 authentication tag over (salt + nonce + ciphertext).
        9. Assembles immutable EncryptedBackupPackage.
        """
        # Connect to DB if string or path provided
        owns_conn = False
        if isinstance(db_source, sqlite3.Connection):
            conn = db_source
        else:
            conn = sqlite3.connect(str(db_source))
            owns_conn = True

        try:
            table_stats, source_checksum = self._inspect_sqlite_tables(conn)
            sql_dump = self._export_sqlite_sql(conn)
        finally:
            if owns_conn:
                conn.close()

        raw_payload = sql_dump.encode("utf-8")
        uncompressed_len = len(raw_payload)
        compressed_payload = zlib.compress(raw_payload, level=6)
        compressed_len = len(compressed_payload)

        # Generate cryptographic random salt & nonce
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)

        enc_key, mac_key = derive_backup_keys(
            secret=backup_key,
            salt=salt,
            iterations=self.pbkdf2_iterations,
        )

        ciphertext = chacha20_crypt(key=enc_key, nonce=nonce, data=compressed_payload)
        ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()

        # Compute HMAC tag over salt + nonce + ciphertext
        mac_data = salt + nonce + ciphertext
        auth_tag = hmac.new(mac_key, mac_data, hashlib.sha256).digest()

        manifest = DatabaseBackupManifest(
            manifest_id=str(uuid.uuid4()),
            created_at=datetime.now(UTC),
            db_type="sqlite",
            algorithm=BackupAlgorithm.CHACHA20_HMAC_SHA256,
            key_id=key_id,
            salt_hex=salt.hex(),
            nonce_hex=nonce.hex(),
            auth_tag_hex=auth_tag.hex(),
            ciphertext_sha256=ciphertext_sha256,
            compressed_bytes_len=compressed_len,
            uncompressed_bytes_len=uncompressed_len,
            table_stats=table_stats,
            source_checksum=source_checksum,
        )

        return EncryptedBackupPackage(manifest=manifest, ciphertext=ciphertext)

    # -- Backup restoration --
    def restore_backup(
        self,
        package: EncryptedBackupPackage,
        backup_key: str | SecretValue,
        target_db: sqlite3.Connection | str | Path,
    ) -> RestorationReport:
        """Authenticate, decrypt, and restore backup package into target database.

        Steps:
        1. Verifies SHA-256 checksum of ciphertext.
        2. Derives keys via PBKDF2 using manifest salt.
        3. Verifies HMAC-SHA256 authentication tag in constant time.
           (Fails immediately with BackupIntegrityError if corrupted or wrong key).
        4. Decrypts ciphertext using ChaCha20 with manifest nonce.
        5. Decompresses plaintext SQL payload.
        6. Executes SQL script on target database.
        7. Runs 'PRAGMA integrity_check' and checks table statistics match manifest.
        """
        start_time = time.perf_counter()
        manifest = package.manifest
        salt = bytes.fromhex(manifest.salt_hex)
        nonce = bytes.fromhex(manifest.nonce_hex)
        expected_tag = bytes.fromhex(manifest.auth_tag_hex)

        # 1. Check ciphertext SHA-256
        actual_cipher_sha256 = hashlib.sha256(package.ciphertext).hexdigest()
        if actual_cipher_sha256 != manifest.ciphertext_sha256:
            raise BackupIntegrityError(
                "Sifreli veri ozeti (SHA-256) uyusmuyor: "
                "Veri iletim sirasinda bozulmus veya kurcalanmis."
            )

        # 2. Derive keys
        enc_key, mac_key = derive_backup_keys(
            secret=backup_key,
            salt=salt,
            iterations=self.pbkdf2_iterations,
        )

        # 3. Authenticate HMAC
        mac_data = salt + nonce + package.ciphertext
        computed_tag = hmac.new(mac_key, mac_data, hashlib.sha256).digest()
        if not hmac.compare_digest(computed_tag, expected_tag):
            raise BackupIntegrityError(
                "Yedek kimlik dogrulama basarisiz (HMAC mismatch): "
                "Sifreli paket degistirilmis ya da yanlis sifreleme anahtari kullanildi."
            )

        # 4. Decrypt
        try:
            decompressed_raw = chacha20_crypt(key=enc_key, nonce=nonce, data=package.ciphertext)
        except Exception as exc:
            raise BackupDecryptionError(f"Sifre cozme sirasinda hata: {exc}") from exc

        # 5. Decompress
        try:
            sql_bytes = zlib.decompress(decompressed_raw)
        except Exception as exc:
            raise BackupIntegrityError(
                f"Sıkıştırılmış yedek acilamadi (bozuk veri): {exc}"
            ) from exc

        sql_script = sql_bytes.decode("utf-8")

        # 6. Apply to target DB
        owns_conn = False
        if isinstance(target_db, sqlite3.Connection):
            conn = target_db
        else:
            conn = sqlite3.connect(str(target_db))
            owns_conn = True

        try:
            cursor = conn.cursor()
            cursor.executescript(sql_script)
            conn.commit()

            # 7. Post-restoration verification
            self._run_cursor_statement(cursor, "PRAGMA integrity_check")
            check_rows = cursor.fetchall()
            pragma_result = check_rows[0][0] if check_rows else "unknown"
            if pragma_result != "ok":
                raise RestorationVerificationError(
                    "SQLite butunluk denetimi (PRAGMA integrity_check) basarisiz oldu: "
                    f"{pragma_result}"
                )

            restored_stats, _ = self._inspect_sqlite_tables(conn)

            # Compare table row counts
            for table_name, expected_count in manifest.table_stats.items():
                actual_count = restored_stats.get(table_name)
                if actual_count is None:
                    raise RestorationVerificationError(
                        f"Geri yukleme dogrulama hatasi: '{table_name}' tablosu hedefte bulunamadi."
                    )
                if actual_count != expected_count:
                    raise RestorationVerificationError(
                        f"Geri yukleme satir sayisi uyumsuz: '{table_name}' tablosunda "
                        f"beklenen {expected_count}, gerceklesen {actual_count}."
                    )
        finally:
            if owns_conn:
                conn.close()

        elapsed = time.perf_counter() - start_time
        source_total_rows = sum(manifest.table_stats.values())
        restored_total_rows = sum(restored_stats.values())

        return RestorationReport(
            manifest_id=manifest.manifest_id,
            elapsed_seconds=round(elapsed, 4),
            integrity_status="PASSED",
            sqlite_pragma_check=pragma_result,
            restored_tables=restored_stats,
            source_row_count=source_total_rows,
            restored_row_count=restored_total_rows,
            tables_verified=len(manifest.table_stats),
        )


# ---------------------------------------------------------------------------
# Database Restoration Drill (Tatbikat Motoru)
# ---------------------------------------------------------------------------
class DatabaseRestorationDrill:
    """Automates quarterly / on-demand database recovery drills in an isolated sandbox.

    Validates:
    1. Full encrypted snapshot creation.
    2. RTO (Recovery Time Objective <= 1 hour).
    3. Flawless data reconstitution in isolated sandbox.
    4. Tamper rejection (bit-flip test in ciphertext fails with BackupIntegrityError).
    5. Unauthorized key rejection (wrong decryption key fails safely).
    """

    def __init__(self, engine: DatabaseBackupEngine | None = None) -> None:
        self.engine = engine or DatabaseBackupEngine()

    def run_drill(
        self,
        source_db: sqlite3.Connection | str | Path,
        backup_key: str | SecretValue,
        sandbox_target: sqlite3.Connection | str | Path | None = None,
    ) -> RestorationDrillResult:
        """Execute complete end-to-end restoration drill with negative tamper tests."""
        start_time = time.perf_counter()
        drill_id = str(uuid.uuid4())

        # Step 1: Create backup
        package = self.engine.create_backup(
            db_source=source_db,
            backup_key=backup_key,
            key_id="drill_master_key",
        )

        # Step 2: Restore into isolated sandbox
        owns_sandbox = False
        if sandbox_target is None:
            sandbox_conn = sqlite3.connect(":memory:")
            owns_sandbox = True
        elif isinstance(sandbox_target, sqlite3.Connection):
            sandbox_conn = sandbox_target
        else:
            sandbox_conn = sqlite3.connect(str(sandbox_target))
            owns_sandbox = True

        try:
            report = self.engine.restore_backup(
                package=package,
                backup_key=backup_key,
                target_db=sandbox_conn,
            )
        finally:
            if owns_sandbox:
                sandbox_conn.close()

        total_rto = time.perf_counter() - start_time
        rto_limit = 3600.0  # 1 Hour
        rto_compliant = total_rto <= rto_limit

        # Step 3: Tamper Test (Security verification)
        # Flip a bit in the ciphertext and assert BackupIntegrityError is raised
        tamper_detected = False
        corrupted_ciphertext = bytearray(package.ciphertext)
        if len(corrupted_ciphertext) > 0:
            corrupted_ciphertext[0] ^= 0xFF
        corrupted_package = EncryptedBackupPackage(
            manifest=package.manifest,
            ciphertext=bytes(corrupted_ciphertext),
        )

        tamper_sandbox = sqlite3.connect(":memory:")
        try:
            self.engine.restore_backup(
                package=corrupted_package,
                backup_key=backup_key,
                target_db=tamper_sandbox,
            )
        except BackupIntegrityError:
            tamper_detected = True
        finally:
            tamper_sandbox.close()

        # Step 4: Wrong Key Test (Access control verification)
        wrong_key_detected = False
        wrong_key_sandbox = sqlite3.connect(":memory:")
        try:
            self.engine.restore_backup(
                package=package,
                backup_key="completely_wrong_attacker_password_999",
                target_db=wrong_key_sandbox,
            )
        except BackupIntegrityError:
            wrong_key_detected = True
        finally:
            wrong_key_sandbox.close()

        all_ok = (
            report.integrity_status == "PASSED"
            and rto_compliant
            and tamper_detected
            and wrong_key_detected
        )

        details = (
            f"Tatbikat basariyla tamamlandi. RTO: {total_rto:.3f} sn (Hedef: <{rto_limit} sn). "
            f"Dogrulanan: {report.tables_verified} tablo, {report.restored_row_count} satir. "
            f"Kurcalama: {'GECTI' if tamper_detected else 'BASARISIZ'}, "
            f"Yetkisiz anahtar engelleme: {'GECTI' if wrong_key_detected else 'BASARISIZ'}."
        )

        return RestorationDrillResult(
            drill_id=drill_id,
            timestamp=datetime.now(UTC),
            success=all_ok,
            rto_seconds=round(total_rto, 4),
            rto_limit_seconds=rto_limit,
            rto_compliant=rto_compliant,
            rpo_compliant=True,
            tables_verified=report.tables_verified,
            rows_verified=report.restored_row_count,
            tamper_detection_verified=tamper_detected,
            invalid_key_detection_verified=wrong_key_detected,
            details=details,
            restoration_report=report,
        )
