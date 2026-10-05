"""The evidence ladder: which part of Lumis buys what, measured on frozen incidents.

Runs offline against a finished (or running) experiment folder: every rung sees the *same*
frozen incident as the systems that ran live, and nothing touches the estate except the
read-only evidence fetch of `single_pass_verified`.

| System | The LLM gets | Isolates |
|---|---|---|
| llm_symptoms | alert symptoms + affected services, no evidence | the model alone (guessing) |
| llm_graph | + the scoped service graph | topology |
| single_pass | + the facts Lumis collected (ran live) | curated evidence |
| single_pass_verified | single_pass answers; Lumis fetches and checks their evidence (no LLM) | verification |
| lumis | everything (ran live) | the full system |
"""

import asyncio
import json
import os
import re
import statistics
from pathlib import Path

import httpx

from gridcast_lumis.scoring import graph_entity

SYSTEM_PROMPT = (
    "You are a site reliability engineer diagnosing a production incident. Propose 3 to 5 "
    "competing root-cause hypotheses, most likely first. For each, name the component where the "
    "fault originates (use the given service IDs when you can), the mechanism (what went wrong, "
    "in one sentence), and a short statement. Do not propose actions."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hypotheses"],
    "properties": {"hypotheses": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["root_cause", "mechanism", "statement"],
        "properties": {"root_cause": {"type": "string"}, "mechanism": {"type": "string"},
                       "statement": {"type": "string"}}}}},
}

# Mechanism rubric: does the text state *why*, not just *where*? Applied identically to every
# system's top-ranked candidate. Deliberately concrete, and not satisfiable by echoing the alert
# names alone (see docs/research-notes.md for the caveat). Revised once on 2026-10-04 after
# inspecting outputs: E also accepts "alias moved / slower model / model version", G also
# accepts "rejected"; the revision applies to every system. Second revision (2026-10-05, after a
# spot check of K-O): O also accepts "not delivering ... zone", and component names match
# regardless of hyphens/spaces ("grid telemetry" = grid-telemetry).
RUBRIC: dict[str, str] = {
    "J": r"scal\w*\s+(down\s+)?to\s+(0|zero)|\b(0|zero)\s+replicas|replicas\W{0,5}(=|:|to)?\s*(0|zero)\b",
    "A": r"n\s*\+\s*1|query amplification|(thousands|2[,.]?[45]\d\d|excessive|many|more)\W.{0,30}(quer|sql|statement)"
         r"|lag_resolution|minute[- ]resolution|1\.7\.0",
    "F": r"n\s*\+\s*1|query amplification|(thousands|2[,.]?[45]\d\d|excessive|many|more)\W.{0,30}(quer|sql|statement)"
         r"|lag_resolution|minute[- ]resolution|1\.7\.0",
    "C": r"password|credential|authenticat|secret|rotat",
    "D": r"\boom|out of memory|memory limit|oomkilled|memory.{0,20}(limit|exceed|below)",
    "E": r"(model|alias).{0,50}(promot|chang|switch|new version|swap|mov|load)|promot.{0,40}model|hifi|scenario.?forest"
         r"|(new|slower|different|heavier) (serving )?model|model version",
    "G": r"schema|contract|renam|demand_kw|api.?version|payload.{0,30}(chang|format|field)|field.{0,30}(chang|renam|missing)"
         r"|reject",
    "H": r"\bkw\b|kilowatt|\bunits?\b|\bscal(e|ing)\b|1000|magnitude",
    "I": r"\b503\b|outage|unavailab|\bdown\b|http 5\d\d",
    "B": r"stale|frozen|repeat|identical|snapshot|not (chang|vary)|unchang",
    "K": r"(timeout|time-?out).{0,80}(lower|reduc|tighten|shorten|2 ?s|config|chang|too (short|low))"
         r"|(lower|reduc|tighten|shorten|chang).{0,80}(timeout|time-?out)|ingest_http_timeout",
    "L": r"(export|historian|feed|telemetry|vendor|upstream|source).{0,60}"
         r"(stuck|stopp|stall|halt|not (advanc|deliver|send|publish)|gap|no new)"
         r"|(stuck|stopp|stall|gap|no new).{0,60}(export|historian|feed|telemetry|vendor|upstream)",
    "M": r"\bcpu\b|throttl|millicore|\b50m\b",
    "N": r"\bkw\b|kilowatt|\bunits?\b|\bscal(e|ing)\b|skew|training.?serving|1000",
    "O": r"(zone|tamale|substation|partition|subset).{0,80}"
         r"(missing|gap|stopp|stuck|not report|not deliver|absent|incomplete|no (data|readings))"
         r"|(missing|gap|stopp|stuck|absent|incomplete|not deliver|no longer deliver).{0,80}(zone|tamale|substation|partition)",
}


