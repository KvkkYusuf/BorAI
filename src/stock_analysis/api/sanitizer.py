"""Security sanitization and masking utilities for errors, logs, and API payloads."""

from __future__ import annotations

import re
from typing import Any

# Regular expressions for detecting sensitive information leaks
_EMAIL_REGEX = re.compile(r"\b([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b")

_SQL_LEAK_PATTERNS = [
    re.compile(
        r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(FROM|WHERE|GROUP\s+BY|ORDER\s+BY|JOIN|UNION)\s+[a-zA-Z0-9_.]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(sqlite3\.|psycopg2\.|sqlalchemy\.|sqlite_master|pg_catalog)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(OperationalError|ProgrammingError|IntegrityError|DatabaseError)\b",
        re.IGNORECASE,
    ),
]

_TRACE_LEAK_PATTERNS = [
    re.compile(r"Traceback\s+\(most\s+recent\s+call\s+last\):", re.IGNORECASE),
    re.compile(r'File\s+"[^"]+",\s+line\s+\d+', re.IGNORECASE),
    re.compile(
        r"\b(AttributeError|KeyError|TypeError|ValueError|ZeroDivisionError):\s+",
        re.IGNORECASE,
    ),
]

_SECRET_LEAK_PATTERNS = [
    re.compile(
        r"\b(password|secret|api_key|access_token|refresh_token|private_key)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(Bearer\s+[A-Za-z0-9_\-\.]+)\b", re.IGNORECASE),
    re.compile(r"\b(\$scrypt\$|\$2[aby]\$|\$argon2id\$)\S+", re.IGNORECASE),
]

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "token",
        "api_key",
        "authorization",
        "access_token",
        "refresh_token",
        "client_secret",
    }
)

SAFE_INTERNAL_ERROR_MESSAGE = "Sunucuda beklenmeyen bir hata olustu."
SAFE_DATABASE_ERROR_MESSAGE = "Veritabani islemi sirasinda bir hata meydana geldi."
SAFE_GENERIC_ERROR_MESSAGE = "Guvenlik nedeniyle hata detaylari gizlendi."


def mask_email(email: str | None) -> str:
    """Mask email address to conform to privacy/KVKK rules (e.g. u***@domain.com)."""
    if not email or "@" not in email:
        return ""
    parts = email.split("@", 1)
    user_part = parts[0]
    domain_part = parts[1]
    if not user_part:
        return f"***@{domain_part}"
    first_char = user_part[0]
    return f"{first_char}***@{domain_part}"


def mask_emails_in_text(text: str) -> str:
    """Replace all emails in a text string with their masked format."""
    return _EMAIL_REGEX.sub(r"\1***@\2", text)


def contains_leak_indicators(text: str) -> bool:
    """Check if the given text contains SQL fragments, stack traces, or secrets."""
    for pattern in _SQL_LEAK_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in _TRACE_LEAK_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in _SECRET_LEAK_PATTERNS:
        if pattern.search(text):
            return True
    return False


def sanitize_error_message(
    message: str, fallback_message: str = "Islem sirasinda bir hata olustu."
) -> str:
    """Ensure error messages returned to clients never leak internal details.

    If SQL syntax, tracebacks, or secrets are detected, returns a safe generic message.
    Also masks any email addresses contained in the message.
    """
    if contains_leak_indicators(message):
        return fallback_message
    return mask_emails_in_text(message)


def sanitize_validation_error_loc(loc: tuple[Any, ...]) -> str:
    """Sanitize location path in validation error, ensuring secret values are not reflected."""
    parts = []
    for part in loc:
        str_part = str(part)
        if str_part.lower() in SENSITIVE_FIELD_NAMES:
            parts.append(str_part)
        else:
            parts.append(mask_emails_in_text(str_part))
    return " -> ".join(parts)
