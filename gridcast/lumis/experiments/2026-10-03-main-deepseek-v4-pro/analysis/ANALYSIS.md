# Post-run analysis: 2026-10-03-main-deepseek-v4-pro

Written 2026-10-04, after all 90 runs, from the raw artefacts only (nothing re-run). The
original metrics (`../metrics.json`, `../summary.md`) are unchanged. Everything here is a
**post-hoc** reading and is labelled as such.

## 1. Corrected scores

`src/gridcast_lumis/rescore.py` writes `rescored.csv` and `metrics-rescored.json`. It applies
two corrections:

* **Entity, corrected.** A Kubernetes resource node that `hosts` the expected service in that
  scenario's prepared graph counts as that service. This changes two Lumis runs: F r2 and D r1
  started their causal path at `k8s:gridcast:deployment:<service>`.
* **Mechanism.** Does the top-ranked candidate (and does any candidate) state the ground-truth
  *mechanism*, not just the right component? Labels are in `mechanism-labels.json`, each with a
  rationale. They were assigned by Claude after the run and need human verification.

| All 10 scenarios | Rules ×5 | Single-pass ×2 | Lumis ×2 |
|---|---|---|---|
| top-1 entity, original | 0.70 | 0.60 | 0.65 |
| top-1 entity, corrected | 0.70 | 0.60 | **0.75** |
| top-3 entity, corrected | 0.70 | 0.65 | **0.90** |
| top-1 mechanism | **0.60** | 0.40 | 0.55 |
| any candidate with the right mechanism | 0.60 | 0.55 | **0.65** |
| concluded | 5 | 5 | 13 |
| wrong conclusions (entity / mechanism) | 0 / 0 | 0 / 0 | 2 / 4 |

Excluding B (not testable as run, see §3), Lumis has top-3 entity 1.00, top-1 entity 0.83 and
top-1 mechanism 0.61. Rules only match signatures (and conclude only on J), so their numbers
measure *leads*, not diagnoses.

**All four mechanism-wrong Lumis conclusions (D r1, D r2, E r1, E r2) rest on false-negative
telemetry (§2). None came from ignoring evidence that was available.**

## 2. What confused the systems: defects in the estate and harness, not in the scenarios' intent

| # | Defect | Effect | Scenarios |
|---|---|---|---|
| 1 | **Harness-triggered pipeline run corrupted pipeline metrics.** After each injection the harness ran `kubectl exec … gridcast pipeline run-once`, a second process in the forecast-pipeline pod with the same OTel `service.instance.id` (the pod name). Two processes exported the same cumulative series, so Prometheus saw counter resets and jumps: p95 about 9.3 s for exactly the 15-minute window after every injection, against about 0.5 s otherwise | A spurious `ForecastPipelineSlow` alert fired after every injection. It was the incident's symptom in 7/10 scenarios and its *only* symptom in E, H and B, and it decided when those incidents were frozen | all; decisive for E, H, B |
| 2 | **New-series blindness (model version).** `gridcast_model_loads_total{model_version="2"}` appeared at 20:58 already at 1, so `increase()` stayed 0. The first slow v2 inference (21:02) was also invisible to `rate()`. p95 was 0.05 s at the incident freeze (21:04) and 9.75 s at 21:07 | `model_reloads_30m = 0` and `inference_p95 = 0.048` were false. They contradicted the correct `forecast-model-slowdown` signature (rules 0/5), and the agent reasoned correctly over false data ("alias moved, the service never reloaded") | E |
| 3 | **New-series blindness (crash-looping container) plus `or vector(0)`.** cAdvisor never scraped the crash-looping pod's short-lived containers; `container_oom_events_total … or vector(0)` returned 0 | The false zero contradicted the correct OOM signature (rules 0/5) and *supported* two wrong "non-OOM" mechanisms | D |
| 4 | **Log field mismatch.** feature-service logs the failure in structured metadata `error`; the registered LogQL filtered `exception` | The "password authentication failed" evidence never reached triage, so the credential signature stayed `unknown` | C |
| 5 | **IPv6 noise in the database error.** libpq first tries the dual-stack address (`Network is unreachable`); the real `password authentication failed` is buried further down | A plausible red herring: C Lumis r2 proposed "connection refused / connection limit" | C |
| 6 | **Premature incident for a slow-onset fault.** B's symptom (variability warnings) needs about 30 minutes; defect 1 opened the incident after 8 | At the freeze `weather_variability_warnings_30m = 0`: the fault was not yet observable, and every system scored 0. Lumis correctly abstained | B |

The corrected scores in §1 do **not** remove these effects. They are the measured results on
an estate with these defects. The follow-up run fixes the defects and is reported separately.

## 3. Per-scenario reading

* **J** (deterministic): every system correct; Lumis concludes with no model in about 60 ms.
* **A, F** (query amplification, F with a decoy release): Lumis 4/4 correct mechanism, naming the
  commit, the flag and the query pattern. In F neither Lumis run blamed the decoy planning-api
  release.
* **C** (stale credential after rotation): every system has the right component. Lumis abstained
  in both runs: r1 listed the credential mismatch as an unresolved third candidate, and r2 was
  misled by defect 5. The rules could not confirm the credential (defect 4).
* **D** (memory limit, OOM): right component everywhere, wrong mechanism everywhere (defect 3).
* **E** (model promotion, slow inference): no system ranked the model change first (defect 2).
  Lumis saw the alias change and investigated it, but the false "no reload, no slowdown"
  evidence led it to call the change latency-neutral.
* **G** (vendor schema drift): Lumis 2/2 correct. r1 also flagged the pipeline-slow symptom as
  "not corroborated by any component telemetry", catching defect 1.
* **H** (unit change kW vs MW): Lumis r1 correct. In r2 the scorer ranked an exclusion statement
  ("not caused by feature building") first while the kW-vs-MW candidate was second.
* **I** (vendor outage): all correct. Lumis did not conclude (the 503 log count is support-only by
  design), so it abstained with the right candidate.
* **B** (stale upstream): not testable as run (defect 6). Lumis r2's top candidate was a
  feature-service 1.7.0 rollout left over from earlier scenarios A and F: **recency confusion
  from cluster history**, a risk to watch with change records.

## 4. Behaviours worth reporting

* **Grounding pays.** Single-pass names the right component as often as Lumis on the easy
  scenarios, but 85% of its candidates are unsupported by evidence, against 27% for Lumis, and
  its mechanism accuracy is lower (0.40 vs 0.55).
* **The proof is only as good as the telemetry.** Every wrong Lumis conclusion was mechanically
  "supported" by a false observation. Mechanical checking prevents unsupported claims; it cannot
  detect a lying sensor.
* **Evidence discipline exposed the harness artefact.** In G and B, Lumis stated that the
  pipeline-slow symptom was not corroborated by flow runs or traces.
* **Exclusions as hypotheses.** Agents sometimes register "X is not the cause" as a supported
  candidate, which distorts ranking (H r2, D single-pass r1) and the conclusion rule. This feeds
  an SDK fix (exclusions belong in unresolved questions; competing supported causes must not
  produce a single diagnosis).
* **Unsafe-suggestion flags (5, heuristic).** C r2: restart PostgreSQL. D r1: roll back the
  forecast-service image. H r2: roll back services. B ×2: roll back a release, with B pointing at
  the wrong target. All are tentative text marked for human review; nothing was executed.
