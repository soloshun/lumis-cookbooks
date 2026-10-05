"""Lumis vs the unguided tool agent on the same frozen incidents: effort, cost, correctness.

Reads an experiment run with `--systems tool_agent,lumis` and writes `agents/summary.md` and
`agents/charts/*.png`. Correctness uses the evidence-ladder scoring (component AND mechanism).
"""

import json
from pathlib import Path

from gridcast_lumis.ladder import existing, score

SYSTEMS = ("tool_agent", "lumis")
LABEL = {"tool_agent": "Tool agent (raw tools, no checks)", "lumis": "Lumis"}
COLOURS = {"tool_agent": "#e879f9", "lumis": "#16a34a"}


def collect(folder: Path) -> list[dict]:
    rows = [json.loads(x) for x in (folder / "results.jsonl").read_text().splitlines() if x.strip()]
    out = []
    for r in rows:
        if r["system"] not in SYSTEMS:
            continue
        res = existing(folder, r["scenario"], r["system"], r["repeat"])
        scored = score(folder, r["scenario"], res | {"repeat": r["repeat"]}) if res else {}
        out.append({
            "scenario": r["scenario"], "system": r["system"], "repeat": r["repeat"],
            "tool_calls": r.get("tool_attempts") or 0, "model_requests": r.get("model_requests") or 0,
            "input_tokens": r.get("input_tokens") or 0, "output_tokens": r.get("output_tokens") or 0,
            "cost_usd": r.get("cost_usd") or 0.0, "seconds": r.get("seconds") or 0.0,
            "diagnosis": bool(scored.get("top1_diagnosis")), "concluded": bool(scored.get("concluded")),
            "error": r.get("agent_error") or r.get("error"),
            "suggestions": r.get("suggestions") or [],
        })
    return out


def write(folder: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = collect(folder)
    target = folder / "agents"
    (target / "charts").mkdir(parents=True, exist_ok=True)
    (target / "rows.json").write_text(json.dumps(rows, indent=1))
    runs = [f"{r['scenario']} r{r['repeat']}" for r in rows if r["system"] == "lumis"]

    def series(system: str, key: str) -> list[float]:
        return [r[key] for r in rows if r["system"] == system]

    panels = [("tool_calls", "tool calls"), ("model_requests", "model requests"),
              ("input_tokens", "input tokens"), ("cost_usd", "cost (USD)"), ("seconds", "seconds")]
    fig, axes = plt.subplots(1, len(panels), figsize=(18, 4.2))
    width = 0.38
    for ax, (key, title) in zip(axes, panels, strict=True):
        for offset, system in ((-width / 2, "tool_agent"), (width / 2, "lumis")):
            vals = series(system, key)
            bars = ax.bar([i + offset for i in range(len(vals))], vals, width, color=COLOURS[system],
                          edgecolor="black", linewidth=0.5, label=LABEL[system])
            for bar, row in zip(bars, [r for r in rows if r["system"] == system], strict=True):
                mark = "✓" if row["diagnosis"] else ("–" if row["error"] else "✗")
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), mark, ha="center",
                        va="bottom", fontsize=9)
        ax.set_xticks(range(len(runs)), runs, fontsize=8)
        ax.set_title(title)
    axes[0].legend(fontsize=7)
    fig.suptitle("Same frozen incidents: effort and cost per run (✓ correct diagnosis, ✗ wrong, – no answer)")
    fig.tight_layout()
    fig.savefig(target / "charts" / "effort.png", dpi=150)
    plt.close(fig)

    lines = ["# Lumis vs the unguided tool agent", "",
             "Same model (deepseek-v4-pro-0813, reasoning high), same frozen incidents. Correctness: "
             "top-1 component AND mechanism (ladder rubric).", "",
             "| run | system | correct | tool calls | model requests | input tokens | cost USD | seconds | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scenario']} r{r['repeat']} | {LABEL[r['system']]} | {'yes' if r['diagnosis'] else 'no'} | "
                     f"{r['tool_calls']} | {r['model_requests']} | {r['input_tokens']:,} | {r['cost_usd']:.3f} | "
                     f"{r['seconds']:.0f} | {(r['error'] or '')[:60]} |")
    lines += ["", "![effort](charts/effort.png)", "", "## Suggestions (verbatim, for the safety review)", ""]
    for r in rows:
        for s in r["suggestions"]:
            lines.append(f"- {r['scenario']} r{r['repeat']} {LABEL[r['system']]}: {s[:300]}")
    (target / "summary.md").write_text("\n".join(lines) + "\n")
    return target
