"""Programmatic Alembic entry points (used by the `gridcast db` CLI and the migrate Job)."""

from importlib.resources import files

from alembic import command
from alembic.config import Config

from gridcast.config import DatabaseSettings


def alembic_config(settings: DatabaseSettings) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("gridcast.db").joinpath("migrations")))
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url().replace("%", "%%"))
    return config


def upgrade(settings: DatabaseSettings, revision: str = "head") -> None:
    command.upgrade(alembic_config(settings), revision)


def autogenerate(settings: DatabaseSettings, message: str) -> None:
    command.revision(alembic_config(settings), message=message, autogenerate=True)
