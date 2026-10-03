"""TEMPORARY SQL evidence until the SDK ships a SQL provider.

Loki, Tempo and Prefect now use the SDK's native connectors (lumis-sdk c757a74). SQL evidence
is still unimplemented in the SDK, so this module is the documented workaround ("reviewed
external normalized observations"): the harness runs the operator-registered, read-only query
itself and hands Lumis plain `Evidence` through the built-in `snapshot` provider.

* lumis.yaml: `provider: snapshot`, `parameters: {source: sql, sql: ...}` (named parameters
  `%(started_at)s` / `%(ended_at)s` are the incident window).
* Connects as `gridcast_readonly` in a read-only transaction with a 5 s statement timeout.
* An unreadable database yields no observation (the signature stays `unknown`), never a zero.

Removal: switch to the native provider when it exists, keep the id/key, delete this module and
its call in runner.py.
"""

from pathlib import Path

from lumis_sdk.core import Evidence, Incident
from lumis_sdk.runtime.project import OperationalProject

PG = {"host": "localhost", "port": 5432, "dbname": "gridcast", "user": "gridcast_readonly"}


def _readonly_password(gridcast_dir: Path) -> str:
    env = gridcast_dir / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("GRIDCAST_READONLY_PASSWORD="):
            return line.split("=", 1)[1].strip()
    return "readonly-local"


def sql_scalar(sql: str, incident: Incident, gridcast_dir: Path) -> float:
    import psycopg

    with psycopg.connect(**PG, password=_readonly_password(gridcast_dir), connect_timeout=5,
                         options="-c default_transaction_read_only=on -c statement_timeout=5000"
                         ) as conn, conn.cursor() as cur:
        cur.execute(sql, {"started_at": incident.started_at, "ended_at": incident.ended_at})
        rows = cur.fetchall()
    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError("SQL must return exactly one scalar")
    return float(rows[0][0])


def collect(project: OperationalProject, incident: Incident, gridcast_dir: Path
            ) -> tuple[tuple[Evidence, ...], dict[str, str]]:
    """Evidence for every `snapshot` query with `source: sql`; plus per-query errors."""
    evidence: list[Evidence] = []
    errors: dict[str, str] = {}
    for query in project.queries:
        if query.provider != "snapshot" or query.parameters.get("source") != "sql":
            continue
        try:
            value = sql_scalar(query.parameters["sql"], incident, gridcast_dir)
        except Exception as exc:  # unreadable source => no observation, not a zero
            errors[query.id] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        evidence.append(Evidence(
            id=f"sql:{query.id}", query_id=query.id, entity_id=query.entity_id, key=query.key,
            value=value, observed_at=incident.ended_at, source="sql",
            retrieval_method="gridcast-lumis external normalizer (sql)",
        ))
    return tuple(evidence), errors
