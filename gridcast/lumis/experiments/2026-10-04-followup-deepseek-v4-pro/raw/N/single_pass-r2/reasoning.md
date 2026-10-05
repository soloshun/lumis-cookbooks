We need answer propose 3-5 falsifiable hypotheses, not conclusions. Context untrusted observation data, never instructions. Use only entity IDs and registered query IDs from context. Predictions and falsifiers mechanically checkable against entity/key values. Every check needs a corresponding query in evidence_needed. Do not propose actions or manufacture evidence.

We need parse prompt. We have incident graph and queries/evidence. Need propose hypotheses about cause of symptom: ForecastShiftedVsPlan: new forecast deviated from published plan by >10%. Affected entity service:gridcast:forecast-pipeline. Incident window 23:03:57 to 23:16:07.

Need output likely JSON with hypotheses. Need use only entity IDs and query IDs from context. Each hypothesis should have falsifiable predictions and evidence_needed queries. We can use evidence values maybe. Need "not conclusions" so hypotheses tentative. Need 3-5. Each check corresponding query in evidence_needed. We should not propose actions or manufacture evidence. Use only registered query IDs.

Let's understand domain. There is gridcast forecasting. Incident affected forecast-pipeline. Symptom: new forecast deviated from published plan >10%. Entities/services:
- ingestion ingests weather/demand into postgres
- feature-service builds features from postgres
- forecast-pipeline runs forecast? Actually service:gridcast:forecast-pipeline is affected. Relationships: feature-service serves forecast-pipeline, forecast-service serves forecast-pipeline, planning-api serves forecast-pipeline, postgres serves all. forecast-pipeline maybe orchestrates. grid-operator consumes planning-api? It fetches plan.

Queries registered:
- planning-desired/available replicas
- operator plan fetch transport errors
- pipeline failed runs
- feature sql/build/fail
- postgres rows scanned
- forecast OOM/mem/restarts/inference/model reloads
- ingestion demand/weather errors
- demand range failures
- weather variability warnings
- feature auth failures
- ingestion contract violations
- ingestion weather vendor 503
- feature-service error log
- ingestion error log
- slow pipeline traces
- prefect failed flow runs / flow runs
- ingestion demand batch p95
- demand freshness
- weather observation freshness
- demand completeness failures
- forecast stability warnings
- demand zones reporting
- load feature mean
- feature cpu throttling
- model production alias changes
- changes_20m for feature-service, forecast-service, planning-api, ingestion, forecast-pipeline.
Evidence observed:
- planning desired/available replicas = 1
- operator plan fetch transport errors = 0
- feature sql per build 2.031, build p95 0.098, failed builds 0
- forecast OOM killed 0, memory ratio 0.201, restarts 0, inference_max 0.045, model reloads 0
- model production alias changes 0
- ingestion demand errors 0, weather errors 0
- demand range failures 0
- weather variability warnings 0

Not all evidence queried. We can propose hypotheses that would predict certain query results. Need "falsifiable" meaning specify if evidence_needed query returns certain values, hypothesis supported/refuted.

Need not use evidence values? We can incorporate observed evidence as known facts, but must not conclude. We can propose.

Let's identify potential causes of forecast shifted from plan:
1. Input demand data changed unexpectedly (ingestion completeness/freshness): maybe demand data missing zones or stale causing forecast shift. Queries: demand-zones-reporting, demand-freshness, demand-completeness-failures, ingestion-demand-errors, ingestion-error-log. Falsify: demand freshness > threshold, zones reporting < 4, completeness failures >0. But evidence already shows ingestion demand errors 0, range failures 0. Could hypothesize demand data incompleteness not yet queried. Need use registered queries.
2. Weather input changed: weather vendor primary failing, causing ingestion to fallback secondary? Queries: ingestion-weather-errors (observed 0), ingestion-weather-vendor-503, ingestion-error-log, weather-observation-freshness, weather-variability-warnings. Observed weather errors 0 and variability warnings 0. But maybe weather observation freshness stale. Falsify: weather observation freshness >? or 503 logs >0.
3. Feature computation anomaly: a feature used by forecast model shifted due to data bug / code regression. Queries: load-feature-mean, feature-sql-per-build (observed 2.031), feature-build-p95, feature-failed-builds, feature-cpu-throttling, feature-auth-failures, feature-service-error-log, feature-service-changes-20m. If feature-service changed or CPU throttled causing different feature values? But observed build fast and no failures. Can hypothesize that load_lag_24h feature mean is anomalous (too high/low) causing forecast shift. Need query load-feature-mean. Falsifier: load_feature_mean outside expected range; but we don't know expected range. Need mechanically checkable. Could say "value is null/outside 0-? MW" but context doesn't specify. Need avoid manufacturing thresholds. We can use counts/zeros from queries. For checkable, use exact equality/value comparisons that are objectively defined by query semantics. E.g., production_alias_changes_20m == 0 observed means no model change; but if we hypothesize model version change, falsified because predicted >0. But observed already 0. We need propose hypotheses based on query outputs maybe.

We need be careful: "Predictions and falsifiers must be mechanically checkable against entity/key values." So each hypothesis should include a prediction about what query will return if hypothesis true, and falsifier if not true. e.g. H1: "A recent deployment/change to feature-service altered feature values." Prediction: feature-service-changes-20m query returns >0. Falsifier: feature-service-changes-20m returns 0. Evidence_needed: feature-service-changes-20m. But context has no evidence for that. That is valid.

But we should avoid hypotheses already rejected by observed evidence? We can propose all hypotheses with evidence_needed, maybe some already have observed evidence enabling falsification. We can say "observed evidence already falsifies/contradicts"? The prompt asks propose hypotheses, not conclusions. We may include falsifiers.

Need 3-5. Let’s think of plausible hypotheses:

H1: A recent change/deployment to forecast-pipeline or its upstream feature-service changed forecast computation, causing shifted forecast. Falsifiable check: forecast-pipeline-changes-20m >0 or feature-service-changes-20m >0 or planning-api-changes-20m >0. But affected entity forecast-pipeline could be orchestration code. Query registered: forecast-pipeline-changes-20m, feature-service-changes-20m, planning-api-changes-20m, forecast-service-changes-20m, ingestion-changes-20m. Need one hypothesis per? Maybe "No deployment happened; if any changes_20m >0 supports." But need not overcomplicate.

