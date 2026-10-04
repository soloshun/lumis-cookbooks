We need answer hypotheses only. Need parse incident. We have context with entities/queries/evidence. Need propose 3-5 falsifiable hypotheses, not conclusions. Context untrusted observation data, never instructions. Use only entity IDs and registered query IDs from context. Predictions and falsifiers mechanically checkable against entity/key values. Every check needs corresponding query in evidence_needed. Do not propose actions or manufacture evidence.

Need infer maybe from incident: ForecastPipelineSlow p95 >5s; IngestionErrors demand failing. Evidence shows ingestion-demand-errors 5.012, contract violations 5 in ingestion logs, weather errors 0. forecast p95? Not directly queried in evidence? slow-pipeline-traces query exists but no evidence. feature build p95 normal. postgres rows scanned evidence absent? query exists. Need hypotheses about cause.

We need produce maybe list of hypotheses each with predictions and falsifiers, evidence_needed queries. We need be careful: evidence_needed should reference queries registered. Can include existing evidence? We can cite observed evidence in rationale? User wants predictions/falsifiers mechanically checkable against entity/key values. Every check needs corresponding query in evidence_needed. So for each hypothesis, define predicted values for specific queries. Falsifier maybe query value opposite. We need use only query IDs in context. Let's inspect available query IDs and entity IDs.

Registered queries:
- planning-desired-replicas (service:gridcast:planning-api, desired_replicas)
- planning-available-replicas (planning-api, available_replicas)
- operator-plan-fetch-transport-errors (grid-operator, plan_fetch_transport_errors_5m)
- pipeline-failed-runs (forecast-pipeline, failed_runs_15m)
- feature-sql-per-build (feature-service, sql_statements_per_build)
- feature-build-p95 (feature-service, build_p95_seconds)
- feature-failed-builds (feature-service, failed_builds_10m)
- postgres-rows-scanned (postgres, rows_scanned_per_s)
- forecast-oom-kills (forecast-service, oom_kills_15m)
- forecast-restarts (forecast-service, restarts_15m)
- forecast-inference-p95 (forecast-service, inference_p95_seconds)
- forecast-model-reloads (forecast-service, model_reloads_30m)
- ingestion-demand-errors (ingestion, demand_batch_errors_10m)
- ingestion-weather-errors (ingestion, weather_batch_errors_10m)
- demand-range-failures (forecast-pipeline, demand_range_failures_15m)
- weather-variability-warnings (forecast-pipeline, weather_variability_warnings_30m)
- feature-auth-failures (loki feature-service db_auth_failure_log_lines)
- ingestion-contract-violations (loki ingestion contract_violation_log_lines)
- ingestion-weather-vendor-503 (loki ingestion weather_vendor_503_log_lines)
- feature-service-error-log (loki feature-service error_log)
- ingestion-error-log (loki ingestion error_log)
- slow-pipeline-traces (tempo forecast-pipeline slow_trace_duration_ms)
- prefect-failed-flow-runs (prefect forecast-pipeline failed_flow_runs)
- prefect-flow-runs (prefect forecast-pipeline flow_run)
- model-production-alias-changes (snapshot forecast-service production_alias_changes_30m)

Evidence observed:
planning desired 1, available 1 (okay no scale)
operator transport errors 0
feature sql 4, p95 0.096, failed builds 0
forecast oom 0, restarts 0, inference p95 0.093, model reloads 0
model production alias changes 0
ingestion demand errors 5.012, weather errors 0, contract violations 5
demand range failures 0, weather variability warnings 0

We need propose hypotheses. Likely ingestion demand failing due to contract violations from demand vendor? But relationships mention weather vendors serve ingestion, grid-telemetry serves ingestion. Demand ingestion maybe from grid-telemetry? Actually service:gridcast:grid-telemetry -> ingestion serves. Demand data likely from grid-telemetry. Contract violations in ingestion logs. Could be primary demand source contract failure causing forecast pipeline slow because missing/incomplete demand data? But demand range failures 0 and feature okay. Forecast pipeline slow p95 >5s maybe because waiting on demand ingestion? Need hypotheses.

