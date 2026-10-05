"""SQLAlchemy engine factory with production-style pooling and per-session timeouts."""

import logging
import socket

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

    @event.listens_for(engine, "do_connect")
    def _ipv4_only(dialect, connection_record, cargs, cparams):  # noqa: ANN001
        # The kind network publishes AAAA records with no IPv6 route. libpq tries every address
        # and reports the last failure first, so a real error (e.g. "password authentication
        # failed") was buried under "Network is unreachable". Connect to the IPv4 address only,
        # re-resolved for every new connection.
        host = cparams.get("host")
        if host and "hostaddr" not in cparams:
            try:
                infos = socket.getaddrinfo(host, cparams.get("port") or 5432, socket.AF_INET,
                                           socket.SOCK_STREAM)
                cparams["hostaddr"] = infos[0][4][0]
            except OSError:
                pass

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, connection_record):  # noqa: ANN001
        log.debug("database connection opened", extra={"db_user": settings.user})

    telemetry.instrument_engine(engine)
    return engine