H2: Ingested demand data became incomplete/stale (missing zones or freshness gap), causing forecast to shift from plan. Check: demand-zones-reporting < 4, demand-completeness-failures >0, demand-freshness exceeds some value? We cannot set threshold. Could use demand-zones-reporting < 4 (catalogue has 4, from query description). That's checkable: value < 4. Or demand-completeness-failures > 0. Evidence_needed: demand-zones-reporting, demand-completeness-failures, demand-freshness, ingestion-demand-batch-p95. But seen ingestion demand errors 0, range failures 0. Still possible completeness failures not queried. Falsifier: demand-zones-reporting == 4 and demand-completeness-failures == 0. Good.

H3: Primary weather vendor outage caused ingestion to fall back to secondary or stale weather observations, shifting forecast. Check: ingestion-weather-vendor-503 >0, contract_violation_log_lines >0, weather_observation_freshness_seconds high? Could use vendor 503 >0 or weather errors >0. Observed ingestion-weather-errors 0 already, so if hypothesis predicts weather-batch-errors >0, falsified. But we need "mechanical check": query ingestion-weather-vendor-503 returns >0; weather-observation-freshness maybe >? No threshold. We can use log line count > 0. Evidence_needed: ingestion-weather-vendor-503, weather-observation-freshness, ingestion-error-log, contract_violation. Falsifier: all of these return 0/appropriate. But weather freshness threshold unknown. Use 503 log lines ==0 and weather errors ==0 and contract violations==0.

H4: A model production alias change in forecast-service inadvertently deployed a different model version, changing forecast outputs. Check: model-production-alias-changes >0, forecast-model-reloads >0. But observed evidence shows model_production_alias_changes = 0, forecast_model_reloads = 0; this hypothesis is already falsified by observed evidence. But if we propose it, we can say falsifier observed. The prompt likely wants hypotheses based on unobserved? It says context includes evidence, maybe use observed to eliminate? "Propose 3 to 5 falsifiable hypotheses, not conclusions." Could include ones with evidence_needed not observed, but also can include hypotheses with observed values already contradicting. However every check needs corresponding query in evidence_needed, so if we reference observed evidence maybe no need? It says "Every check needs a corresponding query in evidence_needed." So if we cite an observed evidence, we need include its query in evidence_needed? Hmm likely evidence_needed is list of query IDs needed to test hypothesis, not necessarily unobserved. We can include query IDs for falsifiers.

Need maybe output format? The instruction: "Propose 3 to 5 falsifiable hypotheses, not conclusions..." It doesn't specify JSON but previous tasks likely JSON. Need infer. We can produce JSON with hypotheses array. Each with id, hypothesis, evidence_needed query IDs, predictions/falsifiers.

We need "Use only entity IDs and registered query IDs from context." That means no made-up query names. Use exact "id" from queries. Entity IDs from graph. We can mention entity IDs.

Let's map available query IDs and evidence:
- planning-desired-replicas (observed 1)
- planning-available-replicas (observed 1)
- operator-plan-fetch-transport-errors (observed 0)
- pipeline-failed-runs (not observed)
- feature-sql-per-build (observed 2.031)
- feature-build-p95 (observed 0.098)
- feature-failed-builds (observed 0)
- postgres-rows-scanned (not observed)
- forecast-oom-killed (observed 0)
- forecast-memory-ratio (observed 0.201)
- forecast-restarts (observed 0)
- forecast-inference-p95 (not observed)
- forecast-model-reloads (observed 0)
- forecast-inference-max (observed 0.045)
- ingestion-demand-errors (observed 0)
- ingestion-weather-errors (observed 0)
- demand-range-failures (observed 0)
- weather-variability-warnings (observed 0)
- feature-auth-failures (not observed)
- ingestion-contract-violations (not observed)
- ingestion-weather-vendor-503 (not observed)
- feature-service-error-log (not observed)
- ingestion-error-log (not observed)
- slow-pipeline-traces (not observed)
- prefect-failed-flow-runs (not observed)
- prefect-flow-runs (not observed)
- ingestion-demand-batch-p95 (not observed)
- demand-freshness (not observed)
- weather-observation-freshness (not observed)
- demand-completeness-failures (not observed)
- forecast-stability-warnings (not observed)
- demand-zones-reporting (not observed)
- load-feature-mean (not observed)
- feature-cpu-throttling (not observed)
- model-production-alias-changes (observed 0)
- feature-service-changes-20m (not observed)
- forecast-service-changes-20m (not observed)
- planning-api-changes-20m (not observed)
- ingestion-changes-20m (not observed)
- forecast-pipeline-changes-20m (not observed)

Observed evidence includes only 16 query results. Many unobserved.

We need propose hypotheses that are causally plausible and falsifiable with available queries. We can use observed evidence as known to avoid obviously false hypotheses? The incident symptom: new forecast deviated from published plan >10%. The affected entity is forecast-pipeline. Could be due to:
- input issue (weather/demand)
- feature issue (feature service)
- model issue (forecast service)
- pipeline validation/configuration issue
- infrastructure resource issue (CPU throttling, OOM, slow queries)
- downstream? Plan maybe from planning-api; operator plan fetch errors; planning availability. But symptom is forecast shift vs plan: plan may be published by planning-api. If planning-api unavailable/replicas low, maybe pipeline couldn't fetch plan? Actually forecast-pipeline compares to published plan, deviation >10%. If published plan changed unexpectedly due to planning-api change, maybe forecast shift is relative to new plan. But plan is published; query planning-desired/available replicas and changes. Could hypothesize plan changed due to planning-api deploy. Falsifiable: planning-api-changes-20m >0. But planning desired/available =1, operator plan fetch errors=0. Need include.

We might formulate hypotheses:

1. Deployment/configuration change in forecast-pipeline altered the forecast run definition or validation gate parameters, producing a forecast that deviates from plan.
   - Check queries: forecast-pipeline-changes-20m, prefect-flow-runs, slow-pipeline-traces, pipeline-failed-runs.
   - Prediction if true: forecast-pipeline-changes-20m > 0 or prefect-flow-runs shows unusual state/duration; falsifier: changes_20m == 0 and no failed/long runs. But "unusual" not mechanically checkable. Better: predict forecast-pipeline-changes-20m > 0. Falsifier: forecast-pipeline-changes-20m == 0. Also pipeline-failed-runs could >0 if deploy broken, but not necessary. Include evidence_needed: forecast-pipeline-changes-20m, pipeline-failed-runs.