def mechanism_ok(scenario: str, text: str) -> bool:
    return bool(re.search(RUBRIC[scenario[0]], text, re.IGNORECASE))


# ------------------------------------------------------------------------------- entities
def _graph_maps(raw: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    graph = json.loads((raw / "graph.json").read_text())["graph"]
    hosts = {e["source"]: e["target"] for e in graph["relationships"] if e["kind"] == "hosts"}
    names = [(e["id"].split(":")[-1], e["id"]) for e in graph["entities"]
             if e["id"].startswith("service:")]
    synonyms = [("wx-primary", "service:gridcast:weather-vendor-wx-primary"),
                ("weather vendor", "service:gridcast:weather-vendor-wx-primary"),
                ("historian", "service:gridcast:grid-telemetry"),
                ("scada", "service:gridcast:grid-telemetry"),
                ("database", "service:gridcast:postgres"),
                ("postgresql", "service:gridcast:postgres")]
    return hosts, sorted(names + synonyms, key=lambda pair: -len(pair[0]))


def entity_of(text: str, hosts: dict[str, str], names: list[tuple[str, str]]) -> str | None:
    """A free-text or ID root cause -> canonical graph service ID (longest name match)."""
    text = text.strip()
    if text in hosts:
        return hosts[text]
    if text.startswith("service:"):
        # Free-text answers decorate the ID ("service:gridcast:x (image 1.7.0)"): keep the ID.
        return re.match(r"service:[\w.-]+:[\w.-]+", text).group(0)
    lowered = text.lower().replace("-", " ").replace("_", " ")
    return next((ident for name, ident in names
                 if name.lower().replace("-", " ").replace("_", " ") in lowered), None)


# ------------------------------------------------------------------------- model rungs
def _context(raw: Path, rung: str) -> dict:
    incident = json.loads((raw / "incident.json").read_text())
    context = {"incident": {k: incident[k] for k in
                            ("affected_entities", "symptoms", "started_at", "ended_at")}}
    if rung == "llm_graph":
        graph = json.loads((raw / "single_pass-r1" / "evidence_context.json").read_text())["graph"]
        context["service_graph"] = graph
    return context


async def _ask(client: httpx.AsyncClient, model: str, context: dict) -> dict:
    payload = {
        "model": model, "max_tokens": 32000,
        "provider": {"require_parameters": True, "allow_fallbacks": False},
        "reasoning": {"effort": "high", "exclude": False}, "usage": {"include": True},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": json.dumps(context)}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "hypotheses", "strict": True, "schema": SCHEMA}},
    }
    response = await client.post(
        "https://openrouter.ai/api/v1/chat/completions", json=payload,
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
    response.raise_for_status()
    return response.json()


async def run_rung(folder: Path, scenario: str, rung: str, repeat: int, model: str,
                   client: httpx.AsyncClient) -> dict:
    raw = folder / "raw" / scenario
    out = folder / "ladder" / scenario / f"{rung}-r{repeat}"
    out.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    body = await _ask(client, model, _context(raw, rung))
    (out / "raw_response.json").write_text(json.dumps(body, indent=1))
    message = body["choices"][0]["message"]
    (out / "reasoning.md").write_text(message.get("reasoning") or "")
    hypotheses = json.loads(message.get("content") or "{}").get("hypotheses", [])
    hosts, names = _graph_maps(raw)
    # Generous to the model: if `root_cause` is a category rather than a component, use the
    # first component named in its mechanism or statement.
    ranked = [{"entity": entity_of(h["root_cause"], hosts, names)
               or entity_of(h["mechanism"] + " " + h["statement"], hosts, names),
               "text": f"{h['root_cause']} :: {h['mechanism']} :: {h['statement']}"}
              for h in hypotheses]
    (out / "candidates.json").write_text(json.dumps(ranked, indent=1))
    usage = body.get("usage") or {}
    return {"system": rung, "seconds": round(loop.time() - t0, 1), "ranked": ranked,
            "cost_usd": float(usage.get("cost") or 0), "concluded": False}


# ------------------------------------------------------ single_pass_verified (no LLM)
async def verify_single_pass(folder: Path, scenario: str, repeat: int) -> dict | None:
    """Fetch the evidence each single-pass hypothesis names, then assess it as Lumis would.
    None when that single-pass run produced no candidates (e.g. it failed)."""
    from lumis_sdk.connectors.factory import evidence_connectors
    from lumis_sdk.core import Hypothesis, IncidentContext
    from lumis_sdk.reasoning import assess
    from lumis_sdk.runtime import YamlProject
    from lumis_sdk.runtime.incident_handler import root_cause
    from lumis_sdk.runtime.session import local_connectors

    from gridcast_lumis.runner import PROJECT_FILE, load_sql_dsn

    load_sql_dsn()
    raw = folder / "raw" / scenario
    run = raw / f"single_pass-r{repeat}"
    if not (run / "evidence_context.json").exists() or not (run / "candidates.json").exists():
        return None
    context = IncidentContext.model_validate_json((run / "evidence_context.json").read_text())
    candidates = json.loads((run / "candidates.json").read_text())
    project = YamlProject.from_file(PROJECT_FILE)
    catalog = {q.id: q for q in context.queries}
    fetched = []
    async with httpx.AsyncClient(timeout=20) as client:
        connectors = local_connectors(project.config, project.base, ()) | evidence_connectors(
            project.config.sources, client)
        have = {e.query_id for e in context.evidence}
        wanted = dict.fromkeys(q for c in candidates if c.get("accepted_by_lumis")
                               for q in c["hypothesis"].get("evidence_needed", [])
                               if q in catalog and q not in have and catalog[q].provider != "probe")
        for query_id in wanted:
            try:
                fetched += list(await connectors[catalog[query_id].provider].collect(
                    catalog[query_id], context.incident))
            except Exception:  # noqa: BLE001 - unavailable evidence stays unknown
                continue
    full = context.model_copy(update={"evidence": (*context.evidence, *fetched)})
    order = {"supported": 0, "unresolved": 2, "contradicted": 3}
    assessed = []
    for c in candidates:
        if not c.get("accepted_by_lumis"):
            continue
        a = assess(Hypothesis.model_validate(c["hypothesis"]), full, ("single_pass",))
        assessed.append((order[a.state], c["rank"], a))
    assessed.sort(key=lambda item: (item[0], item[1]))
    viable = [a for _, _, a in assessed if a.state != "contradicted"]
    roots = {root_cause(a.hypothesis.causal_path[0], full) for a in viable}
    concluded = bool(viable) and all(a.state == "supported" for a in viable) and len(roots) == 1
    hosts, _ = _graph_maps(raw)
    ranked = [{"entity": hosts.get(a.hypothesis.causal_path[0], a.hypothesis.causal_path[0]),
               "text": a.hypothesis.statement, "state": a.state} for _, _, a in assessed]
    out = folder / "ladder" / scenario / f"single_pass_verified-r{repeat}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "assessed.json").write_text(json.dumps(
        {"fetched_queries": list(wanted), "facts_added": len(fetched), "ranked": ranked,
         "concluded": concluded}, indent=1))
    return {"system": "single_pass_verified", "seconds": 0.0, "ranked": ranked, "cost_usd": 0.0,
            "concluded": concluded, "facts_added": len(fetched)}


