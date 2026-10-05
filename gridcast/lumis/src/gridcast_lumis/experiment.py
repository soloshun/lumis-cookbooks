"""Controlled experiment: every system on the same frozen incident, per injected scenario.

Protocol (per scenario):
  1. wait for a quiet estate and a 20-minute cooldown since the previous revert (so windows and
     range queries never reach the previous scenario); inject through `gridcastctl chaos`;
  2. wait for alerts that became active after the injection, plus a 120 s settle (group_wait);
  3. freeze ONE incident from those alerts (window: 10 min before the first alert -> now);
  4. run each system against that same incident while the fault is still present:
       rules        Lumis deterministic triage only (rule-tier baseline)           x rules_repeats
       single_pass  one structured LLM completion over the same evidence bundle    x repeats
       lumis        triage, then the tool-using investigator if inconclusive      x repeats
  5. only then read the hidden ground truth, score every run, revert the fault.

Raw artefacts (reports, transcripts with reasoning, receipts, prompts, usage/cost) are written
under experiments/<name>/raw/<scenario>/<system>-r<k>/; one row per run goes to results.jsonl.
"""

import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from lumis_sdk.core import Incident
from lumis_sdk.runtime import PreparedProject

from gridcast_lumis.alerts import firing_alerts, incident_from_alerts
from gridcast_lumis.investigator import openrouter_investigator
from gridcast_lumis.runner import GRIDCAST, HERE, PROJECT_FILE, load_model_credentials, load_project, load_sql_dsn
from gridcast_lumis.scoring import graph_entity, score

EXPERIMENTS = HERE / "experiments"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True).stdout.strip()


def unbounded(prepared: PreparedProject) -> PreparedProject:
    """Raise every SDK budget to its schema maximum (the experiment lets the model run)."""
    cfg = prepared.config
    inv = cfg.investigator.model_copy(update={"budget": cfg.investigator.budget.model_copy(update={
        "request_limit": 30, "tool_calls_limit": 100, "output_tokens_limit": 100000,
        "max_tool_characters": 64000, "max_total_tool_characters": 256000, "max_probes": 0})})
    budget = cfg.budget.model_copy(update={
        "max_queries": 1000, "max_hypotheses": 50, "max_model_output_tokens": 32000,
        "max_context_characters": 1000000, "query_timeout_seconds": 60,
        "source_timeout_seconds": 300, "total_timeout_seconds": 3600})
    return PreparedProject(cfg.model_copy(update={"investigator": inv, "budget": budget}),
                           prepared.base, prepared.graph, prepared.discovery)


def gridcastctl(*args: str) -> str:
    """Fail loudly: a silent inject/revert failure once left a fault active (main run, G)."""
    result = subprocess.run(["uv", "run", "gridcastctl", *args], cwd=GRIDCAST, text=True,
                            capture_output=True, env={"COLUMNS": "200", **_env()})
    if result.returncode:
        raise RuntimeError(f"gridcastctl {' '.join(args)} failed ({result.returncode}): "
                           f"{(result.stderr or result.stdout)[-500:]}")
    return result.stdout


def alerts_with_retry(attempts: int = 5) -> list:
    """The alert API can stall briefly (host wake, Prometheus GC); retry before giving up."""
    for attempt in range(attempts):
        try:
            return firing_alerts()
        except httpx.HTTPError:
            if attempt == attempts - 1:
                raise
            time.sleep(10)
    return []


class HostClock:
    """Detects host sleep: the monotonic clock stops while the Mac sleeps, wall time does not."""

    def __init__(self) -> None:
        self.wall, self.mono = time.time(), time.monotonic()

    def slept_s(self) -> float:
        return round(max(0.0, (time.time() - self.wall) - (time.monotonic() - self.mono)), 1)


def _env() -> dict:
    import os

    return dict(os.environ)


COOLDOWN_S = 1200      # minimum time between a revert and the next injection
MIN_CREDIT_USD = 1.5   # stop before a scenario if the OpenRouter balance is below this


def openrouter_credit_remaining() -> float | None:
    """Account balance (credits minus usage), or None if it cannot be read."""
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        data = httpx.get("https://openrouter.ai/api/v1/credits", timeout=10,
                         headers={"Authorization": f"Bearer {key}"}).json()["data"]
        return round(float(data["total_credits"]) - float(data["total_usage"]), 4)
    except Exception:  # noqa: BLE001
        return None
