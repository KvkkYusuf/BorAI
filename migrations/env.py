"""Alembic migration environment configuration."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure src directory is in sys.path
src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from alembic import context
from sqlalchemy import engine_from_config, pool

from stock_analysis.config import get_settings
from stock_analysis.db import Base

# Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate support
target_metadata = Base.metadata


def get_url() -> str:
    """Retrieve database URL, preferring environment variable or application Settings."""
    custom_url = os.environ.get("DATABASE_URL")
    if custom_url:
        url = custom_url
    else:
        configured_url = config.get_main_option("sqlalchemy.url")
        if configured_url and "driver://user:pass" not in configured_url:
            url = configured_url
        else:
            url = get_settings().database_url

    # Convert asyncpg scheme to standard postgresql driver for sync alembic execution
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
