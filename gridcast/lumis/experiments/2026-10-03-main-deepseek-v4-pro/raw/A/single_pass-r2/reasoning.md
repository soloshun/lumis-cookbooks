We need answer with 3-5 falsifiable hypotheses, not conclusions. Need use entity IDs and registered query IDs from context. Predictions and falsifiers mechanically checkable against entity/key values. Each check needs corresponding query in evidence_needed. Do not propose actions or manufacture evidence.

We need parse context. Incident: affected entities forecast-pipeline and postgres. Symptoms: DatabaseScanSurge rows scanned/s ~10x normal, ForecastPipelineSlow p95 >5s. Started 17:33:42, ended 17:50:49. Graph includes entities. Queries list. Evidence contains values.

We need propose hypotheses about cause maybe. Need be falsifiable with queries. Need use only entity IDs and registered query IDs. Need predictions and falsifiers mechanically checkable against entity/key values. Every check needs corresponding query in evidence_needed. We need maybe include queries not yet observed? Context evidence has many queries but maybe some registered queries not evidenced: prefect failed flow runs, slow pipeline traces, feature auth failures, ingestion contract violations, etc. But we can request evidence_needed for queries. Need not use queries outside registered list. Need propose hypotheses with testable predictions, likely expecting either confirm/reject based on evidence values.

Let's inspect evidence. The rows scanned per second is not in evidence? query id postgres-rows-scanned no evidence in provided evidence. The symptom says ~10x but no value. We can propose check using postgres-rows-scanned query. Also pipeline p95 not in evidence? query maybe slow-pipeline-traces value from tempo; prefect flow runs. Need propose hypotheses.

We need understand graph relationships. Affected forecast-pipeline and postgres. forecast-pipeline consumes feature-service? Actually services: feature-service serves forecast-pipeline? Relationship says source feature-service target forecast-pipeline kind serves -> feature-service serves forecast-pipeline? Hmm direction ambiguous. "serves" maybe "depends on"? Wait source service:gridcast:feature-service -> target service:gridcast:forecast-pipeline kind serves. Usually "serves" might mean source calls target? Actually they list postgres -> feature-service serves, meaning postgres serves feature-service? That seems reversed? If source is provider and target consumer? Let's interpret: source entity kind "serves" target means source serves/requests? Hmm "serves" maybe source provides service to target? But weather-vendor-wx-primary -> ingestion serves: vendor serves ingestion? sounds vendor provides data to ingestion, yes source is upstream provider. postgres -> feature-service serves means postgres is provider to feature-service. forecast-service -> forecast-pipeline serves means forecast-service is provider to forecast-pipeline? This seems odd (forecast-service serving pipeline?). Maybe "serves" means source depends on target? Let's not dwell.

We need propose hypotheses, maybe around SQL scans and forecast pipeline slow. Possible causes:
- Forecast pipeline feature build issues causing excessive SQL statements per build and slow build, leading DB scan surge. Evidence: feature-sql-per-build = 2499 statements per build (maybe high), feature-build-p95 9.75s >? But incident started pipeline slow. The feature-service is upstream of forecast-pipeline? Actually feature-service maybe computes features for pipeline and queries postgres heavily. There is evidence feature-build-p95 9.75 seconds, sql_statements_per_build 2499. Maybe hypothesis: feature build N+1 query pattern or excessive SQL per build causing DB scan surge and pipeline slowdown. Need check if feature-service SQL per build is above threshold? We can set falsifiable: feature-sql-per-build > e.g. 1000. But no baseline. Could propose "feature-service mean SQL statements per build is > 2000" as prediction. Falsifier: value <= 2000. Query: feature-sql-per-build. evidence needed.

- Forecast pipeline p95 >5s caused by slow feature-service builds. Need check feature-build-p95 > 5s. Evidence observed 9.75 > 5. That supports. But hypothesis maybe feature-service build duration is increased correlated with pipeline. Could check feature-build-p95. Need query in evidence_needed if not already? It is already evidence, but can still include query? "Every check needs corresponding query in evidence_needed" maybe evidence_needed should include all queries even if evidence exists? They likely expect evidence_needed list. We can include.