LOOKBACK = timedelta(minutes=10)   # incident window starts this long before the first alert


def wait_quiet(state: Path, timeout: int = 3600) -> bool:
    """No alert firing AND >= COOLDOWN_S since the previous revert, so that neither the incident
    window nor any range query ([5m]..[30m] at incident end) reaches the previous scenario."""
    last = json.loads(state.read_text()).get("last_revert") if state.exists() else None
    deadline = time.time() + timeout
    while time.time() < deadline:
        cooled = last is None or time.time() - last >= COOLDOWN_S
        if cooled and not alerts_with_retry():
            return True
        time.sleep(20)
    return False


def mark_revert(state: Path) -> None:
    state.write_text(json.dumps({"last_revert": time.time()}))


def wait_fresh(since: datetime, timeout: int) -> list:
    deadline = time.time() + timeout
    while time.time() < deadline:
        alerts = alerts_with_retry()
        if any(a.active_at >= since for a in alerts):
            return alerts
        time.sleep(15)
    return []


def thinking_markdown(transcript: list) -> str:
    lines = []
    for i, message in enumerate(transcript):
        for part in message.get("parts", []):
            kind = part.get("part_kind")
            if kind == "thinking":
                lines += [f"### request {i} · thinking", "", part.get("content", ""), ""]
            elif kind == "tool-call":
                lines += [f"**tool call** `{part.get('tool_name')}` "
                          f"`{json.dumps(part.get('args'), default=str)[:600]}`", ""]
            elif kind == "text":
                lines += [f"**text** {part.get('content', '')[:2000]}", ""]
    return "\n".join(lines)


def transcript_cost(transcript: list) -> float:
    total = 0.0
    for message in transcript:
        details = message.get("provider_details")
        if isinstance(details, dict) and details.get("cost") is not None:
            total += float(details["cost"])
    return total


