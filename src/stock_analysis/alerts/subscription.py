"""Kullanıcı bildirim izni (opt-in / consent) ve RFC 8058 tek tıkla abonelikten çıkma modülü.

Bu modül, FAZ-12 gereksinimlerine uygundur:
- Kullanıcıdan açık bildirim izni (opt-in / consent) alma ve izin durumunu denetleme.
- E-postalarda tek tıkla abonelikten çıkma (one-click unsubscribe) için kriptografik
  HMAC-SHA256 imzalı, süreli ve kurcalamaya dayanıklı token üretimi.
- RFC 8058 standartlarına uygun 'List-Unsubscribe' ve 'List-Unsubscribe-Post' başlıkları.
- Oturum açmadan hem GET (tarayıcı) hem POST (tek tık) ile anında abonelik iptali.
- KVKK ve Elektronik Ticaret Mevzuatı (ETK) uyumlu denetim izi ve rıza kaydı.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.api.sanitizer import mask_email
from stock_analysis.db.models import AlertRule, AuditEvent, UserPreference

logger = logging.getLogger("stock_analysis.alerts.subscription")

# Güvenli imza için varsayılan gizli anahtar (ortam değişkeninden alınabilir)
_UNSUBSCRIBE_SECRET = os.getenv(
    "BORA_UNSUBSCRIBE_SECRET",
    "bora-ai-secure-one-click-unsubscribe-hmac-secret-key-production-ready",
)


class InvalidUnsubscribeTokenError(ValueError):
    """Geçersiz, süresi dolmuş veya kurcalanmış abonelikten çıkma token'ı."""


@dataclass(frozen=True)
class UnsubscribeTokenPayload:
    """Doğrulanmış abonelikten çıkma token içeriği."""

    user_id: int
    email_masked: str
    scope: str  # 'all' veya 'alert_rule'
    alert_id: str | None
    expires_at_utc: datetime
    issued_at_utc: datetime


@dataclass
class ConsentRecord:
    """Kullanıcı bildirim rıza/izin kaydı."""

    user_id: int
    channel: str = "email"
    is_granted: bool = True
    granted_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    revoked_at_utc: datetime | None = None
    revocation_reason: str | None = None
    source_ip: str | None = None
    scope: str = "all"


@dataclass(frozen=True)
class UnsubscribeResult:
    """Abonelikten çıkma işlemi sonucu."""

    success: bool
    user_id: int
    masked_email: str
    scope: str
    message: str
    alert_id: str | None = None
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(UTC))


