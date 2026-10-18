"""Alembic env for the PLATFORM database (contract 11.2; owner C).

    cd backend && PLATFORM_DATABASE_URL=postgresql+psycopg://... \
        .venv/bin/alembic -n platform upgrade head

Separate from the store schema (``backend/alembic``): its own metadata
(``app.platform.models.PlatformBase``) and its own ``alembic_version`` table
inside the platform database. The URL comes from, in order: ``-x db_url=...``,
the ``sqlalchemy.url`` the caller set (``app.platform.db.upgrade_platform_db``),
then ``PLATFORM_DATABASE_URL``.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.platform.models import PlatformBase

config = context.config


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    if x.get("db_url"):
        return x["db_url"]
    url = config.attributes.get("platform_url") or config.get_main_option("sqlalchemy.url") or ""
    if url and not url.startswith("driver://"):
        return url
    from app.platform.db import platform_url

    return platform_url()


if config.config_file_name is not None and not config.attributes.get("platform_url"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = PlatformBase.metadata


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from app.platform.db import make_engine

    connectable = make_engine(_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
