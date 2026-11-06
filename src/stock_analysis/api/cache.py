"""HTTP Caching (ETag, Cache-Control, 304 Not Modified) and Redis / In-Memory cache storage.

Supports deterministic ETag computation, conditional request validation,
and configurable TTL-based caching with automatic in-memory fallback.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, cast, runtime_checkable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from stock_analysis.config import get_settings

logger = logging.getLogger("stock_analysis.api.cache")


def compute_etag(content: bytes) -> str:
    """Compute a deterministic strong HTTP ETag from raw response bytes."""
    digest = hashlib.sha256(content).hexdigest()[:16]
    return f'"{digest}"'


@runtime_checkable
class CacheBackend(Protocol):
    """Protocol defining key-value cache backend operations."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, expire_seconds: int = 60) -> None: ...
    async def delete(self, key: str) -> bool: ...
    async def clear(self) -> None: ...
    def get_sync(self, key: str) -> str | None: ...
    def set_sync(self, key: str, value: str, expire_seconds: int = 60) -> None: ...
    def delete_sync(self, key: str) -> bool: ...
    def clear_sync(self) -> None: ...


class InMemoryCacheBackend:
    """Thread-safe in-memory cache backend with monotonic TTL expiration."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get_sync(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expiry, value = entry
            if time.monotonic() >= expiry:
                self._store.pop(key, None)
                return None
            return value

    def set_sync(self, key: str, value: str, expire_seconds: int = 60) -> None:
        with self._lock:
            expiry = time.monotonic() + expire_seconds
            self._store[key] = (expiry, value)

    def delete_sync(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear_sync(self) -> None:
        with self._lock:
            self._store.clear()

    async def get(self, key: str) -> str | None:
        return self.get_sync(key)

    async def set(self, key: str, value: str, expire_seconds: int = 60) -> None:
        self.set_sync(key, value, expire_seconds=expire_seconds)

    async def delete(self, key: str) -> bool:
        return self.delete_sync(key)

    async def clear(self) -> None:
        self.clear_sync()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class RedisCacheBackend:
    """Redis cache backend using redis client with graceful error handling and fallback."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._sync_client: Any = None
        self._async_client: Any = None
        self._fallback = InMemoryCacheBackend()
        self._redis_available: bool = True

    def _get_sync_client(self) -> Any:
        if not self._redis_available:
            return None
        if self._sync_client is None:
            try:
                import redis

                client = redis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                client.ping()
                self._sync_client = client
            except Exception as exc:
                logger.debug(
                    "Redis sync unavailable (%s), fallback to memory: %s",
                    self._redis_url,
                    exc,
                )
                self._redis_available = False
                self._sync_client = None
        return self._sync_client

    async def _get_async_client(self) -> Any:
        if not self._redis_available:
            return None
        if self._async_client is None:
            try:
                import redis.asyncio as aioredis

                client = aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                await client.ping()
                self._async_client = client
            except Exception as exc:
                logger.debug(
                    "Redis async unavailable (%s), fallback to memory: %s",
                    self._redis_url,
                    exc,
                )
                self._redis_available = False
                self._async_client = None
        return self._async_client

    def get_sync(self, key: str) -> str | None:
        client = self._get_sync_client()
        if client is not None:
            try:
                val = client.get(key)
                return str(val) if val is not None else None
            except Exception as exc:
                logger.debug("Redis get_sync error on '%s': %s", key, exc)
        return self._fallback.get_sync(key)

    def set_sync(self, key: str, value: str, expire_seconds: int = 60) -> None:
        client = self._get_sync_client()
        if client is not None:
            try:
                client.set(key, value, ex=expire_seconds)
                return
            except Exception as exc:
                logger.debug("Redis set_sync error on '%s': %s", key, exc)
        self._fallback.set_sync(key, value, expire_seconds=expire_seconds)

    def delete_sync(self, key: str) -> bool:
        client = self._get_sync_client()
        if client is not None:
            try:
                res = client.delete(key)
                return bool(res > 0)
            except Exception as exc:
                logger.debug("Redis delete_sync error on '%s': %s", key, exc)
        return self._fallback.delete_sync(key)

    def clear_sync(self) -> None:
        client = self._get_sync_client()
        if client is not None:
            try:
                client.flushdb()
            except Exception as exc:
                logger.debug("Redis clear_sync error: %s", exc)
        self._fallback.clear_sync()

    async def get(self, key: str) -> str | None:
        client = await self._get_async_client()
        if client is not None:
            try:
                val = await client.get(key)
                return str(val) if val is not None else None
            except Exception as exc:
                logger.debug("Redis get error on '%s': %s", key, exc)
        return await self._fallback.get(key)

    async def set(self, key: str, value: str, expire_seconds: int = 60) -> None:
        client = await self._get_async_client()
        if client is not None:
            try:
                await client.set(key, value, ex=expire_seconds)
                return
            except Exception as exc:
                logger.debug("Redis set error on '%s': %s", key, exc)
        await self._fallback.set(key, value, expire_seconds=expire_seconds)

    async def delete(self, key: str) -> bool:
        client = await self._get_async_client()
        if client is not None:
            try:
                res = await client.delete(key)
                return bool(res > 0)
            except Exception as exc:
                logger.debug("Redis delete error on '%s': %s", key, exc)
        return await self._fallback.delete(key)

    async def clear(self) -> None:
        client = await self._get_async_client()
        if client is not None:
            try:
                await client.flushdb()
            except Exception as exc:
                logger.debug("Redis clear error: %s", exc)
        await self._fallback.clear()


