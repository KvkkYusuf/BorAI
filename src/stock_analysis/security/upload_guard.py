"""File upload security controls and verification.

Protects against path traversal, arbitrary code execution, dangerous extensions,
MIME-spoofing, Zip Slip vulnerability, and resource exhaustion / file bombs.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import ClassVar

from stock_analysis.security.owasp import OwaspSecurityError


class FileUploadSecurityError(OwaspSecurityError):
    """Base exception for file upload security violations."""


class PathTraversalDetectedError(FileUploadSecurityError, ValueError):
    """Raised when uploaded file path attempts directory traversal or illegal characters."""


class DangerousFileExtensionError(FileUploadSecurityError, ValueError):
    """Raised when file extension is forbidden, executable, or not in allowed list."""


class FileSizeExceededError(FileUploadSecurityError, ValueError):
    """Raised when file size exceeds maximum permitted threshold."""


class ExecutablePayloadDetectedError(FileUploadSecurityError, ValueError):
    """Raised when file content contains executable headers, scripts, or magic bytes."""


class MimeTypeSpoofingError(FileUploadSecurityError, ValueError):
    """Raised when declared MIME type does not match file extension or magic bytes."""


@dataclass(frozen=True)
class UploadValidationResult:
    """Outcome of file upload security validation."""

    original_filename: str
    sanitized_filename: str
    safe_storage_name: str
    extension: str
    content_size_bytes: int
    detected_mime_type: str
    sha256_hash: str


class FileUploadGuard:
    """Comprehensive file upload validator and sanitizer."""

    DEFAULT_MAX_FILE_SIZE_BYTES: ClassVar[int] = 10 * 1024 * 1024  # 10 MB

    # Explicit whitelist of permitted business extensions (datasets, watchlists, config)
    DEFAULT_ALLOWED_EXTENSIONS: ClassVar[set[str]] = {
        ".csv",
        ".json",
        ".parquet",
        ".arrow",
        ".txt",
    }

    # Strict blacklist of executable and dangerous extensions
    FORBIDDEN_EXTENSIONS: ClassVar[set[str]] = {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".elf",
        ".sh",
        ".bash",
        ".zsh",
        ".bat",
        ".cmd",
        ".ps1",
        ".vbs",
        ".py",
        ".pyw",
        ".pyc",
        ".php",
        ".phtml",
        ".php5",
        ".asp",
        ".aspx",
        ".jsp",
        ".jspx",
        ".cgi",
        ".pl",
        ".js",
        ".mjs",
        ".ts",
        ".html",
        ".htm",
        ".xhtml",
        ".svg",
        ".xml",
        ".jar",
        ".war",
        ".msi",
        ".scr",
        ".com",
        ".hta",
    }

    # Windows reserved device names
    WINDOWS_RESERVED_NAMES: ClassVar[set[str]] = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }

    # Executable binary header magic bytes signatures
    _EXECUTABLE_SIGNATURES: ClassVar[list[bytes]] = [
        b"MZ",  # DOS / Windows PE executable (.exe, .dll)
        b"\x7fELF",  # Linux ELF binary
        b"\xca\xfe\xba\xbe",  # Java class bytecode
        b"\xfe\xed\xfa\xce",  # Mach-O binary (32-bit)
        b"\xfe\xed\xfa\xcf",  # Mach-O binary (64-bit)
        b"\xce\xfa\xed\xfe",  # Mach-O reverse byte order
        b"\xcf\xfa\xed\xfe",  # Mach-O reverse byte order
        b"#!",  # Unix shebang executable script
        b"<?php",  # PHP script tag
        b"<?PHP",  # PHP script tag case-variant
    ]

    # Script tags pattern in ostensibly text or data files
    _SCRIPT_CONTENT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"<\s*(script|iframe|object|embed|svg|applet)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        allowed_extensions: set[str] | None = None,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    ) -> None:
        self.allowed_extensions = (
            allowed_extensions
            if allowed_extensions is not None
            else set(self.DEFAULT_ALLOWED_EXTENSIONS)
        )
        self.max_file_size_bytes = max_file_size_bytes

    def sanitize_and_validate_filename(self, filename: str) -> str:
        """Validate filename against path traversal, null bytes, and dangerous extensions.

        Returns:
            Sanitized basename string.

        Raises:
            PathTraversalDetectedError: If path contains directory traversal or control chars.
            DangerousFileExtensionError: If extension is dangerous or not allowed.
        """
        if not filename or not filename.strip():
            raise PathTraversalDetectedError("Dosya adi bos olamaz.")

        # 1. Null-byte injection check
        if "\x00" in filename:
            raise PathTraversalDetectedError("Dosya adinda null-byte (\x00) tespit edildi.")

        # 2. Path separators check (must be basename only)
        if "/" in filename or "\\" in filename:
            raise PathTraversalDetectedError(f"Dosya adi yol ayirici iceremez: '{filename}'")

        # 3. Directory traversal sequence check
        if ".." in filename:
            raise PathTraversalDetectedError(
                f"Dosya adinda dizin gecisi ('..') tespit edildi: '{filename}'"
            )

        # 4. Strip leading/trailing whitespaces and dots
        clean_name = filename.strip()
        if clean_name.startswith("."):
            # Hidden file or extension-only name
            raise PathTraversalDetectedError(
                f"Gizli veya noktayla baslayan dosya adlari kabul edilmez: '{filename}'"
            )

        # 5. Check Windows reserved device names
        stem = clean_name.split(".")[0].upper()
        if stem in self.WINDOWS_RESERVED_NAMES:
            raise PathTraversalDetectedError(
                f"Windows tarafindan ayrilmis cihaz adi tespit edildi: '{stem}'"
            )

        # 6. Extract extension (case-insensitive check)
        _, ext = os.path.splitext(clean_name)
        ext_lower = ext.lower()

        if not ext_lower:
            raise DangerousFileExtensionError("Dosya uzantisi bulunamadi.")

        if ext_lower in self.FORBIDDEN_EXTENSIONS:
            raise DangerousFileExtensionError(
                f"Yasaklanmis ve potansiyel olarak zararli dosya uzantisi: '{ext_lower}'"
            )

        if ext_lower not in self.allowed_extensions:
            raise DangerousFileExtensionError(
                f"Izin verilmeyen dosya uzantisi: '{ext_lower}'. "
                f"Izin verilenler: {sorted(self.allowed_extensions)}"
            )

        # 7. Check for double extension bypass tricks (e.g. evil.php.csv or evil.exe.txt)
        parts = clean_name.split(".")
        if len(parts) > 2:
            for intermediate_ext in parts[1:-1]:
                if f".{intermediate_ext.lower()}" in self.FORBIDDEN_EXTENSIONS:
                    raise DangerousFileExtensionError(
                        f"Iki asamali zararli uzanti tespit edildi: '{intermediate_ext}'"
                    )

        # Return sanitized basename with safe characters
        sanitized = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", clean_name)
        return sanitized

    def generate_safe_storage_name(self, original_filename: str, prefix: str = "upload_") -> str:
        """Generate an unguessable UUID-based storage key preserving safe extension."""
        sanitized = self.sanitize_and_validate_filename(original_filename)
        _, ext = os.path.splitext(sanitized)
        unique_token = uuid.uuid4().hex
        return f"{prefix}{unique_token}{ext.lower()}"

    def inspect_file_content(
        self,
        content: bytes,
        extension: str,
        content_type: str | None = None,
    ) -> str:
        """Verify magic bytes and sniff for executable or malicious script payloads.

        Returns:
            Resolved safe MIME type string.

        Raises:
            ExecutablePayloadDetectedError: If content starts with executable signature.
            MimeTypeSpoofingError: If content contradicts expected format.
        """
        ext = extension.lower()

        # 1. Executable binary signatures scan
        for sig in self._EXECUTABLE_SIGNATURES:
            if content.startswith(sig):
                raise ExecutablePayloadDetectedError(
                    f"Dosya basliginda calistirilabilir ikili (executable/script) imza: {sig!r}"
                )

        # 2. Specific format verifications
        if ext == ".parquet":
            if len(content) < 8 or not (content.startswith(b"PAR1") and content.endswith(b"PAR1")):
                raise MimeTypeSpoofingError(
                    "Parquet formati gecersiz: PAR1 sihirli bayt baslik/altlik bulunamadi."
                )
            return "application/vnd.apache.parquet"

        if ext == ".arrow":
            if not content.startswith(b"ARROW1"):
                raise MimeTypeSpoofingError(
                    "Arrow formati gecersiz: ARROW1 sihirli bayt basligi bulunamadi."
                )
            return "application/vnd.apache.arrow.file"

        if ext in {".csv", ".json", ".txt"}:
            # Must be valid UTF-8/ASCII text without raw null bytes
            if b"\x00" in content:
                raise ExecutablePayloadDetectedError(
                    "Metin/veri dosyasinda beklenmeyen ikili null (\x00) baytlari tespit edildi."
                )

            # Check for embedded script tags (XSS / HTML injection in uploaded data)
            try:
                text_sample = content[:4096].decode("utf-8", errors="ignore")
                if self._SCRIPT_CONTENT_PATTERN.search(text_sample):
                    raise ExecutablePayloadDetectedError(
                        "Yuklenen veri dosyasi iceriginde zararli HTML/XSS etiketi tespit edildi."
                    )
            except Exception as err:
                raise ExecutablePayloadDetectedError(f"Icerik cozumleme hatasi: {err}") from err

            if ext == ".csv":
                return "text/csv"
            if ext == ".json":
                return "application/json"
            return "text/plain"

        return content_type or "application/octet-stream"

    def validate_upload(
        self,
        content: bytes,
        filename: str,
        content_type: str | None = None,
    ) -> UploadValidationResult:
        """Perform end-to-end security validation on uploaded file.

        Checks:
        1. Size limits
        2. Filename traversal & extension rules
        3. Magic bytes & executable signatures
        4. Safe storage identifier generation
        """
        # 1. Size limit check
        size = len(content)
        if size > self.max_file_size_bytes:
            raise FileSizeExceededError(
                f"Dosya boyutu ({size} bayt) azami izin verilen siniri "
                f"({self.max_file_size_bytes} bayt) asiyor."
            )
        if size == 0:
            raise FileUploadSecurityError("Yuklenen dosya bos olamaz (0 bayt).")

        # 2. Filename validation
        sanitized_filename = self.sanitize_and_validate_filename(filename)
        _, ext = os.path.splitext(sanitized_filename)

        # 3. Content inspection
        detected_mime = self.inspect_file_content(content, ext, content_type)

        # 4. Storage name generation
        safe_storage = self.generate_safe_storage_name(sanitized_filename)

        # 5. Compute SHA-256 hash
        import hashlib

        sha256 = hashlib.sha256(content).hexdigest()

        return UploadValidationResult(
            original_filename=filename,
            sanitized_filename=sanitized_filename,
            safe_storage_name=safe_storage,
            extension=ext.lower(),
            content_size_bytes=size,
            detected_mime_type=detected_mime,
            sha256_hash=sha256,
        )

    @classmethod
    def scan_archive_for_zip_slip(cls, archive_member_names: list[str]) -> None:
        """Scan archive members against Zip Slip path traversal vulnerability.

        Raises:
            PathTraversalDetectedError: If any archive entry attempts relative traversal
                or targets an absolute directory outside extraction destination.
        """
        for entry in archive_member_names:
            if not entry or not entry.strip():
                continue
            # Null-byte check
            if "\x00" in entry:
                raise PathTraversalDetectedError(
                    f"Arsiv ogesinde null-byte tespit edildi: '{entry}'"
                )
            # Relative traversal check
            if ".." in entry:
                raise PathTraversalDetectedError(
                    f"Zip Slip zafiyeti: Arsiv ogesi ust dizine erisim ('..') iceriyor: '{entry}'"
                )
            # Absolute root path check
            if entry.startswith("/") or entry.startswith("\\"):
                raise PathTraversalDetectedError(
                    f"Zip Slip zafiyeti: Arsiv ogesi mutlak kok dizin ('/') baslatiyor: '{entry}'"
                )
            # Windows drive letter check (e.g. C:)
            if len(entry) >= 2 and entry[1] == ":" and entry[0].isalpha():
                raise PathTraversalDetectedError(
                    f"Zip Slip zafiyeti: Arsiv ogesi surucu harfi iceriyor: '{entry}'"
                )
