"""Logging and Error Tracking Security Sanitizer.

Prevents passwords, API keys, tokens, credentials, connection strings,
and Personally Identifiable Information (PII) such as emails and phone numbers
from being emitted into application logs or dispatched to error tracking services.
"""

from __future__ import annotations

import copy
import logging
import re
import sys
from typing import Any, cast

from stock_analysis.security.audit import hash_client_ip, mask_email

# Precompiled regex patterns for detecting and scrubbing credentials and secrets
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"private[_-]?key|auth[_-]?token|client[_-]?secret|session[_-]?id)\b"
    r"(\s*[:=]\s*|\s+is\s+)(['\"]?)(?!\[REDACTED\])([^'\",\s]+)\3"
)

BEARER_TOKEN_PATTERN = re.compile(r"(?i)\b(bearer\s+)(?!\[REDACTED\])([A-Za-z0-9_\-\.]{8,})\b")

JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

DATABASE_URL_PATTERN = re.compile(
    r"\b((?:postgresql|postgres|mysql|sqlite|redis|amqp|mongodb)(?:\+[a-z0-9]+)?://"
    r"[^:\s@]+):(?!\[REDACTED\]@)([^@\s]+)@([a-zA-Z0-9_\-\.]+(?::\d+)?(?:/[^\s]*)?)\b"
)

HTTP_BASIC_AUTH_PATTERN = re.compile(
    r"\b(https?://[^:\s@]+):(?!\[REDACTED\]@)([^@\s]+)@([a-zA-Z0-9_\-\.]+(?::\d+)?(?:/[^\s]*)?)\b"
)

PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]+ PRIVATE KEY-----"
)

EMAIL_ADDRESS_PATTERN = re.compile(r"\b([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b")

TURKISH_PHONE_PATTERN = re.compile(r"(?:\+90|0090|0)?\s*([5]\d{2})\s*(\d{3})\s*(\d{2})\s*(\d{2})\b")

TURKISH_TCKN_PATTERN = re.compile(
    r"(?i)\b(tckn|tc|kimlik(?:[_-]?no)?|national[_-]?id)([\s:=_-]+)([1-9]\d{10})\b"
)

# Standard set of dict keys that contain sensitive values and must be redacted
SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "auth_token",
        "token",
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "private_key",
        "client_secret",
        "session_id",
        "sessionid",
        "pin",
        "cvv",
        "credit_card",
        "db_password",
        "smtp_password",
    }
)


class SensitiveDataLeakError(ValueError):
    """Raised when an unredacted credential, token, or PII leak is detected in sanitized data."""


def sanitize_text(text: str) -> str:
    """Sanitize raw text by scrubbing secrets, passwords, tokens, and masking PII."""
    if not text:
        return text

    # 1. Private key blocks
    scrubbed = PRIVATE_KEY_BLOCK_PATTERN.sub("[REDACTED_PRIVATE_KEY]", text)

    # 2. Database connection strings with embedded passwords
    scrubbed = DATABASE_URL_PATTERN.sub(r"\1:[REDACTED]@\3", scrubbed)

    # 3. HTTP basic auth URLs
    scrubbed = HTTP_BASIC_AUTH_PATTERN.sub(r"\1:[REDACTED]@\3", scrubbed)

    # 4. Bearer tokens
    scrubbed = BEARER_TOKEN_PATTERN.sub(r"\1[REDACTED]", scrubbed)

    # 5. JWT tokens
    scrubbed = JWT_PATTERN.sub("[JWT_TOKEN_REDACTED]", scrubbed)

    # 6. Explicit password/secret/api_key assignments
    scrubbed = SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2\3[REDACTED]\3", scrubbed)

    # 7. Emails (mask according to KVKK: u***@domain.com)
    def _mask_email_match(match: re.Match[str]) -> str:
        return mask_email(match.group(0))

    scrubbed = EMAIL_ADDRESS_PATTERN.sub(_mask_email_match, scrubbed)

    # 8. Turkish mobile numbers: +90 5XX XXX XX12 -> +90 5XX *** **12
    def _mask_phone_match(match: re.Match[str]) -> str:
        code, last = match.group(1), match.group(4)
        return f"+90 {code} *** **{last}"

    scrubbed = TURKISH_PHONE_PATTERN.sub(_mask_phone_match, scrubbed)

    # 9. Explicit TCKN values
    scrubbed = TURKISH_TCKN_PATTERN.sub(r"\1\2***********", scrubbed)

    return scrubbed


