We need answer user. Need reason. We need produce 3-5 competing root-cause hypotheses, most likely first. For each, name component where fault originates (use given service IDs when possible), mechanism one sentence, short statement. No actions.

We need infer from incident: affected_entities: service:gridcast:forecast-pipeline. Symptoms: ForecastPipelineSlow: Forecast pipeline p95 duration above 5s. started 17:44:12 ended 18:01:19. Service graph entities. Need identify possible root causes. Need think as SRE diagnosing. Need propose hypotheses, likely first. Need use given service IDs.

We need parse graph. Entities include services:
- feature-service (high, forecasting-team)
- forecast-pipeline (high, forecasting-team)
- forecast-service (high, ml-platform)
- grid-operator (critical, consumer)
- grid-telemetry (external-vendor, operable false)
- ingestion (high, data-platform)
- planning-api (critical, planning-team)
- postgres (database, managed true, critical)
- weather-vendor-wx-primary (external-vendor, operable false)
- weather-vendor-wx-secondary (external-vendor, operable false, fallback_for weather-vendor-wx-primary)
Relationships:
- feature-service serves forecast-pipeline? Relationship source feature-service -> target forecast-pipeline, kind serves. Hmm direction maybe "source serves target"? Actually in graph relationships: source service:gridcast:feature-service target service:gridcast:forecast-pipeline kind serves, provenance declared/prometheus. "serves" usually means source calls target? Need interpret. They list source -> target kind "serves". If source service calls target? Let's infer: postgres -> feature-service, forecast-pipeline, forecast-service, ingestion, planning-api: kind serves. In many service graphs, "serves" means source provides service to target? Hmm if source postgres serves feature-service, maybe feature-service depends on postgres. Direction source (postgres) -> target (feature-service) kind serves. That suggests source is provider/dependency to target. But earlier k8s service routes_to pod. "hosts" from deployment to service. Then "service:feature-service -> service:forecast-pipeline kind serves" might mean feature-service provides service to forecast-pipeline? Wait feature-service likely calls forecast-pipeline? Need semantic: In service map, relationship "source serves target" usually means source is upstream service that calls target? Let's examine. They use source to target "serves" with provenance declared/prometheus. For "postgres -> forecast-pipeline" if postgres "serves" forecast-pipeline, that means postgres is dependency of forecast-pipeline. Direction is dependency (postgres) -> dependent (forecast-pipeline). For "feature-service -> forecast-pipeline" "feature-service" serves forecast-pipeline? That would mean feature-service is dependency of forecast-pipeline? Hmm feature-service probably calls forecast-pipeline? Actually forecast-pipeline maybe consumes from feature-service? Let's think.

Services: gridcast forecast pipeline slow. Inputs maybe from feature-service and forecast-service, planning-api etc.