# ----------------------------------------------------- existing systems, same scoring
def _live_row(folder: Path, scenario: str, system: str, repeat: int) -> dict:
    for line in (folder / "results.jsonl").read_text().splitlines():
        row = json.loads(line)
        if (row["scenario"], row["system"], row["repeat"]) == (scenario, system, repeat):
            return {"cost_usd": row.get("cost_usd") or 0.0, "seconds": row.get("seconds")}
    return {}


def existing(folder: Path, scenario: str, system: str, repeat: int) -> dict | None:
    live = _live_row(folder, scenario, system, repeat)
    raw = folder / "raw" / scenario
    run = raw / f"{system}-r{repeat}"
    hosts, _ = _graph_maps(raw)
    if system == "tool_agent":
        if not (run / "candidates.json").exists():
            return None
        data = json.loads((run / "candidates.json").read_text())
        _, names = _graph_maps(raw)
        ranked = [{"entity": entity_of(c["entity"], hosts, names) if c["entity"] else None, "text": c["text"]}
                  for c in data["candidates"]]
        return {"system": system, "ranked": ranked, "concluded": False} | live
    if system == "single_pass":
        if not (run / "candidates.json").exists():
            return None
        cands = json.loads((run / "candidates.json").read_text())
        ranked = []
        for c in cands:
            path = [x for x in (c["hypothesis"].get("causal_path") or []) if isinstance(x, str) and ":" in x]
            if path:
                ranked.append({"entity": hosts.get(path[0], path[0]), "text": c["hypothesis"].get("statement", "")})
        supported = [c for c in cands if (c.get("assessment") or {}).get("state") == "supported"]
        return {"system": system, "ranked": ranked,
                "concluded": bool(supported) and supported[0]["rank"] == 1} | live
    if not (run / "report.json").exists():
        return None
    report = json.loads((run / "report.json").read_text())
    order = {"supported": 0, "unresolved": 2, "contradicted": 3}
    items = [(order[a["state"]], a["hypothesis"]) for a in report["assessments"]]
    known = {a["hypothesis"]["id"] for a in report["assessments"]}
    items += [(1, f["assessment"]["hypothesis"]) for f in report["findings"]
              if f["status"] == "match" and f["assessment"]["hypothesis"]["id"] not in known]
    items.sort(key=lambda item: item[0])
    ranked = [{"entity": hosts.get(h["causal_path"][0], h["causal_path"][0]), "text": h["statement"]}
              for _, h in items]
    return {"system": system, "ranked": ranked,
            "concluded": report["conclusion"] == "supported_diagnosis"} | live


