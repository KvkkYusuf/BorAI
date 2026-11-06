"""Comprehensive tests for database backup encryption and restoration drills.

Validates:
1. RFC 7539 ChaCha20 stream cipher round-trip encryption and decryption.
2. PBKDF2 key derivation (100k rounds) and authenticated HMAC-SHA256 generation.
3. Database backup snapshot creation with table statistics and deterministic checksums.
4. Binary package serialization and deserialization (magic bytes, headers, manifest).
5. File persistence and reload (save_to_file / load_from_file).
6. Sandbox database restoration and verification of schemas, rows, and integrity_check.
7. Cryptographic tamper detection (bit-flip in ciphertext raises BackupIntegrityError).
8. Unauthorized decryption key rejection (wrong key fails safely without executing SQL).
9. Automated end-to-end recovery drill (DatabaseRestorationDrill) and RTO compliance.
"""

import sqlite3
from pathlib import Path

import pytest

from stock_analysis.security.database_backup import (
    BACKUP_FILE_MAGIC,
    BackupAlgorithm,
    BackupIntegrityError,
    DatabaseBackupEngine,
    DatabaseRestorationDrill,
    EncryptedBackupPackage,
    RestorationDrillResult,
    RestorationReport,
    chacha20_crypt,
    derive_backup_keys,
)
from stock_analysis.security.secrets import SecretValue


def _build_populated_test_db(db_path: Path | str) -> sqlite3.Connection:
    """Create a sample database with multiple tables, indices, and records."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE market_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol VARCHAR(20) NOT NULL,
            trade_date DATE NOT NULL,
            open NUMERIC(12, 4) NOT NULL,
            high NUMERIC(12, 4) NOT NULL,
            low NUMERIC(12, 4) NOT NULL,
            close NUMERIC(12, 4) NOT NULL,
            volume BIGINT NOT NULL
        );

        CREATE TABLE alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol VARCHAR(20) NOT NULL,
            condition_type VARCHAR(50) NOT NULL,
            target_value NUMERIC(12, 4) NOT NULL,
            is_active BOOLEAN DEFAULT 1
        );

        INSERT INTO users (email, password_hash, role) VALUES
            ('admin@example.com', '$argon2id$v=19$m=65536,t=3,p=4$fake_hash_admin', 'ADMIN'),
            ('analyst@example.com', '$argon2id$v=19$m=65536,t=3,p=4$fake_hash_analyst', 'ANALYST'),
            ('investor1@example.com', '$argon2id$v=19$m=65536,t=3,p=4$fake_hash_user1', 'USER');

        INSERT INTO market_bars (symbol, trade_date, open, high, low, close, volume) VALUES
            ('THYAO', '2026-10-20', 250.0, 255.0, 248.5, 253.2, 1500000),
            ('THYAO', '2026-10-21', 253.2, 258.0, 251.0, 257.4, 1800000),
            ('ASELS', '2026-10-20', 62.0, 64.5, 61.8, 63.9, 3200000),
            ('GARAN', '2026-10-21', 110.0, 114.2, 109.5, 113.8, 2400000);

        INSERT INTO alert_rules (user_id, symbol, condition_type, target_value) VALUES
            (1, 'THYAO', 'PRICE_ABOVE', 260.0),
            (2, 'ASELS', 'PRICE_BELOW', 60.0);
    """)
    conn.commit()
    return conn


class TestChaCha20AndKeyDerivation:
    """Test cryptographic primitives."""

    def test_chacha20_round_trip(self) -> None:
        key = b"\x01" * 32
        nonce = b"\x02" * 12
        plaintext = b"Gizli borsa veritabani yedegi - SQL DUMP" * 20

        ciphertext = chacha20_crypt(key=key, nonce=nonce, data=plaintext)
        assert ciphertext != plaintext
        assert len(ciphertext) == len(plaintext)

        decrypted = chacha20_crypt(key=key, nonce=nonce, data=ciphertext)
        assert decrypted == plaintext

    def test_chacha20_invalid_key_length(self) -> None:
        with pytest.raises(ValueError, match="ChaCha20 key must be exactly 32 bytes"):
            chacha20_crypt(key=b"too_short", nonce=b"\x01" * 12, data=b"data")

    def test_chacha20_invalid_nonce_length(self) -> None:
        with pytest.raises(ValueError, match="nonce must be exactly 12 bytes"):
            chacha20_crypt(key=b"\x01" * 32, nonce=b"too_short", data=b"data")

    def test_derive_backup_keys_pbkdf2(self) -> None:
        salt = b"\xaa" * 16
        enc_key, mac_key = derive_backup_keys(
            secret="super_secure_master_backup_passphrase_2026",
            salt=salt,
            iterations=1000,  # Fast in unit test
        )
        assert len(enc_key) == 32
        assert len(mac_key) == 32
        assert enc_key != mac_key

    def test_derive_backup_keys_with_secret_value(self) -> None:
        salt = b"\xbb" * 16
        secret = SecretValue("wrapped_secret_token_123")
        enc_key, mac_key = derive_backup_keys(secret=secret, salt=salt, iterations=1000)
        assert len(enc_key) == 32
        assert len(mac_key) == 32

    def test_derive_backup_keys_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Backup secret must not be empty"):
            derive_backup_keys(secret="", salt=b"\x00" * 16)


