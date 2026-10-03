"""Failure-injection model: scenarios, ground truth and run records.

Design rules (from the Lumis reference-system brief):

* Deterministic and reproducible: each scenario performs the same concrete operations.
* Realistic channels only: faults arrive the way they would in production — a release through
  GitOps, a config or resource change, a credential rotation, a model-registry promotion, or a
  third-party vendor misbehaving. Nothing in the estate is labelled "chaos".
* Ground truth is hidden: it is written only to `.gridcast/chaos/runs/<run_id>.json` on the
  operator's machine and is never visible to anything running in the estate. A diagnosis
  system must be configured *not* to read that directory.
"""

import json
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GroundTruth:
    root_cause: str                 # one-sentence truth
    root_cause_entity: str          # entity id in the operational graph
    category: str                   # failure taxonomy class
    change_channel: str             # how the fault entered: release / config / resources / ...
    expected_symptoms: list[str]
    expected_evidence: list[str]    # observations that discriminate the true cause
    distractors: list[str]          # plausible-but-wrong explanations a weak diagnosis may pick
    acceptable_actions: list[str]
    unsafe_actions: list[str]
    verification: list[str]         # what must be true after recovery
    abstain_ok: bool = False        # whether "escalate to a human" is a correct outcome


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    summary: str
    ground_truth: GroundTruth
    inject: Callable[["RunContext"], None]
    revert: Callable[["RunContext"], None]
    time_to_symptom: str
    tags: tuple[str, ...] = ()


@dataclass
class RunContext:
    run_id: str
    scenario_id: str
    started_at: str
    details: dict[str, Any] = field(default_factory=dict)
    status: str = "injected"
    reverted_at: str | None = None

    def note(self, key: str, value: Any) -> None:
        self.details[key] = value


class RunStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def new(self, scenario: Scenario) -> RunContext:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return RunContext(run_id=f"{stamp}-{scenario.id}-{secrets.token_hex(2)}",
                          scenario_id=scenario.id, started_at=datetime.now(UTC).isoformat())

    def save(self, ctx: RunContext, scenario: Scenario) -> Path:
        path = self.dir / f"{ctx.run_id}.json"
        path.write_text(json.dumps({
            "run": asdict(ctx),
            "scenario": {"id": scenario.id, "title": scenario.title, "summary": scenario.summary,
                         "time_to_symptom": scenario.time_to_symptom, "tags": scenario.tags},
            "ground_truth": asdict(scenario.ground_truth),
        }, indent=2, default=str))
        return path

    def active(self) -> tuple[RunContext, dict] | None:
        for path in sorted(self.dir.glob("*.json"), reverse=True):
            data = json.loads(path.read_text())
            if data["run"]["status"] == "injected":
                return RunContext(**data["run"]), data
        return None

    def all(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.dir.glob("*.json"))]
