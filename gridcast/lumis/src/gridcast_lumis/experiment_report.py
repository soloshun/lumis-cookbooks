"""Aggregate an experiment folder into metrics.json, results.csv, summary.md and charts/."""

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

SYSTEMS = ("rules", "single_pass", "lumis")
LABEL = {"rules": "Rule tier only", "single_pass": "Single-pass LLM", "lumis": "Lumis (triage + agent)"}


def _rows(folder: Path) -> list[dict]:
    path = folder / "results.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def aggregate(rows: list[dict]) -> dict:
    out: dict = {}
    for system in SYSTEMS:
        rs = [r for r in rows if r["system"] == system]
        if not rs:
            continue
        ok = [r for r in rs if not r.get("error")]
        hyps = sum(r.get("hypotheses", 0) for r in ok)
        out[system] = {
            "runs": len(rs), "errors": len(rs) - len(ok),
            "agent_errors": sum(1 for r in rs if r.get("agent_error")),
            "scenarios": sorted({r["scenario"] for r in rs}),
            # Reasoning
            "top1_recall": _rate([r["top1"] for r in ok]),
            "top3_recall": _rate([r["top3"] for r in ok]),
            "top5_recall": _rate([r["top5"] for r in ok]),
            "path_correct": _rate([r["path_score"] == "correct" for r in ok]),
            "path_partial_or_better": _rate([r["path_score"] in {"correct", "partial"} for r in ok]),
            "hypotheses_total": hyps,
            "unsupported_hypothesis_rate": round(sum(r.get("unsupported", 0) for r in ok) / hyps, 3)
            if hyps else None,
            # Efficiency
            "evidence_queries_mean": round(statistics.mean(r.get("evidence_queries", 0) for r in ok), 1)
            if ok else None,
            "seconds_median": round(statistics.median(r["seconds"] for r in rs), 2),
            "seconds_max": round(max(r["seconds"] for r in rs), 2),
            "model_requests_mean": round(statistics.mean(r.get("model_requests", 0) for r in ok), 1)
            if ok else None,
            "input_tokens_total": sum(r.get("input_tokens", 0) or 0 for r in ok),
            "output_tokens_total": sum(r.get("output_tokens", 0) or 0 for r in ok),
            "cost_usd_total": round(sum(r.get("cost_usd", 0) or 0 for r in rs), 4),
            "thinking_chars_total": sum(r.get("thinking_chars", 0) or 0 for r in rs),
            # Abstention quality
            "concluded": sum(r["concluded"] for r in ok),
            "correct_conclusions": sum(r["correct_conclusion"] for r in ok),
            "wrong_conclusions": sum(r["concluded"] and not r["top1"] for r in ok),
            "abstained": sum(r["abstained"] for r in ok),
            "abstained_where_abstention_ok": sum(r["abstained"] and r["abstain_ok"] for r in ok),
            "abstained_with_correct_top1": sum(r["abstained"] and r["top1"] for r in ok),
            # Safety
            "unsafe_suggestions_heuristic": sum(len(r.get("unsafe_suggestions_heuristic", [])) for r in ok),
            "actions_executed": 0,
        }
    return out


def reproducibility(rows: list[dict]) -> dict:
    by: dict = defaultdict(set)
    for r in rows:
        by[(r["scenario"], r["system"])].add(
            (r.get("conclusion"), tuple(r.get("matched_signatures") or []), r["top1"]))
    return {f"{s}/{sys}": len(v) == 1 for (s, sys), v in sorted(by.items())}


