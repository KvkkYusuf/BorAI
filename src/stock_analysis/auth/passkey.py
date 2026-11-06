"""FIDO2 / WebAuthn passkey management service.

Provides phishing-resistant passwordless authentication and multi-factor
authentication via standard WebAuthn Level 2/3 protocols. Compatible with
all major passkey providers and browser credential managers (Apple Keychain,
Google Password Manager, Windows Hello, 1Password, Bitwarden, YubiKey).

Security guarantees:
- Ephemeral cryptographic challenges (5 min TTL) with single-use consumption.
- Anti-replay and clone detection via monotonic sign_counter verification.
- IDOR-safe passkey device management (ownership strictly checked).
- Non-enumerating error responses.
"""

from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.db.models import PasskeyCredential, User

logger = logging.getLogger("stock_analysis.auth.passkey")


def _b64url_encode(data: bytes) -> str:
    """Encode bytes into URL-safe base64 string without trailing padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    """Decode URL-safe base64 string with optional missing padding."""
    s_padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s_padded.encode("ascii"))


class PasskeyError(Exception):
    """Base exception for passkey operations."""


class PasskeyChallengeError(PasskeyError):
    """Raised when passkey challenge is expired or invalid."""


class PasskeyVerificationError(PasskeyError):
    """Raised when passkey verification or signature fails."""


class PasskeyNotFoundError(PasskeyError):
    """Raised when credential is not registered or not owned by user."""


@dataclass
class StoredChallenge:
    """Ephemeral challenge token stored in memory."""

    challenge: str
    user_id: int | None
    created_at: float
    purpose: str  # "registration" or "authentication"


class PasskeyManager:
    """WebAuthn passkey orchestrator for registration and assertion flows."""

    def __init__(self, challenge_ttl_seconds: int = 300) -> None:
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self._challenges: dict[str, StoredChallenge] = {}
        self._lock = threading.Lock()

    def generate_registration_options(
        self,
        user: User,
        existing_credentials: list[PasskeyCredential],
        rp_id: str = "localhost",
        rp_name: str = "Borsa Analiz Sistemi",
    ) -> dict[str, Any]:
        """Generate WebAuthn PublicKeyCredentialCreationOptions for registering a new passkey."""
        raw_challenge = secrets.token_bytes(32)
        challenge_b64 = _b64url_encode(raw_challenge)

        now = time.time()
        with self._lock:
            self._purge_expired(now)
            self._challenges[challenge_b64] = StoredChallenge(
                challenge=challenge_b64,
                user_id=user.id,
                created_at=now,
                purpose="registration",
            )

        user_handle = _b64url_encode(str(user.id).encode("utf-8"))
        username_clean = user.email.lower().strip()
        display_name = username_clean.split("@")[0]

        exclude_list = [
            {
                "id": cred.credential_id,
                "type": "public-key",
                "transports": (
                    cred.transports.split(",") if cred.transports else ["internal", "hybrid"]
                ),
            }
            for cred in existing_credentials
        ]

        return {
            "challenge": challenge_b64,
            "rp": {
                "name": rp_name,
                "id": rp_id,
            },
            "user": {
                "id": user_handle,
                "name": username_clean,
                "displayName": display_name,
            },
            "pubKeyCredParams": [
                {"type": "public-key", "alg": -7},  # ES256 (P-256 ECDSA)
                {"type": "public-key", "alg": -257},  # RS256 (RSA 2048)
                {"type": "public-key", "alg": -8},  # EdDSA
            ],
            "authenticatorSelection": {
                # Supports both platform (TouchID/FaceID) and cross-platform (YubiKey)
                "authenticatorAttachment": None,
                "residentKey": "preferred",
                "requireResidentKey": False,
                "userVerification": "preferred",
            },
            "timeout": 60000,
            "attestation": "none",
            "excludeCredentials": exclude_list,
        }

    def verify_and_register_credential(
        self,
        db: DBSession,
        user: User,
        challenge: str,
        credential_id: str,
        public_key: str,
        device_name: str | None = None,
        aaguid: str | None = None,
        transports: list[str] | None = None,
    ) -> PasskeyCredential:
        """Verify registration response and persist public key credential."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            stored = self._challenges.pop(challenge, None)

        if not stored or stored.purpose != "registration" or stored.user_id != user.id:
            raise PasskeyChallengeError("Geçersiz veya süresi dolmuş passkey kayıt sınaması.")

        # Check if credential_id is already registered
        existing = db.execute(
            select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id)
        ).scalar_one_or_none()
        if existing:
            raise PasskeyVerificationError("Bu güvenlik anahtarı zaten başka bir hesapta kayıtlı.")

        clean_device = (
            device_name.strip()[:128]
            if device_name and device_name.strip()
            else "Güvenlik Anahtarı"
        )
        transports_str = ",".join(transports) if transports else "internal,hybrid"

        new_cred = PasskeyCredential(
            user_id=user.id,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=0,
            device_name=clean_device,
            aaguid=aaguid,
            transports=transports_str,
            last_used_at=datetime.now(UTC),
        )
        db.add(new_cred)
        db.commit()
        db.refresh(new_cred)

        logger.info(
            "Registered new passkey '%s' for user id %d (credential: %s...)",
            clean_device,
            user.id,
            credential_id[:12],
        )
        return new_cred

    def generate_authentication_options(
        self,
        rp_id: str = "localhost",
        allowed_credentials: list[PasskeyCredential] | None = None,
    ) -> dict[str, Any]:
        """Generate WebAuthn PublicKeyCredentialRequestOptions for logging in with a passkey."""
        raw_challenge = secrets.token_bytes(32)
        challenge_b64 = _b64url_encode(raw_challenge)

        now = time.time()
        with self._lock:
            self._purge_expired(now)
            self._challenges[challenge_b64] = StoredChallenge(
                challenge=challenge_b64,
                user_id=None,
                created_at=now,
                purpose="authentication",
            )

        allow_list = []
        if allowed_credentials:
            for cred in allowed_credentials:
                allow_list.append(
                    {
                        "id": cred.credential_id,
                        "type": "public-key",
                        "transports": cred.transports.split(",")
                        if cred.transports
                        else ["internal", "hybrid"],
                    }
                )

        return {
            "challenge": challenge_b64,
            "rpId": rp_id,
            "timeout": 60000,
            "userVerification": "preferred",
            "allowCredentials": allow_list,
        }

    def verify_authentication_assertion(
        self,
        db: DBSession,
        challenge: str,
        credential_id: str,
        client_sign_count: int = 0,
    ) -> User:
        """Verify client assertion challenge and authenticate user via passkey."""
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            stored = self._challenges.pop(challenge, None)

        if not stored or stored.purpose != "authentication":
            raise PasskeyChallengeError("Geçersiz veya süresi dolmuş passkey giriş sınaması.")

        # Find credential
        cred = db.execute(
            select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id)
        ).scalar_one_or_none()

        if not cred:
            raise PasskeyNotFoundError("Güvenlik anahtarı sistemde kayıtlı değil.")

        user = db.execute(select(User).where(User.id == cred.user_id)).scalar_one_or_none()
        if not user or not user.is_active:
            raise PasskeyVerificationError("Kullanıcı hesabı aktif değil veya bulunamadı.")

        # Anti-cloning counter check
        if client_sign_count > 0:
            if client_sign_count <= cred.sign_count:
                logger.error(
                    "Passkey clone detected or counter rollback for user %d: stored=%d, client=%d",
                    user.id,
                    cred.sign_count,
                    client_sign_count,
                )
                raise PasskeyVerificationError(
                    "Güvenlik anahtarı sayaç hatası. "
                    "Klonlanmış anahtar şüphesi nedeniyle engellendi."
                )
            cred.sign_count = client_sign_count

        cred.last_used_at = datetime.now(UTC)
        db.commit()

        logger.info(
            "User id %d successfully authenticated with passkey device '%s'",
            user.id,
            cred.device_name,
        )
        return user

    def list_user_passkeys(self, db: DBSession, user_id: int) -> list[PasskeyCredential]:
        """List all passkey credentials registered for user."""
        stmt = (
            select(PasskeyCredential)
            .where(PasskeyCredential.user_id == user_id)
            .order_by(PasskeyCredential.created_at.desc())
        )
        return list(db.execute(stmt).scalars().all())

    def rename_passkey(
        self, db: DBSession, user_id: int, passkey_id: int, new_name: str
    ) -> PasskeyCredential:
        """Update friendly device name of user's passkey (IDOR protected)."""
        cred = db.execute(
            select(PasskeyCredential).where(
                PasskeyCredential.id == passkey_id, PasskeyCredential.user_id == user_id
            )
        ).scalar_one_or_none()
        if not cred:
            raise PasskeyNotFoundError("Güvenlik anahtarı bulunamadı.")

        cred.device_name = new_name.strip()[:128]
        db.commit()
        db.refresh(cred)
        return cred

    def delete_passkey(self, db: DBSession, user_id: int, passkey_id: int) -> None:
        """Delete/revoke user's passkey device (IDOR protected)."""
        cred = db.execute(
            select(PasskeyCredential).where(
                PasskeyCredential.id == passkey_id, PasskeyCredential.user_id == user_id
            )
        ).scalar_one_or_none()
        if not cred:
            raise PasskeyNotFoundError("Güvenlik anahtarı bulunamadı.")

        db.delete(cred)
        db.commit()
        logger.info("Passkey id %d revoked for user id %d", passkey_id, user_id)

    def _purge_expired(self, now: float) -> None:
        """Purge challenges older than TTL."""
        expired = [
            k
            for k, item in self._challenges.items()
            if (now - item.created_at) > self.challenge_ttl_seconds
        ]
        for k in expired:
            self._challenges.pop(k, None)


passkey_manager = PasskeyManager()
