"""Lifecycle of the local estate: cluster, platform, images, secrets, jobs and health."""

import json
import string
import subprocess
import time
from datetime import UTC, datetime

import httpx

from gridcast.ctl import gitops
from gridcast.ctl.shell import CLUSTER, CONTEXT, REGISTRY, compose, console, env, kubectl, paths, run

RUNTIME_EXTRAS = {"base": "", "ml": "--extra ml", "pipeline": "--extra ml --extra pipeline"}
NAMESPACES = ("gridcast", "vendors", "observability")
DB_SECRETS = {
    "db-owner": ("gridcast_owner", "GRIDCAST_OWNER_PASSWORD"),
    "db-ingest": ("gridcast_ingest", "GRIDCAST_INGEST_PASSWORD"),
    "db-app": ("gridcast_app", "GRIDCAST_APP_PASSWORD"),
    "db-planning": ("gridcast_planning", "GRIDCAST_PLANNING_PASSWORD"),
    "db-pipeline": ("gridcast_pipeline", "GRIDCAST_PIPELINE_PASSWORD"),
    "db-readonly": ("gridcast_readonly", "GRIDCAST_READONLY_PASSWORD"),
}


# --------------------------------------------------------------------------- cluster/platform
def cluster_exists() -> bool:
    out = run(["kind", "get", "clusters"], capture=True, check=False, quiet=True).stdout
    return CLUSTER in out.split()


def ensure_cluster() -> None:
    if cluster_exists():
        console.print(f"kind cluster [bold]{CLUSTER}[/bold] already running")
    else:
        run(["kind", "create", "cluster", "--config", str(paths().infra / "kind" / "cluster.yaml")])
    # Let containerd on the node pull `localhost:5001/...` from the registry container.
    node = f"{CLUSTER}-control-plane"
    run(["docker", "exec", node, "mkdir", "-p", "/etc/containerd/certs.d/localhost:5001"], quiet=True)
    run(["docker", "exec", "-i", node, "sh", "-c",
         "cat > /etc/containerd/certs.d/localhost:5001/hosts.toml"],
        input='[host."http://gridcast-registry:5000"]\n', quiet=True)


def platform_up() -> None:
    compose("up", "-d", "--wait", "--wait-timeout", "240")


def platform_down(volumes: bool = False) -> None:
    compose("down", *(["--volumes"] if volumes else []), check=False)


# ------------------------------------------------------------------------------------- images
def revision() -> str:
    root = paths().root
    sha = run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture=True, check=False,
              quiet=True).stdout.strip() or "unknown"
    dirty = run(["git", "-C", str(root), "status", "--porcelain", "--", "."], capture=True,
                check=False, quiet=True).stdout.strip()
    return f"{sha}-dirty" if dirty else sha


def build_images(services: list[str] | None = None, push: bool = True) -> None:
    root = paths().root
    catalog = gitops.releases()
    selected = {k: v for k, v in catalog.items() if not services or k in services}
    for profile in sorted({spec["runtime"] for spec in selected.values()}):
        tag = f"{REGISTRY}/gridcast/runtime:{profile}"
        run(["docker", "build", "-q", "-f", str(root / "deploy/docker/Dockerfile"),
             "--target", "runtime", "--build-arg", f"EXTRAS={RUNTIME_EXTRAS[profile]}",
             "-t", tag, str(root)])
    rev, created = revision(), datetime.now(UTC).isoformat(timespec="seconds")
    for service, spec in selected.items():
        for version, release in spec["releases"].items():
            manifest = {"service": service, "version": str(version), "revision": rev,
                        "built_at": created, "changelog": release.get("changelog", []),
                        "flags": release.get("flags", {})}
            tag = f"{REGISTRY}/gridcast/{service}:{version}"
            run(["docker", "build", "-q", "-f", str(root / "deploy/docker/Dockerfile.release"),
                 "--build-arg", f"RUNTIME_IMAGE={REGISTRY}/gridcast/runtime:{spec['runtime']}",
                 "--build-arg", f"SERVICE={service}", "--build-arg", f"VERSION={version}",
                 "--build-arg", f"REVISION={rev}", "--build-arg", f"CREATED={created}",
                 "--build-arg", f"RELEASE_JSON={json.dumps(manifest)}",
                 "-t", tag, str(root / "deploy/docker")])
            if push:
                push_image(tag)


def push_image(tag: str, attempts: int = 4) -> None:
    for attempt in range(1, attempts + 1):
        result = run(["docker", "push", "-q", tag], check=False, capture=True)
        if result.returncode == 0:
            return
        console.print(f"[yellow]push failed (attempt {attempt}/{attempts}), retrying[/yellow]")
        time.sleep(3 * attempt)
    raise SystemExit(f"could not push {tag}: {result.stderr.strip()[-300:]}")