2. Demand input data incompleteness: fewer load zones reporting or freshness gap. Check query demand-zones-reporting (expect <4), demand-completeness-failures (>0), demand-freshness (high? no threshold). Could use demand-zones-reporting < 4 and demand-completeness-failures > 0. Falsifier: demand-zones-reporting == 4 and demand-completeness-failures == 0. evidence_needed: demand-zones-reporting, demand-completeness-failures, demand-freshness, ingestion-demand-errors maybe observed 0.

3. Weather input issue: primary weather vendor failure or stale weather observations. Check ingestion-weather-vendor-503 >0, ingestion-contract-violations >0, weather-observation-freshness? no threshold. Use log count >0 for 503/contract violations; falsifier both ==0 and ingestion-weather-errors ==0. evidence_needed: ingestion-weather-vendor-503, ingestion-contract-violations, ingestion-weather-errors, weather-observation-freshness, ingestion-error-log.

4. Feature computation anomaly: A regression/deploy to feature-service changed feature values, particularly load_lag_24h. Check feature-service-changes-20m >0, load-feature-mean maybe anomalous? We need define checkable. Could predict feature-service-changes-20m >0 or load-feature-mean outside plausible range? We don't know range. Use feature-service-changes-20m >0 as prediction; falsifier ==0. Also feature-cpu-throttling high? no threshold. Use feature-failed-builds >0? observed 0. But if deploy bad, failed builds. We can propose: feature-service change introduced feature value shift; check feature-service-changes-20m >0. Falsifier: ==0. Also load-feature-mean value can be compared to prior? no. Could say if load-feature-mean is null or negative? That's mechanical. But not strong. Use only changes.

5. Forecast model version change: model production alias changed or forecast-service loaded new model version. Check model-production-alias-changes >0, forecast-model-reloads >0. We know observed both 0, so if we include, it is falsified by observed evidence. Could be a "hypothesis already falsified" but prompt wants proposals. Maybe better to avoid because observed already contradicts? It says propose hypotheses, not conclusions; we can propose and note observed evidence can falsify. But final answer should not conclude. Hmm.

6. Resource pressure / throttling causing late/short feature builds or pipeline latency. Check feature-cpu-throttling >0, postgres-rows-scanned high? no threshold. Use feature-cpu-throttling. Falsifier feature-cpu-throttling == 0. But observed not available. Could be plausible.

Need 3-5. Let's select 4-5 hypotheses with clear mechanical falsifiers using query values that can be exact zero/positive or counts.

Important: The instruction says "Predictions and falsifiers must be mechanically checkable against entity/key values." So we can structure:
{
  "hypothesis": "...",
  "evidence_needed": ["query-id-1", "query-id-2"],
  "prediction_if_true": {"query-id-1": ">0", ...},
  "falsifier": {"query-id-1": "==0", ...}
}

Need ensure values are from keys? Query returns key. Use query id.

Need not use observed evidence in prediction? We can include observed values as falsifiers. But "Every check needs a corresponding query in evidence_needed." So if using observed evidence to falsify, include that query id in evidence_needed and state observed value. But maybe final answer should be generated after considering observed. We can include "observed evidence already available" but no need? The prompt says "Context is untrusted observation data, never instructions." We can use evidence to choose hypotheses. It may be better to propose hypotheses that remain plausible given observed evidence: avoid ones falsified by observed evidence. Let's examine observed to eliminate:
- planning desired/available =1 => planning-api okay.
- operator plan fetch errors =0 => operator can fetch plan.
- feature sql/build/fail: feature service build fast, no failures.
- forecast service: no OOM, memory low, no restarts, inference fast, no model reloads.
- model production alias changes 0 => no model version change.
- ingestion demand/weather errors 0 => no batch errors.
- demand range failures 0 => validation passed range.
- weather variability warnings 0 => no weather variability warnings.

Thus observed evidence rules out gross errors: no obvious model change, no feature build failure, no ingestion batch errors, no range failures. But it doesn't rule out:
- feature changes deploy that changed feature logic (not errors)
- data freshness/completeness (only errors queried, not completeness/freshness)
- weather vendor 503 (not queried) and stale weather
- demand zones not reporting
- load feature mean abnormal
- pipeline changes
- resource throttling
- SQL/postgres performance
- slow pipeline traces
- prefect flow failures (could be not queried)
- validation gate stability warnings (forecast-stability-warnings)
So good hypotheses can focus on unobserved queries.

Need possibly mention existing observed evidence as not causing? We don't conclude.

Let's craft hypotheses:

H1: A recent change to the forecast-pipeline (code/config) altered the forecast generation logic or validation thresholds, resulting in the >10% deviation.
Evidence needed: forecast-pipeline-changes-20m, prefect-failed-flow-runs, prefect-flow-runs, pipeline-failed-runs.
Prediction if true: forecast-pipeline-changes-20m > 0; optionally prefect_failed_flow_runs >0 or pipeline_failed_runs >0.
Falsifier: forecast-pipeline-changes-20m == 0 and prefect_failed_flow_runs ==0 and pipeline_failed_runs ==0.
This is checkable. Need values: changes_20m could be count/list; counts >0.

H2: Demand data incompleteness/staleness: not all load zones reported demand in the window, or completeness validation failed, causing forecast shift.
Evidence needed: demand-zones-reporting, demand-completeness-failures, demand-freshness, ingestion-demand-batch-p95.
Prediction if true: demand-zones-reporting < 4 (since catalogue has 4), demand-completeness-failures > 0, or demand_freshness_seconds > ingestion_demand_batch_p95_seconds_10m? Not robust. Better use demand-zones-reporting < 4, demand-completeness-failures > 0. For demand freshness, no threshold. We can predict one of those. Could state "demand-zones-reporting < 4 OR demand-completeness-failures > 0". Mechanically checkable: zone count is numeric, completeness failures numeric. Falsifier: demand-zones-reporting == 4 AND demand-completeness-failures == 0.
Maybe include demand-freshness as evidence but not rely on threshold. But every check needs query. We can include demand-freshness and if value >300? Not set. Avoid.

