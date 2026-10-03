"""Process, path and environment helpers for gridcastctl (runs on the developer machine)."""

import os
import shlex
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rich.console import Console

console = Console()
CLUSTER = "gridcast"
CONTEXT = f"kind-{CLUSTER}"
REGISTRY = "localhost:5001"


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def infra(self) -> Path:
        return self.root / "infra"

    @property
    def deploy(self) -> Path:
        return self.root / "deploy"

    @property
    def state(self) -> Path:
        return self.root / ".gridcast"

    @property
    def gitops(self) -> Path:
        return self.state / "gitops"

    @property
    def chaos_runs(self) -> Path:
        return self.state / "chaos"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"


@lru_cache
def paths() -> Paths:
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "deploy" / "releases.yaml").is_file():
            return Paths(candidate)
    raise SystemExit("gridcastctl must be run inside the gridcast project directory")


@lru_cache
def env() -> dict[str, str]:
    """Values from .env (falling back to .env.example), overridden by the process env."""
    values: dict[str, str] = {}
    source = paths().env_file
    if not source.exists():
        source = paths().root / ".env.example"
    for line in source.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k in values})
    return values


def run(
    cmd: list[str] | str,
    *,
    check: bool = True,
    capture: bool = False,
    cwd: Path | None = None,
    input: str | None = None,
    quiet: bool = False,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    args = shlex.split(cmd) if isinstance(cmd, str) else cmd
    if not quiet:
        console.print(f"[dim]$ {' '.join(args)}[/dim]")
    result = subprocess.run(
        args, cwd=cwd, text=True, input=input,
        capture_output=capture, env={**os.environ, **(env_extra or {})},
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def kubectl(*args: str, capture: bool = True, check: bool = True, input: str | None = None,
            quiet: bool = True) -> str:
    result = run(["kubectl", "--context", CONTEXT, *args], capture=capture, check=check,
                 input=input, quiet=quiet)
    return result.stdout if capture else ""


def compose(*args: str, capture: bool = False, check: bool = True) -> str:
    p = paths()
    result = run(["docker", "compose", "-f", str(p.infra / "compose.yaml"), "--env-file",
                  str(p.env_file if p.env_file.exists() else p.root / ".env.example"), *args],
                 capture=capture, check=check)
    return result.stdout if capture else ""
