"""Local GitOps repository for the estate's desired state.

`.gridcast/gitops` is a git repository seeded from `deploy/k8s`. Every change an operator
(or the failure injector, or later Lumis) makes to the estate is a commit with an author and a
conventional message, followed by `kubectl apply -k`. This gives a real, queryable change
history (`git log`, `git diff`) — the "recent changes" evidence source for diagnosis — and a
real rollback mechanism (`git revert`).
"""

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml

from gridcast.ctl.shell import REGISTRY, console, kubectl, paths, run

DEFAULT_AUTHOR = "platform-team <platform@gridcast.dev>"


def repo() -> Path:
    return paths().gitops


def git(*args: str, capture: bool = True, check: bool = True) -> str:
    return run(["git", "-C", str(repo()), *args], capture=capture, check=check, quiet=True).stdout


def releases() -> dict:
    return yaml.safe_load((paths().deploy / "releases.yaml").read_text())["services"]


def init(force: bool = False) -> None:
    target = repo()
    if (target / ".git").exists() and not force:
        console.print(f"gitops repository already initialised at {target}")
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(paths().deploy / "k8s", target, ignore=shutil.ignore_patterns("jobs"))
    kustomization = target / "kustomization.yaml"
    text = kustomization.read_text()
    for service, spec in releases().items():
        text = _set_image(text, service, spec["default"])
    kustomization.write_text(text)
    run(["git", "init", "-q", "-b", "main", str(target)], quiet=True)
    git("config", "user.name", "gridcast-bootstrap")
    git("config", "user.email", "bootstrap@gridcast.dev")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: bootstrap GridCast estate desired state",
        "--author", "gridcast-bootstrap <bootstrap@gridcast.dev>")
    console.print(f"[green]gitops repository initialised[/green] at {target}")


def sync_base(*, author: str = DEFAULT_AUTHOR) -> str:
    """Bring manifest changes from deploy/k8s into the GitOps repo, keeping deployed tags
    and any later edits to files that did not change upstream."""
    source = paths().deploy / "k8s"
    for path in source.rglob("*.yaml"):
        rel = path.relative_to(source)
        if rel.parts[0] == "jobs" or rel.name == "kustomization.yaml":
            continue
        target = repo() / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
    return commit_and_apply("chore(platform): sync base manifests from deploy/k8s", author)


def _set_image(text: str, service: str, version: str) -> str:
    pattern = re.compile(
        rf"(- \{{ name: gridcast/{re.escape(service)},\s+newName: )\S+?(,\s+newTag: )[^ }}]+"
    )
    if not pattern.search(text):
        return text
    return pattern.sub(rf"\g<1>{REGISTRY}/gridcast/{service}\g<2>{version}", text)


def current_version(service: str) -> str | None:
    text = (repo() / "kustomization.yaml").read_text()
    match = re.search(rf"name: gridcast/{re.escape(service)},.*newTag: ([^ }}]+)", text)
    return match.group(1) if match else None


def commit_and_apply(message: str, author: str = DEFAULT_AUTHOR,
                     when: datetime | None = None) -> str:
    git("add", "-A")
    if not git("status", "--porcelain").strip():
        console.print("[yellow]no change to desired state[/yellow]")
        return git("rev-parse", "--short", "HEAD").strip()
    date = (when or datetime.now(UTC)).isoformat()
    run(["git", "-C", str(repo()), "commit", "-q", "-m", message, "--author", author,
         "--date", date], quiet=True)
    sha = git("rev-parse", "--short", "HEAD").strip()
    try:
        apply()
    except SystemExit as exc:
        # Desired state must never drift from what the cluster accepted.
        git("reset", "--hard", "HEAD~1")
        raise SystemExit(f"kubectl rejected the change; commit {sha} rolled back.\n{exc}") from None
    console.print(f"[green]committed[/green] {sha} {message.splitlines()[0]}")
    return sha


def apply() -> None:
    kubectl("apply", "-k", str(repo()), capture=True)


def set_image(service: str, version: str, *, reason: str, author: str = DEFAULT_AUTHOR) -> str:
    spec = releases()[service]
    if version not in {str(v) for v in spec["releases"]}:
        raise SystemExit(f"{service} has no release {version}; see deploy/releases.yaml")
    path = repo() / "kustomization.yaml"
    previous = current_version(service)
    path.write_text(_set_image(path.read_text(), service, version))
    changelog = spec["releases"][version].get("changelog", [])
    body = "\n".join(f"- {line}" for line in changelog)
    message = f"deploy({service}): {previous} -> {version}\n\n{reason}\n\n{body}".strip()
    sha = commit_and_apply(message, author)
    annotate(service, f"deploy {service} {version}: {reason}")
    return sha


def set_config(file: str, key: str, value: str, *, reason: str, author: str = DEFAULT_AUTHOR,
               scope: str = "config") -> str:
    path = repo() / file
    text = path.read_text()
    pattern = re.compile(rf"^(\s+{re.escape(key)}: ).*$", re.MULTILINE)
    if not pattern.search(text):
        raise SystemExit(f"{key} not found in {file}")
    path.write_text(pattern.sub(rf'\g<1>"{value}"' if value.isdigit() else rf"\g<1>{value}", text))
    return commit_and_apply(f"chore({scope}): set {key}={value}\n\n{reason}", author)


def set_resources(deployment: str, *, cpu: str | None = None, memory: str | None = None,
                  reason: str, author: str = DEFAULT_AUTHOR) -> str:
    path = repo() / "estate" / f"{deployment}.yaml"
    text = path.read_text()
    match = re.search(r'limits: \{ cpu: "?([^",]+)"?, memory: ([^ }]+) \}', text)
    if not match:
        raise SystemExit(f"no resource limits found for {deployment}")
    new_cpu, new_mem = cpu or match.group(1), memory or match.group(2)
    text = text.replace(match.group(0), f'limits: {{ cpu: "{new_cpu}", memory: {new_mem} }}')
    req = re.search(r"requests: \{ cpu: ([^,]+), memory: ([^ }]+) \}", text)
    if req:
        # Requests may not exceed limits; clamp them like a right-sizing tool would.
        req_cpu, req_mem = req.group(1), req.group(2)
        if _millicores(req_cpu) > _millicores(new_cpu):
            req_cpu = new_cpu
        if _mebibytes(req_mem) > _mebibytes(new_mem):
            req_mem = new_mem
        text = text.replace(req.group(0), f"requests: {{ cpu: {req_cpu}, memory: {req_mem} }}")
    path.write_text(text)
    sha = commit_and_apply(
        f"chore({deployment}): set limits cpu={new_cpu} memory={new_mem}\n\n{reason}", author)
    annotate(deployment, f"resources cpu={new_cpu} memory={new_mem}: {reason}")
    return sha


def _mebibytes(value: str) -> float:
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor
    return float(value) / 2**20


def _millicores(value: str) -> float:
    return float(value[:-1]) if value.endswith("m") else float(value) * 1000


def revert(sha: str, *, reason: str, author: str = DEFAULT_AUTHOR) -> str:
    git("revert", "--no-edit", "--no-commit", sha)
    subject = git("log", "-1", "--format=%s", sha).strip()
    return commit_and_apply(f'revert: "{subject}"\n\nReverts {sha}. {reason}', author)


def annotate(deployment: str, cause: str) -> None:
    kubectl("-n", "gridcast", "annotate", f"deployment/{deployment}",
            f"kubernetes.io/change-cause={cause}", "--overwrite", check=False)


def log(limit: int = 20) -> str:
    return git("log", f"-{limit}", "--date=iso-strict",
               "--format=%h  %ad  %an%n    %s")
