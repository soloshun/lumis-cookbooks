# Data model

One PostgreSQL 17 database (`gridcast`) with six schemas that follow the data's journey. Every
table and column has a `COMMENT`, so pgAdmin's ER diagram and Grafana's SQL editor are
self-documenting. Migrations live in `src/gridcast/db/migrations` (Alembic) and run as a
Kubernetes Job (`gridcastctl job migrate`).

```mermaid
erDiagram
    REF_WEATHER_STATIONS ||--o{ REF_ZONES : "drives demand of"
    REF_WEATHER_STATIONS ||--o{ RAW_WEATHER_OBSERVATIONS : observed_at
    REF_WEATHER_STATIONS ||--o{ RAW_WEATHER_FORECASTS : forecast_for
    REF_WEATHER_PROVIDERS ||--o{ RAW_WEATHER_OBSERVATIONS : supplied_by
    REF_WEATHER_PROVIDERS ||--o{ RAW_WEATHER_FORECASTS : issued_by
    REF_ZONES ||--o{ RAW_DEMAND_READINGS : measured_in
    FEATURES_FEATURE_RUNS ||--|{ FEATURES_FORECAST_FEATURES : contains
    REF_ZONES ||--o{ FEATURES_FORECAST_FEATURES : for_zone
    FEATURES_FEATURE_RUNS ||--o{ ML_FORECAST_RUNS : input_to
    ML_MODELS ||--o{ ML_MODEL_ALIASES : "pointed to by"
    ML_MODELS ||--o{ ML_MODEL_EVENTS : history
    ML_MODELS ||--o{ ML_FORECAST_RUNS : served
    ML_FORECAST_RUNS ||--|{ ML_FORECASTS : produces
    ML_FORECAST_RUNS ||--o{ QUALITY_FORECAST_VALIDATIONS : gated_by
    ML_FORECAST_RUNS ||--o{ PLANNING_DISPATCH_PLANS : basis_of
    PLANNING_DISPATCH_PLANS ||--|{ PLANNING_PLAN_INTERVALS : schedules

    REF_WEATHER_STATIONS {
        text station_id PK
        float latitude
        float longitude
    }
    REF_ZONES {
        text zone_id PK
        text station_id FK
        float base_load_mw
        float cooling_mw_per_degc
    }
    REF_WEATHER_PROVIDERS {
        text provider_id PK
        smallint priority
    }
    RAW_WEATHER_OBSERVATIONS {
        text station_id PK
        timestamptz observed_at PK
        text provider_id FK
        float temperature_c
        timestamptz ingested_at
    }
    RAW_WEATHER_FORECASTS {
        text station_id PK
        timestamptz issued_at PK
        timestamptz valid_at PK
        text provider_id FK
    }
    RAW_DEMAND_READINGS {
        text zone_id PK
        timestamptz ts PK
        float load_mw
        text quality
    }
    RAW_INGESTION_BATCHES {
        bigint batch_id PK
        text dataset
        text status
        text error
    }
    FEATURES_FEATURE_RUNS {
        uuid feature_run_id PK
        timestamptz as_of
        text builder_version
        text lag_resolution
        int db_queries
        float duration_ms
    }
    FEATURES_FORECAST_FEATURES {
        uuid feature_run_id PK
        text zone_id PK
        timestamptz target_ts PK
        float load_lag_24h
        float load_lag_168h
    }
    ML_MODELS {
        text model_name PK
        int version PK
        text profile
        jsonb metrics
        text artifact_uri
    }
    ML_MODEL_ALIASES {
        text model_name PK
        text alias PK
        int version FK
    }
    ML_MODEL_EVENTS {
        bigint event_id PK
        text event
        text actor
        text reason
    }
    ML_FORECAST_RUNS {
        uuid forecast_run_id PK
        uuid feature_run_id FK
        int model_version FK
        float inference_ms
    }
    ML_FORECASTS {
        uuid forecast_run_id PK
        text zone_id PK
        timestamptz target_ts PK
        float load_mw_p10
        float load_mw_p50
        float load_mw_p90
    }
    QUALITY_CHECK_RESULTS {
        bigint check_id PK
        text check_name
        text status
        float observed
    }
    QUALITY_FORECAST_VALIDATIONS {
        uuid validation_id PK
        uuid forecast_run_id FK
        text decision
        jsonb failed_checks
    }
    PLANNING_DISPATCH_PLANS {
        uuid plan_id PK
        uuid forecast_run_id FK
        text status
        timestamptz published_at
    }
    PLANNING_PLAN_INTERVALS {
        uuid plan_id PK
        text zone_id PK
        timestamptz interval_start PK
        float scheduled_generation_mw
    }
```

