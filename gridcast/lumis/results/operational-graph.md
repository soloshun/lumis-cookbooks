# GridCast operational graph (as Lumis sees it)

Prepared 2026-10-03 12:48 UTC from: declared (10 entities), kubernetes (43 entities), prometheus.service_graph (9 entities).
Solid edges were observed in traces (service graph); dashed edges are declared only.

```mermaid 
flowchart LR
    db_migrate["db-migrate<br/><small>1 k8s objects</small>"]
    feature_service["feature-service<br/><small>6 k8s objects</small>"]
    forecast_pipeline["forecast-pipeline<br/><small>4 k8s objects</small>"]
    forecast_service["forecast-service<br/><small>5 k8s objects</small>"]
    grid_operator(["grid-operator<br/><small>4 k8s objects</small>"])
    grid_telemetry{{"grid-telemetry"}}
    ingest_backfill["ingest-backfill<br/><small>1 k8s objects</small>"]
    ingestion["ingestion<br/><small>5 k8s objects</small>"]
    model_train["model-train<br/><small>1 k8s objects</small>"]
    model_train_hifi["model-train-hifi<br/><small>1 k8s objects</small>"]
    planning_api["planning-api<br/><small>5 k8s objects</small>"]
    postgres[("postgres")]
    weather_vendor_wx_primary{{"weather-vendor-wx-primary"}}
    weather_vendor_wx_secondary{{"weather-vendor-wx-secondary"}}
    feature_service -->|serves| forecast_pipeline
    forecast_service -->|serves| forecast_pipeline
    grid_telemetry -->|serves| ingestion
    planning_api -->|serves| forecast_pipeline
    planning_api -->|serves| grid_operator
    postgres -->|serves| feature_service
    postgres -->|serves| forecast_pipeline
    postgres -->|serves| forecast_service
    postgres -->|serves| ingestion
    postgres -->|serves| planning_api
    weather_vendor_wx_primary -->|serves| ingestion
    weather_vendor_wx_secondary -.->|serves| ingestion
```