async def run_system(system: str, prepared: PreparedProject, incident: Incident, out: Path,
                     ) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    row: dict = {"system": system}
    try:
        if system == "rules":
            report = await prepared.handle_incident(incident)
            (out / "report.json").write_text(report.model_dump_json(indent=1))
            row["report"] = report
        elif system == "lumis":
            assert prepared.config.models is not None
            retries = prepared.config.investigator.budget.validation_retries
            async with openrouter_investigator(prepared.config.models, timeout=300, retries=retries,
                                               unbounded=True) as investigator:
                try:
                    report = await prepared.handle_incident(incident, investigator=investigator)
                finally:
                    transcript = json.loads(investigator.transcript)
                    (out / "transcript.json").write_text(json.dumps(transcript, indent=1))
                    (out / "reasoning.md").write_text(thinking_markdown(transcript))
                    row["cost_usd"] = round(transcript_cost(transcript), 6)
                    row["thinking_chars"] = sum(
                        len(p.get("content", "")) for m in transcript for p in m.get("parts", [])
                        if p.get("part_kind") == "thinking")
            (out / "report.json").write_text(report.model_dump_json(indent=1))
            row["report"] = report
            if report.stop_reason not in {"agent_completed", "sufficient_terminal_signature"}:
                row["agent_error"] = report.stop_reason
        elif system == "tool_agent":
            # Baseline (iii): raw read-only tools, no registry, no acceptance, no assessment.
            from gridcast_lumis.ladder import _graph_maps, entity_of
            from gridcast_lumis.tool_agent import investigate

            assert prepared.config.models is not None
            output, messages, error = await investigate(prepared.config.models.model, incident)
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            transcript = json.loads(ModelMessagesTypeAdapter.dump_json(messages))
            (out / "transcript.json").write_text(json.dumps(transcript, indent=1))
            (out / "reasoning.md").write_text(thinking_markdown(transcript))
            hosts, names = _graph_maps(out.parent)
            candidates = [{"entity": entity_of(h.root_cause, hosts, names)
                           or entity_of(h.mechanism + " " + h.statement, hosts, names),
                           "text": f"{h.root_cause} :: {h.mechanism} :: {h.statement}"}
                          for h in (output.hypotheses if output else [])]
            (out / "candidates.json").write_text(json.dumps(
                {"candidates": candidates, "suggestions": output.suggestions if output else []}, indent=1))
            row["cost_usd"] = round(transcript_cost(transcript), 6)
            row["thinking_chars"] = sum(len(part.get("content", "")) for m in transcript
                                        for part in m.get("parts", []) if part.get("part_kind") == "thinking")
            row["tool_agent"] = {"candidates": candidates,
                                 "suggestions": output.suggestions if output else [],
                                 "tool_calls": sum(1 for m in transcript for part in m.get("parts", [])
                                                   if part.get("part_kind") == "tool-call"),
                                 "requests": sum(1 for m in transcript if m.get("kind") == "response")}
            if error:
                row["agent_error"] = error
        elif system == "single_pass":
            # Baseline (ii): the same evidence bundle as triage, then ONE structured completion
            # through the SDK's own OpenRouter adapter (same schema, same redaction). The raw
            # answer is kept and scored as-is; each candidate is then checked individually
            # against Lumis' acceptance rules and, if accepted, mechanically assessed.
            import os

            import httpx
            from lumis_sdk.core import Hypothesis
            from lumis_sdk.core.contracts import validate_hypothesis
            from lumis_sdk.models.structured import INSTRUCTIONS as SP_INSTRUCTIONS
            from lumis_sdk.models.structured import provider_schema
            from lumis_sdk.reasoning import assess
            from lumis_sdk.security.operational import redact_context

            cfg = prepared.config
            evidence_ids = tuple(dict.fromkeys(
                q for rule in cfg.checks for q in rule.hypothesis.evidence_needed))
            bundle = PreparedProject(
                cfg.model_copy(update={"initial_query_ids": evidence_ids}), prepared.base,
                prepared.graph, prepared.discovery)
            collected = await bundle.investigate(incident, generation_only=True)  # evidence only
            context = collected.context
            assert cfg.models is not None
            async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
                safe = redact_context(context)
                # Same request as the SDK OpenRouter adapter (instructions, schema, strict JSON,
                # require_parameters) but without its 100 KB response cap, with reasoning and
                # usage accounting on, and the full response kept.
                payload = {
                    "model": cfg.models.model, "max_tokens": cfg.budget.max_model_output_tokens,
                    "provider": {"require_parameters": True},
                    "reasoning": {"effort": "high", "exclude": False}, "usage": {"include": True},
                    "messages": [{"role": "system", "content": SP_INSTRUCTIONS},
                                 {"role": "user", "content": safe.model_dump_json()}],
                    "response_format": {"type": "json_schema", "json_schema": {
                        "name": "lumis_hypotheses", "strict": True, "schema": provider_schema()}},
                }
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions", json=payload,
                    headers={"Authorization": f"Bearer {os.environ.get(cfg.models.credential_env, '')}"})
                response.raise_for_status()
                body = response.json()
            (out / "raw_response.json").write_text(json.dumps(body, indent=1))
            choice = body["choices"][0]
            raw_text = choice["message"].get("content") or "{}"
            (out / "reasoning.md").write_text(choice["message"].get("reasoning") or "")
            usage = body.get("usage") or {}
            row["cost_usd"] = round(float(usage.get("cost") or 0), 6)
            row["thinking_chars"] = len(choice["message"].get("reasoning") or "")
            row["input_tokens"] = usage.get("prompt_tokens")
            row["output_tokens"] = usage.get("completion_tokens")
            row["finish_reason"] = choice.get("finish_reason")
            raw = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
            candidates = []
            for rank, item in enumerate(raw.get("hypotheses", []), 1):
                entry = {"rank": rank, "hypothesis": item}
                try:
                    hypothesis = Hypothesis.model_validate(item)
                except Exception as exc:  # schema-invalid: still scored on its stated path
                    entry.update({"accepted_by_lumis": False, "rejection": f"schema: {exc}"[:500]})
                    candidates.append(entry)
                    continue
                try:
                    validate_hypothesis(hypothesis, safe)
                    entry["accepted_by_lumis"] = True
                    entry["assessment"] = assess(hypothesis, context, ("single_pass",)).model_dump(mode="json")
                except ValueError as exc:
                    entry["accepted_by_lumis"] = False
                    entry["rejection"] = str(exc)
                candidates.append(entry)
            (out / "candidates.json").write_text(json.dumps(candidates, indent=1))
            (out / "evidence_context.json").write_text(context.model_dump_json(indent=1))
            row["single_pass"] = {"candidates": candidates,
                                  "evidence_queries": sum(1 for t in collected.trace if t.kind == "query")}
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        (out / "error.txt").write_text(traceback.format_exc())
    row["seconds"] = round(time.perf_counter() - t0, 3)
    return row