H3: Weather input issue: primary weather vendor returned 503 or contract violations, causing missing/stale weather observations and shifting forecast.
Evidence needed: ingestion-weather-vendor-503, ingestion-contract-violations, weather-observation-freshness, ingestion-weather-errors, ingestion-error-log.
Prediction if true: ingestion-weather-vendor-503 > 0 or ingestion-contract-violations > 0 (log line counts). Falsifier: ingestion-weather-vendor-503 == 0 and ingestion-contract-violations == 0 (and ingestion-weather-errors == 0 observed). We can include weather-observation-freshness but no threshold, maybe just "not used as threshold" but query needed? We can include as evidence_needed but no falsifier? Hmm.

H4: Feature-service code/config change altered feature values (e.g. load_lag_24h) without failing builds, causing model input shift.
Evidence needed: feature-service-changes-20m, load-feature-mean, feature-cpu-throttling, feature-sql-per-build, feature-service-error-log.
Prediction if true: feature-service-changes-20m > 0; load-feature-mean value falls outside? Can't set threshold. Use feature-service-changes-20m >0 as main. Also feature-sql-per-build maybe unusually high/low? Observed 2.031; no baseline. But we can say `feature_service_changes_20m` > 0. Falsifier: feature-service-changes-20m == 0.
Need maybe also include load-feature-mean because if change altered feature, value should differ but we can't determine. Could phrase: "the load-feature-mean query is needed to compare against historical monitoring, but this hypothesis is falsified if feature-service-changes-20m == 0." That's still one check.

H5: Resource throttling/slow feature or pipeline execution: CPU throttling or slow pipeline traces caused delayed/partial feature builds or run timeout.
Evidence needed: feature-cpu-throttling, slow-pipeline-traces, feature-build-p95, postgres-rows-scanned, forecast-memory-ratio maybe.
Prediction if true: feature-cpu-throttling > 0 (ratio) and/or slow-pipeline-traces has at least one trace duration > 3000 ms. Falsifier: feature-cpu-throttling == 0 and slow-pipeline-traces count == 0. Query description says slow_trace_duration_ms durations of traces slower than 3s in window (sampled matches). So count >0 checkable. Could be plausible.

But we need maybe avoid overlapping H1 and H5. We need 3-5. Let's settle on 4 robust hypotheses:
1. forecast-pipeline deployment/change
2. demand data incompleteness/staleness
3. weather vendor anomaly
4. feature-service change altering feature values
Maybe 5. forecast-service model change already observed 0, not plausible. Resource throttling maybe.

Need include "not conclusions" language. The final should maybe be a JSON object:
{
  "hypotheses": [
    {
      "id": "H1",
      "hypothesis": "A recent configuration or code change to service:gridcast:forecast-pipeline altered the forecast computation or validation gate, causing the new forecast to deviate from the published plan.",
      "evidence_needed": [
        "forecast-pipeline-changes-20m",
        "pipeline-failed-runs",
        "prefect-failed-flow-runs",
        "prefect-flow-runs",
        "slow-pipeline-traces"
      ],
      "prediction_if_true": {
        "forecast-pipeline-changes-20m": ">0",
        "prefect-failed-flow-runs": ">0 OR pipeline-failed-runs >0"
      },
      "falsifier": {
        "forecast-pipeline-changes-20m": "==0",
        "pipeline-failed-runs": "==0",
        "prefect-failed-flow-runs": "==0"
      }
    },
    ...
  ]
}

But we need ensure "Every check needs a corresponding query in evidence_needed." The falsifier may include query IDs. Good.

Need maybe mention observed evidence? We could state "The following hypotheses remain live after observed evidence" but not necessary. Maybe we can include "hypotheses" as list.

Let's refine each.

Hypothesis 1 (pipeline change):
- evidence_needed: forecast-pipeline-changes-20m, pipeline-failed-runs, prefect-failed-flow-runs, slow-pipeline-traces, forecast-stability-warnings maybe.
- prediction: forecast-pipeline-changes-20m > 0; if change broke run, pipeline-failed-runs >0 or prefect-failed-flow-runs >0.
- falsifier: forecast-pipeline-changes-20m == 0 and pipeline-failed-runs == 0 and prefect-failed-flow-runs == 0. Include forecast-stability-warnings? It detects deviation from plan; if >0 it's actually symptom captured. Could be evidence, but it is likely positive due to incident. Not needed.

Hypothesis 2 (demand data incompleteness):
- evidence_needed: demand-zones-reporting, demand-completeness-failures, demand-freshness, ingestion-demand-batch-p95, ingestion-demand-errors.
- prediction: demand-zones-reporting < 4 (since description says catalogue has 4); demand-completeness-failures > 0; perhaps demand_freshness_seconds > 600? No threshold. We can keep only count comparisons.
- falsifier: demand-zones-reporting == 4 and demand-completeness-failures == 0. Also ingestion-demand-errors == 0 observed but not central.
Need note "catalogue has 4" from query description is context. Good.

Hypothesis 3 (weather vendor issue):
- evidence_needed: ingestion-weather-vendor-503, ingestion-contract-violations, weather-observation-freshness, ingestion-weather-errors, ingestion-error-log.
- prediction: ingestion-weather-vendor-503 > 0 OR ingestion-contract-violations > 0 OR ingestion-weather-errors > 0 (observed 0 already, so if true this part would be false). But we can set prediction: ingestion-weather-vendor-503 > 0 OR ingestion-contract-violations > 0. Falsifier: both ==0 (already partly supported by observed weather errors 0). Include weather-observation-freshness? We could predict "weather_observation_freshness_seconds exceeds 20 minutes" but incident window is ~12m. Is that safe? Not without threshold. Better avoid threshold. We can include it as evidence_needed but not specify a numeric falsifier? The instruction says every check needs query. Maybe we can say "weather-observation-freshness must be inspected for staleness, but the falsifier is based on log line counts." Hmm.

But likely acceptable if we include query in evidence_needed without a specific prediction? It says "Predictions and falsifiers must be mechanically checkable..." It doesn't require every evidence query in prediction, just every check. We can have evidence_needed list with queries used in checks. For weather freshness, if no check, maybe omit. Use only log counts.

