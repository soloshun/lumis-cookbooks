"""Vendor-side fault state for the external-provider simulators.

Vendors are *external* systems from GridCast's point of view: their faults are not deployments
in the estate, leave no trace in GridCast's change history, and cannot be fixed by restarting
anything GridCast owns. They are toggled through a token-protected admin API that only the
failure injector (`gridcastctl chaos`) uses.
"""

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

FaultMode = Literal["none", "stale", "outage", "slow", "unit_change", "schema_break", "gap"]


class FaultRequest(BaseModel):
    mode: FaultMode
    latency_ms: int = Field(default=0, ge=0, le=120_000)
    note: str | None = None
    since: datetime | None = Field(default=None, description="Backdate the fault start")
    zones: list[str] | None = Field(default=None, description="Limit a data fault to these zones")


class FaultState(BaseModel):
    mode: FaultMode = "none"
    latency_ms: int = 0
    since: datetime | None = None
    note: str | None = None
    zones: list[str] | None = None

    def affects(self, zone_id: str | None) -> bool:
        return self.zones is None or zone_id is None or zone_id in self.zones


class Faults:
    def __init__(self, supported: set[str]) -> None:
        self.supported = supported | {"none"}
        self._state = FaultState()
        self._lock = threading.Lock()

    @property
    def state(self) -> FaultState:
        with self._lock:
            return self._state.model_copy()

    def set(self, request: FaultRequest) -> FaultState:
        if request.mode not in self.supported:
            raise HTTPException(400, f"fault {request.mode!r} not supported by this vendor")
        with self._lock:
            self._state = FaultState(
                mode=request.mode,
                latency_ms=request.latency_ms,
                since=None if request.mode == "none" else (request.since or datetime.now(UTC)),
                note=request.note,
                zones=request.zones,
            )
            return self._state.model_copy()

    async def apply_transport_faults(self) -> JSONResponse | None:
        """Faults that affect every data request regardless of payload."""
        state = self.state
        if state.mode == "outage":
            return JSONResponse({"error": "service_unavailable"}, status_code=503)
        if state.mode == "slow" and state.latency_ms:
            await asyncio.sleep(state.latency_ms / 1000)
        return None


def admin_router(faults: Faults, token: str) -> APIRouter:
    def authorize(x_vendor_admin_token: str = Header(default="")) -> None:
        if not token or x_vendor_admin_token != token:
            raise HTTPException(401, "invalid vendor admin token")

    router = APIRouter(prefix="/admin", tags=["vendor-admin"], dependencies=[Depends(authorize)])

    @router.get("/faults")
    def get_fault() -> FaultState:
        return faults.state

    @router.post("/faults")
    def set_fault(request: FaultRequest) -> FaultState:
        state = faults.set(request)
        log.info("vendor fault changed", extra={"mode": state.mode})
        return state

    @router.delete("/faults")
    def clear_fault() -> FaultState:
        return faults.set(FaultRequest(mode="none"))

    return router
