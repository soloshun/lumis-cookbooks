import json

from gridcast.chaos.model import RunStore
from gridcast.chaos.scenarios import SCENARIOS, resolve
from gridcast.ctl.gitops import _set_image
from gridcast.quality.checks import CheckResult, _grade, decide
from gridcast.release import Release, load_release


def test_grading_and_decision():
    assert _grade(10, 20, 30) == "pass"
    assert _grade(25, 20, 30) == "warn"
    assert _grade(None, 20, 30) == "fail"
    assert _grade(10, 45, 20, higher_is_worse=False) == "fail"
    results = [CheckResult("a", "x", "pass"), CheckResult("b", "y", "warn"),
               CheckResult("c", "z", "fail")]
    assert decide(results) == ("hold", ["c:z"], ["b:y"])
    assert decide(results[:2])[0] == "publish"


def test_scenario_catalogue_is_complete():
    assert len(SCENARIOS) >= 10
    assert resolve("j").ground_truth.category == "availability.scaled_to_zero"
    for scenario in SCENARIOS.values():
        gt = scenario.ground_truth
        assert gt.root_cause and gt.root_cause_entity and gt.category
        assert gt.expected_evidence and gt.acceptable_actions and gt.unsafe_actions
        assert gt.verification
    assert resolve("a").id.startswith("A-")
    assert resolve("E-model-serving-slowdown").id == "E-model-serving-slowdown"


def test_run_store_round_trip(tmp_path):
    store = RunStore(tmp_path)
    scenario = SCENARIOS["B-stale-weather-feed"]
    ctx = store.new(scenario)
    ctx.note("fault", {"mode": "stale"})
    store.save(ctx, scenario)
    active, data = store.active()
    assert active.run_id == ctx.run_id
    assert data["ground_truth"]["root_cause_entity"] == "vendor:wx-primary"
    ctx.status = "reverted"
    store.save(ctx, scenario)
    assert store.active() is None
    assert json.loads(next((tmp_path / "runs").glob("*.json")).read_text())["run"]["status"] == "reverted"


def test_gitops_image_bump_preserves_layout():
    text = ("  - { name: gridcast/feature-service,   newName: localhost:5001/gridcast/feature-service,"
            "   newTag: 1.6.0 }\n  - { name: gridcast/planning-api, newName: x, newTag: 2.3.0 }\n")
    out = _set_image(text, "feature-service", "1.7.0")
    assert "newTag: 1.7.0 }" in out
    assert "planning-api, newName: x, newTag: 2.3.0" in out


def test_release_manifest_fallback(tmp_path):
    assert load_release(str(tmp_path / "missing.json"), "svc").service == "svc"
    release = Release(flags={"lag_resolution": "minute"})
    assert release.flag("lag_resolution") == "minute"
    assert release.flag("missing", 1) == 1