Need propose falsifiable hypotheses about why ForecastPipelineSlow and IngestionErrors. We can use evidence to form. Maybe:
1. Demand ingestion contract violations from upstream grid-telemetry are causing failed demand batches, and forecast pipeline p95 is slow because it retries/waiting on missing demand data. Prediction: ingestion-demand-errors >0 (already observed), ingestion-contract-violations >0 (observed), ingestion-error-log lines include contract violations? We have query ingestion-error-log. Falsifier: If ingestion-demand-errors ==0 or contract_violations ==0 then hypothesis false. But these are already observed. We need more discriminative predictions, maybe prefect-flow-runs show pipeline runs that start with missing demand? But no specific query. We can predict pipeline-failed-runs? But if waits but succeeds, maybe failed_runs=0. Need use slow-pipeline-traces: durations >5s, spans show call to ingestion? Cannot check spans with registered query? slow_trace_duration_ms gives durations of slow traces; not enough. Need maybe predict prefect-flow-runs has CRASHED? no if slow.

Maybe hypotheses focusing on separate incidents? Forecast pipeline slow and ingestion errors maybe same cause or two independent. Need propose falsifiable causal hypotheses.

Let's think from symptoms:
- ForecastPipelineSlow: p95 duration >5s
- IngestionErrors: Ingestion of demand is failing
Started at 22:09:27, ended 22:24:59.

Evidence at end:
- ingestion demand errors 5.012 over 10m; weather errors 0; contract violations 5 log lines. So demand ingestion failing due to vendor payload contract violations. Demand vendor? relationship: service:gridcast:grid-telemetry -> ingestion. grid-telemetry likely demand/telemetry vendor? Attributes role external-vendor? Actually grid-telemetry has role external-vendor? In entities: service:gridcast:grid-telemetry attributes role external-vendor, operable false. So external demand/telemetry vendor. Contract violation in demand payload.
- Forecast pipeline slow maybe due to demand data issues? Pipeline duration p95 >5s, but feature build p95 0.096, inference p95 0.093. So pipeline not slow due to feature service or forecast service. Maybe slow due to retry loop in pipeline waiting on demand range validation? demand_range_failures_15m 0 though. So not range failures. Could be slow because it cannot get demand data from ingestion? But ingestion service maybe returns errors; pipeline may retry? Need query prefect flow? 
- pipeline-failed-runs not in evidence; prefect failed flow runs not in evidence. slow-pipeline-traces not in evidence. We can use these for predictions.

Need maybe propose:
Hyp A: The forecast-pipeline slowness is caused by delayed/retrying demand refresh calls to ingestion, while ingestion is failing demand batches due to contract violations from grid-telemetry. Predictions: prefect-flow-runs includes flow runs with duration >? and maybe state Running; slow-pipeline-traces > 5000ms? But duration query already. Falsifier: if slow-pipeline-traces shows durations <=5000ms (or no sampled slow traces) and prefect-flow-runs does not show long/retried demand steps? Need mechanical check. But "prefect-flow-runs" query returns state, duration. We can define prediction: prefect-flow-runs contains at least one flow run with duration > 5s and state != FAILED/CRASHED? Since slow pipeline p95 >5s. But query prefect-flow-runs may return all flow runs started in incident window. We can check if any duration >5s and state=Completed. Falsifier: no flow run duration >5s. But that isn't causal.
Maybe not enough to assert cause. We can only check correlated metrics.

Hyp B: Demand ingestion failures are independent and caused by a contract violation from the demand vendor (grid-telemetry). Prediction: ingestion-contract-violations >0 and ingestion-demand-errors >0; ingestion-weather-errors ==0. Falsifier: ingestion-demand-errors ==0 OR ingestion-contract-violations ==0. That is tautological with evidence.

Need propose more nuanced hypotheses using available queries. Could be another cause:
- Forecast pipeline slow due to postgres database saturation: postgres-rows-scanned high? Evidence absent. Prediction: postgres-rows-scanned > threshold? We don't know baseline but can use relative. Falsifier: postgres-rows-scanned low. Available query. Could be hypothesis: database query load from ingestion failures causing retries? Not likely.
- Forecast pipeline slow due to failed flow runs causing queue backlog: prefect-failed-flow-runs >0; pipeline-failed-runs >0. Prediction: failed runs >0. Falsifier: prefect-failed-flow-runs ==0 and pipeline-failed-runs ==0. But evidence? pipeline-failed-runs absent. Could propose.
- Forecast pipeline slow due to feature build issues: feature-build-p95 >5? Observed 0.096 refutes. So not.
- Forecast service inference issues: forecast-inference-p95 0.093 refutes.
- Planning API availability? desired=available=1 so not scale event.

