"""Alembic environment. Migrations run as the schema owner (gridcast_owner)."""

from alembic import context
from sqlalchemy import create_engine, pool

from gridcast.db.schema import metadata

config = context.config


def run_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not configured")
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            include_schemas=True,
            version_table_schema="public",
            compare_type=True,
            include_name=lambda name, type_, parent: (
                type_ != "schema" or name in {"ref", "raw", "features", "ml", "quality", "planning"}
            ),
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
