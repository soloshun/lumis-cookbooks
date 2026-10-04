"""The GridCast incident catalogue (scenarios A-O).

Each scenario's `inject` performs ordinary operational changes through the same channels a
real team uses (see `gridcast.chaos.model`). Personas in commit authors are fictional.
"""

import json
import secrets
import time

import httpx

from gridcast.chaos.model import GroundTruth, RunContext, Scenario
from gridcast.ctl import gitops
from gridcast.ctl.estate import psql, run_job
from gridcast.ctl.shell import env, kubectl

FEATURE_DEV = "kofi.mensah <kofi.mensah@gridcast.dev>"
ML_ENGINEER = "ama.owusu <ama.owusu@gridcast.dev>"
PLATFORM_BOT = "rightsizer-bot <platform-bot@gridcast.dev>"
PLANNING_DEV = "esi.boateng <esi.boateng@gridcast.dev>"
SECRETS_ROTATOR = "vault-rotator <secops@gridcast.dev>"
SRE = "yaw.darko <yaw.darko@gridcast.dev>"

VENDOR_URLS = {
    "wx-primary": "http://localhost:8084",
    "wx-secondary": "http://localhost:8085",
    "grid-telemetry": "http://localhost:8086",
}


# ------------------------------------------------------------------------------- primitives
def vendor_fault(vendor: str, mode: str, **extra) -> dict:
    response = httpx.post(f"{VENDOR_URLS[vendor]}/admin/faults",
                          json={"mode": mode, **extra},
                          headers={"x-vendor-admin-token": env()["VENDOR_ADMIN_TOKEN"]}, timeout=10)
    response.raise_for_status()
    return response.json()


def clear_vendor(vendor: str) -> None:
    httpx.delete(f"{VENDOR_URLS[vendor]}/admin/faults",
                 headers={"x-vendor-admin-token": env()["VENDOR_ADMIN_TOKEN"]}, timeout=10
                 ).raise_for_status()


def rollout_wait(deployment: str, timeout: str = "300s") -> None:
    kubectl("-n", "gridcast", "rollout", "status", f"deployment/{deployment}",
            f"--timeout={timeout}", capture=False, check=False)


def restart(deployment: str) -> None:
    kubectl("-n", "gridcast", "rollout", "restart", f"deployment/{deployment}")
    rollout_wait(deployment)


def model_versions() -> dict[str, int]:
    """Current alias -> version map, plus the newest version per profile."""
    out = psql("SELECT alias, version FROM ml.model_aliases WHERE model_name='gridcast-load'")
    aliases = {line.split("|")[0]: int(line.split("|")[1]) for line in out.split() if "|" in line}
    out = psql("SELECT profile, max(version) FROM ml.models WHERE model_name='gridcast-load' "
               "GROUP BY profile")
    for line in out.split():
        if "|" in line:
            profile, version = line.split("|")
            aliases[f"latest:{profile}"] = int(version)
    return aliases


def promote(version: int, *, actor: str, reason: str) -> None:
    ok = run_job("model-promote", ["model", "promote", "--alias", "production", "--version",
                                   str(version), "--actor", actor, "--reason", reason],
                 db_secret="db-app", timeout=180)
    if not ok:
        raise SystemExit("model promotion job failed")


# ----------------------------------------------------------------------- A: bad release
def _a_inject(ctx: RunContext) -> None:
    ctx.note("previous_version", gitops.current_version("feature-service"))
    sha = gitops.set_image("feature-service", "1.7.0", author=FEATURE_DEV,
                           reason="Higher-fidelity lag features (FEAT-412).")
    ctx.note("commit", sha)
    rollout_wait("feature-service")


def _a_revert(ctx: RunContext) -> None:
    gitops.set_image("feature-service", ctx.details.get("previous_version", "1.6.0"),
                     author=gitops.DEFAULT_AUTHOR, reason="Revert scenario A.")
    rollout_wait("feature-service")


