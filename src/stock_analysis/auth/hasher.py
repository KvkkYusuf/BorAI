"""Secure password hashing and validation conforming to modern cryptographic standards."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass


class PasswordPolicyError(ValueError):
    """Raised when a password fails policy requirements."""


@dataclass(frozen=True)
class PasswordPolicy:
    """Password strength policy enforcement."""

    min_length: int = 10
    max_length: int = 128
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digit: bool = True
    require_special: bool = True

    def validate(self, password: str) -> None:
        """Validate password against complexity rules."""
        if len(password) < self.min_length:
            msg = f"Parola en az {self.min_length} karakter olmalıdır."
            raise PasswordPolicyError(msg)
        if len(password) > self.max_length:
            msg = f"Parola en fazla {self.max_length} karakter olmalıdır."
            raise PasswordPolicyError(msg)
        if self.require_uppercase and not re.search(r"[A-Z]", password):
            msg = "Parola en az bir büyük harf içermelidir."
            raise PasswordPolicyError(msg)
        if self.require_lowercase and not re.search(r"[a-z]", password):
            msg = "Parola en az bir küçük harf içermelidir."
            raise PasswordPolicyError(msg)
        if self.require_digit and not re.search(r"\d", password):
            msg = "Parola en az bir rakam içermelidir."
            raise PasswordPolicyError(msg)
        if self.require_special and not re.search(r"[!@#$%^&*(),.?\":{}|<>\-_=+;]", password):
            msg = "Parola en az bir özel karakter içermelidir."
            raise PasswordPolicyError(msg)


class PasswordHasher:
    """Modern memory-hard password hasher using hashlib.scrypt with salt and constant-time check."""

    def __init__(
        self,
        *,
        n: int = 16384,
        r: int = 8,
        p: int = 1,
        maxmem: int = 33554432,  # 32 MB
        policy: PasswordPolicy | None = None,
    ) -> None:
        self.n = n
        self.r = r
        self.p = p
        self.maxmem = maxmem
        self.policy = policy or PasswordPolicy()

    def hash(self, password: str) -> str:
        """Validate policy and generate a secure scrypt password hash string."""
        self.policy.validate(password)
        salt = secrets.token_bytes(16)
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self.n,
            r=self.r,
            p=self.p,
            maxmem=self.maxmem,
            dklen=64,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii")
        key_b64 = base64.b64encode(key).decode("ascii")
        return f"$scrypt$n={self.n},r={self.r},p={self.p}${salt_b64}${key_b64}"

    def verify(self, password: str, hashed_password: str) -> bool:
        """Verify password against scrypt hash using timing-attack-safe comparison."""
        if not hashed_password.startswith("$scrypt$"):
            return False

        try:
            parts = hashed_password.split("$")
            # Expected format: ['', 'scrypt', 'n=...,r=...,p=...', 'salt_b64', 'key_b64']
            if len(parts) != 5:
                return False

            param_str = parts[2]
            salt_b64 = parts[3]
            expected_key_b64 = parts[4]

            params = dict(item.split("=") for item in param_str.split(","))
            n = int(params["n"])
            r = int(params["r"])
            p = int(params["p"])

            salt = base64.b64decode(salt_b64)
            expected_key = base64.b64decode(expected_key_b64)

            computed_key = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=n,
                r=r,
                p=p,
                maxmem=self.maxmem,
                dklen=len(expected_key),
            )
            return hmac.compare_digest(computed_key, expected_key)
        except Exception:
            return False

    def needs_rehash(self, hashed_password: str) -> bool:
        """Check if hash parameters are outdated compared to current hasher configuration."""
        if not hashed_password.startswith("$scrypt$"):
            return True
        try:
            parts = hashed_password.split("$")
            if len(parts) != 5:
                return True
            param_str = parts[2]
            params = dict(item.split("=") for item in param_str.split(","))
            return (
                int(params["n"]) != self.n
                or int(params["r"]) != self.r
                or int(params["p"]) != self.p
            )
        except Exception:
            return True