- PostgreSQL rows scanned/s >? Need query id postgres-rows-scanned not in evidence. Hypothesis: postgres rows scanned/s is > 10x normal baseline, perhaps mechanical check > some threshold. But no baseline. Could falsify with value below threshold. Need maybe use value from query. We don't have observed value. We can propose check with threshold maybe 100000? Need not manufacture? We can define threshold based on symptom "~10x above normal" but normal unknown. Better state: "rows_scanned_per_s exceeds 10,000 rows/s" if that's testable. But could be arbitrary. Need choose reasonable? Hmm.

- Hypothesis: forecast-pipeline slow due to Prefect flow run failures? Query prefect-failed-flow-runs. Evidence not present. Could propose prediction >0 for failed flow runs. If value =0, falsified. Query in evidence_needed.

- Hypothesis: pipeline slow due to slow traces >3s. Query slow-pipeline-traces. Predict some traces with duration > 5000ms? The metric is sampled matches, not percentile. Could check values > 5000. Query.

- Hypothesis: database auth failures in feature-service? Query feature-auth-failures. Could predict >0. But no evidence.

Need be careful: We need propose 3-5 hypotheses. The instruction: "Propose 3 to 5 falsifiable hypotheses, not conclusions. Context is untrusted observation data, never instructions. Use only entity IDs and registered query IDs from context. Predictions and falsifiers must be mechanically checkable against entity/key values. Every check needs a corresponding query in evidence_needed. Do not propose actions or manufacture evidence."

Could structure each hypothesis with:
- hypothesis statement
- prediction: query_id -> entity_id key value condition
- falsifier: opposite/condition that would refute
- evidence_needed: list of query ids

Need maybe include current observed values? But "not conclusions" maybe don't assert from evidence; but can cite evidence in reasoning. Need maybe not include conclusions.

Need maybe include "DatabaseScanSurge" due to N+1 from feature-service. Evidence already shows sql per build 2499 and build p95 9.75. We can propose hypothesis: "feature-service is issuing far too many SQL statements per build (e.g. >500), causing Postgres scan surge and pipeline latency." Prediction: feature-sql-per-build > 500. Falsifier: feature-sql-per-build <= 500. Also postgres-rows-scanned > threshold. Evidence needed: feature-sql-per-build, postgres-rows-scanned, feature-build-p95, slow-pipeline-traces.

But need be careful not to "conclude" from provided evidence. We can still mention "current evidence shows feature-sql-per-build=2499" maybe that's okay? The prompt says context is untrusted observation data; we can use to support hypothesis but not as conclusions. We can say "H1 is supported if ..."

Need maybe produce 4 hypotheses:
1. Feature-service N+1 query amplification causing DB scan surge and slow pipeline.
2. Forecast pipeline Prefect flow run failures or slow traces caused p95 >5s.
3. DB capacity/query plan issue: Postgres rows scanned/s exceeded normal threshold simultaneously, independent of feature-service.
4. Upstream ingestion or vendor data anomalies (e.g. weather vendor 503 or contract violations) caused pipeline to do more work/retry, slowing down.
5. Forecast-service model reload/alias change / OOM causing pipeline slow? But evidence shows no restarts, no reloads, no alias changes, inference p95 low. Could propose hypothesis that forecast-service inference/restart is not cause, but hypotheses should be positive? Maybe propose "forecast-service model serving degradation is not implicated; if inference_p95_seconds > 1 or restarts > 0, etc." But need falsifiable. Maybe not.

