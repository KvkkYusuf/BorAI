"""Tests for Alembic database migrations."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    """Create an Alembic Config pointing to project root alembic.ini with a temp SQLite database."""
    project_root = Path(__file__).resolve().parent.parent
    ini_path = project_root / "alembic.ini"

    cfg = Config(str(ini_path))
    # Point script_location to absolute path to guarantee discovery
    cfg.set_main_option("script_location", str(project_root / "migrations"))

    db_path = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_path}"
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_alembic_upgrade_and_downgrade(alembic_config: Config) -> None:
    """Test full schema upgrade to head and downgrade to base."""
    db_url = alembic_config.get_main_option("sqlalchemy.url")
    assert db_url is not None
    engine = create_engine(db_url)

    # 1. Upgrade schema to head
    command.upgrade(alembic_config, "head")

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    expected_tables = {
        "users",
        "sessions",
        "instruments",
        "market_bars",
        "corporate_actions",
        "watchlists",
        "watchlist_items",
        "alert_rules",
        "alert_events",
        "model_versions",
        "forecasts",
        "audit_events",
        "user_preferences",
        "passkey_credentials",
        "alembic_version",
    }
    assert expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}"

    # Verify key columns on users
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    expected_user_cols = {
        "id",
        "email",
        "hashed_password",
        "is_active",
        "is_verified",
        "role",
        "mfa_enabled",
        "mfa_secret",
        "mfa_recovery_codes",
    }
    assert expected_user_cols.issubset(user_columns)

    # 2. Downgrade schema to base
    command.downgrade(alembic_config, "base")

    inspector_after_downgrade = inspect(engine)
    remaining_tables = set(inspector_after_downgrade.get_table_names())
    # After full downgrade, all custom business tables should be dropped
    business_tables = expected_tables - {"alembic_version"}
    assert business_tables.isdisjoint(remaining_tables)


def test_alembic_heads_check(alembic_config: Config) -> None:
    """Verify there are no multiple conflicting heads or broken branches."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 migration head, got: {heads}"
