"""Security HTTP headers, Content Security Policy (CSP), and TLS/HSTS controls.

Implements defense-in-depth against:
- Clickjacking (X-Frame-Options, CSP frame-ancestors 'none')
- MIME sniffing (X-Content-Type-Options: nosniff)
- Cross-Site Scripting / Data injection (Content-Security-Policy)
- Protocol Downgrade & Man-in-the-Middle (HTTP Strict Transport Security / HSTS)
- Device capability abuse (Permissions-Policy)
- Cross-Origin Isolation (Cross-Origin-Opener-Policy, Cross-Origin-Resource-Policy)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from stock_analysis.config import Settings, get_settings

logger = logging.getLogger("stock_analysis.security.headers")

DEFAULT_CSP_DIRECTIVES: dict[str, list[str]] = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:", "https:"],
    "font-src": ["'self'", "data:"],
    "connect-src": ["'self'"],
    "frame-ancestors": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "object-src": ["'none'"],
}

DEFAULT_PERMISSIONS_POLICY: str = (
    "geolocation=(), camera=(), microphone=(), payment=(), usb=(), vr=(), "
    "accelerometer=(), gyroscope=()"
)


@dataclass
class SecurityHeadersConfig:
    """Configuration parameters for HTTP security headers."""

    enable_hsts: bool = True
    hsts_max_age_seconds: int = 31536000
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False
    csp_enabled: bool = True
    csp_report_only: bool = False
    custom_csp_directives: str | None = None
    force_hsts: bool = False
    app_env: str = "development"
    referrer_policy: str = "strict-origin-when-cross-origin"
    frame_options: str = "DENY"
    permissions_policy: str = DEFAULT_PERMISSIONS_POLICY
    cross_origin_opener_policy: str = "same-origin"
    cross_origin_resource_policy: str = "same-origin"
    csp_directives: dict[str, list[str]] = field(
        default_factory=lambda: dict(DEFAULT_CSP_DIRECTIVES)
    )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, force_hsts: bool = False
    ) -> SecurityHeadersConfig:
        """Create SecurityHeadersConfig from application Settings."""
        return cls(
            enable_hsts=settings.enable_hsts,
            hsts_max_age_seconds=settings.hsts_max_age_seconds,
            hsts_include_subdomains=settings.hsts_include_subdomains,
            hsts_preload=settings.hsts_preload,
            csp_enabled=settings.csp_enabled,
            csp_report_only=settings.csp_report_only,
            custom_csp_directives=settings.custom_csp_directives,
            force_hsts=force_hsts,
            app_env=settings.app_env,
        )


def build_content_security_policy(
    directives: dict[str, list[str]] | None = None,
    custom_override: str | None = None,
) -> str:
    """Format Content Security Policy directive string.

    Args:
        directives: Dictionary mapping CSP directive names to lists of source tokens.
        custom_override: Raw CSP string overriding defaults if provided.

    Returns:
        Semicolon-separated CSP string.
    """
    if custom_override and custom_override.strip():
        return custom_override.strip()

    policy_dict = directives if directives is not None else DEFAULT_CSP_DIRECTIVES
    tokens: list[str] = []
    for directive, sources in policy_dict.items():
        if sources:
            tokens.append(f"{directive} {' '.join(sources)}")
        else:
            tokens.append(directive)

    return "; ".join(tokens)


def build_hsts_header(
    max_age_seconds: int = 31536000,
    include_subdomains: bool = True,
    preload: bool = False,
) -> str:
    """Build Strict-Transport-Security header value RFC 6797.

    Args:
        max_age_seconds: Max age duration in seconds (default: 1 year).
        include_subdomains: Whether to apply HSTS to all subdomains.
        preload: Whether to consent to browser preloading lists.

    Returns:
        Formatted HSTS header value.
    """
    parts = [f"max-age={max_age_seconds}"]
    if include_subdomains:
        parts.append("includeSubDomains")
    if preload:
        parts.append("preload")
    return "; ".join(parts)


def should_apply_hsts(request: Request, config: SecurityHeadersConfig) -> bool:
    """Determine whether Strict-Transport-Security should be attached to response.

    HSTS is applied when:
    - force_hsts is explicitly True
    - Application is running in production (APP_ENV=production)
    - Request was received via HTTPS (direct scheme or X-Forwarded-Proto header)
    """
    if not config.enable_hsts:
        return False
    if config.force_hsts:
        return True
    if config.app_env.lower() == "production":
        return True
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    return forwarded_proto == "https"


def apply_security_headers(
    response: Response,
    request: Request,
    config: SecurityHeadersConfig | None = None,
) -> None:
    """Apply baseline security headers, CSP, and HSTS to an outgoing response."""
    if config is None:
        settings = get_settings()
        config = SecurityHeadersConfig.from_settings(settings)

    # 1. MIME Sniffing protection
    response.headers["X-Content-Type-Options"] = "nosniff"

    # 2. Clickjacking protection (legacy fallback for older browsers)
    response.headers["X-Frame-Options"] = config.frame_options

    # 3. Referrer leakage prevention
    response.headers["Referrer-Policy"] = config.referrer_policy

    # 4. Device and sensor capability restrictions
    if config.permissions_policy:
        response.headers["Permissions-Policy"] = config.permissions_policy

    # 5. Cross-Origin isolation headers
    response.headers["Cross-Origin-Opener-Policy"] = config.cross_origin_opener_policy
    response.headers["Cross-Origin-Resource-Policy"] = config.cross_origin_resource_policy

    # 6. Legacy XSS filter disablement (modern standard prefers CSP)
    response.headers["X-XSS-Protection"] = "0"

    # 7. Content Security Policy (CSP)
    if config.csp_enabled:
        csp_value = build_content_security_policy(
            directives=config.csp_directives,
            custom_override=config.custom_csp_directives,
        )
        csp_header_name = (
            "Content-Security-Policy-Report-Only"
            if config.csp_report_only
            else "Content-Security-Policy"
        )
        response.headers[csp_header_name] = csp_value

    # 8. HTTP Strict Transport Security (HSTS)
    if should_apply_hsts(request, config):
        response.headers["Strict-Transport-Security"] = build_hsts_header(
            max_age_seconds=config.hsts_max_age_seconds,
            include_subdomains=config.hsts_include_subdomains,
            preload=config.hsts_preload,
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """ASGI Middleware applying modern security headers and CSP to all HTTP responses."""

    def __init__(
        self,
        app: ASGIApp,
        config: SecurityHeadersConfig | None = None,
    ) -> None:
        super().__init__(app)
        self.config = config

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        resolved_config = self.config
        if resolved_config is None:
            settings = get_settings()
            resolved_config = SecurityHeadersConfig.from_settings(settings)

        apply_security_headers(response, request, resolved_config)
        return response