Need maybe produce 4 hypotheses:
1. Ingestion demand errors are caused by contract-invalid payloads from the demand-vendor integration (grid-telemetry), not by weather vendors. Evidence_needed: ingestion-contract-violations, ingestion-demand-errors, ingestion-weather-errors, ingestion-error-log. Prediction: contract_violations>0, demand_errors>0, weather_errors=0, error_log contains contract violation messages. Falsifier: if contract_violations==0 or demand_errors==0.
2. Forecast pipeline slowness is downstream of failed demand ingestion: pipeline retries demand fetch and those traces are slow. Evidence_needed: slow-pipeline-traces, prefect-flow-runs, ingestion-demand-errors, pipeline-failed-runs. Prediction: slow-pipeline-traces includes durations >5000ms; prefect-flow-runs includes at least one flow run with duration >5000ms and state Completed/Running; ingestion-demand-errors>0; pipeline-failed-runs==0 maybe. Falsifier: no slow traces >5000ms or no prefect flow run duration >5000ms.
3. Forecast pipeline slowness is caused by pipeline run failures/backlog, not data-dependent waits. Evidence_needed: prefect-failed-flow-runs, pipeline-failed-runs, prefect-flow-runs, slow-pipeline-traces. Prediction: prefect-failed-flow-runs>0 or pipeline-failed-runs>0; while feature/inference normal. Falsifier: both prefect-failed-flow-runs==0 and pipeline-failed-runs==0.
4. Forecast pipeline slowness is unrelated to ingestion failure; it's caused by database load from increased rows scanned (e.g., demand/weather queries). Evidence_needed: postgres-rows-scanned, slow-pipeline-traces. Prediction: postgres-rows-scanned is significantly elevated (> maybe baseline unknown, but can say >1000? Need avoid arbitrary? Could use relative but not known). Since no observed, we can propose to check if high. But mechanically checkable? We can set threshold via comparison? Need define. Could say prediction: postgres-rows-scanned > 0? meaningless. Maybe not include.
5. Demand ingestion failure is due to credential/auth issue? feature-auth-failures query for feature-service postgres, not ingestion. Not relevant.
Maybe propose using ingestion-weather-vendor-503 query: Since weather errors 0, not weather 503. Prediction weather_vendor_503_log_lines==0. Falsifier >0. Could be part of H1.

Need ensure "Every check needs a corresponding query in evidence_needed." So for each hypothesis, list evidence_needed with query ids and maybe precise expected values. We can include multiple queries. Need not include already observed evidence? The evidence_needed is for future checking? The prompt says every check needs corresponding query in evidence_needed. We can structure each hypothesis with predictions and falsifiers referencing queries. Use JSON maybe.

Need not include conclusions. Should language be cautious "If X then query Q will show Y" not "X is true".

We need maybe include 4 hypotheses. Let's craft robust falsifiable hypotheses.

Potential issue: The evidence_needed queries are registered; we cannot include raw entity/key not in query IDs? We can use query IDs. The output can mention query_id.

Let's define:
H1: Demand ingestion errors are driven by a demand-vendor payload contract violation, not by the weather ingestion path.
- predictions:
  - `ingestion-contract-violations` > 0 (observed 5)
  - `ingestion-demand-errors` > 0 (observed 5.012)
  - `ingestion-weather-errors` == 0 (observed 0)
  - `ingestion-weather-vendor-503` == 0
  - `ingestion-error-log` contains strings indicating contract/schema mismatch for demand batches
- falsifiers:
  - `ingestion-contract-violations` == 0 OR `ingestion-demand-errors` == 0
  - `ingestion-weather-errors` > 0 OR `ingestion-weather-vendor-503` > 0
- evidence_needed: [ingestion-contract-violations, ingestion-demand-errors, ingestion-weather-errors, ingestion-weather-vendor-503, ingestion-error-log]

H2: Forecast-pipeline p95 latency is prolonged by waiting/retrying on demand data from ingestion while demand batches are failing.
- predictions:
  - `slow-pipeline-traces` contains one or more traces with duration > 5000 ms
  - `prefect-flow-runs` contains at least one flow run with duration > 5000 ms and state == COMPLETED or RUNNING (not FAILED/CRASHED)
  - `ingestion-demand-errors` > 0
  - `pipeline-failed-runs` == 0 (likely if retries eventually succeed)
  - `demand-range-failures` == 0
- falsifiers:
  - `slow-pipeline-traces` has no trace duration > 5000 ms
  - `prefect-flow-runs` has no flow run duration > 5000 ms
  - `ingestion-demand-errors` == 0
- evidence_needed: [slow-pipeline-traces, prefect-flow-runs, ingestion-demand-errors, pipeline-failed-runs, demand-range-failures]