def sanitize_data_structure(data: Any) -> Any:
    """Recursively scrub sensitive keys and sanitize text in arbitrary nested structures."""
    if isinstance(data, dict):
        clean_dict: dict[str, Any] = {}
        for key, value in data.items():
            str_key = str(key).lower().strip()
            if any(s in str_key for s in SENSITIVE_KEY_NAMES):
                clean_dict[key] = "[REDACTED]"
            else:
                clean_dict[key] = sanitize_data_structure(value)
        return clean_dict
    elif isinstance(data, (list, tuple, set)):
        clean_seq = [sanitize_data_structure(item) for item in data]
        if isinstance(data, tuple):
            return tuple(clean_seq)
        if isinstance(data, set):
            try:
                return set(clean_seq)
            except TypeError:
                return clean_seq
        return clean_seq
    elif isinstance(data, str):
        return sanitize_text(data)
    return data


class SensitiveDataFilter(logging.Filter):
    """Logging filter that scrubs credentials, tokens, passwords, and PII from log records.

    Inspects and sanitizes:
    - record.msg (string or dict/object)
    - record.args (tuple or dict passed for interpolation)
    - custom extra attributes attached to the LogRecord
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitize main message
        if isinstance(record.msg, str):
            record.msg = sanitize_text(record.msg)
        elif isinstance(record.msg, (dict, list, tuple)):
            record.msg = sanitize_data_structure(record.msg)

        # Sanitize formatting arguments
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_data_structure(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize_data_structure(arg) for arg in record.args)

        # Sanitize any extra attributes on the record
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "asctime",
        }
        for attr, value in record.__dict__.items():
            if attr not in standard_attrs and not attr.startswith("_"):
                if isinstance(value, (str, dict, list, tuple)):
                    setattr(record, attr, sanitize_data_structure(value))

        return True


class SanitizedLogFormatter(logging.Formatter):
    """Log formatter that ensures formatted strings and exception tracebacks are sanitized."""

    def format(self, record: logging.LogRecord) -> str:
        # If exc_info is True (standard logger convention), resolve to sys.exc_info()
        if getattr(record, "exc_info", None) is True:
            record.exc_info = sys.exc_info()
        # Format record normally
        formatted = super().format(record)
        # Ensure final formatted string (tracebacks and format args) is sanitized
        return sanitize_text(formatted)


class ErrorTrackingSanitizer:
    """Intercepts and scrubs error payloads before dispatching to external error monitoring."""

    def __init__(self, ip_salt: str = "error_tracking_ip_salt") -> None:
        self.ip_salt = ip_salt

    def sanitize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Deeply scrub an error tracking event dictionary (e.g. Sentry or custom APM).

        Removes/masks:
        - Sensitive HTTP request headers (Authorization, Cookie, X-Api-Key)
        - Sensitive query parameters and request payload body
        - Plain user emails and personal info
        - Raw client IP addresses (hashes with SHA-256)
        - Secrets in breadcrumbs, tags, and extra metadata
        - Sensitive variables in stack traces
        """
        clean_event = copy.deepcopy(event)

        # 1. Scrub HTTP Request metadata
        if "request" in clean_event and isinstance(clean_event["request"], dict):
            req = clean_event["request"]
            # Scrub headers
            if "headers" in req and isinstance(req["headers"], dict):
                clean_headers: dict[str, Any] = {}
                sensitive_headers = {
                    "authorization",
                    "proxy-authorization",
                    "cookie",
                    "set-cookie",
                    "x-api-key",
                }
                for h_name, h_val in req["headers"].items():
                    h_lower = str(h_name).lower()
                    if h_lower in sensitive_headers:
                        clean_headers[h_name] = "[REDACTED]"
                    else:
                        clean_headers[h_name] = sanitize_text(str(h_val))
                req["headers"] = clean_headers

            # Scrub query params
            if "query_params" in req and isinstance(req["query_params"], dict):
                req["query_params"] = sanitize_data_structure(req["query_params"])
            elif "query_string" in req and isinstance(req["query_string"], str):
                req["query_string"] = sanitize_text(req["query_string"])

            # Scrub request payload/data
            if "data" in req:
                req["data"] = sanitize_data_structure(req["data"])
            if "body" in req:
                req["body"] = sanitize_data_structure(req["body"])

        # 2. Scrub User context
        if "user" in clean_event and isinstance(clean_event["user"], dict):
            user_data = clean_event["user"]
            if "email" in user_data and isinstance(user_data["email"], str):
                user_data["email"] = mask_email(user_data["email"])
            if "ip_address" in user_data and user_data["ip_address"]:
                user_data["ip_address"] = hash_client_ip(user_data["ip_address"], salt=self.ip_salt)
            # Remove any raw credentials or secrets in user dict
            for key in list(user_data.keys()):
                if key.lower() in SENSITIVE_KEY_NAMES:
                    user_data[key] = "[REDACTED]"

        # 3. Scrub Breadcrumbs
        if "breadcrumbs" in clean_event and isinstance(clean_event["breadcrumbs"], (list, dict)):
            clean_event["breadcrumbs"] = sanitize_data_structure(clean_event["breadcrumbs"])

        # 4. Scrub Extra / Contexts
        if "extra" in clean_event and isinstance(clean_event["extra"], dict):
            clean_event["extra"] = sanitize_data_structure(clean_event["extra"])
        if "contexts" in clean_event and isinstance(clean_event["contexts"], dict):
            clean_event["contexts"] = sanitize_data_structure(clean_event["contexts"])

        # 5. Scrub Exceptions / Stack traces
        if "exception" in clean_event and isinstance(clean_event["exception"], dict):
            exc = clean_event["exception"]
            if "values" in exc and isinstance(exc["values"], list):
                for val in exc["values"]:
                    if isinstance(val, dict):
                        if "value" in val and isinstance(val["value"], str):
                            val["value"] = sanitize_text(val["value"])
                        if "stacktrace" in val and isinstance(val["stacktrace"], dict):
                            st = val["stacktrace"]
                            if "frames" in st and isinstance(st["frames"], list):
                                for frame in st["frames"]:
                                    if isinstance(frame, dict):
                                        if "vars" in frame and isinstance(frame["vars"], dict):
                                            frame["vars"] = sanitize_data_structure(frame["vars"])

        # 6. Global pass on entire event to catch anything stray
        return cast(dict[str, Any], sanitize_data_structure(clean_event))

    def verify_clean_event(self, event: dict[str, Any]) -> tuple[bool, list[str]]:
        """Verify that an event dict is free of unredacted secrets and plain emails.

        Returns (is_clean, violations).
        """
        violations: list[str] = []

        def _inspect(obj: Any, path: str = "root") -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    k_str = str(k).lower()
                    if k_str in SENSITIVE_KEY_NAMES and v != "[REDACTED]":
                        violations.append(f"Unredacted sensitive key '{path}.{k}' with value '{v}'")
                    _inspect(v, f"{path}.{k}")
            elif isinstance(obj, (list, tuple)):
                for idx, item in enumerate(obj):
                    _inspect(item, f"{path}[{idx}]")
            elif isinstance(obj, str):
                # Check for plain emails that aren't masked (e.g. user@domain.com)
                for email_match in EMAIL_ADDRESS_PATTERN.finditer(obj):
                    email = email_match.group(0)
                    if "***" not in email:
                        violations.append(
                            f"Unmasked plain email address found at '{path}': '{email}'"
                        )
                # Check for bearer tokens
                if BEARER_TOKEN_PATTERN.search(obj):
                    violations.append(f"Unredacted Bearer token found at '{path}'")
                # Check for JWT tokens
                if JWT_PATTERN.search(obj):
                    violations.append(f"Unredacted JWT token found at '{path}'")
                # Check for passwords in DB strings
                if DATABASE_URL_PATTERN.search(obj):
                    violations.append(f"Unredacted database credentials found at '{path}'")

        _inspect(event)
        return len(violations) == 0, violations

    def assert_clean_event(self, event: dict[str, Any]) -> None:
        """Assert that the event is clean, raising SensitiveDataLeakError on any violation."""
        is_clean, violations = self.verify_clean_event(event)
        if not is_clean:
            raise SensitiveDataLeakError(
                f"Error tracking event contains sensitive leaks: {'; '.join(violations)}"
            )


def configure_secure_logging(
    root_level: int = logging.INFO,
    attach_to_root: bool = True,
    target_loggers: list[str] | None = None,
) -> SensitiveDataFilter:
    """Attach SensitiveDataFilter and SanitizedLogFormatter to application loggers."""
    secure_filter = SensitiveDataFilter()
    formatter = SanitizedLogFormatter(fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    loggers_to_configure: list[logging.Logger] = []
    if attach_to_root:
        loggers_to_configure.append(logging.getLogger())

    targets = target_loggers or ["stock_analysis", "uvicorn", "fastapi"]
    for name in targets:
        loggers_to_configure.append(logging.getLogger(name))

    for log in loggers_to_configure:
        log.setLevel(root_level)
        # Avoid adding duplicate filters
        if not any(isinstance(f, SensitiveDataFilter) for f in log.filters):
            log.addFilter(secure_filter)

        for handler in log.handlers:
            handler.setFormatter(formatter)
            if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
                handler.addFilter(secure_filter)

    return secure_filter