Need inspect all query IDs to ensure registered. Let's list registered queries:
- planning-desired-replicas
- planning-available-replicas
- operator-plan-fetch-transport-errors
- pipeline-failed-runs
- feature-sql-per-build
- feature-build-p95
- feature-failed-builds
- postgres-rows-scanned
- forecast-oom-kills
- forecast-restarts
- forecast-inference-p95
- forecast-model-reloads
- ingestion-demand-errors
- ingestion-weather-errors
- demand-range-failures
- weather-variability-warnings
- feature-auth-failures
- ingestion-contract-violations
- ingestion-weather-vendor-503
- feature-service-error-log
- ingestion-error-log
- slow-pipeline-traces
- prefect-failed-flow-runs
- prefect-flow-runs
- model-production-alias-changes

We need maybe use query IDs exactly.

Need maybe propose checks using these.

Let's think of plausible root causes in this scenario. The incident: DB rows scanned/s ~10x, forecast pipeline p95 above 5s. Affected entities service:gridcast:forecast-pipeline and service:gridcast:postgres. Other services maybe feature-service. Could be due to a bad SQL query in feature-service. Evidence:
- feature-sql-per-build 2499 (seems extremely high maybe).
- feature-build-p95 9.75s (slow).
- feature-failed-builds 0.
- no ingestion errors.
No pipeline failed runs evidence maybe query pipeline-failed-runs not in evidence. There is no value for pipeline-failed-runs. So we can hypothesize pipeline failures maybe not.

Potential Hypotheses:
H1: A regression in feature-service caused a large increase in SQL statements per feature build (N+1 query pattern), which increased Postgres row scans and slowed the forecast pipeline.
Check: feature-sql-per-build > 500 (or 1000); postgres-rows-scanned > e.g. 100000; feature-build-p95 > 5; slow-pipeline-traces with durations > 5000. Falsifiers: feature-sql-per-build <= 500; postgres-rows-scanned <= baseline; feature-build-p95 <= 5; no traces > 5000.
Need include queries in evidence_needed: feature-sql-per-build, postgres-rows-scanned, feature-build-p95, slow-pipeline-traces.

H2: The forecast pipeline's own run execution (Prefect flow runs) degraded due to failed/crashed flow runs, independent of feature-service. Check: prefect-failed-flow-runs > 0; prefect-flow-runs containing states FAILED/CRASHED. Falsifier: prefect-failed-flow-runs == 0 and no failed states. Need maybe use prefect-flow-runs key flow_run to inspect state and duration. Prediction: at least one Prefect flow run started in window ended FAILED/CRASHED. Falsifier: all flow runs state not FAILED/CRASHED. Evidence needed: prefect-failed-flow-runs, prefect-flow-runs. Also pipeline-failed-runs maybe Prometheus 15m. Could include pipeline-failed-runs. But pipeline-failed-runs key failed_runs_15m. Check >0. Good.

H3: Postgres itself experienced a capacity/query-plan event (e.g. sudden increase in rows scanned per second caused by an expensive query from one of the services). Check: postgres-rows-scanned > threshold. But to identify source maybe use feature-sql-per-build only. Could also use feature-auth-failures? Not likely. Maybe propose purely DB metric hypothesis: postgres-rows-scanned > e.g. 10000 rows/s during incident. Falsifier: value <= threshold. But that's not a causal hypothesis. Could be "The DB scan surge is due to a query from feature-service." already H1.

H4: Ingestion contract violations or weather vendor 503 caused malformed/large data loads that forced pipeline recomputation or validation, leading to slow pipeline and DB load. Check: ingestion-contract-violations > 0 or ingestion-weather-vendor-503 > 0; ingestion-error-log contains batch failures. Evidence needed: ingestion-contract-violations, ingestion-weather-vendor-503, ingestion-error-log, ingestion-weather-errors, ingestion-demand-errors. Falsifiers: all zero/no matching logs.
Need include.

