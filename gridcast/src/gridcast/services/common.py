"""Shared FastAPI scaffolding: logging, telemetry, health probes and release metadata."""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gridcast import telemetry
from gridcast.config import BaseServiceSettings
from gridcast.logs import configure_logging
from gridcast.release import Release, load_release

log = logging.getLogger(__name__)

ReadinessCheck = Callable[[], tuple[bool, str]]


def bootstrap(service: str, settings: BaseServiceSettings) -> Release:
    """Configure logging and telemetry for a process; returns its release manifest."""
    release = load_release(settings.release_file, service)
    configure_logging(service, release.version, settings.log_level, settings.log_format)
    telemetry.setup_telemetry(service, release.version, settings.environment)
    return release


def create_app(
    service: str,
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
    readiness: ReadinessCheck | None = None,
    description: str = "",
) -> FastAPI:
    settings = BaseServiceSettings()
    release = bootstrap(service, settings)
    app = FastAPI(
        title=service, version=release.version, description=description, lifespan=lifespan
    )
    app.state.release = release
    app.state.settings = settings

    @app.get("/healthz", tags=["ops"], summary="Liveness probe")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], summary="Readiness probe")
    def readyz() -> JSONResponse:
        if readiness is None:
            return JSONResponse({"status": "ready"})
        ok, detail = readiness()
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "detail": detail},
            status_code=200 if ok else 503,
        )

    @app.get("/release", tags=["ops"], summary="Release manifest of the running build")
    def release_info() -> Release:
        return release

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled error",
            extra={"path": request.url.path, "error_type": type(exc).__name__,
                   "error": str(exc).splitlines()[0][:300] if str(exc) else ""},
        )
        return JSONResponse(
            {"error": type(exc).__name__, "detail": str(exc)[:500]}, status_code=500
        )

    telemetry.instrument_fastapi(app)
    return app


def serve(app: FastAPI) -> None:
    settings: BaseServiceSettings = app.state.settings
    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=10,
    )
