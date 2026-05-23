import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Allow importing backend modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.database import Base
from backend.models import Dataset, Image, Tag, BackgroundJob, ThresholdSettings, DatasetBranch, DatasetVersion, VersionImageState  # noqa: F401 - needed for autogenerate

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    from backend.config import settings
    # Alembic needs a sync URL (no +aiosqlite)
    return str(settings.database_url).replace("+aiosqlite", "")


def run_migrations_offline() -> None:
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
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
