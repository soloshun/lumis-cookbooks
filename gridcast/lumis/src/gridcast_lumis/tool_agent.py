"""Baseline (iii): an unguided tool agent. Raw read-only tools, none of Lumis' boundaries.

Same model, reasoning effort and frozen incident as Lumis, but:

* no operator query registry: the model writes its own PromQL, LogQL and SQL;
* no scoped graph, no acceptance rules, no mechanical assessment of its claims;
* free-text hypotheses and suggestions, taken at face value.

It is read-only (GET queries, `kubectl get`, git log/show, file reads, a read-only SQL role) and
cannot see the chaos ground truth, `.env` or the docs. It isolates what Lumis' boundaries add
over "give a capable model the same telemetry and tools".
"""

import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from gridcast_lumis.runner import GRIDCAST

PROMPT = (
    "You are a site reliability engineer investigating a live production incident in the "
    "GridCast estate (Kubernetes namespace `gridcast`). Use the read-only tools to find the root "
    "cause. Then return 3 to 5 competing root-cause hypotheses, most likely first, each naming the "
    "component where the fault originates (use service:gridcast:<name> IDs when you can), the "
    "mechanism and a short statement, plus suggested next steps. You cannot change anything. "
    "Budget: about 40 tool calls; stop as soon as you have enough evidence and answer."
)
MAX_OUT = 8000
READABLE = ("src/", "deploy/", "infra/")
KINDS = ("pods", "deployments", "replicasets", "events", "services", "configmaps")


class Candidate(BaseModel):
    root_cause: str
    mechanism: str
    statement: str


class Diagnosis(BaseModel):
    hypotheses: list[Candidate] = Field(min_length=1, max_length=5)
    suggestions: list[str] = Field(default_factory=list, max_length=5)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUT else text[: MAX_OUT - 12] + " [truncated]"


def _run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=20)
    return _clip(result.stdout if result.returncode == 0 else f"error: {result.stderr[:500]}")


def build_agent(model, model_settings, incident) -> "Agent":  # noqa: ANN001, F821
    from pydantic_ai import Agent

    end = incident.ended_at
    agent = Agent(model, output_type=Diagnosis, instructions=PROMPT, retries=2,
                  model_settings=model_settings)

    @agent.tool_plain
    async def prometheus_query(promql: str) -> str:
        """Instant PromQL query evaluated at the incident end time."""
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("http://localhost:9090/api/v1/query",
                                 params={"query": promql, "time": end.timestamp()})
        return _clip(r.text)

    @agent.tool_plain
    async def loki_query(logql: str, limit: int = 50) -> str:
        """LogQL over the incident window; returns lines with all their fields."""
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("http://localhost:3100/loki/api/v1/query_range", params={
                "query": logql, "start": incident.started_at.isoformat(), "end": end.isoformat(),
                "limit": min(limit, 200), "direction": "backward"})
        return _clip(r.text)

    @agent.tool_plain
    def kubectl_get(kind: Literal["pods", "deployments", "replicasets", "events", "services", "configmaps"],
                    name: str | None = None) -> str:
        """`kubectl get` (YAML) in namespace gridcast. Secrets are not available."""
        args = ["kubectl", "--context", "kind-gridcast", "-n", "gridcast", "get", kind]
        return _run([*args, *([name] if name else []), "-o", "yaml"])

    @agent.tool_plain
    def git_log(repo: Literal["gitops", "source"], max_count: int = 20) -> str:
        """Recent commits (hash, time, author, subject) of the GitOps or source repository."""
        root = GRIDCAST / ".gridcast" / "gitops" if repo == "gitops" else GRIDCAST.parent
        return _run(["git", "log", f"-{min(max_count, 50)}", "--format=%H %cI %an %s"], cwd=root)

    @agent.tool_plain
    def git_show(repo: Literal["gitops", "source"], commit: str) -> str:
        """Full diff of one commit."""
        root = GRIDCAST / ".gridcast" / "gitops" if repo == "gitops" else GRIDCAST.parent
        if not commit.isalnum():
            return "error: commit must be a hash"
        return _run(["git", "show", "--stat", "--patch", commit], cwd=root)

    @agent.tool_plain
    def read_file(path: str) -> str:
        """Read a file of the GridCast repository under src/, deploy/ or infra/."""
        clean = Path(path).as_posix().lstrip("/")
        if ".." in clean or not clean.startswith(READABLE) or ".env" in clean:
            return "error: only src/, deploy/ and infra/ are readable"
        target = GRIDCAST / clean
        return _clip(target.read_text()) if target.is_file() else "error: no such file"

    @agent.tool_plain
    async def sql_query(sql: str) -> str:
        """Read-only SQL on the GridCast database (read-only role and transaction, 5 s timeout)."""
        import psycopg

        try:
            async with await psycopg.AsyncConnection.connect(
                    os.environ["GRIDCAST_LUMIS_SQL_DSN"],
                    options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn, \
                    conn.cursor() as cur:
                await cur.execute(sql)
                rows = await cur.fetchmany(50)
                cols = [d.name for d in cur.description or []]
            return _clip(json.dumps({"columns": cols, "rows": rows}, default=str))
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {str(exc)[:300]}"

    return agent


@asynccontextmanager
async def tool_agent_model(model_id: str, timeout: float = 300):
    import httpx2
    from openai import AsyncOpenAI
    from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    async with (
        httpx2.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as http,
        # Transport retries with backoff: the agent's request bursts tripped the provider's
        # upstream rate limit (HTTP 429) in its first live run.
        AsyncOpenAI(api_key=os.environ["OPENROUTER_API_KEY"], max_retries=4, http_client=http,
                    base_url="https://openrouter.ai/api/v1") as client,
    ):
        settings = OpenRouterModelSettings(
            openrouter_provider={"allow_fallbacks": False, "require_parameters": True},
            openrouter_usage={"include": True}, thinking="high")
        yield OpenRouterModel(model_id, provider=OpenRouterProvider(openai_client=client)), settings


async def investigate(model_id: str, incident) -> tuple[Diagnosis | None, list, str | None]:  # noqa: ANN001
    """Run the unguided agent on the frozen incident; returns output, messages and any error."""
    from pydantic_ai import capture_run_messages
    from pydantic_ai.usage import UsageLimits

    prompt = json.dumps({"incident": incident.model_dump(mode="json")})
    async with tool_agent_model(model_id) as (model, settings):
        agent = build_agent(model, settings, incident)
        with capture_run_messages() as messages:
            try:
                # A runaway guard only (Lumis' runs used about 11 requests). In a smoke test on a
                # healthy estate the agent never converged and exhausted 60 requests.
                result = await asyncio.wait_for(
                    agent.run(prompt, usage_limits=UsageLimits(request_limit=100)), timeout=1800)
                return result.output, list(messages), None
            except Exception as exc:  # noqa: BLE001
                return None, list(messages), f"{type(exc).__name__}: {str(exc)[:300]}"