def score(folder: Path, scenario: str, result: dict) -> dict:
    truth = json.loads((folder / "raw" / scenario / "ground_truth.json").read_text())["ground_truth"]
    expected = graph_entity(truth["root_cause_entity"])
    ranked = result["ranked"]
    top = ranked[0] if ranked else None
    entity_ok = bool(top) and top["entity"] == expected
    mech_ok = bool(top) and mechanism_ok(scenario, top["text"])
    return {"scenario": scenario, "system": result["system"], "repeat": result.get("repeat"),
            "top1_entity": entity_ok, "top3_entity": expected in [r["entity"] for r in ranked[:3]],
            "top1_mechanism": mech_ok, "top1_diagnosis": entity_ok and mech_ok,
            "any_diagnosis": any(r["entity"] == expected and mechanism_ok(scenario, r["text"]) for r in ranked),
            "concluded": result.get("concluded", False),
            "correct_conclusion": result.get("concluded", False) and entity_ok and mech_ok,
            "cost_usd": result.get("cost_usd"), "seconds": result.get("seconds"),
            "facts_added": result.get("facts_added")}


SYSTEMS = ("rules", "llm_symptoms", "llm_graph", "single_pass", "single_pass_verified", "tool_agent", "lumis")


def aggregate(rows: list[dict]) -> dict:
    out = {}
    for system in SYSTEMS:
        rs = [r for r in rows if r["system"] == system]
        if not rs:
            continue
        concluded = [r for r in rs if r["concluded"]]
        def rate(key: str, rs: list[dict] = rs) -> float:
            return round(sum(r[key] for r in rs) / len(rs), 3)

        out[system] = {
            "runs": len(rs), "top1_entity": rate("top1_entity"), "top3_entity": rate("top3_entity"),
            "top1_mechanism": rate("top1_mechanism"), "top1_diagnosis": rate("top1_diagnosis"),
            "any_diagnosis": rate("any_diagnosis"), "concluded": len(concluded),
            "conclusion_precision": round(sum(r["correct_conclusion"] for r in concluded) / len(concluded), 3)
            if concluded else None,
            "cost_usd_total": round(sum(r.get("cost_usd") or 0 for r in rs), 4),
            "seconds_median": statistics.median(r["seconds"] for r in rs if r.get("seconds") is not None)
            if any(r.get("seconds") is not None for r in rs) else None,
        }
    return out


