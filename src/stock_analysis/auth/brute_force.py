"""Brute force and credential stuffing defense with dual-key (account & IP) tracking."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status

logger = logging.getLogger("stock_analysis.auth.brute_force")


@dataclass(frozen=True)
class LockoutStatus:
    """Status indicating whether an identifier is locked out and remaining duration."""

    is_locked: bool
    retry_after: int = 0
    reason: str = ""


class BruteForceDefender:
    """Thread-safe sliding-window defence protecting against brute force and credential stuffing.

    Uses a dual-key tracking strategy:
    1. Account Key (email): Defends against targeted brute force password guessing on a single
       account, regardless of proxy/IP rotation.
    2. IP Key (client_ip): Defends against horizontal credential stuffing sprays across multiple
       different user accounts originating from a single IP or CIDR.
    """

    MAX_ACCOUNT_FAILURES: int = 5
    MAX_IP_FAILURES: int = 15
    WINDOW_SECONDS: int = 900  # 15 minutes
    LOCKOUT_DURATION_SECONDS: int = 900  # 15 minutes

    def __init__(
        self,
        *,
        max_account_failures: int = MAX_ACCOUNT_FAILURES,
        max_ip_failures: int = MAX_IP_FAILURES,
        window_seconds: int = WINDOW_SECONDS,
        lockout_duration_seconds: int = LOCKOUT_DURATION_SECONDS,
    ) -> None:
        self.max_account_failures = max_account_failures
        self.max_ip_failures = max_ip_failures
        self.window_seconds = window_seconds
        self.lockout_duration_seconds = lockout_duration_seconds
        self._lock = threading.Lock()
        # Key -> list of failure timestamps
        self._failures: dict[str, list[float]] = defaultdict(list)
        # Key -> lockout until timestamp
        self._lockouts: dict[str, float] = {}

    @staticmethod
    def mask_email(email: str) -> str:
        """Mask email for safe audit logging."""
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 1:
            masked = f"{local}***"
        else:
            masked = f"{local[0]}***"
        return f"{masked}@{domain}"

    def reset(self) -> None:
        """Clear all tracking state (used in test teardown)."""
        with self._lock:
            self._failures.clear()
            self._lockouts.clear()

    def check_lockout(self, client_ip: str, email: str) -> LockoutStatus:
        """Check whether the account or client IP is currently in locked state."""
        now = time.time()
        account_key = f"acc:{email.strip().lower()}"
        ip_key = f"ip:{client_ip.strip()}"

        with self._lock:
            # 1. Check account-level lockout
            acc_locked_until = self._lockouts.get(account_key, 0.0)
            if acc_locked_until > now:
                retry_after = max(1, int(acc_locked_until - now))
                return LockoutStatus(
                    is_locked=True,
                    retry_after=retry_after,
                    reason="ACCOUNT_LOCKED",
                )

            # 2. Check IP-level credential stuffing spray lockout
            ip_locked_until = self._lockouts.get(ip_key, 0.0)
            if ip_locked_until > now:
                retry_after = max(1, int(ip_locked_until - now))
                return LockoutStatus(
                    is_locked=True,
                    retry_after=retry_after,
                    reason="IP_LOCKED",
                )

        return LockoutStatus(is_locked=False)

    def enforce_limits(self, client_ip: str, email: str) -> None:
        """Raise HTTP 429 Too Many Requests if the account or IP is locked."""
        status_info = self.check_lockout(client_ip, email)
        if status_info.is_locked:
            now_iso = datetime.now(UTC).isoformat()
            if status_info.reason == "ACCOUNT_LOCKED":
                msg = (
                    f"Çok fazla hatalı giriş denemesi yapıldı. Güvenliğiniz için "
                    f"lütfen {status_info.retry_after} saniye sonra tekrar deneyin."
                )
                code = "ACCOUNT_TEMPORARILY_LOCKED"
            else:
                msg = (
                    f"Bu IP adresinden çok sayıda başarısız giriş denemesi tespit edildi. "
                    f"Lütfen {status_info.retry_after} saniye sonra tekrar deneyin."
                )
                code = "IP_SPRAY_THROTTLED"

            logger.warning(
                "Login attempt blocked by defender: reason=%s, email=%s, ip=%s",
                status_info.reason,
                self.mask_email(email),
                client_ip,
            )

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": code,
                    "message": msg,
                    "retry_after": status_info.retry_after,
                    "timestamp": now_iso,
                },
                headers={"Retry-After": str(status_info.retry_after)},
            )

    def record_failure(self, client_ip: str, email: str) -> None:
        """Record an authentication failure for both account and IP keys."""
        now = time.time()
        window_start = now - self.window_seconds
        account_key = f"acc:{email.strip().lower()}"
        ip_key = f"ip:{client_ip.strip()}"

        with self._lock:
            # 1. Update account failures
            acc_times = [t for t in self._failures[account_key] if t > window_start]
            acc_times.append(now)
            self._failures[account_key] = acc_times

            if len(acc_times) >= self.max_account_failures:
                self._lockouts[account_key] = now + self.lockout_duration_seconds
                logger.warning(
                    "Account locked due to brute force: %s (%d failures)",
                    self.mask_email(email),
                    len(acc_times),
                )

            # 2. Update IP failures (credential stuffing detection)
            ip_times = [t for t in self._failures[ip_key] if t > window_start]
            ip_times.append(now)
            self._failures[ip_key] = ip_times

            if len(ip_times) >= self.max_ip_failures:
                self._lockouts[ip_key] = now + self.lockout_duration_seconds
                logger.warning(
                    "IP locked due to credential stuffing: %s (%d failures)",
                    client_ip,
                    len(ip_times),
                )

    def record_success(self, client_ip: str, email: str) -> None:
        """Clear account failure history on successful login."""
        account_key = f"acc:{email.strip().lower()}"
        with self._lock:
            self._failures.pop(account_key, None)
            self._lockouts.pop(account_key, None)


# Module-level default instance
brute_force_defender = BruteForceDefender()
