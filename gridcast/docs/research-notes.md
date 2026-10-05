# Research notes: Lumis on the GridCast estate

This is a record of what was run, what was measured, what went wrong and what we changed. It is
written so that a reader can check every claim against the artefacts in
`lumis/experiments/`. Nothing here claims more than those artefacts show.

## 1. Question and scope

**Question.** On a realistic, instrumented estate with injected faults, does an
evidence-grounded investigator (Lumis: deterministic triage, then a tool-using agent whose
candidates are checked mechanically against operator-registered evidence) find root causes
better than:

1. the deterministic rule tier alone, and
2. a single LLM completion given the same evidence?

We also ask what it costs, and whether it is safe.

**Scope and limits, stated up front:**

* One estate (GridCast: 10 services on kind, Prometheus/Loki/Tempo/Prefect, PostgreSQL), with
  synthetic but operationally realistic faults injected through ordinary channels (GitOps
  commits, config, secrets, vendors, model registry).
* **One model family.** Every model run used DeepSeek v4 through OpenRouter:
  `deepseek/deepseek-v4-pro-0813` with reasoning effort "high" for the reported runs
  (`deepseek-v4-flash` only during development). We did not evaluate other models. Others may
  perform better or worse; nothing here generalises across models.
* Small samples: 2 repeats per model system per scenario and 5 for the rule tier.
  Percentages over 20 model runs move by 5 points per run.
* The author of the harness, scenarios, signatures and post-hoc labels is the same team (with an
  LLM assistant). The ground truth is hidden from Lumis, but not from us.

## 2. Setup

| Item | Value |
|---|---|
| Estate | GridCast (`gridcast/`), kind cluster + Docker platform; see `docs/architecture.md` |
| Scenarios | Main run: A–J (10). Follow-up: A–O (15; K–O added after the main run). See `docs/scenarios.md` |
| Systems | **rules**: Lumis deterministic triage only (×5). **single_pass**: same evidence bundle, one structured LLM completion, candidates judged by Lumis (×2). **lumis**: triage → tool-using agent → mechanical assessment (×2) |
| Model | `deepseek/deepseek-v4-pro-0813`, reasoning effort high, OpenRouter (`allow_fallbacks: false`, `require_parameters: true`) |
| Budgets | pydantic-ai request/tool/token caps lifted; SDK broker budgets at schema maxima ("let the model do its own thing") |
| Protocol | Per scenario: estate quiet and 20-minute cooldown → inject → first fresh alert → 120 s settle → one frozen incident (alert entities, window = first alert − 10 min … now) → all systems on that same incident → ground truth read → revert |
| Metrics | SEAMS research-plan families: top-k entity recall, causal-path score, unsupported-hypothesis rate, efficiency (time, tokens, cost), abstention, safety. Post hoc: mechanism score |
| Code | `lumis/src/gridcast_lumis/experiment.py`, `experiment_report.py`, `rescore.py`; SDK `soloshun/lumis-sdk` |

## 2a. The systems compared, and exactly how they differ

Every system answers the same frozen incident (same alerts, same time window, same model and
reasoning effort where a model is used). They differ only in what they are given and what they
are allowed to do:

| System | What it is given | Can it fetch more? | Is its answer checked? | Isolates |
|---|---|---|---|---|
| **rules** | Operator signatures, plus the facts their queries return | No | Yes, mechanically; it concludes only on a sufficient terminal signature | Deterministic triage alone |
| **llm_symptoms** ("Claude-web") | The alert symptoms, the affected service IDs and the time window. No graph, no facts | No | No | What a model guesses from the alert alone, like pasting an error into a chat assistant |
| **llm_graph** | llm_symptoms plus the scoped service graph (topology, owners, criticality) | No | No | The value of topology |
| **single_pass** | llm_graph plus the query catalog and the ~16–20 facts Lumis collected for the rule signatures, in one structured completion | No | Partly: each hypothesis is checked against the facts already collected, but nobody fetches the evidence its hypotheses name | The value of curated evidence |
| **single_pass_verified** | The same single-pass answers. Lumis then fetches the evidence each hypothesis names (queries pinned to the incident window) and assesses them, with no new LLM call | Lumis fetches; the model does not | Yes, mechanically | The value of verification, separated from the agent loop |
| **tool_agent** *(planned, 2 scenarios)* | Raw read tools with no registry, no acceptance rules and no mechanical check | Yes, freely | No | The value of Lumis' boundaries (fabrication, unsafe actions) |
| **lumis** | Everything: rules first; then the agent with the scoped graph, registered queries, change records, allowlisted code and Git, and multiple turns | Yes, through registered queries and tools | Yes, mechanically; one root cause required to conclude | The full system |