# ------------------------------------------------------------------------------ runner
def _done(folder: Path, scenario: str) -> bool:
    raw = folder / "raw" / scenario
    return (raw / "ground_truth.json").exists() and (raw / "revert.txt").exists()


async def run_ladder(folder: Path, scenarios: list[str] | None, repeats: int, model: str) -> Path:
    target = folder / "ladder"
    target.mkdir(exist_ok=True)
    results_file = target / "results.jsonl"
    rows = [json.loads(x) for x in results_file.read_text().splitlines() if x.strip()] \
        if results_file.exists() else []
    seen = {(r["scenario"], r["system"], r["repeat"]) for r in rows}
    available = sorted(p.name for p in (folder / "raw").iterdir() if _done(folder, p.name))
    todo = [s for s in (scenarios or available) if s in available]
    async with httpx.AsyncClient(timeout=600, trust_env=False) as client:
        for scenario in todo:
            jobs = []
            for k in range(1, repeats + 1):
                for rung in ("llm_symptoms", "llm_graph"):
                    if (scenario, rung, k) not in seen:
                        jobs.append((rung, k, run_rung(folder, scenario, rung, k, model, client)))
            new = []
            for (rung, k, _job), result in zip(
                    jobs, await asyncio.gather(*(j for _, _, j in jobs), return_exceptions=True),
                    strict=True):
                if isinstance(result, BaseException):
                    print(f"[{scenario}] {rung} r{k}: error {type(result).__name__}: {result}"[:300])
                    continue
                new.append(score(folder, scenario, result | {"repeat": k}))
            for k in range(1, repeats + 1):
                if (scenario, "single_pass_verified", k) not in seen:
                    result = await verify_single_pass(folder, scenario, k)
                    if result is not None:
                        new.append(score(folder, scenario, result | {"repeat": k}))
                for system, n in (("single_pass", repeats), ("lumis", repeats), ("rules", 5)):
                    if k == 1:
                        for j in range(1, n + 1):
                            if (scenario, system, j) not in seen and (res := existing(folder, scenario, system, j)):
                                new.append(score(folder, scenario, res | {"repeat": j}))
            with results_file.open("a") as handle:
                for row in new:
                    handle.write(json.dumps(row) + "\n")
            rows += new
            seen |= {(r["scenario"], r["system"], r["repeat"]) for r in new}
            print(f"[{scenario}] ladder: +{len(new)} rows")
    write_summary(target, rows)
    return target


