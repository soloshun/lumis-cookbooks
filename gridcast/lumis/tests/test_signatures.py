"""Offline checks of lumis.yaml: schema validity and what each signature concludes.

Observations are synthetic fixtures shaped like the live Prometheus answers measured on the
estate; no cluster, Prometheus or model is needed.
"""

from datetime import UTC, datetime, timedelta

import pytest
from lumis_sdk.checks import evaluate_checks, sufficient_finding
from lumis_sdk.core import Evidence, Incident, IncidentContext
from lumis_sdk.runtime.project import load_project

from gridcast_lumis.runner import PROJECT_FILE
from gridcast_lumis.scoring import SIGNATURES, graph_entity

END = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
HEALTHY = {
    "planning-desired-replicas": 1, "planning-available-replicas": 1,
    "operator-plan-fetch-transport-errors": 0, "pipeline-failed-runs": 0,
    "feature-sql-per-build": 4, "feature-build-p95": 0.05, "feature-failed-builds": 0,
    "postgres-rows-scanned": 6000, "forecast-oom-killed": 0, "forecast-memory-ratio": 0.3,
    "forecast-restarts": 0, "forecast-inference-p95": 0.1, "forecast-inference-max": 0.1,
    "forecast-model-reloads": 0, "ingestion-demand-errors": 0,
    "ingestion-weather-errors": 0, "demand-range-failures": 0, "weather-variability-warnings": 0,
    # Prefect failed_count is a real 0 when runs exist; the SQL count returns a real 0.
    # Loki counts are *absent* when nothing matches (native connector semantics).
    "prefect-failed-flow-runs": 0, "model-production-alias-changes": 0,
}
SCENARIOS = {
    "healthy": {},
    "J": {"planning-desired-replicas": 0, "planning-available-replicas": 0,
          "operator-plan-fetch-transport-errors": 3, "pipeline-failed-runs": 2},
    "A": {"feature-sql-per-build": 2499, "feature-build-p95": 5.6, "postgres-rows-scanned": 120000},
    "D": {"forecast-oom-killed": 1, "forecast-memory-ratio": 1.2, "forecast-restarts": 3},
    "E": {"forecast-inference-p95": 8.0, "forecast-inference-max": 9.5, "forecast-model-reloads": 1,
          "model-production-alias-changes": 1},
    "G": {"ingestion-demand-errors": 5, "ingestion-contract-violations": 5},
    "C": {"feature-failed-builds": 4, "feature-auth-failures": 12, "pipeline-failed-runs": 2,
          "prefect-failed-flow-runs": 2},
    "I": {"ingestion-weather-errors": 6, "ingestion-weather-vendor-503": 24},
}
EXPECTED = {  # scenario -> (matched signatures, terminal sufficient signature or None)
    "healthy": (set(), None),
    "J": ({"planning-api-scaled-to-zero"}, "planning-api-scaled-to-zero"),
    "A": ({"feature-query-amplification"}, None),
    "D": ({"forecast-service-oom-killed"}, None),
    "E": ({"forecast-model-slowdown"}, None),
    "G": ({"demand-feed-rejected"}, None),
    "C": ({"feature-builds-failing", "feature-service-db-auth-failing"}, None),
    "I": ({"weather-feed-failing"}, None),
}


@pytest.fixture(scope="module")
def project():
    return load_project(PROJECT_FILE)


def context(project, values: dict, affected: tuple[str, ...]) -> IncidentContext:
    catalog = {q.id: q for q in project.queries}
    evidence = tuple(
        Evidence(id=f"prometheus:{qid}", query_id=qid, entity_id=catalog[qid].entity_id,
                 key=catalog[qid].key, value=float(v), observed_at=END, source="prometheus",
                 retrieval_method="fixture")
        for qid, v in values.items()
    )
    incident = Incident(id="fixture", affected_entities=affected, symptoms=("fixture",),
                        started_at=END - timedelta(minutes=30), ended_at=END)
    return IncidentContext(incident=incident, graph=project.graph, queries=project.queries,
                           evidence=evidence)


def test_healthy_estate_without_log_facts_is_fully_contradicted(project):
    """Absent Loki facts must not leave any signature unknown on a healthy estate (else J could
    never be concluded deterministically): each is falsified by a Prometheus observable."""
    ctx = context(project, HEALTHY | SCENARIOS["J"], ("service:gridcast:grid-operator",))
    findings = {f.rule_id: f.status for f in evaluate_checks(project.checks, ctx)}
    assert set(findings.values()) == {"no_match", "match"}
    assert [r for r, s in findings.items() if s == "match"] == ["planning-api-scaled-to-zero"]


def test_project_validates(project):
    assert project.sources.kubernetes.namespace == "gridcast"
    assert {r.id for r in project.checks} == set(SIGNATURES)
    providers = [q.provider for q in project.queries]
    assert providers.count("loki") == 5 and providers.count("tempo") == 1
    assert providers.count("prefect") == 2 and providers.count("sql") == 3
    assert providers.count("changes") == 5 and "snapshot" not in providers
    assert project.sources.sql.enabled and project.sources.changes.enabled
    assert [r.id for r in project.checks if r.terminal] == ["planning-api-scaled-to-zero"]


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_signature_outcomes(project, scenario):
    ctx = context(project, HEALTHY | SCENARIOS[scenario],
                  ("service:gridcast:grid-operator", "service:gridcast:forecast-pipeline"))
    findings = evaluate_checks(project.checks, ctx)
    matched = {f.rule_id for f in findings if f.status == "match"}
    assert not [f for f in findings if f.status == "unknown"]
    expected_matches, terminal = EXPECTED[scenario]
    assert matched == expected_matches
    sufficient = sufficient_finding(project.checks, findings, ctx)
    assert (sufficient.rule_id if sufficient else None) == terminal


def test_unreadable_external_source_leaves_signature_unknown(project):
    """C with no auth-failure log facts (Loki empty or unreachable): the credential signature
    cannot be decided, so it is `unknown`, while Prometheus-only signatures still evaluate."""
    values = {k: v for k, v in (HEALTHY | SCENARIOS["C"]).items() if k != "feature-auth-failures"}
    ctx = context(project, values, ("service:gridcast:forecast-pipeline",))
    findings = {f.rule_id: f.status for f in evaluate_checks(project.checks, ctx)}
    assert findings["feature-service-db-auth-failing"] == "unknown"
    assert findings["feature-builds-failing"] == "match"
    assert sufficient_finding(project.checks, evaluate_checks(project.checks, ctx), ctx) is None


def test_missing_evidence_is_never_a_diagnosis(project):
    partial = {k: v for k, v in (HEALTHY | SCENARIOS["J"]).items()
               if k != "operator-plan-fetch-transport-errors"}
    ctx = context(project, partial, ("service:gridcast:grid-operator",))
    findings = evaluate_checks(project.checks, ctx)
    assert sufficient_finding(project.checks, findings, ctx) is None


def test_ground_truth_ids_map_to_graph():
    assert graph_entity("deployment:gridcast/planning-api") == "service:gridcast:planning-api"
    assert graph_entity("vendor:wx-primary") == "service:gridcast:weather-vendor-wx-primary"
    assert graph_entity("model:gridcast-load@production") == "service:gridcast:forecast-service"