Two consequences for reading the results:

* **single_pass is strong by construction.** It is "Lumis-lite": Lumis' graph, Lumis' evidence
  and Lumis' checking around one LLM call. Close top-1 scores between single_pass and lumis say
  that *curated evidence* does much of the work, which is part of Lumis' value. They do not say
  the agent adds nothing.
* **llm_symptoms is the "Claude-web" baseline.** The alerts already name the affected services,
  so even this rung can score on *component*. The *mechanism* score separates guessing a likely
  component from knowing what broke.

## 3. Main run (2026-10-03, SDK c757a74 plus documented cookbook workarounds)

Folder: `lumis/experiments/2026-10-03-main-deepseek-v4-pro/`. There were 90 runs with no
system errors. The run was restarted twice: once to switch API keys during a cooldown, and once
after the host slept during G, which was re-run. Neither restart changed any input the systems
saw; `RUN-NOTES.md` has the evidence.

| All 10 scenarios | Rules ×5 | Single-pass ×2 | Lumis ×2 |
|---|---|---|---|
| top-1 entity (as scored during the run) | 0.70 | 0.60 | 0.65 |
| top-1 entity, corrected (resource node hosting the service = that service) | 0.70 | 0.60 | 0.75 |
| top-3 entity, corrected | 0.70 | 0.65 | 0.90 |
| top-1 **mechanism** (post-hoc labels) | 0.60 | 0.40 | 0.55 |
| candidates without evidence support | 0.00 | 0.85 | 0.27 |
| concluded / wrong (mechanism) | 5 / 0 | 5 / 0 | 13 / 4 |
| median time per run | 0.1 s | 152 s | 394 s |
| model cost, total | $0 | $0.67 | $3.80 |
| tokens in / out, total | 0 | 0.24 M / 0.20 M | 8.2 M / 0.60 M |

How to read this honestly:

* **Rules** never conclude except on J; their scores measure the signature that matched (a
  lead). Escalating everything else costs nothing in wrong answers and solves nothing alone.
* **Lumis** puts the right component in its top 3 in 18 of 20 runs (excluding B, which was not
  testable: 18/18). Its four wrong conclusions (D ×2, E ×2) all rest on **false-negative
  telemetry**: the mechanical check confirmed a wrong mechanism because an observation was wrong.
* **Single-pass** names the right component almost as often on easy scenarios, but most of its
  candidates have no evidence support, and its mechanism accuracy is lower.
* The corrections (hosts mapping, mechanism labels) were made **after seeing results**. Both
  numbers are reported; the mechanism labels were assigned by an LLM (Claude) with a rationale
  per label (`analysis/mechanism-labels.json`) and need human verification.

### Defects found in the estate and harness during analysis

Full detail is in `analysis/ANALYSIS.md`. These are measurement problems, not properties of the
systems:

1. The harness triggered an extra pipeline run in a second process in the same pod. Both
   processes exported the same OTel identity, which corrupted rate(), and a spurious
   `ForecastPipelineSlow` alert followed every injection (7 of 10 incidents).
2. New-series blindness: a new `model_version` label (E) and short-lived crash-looping
   containers (D) produced series whose first event `rate()`/`increase()` cannot see. With
   `or vector(0)` this became a false "zero".
3. A LogQL query filtered the wrong structured-metadata field (C).
4. IPv6 "Network is unreachable" text ahead of the real authentication error (C).
5. B's incident was opened by defect 1 before its slow-onset fault was observable.

## 4. What gave Lumis its edge, and where it did not help

Observed in the transcripts and reports, not inferred from scores alone:

* **Scoped graph plus registered evidence.** The agent sees only the incident's neighbourhood
  and asks for evidence by query ID. It cannot invent PromQL, and every claim is checked against
  facts Lumis collected itself. Unsupported candidates: 27% (Lumis) vs 85% (single-pass).
* **Mechanical assessment exposes bad symptoms.** In G and B the agent reported that the
  pipeline-slow symptom was "not corroborated by any component telemetry" (it was defect 1).
* **Tools reach the cause, not just the symptom.** On A and F the agent read the GitOps diff and
  the feature-store code and named the commit, the release flag and the query pattern. In F it
  rejected the decoy release.
* **Deterministic triage is fast and free when a signature is sufficient.** J concludes in about
  60 ms with no model, and was correct in 9/9 runs.
* **It cannot detect a lying sensor.** A false observation is indistinguishable from a true one.
  The defence is telemetry design (two independent sources for important mechanisms; no false
  zeros), not more reasoning.
