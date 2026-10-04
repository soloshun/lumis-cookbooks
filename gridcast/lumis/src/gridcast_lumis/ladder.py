"""The evidence ladder: which part of Lumis buys what, measured on frozen incidents.

Runs offline against a finished (or running) experiment folder: every rung sees the *same*
frozen incident as the systems that ran live, and nothing touches the estate except the
read-only evidence fetch of `single_pass_verified`.

| System | The LLM gets | Isolates |
|---|---|---|
| llm_symptoms | alert symptoms + affected services ("paste the alert into a chat") | the model alone |
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
# names alone (see docs/research-notes.md for the caveat).
RUBRIC: dict[str, str] = {
    "J": r"scal\w*\s+(down\s+)?to\s+(0|zero)|\b(0|zero)\s+replicas|replicas\W{0,5}(=|:|to)?\s*(0|zero)\b",
    "A": r"n\s*\+\s*1|query amplification|(thousands|2[,.]?[45]\d\d|excessive|many|more)\W.{0,30}(quer|sql|statement)"
         r"|lag_resolution|minute[- ]resolution|1\.7\.0",
    "F": r"n\s*\+\s*1|query amplification|(thousands|2[,.]?[45]\d\d|excessive|many|more)\W.{0,30}(quer|sql|statement)"
         r"|lag_resolution|minute[- ]resolution|1\.7\.0",
    "C": r"password|credential|authenticat|secret|rotat",
    "D": r"\boom|out of memory|memory limit|oomkilled|memory.{0,20}(limit|exceed|below)",
    "E": r"(model|alias).{0,50}(promot|chang|switch|new version|swap)|promot.{0,40}model|hifi|scenario.?forest",
    "G": r"schema|contract|renam|demand_kw|api.?version|payload.{0,30}(chang|format|field)|field.{0,30}(chang|renam|missing)",
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
         r"(missing|gap|stopp|stuck|not report|absent|incomplete|no (data|readings))"
         r"|(missing|gap|stopp|stuck|absent|incomplete).{0,80}(zone|tamale|substation|partition)",
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
        return text
    lowered = text.lower()
    return next((ident for name, ident in names if name.lower() in lowered), None)


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
async def verify_single_pass(folder: Path, scenario: str, repeat: int) -> dict:
    """Fetch the evidence each single-pass hypothesis names, then assess it as Lumis would."""
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
def existing(folder: Path, scenario: str, system: str, repeat: int) -> dict | None:
    raw = folder / "raw" / scenario
    run = raw / f"{system}-r{repeat}"
    hosts, _ = _graph_maps(raw)
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
        return {"system": system, "ranked": ranked, "concluded": bool(supported) and supported[0]["rank"] == 1}
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
            "concluded": report["conclusion"] == "supported_diagnosis"}


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


SYSTEMS = ("rules", "llm_symptoms", "llm_graph", "single_pass", "single_pass_verified", "lumis")


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


def write_summary(target: Path, rows: list[dict]) -> None:
    metrics = aggregate(rows)
    (target / "metrics.json").write_text(json.dumps(metrics, indent=1))
    label = {"rules": "Rules only", "llm_symptoms": "LLM, symptoms only", "llm_graph": "LLM + graph",
             "single_pass": "LLM + Lumis evidence (one shot)",
             "single_pass_verified": "one shot + Lumis verification", "lumis": "Lumis"}
    systems = [s for s in SYSTEMS if s in metrics]
    keys = [("runs", "runs"), ("top1_entity", "top-1 component"), ("top3_entity", "top-3 component"),
            ("top1_mechanism", "top-1 mechanism (rubric)"), ("top1_diagnosis", "top-1 component AND mechanism"),
            ("any_diagnosis", "right diagnosis anywhere in output"), ("concluded", "concluded"),
            ("conclusion_precision", "conclusion precision"), ("cost_usd_total", "cost USD"),
            ("seconds_median", "seconds, median")]
    scen = sorted({r["scenario"] for r in rows})
    lines = ["# Evidence ladder", "", f"Scenarios: {', '.join(scen)}. Generated by `gridcast-lumis ladder`.",
             "Mechanism uses the regex rubric in `src/gridcast_lumis/ladder.py` (RUBRIC).", "",
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
    (target / "summary.md").write_text("\n".join(lines) + "\n")
