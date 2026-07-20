"""Alembic migration environment.

Uses the application settings for the database URL and the ORM's
``Base.metadata`` as the autogenerate target, so migrations stay in lock-step
with :mod:`aetherseed.core.storage.models`.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from aetherseed.config import get_settings
from aetherseed.core.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime database URL from settings (never hardcode secrets).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTERs
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