## Schemas

| Schema | Holds | Written by |
|---|---|---|
| `ref` | zones, stations, vendors (from `src/gridcast/data/catalog.yaml`) | migrate job |
| `raw` | vendor data as ingested + ingestion audit (`ingestion_batches`) | ingestion |
| `features` | one `feature_runs` row per build, 96 `forecast_features` rows (4 zones × 24 h) | feature-service |
| `ml` | model registry (`models`, `model_aliases`, `model_events`), forecast runs, forecasts | training jobs, forecast-service |
| `quality` | every check result; publish/hold decisions | forecast-pipeline |
| `planning` | dispatch plans and per-zone hourly intervals | planning-api |

## Roles and least privilege

```mermaid
flowchart LR
    owner[gridcast_owner] -->|owns, migrates| ALL[(all schemas)]
    ingest[gridcast_ingest] -->|write| raw[(raw)]
    ingest -->|read| ref[(ref)]
    app["gridcast_app<br/>(feature-service + forecast-service + training)"] -->|write| features[(features)] & ml[(ml)]
    app -->|read| raw & ref
    planning[gridcast_planning] -->|write| plan[(planning)]
    planning -->|read| ml & quality[(quality)] & raw
    pipeline[gridcast_pipeline] -->|write| quality
    pipeline -->|read| raw & features & ml & plan
    ro["gridcast_readonly<br/>(Grafana, pgAdmin, exporter, Lumis)"] -->|read + pg_monitor| ALL
```

Roles are created on first boot by `infra/postgres/init/00-gridcast.sh` (passwords from
`.env`); grants and default privileges are applied by the first migration. `gridcast_app` is
deliberately **shared** by two services — that shared credential is what scenario C breaks.

## Viewing the database and its ER diagram (pgAdmin)

1. Open <http://localhost:5050> (desktop mode, no login needed).
2. Expand **Servers → GridCast → GridCast (owner – full ERD and DDL)**. The connection is
   pre-provisioned; the password comes from a passfile rendered at container start.
3. Right-click the **gridcast** database → **ERD For Database**. pgAdmin draws all six schemas
   with keys and relationships; you can rearrange, export as PNG/SQL, or right-click a single
   table → **ERD For Table**.
4. **Query Tool** on the read-only server is safe for exploration; the owner server can run DDL.

Other ways in: `gridcastctl psql` (read-only psql), the **GridCast — Data, models and decisions**
Grafana dashboard (SQL panels), or any client at `postgresql://gridcast_readonly@localhost:5432/gridcast`.

## Useful queries

```sql
-- Latest pipeline decisions
SELECT validated_at, decision, failed_checks, warnings
FROM quality.forecast_validations ORDER BY validated_at DESC LIMIT 10;

-- Feature builds: who built them and how expensive they were (scenario A shows up here)
SELECT created_at, builder_version, lag_resolution, db_queries, round(duration_ms) AS ms
FROM features.feature_runs ORDER BY created_at DESC LIMIT 10;

-- Model registry history (scenario E shows up here)
SELECT at, event, alias, version, previous_version, actor, reason
FROM ml.model_events ORDER BY at DESC;

-- Top statements by calls (query amplification evidence)
SELECT calls, rows, round(mean_exec_time::numeric, 2) AS mean_ms, left(query, 90)
FROM pg_stat_statements ORDER BY calls DESC LIMIT 10;

-- Published forecast vs actual for the last completed hour, per zone
WITH plan AS (SELECT forecast_run_id FROM planning.dispatch_plans
              WHERE published_at <= date_trunc('hour', now()) - interval '1 hour'
              ORDER BY published_at DESC LIMIT 1)
SELECT f.zone_id, f.load_mw_p50, avg(d.load_mw) AS actual_mw
FROM ml.forecasts f JOIN plan USING (forecast_run_id)
JOIN raw.demand_readings d ON d.zone_id = f.zone_id
     AND date_trunc('hour', d.ts) = f.target_ts
WHERE f.target_ts = date_trunc('hour', now()) - interval '1 hour'
GROUP BY f.zone_id, f.load_mw_p50;
-- (the planning API computes rolling MAPE/coverage: GET http://localhost:8080/v1/accuracy?hours=6)
```
