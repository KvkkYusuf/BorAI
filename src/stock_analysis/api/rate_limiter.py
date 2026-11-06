"""Thread-safe in-memory sliding window rate limiter for API endpoints.

Implements IP and authenticated user-based rate limiting with standard 429
responses and Retry-After headers, adhering to security guidelines.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException, Request, status

logger = logging.getLogger("stock_analysis.api.rate_limiter")


class SlidingWindowRateLimiter:
    """Sliding-window rate limiter tracking timestamps per client identifier."""

    def __init__(
        self,
        times: int,
        seconds: int,
        key_func: Callable[[Request], str] | None = None,
        enabled: bool = True,
    ) -> None:
        self.times = times
        self.seconds = seconds
        self.key_func = key_func or self._default_client_key
        self.enabled = enabled
        self._lock = threading.Lock()
        self._history: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def _default_client_key(request: Request) -> str:
        """Derive rate-limit key from client IP address or forward headers."""
        # Check forwarded headers first (for reverse proxy environments)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client and request.client.host:
            client_ip = request.client.host
        else:
            client_ip = "127.0.0.1"

        return f"ip:{client_ip}"

    def reset(self) -> None:
        """Clear all rate limiting history (primarily for tests)."""
        with self._lock:
            self._history.clear()

    def __call__(self, request: Request) -> None:
        """FastAPI dependency callable that checks rate limit for incoming request."""
        if not self.enabled:
            return

        key = self.key_func(request)
        now = time.time()
        window_start = now - self.seconds

        with self._lock:
            timestamps = self._history[key]
            # Prune timestamps older than window
            valid_timestamps = [t for t in timestamps if t > window_start]

            if len(valid_timestamps) >= self.times:
                oldest_timestamp = valid_timestamps[0]
                retry_after = max(1, int(oldest_timestamp + self.seconds - now))
                logger.warning(
                    "Rate limit exceeded for client %s: %d requests in %ds (limit %d)",
                    key,
                    len(valid_timestamps) + 1,
                    self.seconds,
                    self.times,
                )
                now_iso = datetime.now(UTC).isoformat()
                message_str = (
                    f"Cok fazla istek gonderildi. Lutfen {retry_after} saniye sonra tekrar deneyin."
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": message_str,
                        "retry_after": retry_after,
                        "timestamp": now_iso,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            valid_timestamps.append(now)
            self._history[key] = valid_timestamps


class TieredRateLimiter:
    """Rate limiter separating anonymous and authenticated traffic with different limits.

    Anonymous clients (no Bearer token) are subjected to strict rate limits by IP (e.g. 20 req/min).
    Authenticated clients (with valid Bearer token) have higher limits (e.g. 60 req/min) tracked
    by a token digest or identity.
    """

    def __init__(
        self,
        anon_times: int = 20,
        anon_seconds: int = 60,
        auth_times: int = 60,
        auth_seconds: int = 60,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.anon_limiter = SlidingWindowRateLimiter(
            times=anon_times,
            seconds=anon_seconds,
            key_func=lambda req: f"anon:{SlidingWindowRateLimiter._default_client_key(req)}",
            enabled=enabled,
        )
        self.auth_limiter = SlidingWindowRateLimiter(
            times=auth_times,
            seconds=auth_seconds,
            key_func=self._auth_client_key,
            enabled=enabled,
        )

    @staticmethod
    def _auth_client_key(request: Request) -> str:
        """Derive key for authenticated request using hashed token."""
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.startswith("Bearer ") and len(auth_header) > 7:
            token = auth_header[7:].strip()
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            return f"user:{token_hash}"
        return f"auth:{SlidingWindowRateLimiter._default_client_key(request)}"

    def reset(self) -> None:
        """Reset internal rate limit history for both tiers."""
        self.anon_limiter.reset()
        self.auth_limiter.reset()

    def __call__(self, request: Request) -> None:
        """FastAPI dependency callable routing request to anonymous or authenticated limiter."""
        if not self.enabled:
            return

        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.startswith("Bearer ") and len(auth_header) > 7:
            self.auth_limiter(request)
        else:
            self.anon_limiter(request)


# Standard pre-configured rate limiters for different endpoint sensitivities
auth_rate_limiter = SlidingWindowRateLimiter(times=5, seconds=60)
# Anonymous read-only market queries: strictly 20 req/min; Authenticated: 60 req/min
market_rate_limiter = TieredRateLimiter(
    anon_times=20, anon_seconds=60, auth_times=60, auth_seconds=60
)
anon_market_rate_limiter = market_rate_limiter.anon_limiter
auth_market_rate_limiter = market_rate_limiter.auth_limiter

analytics_rate_limiter = SlidingWindowRateLimiter(times=30, seconds=60)
forecast_rate_limiter = SlidingWindowRateLimiter(times=10, seconds=60)
user_rate_limiter = SlidingWindowRateLimiter(times=60, seconds=60)
destructive_rate_limiter = SlidingWindowRateLimiter(times=5, seconds=60)
public_rate_limiter = SlidingWindowRateLimiter(times=30, seconds=60)


def reset_all_rate_limiters() -> None:
    """Reset all global rate limiters."""
    auth_rate_limiter.reset()
    market_rate_limiter.reset()
    analytics_rate_limiter.reset()
    forecast_rate_limiter.reset()
    user_rate_limiter.reset()
    destructive_rate_limiter.reset()
    public_rate_limiter.reset()
