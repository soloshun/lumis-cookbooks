"""gridcastctl — operate the GridCast estate from your laptop.

Every state-changing command goes through the same channels a real platform team uses:
GitOps commits for desired state, Kubernetes for rollouts, the model registry for models and
vendor admin APIs for third-party faults.
"""

import json
import time
from datetime import UTC, datetime
from typing import Annotated

import typer
from rich.table import Table

from gridcast.chaos.model import RunStore
from gridcast.chaos.scenarios import SCENARIOS, resolve
from gridcast.ctl import estate, gitops
from gridcast.ctl.shell import CONTEXT, compose, console, env, kubectl, paths, run

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
images_app = typer.Typer(no_args_is_help=True, help="Build and publish service images.")
job_app = typer.Typer(no_args_is_help=True, help="Run one-off estate jobs in the cluster.")
model_app = typer.Typer(no_args_is_help=True, help="Model registry operations.")
chaos_app = typer.Typer(no_args_is_help=True, help="Inject and revert reproducible incidents.")
gitops_app = typer.Typer(no_args_is_help=True, help="Inspect the estate's change history.")
config_app = typer.Typer(no_args_is_help=True, help="Change estate configuration (via GitOps).")
llm_app = typer.Typer(no_args_is_help=True, help="LLM provider (OpenRouter) checks for Lumis.")
for sub, name in ((images_app, "images"), (job_app, "job"), (model_app, "model"),
                  (chaos_app, "chaos"), (gitops_app, "gitops"), (config_app, "config"),
                  (llm_app, "llm")):
    app.add_typer(sub, name=name)

URLS = {
    "Planning API (operators)": "http://localhost:8080/docs",
    "Forecast service": "http://localhost:8081/docs",
    "Feature service": "http://localhost:8082/docs",
    "Ingestion status": "http://localhost:8083/status",
    "Grafana (dashboards, logs, traces)": "http://localhost:{GRAFANA_PORT}",
    "Prometheus": "http://localhost:{PROMETHEUS_PORT}",
    "Prefect (pipeline runs)": "http://localhost:{PREFECT_PORT}",
    "pgAdmin (SQL, ER diagram)": "http://localhost:{PGADMIN_PORT}",
    "PostgreSQL": "postgresql://gridcast_readonly@localhost:{POSTGRES_PORT}/gridcast",
    "S3 API (SeaweedFS)": "http://localhost:{S3_PORT}",
    "S3 admin UI (SeaweedFS, admin/S3_ADMIN_PASSWORD)": "http://localhost:{S3_ADMIN_UI_PORT}",
    "S3 file browser (SeaweedFS filer)": "http://localhost:{S3_FILER_UI_PORT}/buckets/",
    "S3 master UI (SeaweedFS volumes)": "http://localhost:{S3_MASTER_UI_PORT}",
}


def _urls() -> dict[str, str]:
    values = env()
    return {k: v.format(**values) for k, v in URLS.items()}


# ------------------------------------------------------------------------------ lifecycle
@app.command()
def up(
    skip_build: Annotated[bool, typer.Option(help="Reuse images already in the registry")] = False,
    skip_train: Annotated[bool, typer.Option(help="Skip model training (needs existing models)")] = False,
    backfill_days: Annotated[int, typer.Option(help="Days of history to backfill")] = 21,
    skip_hifi: Annotated[bool, typer.Option(help="Do not train the hifi model (scenario E)")] = False,
) -> None:
    """Create everything: kind cluster, platform, images, GitOps repo, data and models."""
    t0 = time.time()
    if not paths().env_file.exists():
        paths().env_file.write_text((paths().root / ".env.example").read_text())
        console.print("created .env from .env.example")
    console.rule("1/7 kind cluster")
    estate.ensure_cluster()
    console.rule("2/7 platform services (docker compose)")
    estate.platform_up()
    console.rule("3/7 images")
    if not skip_build:
        estate.build_images()
    console.rule("4/7 desired state (GitOps) and secrets")
    gitops.init()
    kubectl("apply", "-f", str(gitops.repo() / "namespaces.yaml"))
    estate.apply_secrets()
    gitops.apply()
    estate.set_vendor_truth_mode()
    console.rule("5/7 database schema")
    if not estate.run_job("db-migrate", ["db", "migrate"], db_secret="db-owner", timeout=300):
        raise typer.Exit(1)
    console.rule("6/7 history backfill and models")
    kubectl("-n", "vendors", "wait", "--for=condition=Available", "deployment", "--all",
            "--timeout=300s", capture=False)
    estate.run_job("ingest-backfill", ["ingest", "backfill", "--days", str(backfill_days)],
                   db_secret="db-ingest", timeout=900)
    if not skip_train:
        has_model = estate.psql("SELECT count(*) FROM ml.model_aliases").strip() not in ("", "0")
        if not has_model:
            estate.run_job("model-train", ["model", "train", "--profile", "standard", "--promote",
                                           "--actor", "ml-platform-bootstrap"],
                           db_secret="db-app", timeout=1800)
            if not skip_hifi:
                estate.run_job("model-train-hifi", ["model", "train", "--profile", "hifi",
                                                    "--max-train-rows", "60000",
                                                    "--actor", "ama.owusu"],
                               db_secret="db-app", timeout=2400)
    console.rule("7/7 rollout")
    estate.wait_ready()
    console.print(f"[bold green]GridCast is up[/bold green] in {time.time() - t0:.0f}s")
    urls()