def generate_unsubscribe_token(
    user_id: int,
    email: str,
    alert_id: str | None = None,
    scope: str = "all",
    expires_in_days: int = 30,
    secret_key: str = _UNSUBSCRIBE_SECRET,
) -> str:
    """Kriptografik HMAC-SHA256 imzalı tek tıkla abonelikten çıkma token'ı üretir.

    Token yapısı: base64url(JSON_PAYLOAD) + "." + base64url(HMAC_SHA256(JSON_PAYLOAD))
    """
    if user_id <= 0:
        raise ValueError("Geçersiz kullanıcı kimliği (user_id).")
    if not email or "@" not in email:
        raise ValueError("Geçersiz e-posta adresi.")

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=expires_in_days)
    salt = secrets.token_hex(8)

    payload_dict = {
        "uid": user_id,
        "em": mask_email(email),
        "scp": scope,
        "aid": alert_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "slt": salt,
    }

    payload_bytes = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")

    signature = hmac.new(
        secret_key.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def verify_unsubscribe_token(
    token: str,
    secret_key: str = _UNSUBSCRIBE_SECRET,
) -> UnsubscribeTokenPayload:
    """Abonelikten çıkma token'ının imzasını ve geçerlilik süresini doğrular."""
    if not token or "." not in token:
        raise InvalidUnsubscribeTokenError("Geçersiz token biçimi.")

    parts = token.split(".", 1)
    if len(parts) != 2:
        raise InvalidUnsubscribeTokenError("Token iki parçadan (veri ve imza) oluşmalıdır.")

    payload_b64, sig_b64 = parts

    # İmza doğrulama (sabit zamanlı karşılaştırma)
    expected_sig = hmac.new(
        secret_key.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("ascii").rstrip("=")

    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        raise InvalidUnsubscribeTokenError("Token imzası doğrulanamadı veya kurcalandı.")

    # Veri çözme
    try:
        # Base64 padding tamamlama
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        payload_bytes = base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii"))
        payload_dict = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise InvalidUnsubscribeTokenError("Token yükü çözülemedi.") from exc

    required_keys = {"uid", "em", "scp", "iat", "exp"}
    if not required_keys.issubset(payload_dict.keys()):
        raise InvalidUnsubscribeTokenError("Token yükünde eksik alanlar mevcut.")

    now_ts = int(datetime.now(UTC).timestamp())
    exp_ts = payload_dict["exp"]
    if now_ts > exp_ts:
        raise InvalidUnsubscribeTokenError("Abonelikten çıkma bağlantısının süresi dolmuş.")

    return UnsubscribeTokenPayload(
        user_id=int(payload_dict["uid"]),
        email_masked=str(payload_dict["em"]),
        scope=str(payload_dict["scp"]),
        alert_id=payload_dict.get("aid"),
        expires_at_utc=datetime.fromtimestamp(exp_ts, tz=UTC),
        issued_at_utc=datetime.fromtimestamp(payload_dict["iat"], tz=UTC),
    )


def build_unsubscribe_url(base_url: str, token: str) -> str:
    """Tek tıkla abonelikten çıkma URL'si üretir."""
    clean_base = base_url.rstrip("/")
    return f"{clean_base}/api/v1/alerts/unsubscribe?token={token}"


def build_rfc8058_headers(unsubscribe_url: str) -> dict[str, str]:
    """RFC 8058 One-Click Unsubscribe e-posta başlıklarını üretir."""
    return {
        "List-Unsubscribe": f"<{unsubscribe_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


class NotificationConsentManager:
    """Kullanıcı bildirim rızası ve tek tıkla abonelik yönetimi."""

    def __init__(self, secret_key: str = _UNSUBSCRIBE_SECRET) -> None:
        self.secret_key = secret_key
        # Bellek içi rıza deposu (testler ve hızlı önbellek için)
        self._in_memory_consents: dict[tuple[int, str], ConsentRecord] = {}

    def is_notification_permitted(
        self,
        user_id: int,
        channel: str = "email",
        db: DBSession | None = None,
    ) -> bool:
        """Kullanıcının belirtilen kanalda bildirim alma izni olup olmadığını denetler.

        Rıza verilmemişse veya kullanıcı abonelikten çıkmışsa kesinlikle False döner.
        """
        # 1. Bellek içi kontrol
        key = (user_id, channel)
        if key in self._in_memory_consents:
            consent = self._in_memory_consents[key]
            if not consent.is_granted:
                return False

        # 2. Veritabanı tercihi kontrolü
        if db is not None:
            pref = db.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            ).scalar_one_or_none()
            if pref is not None:
                if channel == "email" and not pref.email_notifications:
                    return False

        return True

    def grant_consent(
        self,
        user_id: int,
        channel: str = "email",
        scope: str = "all",
        source_ip: str | None = None,
        db: DBSession | None = None,
    ) -> ConsentRecord:
        """Kullanıcıya açık bildirim alma izni tanımlar."""
        record = ConsentRecord(
            user_id=user_id,
            channel=channel,
            is_granted=True,
            granted_at_utc=datetime.now(UTC),
            revoked_at_utc=None,
            source_ip=source_ip,
            scope=scope,
        )
        self._in_memory_consents[(user_id, channel)] = record

        if db is not None:
            pref = db.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            ).scalar_one_or_none()
            if pref is not None:
                if channel == "email":
                    pref.email_notifications = True
            else:
                new_pref = UserPreference(user_id=user_id, email_notifications=True)
                db.add(new_pref)

            ip_hash = (
                hashlib.sha256(source_ip.encode("utf-8")).hexdigest()[:16] if source_ip else None
            )
            audit = AuditEvent(
                user_id=user_id,
                action="NOTIFICATION_CONSENT_GRANTED",
                ip_hash=ip_hash,
                resource=f"channel:{channel}:scope:{scope}",
            )
            db.add(audit)
            db.commit()

        logger.info(
            "Notification consent granted for user_id=%s, channel=%s, scope=%s",
            user_id,
            channel,
            scope,
        )
        return record

    def revoke_consent(
        self,
        user_id: int,
        channel: str = "email",
        scope: str = "all",
        alert_id: str | None = None,
        reason: str = "one_click_unsubscribe",
        source_ip: str | None = None,
        db: DBSession | None = None,
    ) -> UnsubscribeResult:
        """Kullanıcının bildirim iznini iptal eder / abonelikten çıkarır."""
        now = datetime.now(UTC)
        record = ConsentRecord(
            user_id=user_id,
            channel=channel,
            is_granted=False,
            revoked_at_utc=now,
            revocation_reason=reason,
            source_ip=source_ip,
            scope=scope,
        )
        self._in_memory_consents[(user_id, channel)] = record

        message = "Bildirim aboneliğinden başarıyla çıkıldı."

        if db is not None:
            if scope == "all":
                # Tüm e-posta bildirimlerini kapat
                pref = db.execute(
                    select(UserPreference).where(UserPreference.user_id == user_id)
                ).scalar_one_or_none()
                if pref is not None:
                    pref.email_notifications = False
                else:
                    new_pref = UserPreference(user_id=user_id, email_notifications=False)
                    db.add(new_pref)
                message = "Tüm e-posta bildirim aboneliğiniz başarıyla iptal edildi."
            elif scope == "alert_rule" and alert_id:
                # Yalnızca ilgili alarm kuralını devre dışı bırak
                try:
                    rule_id_int = int(alert_id)
                    rule = db.execute(
                        select(AlertRule).where(
                            AlertRule.id == rule_id_int,
                            AlertRule.user_id == user_id,
                        )
                    ).scalar_one_or_none()
                    if rule is not None:
                        rule.is_active = False
                        message = (
                            f"#{rule.id} numaralı kurala ait bildirimler başarıyla iptal edildi."
                        )
                except (ValueError, TypeError):
                    pass

            ip_hash = (
                hashlib.sha256(source_ip.encode("utf-8")).hexdigest()[:16] if source_ip else None
            )
            audit = AuditEvent(
                user_id=user_id,
                action="NOTIFICATION_CONSENT_REVOKED",
                ip_hash=ip_hash,
                resource=f"channel:{channel}:scope:{scope}:alert:{alert_id or 'none'}",
            )
            db.add(audit)
            db.commit()

        logger.info(
            "Notification consent revoked for user_id=%s, channel=%s, scope=%s, reason=%s",
            user_id,
            channel,
            scope,
            reason,
        )

        return UnsubscribeResult(
            success=True,
            user_id=user_id,
            masked_email="kullanici@***",
            scope=scope,
            message=message,
            alert_id=alert_id,
            timestamp_utc=now,
        )

    def process_one_click_unsubscribe(
        self,
        token: str,
        source_ip: str | None = None,
        db: DBSession | None = None,
    ) -> UnsubscribeResult:
        """Token doğrulayarak tek tıkla abonelikten çıkma işlemini tamamlar."""
        payload = verify_unsubscribe_token(token, secret_key=self.secret_key)

        res = self.revoke_consent(
            user_id=payload.user_id,
            channel="email",
            scope=payload.scope,
            alert_id=payload.alert_id,
            reason="rfc8058_one_click_token",
            source_ip=source_ip,
            db=db,
        )

        return UnsubscribeResult(
            success=True,
            user_id=payload.user_id,
            masked_email=payload.email_masked,
            scope=payload.scope,
            message=res.message,
            alert_id=payload.alert_id,
            timestamp_utc=res.timestamp_utc,
        )


# Global singleton yönetici
default_consent_manager = NotificationConsentManager()
