"""RFC 6238 Time-Based One-Time Password (TOTP) and recovery code implementation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass
from urllib.parse import quote


class MFAError(ValueError):
    """Raised when MFA operation or verification fails."""


@dataclass(frozen=True)
class MFASetupResult:
    """Setup credentials generated for enabling MFA on an account."""

    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class TOTPManager:
    """Manages RFC 6238 TOTP generation, validation, and single-use recovery codes."""

    TIME_STEP_SECONDS: int = 30
    DIGITS: int = 6
    ISSUER: str = "BoraI"
    RECOVERY_CODE_COUNT: int = 8

    @staticmethod
    def generate_secret() -> str:
        """Generate a cryptographically secure 160-bit Base32 encoded secret key."""
        raw_bytes = secrets.token_bytes(20)
        return base64.b32encode(raw_bytes).decode("ascii").replace("=", "")

    @staticmethod
    def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
        """Generate random single-use recovery codes formatted as 'xxxx-xxxx'."""
        codes = []
        for _ in range(count):
            hex_part = secrets.token_hex(4).upper()
            codes.append(f"{hex_part[:4]}-{hex_part[4:]}")
        return codes

    @staticmethod
    def hash_recovery_code(code: str) -> str:
        """Hash a recovery code for secure persistent storage."""
        clean = code.strip().upper().replace(" ", "").replace("-", "")
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    def generate_provisioning_uri(self, secret: str, email: str) -> str:
        """Generate standard otpauth URI compatible with Google Authenticator, Authy, etc."""
        encoded_issuer = quote(self.ISSUER)
        encoded_email = quote(email, safe="@")
        label = f"{encoded_issuer}:{encoded_email}"
        return (
            f"otpauth://totp/{label}"
            f"?secret={secret}&issuer={encoded_issuer}"
            f"&algorithm=SHA1&digits={self.DIGITS}&period={self.TIME_STEP_SECONDS}"
        )

    def initiate_setup(self, email: str) -> MFASetupResult:
        """Generate a new secret, provisioning URI, and recovery codes for initial setup."""
        secret = self.generate_secret()
        uri = self.generate_provisioning_uri(secret, email)
        recovery_codes = self.generate_recovery_codes()
        return MFASetupResult(
            secret=secret,
            provisioning_uri=uri,
            recovery_codes=recovery_codes,
        )

    def generate_code(self, secret: str, for_time: float | None = None) -> str:
        """Compute the 6-digit TOTP code for the given secret at specified time."""
        current_time = time.time() if for_time is None else for_time
        time_step = int(current_time // self.TIME_STEP_SECONDS)

        # Pad base32 secret if required
        padding = (8 - len(secret) % 8) % 8
        padded_secret = secret + ("=" * padding)
        try:
            key = base64.b32decode(padded_secret, casefold=True)
        except Exception as err:
            msg = "Geçersiz Base32 MFA gizli anahtarı."
            raise MFAError(msg) from err

        counter_bytes = struct.pack(">Q", time_step)
        hmac_digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()

        # Dynamic truncation (RFC 4226 Section 5.4)
        offset = hmac_digest[-1] & 0x0F
        binary_val = struct.unpack(">I", hmac_digest[offset : offset + 4])[0] & 0x7FFFFFFF
        code_int = binary_val % (10**self.DIGITS)
        return str(code_int).zfill(self.DIGITS)

    def verify_code(
        self,
        secret: str,
        code: str,
        window: int = 1,
        for_time: float | None = None,
    ) -> bool:
        """Verify given 6-digit code against secret with +/- window time step drift allowance."""
        clean_code = code.strip()
        if len(clean_code) != self.DIGITS or not clean_code.isdigit():
            return False

        current_time = time.time() if for_time is None else for_time
        base_step = int(current_time // self.TIME_STEP_SECONDS)

        for offset in range(-window, window + 1):
            step_time = (base_step + offset) * self.TIME_STEP_SECONDS
            expected_code = self.generate_code(secret, for_time=step_time)
            if hmac.compare_digest(clean_code, expected_code):
                return True

        return False

    def verify_recovery_code(
        self,
        raw_code: str,
        stored_hashed_codes: list[str],
    ) -> tuple[bool, list[str]]:
        """Verify a single-use recovery code.

        Returns:
            Tuple of (is_valid, remaining_hashed_codes).
            If valid, the consumed code's hash is removed from remaining list.
        """
        code_hash = self.hash_recovery_code(raw_code)
        for i, stored_hash in enumerate(stored_hashed_codes):
            if hmac.compare_digest(code_hash, stored_hash):
                remaining = [h for j, h in enumerate(stored_hashed_codes) if j != i]
                return True, remaining
        return False, stored_hashed_codes


# Module-level default instance
totp_manager = TOTPManager()
