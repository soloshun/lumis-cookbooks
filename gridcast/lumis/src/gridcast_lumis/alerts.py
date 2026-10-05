"""Incident intake: firing Prometheus alerts -> a bounded Lumis `Incident`.

This is the piece a production deployment replaces with an Alertmanager webhook. Every GridCast
alert carries an `entity` label (the canonical graph ID where the symptom is observed); the
incident's affected entities are exactly those labels. Nothing here knows about injected faults.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from lumis_sdk.core import Incident


@dataclass(frozen=True)
class FiringAlert:
    name: str
    entity: str
    summary: str
    active_at: datetime
    severity: str


def firing_alerts(prometheus: str = "http://localhost:9090") -> list[FiringAlert]:
    response = httpx.get(f"{prometheus}/api/v1/alerts", timeout=10)
    response.raise_for_status()
    alerts = []
    for alert in response.json()["data"]["alerts"]:
        labels = alert.get("labels", {})
        if alert.get("state") != "firing" or "entity" not in labels:
            continue
        alerts.append(FiringAlert(
            name=labels.get("alertname", "unknown"),
            entity=labels["entity"],
            summary=alert.get("annotations", {}).get("summary", labels.get("alertname", "")),
            active_at=datetime.fromisoformat(alert["activeAt"].replace("Z", "+00:00")),
            severity=labels.get("severity", "ticket"),
        ))
    return sorted(alerts, key=lambda a: (a.active_at, a.name))


def incident_from_alerts(
    alerts: list[FiringAlert],
    *,
    lookback: timedelta = timedelta(minutes=30),
    now: datetime | None = None,
    prefix: str = "gridcast",
) -> Incident | None:
    """Group all currently firing alerts into one incident (one estate, one page)."""
    if not alerts:
        return None
    # Whole seconds: Prometheus echoes the query time rounded to milliseconds; a sub-ms end time
    # can round *past* the window and the SDK then (correctly) rejects every observation.
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    entities = sorted({a.entity for a in alerts})
    symptoms = sorted({f"{a.name}: {a.summary}" for a in alerts})
    digest = hashlib.sha1("|".join(entities).encode()).hexdigest()[:6]
    return Incident(
        id=f"{prefix}-{now:%Y%m%dT%H%M%SZ}-{digest}",
        affected_entities=tuple(entities),
        symptoms=tuple(symptoms),
        started_at=(min(a.active_at for a in alerts) - lookback).replace(microsecond=0),
        ended_at=now,
    )


def manual_incident(entities: list[str], symptom: str, lookback: timedelta) -> Incident:
    now = datetime.now(UTC).replace(microsecond=0)
    return Incident(
        id=f"manual-{now:%Y%m%dT%H%M%SZ}",
        affected_entities=tuple(sorted(set(entities))),
        symptoms=(symptom,),
        started_at=now - lookback,
        ended_at=now,
    )