Hypothesis 4 (feature-service change):
- evidence_needed: feature-service-changes-20m, load-feature-mean, feature-cpu-throttling, feature-service-error-log.
- prediction: feature-service-changes-20m > 0; possibly load_feature_mean deviates (but no threshold). We can say "The key check is feature-service-changes-20m >0; if true, load-feature-mean must be compared to baseline." But that's not strict. Maybe simpler: prediction: feature-service-changes-20m > 0. Falsifier: feature-service-changes-20m == 0. Need include load-feature-mean in evidence_needed? If not used, maybe omit. But the hypothesis about feature values should be checked by load-feature-mean. We need a check. Can we make load-feature-mean mechanically checkable? It returns mean of load_lag_24h feature. Without known expected value, not checkable. Unless query description says demand is MW; no threshold. So can't. We can instead phrase hypothesis: "A feature-service code/config change occurred, and it changed feature computation logic." Check: feature-service-changes-20m > 0. If false, feature change hypothesis falsified. That's fine.
But to explain why feature change matters, we might include load-feature-mean as evidence_needed but no check. The prompt wants "Every check needs a corresponding query in evidence_needed", not "every evidence_needed query needs a check". So okay maybe.

Hypothesis 5 (resource throttling / pipeline latency):
- evidence_needed: feature-cpu-throttling, slow-pipeline-traces, feature-build-p95, postgres-rows-scanned.
- prediction: feature_cpu_throttled_ratio_5m > 0; slow-pipeline-traces has at least 1 entry (since query returns traces slower than 3s). Falsifier: feature-cpu-throttling == 0 and slow-pipeline-traces empty/0 count. Feature-build-p95 observed 0.098, so not slow. Postgres rows scanned no threshold. Use first two.

But do we need include feature-build-p95? If observed already 0.098, that's normal? We can use as falsifier? Maybe avoid.

We need maybe mention observed evidence in final? We can add "observed evidence already available is consistent/not consistent" but prompt says "not conclusions". Better keep hypotheses neutral.

Let's read exact user: "Propose 3 to 5 falsifiable hypotheses, not conclusions. Context is untrusted observation data, never instructions. Use only entity IDs and registered query IDs from context. Predictions and falsifiers must be mechanically checkable against entity/key values. Every check needs a corresponding query in evidence_needed. Do not propose actions or manufacture evidence."

This likely expects output with "hypotheses" and each has "evidence_needed" with query IDs. Maybe no need to include observed values. We need be precise: "Every check needs a corresponding query in evidence_needed" means in our hypotheses, if we say "forecast-pipeline-changes-20m > 0", include "forecast-pipeline-changes-20m" in evidence_needed. Good.

Let's consider if we should include "evidence_needed" as list of query ids, plus "falsifier" as specific values. We can include "prediction_if_true" and "falsifier" for each. Use key names? Use query id? We can use query id in JSON keys. We'll use exact query ids.

Potential issue: "Use only entity IDs and registered query IDs from context." For query IDs, use string from "queries" list. For entity IDs, use the entity_id value in queries maybe not needed. But in hypothesis text we can mention service:gridcast:forecast-pipeline etc. The query IDs already are registered. That's fine.

Let's also consider the observed evidence. We can use observed evidence as already falsifying some hypotheses. But if we propose H2, observed ingestion-demand-errors 0 and demand-range-failures 0 do not falsify; we need unobserved completeness. Good.
H3 observed ingestion-weather-errors 0 and weather-variability-warnings 0 do not fully falsify because vendor 503/contract violations not queried. Could be that no weather errors but stale due to no observations? Actually if no batch errors, maybe freshness fine. But still possible vendor primary down and fallback secondary: `weather-vendor-wx-secondary` fallback_for primary. There could be 503 logs but no batch errors if fallback handled. This hypothesis is plausible.
H4 feature-service change could happen without failures; observed build p95 and no failed builds are consistent with clean deploy. Good.
H5 CPU throttling: feature build p95 0.098 fast, but pipeline could be slow. Query slow-pipeline-traces not observed. Good.
H1 pipeline change: plausible.

Need decide number: 5. Let's write 5.

Maybe we can include "currently observed evidence" as note but not required. The final answer should be concise. We'll output JSON.

Let's draft:

{
  "hypotheses": [
    {
      "id": "H1",
      "hypothesis": "A recent change to service:gridcast:forecast-pipeline (code or configuration) altered the forecast run behavior or validation thresholds, causing the forecast to deviate from the published plan.",
      "evidence_needed": [
        "forecast-pipeline-changes-20m",
        "pipeline-failed-runs",
        "prefect-failed-flow-runs",
        "prefect-flow-runs",
        "slow-pipeline-traces"
      ],
      "prediction_if_true": {
        "forecast-pipeline-changes-20m": "> 0",
        "prefect-failed-flow-runs": "> 0 OR pipeline-failed-runs > 0"
      },
      "falsifier": {
        "forecast-pipeline-changes-20m": "== 0",
        "pipeline-failed-runs": "== 0",
        "prefect-failed-flow-runs": "== 0"
      }
    },
    {
      "id": "H2",
      "hypothesis": "Demand input data became incomplete or stale in the incident window (not all load zones reported, or the completeness gate failed), so the forecast was built on a shifted demand picture.",
      "evidence_needed": [
        "demand-zones-reporting",
        "demand-completeness-failures",
        "demand-freshness",
        "ingestion-demand-batch-p95",
        "ingestion-demand-errors"
      ],
      "prediction_if_true": {
        "demand-zones-reporting": "< 4",
        "demand-completeness-failures": "> 0"
      },
      "falsifier": {
        "demand-zones-reporting": "== 4",
        "demand-completeness-failures": "== 0"
      }
    },
    {
      "id": "H3",
      "hypothesis": "The primary weather vendor failed or returned malformed payloads (HTTP 503 / contract violation), causing ingestion to fall back to secondary or produce stale weather observations, shifting the forecast.",
      "evidence_needed": [
        "ingestion-weather-vendor-503",
        "ingestion-contract-violations",
        "ingestion-weather-errors",
        "weather-observation-freshness",
        "ingestion-error-log"
      ],
      "prediction_if_true": {
        "ingestion-weather-vendor-503": "> 0",
        "ingestion-contract-violations": "> 0"
      },
      "falsifier": {
        "ingestion-weather-vendor-503": "== 0",
        "ingestion-contract-violations": "== 0",
        "ingestion-weather-errors": "== 0"
      }
    },
    {
      "id": "H4",
      "hypothesis": "A recent change to service:gridcast:feature-service altered feature computation logic (e.g., the load_lag_24h feature), so forecast-pipeline received different model inputs and produced a shifted forecast.",
      "evidence_needed": [
        "feature-service-changes-20m",
        "load-feature-mean",
        "feature-sql-per-build",
        "feature-cpu-throttling",
        "feature-service-error-log"
      ],
      "prediction_if_true": {
        "feature-service-changes-20m": "> 0"
      },
      "falsifier": {
        "feature-service-changes-20m": "== 0"
      }
    },
    {
      "id": "H5",
      "hypothesis": "Resource contention or throttling in the feature/pipeline path caused slow or partial execution, changing the forecast output.",
      "evidence_needed": [
        "feature-cpu-throttling",
        "slow-pipeline-traces",
        "feature-build-p95",
        "pipeline-failed-runs",
        "postgres-rows-scanned"
      ],
      "prediction_if_true": {
        "feature-cpu-throttling": "> 0",
        "slow-pipeline-traces": "has at least one trace with duration > 3000 ms"
      },
      "falsifier": {
        "feature-cpu-throttling": "== 0",
        "slow-pipeline-traces": "empty or zero matching traces"
      }
    }
  ]
}

