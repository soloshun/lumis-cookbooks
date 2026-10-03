"""gridcast-lumis — run Lumis against the live GridCast estate.

  alerts                 what is firing right now (with graph entity labels)
  incident               the Incident that would be built from firing alerts
  run                    alerts -> incident -> Lumis -> report (saved under runs/)
  drill X                inject scenario X -> wait for alerts -> run -> score -> revert
  bench                  repeat the deterministic path N times on one incident; latency stats
  watch                  production-style loop: poll alerts, open an incident per new alert group
  report                 results/summary.md + charts from all drills and benchmarks
  graph                  render the discovered operational graph (SVG / terminal)
"""

import asyncio
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from lumis_sdk.core import Incident
from lumis_sdk.runtime import YamlProject
from rich.console import Console
from rich.table import Table

from gridcast_lumis import results
from gridcast_lumis.alerts import firing_alerts, incident_from_alerts, manual_incident
from gridcast_lumis.runner import GRIDCAST, PROJECT_FILE, RunResult, run_incident, save
from gridcast_lumis.scoring import latest_ground_truth, score

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
console = Console()


# ---------------------------------------------------------------------------------- helpers
def gridcastctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", "gridcastctl", *args], cwd=GRIDCAST, text=True,
                          capture_output=True, check=check, env={**_env(), "COLUMNS": "200"})


def _env() -> dict:
    import os

    return dict(os.environ)


