# Lumis SDK findings from the first live GridCast runs

Tested: `lumis-sdk` `dev` at **c757a74** (Loki/Tempo/Prefect connectors), against the live
GridCast estate, OpenRouter models `deepseek/deepseek-v4-flash` and `deepseek/deepseek-v4-pro-0813`.
Each finding has the evidence, its effect, the cookbook-side workaround used at the time, and
the SDK fix.

> **Status (2026-10-04):** all fixes are merged into `lumis-sdk` `dev` (#105, plus #106 for
> competing supported candidates, found in the main-run analysis), and the cookbook no longer
> carries any of the workarounds. The main experiment ran on c757a74 *with* them; the follow-up
> runs on the fixed SDK without them.

| # | Finding | Severity | Workaround used in the main run (now removed) | SDK fix (merged in #105) |
|---|---|---|---|---|
| 1 | `parallel_tool_calls: false` + OpenRouter `require_parameters: true` → no DeepSeek endpoint qualifies (HTTP 404) | blocks agent | `investigator.py` omits the parameter | **fixed** 8009c9a: parameter no longer sent |
| 2 | Agent `retries=0`: one malformed tool argument aborts the whole investigation | high | `investigator.py`: retries = 2 | **fixed** 8009c9a: `investigator.budget.validation_retries` (default 2) |
| 3 | Final output is all-or-nothing: one invalid hypothesis (or a revised hypothesis reusing its registered id) discards every hypothesis and suggestion | high | `investigator.py`: output validator returns SDK acceptance errors to the model (`ModelRetry`) | **fixed** 8009c9a: built-in output validator (`InvestigationTools.acceptance_problems`) + per-candidate acceptance in `handle_incident`; rejections listed in `unresolved_questions` |
| 4 | Usage-limit exhaustion discards everything the agent learned | medium | experiment lifts caps | **fixed** 8009c9a: stop reason `agent_budget_exhausted` (and `agent_output_invalid`); registered candidates assessed, their evidence collected |
| 5 | PII redaction masks ordinary numbers and ISO dates in agent tool output | high (agent quality) | PromQL results rounded in `lumis.yaml`; dates not fixable | **fixed** 20ab503: phone needs a phone shape; decimals, epochs, ISO dates, IPv4 kept; card not inside a decimal |
| 6 | Prometheus evidence can fall outside a sub-millisecond incident window → all evidence rejected | high (intermittent) | incident times truncated to whole seconds | **fixed** 1f9c069: `observed_at = min(echo, ended_at)` |
| 7 | `git.log` returns ids/timestamps only → agent walks diffs one by one | medium (cost) | — | **fixed (opt-in)** a0042a1: repository `include_commit_subjects: true`; plus typed change records d777046 (redacted, 200 chars) |
| 8 | Completed Job pods become `service:` entities via `app.kubernetes.io/name` | low | — | **fixed** 1f9c069: `Succeeded` pods skipped (failed pods kept) |
| 9 | No SQL evidence provider | medium | `external_evidence.py` (snapshot) | **fixed** c54500e: `sources.sql` + `provider: sql` (`lumis-sdk[sql]`, read-only transaction, statement timeout, DSN from env) |
| 10 | Agent banner printed to stdout by pydantic-ai | cosmetic | `PYDANTIC_AI_NO_BANNER=1` | **fixed** 8009c9a: banner disabled when the agent module loads |
| 11 | OpenRouter candidate-model adapter caps responses at 100 KB; reasoning models' responses exceed it (2 of 4 early single-pass runs) | medium | experiment replicates the request without the cap | **fixed** 8394ae4: `max_response_bytes` (default 1 MB) on all adapters |
| 12 | Candidate-model adapter rejects the whole batch when one candidate is invalid; raw output not exposed | medium (baselines) | experiment single-pass parses candidates individually | **fixed** 8394ae4: per-candidate parsing; `rejections` + `last_response` on the adapter; runtime traces each rejection |
| — | Reasoning effort not configurable | — | `investigator.py` sets `openrouter_reasoning` | **added**: `models.reasoning` (Pydantic AI `thinking`) |
| — | Agent transcript not retrievable | — | `capture_run_messages` in `investigator.py` | **added**: `PydanticInvestigator.messages` |

The root-cause notes behind each fix are in the SDK's
`docs/design-notes/gridcast-integration-lessons.md`.

## 1. OpenRouter routing fails for DeepSeek models

`PydanticInvestigator.run` sends `model_settings={"max_tokens": …, "parallel_tool_calls": False}`
and `providers.configured_investigator` sends `provider: {allow_fallbacks: false,
require_parameters: true}`. OpenRouter then keeps only endpoints that advertise *every* request
parameter. No DeepSeek endpoint lists `parallel_tool_calls`:

```text
ModelHTTPError 404 deepseek/deepseek-v4-pro-0813: No endpoints found that can handle the requested
parameters … routing_funnel: Initial 20 → Tool Compatibility 17 → Guardrails 16 → failed at "Filter by Parameters"
```

Captured request keys: `extra_body, max_tokens, messages, model, parallel_tool_calls (false),
tool_choice ("required"), tools`. Removing only `parallel_tool_calls` routes successfully.
Tool execution is already serialized by `Tool(..., sequential=True)`.
**Suggested fix:** drop `parallel_tool_calls` for OpenRouter (or send it only when the selected
endpoint supports it); keep `sequential=True`.

## 2. Zero validation retries