Let's map relationships:
- feature-service -> forecast-pipeline (serves)
- forecast-service -> forecast-pipeline (serves)
- grid-telemetry -> ingestion (serves)
- planning-api -> forecast-pipeline (serves)
- planning-api -> grid-operator (serves)
- postgres -> feature-service, forecast-pipeline, forecast-service, ingestion, planning-api (serves)
- weather-vendor-wx-primary -> ingestion (serves)
- weather-vendor-wx-secondary -> ingestion (serves, declared only)
This likely indicates upstream providers to downstream consumers? For postgres, it serves data to all services, so arrow from postgres to each consumer. For weather vendors to ingestion, vendors serve data to ingestion. Then planning-api -> grid-operator means planning-api serves grid-operator? But earlier planning-api -> forecast-pipeline seems planning-api serves forecast-pipeline (calls? maybe planning-api provides forecasts? Hmm planning-api maybe consumes forecast-pipeline? Actually if source serves target, then planning-api serves forecast-pipeline? That seems unlikely; planning-api might provide planning data to forecast-pipeline? Or maybe relationship direction is reversed: source calls target (serves as client?).

Let's consider known service graph conventions. In Dynatrace, "calls" direction from caller to callee. Here kind "serves": source "serves" target maybe source is provider, target is consumer. If postgres source serves target forecast-pipeline, then forecast-pipeline consumes postgres. If feature-service source serves forecast-pipeline, forecast-pipeline consumes feature-service. That makes sense: forecast-pipeline calls feature-service and forecast-service? Actually if forecast-pipeline consumes feature-service, then feature-service is a dependency and slowness in feature-service would cause forecast-pipeline slow. Similarly forecast-service is dependency. Planning-api is dependency? planning-api source -> forecast-pipeline target "serves": planning-api serves forecast-pipeline? That means forecast-pipeline depends on planning-api? Maybe forecast-pipeline calls planning-api. Could be.
But "grid-operator" is critical consumer; maybe planning-api serves grid-operator? If planning-api source -> grid-operator target, then grid-operator depends on planning-api. That's plausible: planning-api provides planning data to grid-operator. Good.
Weather-vendor -> ingestion: ingestion consumes vendors. Yes.
Postgres -> all: all consume postgres. Good.

Thus relationships indicate data/function provider -> consumer. For forecast-pipeline, upstream dependencies: feature-service, forecast-service, planning-api, postgres. It maybe is a pipeline consuming data from these services. Forecast-pipeline is also served by maybe none? It doesn't serve other services? It has no outgoing "serves" to other services? Actually forecast-pipeline maybe only sink? Hmm affected service is forecast-pipeline, but downstream? The graph shows no relation from forecast-pipeline to others. It might be a batch pipeline not serving others, but "service:gridcast:forecast-pipeline" has high criticality.

Need root causes hypotheses:
1. Upstream dependency slowness in forecast-service or feature-service or planning-api causing forecast-pipeline p95 >5s. Which most likely? We need choose first. Need maybe infer from affected only forecast-pipeline, not necessarily upstream. Could be inside forecast-pipeline itself: resource saturation, pod CPU/memory, GC, thread pool, DB queries. Need propose 3-5 competing.

Need include component where fault originates using given IDs. Mechanism one sentence. Short statement.

Most likely first? We need order likely. We can propose:
1. forecast-pipeline itself: code/performance regression or resource limit/CPU throttling causing its own processing duration p95 above 5s. Component: service:gridcast:forecast-pipeline (deployment/pod)
2. postgres: slow queries/locking/saturation, because forecast-pipeline depends on postgres and p95 of pipeline queries exceed threshold. Component: service:gridcast:postgres
3. feature-service: upstream latency causing forecast-pipeline waiting on feature-service. component: service:gridcast:feature-service
4. forecast-service: ML inference latency causing pipeline slow. Component: service:gridcast:forecast-service
5. planning-api: planning data dependency slow. Component: service:gridcast:planning-api
Maybe external weather vendor? Forecast-pipeline likely not directly calls weather vendor; ingestion consumes weather. Slowness in weather vendor maybe affects ingestion and downstream maybe forecast-pipeline eventually, but forecast-pipeline may not directly call weather vendor. Could be delayed data? But incident is p95 duration above 5s, likely request latency not data delay. Need not include? We can include maybe external vendor as less likely because no direct relationship and symptom is pipeline duration.

Need be careful: User asks "Propose 3 to 5 competing root-cause hypotheses, most likely first. For each, name the component where the fault originates (use the given service IDs when you can), the mechanism (what went wrong, in one sentence), and a short statement. Do not propose actions."

Need maybe format as table or list. Each hypothesis includes component ID, mechanism, statement. Need no actions. Could include confidence/order. Need "competing root-cause hypotheses". Need most likely first. We can use "Hypothesis 1 (most likely)". We need not mention actions.

Need decide if first is forecast-pipeline itself. The affected entity is forecast-pipeline, symptom is pipeline p95 >5s. Internal regressions or resource saturation most likely? Maybe as SRE, if only forecast-pipeline SLO breaches and no other service symptoms, likely internal degradation in forecast-pipeline itself (CPU throttling, memory pressure, GC pauses, increased input data volume, new deployment). But the graph includes many possible upstreams. Need order.

Let's think about possible "competing" root causes. We can propose:
1. Forecast-pipeline resource saturation/CPU throttling due to increased workload or container limit; p95 increases because processing stages queue.
2. postgres slowdown due to connection pool exhaustion/lock contention; forecast-pipeline queries get slow.
3. feature-service latency increase; forecast-pipeline blocks on feature-service calls.
4. forecast-service inference latency; forecast-pipeline waits on model serving.
5. planning-api dependency slow; if pipeline calls planning-api for constraints.

Which is most likely? We might rank internal resource saturation first, then postgres, then upstream feature-service/forecast-service, then planning-api. Or maybe postgres first because forecast-pipeline reads from postgres and postgres is shared critical. Hmm.

Need maybe use graph to infer high fan-in: forecast-pipeline has incoming serves from feature-service, forecast-service, planning-api. Wait if direction provider->consumer: feature-service, forecast-service, planning-api all serve forecast-pipeline; postgres serves forecast-pipeline. So forecast-pipeline depends on all four. The symptom is forecast pipeline p95 duration; possible causes could be any of these dependencies. Most likely maybe the pipeline's own code or data volume, then postgres.

Need ensure "component where fault originates" maybe if forecast-pipeline itself, component ID is service:gridcast:forecast-pipeline or k8s deployment/pod. Use "service:gridcast:forecast-pipeline (k8s:gridcast:deployment:forecast-pipeline)". Good.

Need maybe include "service:gridcast:postgres" as component. "service:gridcast:feature-service" etc.

Need maybe mention "grid-operator" or "ingestion"? Not directly tied to forecast-pipeline except maybe pipeline not serving. But maybe if grid-operator consumes from planning-api and planning-api is slow due to grid-operator's load? Hmm not likely. External weather vendors not direct. Could propose maybe "weather-vendor-wx-primary degradation causing ingestion backpressure, which indirectly delays forecast-pipeline input data" but forecast-pipeline doesn't call ingestion directly? No relation. Not good.

Need maybe identify "fault originates" could be in Kubernetes components not service IDs? We have service IDs. Need use given service IDs "when you can". For internal resource saturation, component service:gridcast:forecast-pipeline or k8s deployment. Good.

Let's formulate:

Hypothesis 1 (most likely)
Component: service:gridcast:forecast-pipeline (k8s:gridcast:deployment:forecast-pipeline)
Mechanism: The pipeline itself is CPU-throttled or saturated due to increased job volume/processing complexity or a recent code change, causing internal stages to queue and the p95 end-to-end duration to exceed 5s.
Statement: Forecast pipeline is slow because its own processing capacity is the bottleneck, not a downstream/upstream dependency.

Hypothesis 2
Component: service:gridcast:postgres
Mechanism: Shared PostgreSQL is experiencing slow queries, lock contention, or connection-pool exhaustion, so forecast-pipeline DB reads/writes block and inflate pipeline duration.
Statement: Forecast pipeline is slow because its database dependency is degraded or overloaded.

Hypothesis 3
Component: service:gridcast:feature-service
Mechanism: feature-service is responding slowly to forecast-pipeline calls due to its own CPU/memory/GC pressure or downstream dependency, causing forecast-pipeline to wait on feature data longer than normal.
Statement: Forecast pipeline is slow because feature-service, an upstream provider, has elevated latency.

Hypothesis 4
Component: service:gridcast:forecast-service
Mechanism: forecast-service model inference or serving path is degraded, so calls from forecast-pipeline to forecast-service take longer and push pipeline p95 above threshold.
Statement: Forecast pipeline is slow because its ML forecast-service dependency is degraded.

Hypothesis 5
Component: service:gridcast:planning-api
Mechanism: planning-api is slow or availability-reduced due to critical request load from grid-operator, causing forecast-pipeline calls to planning-api to stall or retry.
Statement: Forecast pipeline is slow because planning-api, a critical upstream dependency, is overloaded or degraded.

Need maybe include no actions. Fine.

But let's examine if "serves" direction might be opposite. If feature-service -> forecast-pipeline means feature-service calls forecast-pipeline, then feature-service is downstream/consumer not upstream. Hmm need ensure root cause statement matches. The symptom affected is forecast-pipeline; if feature-service "serves" forecast-pipeline? Wait we assumed provider->consumer. But if it's actually "serves" source service calls target? Let's re-evaluate relationship semantics in this dataset. They have k8s service routes_to pod. "source" service:feature-service, target:service:forecast-pipeline, kind "serves". In service graph generated by Prometheus? Maybe "serves" means the source service serves traffic to target? Hmm.

Let's infer from known "service graph" relationship types. They might use "serves" edge from server to client? In a service map, usually arrows denote direction of calls: client -> server. But they label "serves" maybe source "serves" target? If target is the client, source is server. That's what we used.

But the arrows from k8s deployment to service are "hosts": deployment hosts service? Source deployment -> target k8s service kind hosts. That means deployment owns host? Actually service is an abstraction, deployment hosts the service? That direction maybe source deployment exposes/hosts the service. Fine.

Then service relationships: source service:postgres -> target service:ingestion kind "serves" means postgres serves ingestion. That is server -> client. Good.

forecast-service -> forecast-pipeline: forecast-service serves forecast-pipeline: forecast-pipeline is client. That seems plausible: forecast-pipeline calls forecast-service. But name forecast-service might be a ML service for making forecasts, and forecast-pipeline orchestrates, likely calls forecast-service. Yes.

planning-api -> forecast-pipeline: planning-api serves forecast-pipeline: forecast-pipeline calls planning-api. Hmm maybe forecast-pipeline calls planning-api for grid topology/constraints. Yes.

feature-service -> forecast-pipeline: feature-service serves forecast-pipeline: forecast-pipeline calls feature-service for features. Good.

postgres -> forecast-pipeline: forecast-pipeline calls DB. Good.

Thus our hypotheses are consistent.

Need maybe consider if "forecast-pipeline" is itself a service that maybe is "served" by feature-service and forecast-service and planning-api, but in graph no arrow from forecast-pipeline to others. Fine.

Potential issue: "ForecastPipelineSlow: Forecast pipeline p95 duration above 5s" might be a synthetic check or metric from forecast-pipeline itself. If it's p95 duration from pipeline's own processing, maybe related to internal stages. We can say most likely internal capacity.

Need maybe include "started_at/ended_at" maybe could indicate incident duration 17 minutes. Could be transient. We can mention if relevant? No.

Need maybe in mechanism one sentence, not too long. Need no actions. Use "most likely first" in ordering.

Need maybe ensure "short statement" not too detailed. Could be one sentence each.

Need maybe include "use given service IDs when you can" so component should include full ID e.g., "service:gridcast:postgres" not "postgres".

Let's think of other possible root causes in graph:
- PostgreSQL is managed, critical, shared by many services. Could be plausible first if typical for pipeline slowdown.
- There are many ReplicaSets for feature-service, forecast-service, etc. Could indicate many rollouts? Hmm maybe not relevant. But the service graph includes multiple replicasets. The current pod: forecast-pipeline-75dcf6fbfd-vgvdz. There are multiple replicasets for forecast-pipeline: 544c8596b5, 5c7f79fc74, 6874878b9c, 75dcf6fbfd, 85d68dcfb7. Current pod belongs to 75dcf6fbfd. Could indicate frequent deployments. Not necessarily.

Could be recent deploy of forecast-pipeline itself. We can mention "recent deployment/code change" but no deployment event given. As hypothesis, "forecast-pipeline own performance regression or resource limit (CPU throttling)". Good.

Could be "weather-vendor-wx-primary degradation" causing input data lag to ingestion, then pipeline waits on incomplete data? But forecast-pipeline doesn't call ingestion. However maybe pipeline reads from postgres/feature-service that depend on ingestion. But symptom p95 duration could increase if data not ready? Hmm maybe pipeline waits for upstream data. But less direct. We could include as lower hypothesis if need 5. But maybe better to use direct dependencies.

Need maybe include "ingestion" if pipeline indirectly depends on data freshness: If weather vendor primary degraded, ingestion falls back to secondary, causing delayed weather data; forecast-pipeline spends longer recomputing or waiting? Hmm not likely p95 duration, but could be. The graph includes weather-vendor-wx-primary -> ingestion. The incident time 17:44 to 18:01. Maybe weather vendor primary had issues, ingestion fallback. But forecast-pipeline p95 duration maybe affected? Maybe not.

Maybe "grid-telemetry" external vendor -> ingestion, not forecast-pipeline. No.

Let's rank:
1. forecast-pipeline itself (CPU/resource saturation/deployment)
2. postgres (shared DB slowdown)
3. feature-service (upstream latency)
4. forecast-service (ML inference latency)
5. planning-api (upstream load due to grid-operator)
Maybe mention "external weather vendor degradation indirectly delaying pipeline inputs" as sixth? User asks 3 to 5. We'll give 5.

Need maybe ensure mechanism one sentence:
- Hypothesis 1: "The forecast-pipeline deployment/pod is CPU-throttled or has saturated worker threads due to increased input volume or a performance regression, so internal task queues grow and end-to-end p95 crosses 5s."
That's one sentence.

- Hypothesis 2: "Shared postgres is experiencing slow query plans, lock contention, or connection pool exhaustion for the forecast-pipeline's read/write path, causing database calls from the pipeline to block and increasing p95 duration."
One sentence.

- Hypothesis 3: "feature-service is returning responses to forecast-pipeline more slowly than normal due to its own resource saturation or downstream postgres latency, making the pipeline wait on feature construction."
One sentence.

- Hypothesis 4: "forecast-service model inference is degraded or overloaded, so forecast-pipeline calls into it are slow and inflate pipeline p95."
One sentence.

- Hypothesis 5: "planning-api is overloaded by critical grid-operator traffic or is itself slow, causing forecast-pipeline calls to planning-api to be delayed/retried."
One sentence.

Need maybe "short statement" could be:
- "The pipeline is bottlenecked on its own compute."
- "The pipeline is bottlenecked on database access."
- "The pipeline is bottlenecked on feature-service."
- "The pipeline is bottlenecked on ML inference."
- "The pipeline is bottlenecked on planning-api."

Need maybe "Do not propose actions" so avoid "check logs" or "scale". Fine.

Need maybe format as nested list or table. Let's maybe provide a table with columns: Rank, Component (originating fault), Mechanism, Statement. That's clear. For component use service IDs plus maybe deployment. Need maybe "service:gridcast:forecast-pipeline (k8s:gridcast:deployment:forecast-pipeline)" etc.

Need maybe "most likely first" as rank. Good.

Need ensure no extra actions. We can say "Hypotheses ordered by likelihood." Fine.

Let's consider if there is any nuance: "forecast-pipeline" affected entity exactly same as component ID. If internal issue, component is same as affected entity. That's acceptable. Some root-cause hypotheses could say "fault originates in forecast-pipeline itself". Good.

Potential issue: "name the component where the fault originates" maybe they expect "fault originates at service:gridcast:forecast-pipeline" but if it's same as affected, maybe not a "root cause" from another component. But root cause can be internal.

Need maybe mention "k8s:gridcast:pod:forecast-pipeline-75dcf6fbfd-vgvdz" as more specific? The fault may be within current pod. Could use "service:gridcast:forecast-pipeline (pod k8s:gridcast:pod:forecast-pipeline-75dcf6fbfd-vgvdz)". But "use given service IDs when you can", service ID is fine. For internal resource, the component could be "k8s:gridcast:deployment:forecast-pipeline" or "service:gridcast:forecast-pipeline". Use both.

Need maybe mention "postgres" service ID: service:gridcast:postgres. Good.
- "feature-service": service:gridcast:feature-service
- "forecast-service": service:gridcast:forecast-service
- "planning-api": service:gridcast:planning-api

Potential alternative: "grid-operator" causing planning-api load and indirectly forecast-pipeline. But fault originates in planning-api? Could include "planning-api is slow because grid-operator demand saturates it" but component is planning-api. If we want identify fault origin in grid-operator, we could propose: "grid-operator sends burst traffic to planning-api causing planning-api contention and forecast-pipeline calls queue behind them." Component: service:gridcast:grid-operator. But is that likely? Maybe not. But if we want use graph relationships, planning-api serves both forecast-pipeline and grid-operator; grid-operator is critical consumer. If grid-operator load increased, planning-api overloaded; forecast-pipeline depends on planning-api, causing slow. This is a possible root cause originating at grid-operator. Might be competing. But user asks 3-5. Could include as hypothesis 5: component grid-operator. But mechanism one sentence: "grid-operator has issued a burst/retry storm of critical requests to planning-api, consuming planning-api capacity so forecast-pipeline calls to planning-api are delayed." That uses graph. Which is better? Planning-api itself or grid-operator? The graph explicitly has planning-api -> grid-operator, meaning planning-api serves grid-operator? Actually if planning-api serves grid-operator, yes. If grid-operator overloads planning-api, it could impact forecast-pipeline because planning-api also serves forecast-pipeline. That's plausible. But from incident only forecast-pipeline slow; planning-api might not show symptom in affected_entities? It only lists forecast-pipeline. There is no symptom for planning-api or grid-operator. But if planning-api overloaded, might also show symptoms but maybe not alerted. Could be.

Maybe include both? We need 3-5; 5 can include planning-api due to own degradation. But component fault origin should be precise. If fault is in grid-operator's demand, component is grid-operator. If fault is planning-api's capacity, component is planning-api. We can make one hypothesis for planning-api. Good.

Need maybe maybe "feature-service" and "forecast-service" both serve forecast-pipeline. Could there be a "noisy neighbor" in postgres causing slowness from one tenant? We have postgres serving all. Good.

Let's maybe include 5 hypotheses:
1. Forecast-pipeline internal compute/resource saturation or code regression.
2. Postgres slowdown/shared DB contention.
3. Forecast-service ML inference/serving degradation.
4. Feature-service upstream latency degradation.
5. Planning-api overload/degradation (possibly due to grid-operator demand).

Order: Why forecast-service before feature-service? Which is more likely? Maybe forecast-service ML is heavier and more likely to slow. But feature-service also high. Could order by dependencies in graph? Maybe forecast-pipeline likely calls forecast-service for model inference, feature-service for features, planning-api for constraints. Which is most likely to be slow? Without data, maybe internal > postgres > forecast-service > feature-service > planning-api. But maybe "forecast-service" is ML platform and likely latency source. Hmm.

Need perhaps not overthink; any order is okay if well-reasoned. But user says "most likely first", we need define. Maybe first should be "forecast-pipeline's own resource saturation" because single affected service. Then "postgres" because DB is common cause. Then "forecast-service" because ML inference could be slow. Then "feature-service" then "planning-api". Or "feature-service before forecast-service" due feature service calls postgres? Maybe feature-service high criticality. Let's keep first two clear.

Need maybe include "short statement" as separate concise sentence. We can format:

| # | Component (fault origin) | Mechanism | Statement |
|---|---|---|---|
| 1 | service:gridcast:forecast-pipeline (k8s:gridcast:deployment:forecast-pipeline) | The pipeline's own workers are saturated or CPU-throttled because of increased job volume or a performance regression, causing internal stage queueing and p95 > 5s. | The pipeline is bottlenecked on its own compute. |
...

Need ensure no actions. Good.

Let's consider whether "postgres" should be first instead. We need maybe infer from service graph that forecast-pipeline has many dependencies; but no symptoms for other services. If postgres was slow, all services would be slow, likely multiple affected entities not just forecast-pipeline. But incident's affected_entities only forecast-pipeline, maybe because only SLO alert for forecast-pipeline. Still, if DB slow, other services may also degrade. But not necessarily alerted. Internal resource saturation in forecast-pipeline more likely if only forecast-pipeline alert. So first can be internal.

Could there be "recent deployment of forecast-pipeline" because service graph shows multiple replicasets; current pod from 75dcf6fbfd. We can mention "recent deploy/regression" but mechanism should be one sentence. We'll include.

Need maybe maybe "started_at 17:44 ended 18:01" though incident ended, maybe resolved. Could mention "transient 17-min event" no.

Potential need to "use the given service IDs when you can" maybe for forecast-pipeline include both service and deployment. Let's do.

Need maybe no "actions" like "check CPU" etc. Fine.

Let's draft final answer.

But before final, let's think if there is any hidden interpretation: "ForecastPipelineSlow: Forecast pipeline p95 duration above 5s" might be a metric from service:gridcast:forecast-pipeline. It's affected entity. The graph "relationships" have "source: service:gridcast:feature-service -> target service:gridcast:forecast-pipeline kind serves" maybe if "feature-service serves forecast-pipeline" then forecast-pipeline is served by feature-service. In root cause analysis, could be "feature-service is serving forecast-pipeline, and feature-service has high latency, which makes forecast-pipeline slow." Yes our mechanism aligns.

Need maybe also mention "postgres" as serving forecast-pipeline, but if postgres is managed and critical, maybe database performance. Good.

Potential issue: "service:gridcast:forecast-pipeline" has no "kind: kubernetes.service" only deployment/pod/replicaset and service entity. Maybe "k8s:gridcast:service:forecast-pipeline" missing. There's service entity "service:gridcast:forecast-pipeline" only from app label/Prometheus, no k8s service. That's okay.

Let's maybe include "k8s:gridcast:deployment:forecast-pipeline" in component.

Need maybe not mention "postgres" as "shared" maybe yes.

Let's write final with table and no extra actions.

Need maybe ensure each mechanism exactly one sentence. In table mechanism maybe one sentence. Yes.

Need maybe statement "Short statement" maybe no longer than a sentence.

Let's produce final answer.