# ------------------------------------------------------------------------------------ secrets
def _secret(namespace: str, name: str, data: dict[str, str]) -> None:
    args = ["-n", namespace, "create", "secret", "generic", name, "--dry-run=client", "-o", "yaml"]
    args += [f"--from-literal={k}={v}" for k, v in data.items()]
    manifest = kubectl(*args)
    kubectl("apply", "-f", "-", input=manifest)


def apply_secrets() -> None:
    values = env()
    for name, (user, var) in DB_SECRETS.items():
        _secret("gridcast", name, {"username": user, "password": values[var]})
    _secret("gridcast", "s3-credentials", {"access_key": values["S3_ACCESS_KEY"],
                                            "secret_key": values["S3_SECRET_KEY"]})
    _secret("vendors", "vendor-admin", {"token": values["VENDOR_ADMIN_TOKEN"]})


def set_vendor_truth_mode() -> None:
    mode = env().get("WEATHER_TRUTH_MODE", "hybrid")
    kubectl("-n", "vendors", "patch", "configmap", "vendor-runtime", "--type", "merge", "-p",
            json.dumps({"data": {"WEATHER_VENDOR_TRUTH_MODE": mode,
                                 "GRID_TELEMETRY_TRUTH_MODE": mode}}))


# --------------------------------------------------------------------------------------- jobs
def run_job(kind: str, args: list[str], *, db_secret: str, timeout: int = 900) -> bool:
    stamp = datetime.now(UTC).strftime("%m%d%H%M%S")
    name = f"{kind}-{stamp}"
    template = string.Template((paths().deploy / "k8s/jobs/job.yaml.tmpl").read_text())
    version = gitops.releases()["jobs"]["default"]
    manifest = template.substitute(
        JOB_NAME=name, JOB_KIND=kind, IMAGE=f"{REGISTRY}/gridcast/jobs:{version}",
        ARGS=json.dumps(args), DB_SECRET=db_secret,
    )
    kubectl("apply", "-f", "-", input=manifest)
    console.print(f"job [bold]{name}[/bold]: gridcast {' '.join(args)}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = json.loads(kubectl("-n", "gridcast", "get", "job", name, "-o", "json"))["status"]
        if status.get("succeeded"):
            console.print(f"[green]job {name} succeeded[/green]")
            return True
        if status.get("failed", 0) > 2:
            break
        time.sleep(5)
    console.print(f"[red]job {name} did not succeed[/red]; logs:")
    console.print(kubectl("-n", "gridcast", "logs", f"job/{name}", "--tail", "40", check=False))
    return False


# ------------------------------------------------------------------------------------- health
def wait_ready(namespaces: tuple[str, ...] = ("gridcast", "vendors"), timeout: int = 600) -> None:
    for ns in namespaces:
        kubectl("-n", ns, "wait", "--for=condition=Available", "deployment", "--all",
                f"--timeout={timeout}s", capture=False, check=False)
    kubectl("-n", "observability", "rollout", "status", "daemonset/otel-collector",
            "--timeout=300s", capture=False, check=False)


def pods() -> list[dict]:
    out = kubectl("get", "pods", "-A", "-l", "app.kubernetes.io/part-of=gridcast", "-o", "json")
    rows = []
    for pod in json.loads(out)["items"]:
        if pod["status"].get("phase") == "Succeeded":
            continue  # finished Jobs
        statuses = pod["status"].get("containerStatuses", [])
        rows.append({
            "namespace": pod["metadata"]["namespace"],
            "name": pod["metadata"]["name"],
            "phase": pod["status"].get("phase"),
            "ready": all(s.get("ready") for s in statuses) if statuses else False,
            "restarts": sum(s.get("restartCount", 0) for s in statuses),
            "image": statuses[0]["image"].rsplit("/", 1)[-1] if statuses else "",
        })
    return rows


def http_json(url: str, **kw) -> dict | list | None:
    try:
        response = httpx.get(url, timeout=10, **kw)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def psql(sql: str, *, user: str | None = None) -> str:
    values = env()
    args = ["docker", "exec", "-i", "-e", f"PGPASSWORD={values['POSTGRES_PASSWORD']}",
            "gridcast-postgres", "psql", "-v", "ON_ERROR_STOP=1", "-At",
            "-U", user or values["POSTGRES_USER"], "-d", "gridcast", "-c", sql]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def context_ok() -> bool:
    return run(["kubectl", "config", "get-contexts", CONTEXT], capture=True, check=False,
               quiet=True).returncode == 0