def metrics_for(row: dict, truth: dict, hosts: dict[str, str] | None = None) -> dict:
    """Metric families for one run (see experiments/README)."""
    gt = truth["ground_truth"]
    expected = graph_entity(gt["root_cause_entity"])
    m: dict = {"system": row["system"], "seconds": row["seconds"], "error": row.get("error"),
               "agent_error": row.get("agent_error"), "cost_usd": row.get("cost_usd", 0.0),
               "thinking_chars": row.get("thinking_chars", 0)}
    if "report" in row:
        report = row["report"]
        s = score(report, truth, hosts)
        # Candidate ranking: mechanically supported hypotheses, then matched (evidence-supported)
        # signatures, then unresolved, then contradicted hypotheses.
        matched = [f for f in report.findings if f.status == "match"]
        leads = [f.rule_id for f in matched]
        order = {"supported": 0, "unresolved": 2, "contradicted": 3}
        candidates = [(order[a.state], a.hypothesis.causal_path) for a in report.assessments]
        candidates += [(1, f.assessment.hypothesis.causal_path) for f in matched
                       if f.assessment.hypothesis.id not in {a.hypothesis.id for a in report.assessments}]
        paths = [path for _, path in sorted(candidates, key=lambda c: c[0])]
        hyps = list(report.assessments) + [f.assessment for f in matched
                                           if f.assessment.hypothesis.id not in
                                           {a.hypothesis.id for a in report.assessments}]
        m.update({
            "route": report.route, "conclusion": report.conclusion, "outcome": s.get("outcome"),
            "matched_signatures": leads, "hypotheses": len(hyps),
            "supported": sum(a.state == "supported" for a in hyps),
            "unsupported": sum(a.state != "supported" for a in hyps),
            "evidence_queries": report.metrics.evidence_queries,
            "tool_attempts": report.metrics.tool_attempts,
            "model_requests": report.metrics.model_requests,
            "input_tokens": report.metrics.input_tokens, "output_tokens": report.metrics.output_tokens,
            "suggestions": [x.description for x in report.suggestions],
            "unresolved_questions": list(report.unresolved_questions),
        })
    elif "tool_agent" in row:
        ta = row["tool_agent"]
        paths = [[c["entity"]] for c in ta["candidates"] if c["entity"]]
        m.update({"route": "tool_agent", "conclusion": "candidates", "outcome": None,
                  "matched_signatures": [], "hypotheses": len(ta["candidates"]),
                  "supported": 0, "unsupported": len(ta["candidates"]),
                  "model_requests": ta["requests"], "tool_attempts": ta["tool_calls"],
                  "suggestions": ta["suggestions"]})
    elif "single_pass" in row:
        cands = row["single_pass"]["candidates"]
        # Model's own ranking; non-entity tokens (e.g. ">" separators) are ignored for scoring
        # only — Lumis acceptance is recorded separately and does not get this leniency.
        paths = [[x for x in (c["hypothesis"].get("causal_path") or []) if isinstance(x, str) and ":" in x]
                 for c in cands]
        paths = [p for p in paths if p]
        states = [c.get("assessment", {}).get("state", "rejected") for c in cands]
        supported = [c for c, st in zip(cands, states, strict=True) if st == "supported"]
        m.update({"route": "single_pass",
                  "conclusion": "supported" if supported and supported[0]["rank"] == 1 else "candidates",
                  "outcome": None, "matched_signatures": [], "hypotheses": len(cands),
                  "supported": len(supported), "unsupported": len(cands) - len(supported),
                  "accepted_by_lumis": sum(c["accepted_by_lumis"] for c in cands),
                  "evidence_queries": row["single_pass"]["evidence_queries"], "model_requests": 1,
                  "input_tokens": row.get("input_tokens"), "output_tokens": row.get("output_tokens"),
                  "finish_reason": row.get("finish_reason")})
    else:
        paths = []
    # A resource node that hosts the expected service (e.g. its Kubernetes Deployment) counts as
    # that service; the literal comparison is kept as top1_literal.
    m["top1_literal"] = bool(paths) and paths[0][0] == expected
    firsts = [(hosts or {}).get(p[0], p[0]) for p in paths]
    m["top1"] = bool(firsts[:1]) and firsts[0] == expected
    m["top3"] = expected in firsts[:3]
    m["top5"] = expected in firsts[:5]
    m["path_score"] = ("correct" if firsts[:1] == [expected] else
                       "partial" if any(expected in p for p in paths) else
                       "unrelated" if paths else "none")
    m["concluded"] = m.get("conclusion") in {"supported_diagnosis", "supported"}
    m["correct_conclusion"] = m["concluded"] and m["top1"]
    m["abstained"] = not m["concluded"]
    m["abstain_ok"] = gt.get("abstain_ok", False)
    text = " ".join(m.get("suggestions", [])).lower()
    unsafe = []
    for action in gt["unsafe_actions"]:
        words = [w for w in action.lower().replace("/", " ").split() if len(w) > 3][:3]
        if words and all(w in text for w in words):
            unsafe.append(action)
    m["unsafe_suggestions_heuristic"] = unsafe
    m["actions_executed"] = 0  # the SDK kernel is read-only by construction
    return m


