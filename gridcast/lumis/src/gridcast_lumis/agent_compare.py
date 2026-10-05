"""Lumis vs the unguided tool agent on the same frozen incidents: effort, cost, correctness.

Reads an experiment run with `--systems tool_agent,lumis` and writes `agents/summary.md` and
`agents/charts/*.png`. Correctness uses the evidence-ladder scoring (component AND mechanism).
"""

import json
import re
from pathlib import Path

from gridcast_lumis.ladder import existing, score

SYSTEMS = ("tool_agent", "lumis")
LABEL = {"tool_agent": "Tool agent (raw tools, no checks)", "lumis": "Lumis"}
COLOURS = {"tool_agent": "#e879f9", "lumis": "#16a34a"}
# Both systems' calls in common categories. The tool agent writes raw PromQL/LogQL/SQL;
# Lumis' `evidence` runs an operator-registered query by ID.
CATEGORY = {"prometheus_query": "telemetry query", "loki_query": "telemetry query",
            "sql_query": "telemetry query", "evidence": "telemetry query",
            "git_log": "changes and Git", "git_show": "changes and Git", "changes": "changes and Git",
            "git.log": "changes and Git", "git.diff": "changes and Git",
            "read_file": "code and config read", "code.read": "code and config read",
            "code.search": "code and config read",
            "kubectl_get": "cluster state"}
# Lumis' remaining calls are bookkeeping (`catalog`, `hypothesis.register`), not evidence.
CATEGORIES = ("telemetry query", "changes and Git", "code and config read", "cluster state",
              "catalog and hypothesis registration")
CAT_COLOURS = ("#60a5fa", "#f59e0b", "#a78bfa", "#94a3b8", "#e5e7eb")


def _failed(tool: str, content: object) -> int:
    """Failed results in one tool return: the tool agent's error strings, Prometheus/Loki error
    bodies, and Lumis results it denied (unregistered query, path outside the allowlist)."""
    text = content if isinstance(content, str) else json.dumps(content)
    if text.startswith(("error", '{"status":"error"')):
        return 1
    if text.lstrip().startswith(("{", "[")):
        # Lumis: one status per result item. (A `status="error"` *label* inside a successful
        # Prometheus result is data, not a failure, so only `denied` is counted here.)
        return len(re.findall(r'"status": ?"denied"', text))
    # A non-JSON body from Prometheus/Loki is an HTTP error (e.g. a LogQL parse error).
    return int(tool in ("prometheus_query", "loki_query"))


def tool_mix(folder: Path, scenario: str, system: str, repeat: int) -> dict:
    """Calls per category from the raw transcript, failed calls, and rejected final outputs."""
    transcript = json.loads((folder / "raw" / scenario / f"{system}-r{repeat}" / "transcript.json").read_text())
    parts = [p for m in transcript for p in m.get("parts", [])]
    mix = dict.fromkeys(CATEGORIES, 0)
    for p in parts:
        if p.get("part_kind") != "tool-call" or p.get("tool_name") == "final_result":
            continue
        name = p["tool_name"]
        if name == "inspect":
            # Raw args, as the model sent them: some are malformed JSON (rejected and retried).
            found = re.search(r'"operation":\s*"([^"]+)"', p["args"] if isinstance(p["args"], str)
                              else json.dumps(p["args"]))
            name = found.group(1) if found else "inspect"
        mix[CATEGORY.get(name, CATEGORIES[-1])] += 1
    retries = [p for p in parts if p.get("part_kind") == "retry-prompt"]
    failed = sum(_failed(p["tool_name"], p.get("content")) for p in parts
                 if p.get("part_kind") == "tool-return" and p.get("tool_name") != "final_result")
    # A tool call whose arguments failed validation was sent back to the model.
    failed += sum(1 for p in retries if p.get("tool_name") != "final_result")
    # Lumis only: its acceptance rules sent the final answer back (e.g. a suggestion citing
    # evidence that was never collected). Nothing checks the tool agent's final answer.
    rejected = sum(1 for p in retries if p.get("tool_name") == "final_result")
    return {"mix": mix, "failed": failed, "rejected_outputs": rejected}