@app.command()
def down(
    purge: Annotated[bool, typer.Option(help="Also delete platform volumes and local state")] = False,
) -> None:
    """Delete the kind cluster and stop the platform (data kept unless --purge)."""
    run(["kind", "delete", "cluster", "--name", "gridcast"], check=False)
    estate.platform_down(volumes=purge)
    if purge and paths().state.exists():
        import shutil

        shutil.rmtree(paths().state)
        console.print("removed .gridcast state (gitops repo, chaos runs)")


@app.command()
def urls() -> None:
    """Print the estate's local URLs."""
    table = Table("What", "Where", show_header=True, header_style="bold")
    for name, url in _urls().items():
        table.add_row(name, url)
    console.print(table)


@app.command()
def status() -> None:
    """Pods, platform containers, pipeline outcome and plan freshness at a glance."""
    if not estate.cluster_exists():
        console.print("[red]kind cluster not running[/red] — run `gridcastctl up`")
        raise typer.Exit(1)
    table = Table("namespace", "pod", "ready", "restarts", "image")
    for p in sorted(estate.pods(), key=lambda r: (r["namespace"], r["name"])):
        table.add_row(p["namespace"], p["name"], "[green]yes[/green]" if p["ready"] else "[red]no[/red]",
                      str(p["restarts"]), p["image"])
    console.print(table)
    console.print(compose("ps", "--format", "table {{.Name}}\t{{.Status}}", capture=True))
    plan = estate.http_json("http://localhost:8080/v1/plans/current")
    if isinstance(plan, dict):
        console.print(f"current plan [bold]{plan['plan_id']}[/bold] age {plan['age_seconds']:.0f}s, "
                      f"peak {plan['peak_load_mw']:.0f} MW")
    else:
        console.print("[yellow]no dispatch plan available yet[/yellow]")
    try:
        last = estate.psql("SELECT decision, validated_at, failed_checks FROM "
                           "quality.forecast_validations ORDER BY validated_at DESC LIMIT 1").strip()
        console.print(f"last validation: {last or 'none yet'}")
    except Exception:
        pass
    active = RunStore(paths().chaos_runs).active()
    if active:
        console.print(f"[magenta]active chaos run[/magenta]: {active[0].run_id}")


@app.command()
def verify() -> None:
    """End-to-end health checks across the estate and its telemetry."""
    from gridcast.ctl.verify import run_checks

    if not run_checks():
        raise typer.Exit(1)


# ------------------------------------------------------------------------------ images
@images_app.command("build")
def images_build(
    services: Annotated[list[str] | None, typer.Argument(help="Services (default: all)")] = None,
    push: bool = True,
) -> None:
    """Build runtime + per-release images and push them to the local registry."""
    estate.build_images(services, push=push)


# ------------------------------------------------------------------------------ changes
@app.command()
def deploy(
    service: str,
    version: str,
    reason: Annotated[str, typer.Option(help="Why (goes in the commit)")] = "Manual deploy.",
    author: Annotated[str, typer.Option()] = gitops.DEFAULT_AUTHOR,
) -> None:
    """Roll out a release from deploy/releases.yaml through GitOps."""
    gitops.set_image(service, version, reason=reason, author=author)
    kubectl("-n", "gridcast", "rollout", "status", f"deployment/{service}", "--timeout=300s",
            capture=False, check=False)


@app.command()
def rollback(
    service: str,
    reason: Annotated[str, typer.Option()] = "Rollback.",
    author: Annotated[str, typer.Option()] = gitops.DEFAULT_AUTHOR,
) -> None:
    """Revert the most recent GitOps commit that deployed SERVICE."""
    shas = gitops.git("log", "--format=%h", f"--grep=^deploy({service})").split()
    if not shas:
        raise SystemExit(f"no deploy commit found for {service}")
    gitops.revert(shas[0], reason=reason, author=author)
    kubectl("-n", "gridcast", "rollout", "status", f"deployment/{service}", "--timeout=300s",
            capture=False, check=False)


