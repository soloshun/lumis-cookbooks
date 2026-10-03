"""Run one incident through the SDK and keep everything needed to audit or score it later."""

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lumis_sdk.core import Incident
from lumis_sdk.investigation.contracts import IncidentReport
from lumis_sdk.runtime import IncidentStore, YamlProject

from gridcast_lumis import external_evidence
from gridcast_lumis.investigator import openrouter_investigator

HERE = Path(__file__).resolve().parents[2]          # gridcast/lumis
PROJECT_FILE = HERE / "lumis.yaml"
RUNS = HERE / "runs"
GRIDCAST = HERE.parent                               # gridcast/


def load_model_credentials(keys: tuple[str, ...] = ("OPENROUTER_API_KEY",)) -> None:
    """Read only the named keys from gridcast/.env into the environment (never printed)."""
    env_file = GRIDCAST / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip() in keys and value.strip() and not os.environ.get(key.strip()):
                os.environ[key.strip()] = value.strip()


@dataclass
class Timings:
    prepare_s: float = 0.0          # discovery: declared + Kubernetes + service graph, ID binding
    external_s: float = 0.0         # TEMPORARY Loki / SQL / Prefect normalizer (see external_evidence)
    handle_s: float = 0.0           # scoping, evidence queries, triage, optional agent, report
    total_s: float = 0.0


@dataclass
class RunResult:
    incident: Incident
    report: IncidentReport
    timings: Timings
    discovery: dict = field(default_factory=dict)
    run_dir: Path | None = None


def load_project(project_file: Path = PROJECT_FILE, model: str | None = None) -> YamlProject:
    """The YAML project, optionally with a different model id (no file edits)."""
    project = YamlProject.from_file(project_file)
    if model and project.config.models is not None:
        models = project.config.models.model_copy(update={"model": model})
        project = YamlProject(project.config.model_copy(update={"models": models}), project.base)
    return project


async def run_incident(incident: Incident, *, use_agent: bool = False,
                       project_file: Path = PROJECT_FILE, prepared=None,  # noqa: ANN001
                       model: str | None = None) -> RunResult:
    if use_agent:
        load_model_credentials()
    timings = Timings()
    t0 = time.perf_counter()
    if prepared is None:
        prepared = await load_project(project_file, model).prepare(at=incident.ended_at)
    t1 = time.perf_counter()
    observations, external_errors = await asyncio.to_thread(
        external_evidence.collect, prepared.config, incident, GRIDCAST)
    te = time.perf_counter()
    models = prepared.config.models
    if use_agent and models is not None and models.provider == "openrouter":
        # TEMPORARY: inject the patched investigator (see investigator.py) through the SDK's
        # extension port; triage still runs first and the agent only runs if it is inconclusive.
        async with openrouter_investigator(
            models, timeout=prepared.config.budget.source_timeout_seconds
        ) as investigator:
            report = await prepared.handle_incident(
                incident, observations=observations, investigator=investigator)
    else:
        report = await prepared.handle_incident(
            incident, observations=observations, use_agent=use_agent)
    t2 = time.perf_counter()
    timings.prepare_s, timings.external_s = t1 - t0, te - t1
    timings.handle_s, timings.total_s = t2 - te, t2 - t0
    discovery = {s.name: s.status for s in prepared.discovery.sources}
    discovery["external_evidence_errors"] = external_errors
    discovery["model"] = prepared.config.models.model if (use_agent and prepared.config.models) else None
    discovery["entities"] = len(prepared.discovery.graph.entities)
    discovery["relationships"] = len(prepared.discovery.graph.relationships)
    return RunResult(incident, report, timings, discovery)


def save(result: RunResult, *, label: str | None = None) -> Path:
    """runs/<incident>/ {incident.json, report.json, run.json} + runs/incidents.sqlite."""
    run_dir = RUNS / result.incident.id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "incident.json").write_text(result.incident.model_dump_json(indent=2))
    (run_dir / "report.json").write_text(result.report.model_dump_json(indent=2))
    (run_dir / "run.json").write_text(json.dumps({
        "label": label, "timings": asdict(result.timings), "discovery": result.discovery,
    }, indent=2))
    IncidentStore(RUNS / "incidents.sqlite").save(result.report)
    result.run_dir = run_dir
    return run_dir