_global_cache: CacheBackend | None = None


def get_cache_backend() -> CacheBackend:
    """Return the global cache backend instance (Redis with In-Memory fallback)."""
    global _global_cache
    if _global_cache is None:
        settings = get_settings()
        if settings.redis_url and settings.redis_url.startswith("redis://"):
            _global_cache = RedisCacheBackend(redis_url=settings.redis_url)
        else:
            _global_cache = InMemoryCacheBackend()
    return _global_cache


def set_cache_backend(backend: CacheBackend | None) -> None:
    """Override or reset the global cache backend (mainly for testing)."""
    global _global_cache
    _global_cache = backend


class HttpCacheMiddleware(BaseHTTPMiddleware):
    """Middleware managing ETag generation, 304 validation, and Cache-Control headers."""

    def __init__(
        self,
        app: ASGIApp,
        default_max_age: int = 60,
        cacheable_paths: tuple[str, ...] = (
            "/api/v1/market/symbols",
            "/api/v1/market/history",
            "/api/v1/analytics/indicators",
            "/api/v1/analytics/screener",
            "/api/v1/system/health",
            "/api/v1/system/info",
        ),
    ) -> None:
        super().__init__(app)
        self.default_max_age = default_max_age
        self.cacheable_paths = cacheable_paths

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Only process safe, idempotent HTTP GET and HEAD requests
        if request.method not in ("GET", "HEAD"):
            return await call_next(request)

        path = request.url.path
        is_cacheable = any(path.startswith(prefix) for prefix in self.cacheable_paths)

        response: Response = await call_next(request)

        # Do not cache error responses or non-200 responses
        if response.status_code != 200:
            return response

        if not is_cacheable:
            return response

        # Read response body to calculate deterministic ETag
        body_chunks: list[bytes] = []
        body_iter = getattr(response, "body_iterator", None)
        if body_iter is not None:
            async for chunk in cast(AsyncIterator[Any], body_iter):
                if isinstance(chunk, bytes):
                    body_chunks.append(chunk)
                elif isinstance(chunk, str):
                    body_chunks.append(chunk.encode("utf-8"))
        elif hasattr(response, "body"):
            raw_body = getattr(response, "body", b"")
            if isinstance(raw_body, bytes):
                body_chunks.append(raw_body)
            elif isinstance(raw_body, str):
                body_chunks.append(raw_body.encode("utf-8"))

        full_body = b"".join(body_chunks)
        etag = compute_etag(full_body)

        # If client supplied If-None-Match matching the ETag, return HTTP 304 Not Modified
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match.strip() == etag:
            headers = dict(response.headers)
            headers["etag"] = etag
            headers["cache-control"] = f"public, max-age={self.default_max_age}"
            return Response(status_code=304, headers=headers)

        # Reconstruct response with cached headers
        response.headers["etag"] = etag
        if "cache-control" not in response.headers:
            response.headers["cache-control"] = f"public, max-age={self.default_max_age}"

        # Return reconstructed response with consumed body
        return Response(
            content=full_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
