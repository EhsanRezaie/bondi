import asyncio
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
from app.core.config import settings
from app.db.base import Base
import app.models

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def include_object(object, name, type_, reflected, compare_to):
    """فقط جداول schema خودمون رو include کن، جداول PostGIS رو نه"""
    if type_ == "table" and reflected and compare_to is None:
        return False
    return True


async def run_migrations_online():
    connectable = create_async_engine(settings.DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda conn: context.configure(
                connection=conn,
                target_metadata=target_metadata,
                include_object=include_object,
                compare_type=True,
                compare_server_default=False,
            )
        )
        async with connection.begin():
            await connection.run_sync(lambda _: context.run_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())