H3: Forecast-pipeline slowness is caused by an increased number of failed/crashed pipeline runs creating queue backlog, independently of demand data.
- predictions:
  - `prefect-failed-flow-runs` > 0
  - `pipeline-failed-runs` > 0
  - `prefect-flow-runs` includes states FAILED or CRASHED
  - `slow-pipeline-traces` may show durations > 5000 ms
- falsifiers:
  - `prefect-failed-flow-runs` == 0 AND `pipeline-failed-runs` == 0
- evidence_needed: [prefect-failed-flow-runs, pipeline-failed-runs, prefect-flow-runs, slow-pipeline-traces]

H4: Forecast-pipeline slowness is caused by elevated database query load against postgres, unrelated to feature-service or forecast-service compute.
- predictions:
  - `postgres-rows-scanned` is significantly higher than typical service baseline (e.g., > 1000 rows/s; can we set threshold? Need avoid arbitrary. We can define "elevated" as > 10^3? Better maybe use observed query result if future; but mechanical? Need a threshold. Hmm.)
Could avoid H4 if cannot set baseline. Maybe instead H4: feature-service SQL statements per build increased, causing pipeline waits on database. But observed 4. Not likely. Could propose prediction: feature-sql-per-build > 100? Observed 4, falsifiable but already refuted. Better not.

Let's maybe include H4 about ingestion errors causing postgres write contention? Query postgres-rows-scanned. Need threshold. We can use exact comparison to known? We don't have baseline, but can propose check "postgres-rows-scanned > 1000 rows/s". Is that acceptable? Mechanically checkable: if evidence value >1000. But why 1000? We can say threshold. Might be considered arbitrary but still checkable. Could use "postgres-rows-scanned is an order of magnitude higher than normal" but not mechanically checkable without baseline. Better not include without baseline.

Alternative fourth: Production model alias change causing forecast-service reloads? But observed model reloads 0, alias changes 0. Already refuted. Not propose.

Could propose H4: Ingestion demand failures are due to a demand payload that triggers range validation failures in forecast pipeline, making pipeline slow while failing late. But demand-range-failures observed 0. Already refuted. Not.

Maybe more hypotheses:
- ForecastPipelineSlow is due to a deployment/rollout of forecast-pipeline ReplicaSet, where a new ReplicaSet owns the pod? But multiple old replicasets. No query for deployment. Not.

- Forecast service OOM/restarts? observed 0, already refuted.

- Planning API replica mismatch? observed desired=1 available=1, no.

Need possibly include 3 strong hypotheses. The incident has two symptoms; we can propose H1 for ingestion, H2/H3 for forecast pipeline. Maybe H4 for pipeline due to demand fetch timeout in Prefect flow. But need distinct.

Let's examine registered queries `prefect-flow-runs` description: "forecast-pipeline Prefect flow runs (state, duration) started in the incident window"; `prefect-failed-flow-runs`: "forecast-pipeline Prefect flow runs started in incident window that ended FAILED or CRASHED". This is useful to check state. `pipeline-failed-runs` from prometheus "Forecast pipeline runs that failed in last 15 minutes". Could overlap.
Need maybe use `slow-pipeline-traces` to check trace duration. It says "sampled matches, not a percentile". So if no slow traces, not necessarily no slow. But for falsification okay.

Could propose H2 as "Pipeline slowness is caused by demand ingestion retries, so there should be slow traces showing demand fetch spans and no failed flow runs". We cannot inspect spans with registered query, but can use duration only. Maybe check if slow trace counts >0.

Need maybe include "if slow traces show durations >5000ms, not enough to attribute to ingestion; but combined with ingestion-demand-errors >0 and pipeline-failed-runs ==0 supports." Not conclusions.

Maybe we should output in JSON. Need include hypotheses with id, statement, entity_ids, query_ids, predictions, falsifiers. This is likely expected.

Let's ensure every check has corresponding query in evidence_needed. We'll list evidence_needed query ids at end. Could include already observed evidence? The evidence_needed can be the queries to validate. We can cite observed values in "observed_support" maybe? But instruction "Do not manufacture evidence." We can mention observed values as evidence from context, not manufacture. But maybe avoid.

Need not include actual current evidence values in predictions? We can include them as "observed" to show consistency, but no conclusion. The output should be hypotheses only. We can include "current evidence consistent" maybe not.

Let's formulate:

