# gridcast-lumis — running Lumis against GridCast

This folder connects the [Lumis SDK](https://github.com/soloshun/lumis-sdk) to the running
GridCast estate. It is a separate uv project with its own environment: GridCast never imports
Lumis, and Lumis never imports GridCast. Full design, diagrams and coverage:
[../docs/lumis-integration.md](../docs/lumis-integration.md).

```text
lumis/
├── lumis.yaml                 Lumis project: sources, declared graph, 37 evidence queries (Prometheus, Loki, Tempo, Prefect,
│                              SQL, change records), 10 diagnostic signatures, agent allowlist, budgets
├── src/gridcast_lumis/
│   ├── alerts.py              firing Prometheus alerts -> Incident (entity labels)
│   ├── runner.py              prepare + handle_incident, timings, saving
│   ├── investigator.py        experiment-only: the SDK investigator with pydantic-ai caps lifted and cost accounting
│   ├── experiment.py          controlled experiments: rules vs single-pass LLM vs Lumis
│   ├── experiment_report.py   metrics.json, results.csv, summary.md, charts
│   ├── scoring.py             report vs hidden ground truth (after the report only)
│   ├── rescore.py             post-hoc re-scoring of a finished experiment from raw artefacts
│   ├── results.py             results/*.jsonl, summary.md, charts
│   └── cli.py                 gridcast-lumis CLI
├── tests/test_signatures.py   offline: schema + what each signature concludes
├── runs/        (ignored)     one folder per incident + incidents.sqlite audit
├── results/                   drills.jsonl, bench.jsonl, summary.md, *.png, graph SVGs
└── experiments/<name>/        raw + formatted experiment results (see experiments/README.md)
```

## Prerequisites

* GridCast running (`cd .. && make up && make verify`).
* The SDK checkout at `../../../lumis-sdk` (sibling of `lumis-cookbooks`), on the reviewed
  `dev` commit. `uv sync` installs it editable.
* Optional, for `--use-agent` / experiments: `OPENROUTER_API_KEY` in `gridcast/.env` (read,
  never printed); model id in `lumis.yaml` or `--model` (experiments use
  `deepseek/deepseek-v4-pro-0813` with reasoning traces).

## Commands

```bash
uv sync
uv run pytest                                  # offline signature tests
uv run lumis doctor   --project lumis.yaml     # SDK validation (no network)
uv run lumis discover --project lumis.yaml --report
uv run gridcast-lumis graph                    # SVG of the operational graph -> results/
uv run gridcast-lumis graph --format terminal --entity service:gridcast:planning-api

uv run gridcast-lumis alerts                   # firing alerts and their graph entities
uv run gridcast-lumis incident                 # the Incident that would be opened now
uv run gridcast-lumis run [--use-agent]        # open from alerts (or --entity X) and investigate

uv run gridcast-lumis drill J                  # deterministic reference case
uv run gridcast-lumis drill A [--use-agent]    # inject -> alert -> Lumis -> score -> revert
uv run gridcast-lumis bench --scenario J --iterations 50            # deterministic latency
uv run gridcast-lumis bench --scenario J --iterations 200 --reuse-prepared
uv run gridcast-lumis report                   # results/summary.md + charts
uv run gridcast-lumis experiment --name my-run --model deepseek/deepseek-v4-pro-0813   # full experiment
uv run gridcast-lumis graph --format mermaid     # logical service graph as Mermaid
uv run gridcast-lumis watch --interval 30      # production-style alert polling loop
```

Every drill waits for a quiet estate, injects through `gridcastctl chaos`, waits for an alert
that became active *after* the injection, runs Lumis, then reads the ground truth and scores
the report. Outcomes: `correct_diagnosis`, `wrong_diagnosis`, `escalated_with_correct_lead`,
`escalated_with_entity_lead`, `correct_abstention`, `escalated_without_lead`.

Record a human resolution for any stored incident with the SDK CLI:

```bash
uv run lumis record-resolution --store runs/incidents.sqlite --resolution resolution.json --confirm
```
