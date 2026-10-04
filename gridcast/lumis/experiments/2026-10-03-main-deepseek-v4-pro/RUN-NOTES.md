# Run notes: 2026-10-03-main-deepseek-v4-pro

## Mid-run restart (credentials only)

The run was started at 16:58 UTC on a company OpenRouter key. Scenarios J, A, F and C ran in
that process. At 19:50 UTC the process was stopped during D's cooldown (the estate was quiet and
no fault was injected) and resumed with `--scenarios D,E,G,H,I,B` in the same folder on the
operator's personal OpenRouter key. `manifest-part1.json` is the original manifest;
`manifest.json` was written by the resumed process.

What did **not** change between the two parts (verified at 21:20 UTC):

| Input | Evidence |
|---|---|
| Lumis SDK code | Part 1 imported SDK c757a74 from a clean checkout (`manifest-part1.json`: dirty false; all modules loaded during J, before any later SDK edits). Part 2 runs with `PYTHONPATH` set to a clean `git archive` export of c757a74; `manifest.json` reports "dirty" because it checks the main checkout, not the export in use |
| `lumis.yaml`, alert rules | Identical to `config/` copies; files last modified before the run started (16:56 and 12:14 UTC) |
| Harness, investigator, scoring code | All `src/gridcast_lumis/*.py` last modified before the run started |
| Model | `deepseek/deepseek-v4-pro-0813` on every response, both parts |
| Routing settings | Unchanged: `allow_fallbacks: false`, `require_parameters: true`, reasoning effort high |
| Protocol | Unchanged: 20-minute cooldown (state.json kept), 10-minute lookback, same repeats |

## Downstream provider routing

OpenRouter chooses the serving provider per request. Observed per scenario:

| Scenario | Lumis agent responses | Single-pass responses |
|---|---|---|
| J | (deterministic, no model) | Ionstream, Wafer |
| A | Wafer ×17 | Wafer, Together |
| F | Wafer ×19 | Wafer ×2 |
| C | Wafer ×23 | Ionstream ×2 |
| D | Wafer ×27 | Ionstream, Wafer |
| E | Wafer ×9 (r1) | Alibaba, Wafer |

The agent was served by one provider throughout, before and after the restart. Single-pass
requests varied across providers in both parts. Providers can differ in quantization and
sampling, so this is a confound for the single-pass baseline. Pin a provider for both systems
in future runs (OpenRouter `provider.order` / `only`).

## Changes deliberately deferred until after the run

Not applied to this run; any of them is a separate, labelled experiment or a reported correction:

* the SDK fixes on lumis-sdk branch `feat/gridcast-integration-hardening`, including typed
  change records;
* removing the cookbook workarounds (`investigator.py`, `external_evidence.py`);
* the scorer correction for Kubernetes resource nodes that host the expected service (F,
  Lumis r2). It will be reported next to the original scores and labelled as made after seeing
  results;
* any prompt or context-engineering change.

## Analysis: why top-1 is lower on D and F (checked against raw artefacts)

### F: a scoring artefact

Rules ×5, single-pass ×2 and Lumis r1 all put feature-service first. Lumis r2 named the right
cause (the 1.7.0 release, the N+1 pattern) and rejected the decoy, but its causal path starts at
`k8s:gridcast:deployment:feature-service`. It first tried the GitOps file, which is not a graph
node. The scorer only compares the first path element with `service:gridcast:feature-service`.
Substantively all F runs are correct.

### D: a telemetry false negative

The fault: a 160 MiB limit on a container whose working set is about 195 MiB. The new pod
`forecast-service-55bf8c6b5c-htkdr` restarted 6 times in the window (`k8s_container_restarts`).

* `forecast-oom-kills` (`container_oom_events_total`, cAdvisor) returned **0**. cAdvisor never
  produced a series for the crash-looping pod: its containers died faster than the scrape
  interval. The only series was the old pod's flat counter, and `or vector(0)` turned "not
  observed" into "zero OOM kills". The termination reason (`OOMKilled`) was not exposed by any
  registered source, and the Kubernetes record expired after the revert.
* **Rules ×5 (top-1 0/5):** the OOM signature has the falsifier `oom_kills_15m eq 0`, so the false
  zero *contradicted the correct signature*. Nothing else matched, so there were no candidates and
  the runs escalated.
* **Lumis r1 and r2:** both predicted `oom_kills_15m eq 0` ("non-OOM crash loop"; r2: model load
  failure or probe kill), and the false zero made those **wrong mechanisms "supported"**. Both
  named the right entity: r2 scored correct, and r1 scored partial for the same Kubernetes-node
  reason as F. Their unresolved questions say they had no forecast-service log, event or
  termination-reason query.
* **Single-pass ×2:** "forecast-service crash-looping causes the pipeline failures": right
  entity, no mechanism, unresolved.

Implications for reporting:

1. The scorer measures the **entity**, not the **mechanism**. On D it credits Lumis with 1–2/2
   entity hits while both Lumis runs got the mechanism wrong. Report a mechanism score
   (ground-truth category vs. candidate statement, labelled by hand or by a judge) alongside
   top-k entity recall.
2. Lumis' mechanical check is only as good as its observations. A false-negative fact both
   falsified the right signature and confirmed a wrong mechanism.
3. `or vector(0)` is safe only when the series is guaranteed to exist whenever the subject
   exists (pre-initialised application counters). It is unsafe for infrastructure metrics of
   short-lived subjects (cAdvisor containers).
4. Fixes for a later, labelled run, not this one:
   * an OOM signal that survives short-lived containers: the termination reason, from
     kube-state-metrics `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}`
     or a Kubernetes pod-status evidence source;
   * `container_memory_working_set_bytes` vs. limit as an independent observable;
   * signatures whose falsifiers come from a source that can observe the subject, with OOM
     cross-checked by two independent sources.

## Host sleep during G (21:40–21:57 UTC) and the second restart

The laptop lid was closed on battery at 21:40 (clamshell sleep; sleep prevention cannot override
it). G was injected at 21:44:03 and the host slept 21:44:33–21:55:44, freezing the estate during
G's window. On wake, a Prometheus `/api/v1/alerts` read timed out (harness error). The harness'
automatic revert then failed silently, because its `gridcastctl` helper ignored failures; G stayed
active. The error path also did not record the revert time, so the harness moved straight to H.
H's injection was refused because G was still active (`raw/H/inject.txt` is empty), so no H
fault ran.

Actions at 21:59: the run was stopped, G was reverted manually, and the cooldown clock was reset.
At 22:00, G, H, I and B were resumed in this folder (`manifest-part2.json` is the part-2
manifest, `stdout-part3.txt` the new output). The resume changed the harness only, nothing
Lumis or the model sees:

* `gridcastctl` failures raise;
* the alert API is retried on transient errors;
* the error path reverts, logs any revert failure and records the revert time;
* host sleep is measured per scenario (wall clock vs. monotonic clock) and written to every
  result row as `host_sleep_s`, with a warning in the log above 30 s.

J, A, F, C, D and E completed before 21:22, and the power log shows no sleep before 21:40, so
they are unaffected. Keep the host on power with the lid open for the remaining scenarios.