@app.command()
def restart(deployment: str) -> None:
    """Rolling restart of an estate deployment (picks up Secrets/ConfigMaps)."""
    kubectl("-n", "gridcast", "rollout", "restart", f"deployment/{deployment}")
    kubectl("-n", "gridcast", "rollout", "status", f"deployment/{deployment}", "--timeout=300s",
            capture=False, check=False)


@config_app.command("set")
def config_set(
    key: str, value: str,
    file: Annotated[str, typer.Option(help="File in the GitOps repo")] = "estate/config.yaml",
    restart_deployment: Annotated[str | None, typer.Option("--restart")] = None,
    reason: Annotated[str, typer.Option()] = "Configuration change.",
    author: Annotated[str, typer.Option()] = gitops.DEFAULT_AUTHOR,
) -> None:
    """Change a ConfigMap key through GitOps (optionally restart the consumer)."""
    gitops.set_config(file, key, value, reason=reason, author=author)
    if restart_deployment:
        restart(restart_deployment)


@config_app.command("weather-provider")
def weather_provider(
    provider: Annotated[str, typer.Argument(help="wx-primary or wx-secondary")],
    reason: Annotated[str, typer.Option()] = "Switch weather vendor.",
    author: Annotated[str, typer.Option()] = gitops.DEFAULT_AUTHOR,
) -> None:
    """Switch ingestion to another weather vendor (the fallback runbook)."""
    if provider not in ("wx-primary", "wx-secondary"):
        raise SystemExit("provider must be wx-primary or wx-secondary")
    gitops.set_config("estate/config.yaml", "INGEST_WEATHER_PROVIDER", provider, reason=reason,
                      author=author, scope="ingestion")
    restart("ingestion")


@config_app.command("resources")
def config_resources(
    deployment: str,
    cpu: Annotated[str | None, typer.Option(help="CPU limit, e.g. 500m")] = None,
    memory: Annotated[str | None, typer.Option(help="Memory limit, e.g. 512Mi")] = None,
    reason: Annotated[str, typer.Option()] = "Resource change.",
    author: Annotated[str, typer.Option()] = gitops.DEFAULT_AUTHOR,
) -> None:
    """Change a deployment's resource limits through GitOps."""
    gitops.set_resources(deployment, cpu=cpu, memory=memory, reason=reason, author=author)


@gitops_app.command("log")
def gitops_log(limit: int = 20) -> None:
    """Recent changes to the estate's desired state."""
    console.print(gitops.log(limit))


@gitops_app.command("show")
def gitops_show(sha: str) -> None:
    """Full diff of one change."""
    console.print(gitops.git("show", "--stat", "--patch", sha))


@gitops_app.command("sync")
def gitops_sync() -> None:
    """Apply manifest changes made under deploy/k8s (keeps deployed image tags).

    Note: this overwrites live edits (e.g. limits, ConfigMap values) in synced files; revert
    any active chaos run first."""
    gitops.sync_base()


@gitops_app.command("revert")
def gitops_revert(sha: str, reason: str = "Revert.",
                  author: str = gitops.DEFAULT_AUTHOR) -> None:
    """Revert one change and apply."""
    gitops.revert(sha, reason=reason, author=author)


# ---------------------------------------------------------------------------------- jobs
@job_app.command("migrate")
def job_migrate() -> None:
    """Apply database migrations and reference data."""
    estate.run_job("db-migrate", ["db", "migrate"], db_secret="db-owner")


@job_app.command("backfill")
def job_backfill(days: int = 10) -> None:
    """Backfill history through the vendor APIs."""
    estate.run_job("ingest-backfill", ["ingest", "backfill", "--days", str(days)],
                   db_secret="db-ingest")


@job_app.command("train")
def job_train(profile: str = "standard", promote: bool = False, actor: str = "ml-platform") -> None:
    """Train and register a model (optionally promote to production)."""
    args = ["model", "train", "--profile", profile, "--actor", actor]
    if profile == "hifi":
        args += ["--max-train-rows", "60000"]
    if promote:
        args.append("--promote")
    estate.run_job(f"model-train-{profile}", args, db_secret="db-app", timeout=2400)


@job_app.command("pipeline")
def job_pipeline() -> None:
    """Trigger one extra forecast-pipeline run now (inside the pipeline pod)."""
    kubectl("-n", "gridcast", "exec", "deploy/forecast-pipeline", "--", "gridcast", "pipeline",
            "run-once", capture=False)