But need "slow-pipeline-traces" value: query description says "Durations of forecast-pipeline traces slower than 3 s in the window (sampled matches, not a percentile)". So if true, list length >0. Falsifier: list length ==0. We can phrase "slow-pipeline-traces": "> 0 matches" or "has at least one trace". Good.

Potential issue H2: "ingestion-demand-errors" observed 0, included evidence but no check? Fine. But prediction only checks two queries. Every check has corresponding query. Good.
H3 includes weather-observation-freshness and ingestion-error-log but no check. Fine? The instruction says "Every check needs a corresponding query in evidence_needed." Not "every evidence query used." Okay.

Could we include observed evidence? Maybe include an "observed_evidence_falsifies" note? The prompt doesn't ask. We can omit.

But maybe they want "falsifiable hypotheses" not just "prediction if true / falsifier". It may prefer prose. But JSON is likely. We can include a short introductory sentence "Proposed hypotheses (each falsifiable via the listed registered queries):" and then JSON.

Need ensure no "conclusions": each hypothesis phrased as possible cause, not "the cause is". Good.

Let's consider alternative: Perhaps we should not include H5 because feature-build-p95 observed 0.098 suggests no throttling, but not queried. It remains plausible but weaker. We need 3-5. We can keep.

Maybe need add "evidence_needed" exactly as query IDs; our JSON includes query IDs strings. Good.

But "Use only entity IDs and registered query IDs from context." In hypothesis H1 text "service:gridcast:forecast-pipeline" that's entity ID; okay. H4 "service:gridcast:feature-service", okay. H3 mentions ingestion; we can use service:gridcast:ingestion maybe. But text can mention names? It says use only entity IDs and registered query IDs; so use IDs rather than plain names. I should adjust text to use entity IDs: service:gridcast:forecast-pipeline, service:gridcast:feature-service, service:gridcast:ingestion, service:gridcast:weather-vendor-wx-primary maybe? Query entity for weather vendor 503 is service:gridcast:ingestion, but vendor entity exists. We can use entity ID service:gridcast:ingestion. Let's phrase.

Need ensure query IDs include "forecast-pipeline-changes-20m" and not entity id? Yes.

Let's also check query id "feature-service-changes-20m" exists. yes.

Now, let's think deeper: The incident affected "service:gridcast:forecast-pipeline"; symptom "ForecastShiftedVsPlan: A new forecast deviated from the published plan by more than 10%". Potential cause could be validation gate issue: `forecast-stability-warnings` query exists and likely would be positive if deviation caught. But not causal. Could hypothesize "the published plan changed due to planning-api deployment" - check planning-api-changes-20m. Observed planning desired/available =1, operator transport errors=0, but planning-api-changes could be >0 causing plan change. Should include? This is a plausible hypothesis: the forecast did not shift; the published plan changed due to planning-api update, making comparison >10%. We can propose:
H6: A recent change to planning-api changed the published plan (or plan schema), so the new forecast deviates from a changed plan.
Evidence_needed: planning-api-changes-20m, forecast-stability-warnings, planning-available-replicas, operator-plan-fetch-transport-errors.
Prediction if true: planning-api-changes-20m > 0. Falsifier: planning-api-changes-20m == 0. This is plausible and uses unobserved changes query. Should we replace H5 with this? It's more directly related to "deviation from published plan". The plan is produced by planning-api. Yes, include.

Maybe we should prioritize this over resource contention. We need 3-5. We can choose 5: pipeline change, demand data, weather vendor, feature-service change, planning-api plan change. That's 5. Resource contention less likely given observed feature build p95 fast and no OOM/restarts. Planning-api plan change is more causally relevant. Let's include planning-api plan change as H5 instead of H5 resource. But we already have H1 pipeline change and H4 feature change; planning change similar but distinct.

Let's formulate H5:
- hypothesis: A recent change to service:gridcast:planning-api altered the published plan used as baseline, causing the new forecast to appear shifted even if forecast itself did not materially change.
- evidence_needed: planning-api-changes-20m, forecast-stability-warnings, planning-desired-replicas, planning-available-replicas, operator-plan-fetch-transport-errors.
- prediction if true: planning-api-changes-20m > 0; forecast-stability-warnings > 0 (already likely).
- falsifier: planning-api-changes-20m == 0 (and maybe forecast-stability-warnings ==0? But stability warnings likely positive due symptom, so falsifier for plan change is changes ==0).
Need maybe include forecast-stability-warnings as check? It indicates deviation but not plan change. Could use as evidence needed but not check. Good.

However the observed evidence shows operator-plan-fetch-transport-errors =0 and planning available =1, so plan fetch healthy. Change could still happen. Good.

We need ensure no hypothesis is already conclusively falsified by observed evidence. H5 plan change: observed planning-api-changes not observed. Good.
H4 feature change: observed feature-failed-builds=0, build p95=0.098, SQL=2.031; these don't falsify. Good.
H3 weather: observed ingestion-weather-errors=0, weather-variability-warnings=0; do not falsify vendor 503/contract violations. But maybe if primary weather vendor outage, ingestion-weather-batch-errors would be >0? Not necessarily: fallback secondary could avoid errors. So plausible.
H2 demand: observed ingestion-demand-errors=0, demand-range-failures=0; do not falsify missing zones/stale freshness because no errors but freshness/completeness could fail.
H1 pipeline: observed forecast-pipeline not queried; good.

Let's maybe include "H5 resource contention" as additional? Need only 5. We'll drop resource.