def collect(folder: Path) -> list[dict]:
    rows = [json.loads(x) for x in (folder / "results.jsonl").read_text().splitlines() if x.strip()]
    out = []
    for r in rows:
        if r["system"] not in SYSTEMS:
            continue
        res = existing(folder, r["scenario"], r["system"], r["repeat"])
        if r["system"] == "tool_agent" and not r.get("input_tokens"):
            # Not recorded in the results row: summed from the raw transcript's per-response usage.
            transcript = json.loads((folder / "raw" / r["scenario"] / f"tool_agent-r{r['repeat']}"
                                     / "transcript.json").read_text())
            usage = [m["usage"] for m in transcript if m.get("kind") == "response"]
            r = r | {"input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                     "output_tokens": sum(u.get("output_tokens", 0) for u in usage)}
        scored = score(folder, r["scenario"], res | {"repeat": r["repeat"]}) if res else {}
        mix = tool_mix(folder, r["scenario"], r["system"], r["repeat"])
        out.append({
            "tool_mix": mix["mix"], "failed_tool_calls": mix["failed"],
            "rejected_outputs": mix["rejected_outputs"],
            "scenario": r["scenario"], "system": r["system"], "repeat": r["repeat"],
            # Both systems counted the same way: investigation tool calls in the raw transcript.
            "tool_calls": sum(mix["mix"].values()), "model_requests": r.get("model_requests") or 0,
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
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, fontsize=9)
    fig.suptitle("Same frozen incidents: effort and cost per run (✓ correct diagnosis, ✗ wrong, – no answer)")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(target / "charts" / "effort.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    labels = [f"{r['scenario']} r{r['repeat']} {'tool agent' if r['system'] == 'tool_agent' else 'Lumis'}"
              for r in rows]
    left = [0] * len(rows)
    for cat, colour in zip(CATEGORIES, CAT_COLOURS, strict=True):
        vals = [r["tool_mix"][cat] for r in rows]
        ax.barh(labels, vals, left=left, color=colour, edgecolor="black", linewidth=0.4, label=cat)
        left = [a + b for a, b in zip(left, vals, strict=True)]
    for i, r in enumerate(rows):
        if r["failed_tool_calls"]:
            ax.text(left[i] + 0.5, i, f"{r['failed_tool_calls']} failed", va="center", fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("tool calls")
    ax.set_title("What each run spent its tool calls on")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(target / "charts" / "tool-mix.png", dpi=150)
    plt.close(fig)

    lines = ["# Lumis vs the unguided tool agent", "",
             "Same model (deepseek-v4-pro-0813, reasoning high), same frozen incidents. Correctness: "
             "top-1 component AND mechanism (ladder rubric).", "",
             "| run | system | correct | concluded | tool calls (failed) | answers sent back | model requests "
             "| input tokens | cost USD | seconds | note |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scenario']} r{r['repeat']} | {LABEL[r['system']]} | {'yes' if r['diagnosis'] else 'no'} | "
                     f"{'yes' if r['concluded'] else 'no'} | {r['tool_calls']} ({r['failed_tool_calls']}) | "
                     f"{r['rejected_outputs'] if r['system'] == 'lumis' else 'n/a (unchecked)'} | "
                     f"{r['model_requests']} | {r['input_tokens']:,} | {r['cost_usd']:.3f} | "
                     f"{r['seconds']:.0f} | {(r['error'] or '')[:60]} |")
    totals = ["", "| totals | correct | concluded | tool calls (failed) | answers sent back | model requests "
              "| input tokens | cost USD | seconds |", "|---|---|---|---|---|---|---|---|---|"]
    for system in SYSTEMS:
        rs = [r for r in rows if r["system"] == system]
        totals.append(f"| {LABEL[system]} | {sum(r['diagnosis'] for r in rs)}/{len(rs)} | "
                      f"{sum(r['concluded'] for r in rs)} | {sum(r['tool_calls'] for r in rs)} "
                      f"({sum(r['failed_tool_calls'] for r in rs)}) | "
                      f"{sum(r['rejected_outputs'] for r in rs) if system == 'lumis' else 'n/a'} | "
                      f"{sum(r['model_requests'] for r in rs)} | "
                      f"{sum(r['input_tokens'] for r in rs):,} | {sum(r['cost_usd'] for r in rs):.3f} | "
                      f"{sum(r['seconds'] for r in rs):.0f} |")
    lines += totals + ["", "![effort](charts/effort.png)", "", "![tool mix](charts/tool-mix.png)", "",
                       "## Suggestions (verbatim, for the safety review)", ""]
    for r in rows:
        for s in r["suggestions"]:
            lines.append(f"- {r['scenario']} r{r['repeat']} {LABEL[r['system']]}: {s[:300]}")
    (target / "summary.md").write_text("\n".join(lines) + "\n")
    return target
