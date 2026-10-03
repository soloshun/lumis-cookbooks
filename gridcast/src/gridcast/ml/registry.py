"""Model registry on PostgreSQL (`ml.models`, `ml.model_aliases`, `ml.model_events`).

Aliases (e.g. `production`) are mutable pointers to immutable versions. Every alias move is
recorded in `ml.model_events` with the actor and reason, giving an auditable change history
independent of Kubernetes deployments: promoting a model changes serving behaviour without
any pod being redeployed.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gridcast.db.schema import model_aliases, model_events, models

MODEL_NAME = "gridcast-load"


@dataclass(frozen=True)
class ModelVersion:
    model_name: str
    version: int
    algorithm: str
    profile: str
    artifact_uri: str
    feature_names: list[str]
    metrics: dict[str, Any]
    params: dict[str, Any]


def register(conn: Connection, *, model_name: str = MODEL_NAME, **fields: Any) -> int:
    current = conn.execute(
        select(func.coalesce(func.max(models.c.version), 0)).where(models.c.model_name == model_name)
    ).scalar_one()
    version = int(current) + 1
    conn.execute(insert(models).values(model_name=model_name, version=version, **fields))
    conn.execute(insert(model_events).values(
        model_name=model_name, version=version, event="registered",
        actor=fields.get("created_by", "unknown"),
        reason=f"{fields.get('profile')} model trained ({fields.get('algorithm')})",
    ))
    return version


def set_alias(
    conn: Connection, alias: str, version: int, *, actor: str, reason: str,
    model_name: str = MODEL_NAME,
) -> int | None:
    previous = conn.execute(select(model_aliases.c.version).where(
        model_aliases.c.model_name == model_name, model_aliases.c.alias == alias
    )).scalar()
    stmt = pg_insert(model_aliases).values(
        model_name=model_name, alias=alias, version=version, updated_by=actor
    )
    conn.execute(stmt.on_conflict_do_update(
        index_elements=["model_name", "alias"],
        set_={"version": version, "updated_by": actor, "updated_at": func.now()},
    ))
    conn.execute(insert(model_events).values(
        model_name=model_name, version=version, event="alias_set", alias=alias,
        previous_version=previous, actor=actor, reason=reason,
    ))
    return previous


def resolve(conn: Connection, alias: str = "production", model_name: str = MODEL_NAME
            ) -> tuple[ModelVersion, datetime] | None:
    row = conn.execute(
        select(models, model_aliases.c.updated_at)
        .join(model_aliases, (model_aliases.c.model_name == models.c.model_name)
              & (model_aliases.c.version == models.c.version))
        .where(model_aliases.c.model_name == model_name, model_aliases.c.alias == alias)
    ).mappings().first()
    if row is None:
        return None
    return ModelVersion(
        model_name=row["model_name"], version=row["version"], algorithm=row["algorithm"],
        profile=row["profile"], artifact_uri=row["artifact_uri"],
        feature_names=list(row["feature_names"]), metrics=dict(row["metrics"]),
        params=dict(row["params"]),
    ), row["updated_at"]


def get_version(conn: Connection, version: int, model_name: str = MODEL_NAME) -> dict | None:
    row = conn.execute(select(models).where(
        models.c.model_name == model_name, models.c.version == version
    )).mappings().first()
    return dict(row) if row else None


def list_versions(conn: Connection, model_name: str = MODEL_NAME) -> list[dict]:
    rows = conn.execute(select(models).where(models.c.model_name == model_name)
                        .order_by(models.c.version)).mappings().all()
    aliases = conn.execute(select(model_aliases).where(model_aliases.c.model_name == model_name)
                           ).mappings().all()
    by_version: dict[int, list[str]] = {}
    for a in aliases:
        by_version.setdefault(a["version"], []).append(a["alias"])
    return [{**dict(r), "aliases": by_version.get(r["version"], [])} for r in rows]