`Agent(..., retries=0)`. DeepSeek v4-pro's first `inspect` call sent an empty string for an
optional identifier (`string_too_short`) and the run ended with `UnexpectedModelBehavior: Tool
'inspect' exceeded max retries count of 0`. **Suggested fix:** 1–2 retries; the retry message
is the validation error, and attempts still count toward the tool budget.

## 3. All-or-nothing output acceptance

`handle_incident` registers each returned hypothesis; any exception discards *all* output
(`stop_reason = investigator_rejected_or_unavailable`). Observed twice on scenario A with a
correct diagnosis in the reasoning trace:

* `evidence_needed` contained `"gitops:kustomization.yaml git.diff"` (a receipt, not a query id)
  → `hypothesis requests unregistered evidence`.
* The model registered `h1` mid-run, then returned a refined `h1` → `revised hypothesis requires
  a new ID`.

Both times the suggestions were valid. **Suggested fix:** validate inside a pydantic-ai output
validator and raise `ModelRetry` with the reason (what the cookbook workaround does), and/or
drop only the invalid hypotheses (and suggestions that depend on them) instead of all output.
With the validator, the same scenario completed: `agent_completed`, `supported_diagnosis`,
correct root cause and causal path.

## 4. Budget exhaustion loses the investigation

With `tool_calls_limit: 12` (and later `request_limit: 12`) a reasoning model exhausted the
budget mid-investigation; `UsageLimitExceeded` is caught generically. Collected evidence and
receipts survive, but the agent's conclusions, suggestions and open questions are lost. **Suggested fix:** on budget exhaustion, return registered candidates and collected
receipts with a distinct stop reason (`agent_budget_exhausted`) rather than
`investigator_rejected_or_unavailable`.

## 5. Redaction masks numbers and dates

`security/redaction.py` `_PHONE_PATTERN = (?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)` (and the card
pattern) match ordinary telemetry:

```text
'rows_scanned 116245.83746'          -> 'rows_scanned [REDACTED_PHONE]'
'"value": 1245.4931506849316'        -> '"value": 1245.[REDACTED_CARD]'
'ts 1791032878.856'                  -> 'ts [REDACTED_PHONE]'
'p95 0.09501834908088541'            -> 'p95 [REDACTED_PHONE]'
ISO dates such as 2026-10-03          -> also match the phone pattern
```

The agent noticed (its reasoning: "postgres-rows-scanned value was redacted ([REDACTED_PHONE])
… could not be independently confirmed"). Deterministic triage is unaffected (it compares raw
numeric evidence), but the model reasons over masked metrics and dates. **Suggested fix:** do
not run free-text PII patterns over typed numeric/timestamp evidence fields; restrict phone/card
detection to string values with phone/card shape (e.g. require separators, Luhn for cards,
exclude decimals and ISO 8601).

## 6. Evidence timestamps vs. the incident window

`PrometheusConnector` queries `time=incident.ended_at.timestamp()` and records the sample
timestamp Prometheus echoes, rounded to milliseconds. With a microsecond end time the echo can
be later than `ended_at` (e.g. `.855917` → `.856`), and `IncidentContext` rejects every
observation as out of window. Two early drills lost all 14 observations this way.
**Suggested fix:** use `observed_at = min(echoed, incident.ended_at)` or query with a time
truncated to milliseconds.

## 7. `git.log` without commit messages

By design `git.log` returns commit ids and timestamps only. On GitOps history the agent had to
`git.diff` commits one at a time to find the relevant change (several requests, ~25k tokens
each). Typed, time-bounded recent-change records (#99) — or commit subjects for an approved
repository — would cut agent cost substantially on A, D and F.

## 8. Job pods as services

Completed Job pods (`db-migrate`, `model-train`, …) carry `app.kubernetes.io/name` and become
`service:gridcast:<job>` entities. **Suggested fix:** skip pods in `Succeeded`/`Failed` phase or
pods owned by Jobs when deriving logical services.

## 9. SQL evidence

No SQL provider. GridCast's model-registry evidence (scenario E) is supplied through the
snapshot provider by `external_evidence.py` (read-only role, read-only transaction, 5 s timeout).

## 11. Response byte cap on the candidate-model adapter

`models/openrouter.OpenRouterHypothesisModel._request` calls `read_json(..., max_bytes=100000)`.
With `deepseek-v4-pro-0813`, two of the first four single completions failed with
`ValueError: HTTP response exceeds byte budget` (147 s and 208 s in): the response carries
the model's reasoning. **Suggested fix:** a configurable model-response cap, and request
`reasoning.exclude` (or `include_reasoning: false`) when reasoning text is not used.

## 12. All-or-nothing candidate batches

`StructuredHypothesisModel.generate` validates the batch schema and every candidate; one bad
candidate (e.g. a check comparing against the string `"baseline_upper_bound"`, or `">"` tokens
inside `causal_path`) rejects the whole source with no access to the raw answer. For the paper's
single-pass baseline the experiment calls the adapter's `_request` directly, keeps the raw JSON
and judges each candidate separately. **Suggested fix:** return accepted candidates plus
per-candidate rejection reasons.

## Notes that are design, not bugs

* Loki `count` yields **no fact** for zero matches. Signatures must therefore use log counts only
  as supporting predictions and keep a Prometheus falsifier; otherwise a healthy estate leaves
  them `unknown` and no terminal signature can ever conclude.
* A first-alert incident can precede slower evidence; the cookbook waits 60–120 s
  (Alertmanager `group_wait`) before opening it.