# --------------------------------------------------------------------------------- model
@model_app.command("list")
def model_list() -> None:
    """Registry versions, metrics and aliases."""
    rows = estate.psql(
        "SELECT m.version, m.profile, m.algorithm, round((m.metrics->>'mape_p50')::numeric, 4), "
        "round((m.metrics->>'coverage_p10_p90')::numeric, 3), "
        "coalesce(string_agg(a.alias, ','), ''), m.created_by, m.created_at "
        "FROM ml.models m LEFT JOIN ml.model_aliases a USING (model_name, version) "
        "GROUP BY m.model_name, m.version ORDER BY m.version")
    table = Table("version", "profile", "algorithm", "MAPE", "coverage", "aliases", "by", "created")
    for line in rows.strip().splitlines():
        table.add_row(*line.split("|"))
    console.print(table)


@model_app.command("promote")
def model_promote(version: int, actor: str = "ml-platform", reason: str = "Manual promotion.") -> None:
    """Point the production alias at VERSION (the forecast service hot-reloads it)."""
    from gridcast.chaos.scenarios import promote

    promote(version, actor=actor, reason=reason)


# --------------------------------------------------------------------------------- chaos
@chaos_app.command("list")
def chaos_list() -> None:
    """Available incident scenarios."""
    table = Table("id", "title", "channel", "time to symptom")
    for s in SCENARIOS.values():
        table.add_row(s.id, s.title, s.ground_truth.change_channel, s.time_to_symptom)
    console.print(table)


@chaos_app.command("inject")
def chaos_inject(scenario: Annotated[str, typer.Argument(help="Scenario id or letter (A-I)")]) -> None:
    """Inject a scenario. Ground truth is written to .gridcast/chaos/runs only."""
    store = RunStore(paths().chaos_runs)
    if store.active():
        raise SystemExit("a chaos run is already active; `gridcastctl chaos revert` first")
    spec = resolve(scenario)
    ctx = store.new(spec)
    console.print(f"[magenta]injecting[/magenta] {spec.id}: {spec.title}")
    spec.inject(ctx)
    path = store.save(ctx, spec)
    console.print(f"run [bold]{ctx.run_id}[/bold] (ground truth: {path.relative_to(paths().root)})")
    console.print(f"symptoms expected: {spec.time_to_symptom}")


@chaos_app.command("revert")
def chaos_revert() -> None:
    """Revert the active scenario and return the estate to baseline."""
    store = RunStore(paths().chaos_runs)
    active = store.active()
    if not active:
        console.print("no active chaos run")
        return
    ctx, _ = active
    spec = SCENARIOS[ctx.scenario_id]
    console.print(f"[magenta]reverting[/magenta] {ctx.run_id}")
    spec.revert(ctx)
    ctx.status, ctx.reverted_at = "reverted", datetime.now(UTC).isoformat()
    store.save(ctx, spec)


@chaos_app.command("status")
def chaos_status() -> None:
    """Active run and history (without ground truth)."""
    table = Table("run", "scenario", "status", "started", "reverted")
    for data in RunStore(paths().chaos_runs).all():
        r = data["run"]
        table.add_row(r["run_id"], r["scenario_id"], r["status"], r["started_at"][:19],
                      (r["reverted_at"] or "")[:19])
    console.print(table)


@chaos_app.command("reveal")
def chaos_reveal(run_id: Annotated[str | None, typer.Argument()] = None) -> None:
    """Print a run's ground truth (for scoring, after diagnosis)."""
    runs = RunStore(paths().chaos_runs).all()
    match = [d for d in runs if run_id is None or d["run"]["run_id"].startswith(run_id)]
    if not match:
        raise SystemExit("no such run")
    console.print_json(json.dumps(match[-1], default=str))


@llm_app.command("check")
def llm_check() -> None:
    """Send one tiny structured-output request with OPENROUTER_MODEL to verify key + model."""
    from gridcast.ctl.llm import check

    if not check():
        raise typer.Exit(1)


@app.command()
def logs(deployment: str, namespace: str = "gridcast", tail: int = 50) -> None:
    """Tail a deployment's logs."""
    kubectl("-n", namespace, "logs", f"deploy/{deployment}", f"--tail={tail}", capture=False)


@app.command()
def psql(user: str = "gridcast_readonly") -> None:
    """Open psql against the estate database."""
    values = env()
    password = values["GRIDCAST_READONLY_PASSWORD"] if user == "gridcast_readonly" else ""
    run(["docker", "exec", "-it", "-e", f"PGPASSWORD={password}", "gridcast-postgres", "psql",
         "-U", user, "-d", "gridcast"], check=False)


def main() -> None:
    if not estate.context_ok():
        console.print(f"[dim]kube context {CONTEXT} not found yet[/dim]")
    app()
