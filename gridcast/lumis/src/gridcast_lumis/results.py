"""Append-only results log, Markdown summary and charts (matplotlib, PNG)."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "results"
LOG = RESULTS / "drills.jsonl"
BENCH = RESULTS / "bench.jsonl"

OUTCOME_COLORS = {
    "correct_diagnosis": "#2e7d32", "escalated_with_correct_lead": "#7cb342",
    "correct_abstention": "#9e9d24", "escalated_with_entity_lead": "#f9a825",
    "escalated_without_lead": "#ef6c00", "wrong_diagnosis": "#c62828",
}


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summarize() -> Path:
    """results/summary.md + results/*.png from every drill and benchmark recorded so far."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    drills, bench = read(LOG), read(BENCH)
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = ["# GridCast × Lumis results", ""]

    if drills:
        lines += ["## Scenario drills", "",
                  "| when (UTC) | scenario | agent model | route | conclusion | outcome | expected | "
                  "matched signatures | queries | model req | tokens in/out | prepare s | handle s |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for d in drills:
            s = d["score"]
            lines.append(
                f"| {d['at'][:19]} | {s.get('scenario', '-')} | {d.get('model') or '-'} | {s['route']} | "
                f"{s['conclusion']} | {s.get('outcome', '-')} | {s.get('expected_entity', '-')} | "
                f"{', '.join(s['matched_signatures']) or '-'} | {s['evidence_queries']} | "
                f"{s['model_requests']} | {s['input_tokens']}/{s['output_tokens']} | "
                f"{d['timings']['prepare_s']:.2f} | {d['timings']['handle_s']:.2f} |")
        lines.append("")
        last = {}
        for d in drills:  # latest run per (scenario, agent)
            last[(d["score"].get("scenario", "?"), d["use_agent"], d.get("model") or "")] = d
        names = [f"{k[0].split('-')[0]}{' + ' + k[2].split('/')[-1] if k[1] else ''}" for k in last]
        prep = [v["timings"]["prepare_s"] for v in last.values()]
        handle = [v["timings"]["handle_s"] for v in last.values()]
        colors = [OUTCOME_COLORS.get(v["score"].get("outcome", ""), "#9e9e9e") for v in last.values()]
        fig, ax = plt.subplots(figsize=(max(6, len(names) * 0.9), 4))
        ax.bar(names, prep, label="prepare (discovery)", color="#90a4ae")
        ax.bar(names, handle, bottom=prep, label="handle (evidence, triage, agent)", color=colors)
        ax.set_ylabel("seconds from incident to report")
        ax.set_title("Time to report per scenario (bar colour = outcome)")
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        fig.savefig(RESULTS / "time_to_report.png", dpi=140)
        plt.close(fig)
        counts: dict[str, int] = defaultdict(int)
        for v in last.values():
            counts[v["score"].get("outcome", "unscored")] += 1
        fig, ax = plt.subplots(figsize=(6, 3.2))
        keys = list(counts)
        ax.barh(keys, [counts[k] for k in keys],
                color=[OUTCOME_COLORS.get(k, "#9e9e9e") for k in keys])
        ax.set_xlabel("scenario runs (latest per scenario)")
        ax.set_title("Outcomes")
        fig.tight_layout()
        fig.savefig(RESULTS / "outcomes.png", dpi=140)
        plt.close(fig)
        lines += ["![time to report](time_to_report.png)", "", "![outcomes](outcomes.png)", ""]

    if bench:
        lines += ["## Deterministic-path benchmark", "",
                  "| when (UTC) | label | n | route | prepare p50/p95 s | handle p50/p95 ms | "
                  "total p50/p95 s | queries/run |", "|---|---|---|---|---|---|---|---|"]
        fig, ax = plt.subplots(figsize=(7, 3.6))
        series = []
        for b in bench:
            prep = [r["prepare_s"] for r in b["runs"]]
            handle = [r["handle_s"] * 1000 for r in b["runs"]]
            total = [r["total_s"] for r in b["runs"]]
            lines.append(
                f"| {b['at'][:19]} | {b['label']} | {len(total)} | {b['route']} | "
                f"{statistics.median(prep):.2f}/{pct(prep, 0.95):.2f} | "
                f"{statistics.median(handle):.0f}/{pct(handle, 0.95):.0f} | "
                f"{statistics.median(total):.2f}/{pct(total, 0.95):.2f} | {b['queries_per_run']} |")
            series.append((b["label"], handle))
        ax.boxplot([s[1] for s in series], tick_labels=[s[0] for s in series], vert=False)
        ax.set_xlabel("handle_incident latency (ms): evidence queries + triage + report")
        ax.set_title("Deterministic triage latency")
        fig.tight_layout()
        fig.savefig(RESULTS / "deterministic_latency.png", dpi=140)
        plt.close(fig)
        lines += ["", "![deterministic latency](deterministic_latency.png)", ""]

    path = RESULTS / "summary.md"
    path.write_text("\n".join(lines) + "\n")
    return path