async def run_scenario(name: Path, scenario: str, systems: list[str], repeats: int,
                       rules_repeats: int, model: str, alert_timeout: int, log) -> list[dict]:  # noqa: ANN001
    raw = name / "raw" / scenario
    raw.mkdir(parents=True, exist_ok=True)
    state = name / "state.json"
    log(f"[{scenario}] waiting for quiet estate and {COOLDOWN_S // 60} min cooldown")
    if not wait_quiet(state):
        log(f"[{scenario}] estate never quiet; skipping")
        return []
    log(f"[{scenario}] injecting")
    (raw / "inject.txt").write_text(gridcastctl("chaos", "inject", scenario))
    # No extra pipeline run: the worker runs every 5 minutes. In the main run a second in-pod
    # process corrupted pipeline metrics and raised a spurious alert after every injection.
    injected = datetime.now(UTC)
    clock = HostClock()
    fired = wait_fresh(injected, alert_timeout)
    if not fired:
        log(f"[{scenario}] no alert within {alert_timeout}s; reverting")
        gridcastctl("chaos", "revert")
        mark_revert(state)
        return []
    time.sleep(120)  # settle: let related alerts and slower evidence arrive (group_wait)
    fired = alerts_with_retry() or fired
    incident = incident_from_alerts(fired, lookback=LOOKBACK)
    assert incident is not None
    (raw / "incident.json").write_text(incident.model_dump_json(indent=1))
    (raw / "alerts.json").write_text(json.dumps([a.__dict__ for a in fired], default=str, indent=1))
    detection_s = (datetime.now(UTC) - injected).total_seconds()
    log(f"[{scenario}] incident {incident.id} affected={list(incident.affected_entities)} "
        f"after {detection_s:.0f}s")
    prepared = unbounded(await load_project(model=model).prepare(at=incident.ended_at))
    (raw / "graph.json").write_text(prepared.discovery.model_dump_json(indent=1))
    rows = []
    plan = [(s, k) for s in systems for k in range(1, (rules_repeats if s == "rules" else repeats) + 1)]

    async def one(system: str, k: int) -> dict:
        event = incident.model_copy(update={"id": f"{incident.id}-{system}-r{k}"})
        row = await run_system(system, prepared, event, raw / f"{system}-r{k}")
        row["repeat"] = k
        log(f"[{scenario}] {system} r{k}: {row.get('error') or row.get('agent_error') or 'ok'} "
            f"({row['seconds']}s)")
        return row

    # Rules (milliseconds) run first; the model systems then run concurrently on the same frozen
    # incident. Every evidence query is pinned to the incident window, so concurrency does not
    # change what any system sees; per-run seconds are measured under this concurrency.
    for system, k in [item for item in plan if item[0] == "rules"]:
        rows.append(await one(system, k))
    models = [(system, k) for system, k in plan if system != "rules"]
    if os.environ.get("GRIDCAST_SEQUENTIAL_SYSTEMS") == "1":  # one model run at a time
        for system, k in models:
            rows.append(await one(system, k))
    else:
        rows += await asyncio.gather(*(one(system, k) for system, k in models))
    truth_runs = sorted((GRIDCAST / ".gridcast" / "chaos" / "runs").glob("*.json"))
    truth = json.loads(truth_runs[-1].read_text())          # read only after all runs
    (raw / "ground_truth.json").write_text(json.dumps(truth, indent=1))
    log(f"[{scenario}] reverting")
    (raw / "revert.txt").write_text(gridcastctl("chaos", "revert"))
    mark_revert(state)
    host_sleep_s = clock.slept_s()
    if host_sleep_s > 30:
        log(f"[{scenario}] WARNING host slept {host_sleep_s:.0f}s during this scenario; "
            "rows flagged (host_sleep_s) and should be re-run")
    results = []
    for row in rows:
        hosts = {edge.source: edge.target for edge in prepared.discovery.graph.relationships
                 if edge.kind == "hosts"}
        metrics = metrics_for(row, truth, hosts)
        metrics.update({"scenario": scenario, "scenario_id": truth["scenario"]["id"],
                        "repeat": row["repeat"], "incident": incident.id, "model":
                        model if row["system"] != "rules" else None,
                        "detection_s": round(detection_s, 1), "host_sleep_s": host_sleep_s,
                        "expected_entity": graph_entity(truth["ground_truth"]["root_cause_entity"]),
                        "expected_category": truth["ground_truth"]["category"]})
        results.append(metrics)
    return results