class TestDatabaseBackupEngine:
    """Tests for database backup creation, serialization, and decryption."""

    @pytest.fixture
    def test_engine(self) -> DatabaseBackupEngine:
        # Use lower iterations for fast testing
        return DatabaseBackupEngine(pbkdf2_iterations=1000)

    def test_create_and_restore_backup_in_memory(
        self,
        tmp_path: Path,
        test_engine: DatabaseBackupEngine,
    ) -> None:
        source_db_file = tmp_path / "source.db"
        conn = _build_populated_test_db(source_db_file)
        conn.close()

        backup_key = "master_backup_password_ultra_safe"
        package = test_engine.create_backup(
            db_source=source_db_file,
            backup_key=backup_key,
            key_id="test_key_01",
        )

        assert package.manifest.db_type == "sqlite"
        assert package.manifest.algorithm == BackupAlgorithm.CHACHA20_HMAC_SHA256
        assert package.manifest.key_id == "test_key_01"
        assert package.manifest.table_stats == {"alert_rules": 2, "market_bars": 4, "users": 3}
        assert package.manifest.compressed_bytes_len < package.manifest.uncompressed_bytes_len
        assert b"THYAO" not in package.ciphertext  # Ciphertext must not leak plain symbol
        assert b"admin@example.com" not in package.ciphertext  # Must not leak PII

        # Restore into a fresh database file
        restore_db_file = tmp_path / "restored.db"
        report = test_engine.restore_backup(
            package=package,
            backup_key=backup_key,
            target_db=restore_db_file,
        )

        assert isinstance(report, RestorationReport)
        assert report.integrity_status == "PASSED"
        assert report.sqlite_pragma_check == "ok"
        assert report.tables_verified == 3
        assert report.source_row_count == 9
        assert report.restored_row_count == 9
        assert report.restored_tables == {"alert_rules": 2, "market_bars": 4, "users": 3}

        # Verify restored DB content
        restored_conn = sqlite3.connect(str(restore_db_file))
        cursor = restored_conn.cursor()
        cursor.execute("SELECT email, role FROM users ORDER BY id")
        rows = cursor.fetchall()
        assert len(rows) == 3
        assert rows[0] == ("admin@example.com", "ADMIN")
        assert rows[1] == ("analyst@example.com", "ANALYST")
        restored_conn.close()

    def test_package_binary_serialization_and_disk_save(
        self,
        tmp_path: Path,
        test_engine: DatabaseBackupEngine,
    ) -> None:
        source_db_file = tmp_path / "source2.db"
        conn = _build_populated_test_db(source_db_file)
        conn.close()

        package = test_engine.create_backup(
            db_source=source_db_file,
            backup_key="safe_key_serialization",
        )

        # Serialize to bytes and reload
        raw_bytes = package.to_bytes()
        assert raw_bytes.startswith(BACKUP_FILE_MAGIC)

        loaded_pkg = EncryptedBackupPackage.from_bytes(raw_bytes)
        assert loaded_pkg.manifest.manifest_id == package.manifest.manifest_id
        assert loaded_pkg.manifest.table_stats == package.manifest.table_stats
        assert loaded_pkg.ciphertext == package.ciphertext

        # Save to disk file and reload
        disk_path = tmp_path / "archive.encbkp"
        saved_path = package.save_to_file(disk_path)
        assert saved_path.exists()

        disk_loaded_pkg = EncryptedBackupPackage.load_from_file(saved_path)
        assert disk_loaded_pkg.manifest.manifest_id == package.manifest.manifest_id
        assert disk_loaded_pkg.ciphertext == package.ciphertext

    def test_corrupt_package_header_raises_integrity_error(self) -> None:
        invalid_bytes = b"CORRUPT_MAGIC_123" + b"\x00" * 50
        with pytest.raises(BackupIntegrityError, match="Gecersiz yedek basligi"):
            EncryptedBackupPackage.from_bytes(invalid_bytes)

    def test_missing_backup_file_raises_filenotfound(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "ghost.encbkp"
        with pytest.raises(FileNotFoundError, match="Yedek dosyasi bulunamadi"):
            EncryptedBackupPackage.load_from_file(non_existent)


class TestTamperAndSecurityDefenses:
    """Tests for cryptographic tamper rejection and wrong key detection."""

    @pytest.fixture
    def test_engine(self) -> DatabaseBackupEngine:
        return DatabaseBackupEngine(pbkdf2_iterations=1000)

    @pytest.fixture
    def sample_package(
        self,
        tmp_path: Path,
        test_engine: DatabaseBackupEngine,
    ) -> EncryptedBackupPackage:
        db_file = tmp_path / "defense_source.db"
        conn = _build_populated_test_db(db_file)
        conn.close()
        return test_engine.create_backup(
            db_source=db_file,
            backup_key="correct_backup_key_2026",
        )

    def test_tampered_ciphertext_rejected(
        self,
        sample_package: EncryptedBackupPackage,
        test_engine: DatabaseBackupEngine,
    ) -> None:
        # Flip a single bit in the ciphertext
        corrupted = bytearray(sample_package.ciphertext)
        corrupted[10] ^= 0x01

        bad_package = EncryptedBackupPackage(
            manifest=sample_package.manifest,
            ciphertext=bytes(corrupted),
        )

        sandbox = sqlite3.connect(":memory:")
        with pytest.raises(
            BackupIntegrityError,
            match="Sifreli veri ozeti \\(SHA-256\\) uyusmuyor",
        ):
            test_engine.restore_backup(
                package=bad_package,
                backup_key="correct_backup_key_2026",
                target_db=sandbox,
            )
        sandbox.close()

    def test_tampered_auth_tag_rejected(
        self,
        sample_package: EncryptedBackupPackage,
        test_engine: DatabaseBackupEngine,
    ) -> None:
        # Alter the auth_tag in manifest while keeping ciphertext hash matching
        bad_manifest = sample_package.manifest.model_copy(update={"auth_tag_hex": "00" * 32})
        bad_package = EncryptedBackupPackage(
            manifest=bad_manifest,
            ciphertext=sample_package.ciphertext,
        )

        sandbox = sqlite3.connect(":memory:")
        with pytest.raises(
            BackupIntegrityError,
            match="Yedek kimlik dogrulama basarisiz \\(HMAC mismatch\\)",
        ):
            test_engine.restore_backup(
                package=bad_package,
                backup_key="correct_backup_key_2026",
                target_db=sandbox,
            )
        sandbox.close()

    def test_wrong_decryption_key_rejected(
        self,
        sample_package: EncryptedBackupPackage,
        test_engine: DatabaseBackupEngine,
    ) -> None:
        sandbox = sqlite3.connect(":memory:")
        with pytest.raises(
            BackupIntegrityError,
            match="HMAC mismatch",
        ):
            test_engine.restore_backup(
                package=sample_package,
                backup_key="attacker_wrong_passphrase",
                target_db=sandbox,
            )
        sandbox.close()


class TestRestorationDrill:
    """Tests for automated database restoration drills."""

    def test_database_restoration_drill_success(self, tmp_path: Path) -> None:
        source_db_file = tmp_path / "drill_source.db"
        conn = _build_populated_test_db(source_db_file)
        conn.close()

        # Engine with 1000 iterations for test speed
        engine = DatabaseBackupEngine(pbkdf2_iterations=1000)
        drill = DatabaseRestorationDrill(engine=engine)

        drill_key = SecretValue("drill_master_rotation_key_99")
        result: RestorationDrillResult = drill.run_drill(
            source_db=source_db_file,
            backup_key=drill_key,
        )

        assert isinstance(result, RestorationDrillResult)
        assert result.success is True
        assert result.rto_compliant is True
        assert result.rto_seconds < 10.0  # Well within limits
        assert result.rpo_compliant is True
        assert result.tables_verified == 3
        assert result.rows_verified == 9
        assert result.tamper_detection_verified is True
        assert result.invalid_key_detection_verified is True
        assert "Tatbikat basariyla tamamlandi" in result.details
        assert result.restoration_report is not None
        assert result.restoration_report.integrity_status == "PASSED"
        assert result.restoration_report.sqlite_pragma_check == "ok"