def wait_for_alerts(timeout: int, *, since: datetime | None = None, poll: int = 15) -> list:
    """First alerts that became active after `since` (stale alerts from a previous incident
    are ignored); returns every firing alert at that moment so the incident is complete."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        alerts = firing_alerts()
        if alerts and (since is None or any(a.active_at >= since for a in alerts)):
            return alerts
        time.sleep(poll)
    return []


def wait_until_quiet(timeout: int, *, poll: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not firing_alerts():
            return True
        time.sleep(poll)
    return False


def show(result: RunResult, scored: dict | None = None) -> None:
    r, t = result.report, result.timings
    console.rule(f"[bold]{result.incident.id}[/bold]")
    console.print(f"affected: {', '.join(result.incident.affected_entities)}")
    for s in result.incident.symptoms:
        console.print(f"  symptom: {s}")
    colour = "green" if r.conclusion == "supported_diagnosis" else "yellow"
    if result.discovery.get("model"):
        console.print(f"model: {result.discovery['model']}")
    console.print(f"route [bold]{r.route}[/bold] · conclusion [{colour}]{r.conclusion}[/{colour}] "
                  f"· stop {r.stop_reason}")
    console.print(f"time: prepare {t.prepare_s:.2f}s + sql {t.external_s * 1000:.0f} ms + handle "
                  f"{t.handle_s * 1000:.0f} ms "
                  f"= {t.total_s:.2f}s · queries {r.metrics.evidence_queries} · model requests "
                  f"{r.metrics.model_requests} · tokens {r.metrics.input_tokens}/{r.metrics.output_tokens}")
    table = Table("signature", "finding", "terminal", "supporting evidence")
    for f in r.findings:
        style = {"match": "green", "no_match": "dim", "unknown": "yellow"}[f.status]
        table.add_row(f.rule_id, f"[{style}]{f.status}[/{style}]", str(f.terminal),
                      ", ".join(e.removeprefix("prometheus:") for e in f.assessment.supporting_evidence_ids))
    console.print(table)
    for a in r.assessments:
        console.print(f"[bold]{a.state}[/bold] ({', '.join(a.sources)}): {a.hypothesis.statement}")
    for s in r.suggestions:
        console.print(f"[cyan]suggestion[/cyan] (needs human review): {s.description}")
    for q in r.unresolved_questions:
        console.print(f"[dim]open question: {q}[/dim]")
    if scored and scored.get("scored"):
        console.print(f"[magenta]score[/magenta] {scored['scenario']}: [bold]{scored['outcome']}"
                      f"[/bold] (expected {scored['expected_entity']}, {scored['expected_category']})")
    if result.run_dir:
        console.print(f"[dim]saved {result.run_dir.relative_to(GRIDCAST)}[/dim]")


def build_incident(entity: list[str] | None, lookback: int) -> Incident:
    if entity:
        return manual_incident(entity, "manually opened incident", timedelta(minutes=lookback))
    incident = incident_from_alerts(firing_alerts(), lookback=timedelta(minutes=lookback))
    if incident is None:
        raise typer.BadParameter("no firing GridCast alerts; use --entity to open one manually")
    return incident


# --------------------------------------------------------------------------------- commands
@app.command()
def alerts() -> None:
    """Firing alerts and the graph entity each one points at."""
    table = Table("alert", "entity", "severity", "active since")
    for a in firing_alerts():
        table.add_row(a.name, a.entity, a.severity, a.active_at.isoformat(timespec="seconds"))
    console.print(table)


@app.command()
def incident(entity: Annotated[list[str] | None, typer.Option()] = None,
             lookback: int = 30) -> None:
    """Print the Incident that would be opened now."""
    console.print_json(build_incident(entity, lookback).model_dump_json())


@app.command()
def run(
    use_agent: Annotated[bool, typer.Option(help="Allow the paid agent if triage is inconclusive")] = False,
    entity: Annotated[list[str] | None, typer.Option(help="Open manually for these graph IDs")] = None,
    incident_file: Annotated[Path | None, typer.Option("--incident")] = None,
    lookback: Annotated[int, typer.Option(help="Minutes before the first alert")] = 30,
    label: str | None = None,
    model: Annotated[str | None, typer.Option(help="Override models.model for this run")] = None,
) -> None:
    """Alerts (or --entity/--incident) -> Lumis -> human-review report under runs/."""
    event = (Incident.model_validate_json(incident_file.read_text()) if incident_file
             else build_incident(entity, lookback))
    result = asyncio.run(run_incident(event, use_agent=use_agent, model=model))
    save(result, label=label)
    show(result)


@app.command()
def drill(
    scenario: Annotated[str, typer.Argument(help="GridCast scenario id or letter, e.g. J")],
    use_agent: bool = False,
    model: Annotated[str | None, typer.Option(help="Override models.model for this run")] = None,
    alert_timeout: Annotated[int, typer.Option(help="Seconds to wait for the first alert")] = 1200,
    settle: Annotated[int, typer.Option(
        help="Seconds to let related alerts/evidence arrive before opening (Alertmanager group_wait)")] = 60,
    keep: Annotated[bool, typer.Option(help="Leave the fault in place after scoring")] = False,
) -> None:
    """Inject a scenario, let alerts open an incident, run Lumis, score, revert."""
    if firing_alerts():
        console.print("[yellow]alerts are already firing; waiting for a quiet estate[/yellow]")
        if not wait_until_quiet(2400):
            raise typer.Exit("estate did not become quiet; fix it before drilling")
    console.print(f"injecting {scenario} …")
    gridcastctl("chaos", "inject", scenario)
    gridcastctl("job", "pipeline", check=False)  # don't wait up to 5 min for the next run
    injected = datetime.now(UTC)
    fired = wait_for_alerts(alert_timeout, since=injected)
    if not fired:
        gridcastctl("chaos", "revert", check=False)
        raise typer.Exit("no alert fired within the timeout")
    if settle:
        time.sleep(settle)
        fired = firing_alerts() or fired
    detected_s = (datetime.now(UTC) - injected).total_seconds()
    event = incident_from_alerts(fired, lookback=timedelta(minutes=30))
    assert event is not None
    result = asyncio.run(run_incident(event, use_agent=use_agent, model=model))
    save(result, label=f"drill-{scenario}")
    scored = score(result.report, latest_ground_truth(GRIDCAST))  # ground truth read only now
    show(result, scored)
    results.append(results.LOG, {
        "at": datetime.now(UTC).isoformat(), "use_agent": use_agent, "incident": event.id,
        "model": result.discovery.get("model"),
        "alerts": [a.name for a in fired], "detection_s": round(detected_s, 1), "settle_s": settle,
        "timings": result.timings.__dict__, "discovery": result.discovery, "score": scored,
    })
    if not keep:
        console.print("reverting …")
        gridcastctl("chaos", "revert")


@app.command()
def bench(
    iterations: int = 20,
    entity: Annotated[list[str] | None, typer.Option()] = None,
    incident_file: Annotated[Path | None, typer.Option("--incident")] = None,
    reuse_prepared: Annotated[bool, typer.Option(help="Discover once, then time triage only")] = False,
    scenario: Annotated[str | None, typer.Option(
        help="Inject this scenario first (waits for a quiet estate and a fresh alert), revert after")] = None,
    label: str = "deterministic",
) -> None:
    """Time the deterministic path repeatedly on the same incident window (no model calls)."""
    if scenario:
        if not wait_until_quiet(1200):
            raise typer.Exit("estate did not become quiet")
        gridcastctl("chaos", "inject", scenario)
        injected = datetime.now(UTC)
        if not wait_for_alerts(1200, since=injected):
            gridcastctl("chaos", "revert", check=False)
            raise typer.Exit("no fresh alert fired")
    base = (Incident.model_validate_json(incident_file.read_text()) if incident_file
            else build_incident(entity, 30))

    async def go() -> list[dict]:
        prepared = None
        if reuse_prepared:
            prepared = await YamlProject.from_file(PROJECT_FILE).prepare(at=base.ended_at)
        runs = []
        for i in range(iterations):
            event = base.model_copy(update={"id": f"{base.id}-b{i:03d}"})
            result = await run_incident(event, prepared=prepared)
            if i == 0:
                save(result, label=f"bench-{label}")  # keep one full report per batch for audit
            runs.append({"prepare_s": result.timings.prepare_s, "handle_s": result.timings.handle_s,
                         "total_s": result.timings.total_s, "route": result.report.route,
                         "conclusion": result.report.conclusion,
                         "queries": result.report.metrics.evidence_queries})
        return runs

    try:
        runs = asyncio.run(go())
    finally:
        if scenario:
            gridcastctl("chaos", "revert", check=False)
    routes = {r["route"] for r in runs}
    record = {"at": datetime.now(UTC).isoformat(), "label": label, "incident": base.id,
              "affected": list(base.affected_entities), "reuse_prepared": reuse_prepared,
              "route": "/".join(sorted(routes)), "queries_per_run": runs[0]["queries"], "runs": runs}
    results.append(results.BENCH, record)
    handle = [r["handle_s"] * 1000 for r in runs]
    total = [r["total_s"] for r in runs]
    console.print(f"{iterations} runs · route {record['route']} · conclusion "
                  f"{runs[0]['conclusion']} · {runs[0]['queries']} evidence queries/run")
    console.print(f"handle_incident: p50 {results.pct(handle, .5):.0f} ms · p95 "
                  f"{results.pct(handle, .95):.0f} ms · end-to-end incl. discovery: p50 "
                  f"{results.pct(total, .5):.2f} s · p95 {results.pct(total, .95):.2f} s")


@app.command()
def watch(
    interval: Annotated[int, typer.Option(help="Seconds between alert polls")] = 30,
    cooldown: Annotated[int, typer.Option(help="Seconds before re-opening the same alert group")] = 900,
    settle: Annotated[int, typer.Option(help="Group wait after a new alert group appears")] = 60,
    use_agent: bool = False,
) -> None:
    """Production-style intake: poll alerts; open one incident per new alert group."""
    seen: dict[frozenset, float] = {}
    console.print(f"watching Prometheus alerts every {interval}s (Ctrl-C to stop)")
    while True:
        fired = firing_alerts()
        group = frozenset(a.entity for a in fired)
        if fired and time.time() - seen.get(group, 0) > cooldown:
            time.sleep(settle)  # let related alerts and evidence arrive (group_wait)
            fired = firing_alerts() or fired
            seen[frozenset(a.entity for a in fired)] = seen[group] = time.time()
            event = incident_from_alerts(fired)
            assert event is not None
            result = asyncio.run(run_incident(event, use_agent=use_agent))
            save(result, label="watch")
            show(result)
        time.sleep(interval)


@app.command()
def experiment(
    name: Annotated[str, typer.Option(help="Folder under experiments/")] = "",
    scenarios: Annotated[str, typer.Option(help="Comma-separated letters")] = "J,A,F,C,D,E,G,H,I,B",
    systems: Annotated[str, typer.Option()] = "rules,single_pass,lumis",
    repeats: Annotated[int, typer.Option(help="Repeats for model-based systems")] = 2,
    rules_repeats: Annotated[int, typer.Option(help="Repeats for the deterministic tier")] = 5,
    model: Annotated[str, typer.Option()] = "deepseek/deepseek-v4-pro-0813",
) -> None:
    """Every system on the same frozen incident per scenario; raw + formatted results."""
    from gridcast_lumis.experiment import run as run_experiment

    folder = run_experiment(
        name or f"{datetime.now(UTC):%Y-%m-%d}-{model.split('/')[-1]}",
        [x.strip() for x in scenarios.split(",") if x.strip()],
        [x.strip() for x in systems.split(",") if x.strip()], repeats, rules_repeats, model, 1500)
    console.print(f"results in {folder.relative_to(GRIDCAST)}")


@app.command()
def experiment_report(folder: Path) -> None:
    """Regenerate metrics.json / summary.md / charts for an experiment folder."""
    from gridcast_lumis.experiment_report import write_report

    console.print(f"wrote {write_report(folder)}")


@app.command()
def report() -> None:
    """Write results/summary.md and charts from every recorded drill and benchmark."""
    path = results.summarize()
    console.print(f"wrote {path.relative_to(GRIDCAST)}")


@app.command()
def graph(
    fmt: Annotated[str, typer.Option("--format", help="svg | mermaid | terminal | json | dot")] = "svg",
    entity: str | None = None,
    hops: int = 2,
) -> None:
    """Render the operational graph Lumis discovers (svg/terminal/json/dot wrap `lumis graph`;
    mermaid is a logical-service view generated from the live prepared graph)."""
    if fmt == "mermaid":
        out = results.RESULTS / "operational-graph.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(asyncio.run(mermaid_graph()))
        console.print(f"wrote {out.relative_to(GRIDCAST)}")
        return
    args = ["uv", "run", "lumis", "graph", "--project", str(PROJECT_FILE), "--format", fmt]
    if entity:
        args += ["--entity", entity, "--hops", str(hops)]
    if fmt == "svg":
        out = results.RESULTS / f"graph-{(entity or 'estate').replace(':', '_')}-{int(time.time())}.svg"
        out.parent.mkdir(parents=True, exist_ok=True)
        args += ["--output", str(out)]
    subprocess.run(args, cwd=PROJECT_FILE.parent, check=True)
    if fmt == "svg":
        console.print(f"wrote {out.relative_to(GRIDCAST)}")


async def mermaid_graph() -> str:
    prepared = await YamlProject.from_file(PROJECT_FILE).prepare()
    snap = prepared.discovery.graph
    services = {e.id: e for e in snap.entities if e.kind == "service"}
    resources: dict[str, int] = {}
    for edge in snap.relationships:
        if edge.kind == "hosts" and edge.target in services:
            resources[edge.target] = resources.get(edge.target, 0) + 1

    def node(entity_id: str) -> str:
        e = services[entity_id]
        role = e.attributes.get("role", "")
        label = e.name + (f"<br/><small>{resources[entity_id]} k8s objects</small>"
                          if entity_id in resources else "")
        shape = ("[(", ")]") if role == "database" else ("{{", "}}") if role == "external-vendor" \
            else ("([", "])") if role == "consumer" else ("[", "]")
        return f'{e.name.replace("-", "_")}{shape[0]}"{label}"{shape[1]}'

    lines = ["# GridCast operational graph (as Lumis sees it)", "",
             f"Prepared {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from: "
             + ", ".join(f"{s.name} ({s.entities} entities)" for s in prepared.discovery.sources
                         if s.status == "ok") + ".",
             "Solid edges were observed in traces (service graph); dashed edges are declared only.",
             "", "```mermaid", "flowchart LR"]
    lines += [f"    {node(i)}" for i in sorted(services)]
    for edge in snap.relationships:
        if edge.source in services and edge.target in services:
            observed = "prometheus.service_graph" in edge.provenance
            arrow = "-->" if observed else "-.->"
            src, dst = services[edge.source].name, services[edge.target].name
            lines.append(f"    {src.replace('-', '_')} {arrow}|{edge.kind}| {dst.replace('-', '_')}")
    lines += ["```", ""]
    return "\n".join(lines)


def main() -> None:
    app()


if __name__ == "__main__":
    main()


