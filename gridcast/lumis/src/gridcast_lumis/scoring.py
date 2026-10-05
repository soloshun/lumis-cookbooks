"""Score a finished Lumis report against GridCast's hidden ground truth.

Ground truth is read only *after* the report exists, by the harness, from
`gridcast/.gridcast/chaos/runs/`. Lumis itself never sees it.
"""

import json
from pathlib import Path

from lumis_sdk.investigation.contracts import IncidentReport

# What each deterministic signature diagnoses, in ground-truth vocabulary. A matched
# nonterminal signature is a *lead* (it narrowed the search), not a diagnosis.
SIGNATURES: dict[str, tuple[str, str | None]] = {
    "planning-api-scaled-to-zero": ("service:gridcast:planning-api", "availability.scaled_to_zero"),
    "feature-query-amplification": ("service:gridcast:feature-service",
                                    "bad_deployment.query_amplification"),
    "feature-builds-failing": ("service:gridcast:feature-service", None),
    "feature-service-db-auth-failing": ("service:gridcast:feature-service",
                                        "configuration.stale_credential"),
    "forecast-service-oom-killed": ("service:gridcast:forecast-service", "resources.memory_limit"),
    "forecast-model-slowdown": ("service:gridcast:forecast-service", "ml.model_change_latency"),
    "demand-feed-rejected": ("service:gridcast:grid-telemetry", "data_contract.schema_drift"),
    "demand-values-out-of-range": ("service:gridcast:grid-telemetry", "data_quality.unit_change"),
    "weather-feed-failing": ("service:gridcast:weather-vendor-wx-primary", "dependency.outage"),
    "weather-feed-repeating": ("service:gridcast:weather-vendor-wx-primary",
                               "data_quality.stale_upstream"),
}


def graph_entity(ground_truth_entity: str) -> str:
    """Map ground-truth IDs (deployment:/vendor:/model:) onto Lumis graph IDs."""
    kind, _, rest = ground_truth_entity.partition(":")
    if kind == "deployment":
        return f"service:gridcast:{rest.split('/')[-1]}"
    if kind == "vendor":
        return {"wx-primary": "service:gridcast:weather-vendor-wx-primary",
                "wx-secondary": "service:gridcast:weather-vendor-wx-secondary",
                "grid-telemetry": "service:gridcast:grid-telemetry"}[rest]
    if kind == "model":
        return "service:gridcast:forecast-service"
    return ground_truth_entity


def latest_ground_truth(gridcast_dir: Path) -> dict | None:
    runs = sorted((gridcast_dir / ".gridcast" / "chaos" / "runs").glob("*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


def score(report: IncidentReport, truth: dict | None, hosts: dict[str, str] | None = None) -> dict:
    """`hosts` maps a resource node to the logical service it hosts (graph `hosts` edges), so a
    conclusion rooted at `k8s:gridcast:deployment:x` counts as `service:gridcast:x`."""
    hosts = hosts or {}
    matched = [f.rule_id for f in report.findings if f.status == "match"]
    unknown = [f.rule_id for f in report.findings if f.status == "unknown"]
    supported = [a for a in report.assessments if a.state == "supported"]
    predicted = [hosts.get(a.hypothesis.causal_path[0], a.hypothesis.causal_path[0]) for a in supported]
    leads = [SIGNATURES[r][0] for r in matched if r in SIGNATURES]
    concluded = report.conclusion == "supported_diagnosis"
    result = {
        "route": report.route,
        "conclusion": report.conclusion,
        "stop_reason": report.stop_reason,
        "matched_signatures": matched,
        "unknown_signatures": unknown,
        "supported_hypotheses": [a.hypothesis.statement for a in supported],
        "predicted_entities": predicted,
        "evidence_queries": report.metrics.evidence_queries,
        "model_requests": report.metrics.model_requests,
        "input_tokens": report.metrics.input_tokens,
        "output_tokens": report.metrics.output_tokens,
    }
    if truth is None:
        return result | {"scored": False}
    gt = truth["ground_truth"]
    expected = graph_entity(gt["root_cause_entity"])
    category_leads = [SIGNATURES[r][1] for r in matched if r in SIGNATURES]
    correct = bool(predicted) and predicted[0] == expected
    if concluded:
        outcome = "correct_diagnosis" if correct else "wrong_diagnosis"
    elif gt["category"] in category_leads:
        outcome = "escalated_with_correct_lead"
    elif gt.get("abstain_ok"):
        outcome = "correct_abstention"
    elif expected in leads:
        outcome = "escalated_with_entity_lead"
    else:
        outcome = "escalated_without_lead"
    return result | {
        "scored": True,
        "scenario": truth["scenario"]["id"],
        "expected_entity": expected,
        "expected_category": gt["category"],
        "abstain_ok": gt.get("abstain_ok", False),
        "correct_entity": correct,
        "entity_in_leads": expected in leads or expected in predicted,
        "outcome": outcome,
    }