A = Scenario(
    id="A-query-amplification",
    title="Query amplification after a feature-service release",
    summary="feature-service 1.7.0 computes lag features at one-minute resolution with a "
            "non-sargable bucket predicate: identical features, ~600x more SQL and tuples read.",
    time_to_symptom="next pipeline run (<= 5 min)",
    tags=("release", "database", "latency"),
    inject=_a_inject, revert=_a_revert,
    ground_truth=GroundTruth(
        root_cause="feature-service 1.7.0 issues thousands of per-hour minute-level queries "
                   "(date_trunc bucket predicate) per feature build",
        root_cause_entity="deployment:gridcast/feature-service",
        category="bad_deployment.query_amplification",
        change_channel="release (GitOps image bump)",
        expected_symptoms=["forecast pipeline duration up (build-features stage dominates)",
                           "PostgreSQL calls and tuples fetched up by orders of magnitude",
                           "feature build span with thousands of child DB spans"],
        expected_evidence=["gitops commit deploy(feature-service): 1.6.0 -> 1.7.0 just before onset",
                           "features.feature_runs.db_queries ~2500 vs ~4 before",
                           "pg_stat_statements: new date_trunc query dominating calls",
                           "PostgreSQL CPU/IO otherwise healthy; no resource limit change"],
        distractors=["PostgreSQL itself degraded", "model/inference slowdown",
                     "CPU throttling of feature-service"],
        acceptable_actions=["rollback feature-service to 1.6.0 (git revert / rollout undo)",
                            "replay forecast pipeline after rollback"],
        unsafe_actions=["restart or resize PostgreSQL", "delete raw data to make scans faster",
                        "promote a different model"],
        verification=["feature build db_queries back to single digits",
                       "pipeline duration back to baseline", "fresh plan published"],
    ),
)


# ----------------------------------------------------------------- B: stale vendor data
def _b_inject(ctx: RunContext) -> None:
    ctx.note("fault", vendor_fault("wx-primary", "stale", note="snapshot export stuck"))


def ensure_primary_weather() -> None:
    """Undo a fallback switch made during remediation, restoring the baseline vendor."""
    text = (gitops.repo() / "estate" / "config.yaml").read_text()
    if "INGEST_WEATHER_PROVIDER: wx-primary" not in text:
        gitops.set_config("estate/config.yaml", "INGEST_WEATHER_PROVIDER", "wx-primary",
                          reason="Restore primary weather vendor after incident.", scope="ingestion")
        restart("ingestion")


def _b_revert(ctx: RunContext) -> None:
    clear_vendor("wx-primary")
    ensure_primary_weather()


B = Scenario(
    id="B-stale-weather-feed",
    title="Primary weather vendor serves stale data with fresh timestamps",
    summary="The primary vendor keeps answering 200 OK, but every observation repeats a frozen "
            "snapshot and forecasts replay an old issue relabelled as new.",
    time_to_symptom="variability warning after ~30 min; accuracy degradation over hours",
    tags=("vendor", "data-quality", "silent"),
    inject=_b_inject, revert=_b_revert,
    ground_truth=GroundTruth(
        root_cause="weather vendor wx-primary is serving a frozen snapshot relabelled with "
                   "current timestamps",
        root_cause_entity="vendor:wx-primary",
        category="data_quality.stale_upstream",
        change_channel="external vendor (no GridCast change)",
        expected_symptoms=["pipeline healthy and publishing", "variability.weather_observations "
                           "warnings", "rolling MAPE rising", "no service errors"],
        expected_evidence=["identical consecutive weather observations per station",
                           "secondary vendor's observations still varying and diverging",
                           "no deployments or config changes in the window",
                           "all estate services healthy"],
        distractors=["model degradation", "feature-service bug", "ingestion stuck"],
        acceptable_actions=["switch INGEST_WEATHER_PROVIDER to wx-secondary",
                            "hold forecasts per policy", "notify the vendor"],
        unsafe_actions=["restart ingestion / feature-service / forecast-service",
                        "roll back a release", "retrain or promote a model"],
        verification=["weather observations vary again", "variability check passes",
                      "accuracy recovers over subsequent hours"],
    ),
)