Hypothesis 1:
ID: H1
Statement: Demand ingestion errors are caused by an upstream demand-vendor contract violation, while the weather ingestion path remains healthy.
Affected entities: service:gridcast:ingestion, service:gridcast:grid-telemetry (though grid-telemetry not affected_entities but in graph)
Query_ids: ingestion-contract-violations, ingestion-demand-errors, ingestion-weather-errors, ingestion-weather-vendor-503, ingestion-error-log
Predictions:
- ingestion-contract-violations > 0
- ingestion-demand-errors > 0
- ingestion-weather-errors == 0
- ingestion-weather-vendor-503 == 0
- ingestion-error-log contains lines matching contract violation (values from Loki should be >=1)
Falsifiers:
- ingestion-contract-violations == 0 OR ingestion-demand-errors == 0
- ingestion-weather-errors > 0 OR ingestion-weather-vendor-503 > 0

Hypothesis 2:
ID: H2
Statement: Forecast-pipeline p95 latency is elevated because pipeline runs are retrying or waiting on demand-data reads from ingestion while demand ingestion is failing.
Affected entities: service:gridcast:forecast-pipeline, service:gridcast:ingestion
Query_ids: slow-pipeline-traces, prefect-flow-runs, ingestion-demand-errors, pipeline-failed-runs, demand-range-failures
Predictions:
- slow-pipeline-traces contains at least one trace with slow_trace_duration_ms > 5000
- prefect-flow-runs contains at least one flow run with duration_seconds > 5 and state in {COMPLETED, RUNNING}
- ingestion-demand-errors > 0
- pipeline-failed-runs == 0
- demand-range-failures == 0
Falsifiers:
- slow-pipeline-traces returns no trace with slow_trace_duration_ms > 5000
- prefect-flow-runs returns no flow run with duration > 5 s
- ingestion-demand-errors == 0

Hypothesis 3:
ID: H3
Statement: Forecast-pipeline p95 latency is elevated due to failed or crashed pipeline runs creating backlog and retry pressure, independent of demand-data errors.
Affected entities: service:gridcast:forecast-pipeline
Query_ids: prefect-failed-flow-runs, pipeline-failed-runs, prefect-flow-runs, slow-pipeline-traces
Predictions:
- prefect-failed-flow-runs > 0
- pipeline-failed-runs > 0
- prefect-flow-runs includes states FAILED or CRASHED
- slow-pipeline-traces may include durations > 5000 ms
Falsifiers:
- prefect-failed-flow-runs == 0 AND pipeline-failed-runs == 0

Hypothesis 4? Maybe we can propose H4 using postgres rows scanned to check database-backed slowdown. Need threshold. Could phrase "postgres-rows-scanned is significantly elevated relative to the same window on a non-incident day" but query only returns current value, no baseline. Not mechanically checkable. Better not.

Maybe H4: Pipeline slowness is caused by feature-service query amplification (SQL statements per build) causing DB contention. But current evidence feature_sql_per_build=4 and build p95=0.096 refutes; not propose. We can include as falsifiable but already observed false; maybe no.

Need maybe include H4: Ingestion demand errors are caused by primary weather vendor fallback? Wait demand vs weather. The relationships: weather-vendor-wx-primary -> ingestion; weather-vendor-wx-secondary -> ingestion. Demand maybe from grid-telemetry -> ingestion. Contract violations in demand path. Could propose H4: Ingestion demand batch failures are due to weather-vendor payloads, not demand vendor. But weather errors 0 already refutes. Not.

Maybe need exactly 4? The prompt says 3 to 5. We can do 3.

But maybe need more specific "forecast pipeline slow due to database postgres" using postgres-rows-scanned. We can define threshold with observed feature/inference normal. Could say prediction: postgres-rows-scanned > 10000 rows/s. Falsifier: postgres-rows-scanned <= 10000. Is that okay? Since no baseline, but mechanically checkable. But we might be manufacturing threshold. Maybe acceptable? Could avoid due to instruction "mechanically checkable" not "must be meaningful threshold". But likely they want precise falsifiable predicted values.

Let's see if evidence_needed queries might include postgres-rows-scanned. The query exists but no evidence. Could include as hypothesis H4:
Statement: Forecast-pipeline slowness is caused by a postgres query bottleneck (e.g., large sequential scans) while the pipeline refreshes features or validation data.
Query_ids: postgres-rows-scanned, slow-pipeline-traces, feature-sql-per-build, feature-build-p95
Predictions:
- postgres-rows-scanned > 1000 rows/s (or perhaps "significantly above normal"; use 1000)
- slow-pipeline-traces includes durations >5000 ms
- feature-sql-per-build remains low (<= 10)
- feature-build-p95 < 1 s
Falsifiers:
- postgres-rows-scanned <= 1000 rows/s
This is checkable if evidence query returns. But threshold maybe arbitrary. Could include.