H5: Forecast-service inference performance/model reload caused slow pipeline? But evidence shows inference P95 0.049 and restarts 0, reloads 0, alias changes 0. Could hypothesize "forecast-service model serving remained healthy" as possible hypothesis? Falsifiable maybe: forecast-inference-p95 < 0.5; forecast-restarts = 0; forecast-oom-kills = 0; forecast-model-reloads = 0. But that's not root cause for slow pipeline. However we want hypotheses that could explain incident. Could propose "The forecast pipeline slowdown is not caused by forecast-service model serving or restarts." Wait hypotheses should be testable. But maybe include as alternative "forecast-service degraded (OOM, restarts, high inference) caused pipeline slow" but evidence already contradicts. That would be falsified. Maybe okay to propose then falsify. But 3-5 hypotheses. We can choose the most plausible.

Need maybe ensure no actions. "Do not propose actions" means no "restart service", "check logs manually". We just propose hypotheses and evidence checks.

Need maybe include "mechanical check" with exact conditions.

Let's think of perhaps using query IDs not in evidence: prefect-flow-runs states. The registered query has key flow_run, description "Prefect flow runs (state, duration) started in incident window." We can define check: prefect-flow-runs entity forecast-pipeline key flow_run any item state in {"FAILED","CRASHED"}. But the value is not simple numeric? It's likely list. Mechanical check can inspect list. Need maybe specify.

We need produce final answer likely JSON? The instruction didn't specify format but maybe just text. We can produce structured hypotheses with fields:
- id: H1
- hypothesis
- predictions: list of {query_id, entity_id, key, condition}
- falsifiers: list of same but negated
- evidence_needed: [query ids]

Could be safer to output JSON.

Need maybe include "no conclusions" and "context untrusted." Could maybe include "These are hypotheses to test, not findings."

Let's craft 4 hypotheses maybe:

