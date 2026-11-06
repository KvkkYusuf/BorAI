"""Safe query execution utilities and parameterized SQL enforcement.

Ensures all database operations adhere to parameterized query / ORM rules,
prohibiting raw string concatenation and unsanitized queries.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.sql import Executable

# Pattern to detect suspicious string concatenation attempts or unescaped
# interpolation in SQL strings
_INTERPOLATION_PATTERN = re.compile(
    r"(\%s|\%d|\{[^}]*\}|\'[^\']*\'\s*\+|\+\s*\'[^\']*\')",
    re.IGNORECASE,
)


class UnsafeQueryError(ValueError):
    """Raised when an unsafe or non-parameterized query attempt is detected."""


def escape_like_pattern(pattern: str, escape_char: str = "\\") -> str:
    r"""Escape wildcard characters in LIKE / ILIKE patterns to prevent wildcard injection.

    Escapes '%', '_', and the escape character itself.
    Example:
        >>> escape_like_pattern("100%_growth")
        '100\\%\\_growth'
    """
    escaped = pattern.replace(escape_char, escape_char * 2)
    escaped = escaped.replace("%", f"{escape_char}%")
    escaped = escaped.replace("_", f"{escape_char}_")
    return escaped


def safe_text(sql: str, **bind_params: Any) -> TextClause:
    """Create a strictly parameterized SQLAlchemy TextClause with bound parameters.

    Ensures that raw strings passed do not contain formatting artifacts,
    requiring named bind parameters (:param_name).

    Args:
        sql: Parameterized SQL string containing :named placeholders
             (e.g. "SELECT * FROM users WHERE id = :user_id")
        **bind_params: Key-value pairs matching placeholders.

    Returns:
        TextClause with bound parameters attached.

    Raises:
        UnsafeQueryError: If the SQL string contains formatting patterns.
    """
    if not isinstance(sql, str):
        raise UnsafeQueryError("SQL statement must be a string.")

    # Check for direct format specifiers like %s or {var}
    if _INTERPOLATION_PATTERN.search(sql):
        raise UnsafeQueryError(
            "SQL query contains suspicious string interpolation or formatting patterns. "
            "Use bound parameters (:param_name) instead of string formatting."
        )

    stmt = text(sql)
    if bind_params:
        stmt = stmt.bindparams(**bind_params)
    return stmt


def validate_executable(stmt: Any) -> Executable:
    """Validate that the query object is a compiled SQLAlchemy Executable, not a raw string.

    Prevents invocation of `session.execute("SELECT ...")` with raw unparameterized strings.

    Args:
        stmt: The statement object to validate.

    Returns:
        The validated Executable object.

    Raises:
        UnsafeQueryError: If a raw string or invalid object is passed.
    """
    if isinstance(stmt, str):
        raise UnsafeQueryError(
            "Direct execution of raw string SQL is forbidden. "
            "Use SQLAlchemy ORM constructs or safe_text(sql, **bind_params)."
        )

    if not isinstance(stmt, Executable):
        raise UnsafeQueryError(
            f"Expected SQLAlchemy Executable statement, got {type(stmt).__name__}."
        )

    return stmt
