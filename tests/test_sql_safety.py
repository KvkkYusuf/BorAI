"""Comprehensive SQL safety, parameterization, and SQL injection resilience tests.

Validates that:
1. ORM / parameterized queries are strictly enforced.
2. Direct execution of unparameterized strings is prohibited.
3. Common SQL injection payloads fail safely across all endpoints without leaking data or syntax.
4. Static AST analysis guarantees zero raw SQL concatenation across src/.
"""

from __future__ import annotations

import ast
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql import text

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.models import Instrument, User
from stock_analysis.db.safe_query import (
    UnsafeQueryError,
    escape_like_pattern,
    safe_text,
    validate_executable,
)
from stock_analysis.db.session import get_db

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def setup_test_db() -> Generator[None, None, None]:
    """Create test tables once for this test module."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(setup_test_db: None) -> Generator[DBSession, None, None]:
    """Provide isolated db session with seed data."""
    session = TestingSessionLocal()
    # Seed a test instrument and user
    inst = Instrument(
        symbol="THYAO",
        name="Türk Hava Yolları A.O.",
        currency="TRY",
        is_active=True,
    )
    user = User(
        email="analyst@example.com",
        hashed_password="scrypt$dummy_hash",
        is_active=True,
    )
    session.add(inst)
    session.add(user)
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.query(Instrument).delete()
        session.query(User).delete()
        session.commit()
        session.close()


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    """Provide a TestClient with rate limiter reset."""
    reset_all_rate_limiters()
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()
    reset_all_rate_limiters()


# ==============================================================================
# 1. Unit Tests for safe_query utilities
# ==============================================================================


def test_escape_like_pattern() -> None:
    """Ensure wildcards and escape characters are properly escaped."""
    assert escape_like_pattern("normal_text") == r"normal\_text"
    assert escape_like_pattern("100%_growth") == r"100\%\_growth"
    assert escape_like_pattern(r"path\to%_file") == r"path\\to\%\_file"
    assert escape_like_pattern("THYAO") == "THYAO"


def test_validate_executable_allows_orm_and_text() -> None:
    """Ensure compiled SQLAlchemy constructs are accepted."""
    stmt = select(Instrument).where(Instrument.symbol == "THYAO")
    assert validate_executable(stmt) is stmt

    txt = text("SELECT * FROM instruments WHERE symbol = :sym").bindparams(sym="THYAO")
    assert validate_executable(txt) is txt


def test_validate_executable_rejects_raw_strings() -> None:
    """Ensure raw strings cannot be passed directly as executable statements."""
    with pytest.raises(UnsafeQueryError, match="Direct execution of raw string SQL is forbidden"):
        validate_executable("SELECT * FROM users WHERE id = 1")

    with pytest.raises(UnsafeQueryError, match="Direct execution of raw string SQL is forbidden"):
        validate_executable("DROP TABLE instruments")


def test_validate_executable_rejects_invalid_types() -> None:
    """Ensure non-executable objects raise UnsafeQueryError."""
    with pytest.raises(UnsafeQueryError, match="Expected SQLAlchemy Executable statement"):
        validate_executable(12345)


def test_safe_text_with_bound_parameters(db_session: DBSession) -> None:
    """Ensure safe_text constructs valid, parameterized queries."""
    stmt = safe_text("SELECT count(*) FROM instruments WHERE symbol = :sym", sym="THYAO")
    result = db_session.execute(stmt).scalar()
    assert result == 1


def test_safe_text_rejects_string_formatting() -> None:
    """Ensure safe_text detects un-parameterized interpolation attempts."""
    with pytest.raises(UnsafeQueryError, match="suspicious string interpolation"):
        safe_text("SELECT * FROM users WHERE email = %s")

    with pytest.raises(UnsafeQueryError, match="suspicious string interpolation"):
        safe_text("SELECT * FROM users WHERE email = {user_email}")


# ==============================================================================
# 2. SQL Injection Payloads against API Endpoints
# ==============================================================================

SQLI_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "THYAO' UNION SELECT 1, 'admin', 'pass', 1, 1 --",
    "THYAO'; DROP TABLE instruments; --",
    "THYAO'; DROP TABLE users; --",
    "THYAO' AND (SELECT count(*) FROM users) > 0 --",
    "admin'--",
    "' OR 'a'='a",
    'THYAO" OR ""="',
    "THYAO'/*comment*/",
    "1'; WAITFOR DELAY '0:0:5'--",
]


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_symbols_search_sqli_resilience(
    client: TestClient, payload: str, db_session: DBSession
) -> None:
    """Ensure symbol search treats SQL injection strings strictly as literal search strings."""
    resp = client.get("/api/v1/market/symbols", params={"q": payload})
    # Must succeed without 500 error or syntax crash
    assert resp.status_code == 200
    data = resp.json()
    clean_p = payload.strip().lower()
    for item in data.get("symbols", []):
        assert clean_p in item["symbol"].lower() or clean_p in item["title"].lower()

    # Verify table integrity: instruments and users must still exist
    assert db_session.execute(select(Instrument)).scalar_one_or_none() is not None
    assert db_session.execute(select(User)).scalar_one_or_none() is not None


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_history_endpoint_sqli_resilience(
    client: TestClient, payload: str, db_session: DBSession
) -> None:
    """Ensure history endpoint symbol parameter is immune to SQL injection and safe."""
    resp = client.get(f"/api/v1/market/history/{payload}")
    # Parameter is treated literally: returns 200 (fallback candles) or 400, never 500
    assert resp.status_code in {200, 400, 404, 422}
    assert resp.status_code != 500

    # Ensure tables were NOT dropped by injected statements like DROP TABLE
    assert db_session.execute(select(Instrument)).scalar_one_or_none() is not None
    assert db_session.execute(select(User)).scalar_one_or_none() is not None


def test_auth_and_user_sqli_resilience(client: TestClient, db_session: DBSession) -> None:
    """Ensure auth endpoints do not allow authentication or data bypass via SQL injection."""
    # 1. Attempt magic-link request with SQL injection email
    sqli_email = "' OR '1'='1"
    resp_magic = client.post("/api/v1/auth/magic-link", json={"email": sqli_email})
    # Pydantic EmailStr validation will reject invalid email format (422)
    assert resp_magic.status_code == 422
    assert resp_magic.status_code != 500

    # 2. Attempt token verification with SQL injection token
    sqli_token = "' OR 1=1; DROP TABLE users; --"
    resp_verify = client.post("/api/v1/auth/verify", json={"token": sqli_token})
    # Unauthorized (401), not 500
    assert resp_verify.status_code == 401
    assert resp_verify.status_code != 500

    # Ensure users table is completely intact
    user_count = db_session.execute(select(User)).scalars().all()
    assert len(user_count) == 1


# ==============================================================================
# 3. Static AST Analysis for Zero Unparameterized SQL in src/
# ==============================================================================


def test_static_codebase_ast_no_sql_concatenation() -> None:
    """Statically parse all Python source files in src/ to guarantee no SQL concatenation.

    Checks that no `.execute(...)` call uses:
    - JoinedStr (f-strings)
    - BinOp with % or + (string formatting/concatenation)
    - Call to .format()
    - Raw string literals without ORM or safe_text
    """
    src_dir = Path(__file__).resolve().parent.parent / "src"
    py_files = list(src_dir.rglob("*.py"))
    assert len(py_files) > 0, "No python source files found in src/"

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Check for calls like db.execute(...) or session.execute(...) or conn.execute(...)
                is_execute_call = isinstance(func, ast.Attribute) and func.attr == "execute"
                if is_execute_call and node.args:
                    first_arg = node.args[0]

                    # 1. Disallow f-strings
                    assert not isinstance(first_arg, ast.JoinedStr), (
                        f"Unsafe f-string query in {py_file.name}:{node.lineno} -> "
                        f"{ast.unparse(node)}"
                    )

                    # 2. Disallow string concatenation or % formatting
                    if isinstance(first_arg, ast.BinOp):
                        assert not isinstance(first_arg.op, (ast.Add, ast.Mod)), (
                            f"Unsafe string formatting in {py_file.name}:{node.lineno} -> "
                            f"{ast.unparse(node)}"
                        )

                    # 3. Disallow .format(...) call as query argument
                    if isinstance(first_arg, ast.Call):
                        if (
                            isinstance(first_arg.func, ast.Attribute)
                            and first_arg.func.attr == "format"
                        ):
                            raise AssertionError(
                                f"Unsafe .format() SQL in {py_file.name}:{node.lineno} -> "
                                f"{ast.unparse(node)}"
                            )

                    # 4. Disallow raw string literals
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        raise AssertionError(
                            f"Raw string passed directly to execute() in "
                            f"{py_file.name}:{node.lineno}. Use ORM or safe_text: "
                            f"{ast.unparse(node)}"
                        )
