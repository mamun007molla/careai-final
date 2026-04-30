"""Alembic env — uses app settings for DATABASE_URL and Base.metadata for models.

Two important fixes baked in:

1. Passwords containing `%` would crash configparser when set via
   `config.set_main_option("sqlalchemy.url", url)`. We bypass the .ini
   layer entirely and pass the URL directly to `create_engine`.

2. If a previous failed migration left enum types in the database (without
   the corresponding tables), Alembic would crash with "type already
   exists". The migration files now use `create_type=False` and explicitly
   pre-create enums with `checkfirst=True` to be idempotent.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import settings
from app.core.database import Base
from app import models  # noqa: F401  (registers all tables on Base.metadata)


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# IMPORTANT: We do NOT call `config.set_main_option("sqlalchemy.url", ...)`.
# That goes through configparser, which treats `%` as variable interpolation
# and crashes on URL-encoded passwords like `Manha3321%40`. Instead, we pass
# the raw URL directly to create_engine below.


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Build engine directly from settings — bypasses configparser entirely
    connectable = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