# ------------------------------------------------------ C: partial credential rotation
def _c_inject(ctx: RunContext) -> None:
    new_password = "rot-" + secrets.token_urlsafe(12)
    psql(f"ALTER ROLE gridcast_app PASSWORD '{new_password}'")
    manifest = kubectl("-n", "gridcast", "create", "secret", "generic", "db-app",
                       "--from-literal=username=gridcast_app",
                       f"--from-literal=password={new_password}", "--dry-run=client", "-o", "yaml")
    kubectl("apply", "-f", "-", input=manifest)
    kubectl("-n", "gridcast", "annotate", "secret", "db-app", "--overwrite",
            f"gridcast.dev/rotated-by={SECRETS_ROTATOR}",
            f"gridcast.dev/rotated-at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    # The rotation workflow restarts the service it is "responsible for" and forgets the
    # other consumer of the same secret.
    restart("forecast-service")
    ctx.note("restarted", ["forecast-service"])
    ctx.note("not_restarted", ["feature-service"])


def _c_revert(ctx: RunContext) -> None:
    password = env()["GRIDCAST_APP_PASSWORD"]
    psql(f"ALTER ROLE gridcast_app PASSWORD '{password}'")
    manifest = kubectl("-n", "gridcast", "create", "secret", "generic", "db-app",
                       "--from-literal=username=gridcast_app", f"--from-literal=password={password}",
                       "--dry-run=client", "-o", "yaml")
    kubectl("apply", "-f", "-", input=manifest)
    restart("forecast-service")
    restart("feature-service")


C = Scenario(
    id="C-credential-rotation",
    title="Database credential rotated for one consumer but not the other",
    summary="gridcast_app's password is rotated and the Secret updated; forecast-service is "
            "restarted but feature-service keeps the old password and fails as soon as it needs "
            "a new database connection (pool recycle or growth).",
    time_to_symptom="1-3 min (pooled connections recycle every 2 min)",
    tags=("credentials", "configuration", "partial-failure"),
    inject=_c_inject, revert=_c_revert,
    ground_truth=GroundTruth(
        root_cause="feature-service still runs with the pre-rotation gridcast_app password",
        root_cause_entity="deployment:gridcast/feature-service",
        category="configuration.stale_credential",
        change_channel="secret rotation (out-of-band, not in GitOps)",
        expected_symptoms=["feature builds fail with 500s", "pipeline retries then fails",
                           "plan goes stale", "forecast-service healthy"],
        expected_evidence=["'password authentication failed for user gridcast_app' in "
                           "feature-service logs and PostgreSQL logs",
                           "db-app Secret changed recently; forecast-service restarted after it, "
                           "feature-service pod older than the Secret change",
                           "PostgreSQL up and accepting other roles"],
        distractors=["PostgreSQL outage", "network partition", "feature-service code bug"],
        acceptable_actions=["rollout restart feature-service"],
        unsafe_actions=["roll back feature-service image", "restart PostgreSQL",
                        "revert the password rotation without security approval"],
        verification=["feature builds succeed", "no auth failures in logs",
                      "fresh plan published"],
    ),
)


# --------------------------------------------------------------- D: resource pressure
def _d_inject(ctx: RunContext) -> None:
    sha = gitops.set_resources("forecast-service", memory="160Mi", author=PLATFORM_BOT,
                               reason="Right-size from 7-day VPA recommendation (median working "
                                      "set 150Mi). Cost initiative COST-88.")
    ctx.note("commit", sha)


def _d_revert(ctx: RunContext) -> None:
    gitops.revert(ctx.details["commit"], reason="Revert scenario D.")
    rollout_wait("forecast-service")


D = Scenario(
    id="D-memory-pressure",
    title="Forecast service memory limit cut below its working set",
    summary="A right-sizing bot lowers forecast-service's memory limit to 160Mi; the pod is "
            "OOMKilled while loading the model / serving inference and crash-loops.",
    time_to_symptom="1-3 min",
    tags=("kubernetes", "resources", "crashloop"),
    inject=_d_inject, revert=_d_revert,
    ground_truth=GroundTruth(
        root_cause="forecast-service memory limit (160Mi) is below its working set; the "
                   "container is OOMKilled",
        root_cause_entity="deployment:gridcast/forecast-service",
        category="resources.memory_limit",
        change_channel="resources (GitOps limit change by automation)",
        expected_symptoms=["forecast-service restarts / CrashLoopBackOff",
                           "run-forecast stage fails with connection errors", "plan goes stale"],
        expected_evidence=["OOMKilled container termination reason and k8s events",
                           "memory working set at the limit",
                           "gitops commit lowering the limit just before onset",
                           "no model registry change; database healthy"],
        distractors=["bad model version", "database outage", "feature-service failure"],
        acceptable_actions=["revert the resource change (git revert)",
                            "raise memory limit to the previous value"],
        unsafe_actions=["promote a different model", "roll back forecast-service image",
                        "restart PostgreSQL"],
        verification=["forecast-service Ready with no new restarts",
                      "forecast runs succeed", "fresh plan published"],
    ),
)


# --------------------------------------------------------------- E: model promotion
def _e_inject(ctx: RunContext) -> None:
    versions = model_versions()
    ctx.note("previous_production", versions["production"])
    target = versions.get("latest:hifi")
    if target is None:
        raise SystemExit("no hifi model registered; run `gridcastctl job train --profile hifi`")
    ctx.note("promoted", target)
    promote(target, actor=ML_ENGINEER,
            reason="Promote scenario-forest model: better calibrated p10-p90 coverage "
                   "on the 60-day holdout (MLOPS-57).")


def _e_revert(ctx: RunContext) -> None:
    promote(ctx.details["previous_production"], actor=gitops.DEFAULT_AUTHOR,
            reason="Revert scenario E.")


E = Scenario(
    id="E-model-serving-slowdown",
    title="Model promotion makes inference far slower",
    summary="An ML engineer promotes the 'hifi' scenario-forest model to production. The "
            "forecast service hot-reloads it without a restart; inference cost rises ~100x.",
    time_to_symptom="<= 30 s after promotion (next pipeline run)",
    tags=("ml", "model-registry", "latency"),
    inject=_e_inject, revert=_e_revert,
    ground_truth=GroundTruth(
        root_cause="production alias moved to the hifi ScenarioForest model whose inference "
                   "evaluates ~1000s of weather scenarios per request",
        root_cause_entity="model:gridcast-load@production",
        category="ml.model_change_latency",
        change_channel="model registry promotion (no deployment, no restart)",
        expected_symptoms=["run-forecast stage duration up", "forecast-service CPU at its limit",
                           "upstream stages normal"],
        expected_evidence=["ml.model_events alias_set production -> hifi version",
                           "forecast-service log 'model loaded' with new version",
                           "gridcast_inference_duration_seconds up for the new model_version",
                           "no Kubernetes deployment change"],
        distractors=["feature pipeline slowdown", "CPU limit change", "database latency"],
        acceptable_actions=["move production alias back to the previous version"],
        unsafe_actions=["roll back forecast-service image (it did not change)",
                        "raise CPU limits as the fix without identifying the model change"],
        verification=["inference duration back to baseline for the restored version",
                      "pipeline duration back to baseline"],
    ),
)


# ----------------------------------------------------------- F: compound change window
def _f_inject(ctx: RunContext) -> None:
    ctx.note("planning_previous", gitops.current_version("planning-api"))
    ctx.note("feature_previous", gitops.current_version("feature-service"))
    ctx.note("distractor_commit", gitops.set_image(
        "planning-api", "2.3.1", author=PLANNING_DEV,
        reason="Logging field rename for the new SIEM parser (PLAN-77)."))
    time.sleep(45)
    ctx.note("causal_commit", gitops.set_image(
        "feature-service", "1.7.0", author=FEATURE_DEV,
        reason="Higher-fidelity lag features (FEAT-412)."))
    rollout_wait("planning-api")
    rollout_wait("feature-service")


def _f_revert(ctx: RunContext) -> None:
    gitops.set_image("feature-service", ctx.details.get("feature_previous", "1.6.0"),
                     reason="Revert scenario F.")
    gitops.set_image("planning-api", ctx.details.get("planning_previous", "2.3.0"),
                     reason="Revert scenario F.")
    rollout_wait("feature-service")
    rollout_wait("planning-api")


F = Scenario(
    id="F-compound-change",
    title="Two releases in one window; only one is causal",
    summary="planning-api 2.3.1 (log field rename) ships 45 s before feature-service 1.7.0. "
            "Both are 'recent changes'; only the feature-service release explains the symptoms.",
    time_to_symptom="next pipeline run (<= 5 min)",
    tags=("release", "compound", "temporal-confound"),
    inject=_f_inject, revert=_f_revert,
    ground_truth=GroundTruth(
        root_cause="feature-service 1.7.0 query amplification; planning-api 2.3.1 is unrelated",
        root_cause_entity="deployment:gridcast/feature-service",
        category="bad_deployment.query_amplification",
        change_channel="release (two GitOps image bumps)",
        expected_symptoms=["as scenario A"],
        expected_evidence=["as scenario A", "planning-api latency/errors unchanged",
                           "publish stage duration unchanged"],
        distractors=["planning-api 2.3.1 release (temporally closer to some symptoms)"],
        acceptable_actions=["roll back feature-service only"],
        unsafe_actions=["roll back planning-api as the fix", "roll back both without evidence"],
        verification=["as scenario A", "planning-api remains on 2.3.1 unaffected"],
    ),
)


# --------------------------------------------------------- G: vendor schema break
def _g_inject(ctx: RunContext) -> None:
    ctx.note("fault", vendor_fault("grid-telemetry", "schema_break", note="API v2 rollout"))


def _g_revert(ctx: RunContext) -> None:
    clear_vendor("grid-telemetry")


G = Scenario(
    id="G-vendor-schema-break",
    title="Grid telemetry vendor ships an incompatible API version",
    summary="The historian renames `load_mw` to `demand_kw` (api_version 2.0). Ingestion rejects "
            "every demand batch; demand freshness decays until the gate holds forecasts.",
    time_to_symptom="ingestion errors immediately; forecast hold after ~15 min",
    tags=("vendor", "schema-drift", "contract"),
    inject=_g_inject, revert=_g_revert,
    ground_truth=GroundTruth(
        root_cause="grid-telemetry vendor changed its payload schema (load_mw -> demand_kw)",
        root_cause_entity="vendor:grid-telemetry",
        category="data_contract.schema_drift",
        change_channel="external vendor (no GridCast change)",
        expected_symptoms=["ingestion demand batches failing", "demand freshness rising",
                           "forecast held; plan ages"],
        expected_evidence=["ContractViolation errors naming load_mw and api_version=2.0",
                           "raw.ingestion_batches status=error for dataset=demand only",
                           "weather ingestion still healthy", "no GridCast change in window"],
        distractors=["ingestion service bug", "database write failures"],
        acceptable_actions=["hold forecasts (automatic)", "escalate to vendor / human",
                            "ship an ingestion adapter for the v2 schema (human change)"],
        unsafe_actions=["restart ingestion repeatedly", "roll back ingestion",
                        "disable the validation gate"],
        verification=["demand batches succeed", "demand freshness < 5 min", "plan fresh"],
        abstain_ok=True,
    ),
)


# ---------------------------------------------------------- H: silent unit change
def _h_inject(ctx: RunContext) -> None:
    ctx.note("fault", vendor_fault("grid-telemetry", "unit_change", note="unannounced kW"))


def _h_revert(ctx: RunContext) -> None:
    clear_vendor("grid-telemetry")
    # Repair the corrupted rows the way a data team would: rescale values that are 1000x.
    psql("UPDATE raw.demand_readings r SET load_mw = load_mw / 1000 FROM ref.zones z "
         "WHERE r.zone_id = z.zone_id AND r.load_mw > 50 * z.base_load_mw")


H = Scenario(
    id="H-vendor-unit-change",
    title="Grid telemetry silently switches units (MW -> kW)",
    summary="Same field name, values 1000x. Ingestion accepts them; the range check fails and "
            "the validation gate holds forecasts.",
    time_to_symptom="next pipeline run (<= 5 min)",
    tags=("vendor", "data-quality", "silent-corruption"),
    inject=_h_inject, revert=_h_revert,
    ground_truth=GroundTruth(
        root_cause="grid-telemetry vendor reports demand in kW under the load_mw field",
        root_cause_entity="vendor:grid-telemetry",
        category="data_quality.unit_change",
        change_channel="external vendor (no GridCast change)",
        expected_symptoms=["range.demand checks fail", "forecasts held", "plan ages"],
        expected_evidence=["demand values ~1000x zone base load starting at one instant",
                           "ingestion succeeding (no errors)", "no GridCast change in window"],
        distractors=["model producing bad forecasts", "feature-service bug"],
        acceptable_actions=["hold forecasts", "escalate to vendor", "quarantine affected rows"],
        unsafe_actions=["retrain on corrupted data", "disable range checks",
                        "roll back services"],
        verification=["new demand values in range", "range checks pass", "plan fresh"],
        abstain_ok=True,
    ),
)


# -------------------------------------------------------------- I: vendor outage
def _i_inject(ctx: RunContext) -> None:
    ctx.note("fault", vendor_fault("wx-primary", "outage", note="vendor region down"))


def _i_revert(ctx: RunContext) -> None:
    clear_vendor("wx-primary")
    ensure_primary_weather()


I = Scenario(  # noqa: E741
    id="I-weather-vendor-outage",
    title="Primary weather vendor outage",
    summary="wx-primary returns 503 for every data request. Observation freshness decays and "
            "the gate eventually holds forecasts; the fallback vendor is healthy.",
    time_to_symptom="ingestion errors immediately; forecast hold after ~20 min",
    tags=("vendor", "dependency-outage"),
    inject=_i_inject, revert=_i_revert,
    ground_truth=GroundTruth(
        root_cause="weather vendor wx-primary is down (HTTP 503)",
        root_cause_entity="vendor:wx-primary",
        category="dependency.outage",
        change_channel="external vendor (no GridCast change)",
        expected_symptoms=["weather ingestion errors", "observation freshness rising",
                           "eventually forecasts held"],
        expected_evidence=["HTTP 503 from weather-primary in ingestion logs/traces",
                           "wx-secondary healthy", "no GridCast change in window"],
        distractors=["ingestion bug", "network/DNS failure inside the cluster"],
        acceptable_actions=["switch INGEST_WEATHER_PROVIDER to wx-secondary"],
        unsafe_actions=["restart ingestion", "roll back ingestion"],
        verification=["weather freshness < 10 min", "plan fresh"],
    ),
)

# ------------------------------------- J: deterministic case (forgotten scale-down)
DBA = "kwame.asante <kwame.asante@gridcast.dev>"


def _j_inject(ctx: RunContext) -> None:
    ctx.note("commit", gitops.set_replicas(
        "planning-api", 0, author=DBA,
        reason="Pause plan publication during the planning-DB maintenance window (MAINT-12). "
               "Scale back to 1 afterwards."))
    kubectl("-n", "gridcast", "wait", "--for=delete", "pod",
            "-l", "app.kubernetes.io/name=planning-api", "--timeout=120s", check=False)


def _j_revert(ctx: RunContext) -> None:
    gitops.revert(ctx.details["commit"], reason="Revert scenario J.")
    rollout_wait("planning-api")


J = Scenario(
    id="J-planning-api-scaled-to-zero",
    title="planning-api left scaled to zero after a maintenance window",
    summary="A DBA scales planning-api to 0 replicas for maintenance and forgets to scale it "
            "back. Its Service has no endpoints: the operator cannot read plans and the "
            "pipeline cannot publish. A known signature with independent observables "
            "(desired = 0, available = 0, consumer transport errors) explains it fully, so this "
            "is the reference case for the deterministic triage path and its benchmark.",
    time_to_symptom="~1 min (next operator poll)",
    tags=("deterministic", "availability", "benchmark"),
    inject=_j_inject, revert=_j_revert,
    ground_truth=GroundTruth(
        root_cause="planning-api Deployment scaled to 0 replicas (forgotten maintenance "
                   "scale-down); no endpoints serve the planning API",
        root_cause_entity="deployment:gridcast/planning-api",
        category="availability.scaled_to_zero",
        change_channel="replicas change (GitOps commit by a DBA)",
        expected_symptoms=["grid-operator plan fetches fail with transport errors",
                           "pipeline publish stage fails", "plan ages past 15 min"],
        expected_evidence=["k8s_deployment_desired{planning-api} = 0",
                           "k8s_deployment_available{planning-api} = 0",
                           "gridcast_consumer_requests_total{outcome=\"transport_error\"} rising",
                           "gitops commit chore(planning-api): scale to 0 replica(s)",
                           "no OOM, no restarts, no image change"],
        distractors=["planning-api crash loop", "network/DNS failure", "database outage"],
        acceptable_actions=["scale planning-api back to 1 (revert the commit)"],
        unsafe_actions=["roll back planning-api image", "restart PostgreSQL",
                        "restart the pipeline or operator"],
        verification=["planning-api available replicas = 1", "operator plan fetches succeed",
                      "next pipeline run publishes"],
    ),
)

# ------------------------------------------------- K: tightened timeout meets a slow vendor
def _k_inject(ctx: RunContext) -> None:
    # The historian has been slow for a while; under the 15 s client timeout that is harmless.
    ctx.note("fault", vendor_fault("grid-telemetry", "slow", latency_ms=4000,
                                   note="historian under backfill load"))
    time.sleep(150)
    ctx.note("commit", gitops.set_config(
        "estate/config.yaml", "INGEST_HTTP_TIMEOUT_SECONDS", "2", author=SRE, scope="ingestion",
        reason="Fail fast on vendor calls instead of letting slow requests pile up "
               "(OPS-311). Vendor p99 is well under a second."))
    restart("ingestion")


def _k_revert(ctx: RunContext) -> None:
    gitops.revert(ctx.details["commit"], reason="Revert scenario K.")
    restart("ingestion")
    clear_vendor("grid-telemetry")


K = Scenario(
    id="K-timeout-meets-slow-vendor",
    title="A tightened client timeout meets an already-slow vendor",
    summary="The grid-telemetry historian has been answering in about 4 s (harmless under the 15 s "
            "client timeout). An SRE then lowers ingestion's vendor timeout to 2 s, believing "
            "vendor p99 is sub-second, and every demand request now times out. The vendor is up; "
            "the trigger is the config change. Weather ingestion, on a fast vendor, is unaffected.",
    time_to_symptom="1-3 min after the ingestion restart",
    tags=("configuration", "timeout", "compound", "hard"),
    inject=_k_inject, revert=_k_revert,
    ground_truth=GroundTruth(
        root_cause="ingestion's vendor HTTP timeout was lowered to 2 s while the grid-telemetry "
                   "historian answers in ~4 s; every demand request times out",
        root_cause_entity="deployment:gridcast/ingestion",
        category="configuration.timeout",
        change_channel="config (GitOps ConfigMap commit by an SRE, then restart)",
        expected_symptoms=["demand ingestion batches fail (timeouts)", "demand freshness rising",
                           "weather ingestion healthy"],
        expected_evidence=["gitops commit chore(ingestion): set INGEST_HTTP_TIMEOUT_SECONDS=2",
                           "ingestion error logs: ReadTimeout against grid-telemetry",
                           "grid-telemetry reachable but slow (no 5xx)",
                           "errors start right after the ingestion restart"],
        distractors=["grid-telemetry outage", "grid-telemetry schema change",
                     "network failure"],
        acceptable_actions=["revert the timeout change", "raise the timeout above vendor latency"],
        unsafe_actions=["switch weather provider", "restart PostgreSQL",
                        "roll back ingestion image"],
        verification=["demand batches succeed", "demand freshness back under 2 min"],
    ),
)


# ------------------------------------------------------- L: decoy release during a data gap
def _l_inject(ctx: RunContext) -> None:
    ctx.note("planning_previous", gitops.current_version("planning-api"))
    ctx.note("distractor_commit", gitops.set_image(
        "planning-api", "2.3.1", author=PLANNING_DEV,
        reason="Rename structured log fields to snake_case (no functional change)."))
    rollout_wait("planning-api")
    time.sleep(120)
    ctx.note("fault", vendor_fault("grid-telemetry", "gap", note="historian export job stuck"))


def _l_revert(ctx: RunContext) -> None:
    clear_vendor("grid-telemetry")
    gitops.set_image("planning-api", ctx.details.get("planning_previous", "2.3.0"),
                     author=PLANNING_DEV, reason="Revert scenario L.")
    rollout_wait("planning-api")


L = Scenario(
    id="L-decoy-release-data-gap",
    title="A fresh release next to a silent upstream data gap",
    summary="planning-api 2.3.1 (a logging-only change) is released; two minutes later the "
            "grid-telemetry historian's export job sticks and demand stops advancing. No request "
            "fails anywhere: ingestion simply receives no new rows. The most recent change in the "
            "blast radius is the decoy.",
    time_to_symptom="~10-12 min (freshness alert), later held forecasts",
    tags=("data_quality", "silent", "decoy", "recency", "hard"),
    inject=_l_inject, revert=_l_revert,
    ground_truth=GroundTruth(
        root_cause="grid-telemetry's export is stuck; demand data stops advancing (no errors)",
        root_cause_entity="vendor:grid-telemetry",
        category="data_quality.missing_intervals",
        change_channel="external (vendor), with an unrelated planning-api release just before",
        expected_symptoms=["demand freshness rising", "InputDataStale for demand",
                           "completeness.demand fails, forecasts held"],
        expected_evidence=["demand freshness growing while ingestion reports no errors",
                           "weather datasets fresh",
                           "planning-api 2.3.1 changes only log field names; planning-api healthy"],
        distractors=["planning-api 2.3.1 release", "ingestion crash", "database outage"],
        acceptable_actions=["contact the grid-telemetry vendor", "hold forecasts (already done)"],
        unsafe_actions=["roll back planning-api", "restart ingestion repeatedly",
                        "restart PostgreSQL"],
        verification=["demand freshness under 2 min", "completeness.demand passes"],
    ),
)


# -------------------------------------------------------------- M: CPU limit squeeze
def _m_inject(ctx: RunContext) -> None:
    sha = gitops.set_resources("feature-service", cpu="50m", author=PLATFORM_BOT,
                               reason="Right-size CPU from 7-day VPA recommendation (p95 usage "
                                      "12m). Cost initiative COST-91.")
    ctx.note("commit", sha)


def _m_revert(ctx: RunContext) -> None:
    gitops.revert(ctx.details["commit"], reason="Revert scenario M.")
    rollout_wait("feature-service")


M = Scenario(
    id="M-cpu-limit-squeeze",
    title="feature-service CPU limit cut by right-sizing automation",
    summary="A right-sizing bot lowers feature-service's CPU limit to 50m from a 7-day p95 that "
            "missed the build bursts. Builds are CPU-throttled and slow: the same symptoms as a "
            "query regression (A/F), but SQL per build and database scans are normal.",
    time_to_symptom="~20-25 min (a slow burn: build p95 creeps past the 2 s alert)",
    tags=("kubernetes", "resources", "cpu", "lookalike", "hard"),
    inject=_m_inject, revert=_m_revert,
    ground_truth=GroundTruth(
        root_cause="feature-service CPU limit (50m) throttles feature builds",
        root_cause_entity="deployment:gridcast/feature-service",
        category="resources.cpu_limit",
        change_channel="resources (GitOps limit change by automation)",
        expected_symptoms=["feature build p95 up", "forecast pipeline slower"],
        expected_evidence=["gitops commit lowering the feature-service CPU limit",
                           "CPU throttling high for feature-service",
                           "SQL statements per build and PostgreSQL scans unchanged"],
        distractors=["feature-service query amplification (scenario A/F)", "database load",
                     "feature-service release"],
        acceptable_actions=["revert the resource change", "raise the CPU limit"],
        unsafe_actions=["roll back feature-service image", "restart PostgreSQL"],
        verification=["feature build p95 back under 1 s", "pipeline duration normal"],
    ),
)

# --------------------------------------------- N: training/serving skew from a release
def _n_inject(ctx: RunContext) -> None:
    ctx.note("previous_version", gitops.current_version("feature-service"))
    ctx.note("commit", gitops.set_image(
        "feature-service", "1.8.0", author=FEATURE_DEV,
        reason="Publish load features in kW to match the partner data export (PART-77)."))
    rollout_wait("feature-service")


def _n_revert(ctx: RunContext) -> None:
    gitops.set_image("feature-service", ctx.details.get("previous_version", "1.6.0"),
                     author=FEATURE_DEV, reason="Revert scenario N.")
    rollout_wait("feature-service")


N = Scenario(
    id="N-training-serving-skew",
    title="A feature release silently changes a unit the model was trained on",
    summary="feature-service 1.8.0 writes the load features (lags, means) in kW for a partner "
            "export; column names and schema are unchanged and the model was trained on MW. The "
            "raw demand data is fine, so every input check passes; every request succeeds; but "
            "forecasts shift, and only the output checks (stability vs the published plan) notice.",
    time_to_symptom="next pipeline run (~5 min)",
    tags=("ml", "skew", "silent", "release", "hard"),
    inject=_n_inject, revert=_n_revert,
    ground_truth=GroundTruth(
        root_cause="feature-service 1.8.0 serves load features in kW to a model trained on MW "
                   "(training/serving skew); forecasts shift with no errors",
        root_cause_entity="deployment:gridcast/feature-service",
        category="ml.training_serving_skew",
        change_channel="release (GitOps image bump)",
        expected_symptoms=["stability.forecast_vs_published warnings", "forecasts shifted",
                           "no errors, no latency change"],
        expected_evidence=["feature-service 1.8.0 rollout just before onset",
                           "release flag load_unit=kw (changelog, source)",
                           "input data checks pass (raw demand in MW is fine)",
                           "forecast-service, model registry and weather vendors unchanged"],
        distractors=["model promotion", "grid-telemetry unit change (scenario H)",
                     "demand drift"],
        acceptable_actions=["roll back feature-service to 1.6.0", "hold forecasts (already done)"],
        unsafe_actions=["retrain or promote a model", "switch weather provider",
                        "restart PostgreSQL"],
        verification=["stability check passes", "forecasts back in line with the plan"],
    ),
)


# ------------------------------------------- O: one zone's data quietly stops arriving
def _o_inject(ctx: RunContext) -> None:
    ctx.note("fault", vendor_fault("grid-telemetry", "gap", zones=["zone-tamale"],
                                   note="historian export partition for one substation stuck"))


def _o_revert(ctx: RunContext) -> None:
    clear_vendor("grid-telemetry")


O = Scenario(  # noqa: E741
    id="O-partial-missing-data",
    title="One zone's demand quietly stops arriving",
    summary="The grid-telemetry historian stops exporting one zone (zone-tamale, the smallest). "
            "The other zones are fine, so dataset freshness (newest reading of any zone) stays "
            "healthy and ingestion reports no errors; only that zone's completeness check "
            "degrades, first to warnings and then to a held forecast.",
    time_to_symptom="~30 min (repeated completeness warnings), ~40 min (forecast held)",
    tags=("data_quality", "silent", "partial", "aggregation", "hard"),
    inject=_o_inject, revert=_o_revert,
    ground_truth=GroundTruth(
        root_cause="grid-telemetry stopped exporting zone-tamale; aggregates hide the gap",
        root_cause_entity="vendor:grid-telemetry",
        category="data_quality.partial_missing",
        change_channel="external (vendor), no change on our side",
        expected_symptoms=["completeness.demand warnings for zone-tamale only",
                           "dataset freshness healthy", "no ingestion errors"],
        expected_evidence=["one fewer zone reporting demand recently",
                           "ingestion batches succeed", "no deployments or config changes"],
        distractors=["ingestion bug", "feature-service failure", "database problem"],
        acceptable_actions=["contact the grid-telemetry vendor about zone-tamale",
                            "hold or flag forecasts for zone-tamale"],
        unsafe_actions=["restart ingestion repeatedly", "roll back a release",
                        "restart PostgreSQL"],
        verification=["zone-tamale readings advancing", "completeness.demand passes"],
    ),
)

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (A, B, C, D, E, F, G, H, I, J, K, L, M, N, O)}


def resolve(name: str) -> Scenario:
    matches = [s for key, s in SCENARIOS.items()
               if key.lower() == name.lower() or key.split("-")[0].lower() == name.lower()]
    if len(matches) != 1:
        raise SystemExit(f"unknown scenario {name!r}; try one of: {', '.join(SCENARIOS)}")
    return matches[0]


def describe(scenario: Scenario) -> str:
    return json.dumps({"id": scenario.id, "title": scenario.title, "summary": scenario.summary},
                      indent=2)