* **It does not rank supported candidates.** Before SDK #106, two supported but competing causes
  still produced one "supported diagnosis" (E).
* **Recency confusion.** In B the agent latched onto an older release still visible in cluster
  history. Change records make recent changes easier to see, so they must be time-bounded and
  should be cross-checked against symptom onset.
* **Exclusions as hypotheses.** Agents sometimes register "X is not the cause" as a candidate,
  which distorts ranking and conclusions (addressed in the agent instructions in SDK #106).

## 5. Model behaviour notes

* **Language mixing.** DeepSeek models are known to switch language occasionally in reasoning.
  We checked all 40 model artefacts of the main run (reasoning traces and answers) for CJK
  characters and found none. That shows only that it did not happen in these runs.
* **Reasoning volume.** Lumis runs produced 1.8 M characters of reasoning in total (about 92 k
  per run); single-pass about 35 k per run. All traces are saved (`raw/*/lumis-r*/reasoning.md`).
* **Provider routing.** OpenRouter served the agent from one provider throughout, while
  single-pass requests went to four different providers. Providers can differ in quantisation,
  which is a confound for single-pass.
* **Output validity.** Early agent runs failed on SDK acceptance rules (a revised hypothesis
  reusing its ID; a git receipt listed as evidence). The SDK now returns these to the model for
  repair (#105).

## 6. Changes made after the main run

Each change has a commit and a recorded reason:

| Area | Change | Where |
|---|---|---|
| SDK | Robust agent on OpenRouter reasoning models, per-candidate acceptance, redaction, Prometheus window, SQL provider, commit subjects, typed change records | lumis-sdk #105 |
| SDK | Competing supported root causes yield `insufficient_evidence`; the agent returns causes only | lumis-sdk #106 |
| Estate | Distinct OTel identity per process; gauges for model load and last inference; kube-state-metrics for termination reasons; IPv4-first DNS in images | gridcast `src/`, `deploy/` |
| Harness | No extra pipeline trigger; loud failures; host-sleep detection; hosts-aware scoring | `experiment.py` |
| Lumis config | OOM from termination reason plus memory ratio; model load and inference from gauges; LogQL field fix; native SQL; change records; no false zeros for infrastructure facts | `lumis/lumis.yaml` |
| Scenarios | K (timeout meets slow vendor), L (decoy release during a silent data gap), M (CPU limit squeeze), N (training/serving skew: load features in kW, model trained on MW), O (one zone missing, aggregates healthy). No rule signatures for K–O, by design | `src/gridcast/chaos/scenarios.py` |
| Harness | Single-pass and Lumis run concurrently on the frozen incident (every query is pinned to its window; per-run seconds are measured under concurrency, unlike the main run); credit guard stops before a scenario when the balance is under $1.50; evidence windows of 20 minutes so the previous scenario's revert is not "recent" | `experiment.py`, `lumis.yaml` |

## 7. Follow-up run and the evidence ladder (2026-10-04/05)

Folder: `lumis/experiments/2026-10-04-followup-deepseek-v4-pro/`. The protocol and model are the
same as the main run, on the fixed SDK (dev 51016d2) and fixed estate, with 15 scenarios (K–O
new) and single-pass and Lumis running concurrently on each frozen incident. There were 135 runs
and no host sleep. Two runs were affected by infrastructure: H's first attempt lost all four
model runs to a network/DNS failure reaching OpenRouter (re-run; the first attempt is kept in
`raw/H.network-failure-1911`), and C Lumis r2 stopped after nine model requests with an
unrecorded provider failure (kept as a degraded run; SDK #113 now records the cause).

The **evidence ladder** (`ladder/`) adds three systems on the same frozen incidents:
llm_symptoms, llm_graph and single_pass_verified (§2a). Every system is scored identically: top-1
component, a transparent regex mechanism rubric per scenario (`ladder.py`, RUBRIC), component AND
mechanism, and conclusion precision.

| All 15 scenarios | Rules | LLM, symptoms only | LLM + graph | Single-pass | Single-pass + verification | Lumis |
|---|---|---|---|---|---|---|
| top-1 component | 0.67 | 0.33 | 0.43 | 0.63 | 0.60 | **0.93** |
| top-1 component AND mechanism | 0.53 | 0.03 | 0.17 | 0.50 | 0.50 | **0.90** |
| right diagnosis anywhere in output | 0.60 | 0.10 | 0.23 | 0.57 | 0.53 | **0.97** |
| concluded (precision) | 5 (1.00) | — | — | 9 (0.89) | 3 (1.00) | **24 (0.96)** |
| hard set K–O, correct diagnoses | 0/25 | 0/10 | 2/10 | 2/10 | 2/10 | **9/10** |
| model cost per run | $0 | $0.005 | $0.015 | $0.035 | $0.035 | $0.14 |
| median seconds per run | 0.13 | — | — | 224 | 224 | 225 |

Charts: `ladder/charts/` (overview of all metrics, the ladder, per scenario, original vs hard,
cost vs correctness).

What the ladder shows:

* **Without Lumis' evidence the model guesses.** Given only the alert (llm_symptoms, the
  "paste the error into a chat" baseline), the model names the symptomatic service and a generic
  cause ("slow external dependency", "resource exhaustion"): 1 correct diagnosis in 30. The graph
  helps it find the right component (top-3 0.80) but not what broke (0.17).
* **Curated evidence does most of single-pass's work.** single_pass is "Lumis-lite" (§2a);
  close top-1 scores to Lumis on easy scenarios come from Lumis' own graph and evidence.
* **Verification alone does not close the gap.** Fetching and checking single-pass's own
  evidence made it more cautious (3 conclusions, all correct, instead of 9) but not more often
  right (0.50 either way). The single-pass hypotheses were wrong because they never looked at the
  cause, not because nobody checked them.
* **The agent's reach is the difference on hard faults.** On K–O Lumis made 9/10 correct
  diagnoses, against 2/10 for single-pass. The decisive facts were outside the pre-collected
  bundle: the timeout commit (K), the CPU-limit commit (M), the `load_unit=kw` release flag in
  `releases.yaml` and the feature monitor (N), and the zones-reporting count (O).
* **Remaining Lumis misses.** C: both runs, one degraded by the provider failure and one naming
  PostgreSQL rather than feature-service while stating the right mechanism. K r2: named the vendor
  while citing the timeout commit.

Behaviour notes (follow-up):

* **Language mixing occurred in reasoning traces only.** 2 of 122 model artefacts contain CJK
  text: single-pass N r1 reasoned entirely in Chinese, and Lumis N r1's thinking had one stray
  character. All final answers were English.
* **Safety.** Nothing was executed (the kernel has no executor). The two heuristic
  "unsafe suggestion" flags (E, G) are false positives on reading: both suggestions escalate to
  an owner for a decision.
* **Unsupported candidates:** single-pass 90%, Lumis 12.5% (single-pass's figure is partly
  structural, see §2a; single_pass_verified is the fair comparison).

Scoring caveats: the mechanism rubric was revised twice after inspecting outputs (E/G wording,
then O wording and hyphen/space-insensitive component names). Each revision applies to every
system and is recorded in the code. Rubric matching is a proxy; a human check of the K–O labels
is recommended before publication.

## 7a. Models not evaluated

Only DeepSeek v4 was used for every reported model run (`deepseek/deepseek-v4-pro-0813` with
reasoning effort high; `deepseek-v4-flash` only during development). This was a cost decision for
a proof of concept: a reasoning model at about $0.14 per Lumis run and $0.035 per single-pass run
let us run 225+ scored runs for under $10. We did **not** evaluate GPT-5-class models,
Claude Haiku or Sonnet, Gemini or Grok. Stronger models may raise every LLM-based rung,
including single-pass and the symptoms-only baseline, and could narrow or widen the gap to Lumis;
nothing here should be read as a claim about them. A small cross-model check (one hard scenario,
e.g. N or M, per model, same frozen incident via the ladder) is the cheapest next step if credit
allows.

## 8. Threats to validity

* Synthetic estate and faults. Faults are realistic in channel and symptom but chosen by us;
  the scenario set is not a sample of real incidents.
* One model family (DeepSeek v4, §7a), few repeats, and no human baseline.
* Post-hoc scoring corrections and LLM-assigned mechanism labels (reported next to the originals).
* The estate defects above affected the main run; the follow-up fixes them, but there may be
  others we have not found.
* Rule signatures were written by people who knew the scenarios. That is why K–O have none.
* Follow-up latencies are measured with the model systems running concurrently; they are not
  directly comparable with the main run's sequential timings.

## 9. Reproduce

```bash
cd gridcast && make up && make verify
cd lumis && uv sync
uv run gridcast-lumis experiment --name <name> --scenarios J,A,F,C,D,E,G,H,I,B,K,L,M,N,O \
  --repeats 2 --rules-repeats 5 --model deepseek/deepseek-v4-pro-0813
uv run gridcast-lumis experiment-report experiments/<name>
```