def manifest(name: Path, args: dict) -> None:
    sdk = HERE.parent.parent.parent / "lumis-sdk"
    (name / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECT_FILE, name / "config" / "lumis.yaml")
    shutil.copy(GRIDCAST / "infra" / "prometheus" / "rules" / "gridcast.yml",
                name / "config" / "alert-rules.yml")
    (name / "manifest.json").write_text(json.dumps({
        "started_at": datetime.now(UTC).isoformat(), "args": args,
        "lumis_sdk": {"commit": _git(sdk, "rev-parse", "HEAD"),
                      "dirty": bool(_git(sdk, "status", "--porcelain", "--", "src"))},
        "cookbooks": {"commit": _git(GRIDCAST, "rev-parse", "HEAD"),
                      "dirty": bool(_git(GRIDCAST, "status", "--porcelain"))},
        "python": platform.python_version(), "platform": platform.platform(),
        "budgets": "SDK investigator/evidence budgets raised to schema maxima; "
                   "no pydantic-ai request/tool/token caps (see experiment.unbounded)",
        "workarounds": [],
        "experiment_settings": ["investigator.py: pydantic-ai caps lifted (unbounded) and OpenRouter "
                                "usage accounting on; otherwise the SDK reference investigator",
                                "single-pass: replica of the SDK OpenRouter request with reasoning "
                                "and usage on, candidates judged individually"],
    }, indent=1))


def run(name: str, scenarios: list[str], systems: list[str], repeats: int, rules_repeats: int,
        model: str, alert_timeout: int) -> Path:
    load_model_credentials()
    load_sql_dsn()
    folder = EXPERIMENTS / name
    folder.mkdir(parents=True, exist_ok=True)
    manifest(folder, {"scenarios": scenarios, "systems": systems, "repeats": repeats,
                      "rules_repeats": rules_repeats, "model": model})
    log_file = folder / "experiment.log"

    def log(message: str) -> None:
        line = f"{datetime.now(UTC):%H:%M:%S} {message}"
        print(line, flush=True)
        with log_file.open("a") as handle:
            handle.write(line + "\n")

    for scenario in scenarios:
        remaining = openrouter_credit_remaining() if set(systems) - {"rules"} else None
        if remaining is not None:
            log(f"[{scenario}] OpenRouter balance ${remaining:.2f}")
            if remaining < MIN_CREDIT_USD:
                log(f"[{scenario}] stopping: balance below ${MIN_CREDIT_USD:.2f}; top up and resume "
                    f"with --scenarios {','.join(scenarios[scenarios.index(scenario):])}")
                break
        try:
            rows = asyncio.run(run_scenario(folder, scenario, systems, repeats, rules_repeats,
                                            model, 3600 if scenario.upper()[0] in "BLMO" else 1500, log))
        except Exception:
            log(f"[{scenario}] harness error:\n{traceback.format_exc()}")
            try:
                gridcastctl("chaos", "revert")
            except RuntimeError as exc:  # nothing active is fine; anything else must be seen
                log(f"[{scenario}] revert after error: {exc}")
            mark_revert(folder / "state.json")  # the next scenario still gets its full cooldown
            continue
        with (folder / "results.jsonl").open("a") as handle:
            for row in rows:
                handle.write(json.dumps(row, default=str) + "\n")
        from gridcast_lumis.experiment_report import write_report

        write_report(folder)
    return folder
