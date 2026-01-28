"""
Alembic Environment Configuration for Seedarr v2.0

This module configures the Alembic migration environment, including:
- Database connection from environment variables or alembic.ini
- Autogenerate support with SQLAlchemy models
- Offline and online migration modes
"""
from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the parent directories to the path so we can import our models
# This handles both running from project root and from backend directory
backend_dir = Path(__file__).resolve().parent.parent
project_root = backend_dir.parent

# Add both paths to support different execution contexts
sys.path.insert(0, str(project_root))  # For imports like 'backend.app.models...'
sys.path.insert(0, str(backend_dir))   # For imports like 'app.models...'

# Import all models for autogenerate support
# Try backend.app first (when running from project root), then app (when running from backend)
try:
    from backend.app.models.base import Base
    from backend.app.models.file_entry import FileEntry
    from backend.app.models.tmdb_cache import TMDBCache
    from backend.app.models.tags import Tags
except ModuleNotFoundError:
    from app.models.base import Base
    from app.models.file_entry import FileEntry
    from app.models.tmdb_cache import TMDBCache
    from app.models.tags import Tags

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url():
    """
    Get database URL from environment variable or alembic.ini.

    Priority:
        1. DATABASE_URL environment variable
        2. sqlalchemy.url from alembic.ini

    Returns:
        Database connection URL string
    """
    return os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