def rescore_all(folder: Path) -> list[dict]:
    """Re-score every saved ladder and live run with the current rubric (no model calls)."""
    rows = []
    target = folder / "ladder"
    for scenario_dir in sorted(p for p in target.iterdir()
                               if p.is_dir() and (folder / "raw" / p.name / "ground_truth.json").exists()):
        sc = scenario_dir.name
        for run in sorted(scenario_dir.iterdir()):
            system, _, k = run.name.rpartition("-r")
            if system in ("llm_symptoms", "llm_graph") and (run / "candidates.json").exists():
                body = json.loads((run / "raw_response.json").read_text())
                hosts, names = _graph_maps(folder / "raw" / sc)
                ranked = [c | {"entity": entity_of(c["entity"], hosts, names) if c.get("entity") else None}
                          for c in json.loads((run / "candidates.json").read_text())]
                result = {"system": system, "ranked": ranked,
                          "cost_usd": float((body.get("usage") or {}).get("cost") or 0), "seconds": None}
            elif system == "single_pass_verified":
                data = json.loads((run / "assessed.json").read_text())
                result = {"system": system, "ranked": data["ranked"], "concluded": data["concluded"],
                          "cost_usd": 0.0, "seconds": 0.0, "facts_added": data["facts_added"]}
            else:
                continue
            rows.append(score(folder, sc, result | {"repeat": int(k)}))
        for system, n in (("single_pass", 2), ("lumis", 2), ("rules", 5), ("tool_agent", 2)):
            for j in range(1, n + 1):
                if res := existing(folder, sc, system, j):
                    rows.append(score(folder, sc, res | {"repeat": j}))
    (target / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    write_summary(target, rows)
    return rows


def write_summary(target: Path, rows: list[dict]) -> None:
    metrics = aggregate(rows)
    (target / "metrics.json").write_text(json.dumps(metrics, indent=1))
    label = {"rules": "Rules only", "llm_symptoms": "LLM, alert only (no evidence)", "llm_graph": "LLM + graph",
             "single_pass": "LLM + Lumis evidence (one shot)",
             "single_pass_verified": "one shot + Lumis verification", "tool_agent": "Tool agent (no checks)",
             "lumis": "Lumis"}
    systems = [s for s in SYSTEMS if s in metrics]
    keys = [("runs", "runs"), ("top1_entity", "top-1 component"), ("top3_entity", "top-3 component"),
            ("top1_mechanism", "top-1 mechanism (rubric)"), ("top1_diagnosis", "top-1 component AND mechanism"),
            ("any_diagnosis", "right diagnosis anywhere in output"), ("concluded", "concluded"),
            ("conclusion_precision", "conclusion precision"), ("cost_usd_total", "cost USD"),
            ("seconds_median", "seconds, median")]
    scen = sorted({r["scenario"] for r in rows})
    lines = ["# Evidence ladder", "", f"Scenarios: {', '.join(scen)}. Generated by `gridcast-lumis ladder`.",
             "Mechanism uses the regex rubric in `src/gridcast_lumis/ladder.py` (RUBRIC).",
             "For Rules only, a correct top-1 is the matched signature: a lead for a human. Rules conclude",
             "only when a terminal signature is sufficient (J); everything else escalates.", "",
             "| metric | " + " | ".join(label[s] for s in systems) + " |", "|---|" + "---|" * len(systems)]
    for key, name in keys:
        lines.append(f"| {name} | " + " | ".join(str(metrics[s].get(key)) for s in systems) + " |")
    lines += ["", "## Top-1 diagnosis (component AND mechanism) per scenario", "",
              "| scenario | " + " | ".join(label[s] for s in systems) + " |", "|---|" + "---|" * len(systems)]
    for sc in scen:
        cells = []
        for s in systems:
            rs = [r for r in rows if r["scenario"] == sc and r["system"] == s]
            cells.append(f"{sum(r['top1_diagnosis'] for r in rs)}/{len(rs)}" if rs else "-")
        lines.append(f"| {sc} | " + " | ".join(cells) + " |")
    charts = write_charts(target, rows)
    lines += ["", "## Charts", ""] + [f"![{name}](charts/{name})" for name in charts]
    (target / "summary.md").write_text("\n".join(lines) + "\n")


# ------------------------------------------------------------------------------ charts
LONG = {"rules": "Rules only", "llm_symptoms": "LLM, alert only\n(no evidence)",
        "llm_graph": "LLM + graph", "single_pass": "Single-pass\n(LLM + Lumis evidence)",
        "single_pass_verified": "Single-pass +\nLumis verification",
        "tool_agent": "Tool agent\n(raw tools, no checks)", "lumis": "Lumis (full)"}
COLOURS = {"rules": "#64748b", "llm_symptoms": "#f97316", "llm_graph": "#facc15",
           "single_pass": "#38bdf8", "single_pass_verified": "#818cf8", "tool_agent": "#e879f9",
           "lumis": "#16a34a"}


def write_charts(target: Path, rows: list[dict]) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    charts = target / "charts"
    charts.mkdir(exist_ok=True)
    metrics = aggregate(rows)
    systems = [s for s in SYSTEMS if s in metrics]
    scenarios = sorted({r["scenario"] for r in rows})
    written = []

    # 1. Everything in one chart: systems x metrics.
    cols = [("top1_entity", "top-1\ncomponent"), ("top3_entity", "top-3\ncomponent"),
            ("top1_mechanism", "top-1\nmechanism"), ("top1_diagnosis", "component AND\nmechanism"),
            ("any_diagnosis", "right diagnosis\nanywhere"), ("conclusion_precision", "conclusion\nprecision")]
    grid = np.array([[metrics[s][k] if metrics[s][k] is not None else np.nan for k, _ in cols]
                     for s in systems], dtype=float)
    fig, ax = plt.subplots(figsize=(10, 5.2))
    im = ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)), [c for _, c in cols], fontsize=9)
    ax.set_yticks(range(len(systems)), [LONG[s].replace("\n", " ") for s in systems], fontsize=9)
    for i in range(len(systems)):
        for j in range(len(cols)):
            v = grid[i, j]
            ax.text(j, i, "n/a" if np.isnan(v) else f"{v:.2f}", ha="center", va="center", fontsize=9,
                    color="black")
    ax.set_title(f"All systems, all metrics ({len(scenarios)} scenarios, same frozen incidents)")
    fig.colorbar(im, ax=ax, fraction=0.03)
    fig.tight_layout()
    fig.savefig(charts / "overview.png", dpi=150)
    plt.close(fig)
    written.append("overview.png")

    # 2. The ladder: what each added component buys.
    fig, ax = plt.subplots(figsize=(10, 4.4))
    vals = [metrics[s]["top1_diagnosis"] for s in systems]
    bars = ax.bar(range(len(systems)), vals, color=[COLOURS[s] for s in systems])
    for bar, v in zip(bars, vals, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=10)
    ax.set_xticks(range(len(systems)), [LONG[s] for s in systems], fontsize=8)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("top-1 component AND mechanism")
    ax.set_title("The evidence ladder: correct diagnosis rate by what the system is given")
    fig.tight_layout()
    fig.savefig(charts / "ladder.png", dpi=150)
    plt.close(fig)
    written.append("ladder.png")

    # 3. Which system solved which scenario.
    grid = np.array([[np.mean([r["top1_diagnosis"] for r in rows if r["scenario"] == sc and r["system"] == s])
                      if any(r["scenario"] == sc and r["system"] == s for r in rows) else np.nan
                      for s in systems] for sc in scenarios], dtype=float)
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(scenarios) + 1.6))
    ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(systems)), [LONG[s] for s in systems], fontsize=7)
    ax.set_yticks(range(len(scenarios)), scenarios)
    for i in range(len(scenarios)):
        for j in range(len(systems)):
            if not np.isnan(grid[i, j]):
                ax.text(j, i, f"{grid[i, j]:.1f}", ha="center", va="center", fontsize=8)
    ax.axhline(scenarios.index("K") - 0.5 if "K" in scenarios else -1, color="black", lw=1.5)
    ax.set_title("Correct diagnosis per scenario (share of repeats); K-O below the line are the hard set")
    fig.tight_layout()
    fig.savefig(charts / "by_scenario.png", dpi=150)
    plt.close(fig)
    written.append("by_scenario.png")

    # 4. Original scenarios vs the hard set.
    hard = [s for s in scenarios if s in "KLMNO"]
    original = [s for s in scenarios if s not in hard]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    width = 0.38
    for offset, subset, label, alpha in ((-width / 2, original, "A-J", 0.55), (width / 2, hard, "K-O (hard)", 1.0)):
        vs = [np.mean([r["top1_diagnosis"] for r in rows if r["system"] == s and r["scenario"] in subset] or [0])
              for s in systems]
        ax.bar([x + offset for x in range(len(systems))], vs, width, label=label, alpha=alpha,
               color=[COLOURS[s] for s in systems], edgecolor="black", linewidth=0.5)
        for x, v in enumerate(vs):
            ax.text(x + offset, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(systems)), [LONG[s] for s in systems], fontsize=8)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("top-1 component AND mechanism")
    ax.set_title("Original scenarios (faded) vs the hard set K-O (solid)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(charts / "original_vs_hard.png", dpi=150)
    plt.close(fig)
    written.append("original_vs_hard.png")

    # 4b. The hard set on its own: K-O, every system, overall and per scenario.
    if hard:
        fig, (left, right) = plt.subplots(1, 2, figsize=(14, 4.6), gridspec_kw={"width_ratios": [1.3, 1]})
        vs = [np.mean([r["top1_diagnosis"] for r in rows if r["system"] == s and r["scenario"] in hard] or [0])
              for s in systems]
        bars = left.bar(range(len(systems)), vs, color=[COLOURS[s] for s in systems], edgecolor="black",
                        linewidth=0.5)
        for bar, v in zip(bars, vs, strict=True):
            left.text(bar.get_x() + bar.get_width() / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=9)
        left.set_xticks(range(len(systems)), [LONG[s] for s in systems], fontsize=7)
        left.set_ylim(0, 1.1)
        left.set_ylabel("top-1 component AND mechanism")
        left.set_title(f"Hard set ({', '.join(hard)}): correct diagnosis rate")
        grid = np.array([[np.mean([r["top1_diagnosis"] for r in rows if r["scenario"] == sc and r["system"] == s])
                          if any(r["scenario"] == sc and r["system"] == s for r in rows) else np.nan
                          for s in systems] for sc in hard], dtype=float)
        right.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
        right.set_xticks(range(len(systems)), [LONG[s].replace("\n", " ") for s in systems], fontsize=7,
                         rotation=30, ha="right")
        right.set_yticks(range(len(hard)), [{"K": "K timeout + slow vendor", "L": "L decoy release, data gap",
                                              "M": "M CPU limit squeeze", "N": "N training/serving skew",
                                              "O": "O one zone missing"}.get(h, h) for h in hard], fontsize=8)
        for i in range(len(hard)):
            for j in range(len(systems)):
                if not np.isnan(grid[i, j]):
                    right.text(j, i, f"{grid[i, j]:.1f}", ha="center", va="center", fontsize=8)
        right.set_title("Per hard scenario (share of repeats)")
        fig.tight_layout()
        fig.savefig(charts / "hard_set.png", dpi=150)
        plt.close(fig)
        written.append("hard_set.png")

    # 5. Cost against correctness.
    fig, ax = plt.subplots(figsize=(8, 4.6))
    for s in systems:
        cost = metrics[s]["cost_usd_total"] / metrics[s]["runs"]
        ax.scatter(max(cost, 0.0005), metrics[s]["top1_diagnosis"], s=160, color=COLOURS[s],
                   edgecolor="black", zorder=3)
        ax.annotate(LONG[s].replace("\n", " "), (max(cost, 0.0005), metrics[s]["top1_diagnosis"]),
                    textcoords="offset points", xytext=(8, 4), fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("model cost per run, USD (log; $0 plotted at $0.0005)")
    ax.set_ylabel("top-1 component AND mechanism")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("What correctness costs")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(charts / "cost_vs_correctness.png", dpi=150)
    plt.close(fig)
    written.append("cost_vs_correctness.png")
    return written