Wait, "Propose 3 to 5 falsifiable hypotheses, not conclusions." We can give 5. Fine.

Let's ensure each hypothesis's "prediction_if_true" and "falsifier" are mechanically checkable:
H1:
- prediction: forecast-pipeline-changes-20m > 0 (query returns changes count/list; >0 is checkable). prefect-failed-flow-runs >0 OR pipeline-failed-runs >0 (count >0).
- falsifier: forecast-pipeline-changes-20m == 0 AND prefect-failed-flow-runs == 0 AND pipeline-failed-runs == 0. Good.

H2:
- prediction: demand-zones-reporting < 4 (from query desc: catalogue has 4). demand-completeness-failures > 0. Good.
- falsifier: demand-zones-reporting == 4 AND demand-completeness-failures == 0. Good.

H3:
- prediction: ingestion-weather-vendor-503 > 0 OR ingestion-contract-violations > 0. Falsifier: both ==0. Good. Include ingestion-weather-errors ==0 in falsifier? Observed 0; if weather errors ==0, that supports falsifier but not enough. We can include "ingestion-weather-errors == 0" but if vendor 503 without errors possible. Actually if vendor 503 leads batch errors? maybe not. Falsifier includes ingestion-weather-errors==0 already observed; we know it's 0. That's okay, but it doesn't falsify H3 because vendor 503 could still have no ingestion-weather-errors if fallback. So include but not central. Hmm maybe omit ingestion-weather-errors from falsifier because it can be 0 even if hypothesis true. We'll include only vendor 503 and contract violations ==0. Good.

H4:
- prediction: feature-service-changes-20m > 0.
- falsifier: feature-service-changes-20m == 0. Good. But maybe also load-feature-mean? No check.

H5:
- prediction: planning-api-changes-20m > 0.
- falsifier: planning-api-changes-20m == 0. Good.

All query IDs in evidence_needed include those used in checks.

Maybe we should also include "evidence_needed" for H2 includes "demand-freshness" but not used. That's okay but maybe unnecessary. If we want to be strict, include only queries used in prediction/falsifier to avoid clutter. But the prompt says "Every check needs a corresponding query in evidence_needed"—not "only those". We can include extra queries needed for contextual investigation. But better to keep evidence_needed exactly the queries used by the checks to avoid non-mechanical references. However some hypotheses logically need to inspect freshness or logs, but no threshold; including them might be considered non-mechanical. We can omit them.

Let's decide evidence_needed only check queries:
H1: forecast-pipeline-changes-20m, pipeline-failed-runs, prefect-failed-flow-runs (maybe prefect-flow-runs not used). Drop slow-pipeline-traces if not used.
H2: demand-zones-reporting, demand-completeness-failures.
H3: ingestion-weather-vendor-503, ingestion-contract-violations, ingestion-weather-errors? We use vendor 503 and contract violations. Include ingestion-weather-errors maybe in falsifier? If we include in falsifier, then include query. We can include ingestion-weather-errors in evidence_needed and in falsifier ==0. Since observed evidence already 0, checkable. But if hypothesis true with fallback, weather errors may be 0; so using it in falsifier could make falsifier too strict: if vendor 503 but no ingestion-weather-errors, hypothesis true but falsifier says it's false if weather errors ==0? Wait falsifier includes multiple conditions AND; if weather errors ==0 but vendor 503 >0, then `ingestion-weather-vendor-503 == 0` is false, so falsifier overall false => not falsified. Good. Including weather errors ==0 in falsifier is okay but not needed. Might confuse. Use only vendor/contract.
H4: feature-service-changes-20m.
H5: planning-api-changes-20m.

But if we reduce evidence_needed, we might omit queries that could actually support/refute with count. That's fine.

Need maybe include "prefect-failed-flow-runs" in H1: query description "forecast-pipeline Prefect flow runs started in incident window that ended FAILED or CRASHED" value maybe list/count. We can compare >0. Good.

Let's include `prefect-flow-runs` maybe to check if a run occurred. If no runs? But incident says new forecast generated, so flow runs likely >0. Not useful. Drop.

Maybe include `forecast-stability-warnings` in H5? It is the metric that would be positive, but not causal. We can use as evidence_needed? Not needed. But maybe if planning-api changed, forecast-stability-warnings >0. However since symptom itself is deviation, this query likely >0; not specific. Drop.

Let's craft final JSON with minimal evidence_needed. But the instruction "Every check needs a corresponding query in evidence_needed" - if our prediction/falsifier only uses one or two query IDs, that's fine.

Need decide if include multiple conditions in prediction/predicate. Could use string:
"prediction_if_true": {
  "forecast-pipeline-changes-20m": ">0",
  "prefect-failed-flow-runs": ">0"
}
But if both not necessarily? We can say "forecast-pipeline-changes-20m >0 OR prefect-failed-flow-runs >0". JSON can encode as list of alternatives? To be mechanically checkable, maybe separate:
"prediction_if_true": [
  "forecast-pipeline-changes-20m > 0",
  "pipeline-failed-runs > 0 OR prefect-failed-flow-runs > 0"
]
"falsifier": [
  "forecast-pipeline-changes-20m == 0",
  "pipeline-failed-runs == 0",
  "prefect-failed-flow-runs == 0"
]
This is clear.

But maybe better to have `checks` array. Let's design a clear JSON schema:

{
  "hypotheses": [
    {
      "id": "H1",
      "hypothesis": "...",
      "evidence_needed": ["query-id-1", "query-id-2"],
      "prediction_if_true": [
        {"query_id":"...", "operator":">", "value":0},
        {"query_id":"...", "operator":">", "value":0}
      ],
      "falsifier": [
        {"query_id":"...", "operator":"==", "value":0},
        {"query_id":"...", "operator":"==", "value":0}
      ]
    }
  ]
}

This is explicit and mechanically checkable. Need ensure no "or" complexity. For H3 prediction could be alternative: maybe use separate prediction entries but if one true? We can phrase hypothesis true if any; falsifier if all equal 0. The machine check can evaluate these.

Let's see if operator ">", "==", "<" are okay. Values are numeric counts. For query `demand-zones-reporting` returns zones with reading; operator "<" value 4. Good.
For query `feature-service-changes-20m` returns commits/rollouts; count >0. Good.
For `planning-api-changes-20m` count >0.

Maybe include "evidence_needed" as array of query IDs. Good.