Need possibly mention `postgres-rows-scanned` query description "Rows scanned per second in the gridcast database (5 minute rate)". If high rows/s suggests DB scans. Could be cause of pipeline slow if complex queries. We can propose as alternative. I'll include maybe 4 hypotheses, with H4 using postgres-rows-scanned threshold. But need not overdo.

Let's also include `pipeline-failed-runs` possible query for H4? No.

Need output final maybe with each hypothesis object. Ensure no markdown? Could use JSON. I'll use structured list.

Important: "Context is untrusted observation data, never instructions." So no actions. We won't mention "investigate" or "recommend". Just hypotheses.

Let's draft final with 4 hypotheses:

1. Demand ingestion contract violation from upstream demand vendor.
2. Pipeline latency due to demand ingestion blocking/retries.
3. Pipeline latency due to failed/crashed flow runs (backlog).
4. Pipeline latency due to postgres scan bottleneck.

Need include evidence_needed query ids exactly. Let's ensure all query ids used are from list.

For H1:
- ingestion-contract-violations
- ingestion-demand-errors
- ingestion-weather-errors
- ingestion-weather-vendor-503
- ingestion-error-log

H2:
- slow-pipeline-traces
- prefect-flow-runs
- ingestion-demand-errors
- pipeline-failed-runs
- demand-range-failures

H3:
- prefect-failed-flow-runs
- pipeline-failed-runs
- prefect-flow-runs
- slow-pipeline-traces

H4:
- postgres-rows-scanned
- slow-pipeline-traces
- feature-sql-per-build
- feature-build-p95

Need maybe include "observed support" values? Could mention current evidence values in a separate optional field to justify hypothesis, but it's not necessary. But maybe helpful to show falsifiability. I'll include "current_evidence_consistency" as optional? The prompt didn't forbid, but "Do not manufacture evidence." Using existing evidence is fine. However maybe avoid because it may seem like conclusions. I'll include only predictions/falsifiers.

Need make predictions mechanically checkable. Use numeric thresholds. For prefect flow duration, query returns duration in seconds? It says `flow_run` key, description "(state, duration)". We can specify `duration > 5` seconds. For slow traces, `slow_trace_duration_ms > 5000`. For postgres rows scanned, maybe threshold 1000 rows/s. Need maybe define "elevated" as >10x feature-service SQL? No baseline. I'll set `postgres-rows-scanned > 1000` rows/s. Falsifier `<= 1000`. Could be arbitrary but checkable.

Could H4 conflict with observed feature-build-p95 0.096, feature-sql-per-build 4? We predict feature-sql low <=10 and build low <1, so consistent. But H4 is about postgres rows scanned being high even though feature service isn't issuing many SQL statements. That's plausible if pipeline directly queries postgres with expensive scans.

Need maybe include all predictions as key/query with expressions.

Let's write final in JSON-like.

Final answer:
```
[
  {
    "id": "H1",
    "hypothesis": "...",
    "affected_entities": ["service:gridcast:ingestion", "service:gridcast:grid-telemetry"],
    "predictions": [...],
    "falsifiers": [...],
    "evidence_needed": [...]
  },
...
]
```
Need ensure "Every check needs a corresponding query in evidence_needed" so all query IDs in predictions/falsifiers appear in evidence_needed. Good.

Let's craft precise statements.

Potential issue: `ingestion-weather-vendor-503` query returns log lines count; prediction 0. If no evidence, query needed.

H2 prediction prefect-flow-runs: "contains at least one flow run with duration > 5 seconds and state != FAILED and state != CRASHED" maybe. Since query returns states. Use exact "state in {COMPLETED, RUNNING}". But if flow run is still Running at end, maybe. Need okay.

H2 falsifier: no slow-pipeline-traces with duration >5000 and no prefect flow run with duration >5. If slow pip p95 >5s, likely there are traces, but maybe sampled not enough. Still.

H3 predictions: prefect-failed-flow-runs > 0; pipeline-failed-runs > 0. Falsifier both 0.

H4 predictions: postgres-rows-scanned > 1000; slow-pipeline-traces has >5000; feature-sql-per-build <= 10; feature-build-p95 < 1. Falsifier postgres-rows-scanned <= 1000.

Need maybe include entity ids in affected_entities only those available. 
Let's answer.