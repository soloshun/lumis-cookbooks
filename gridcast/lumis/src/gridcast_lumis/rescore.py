"""Post-hoc re-scoring of a finished experiment from its raw artefacts (no re-runs).

Two corrections, both reported *next to* the original scores and labelled as made after
seeing results:

1. Entity matching through the prepared graph: a Kubernetes resource node that `hosts` the
   expected logical service (e.g. `k8s:gridcast:deployment:feature-service`) counts as that
   service. The original scorer compared the first causal-path element literally.
2. Mechanism: whether the top-ranked candidate (and any candidate) states the ground-truth
   mechanism, from `analysis/mechanism-labels.json` (post-hoc labels with a rationale each).

Candidate ranking is unchanged from experiment.metrics_for.
"""

import csv
import json
import statistics
from pathlib import Path

from gridcast_lumis.scoring import graph_entity

ORDER = {"supported": 0, "unresolved": 2, "contradicted": 3}
SYSTEMS = ("rules", "single_pass", "lumis")


def _hosts(raw: Path) -> dict[str, str]:
    graph = json.loads((raw / "graph.json").read_text())["graph"]
    return {edge["source"]: edge["target"] for edge in graph["relationships"]
            if edge["kind"] == "hosts"}


def _report_paths(report: dict) -> list[list[str]]:
    ranked = [(ORDER[a["state"]], a["hypothesis"]["causal_path"]) for a in report["assessments"]]
    known = {a["hypothesis"]["id"] for a in report["assessments"]}
    ranked += [(1, f["assessment"]["hypothesis"]["causal_path"]) for f in report["findings"]
               if f["status"] == "match" and f["assessment"]["hypothesis"]["id"] not in known]
    return [path for _, path in sorted(ranked, key=lambda item: item[0])]


def _single_pass_paths(candidates: list[dict]) -> list[list[str]]:
    paths = [[x for x in (c["hypothesis"].get("causal_path") or []) if isinstance(x, str) and ":" in x]
             for c in candidates]
    return [p for p in paths if p]


def rescore(folder: Path) -> list[dict]:
    labels = json.loads((folder / "analysis" / "mechanism-labels.json").read_text())
    rows = [json.loads(x) for x in (folder / "results.jsonl").read_text().splitlines() if x.strip()]
    out = []
    for row in rows:
        sc, system, k = row["scenario"], row["system"], row["repeat"]
        raw = folder / "raw" / sc
        hosts = _hosts(raw)
        expected = graph_entity(json.loads((raw / "ground_truth.json").read_text())
                                ["ground_truth"]["root_cause_entity"])
        run = raw / f"{system}-r{k}"
        if system == "single_pass":
            paths = _single_pass_paths(json.loads((run / "candidates.json").read_text()))
            mech = labels["runs"][sc]["single_pass"][str(k)]
        else:
            report = json.loads((run / "report.json").read_text())
            paths = _report_paths(report)
            if system == "lumis":
                mech = labels["runs"][sc]["lumis"][str(k)]
            else:  # rules: the matched signature's mechanism
                matched = row.get("matched_signatures") or []
                hit = any(labels["rules_signatures"].get(s, {}).get(sc, False) for s in matched)
                mech = {"top1": hit, "any": hit, "why": ", ".join(matched) or "no match"}
        firsts = [hosts.get(p[0], p[0]) for p in paths]
        top1 = bool(firsts) and firsts[0] == expected
        concluded = bool(row.get("concluded"))
        out.append({
            "scenario": sc, "system": system, "repeat": k,
            "top1_original": row["top1"], "top1_corrected": top1,
            "top3_corrected": expected in firsts[:3],
            "mechanism_top1": mech["top1"], "mechanism_any": mech["any"],
            "concluded": concluded,
            "wrong_conclusion_entity": concluded and not top1,
            "wrong_conclusion_mechanism": concluded and not mech["top1"],
            "seconds": row["seconds"], "cost_usd": row.get("cost_usd") or 0,
            "mechanism_note": mech.get("why", ""),
        })
    return out


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def aggregate(rows: list[dict], exclude: tuple[str, ...] = ()) -> dict:
    result = {}
    for system in SYSTEMS:
        rs = [r for r in rows if r["system"] == system and r["scenario"] not in exclude]
        if not rs:
            continue
        result[system] = {
            "runs": len(rs),
            "top1_original": _rate([r["top1_original"] for r in rs]),
            "top1_corrected": _rate([r["top1_corrected"] for r in rs]),
            "top3_corrected": _rate([r["top3_corrected"] for r in rs]),
            "mechanism_top1": _rate([r["mechanism_top1"] for r in rs]),
            "mechanism_any": _rate([r["mechanism_any"] for r in rs]),
            "concluded": sum(r["concluded"] for r in rs),
            "wrong_conclusions_entity": sum(r["wrong_conclusion_entity"] for r in rs),
            "wrong_conclusions_mechanism": sum(r["wrong_conclusion_mechanism"] for r in rs),
            "seconds_median": round(statistics.median(r["seconds"] for r in rs), 1),
            "cost_usd_total": round(sum(r["cost_usd"] for r in rs), 4),
        }
    return result


def write(folder: Path) -> Path:
    rows = rescore(folder)
    target = folder / "analysis"
    with (target / "rescored.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metrics = {"all_scenarios": aggregate(rows),
               "excluding_B": aggregate(rows, exclude=("B",)),
               "per_scenario": {sc: aggregate([r for r in rows if r["scenario"] == sc])
                                for sc in dict.fromkeys(r["scenario"] for r in rows)}}
    (target / "metrics-rescored.json").write_text(json.dumps(metrics, indent=1))
    return target