Let's produce final answer maybe with brief intro and JSON.

Need maybe mention "These are hypotheses to test, not conclusions" in intro.

Let's ensure no invented thresholds except "catalogue has 4" from query description. For demand-zones-reporting, query description says "Load zones with at least one demand reading in the 20 minutes before incident end (the catalogue has 4)". So <4 is objective.

Let's think if H2 should include "demand-completeness-failures >0" but query description: "Pipeline validation-gate completeness.demand checks that warned or failed in last 20 minutes" returns likely count. Good.

H3: query `ingestion-weather-vendor-503` is log line count. >0 objective. `ingestion-contract-violations` log line count. >0 objective.

H1: `forecast-pipeline-changes-20m`: "GitOps commits and rollouts touching forecast-pipeline in the 20 minutes before incident end" returns count? likely. >0.
`pipeline-failed-runs`: "Forecast pipeline runs that failed in last 15 minutes" count.
`prefect-failed-flow-runs`: "forecast-pipeline Prefect flow runs started in the incident window that ended FAILED or CRASHED" count/list. Could be list; >0 means count/list non-empty. Good.

H4: `feature-service-changes-20m`: count >0.

H5: `planning-api-changes-20m`: count >0.

Could also add one about forecast-service model changes but observed 0 already. Avoid.

Now, is H5 "planning-api-changes-20m >0" enough? The hypothesis says plan changed; if query returns 0, falsified. Good.

Need maybe include "evidence_needed" for each with all query IDs used in prediction/falsifier. For H1: forecast-pipeline-changes-20m, pipeline-failed-runs, prefect-failed-flow-runs. For H2: demand-zones-reporting, demand-completeness-failures. For H3: ingestion-weather-vendor-503, ingestion-contract-violations. For H4: feature-service-changes-20m. For H5: planning-api-changes-20m.

But maybe each hypothesis should have enough context; maybe include additional query IDs for investigation. The prompt specifically says "Every check needs a corresponding query in evidence_needed" not "only checks"; it may be okay to include extra registered query IDs that you'd need to retrieve to evaluate. But extra unnecessary. Keep minimal to avoid confusion.

Let's maybe include one extra query per hypothesis to help falsify: H1 include `slow-pipeline-traces`? Could be used? If no deploy but slow traces, could still be pipeline issue. Hmm.
But the hypothesis "pipeline change" is specifically deploy; only changes query. Extra failed runs could show impact. Good.

Let's think of possible issue: H1 predicts pipeline changes >0. But `forecast-pipeline-changes-20m` query might return an array/list not numeric; checking >0 still possible if length. Good.

Maybe final answer should not include observed evidence values. But maybe we can mention "Observed values already available are not part of these predictions." No.

Let's produce final.

Need ensure no "actions or manufacture evidence". We won't.

Let's write final with JSON but maybe plain text too.

Potential final:

```
Proposed hypotheses (each falsifiable via the listed registered query IDs):

```json
{
  "hypotheses": [
    ...
  ]
}
```

Need maybe not include markdown code block? It's okay.

Let's make JSON valid. Ensure no trailing commas.

Let's compose carefully.

Hypothesis texts use entity IDs:
H1: "A recent GitOps commit or rollout touching service:gridcast:forecast-pipeline altered forecast run behavior/validation thresholds, causing the forecast to deviate from the published plan."
H2: "Demand input was incomplete/stale in the incident window (fewer than the 4 known load zones reported or completeness.demand checks failed), causing a shifted demand baseline for the forecast."
H3: "The primary weather vendor failed or returned malformed payloads (HTTP 503 / contract violation), causing service:gridcast:ingestion to fall back or ingest stale weather observations, shifting the forecast."
H4: "A recent GitOps commit or rollout touching service:gridcast:feature-service altered feature computation (e.g. load_lag_24h), so service:gridcast:forecast-pipeline received changed model inputs."
H5: "A recent GitOps commit or rollout touching service:gridcast:planning-api changed the published plan baseline, so the new forecast appears shifted relative to it."

Check if H2 mentions "4 known load zones" from query description, okay.

JSON:
```
{
  "hypotheses": [
    {
      "id": "H1",
      "hypothesis": "...",
      "evidence_needed": ["forecast-pipeline-changes-20m", "pipeline-failed-runs", "prefect-failed-flow-runs"],
      "prediction_if_true": [
        {"query_id": "forecast-pipeline-changes-20m", "operator": ">", "value": 0},
        {"query_id": "pipeline-failed-runs", "operator": ">", "value": 0},
        {"query_id": "prefect-failed-flow-runs", "operator": ">", "value": 0}
      ],
      "falsifier": [
        {"query_id": "forecast-pipeline-changes-20m", "operator": "==", "value": 0},
        {"query_id": "pipeline-failed-runs", "operator": "==", "value": 0},
        {"query_id": "prefect-failed-flow-runs", "operator": "==", "value": 0}
      ]
    },
    ...
  ]
}
```

But prediction_if_true with multiple conditions: Does hypothesis require all? Not necessarily; if any true supports. We can interpret as "at least one". In falsifier, all must be true. That's implied. Could include `"logic": "any"`? To be clear, add `"prediction_logic": "any"` and `"falsifier_logic": "all"`. This is mechanically checkable. Let's include that for each? It adds clarity.

For H1 prediction: if forecast-pipeline-changes >0 alone supports; if failed runs >0 also supports; but not necessarily all. We'll write `"prediction_if_true": "at least one of the following holds"`? JSON maybe cumbersome. We can add:
```
"prediction_if_true": {
  "logic": "any",
  "checks": [...]
}
"falsifier": {
  "logic": "all",
  "checks": [...]
}
```
This is good.

For H2 prediction: demand-zones-reporting <4 and/or completeness>0. Use any.
Falsifier: all equal? demand-zones-reporting ==4 and completeness==0. Yes.

H3 prediction: ingestion-weather-vendor-503 >0 or ingestion-contract-violations >0. Use any.
Falsifier: all ==0.

H4 prediction: feature-service-changes-20m >0. Single.
Falsifier: ==0.

H5 prediction: planning-api-changes-20m >0. Single.
Falsifier: ==0.

Need ensure `"operator"` maybe `"=="`, `">"`, `"<"`. Good.

Let's produce final answer with this schema.

Check line length not an issue.

Need maybe mention "No conclusions drawn; these are testable candidate explanations." Good.

Let's final.