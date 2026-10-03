"""SQLAlchemy engine factory with production-style pooling and per-session timeouts."""

import logging

from sqlalchemy import Engine, create_engine, event

from gridcast import telemetry
from gridcast.config import DatabaseSettings, database_settings

log = logging.getLogger(__name__)


def make_engine(settings: DatabaseSettings | None = None, *, application_name: str) -> Engine:
    settings = settings or database_settings()
    engine = create_engine(
        settings.sqlalchemy_url(),
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        pool_recycle=settings.pool_recycle_seconds,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": settings.connect_timeout_seconds,
            "application_name": settings.application_name or application_name,
            "options": f"-c statement_timeout={settings.statement_timeout_ms}",
        },
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, connection_record):  # noqa: ANN001
        log.debug("database connection opened", extra={"db_user": settings.user})

    telemetry.instrument_engine(engine)
    return engine