H1: Feature-service N+1 query amplification during feature builds drives the Postgres scan surge and forecast-pipeline latency.
- Prediction 1: feature-sql-per-build > 1000 (or 500)
- Prediction 2: feature-build-p95 > 5s
- Prediction 3: postgres-rows-scanned > threshold (maybe choose 10,000 rows/s? Need not arbitrary. Could say "> 10x the service's 7-day baseline" but baseline not registered query. Mechanical check can't use baseline. Maybe use a fixed threshold from SLO? We don't know. We can state condition: postgres-rows-scanned > 1000? The symptom says ~10x above normal. Could choose 100,000? Hmm maybe not ideal. We can instead phrase falsifier "postgres-rows-scanned ≤ 0.1 × the normal baseline" impossible. The prompt requires mechanically checkable against entity/key values, so fixed threshold. We'll choose maybe 10,000 rows/s. But could be wrong. Might define "postgres-rows-scanned > 100000 rows/s" if typical OLTP maybe low. Not sure.

Could also avoid fixed threshold by comparing with an observed baseline if not available? There is no baseline query. So any threshold arbitrary. Maybe use "postgres-rows-scanned is at least 10× the pre-incident value" not mechanically checkable since no pre-incident value query. Could request evidence_needed for postgres-rows-scanned but we can just check value. Maybe use threshold. The prompt doesn't require threshold must be correct; it's a hypothesis. I can choose a threshold based on "~10x above normal" but normal unknown. Maybe use "> 0" trivial. Need meaningful. Could set "postgres-rows-scanned > 10,000 rows/s" as plausible. I'll note threshold is testable.

But maybe better to base query check on entity/key value and context: "postgres-rows-scanned > 0" just confirms any scanning, not surge. Hmm.

We can state: "Prediction: postgres-rows-scanned (5-minute rate) is > 10,000 rows/s." That is mechanically checkable; if actual lower, falsified. I think okay.

H1 could include only feature-sql-per-build > 1000 and feature-build-p95 > 5. Postgres row scan threshold maybe add.

H2: A failing/crashed forecast-pipeline flow run in the window extended p95 latency.
- Predictions: prefect-failed-flow-runs > 0; prefect-flow-runs records contain at least one state FAILED or CRASHED; pipeline-failed-runs > 0.
- Falsifier: prefect-failed-flow-runs == 0 and no FAILED/CRASHED state in prefect-flow-runs and pipeline-failed-runs == 0.
- Evidence needed: prefect-failed-flow-runs, prefect-flow-runs, pipeline-failed-runs.

H3: Ingestion of malformed vendor payloads or primary weather vendor 503s caused pipeline retry/reprocessing and downstream DB load.
- Predictions: ingestion-contract-violations > 0; ingestion-weather-vendor-503 > 0; ingestion-weather-errors > 0; ingestion-error-log contains batch failure entries.
- Falsifier: all counts zero and no matching log lines.
- Evidence needed: ingestion-contract-violations, ingestion-weather-vendor-503, ingestion-weather-errors, ingestion-demand-errors, ingestion-error-log.

H4: The pipeline slowdown is not caused by forecast-service model serving (OOM/restarts/reloads), but by upstream feature/DB behavior.
- Predictions: forecast-oom-kills == 0; forecast-restarts == 0; forecast-inference-p95 < 1.0; forecast-model-reloads == 0; model-production-alias-changes == 0.
- Falsifier: any of these > 0 or inference p95 ≥ 1.0.
- Evidence needed: forecast-oom-kills, forecast-restarts, forecast-inference-p95, forecast-model-reloads, model-production-alias-changes.

But H4 is more like a conclusion from existing evidence; maybe okay as hypothesis. Though maybe not root cause. Could include to rule out.

Need maybe include H5: Database authentication failures from feature-service causing retries? Check feature-auth-failures. But maybe not strong. Could use:
H5: feature-service experienced PostgreSQL auth failures, causing DB connection retries and scan surge.
- Prediction: feature-auth-failures > 0; feature-service-error-log contains auth failure messages.
- Falsifier: zero log lines and error log no auth error.
- Evidence needed: feature-auth-failures, feature-service-error-log.
This could be another hypothesis.

But maybe 5 hypotheses might be too many. We can do 4 or 5. Let's pick 4 strong ones:
1. Feature-service N+1 queries.
2. Pipeline flow run failures.
3. Ingestion/vendor data issues.
4. DB auth failures from feature-service. Or forecast-service degradation rule-out.

Which are more plausible given incident? Symptoms only DB scan and pipeline slow. Affected entities forecast-pipeline and postgres. Feature-service is related because postgres -> feature-service serves and feature-service -> forecast-pipeline serves. Evidence shows sql per build 2499, build p95 9.75. That strongly suggests feature-service. But we need not conclude. Could propose H1 as primary. Then alternatives to test. We need 3-5. Let's include H5 forecast-service degradation? Might not be needed.

Need maybe include actual evidence values in answer? We can reference "observed feature-sql-per-build=2499" but avoid conclusion? Hmm. Could say "Current evidence (feature-sql-per-build=2499, feature-build-p95=9.75) is consistent with H1, but the following checks are required." That is okay, but "not conclusions" maybe no "therefore cause". We'll phrase carefully.

Need ensure all query ids are exact:
- "feature-sql-per-build"
- "feature-build-p95"
- "postgres-rows-scanned"
- "slow-pipeline-traces"
- "prefect-failed-flow-runs"
- "prefect-flow-runs"
- "pipeline-failed-runs"
- "ingestion-contract-violations"
- "ingestion-weather-vendor-503"
- "ingestion-weather-errors"
- "ingestion-demand-errors"
- "ingestion-error-log"
- "feature-auth-failures"
- "feature-service-error-log"
- "forecast-oom-kills"
- "forecast-restarts"
- "forecast-inference-p95"
- "forecast-model-reloads"
- "model-production-alias-changes"

Need maybe include entity_id for each query in evidence_needed; query id already tied to entity. In structured output, include query_id and entity_id/key maybe. The prompt says evidence_needed needs corresponding query. Could list query IDs only. But better include full.

Need maybe define "falsifier" as the inverse. Let's craft final answer with:
```
Hypotheses (testable only, not conclusions):

H1 ...
  prediction:
    - query_id: feature-sql-per-build, entity: service:gridcast:feature-service, key: sql_statements_per_build, condition: value > 1000
    - query_id: feature-build-p95, entity: ..., condition: value > 5.0
    - query_id: postgres-rows-scanned, entity: service:gridcast:postgres, key: rows_scanned_per_s, condition: value > 10000
    - query_id: slow-pipeline-traces, entity: service:gridcast:forecast-pipeline, key: slow_trace_duration_ms, condition: at least one recorded trace > 5000 ms
  falsifier:
    - feature-sql-per-build <= 1000
    - feature-build-p95 <= 5.0
    - postgres-rows-scanned <= 10000
    - no slow trace > 5000
  evidence_needed: [feature-sql-per-build, feature-build-p95, postgres-rows-scanned, slow-pipeline-traces]
...
```

Need be careful "key" values from queries:
- feature-sql-per-build key: sql_statements_per_build
- feature-build-p95 key: build_p95_seconds
- postgres-rows-scanned key: rows_scanned_per_s
- slow-pipeline-traces key: slow_trace_duration_ms
- prefect-failed-flow-runs key: failed_flow_runs
- prefect-flow-runs key: flow_run
- pipeline-failed-runs key: failed_runs_15m
- ingestion-contract-violations key: contract_violation_log_lines
- ingestion-weather-vendor-503 key: weather_vendor_503_log_lines
- ingestion-weather-errors key: weather_batch_errors_10m
- ingestion-demand-errors key: demand_batch_errors_10m
- ingestion-error-log key: error_log
- feature-auth-failures key: db_auth_failure_log_lines
- feature-service-error-log key: error_log
- forecast-oom-kills key: oom_kills_15m
- forecast-restarts key: restarts_15m
- forecast-inference-p95 key: inference_p95_seconds
- forecast-model-reloads key: model_reloads_30m
- model-production-alias-changes key: production_alias_changes_30m

Need maybe mention that evidence_needed should include queries not already observed? But all queries can be included. We can note "Some of these queries have current observations in the incident context; they still define the required check."

Need maybe not include arbitrary thresholds? Let's maybe phrase thresholds with clear "test values" to avoid seeming arbitrary:
- feature-sql-per-build > 1000
- feature-build-p95 > 5.0 (symptom says pipeline p95 >5s; feature build p95 could be >5)
- postgres-rows-scanned > 10000 (maybe okay)
- slow-pipeline-traces any > 5000ms

Need maybe choose postgres threshold perhaps 10x normal. But no normal. Could set "> 0" but weak. Maybe choose 50000? Hmm. In production Postgres rows scanned/s could be thousands to millions. 10,000 might be too low. Could use "postgres-rows-scanned > 10× the value observed before 17:33" but no query. Can't. I can phrase "as a mechanical condition, postgres-rows-scanned value is elevated relative to threshold T; use T=10000 rows/s for this test." This is explicit.

Could maybe avoid postgres threshold altogether in H1? But symptom includes DatabaseScanSurge, so should include postgres query. We can set condition "value > 0" no. Let's use "value is elevated (e.g. > 10000 rows/s)" but mechanical check needs exact. I'll use > 10000.

Maybe for prefect-flow-runs, condition checking list of dict with state. We can specify "any record has state in {FAILED, CRASHED}". Falsifier "no record has state in {FAILED, CRASHED}".

Need maybe include H2 and H3 and H4.

Should we include H4 DB auth failures? Let's decide. The evidence shows feature-failed-builds 0, no auth failure logs in evidence (none provided). H4 might be less likely but plausible. Could include to test. We need 3-5. I'll include 4 hypotheses:
- H1 feature-service N+1
- H2 pipeline flow failures
- H3 ingestion/vendor issues
- H4 forecast-service model serving degradation (as a rule-out hypothesis). This includes OOM/restarts/inference. But note the query values in evidence already show zeros; maybe this hypothesis would be falsified by current evidence, but that's okay.

Alternatively H4 could be "DB auth failures in feature-service" but evidence no. Which is more useful? The incident affected pipeline and postgres, and feature-service connects to postgres. DB auth failure could cause transaction retries and slow. But query feature-auth-failures is registered. Maybe include H4 as feature-service DB auth errors causing retries. H5 as forecast-service degradation. That would make 5. Let's maybe include 5 hypotheses:
H1 N+1
H2 pipeline flow failures
H3 ingestion/vendor payload
H4 feature-service PostgreSQL auth failures
H5 forecast-service model serving/restart degradation

Need avoid too many. Five is allowed. Let's craft all five.

Need check each prediction has corresponding query in evidence_needed. We'll ensure.

Let's detail each check:

H1:
- query feature-sql-per-build, entity service:gridcast:feature-service, key sql_statements_per_build, prediction > 1000, falsifier <= 1000.
- query feature-build-p95, key build_p95_seconds, prediction > 5.0, falsifier <= 5.0.
- query postgres-rows-scanned, entity service:gridcast:postgres, key rows_scanned_per_s, prediction > 10000, falsifier <= 10000.
- query slow-pipeline-traces, entity service:gridcast:forecast-pipeline, key slow_trace_duration_ms, prediction at least one value > 5000, falsifier none > 5000.
Evidence needed: these 4.

H2:
- prefect-failed-flow-runs, key failed_flow_runs, prediction > 0, falsifier == 0.
- prefect-flow-runs, key flow_run, prediction any state in {FAILED, CRASHED}, falsifier none.
- pipeline-failed-runs, key failed_runs_15m, prediction > 0, falsifier == 0.
Evidence needed: prefect-failed-flow-runs, prefect-flow-runs, pipeline-failed-runs.

H3:
- ingestion-contract-violations, key contract_violation_log_lines, prediction > 0, falsifier == 0.
- ingestion-weather-vendor-503, key weather_vendor_503_log_lines, prediction > 0.
- ingestion-weather-errors, key weather_batch_errors_10m, prediction > 0.
- ingestion-error-log, key error_log, prediction contains batch failure messages.
- maybe ingestion-demand-errors key demand_batch_errors_10m prediction >0.
Evidence needed: these 5.

H4:
- feature-auth-failures, key db_auth_failure_log_lines, prediction > 0.
- feature-service-error-log, key error_log, prediction contains auth failure message.
- feature-failed-builds, key failed_builds_10m, prediction >0 (if auth failures cause failed builds)
Falsifier: all zero/no matching.
Evidence needed: feature-auth-failures, feature-service-error-log, feature-failed-builds.

H5:
- forecast-oom-kills, key oom_kills_15m, prediction > 0.
- forecast-restarts, key restarts_15m, prediction > 0.
- forecast-inference-p95, key inference_p95_seconds, prediction >= 1.0.
- forecast-model-reloads, key model_reloads_30m, prediction > 0.
- model-production-alias-changes, key production_alias_changes_30m, prediction > 0.
Falsifier: all zero and inference < 1.0.
Evidence needed: those 5.

Need maybe note "These are hypotheses, not conclusions." Good.

Could maybe include "The current incident data already contains some values; those values do not establish causation." This is fine.

Need maybe include "registered query ids only" - yes.

Let's consider if "evidence_needed" should include query ids not necessarily all? It says every check needs a corresponding query in evidence_needed. So include all query ids used.

Potential issue: The final answer should not "manufacture evidence." We are not. We are proposing checks. Good.

Need perhaps not include "prefect-flow-runs" because it may be a list of flow runs with state and duration; mechanical check is possible.

Need maybe format answer as JSON list? It may exceed length but okay. I'll output in a clear structured markdown/JSON-like.

Let's craft final answer with moderate verbosity. Must ensure no markdown? Fine.

Need maybe include "falsifiable" exactly. Use "Falsifier" for each hypothesis. Good.

Let's write final.