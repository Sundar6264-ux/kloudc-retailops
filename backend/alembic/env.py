"""Alembic environment for a STORE database (one store's schema).

Which database (first match):
1. `config.attributes["db_url"]` - set by app.migrate.upgrade_store_db();
2. `-x db_url=<url>`;
3. `-x store=<CODE>` - the store's database through the tenancy registry
   (multi mode, platform contract 5.1);
4. DATABASE_URL (settings) - single mode, today's behaviour.

SQLite runs the full chain 0001 -> head, as always.

PostgreSQL (platform contract 11.1) never replays 0001-0062 (written for
SQLite): an EMPTY database gets the frozen baseline (alembic/pg_baseline/
0062.sql + 0062_seed.json) and is stamped 0062 in the same transaction,
then the chain continues from 0063. Running a revision <= 0062 on
PostgreSQL, or downgrading below 0062, is refused. Store migrations on
PostgreSQL hold an advisory lock per database, so two upgraders (e.g.
provisioning and migrate-all) never run the chain at the same time.
New migrations (0063+) must run on both SQLite and PostgreSQL
(`batch_alter_table` is fine - a no-op wrapper on PostgreSQL;
`recreate="always"` and SQLite-only SQL are not).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db import Base
from app import models  # noqa: F401  (registers models on Base.metadata)
from app import migrate as store_migrate
from app.team import models as team_models  # noqa: F401
from app.team import chat_models as team_chat_models  # noqa: F401
from app.team import tasks_models as team_tasks_models  # noqa: F401

config = context.config


def _store_url() -> str:
    url = config.attributes.get("db_url")
    if url:
        return str(url)
    x = context.get_x_argument(as_dictionary=True)
    if x.get("db_url"):
        return x["db_url"]
    if x.get("store"):
        try:
            from app.tenancy import get_store, store_database_url
        except ImportError as exc:  # before the tenancy package (track B) is in
            raise SystemExit(f"-x store=... needs app.tenancy: {exc}") from None
        return store_database_url(get_store(x["store"].upper()))
    from app.config import get_settings

    return get_settings().database_url


_url = _store_url()
# configparser interpolation: a literal % (e.g. in a password) must be doubled.
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if url.startswith("postgresql"):
        raise SystemExit("Offline (--sql) migrations are not supported for PostgreSQL store databases.")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        is_pg = connection.dialect.name == "postgresql"
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            if is_pg:
                store_migrate.pg_lock_store_migrations(connection)
                store_migrate.pg_apply_baseline_if_empty(connection)
                store_migrate.pg_guard_revisions(context.get_context())
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