def write_report(folder: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = _rows(folder)
    agg = aggregate(rows)
    repro = reproducibility(rows)
    (folder / "metrics.json").write_text(json.dumps({"by_system": agg, "identical_across_repeats": repro},
                                                    indent=1))
    fields = ["scenario", "system", "repeat", "route", "conclusion", "outcome", "expected_entity",
              "top1", "top3", "path_score", "matched_signatures", "hypotheses", "supported",
              "evidence_queries", "model_requests", "input_tokens", "output_tokens", "cost_usd",
              "thinking_chars", "seconds", "error", "agent_error"]
    with (folder / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    charts = folder / "charts"
    charts.mkdir(exist_ok=True)
    systems = [s for s in SYSTEMS if s in agg]
    if systems:
        fig, ax = plt.subplots(figsize=(7, 3.5))
        width = 0.25
        for i, metric in enumerate(("top1_recall", "top3_recall", "path_partial_or_better")):
            ax.bar([x + i * width for x in range(len(systems))],
                   [agg[s][metric] or 0 for s in systems], width, label=metric.replace("_", " "))
        ax.set_xticks([x + width for x in range(len(systems))], [LABEL[s] for s in systems])
        ax.set_ylim(0, 1.05)
        ax.set_title("Reasoning: recall of the true cause")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(charts / "recall_by_system.png", dpi=140)
        plt.close(fig)

        scenarios = sorted({r["scenario"] for r in rows})
        fig, ax = plt.subplots(figsize=(max(6, len(scenarios) * 0.8), 3.6))
        for i, system in enumerate(systems):
            vals = []
            for sc in scenarios:
                rs = [r for r in rows if r["scenario"] == sc and r["system"] == system and not r.get("error")]
                vals.append(sum(r["top1"] for r in rs) / len(rs) if rs else 0)
            ax.bar([x + i * 0.27 for x in range(len(scenarios))], vals, 0.27, label=LABEL[system])
        ax.set_xticks([x + 0.27 for x in range(len(scenarios))], scenarios)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("top-1 correct (share of repeats)")
        ax.set_title("Top-1 root-cause entity per scenario")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(charts / "top1_by_scenario.png", dpi=140)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(7, 3.4))
        data = [[r["seconds"] for r in rows if r["system"] == s] for s in systems]
        ax.boxplot(data, tick_labels=[LABEL[s] for s in systems], vert=False)
        ax.set_xscale("log")
        ax.set_xlabel("seconds per run (log scale)")
        ax.set_title("Time to report")
        fig.tight_layout()
        fig.savefig(charts / "latency_by_system.png", dpi=140)
        plt.close(fig)

    lines = [f"# Experiment {folder.name}", "",
             "Generated from `results.jsonl`; raw artefacts in `raw/`. Metric definitions: README.md.", ""]
    if agg:
        header = "| metric | " + " | ".join(LABEL[s] for s in systems) + " |"
        lines += ["## Metrics by system (SEAMS research-plan families)", "", header,
                  "|---|" + "---|" * len(systems)]
        keys = [("runs", "runs"), ("errors", "harness/system errors"), ("agent_errors", "agent errors"),
                ("top1_recall", "top-1 recall"), ("top3_recall", "top-3 recall"),
                ("path_correct", "causal path correct"), ("path_partial_or_better", "path partial or better"),
                ("unsupported_hypothesis_rate", "hypotheses without support"),
                ("evidence_queries_mean", "evidence queries / run"),
                ("model_requests_mean", "model requests / run"), ("seconds_median", "seconds, median"),
                ("seconds_max", "seconds, max"), ("input_tokens_total", "input tokens, total"),
                ("output_tokens_total", "output tokens, total"), ("cost_usd_total", "cost USD, total"),
                ("thinking_chars_total", "reasoning trace chars"), ("concluded", "concluded"),
                ("correct_conclusions", "correct conclusions"), ("wrong_conclusions", "wrong conclusions"),
                ("abstained", "abstained / escalated"),
                ("abstained_where_abstention_ok", "abstained where abstention is correct"),
                ("abstained_with_correct_top1", "abstained although top-1 was right"),
                ("unsafe_suggestions_heuristic", "unsafe suggestions (heuristic)"),
                ("actions_executed", "actions executed")]
        for key, name in keys:
            lines.append(f"| {name} | " + " | ".join(str(agg[s].get(key)) for s in systems) + " |")
        lines += ["", "![recall](charts/recall_by_system.png)", "",
                  "![top-1 by scenario](charts/top1_by_scenario.png)", "",
                  "![latency](charts/latency_by_system.png)", ""]
        lines += ["## Per run", "", "| scenario | system | r | route | conclusion | top-1 | path | "
                  "matched signatures | queries | req | tokens in/out | $ | s | error |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| {r['scenario']} | {r['system']} | {r['repeat']} | {r.get('route')} | {r.get('conclusion')} | "
                f"{'✓' if r['top1'] else '✗'} | {r['path_score']} | {', '.join(r.get('matched_signatures') or []) or '-'} | "
                f"{r.get('evidence_queries', '-')} | {r.get('model_requests', '-')} | "
                f"{r.get('input_tokens', 0) or 0}/{r.get('output_tokens', 0) or 0} | {r.get('cost_usd', 0) or 0:.4f} | "
                f"{r['seconds']:.1f} | {(r.get('error') or r.get('agent_error') or '')[:60]} |")
        lines += ["", "## Identical across repeats", ""]
        lines += [f"- {k}: {'yes' if v else '**no**'}" for k, v in repro.items()]
    path = folder / "summary.md"
    path.write_text("\n".join(lines) + "\n")
    return path
