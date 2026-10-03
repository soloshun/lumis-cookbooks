"""Generate the GridCast Grafana dashboards.

Usage: python3 infra/grafana/build_dashboards.py infra/grafana/dashboards
Edit panels here, regenerate, and Grafana picks up the JSON within ~30 s (file provisioning).
"""
import json, sys, pathlib
OUT = pathlib.Path(sys.argv[1])
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
PG = {"type": "grafana-postgresql-datasource", "uid": "gridcast-db"}
_id = [0]
def nid():
    _id[0] += 1; return _id[0]

def ts(title, targets, x, y, w=8, h=8, unit="short", desc="", stack=False, thresholds=None, ds=PROM, legend=True):
    fc = {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10, "showPoints": "never",
           "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []}
    if thresholds:
        fc["defaults"]["thresholds"] = {"mode": "absolute", "steps": [{"color": "green", "value": None}] +
            [{"color": c, "value": v} for v, c in thresholds]}
        fc["defaults"]["custom"]["thresholdsStyle"] = {"mode": "line+area"}
    return {"id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": ds,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "fieldConfig": fc,
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": legend},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": [{"refId": chr(65 + i), "datasource": ds, "expr": e, "legendFormat": l}
                        for i, (e, l) in enumerate(targets)]}

def stat(title, expr, x, y, w=4, h=5, unit="short", steps=((None, "green"),), desc="", decimals=None):
    d = {"unit": unit, "thresholds": {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}}
    if decimals is not None: d["decimals"] = decimals
    return {"id": nid(), "type": "stat", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {**d, "mappings": [{"type": "special", "options": {
                "match": "null+nan", "result": {"text": "awaiting data", "color": "text"}}}]},
                "overrides": []},
            "options": {"colorMode": "background", "graphMode": "area", "reduceOptions": {"calcs": ["lastNotNull"]}},
            "targets": [{"refId": "A", "datasource": PROM, "expr": expr}]}

def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

def table_sql(title, sql, x, y, w=12, h=8, desc=""):
    return {"id": nid(), "type": "table", "title": title, "description": desc, "datasource": PG,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"refId": "A", "datasource": PG, "format": "table", "rawQuery": True, "editorMode": "code", "rawSql": sql}]}

def logs(title, expr, x, y, w=24, h=10):
    return {"id": nid(), "type": "logs", "title": title, "datasource": LOKI, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
            "targets": [{"refId": "A", "datasource": LOKI, "expr": expr}]}

def dashboard(uid, title, panels, desc, annotations=()):
    return {"uid": uid, "title": title, "description": desc, "tags": ["gridcast"], "timezone": "utc",
            "schemaVersion": 41, "version": 1, "refresh": "30s", "time": {"from": "now-3h", "to": "now"},
            "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                      "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                      "name": "Annotations & Alerts", "type": "dashboard"}, *annotations]},
            "panels": panels, "templating": {"list": []}}

MODEL_ANN = {"name": "Model registry changes", "enable": True, "iconColor": "purple", "datasource": PG,
             "target": {"refId": "Anno", "rawQuery": True, "format": "table", "editorMode": "code",
                        "rawSql": "SELECT at AS time, 'model ' || event || coalesce(' ' || alias, '') || ' -> v' || version || ' by ' || actor AS text FROM ml.model_events WHERE $__timeFilter(at)"}}
DEPLOY_ANN = {"name": "Rollouts (k8s events)", "enable": True, "iconColor": "orange", "datasource": LOKI,
              "expr": '{service_name="kubernetes-events", k8s_namespace_name="gridcast"} |= "ScalingReplicaSet"', "titleFormat": "rollout",
              "textFormat": "{{__line__}}"}

y = 0; p = []
p.append(row("Business SLOs — what the grid operator experiences", y)); y += 1
p += [stat("Dispatch plan age", "max(gridcast_consumer_plan_age_seconds)", 0, y, unit="s",
           steps=((None, "green"), (600, "orange"), (900, "red")), desc="Age of the plan operators are using. SLO: < 15 min."),
      stat("Rolling MAPE (6 h)", "max(gridcast_consumer_forecast_mape_ratio)", 4, y, unit="percentunit", decimals=2,
           steps=((None, "green"), (0.05, "orange"), (0.08, "red"))),
      stat("p10–p90 coverage", "max(gridcast_consumer_coverage_ratio)", 8, y, unit="percentunit", decimals=1,
           steps=((None, "red"), (0.6, "orange"), (0.72, "green"))),
      stat("Pipeline runs published (1 h)", 'sum(increase(gridcast_pipeline_runs_total{status="published"}[1h]))', 12, y, decimals=0),
      stat("Pipeline runs held/failed (1 h)", 'sum(increase(gridcast_pipeline_runs_total{status!="published"}[1h])) or vector(0)', 16, y, decimals=0,
           steps=((None, "green"), (1, "orange"), (3, "red"))),
      stat("Firing alerts", 'count(ALERTS{alertstate="firing"}) or vector(0)', 20, y, decimals=0,
           steps=((None, "green"), (1, "red")))]
y += 5
p.append(row("Forecast pipeline (Prefect)", y)); y += 1
p += [ts("Pipeline duration", [("histogram_quantile(0.95, sum by (le) (rate(gridcast_pipeline_run_duration_seconds_bucket[10m])))", "p95"),
                               ("histogram_quantile(0.5, sum by (le) (rate(gridcast_pipeline_run_duration_seconds_bucket[10m])))", "p50")],
         0, y, unit="s", thresholds=[(15, "red")]),
      ts("Stage duration p95", [("histogram_quantile(0.95, sum by (le, stage) (rate(gridcast_pipeline_stage_duration_seconds_bucket[10m])))", "{{stage}}")],
         8, y, unit="s"),
      ts("Pipeline outcomes", [("sum by (status) (increase(gridcast_pipeline_runs_total[10m]))", "{{status}}")], 16, y, stack=True)]
y += 8
p.append(row("Services", y)); y += 1
p += [ts("Feature build: duration and SQL per build", [
            ("histogram_quantile(0.95, sum by (le, lag_resolution) (rate(gridcast_feature_build_duration_seconds_bucket[10m])))", "p95 duration ({{lag_resolution}})"),
         ], 0, y, unit="s"),
      ts("Feature build SQL statements / build", [
            ("sum(rate(gridcast_feature_db_queries_total[10m])) / sum(rate(gridcast_feature_builds_total[10m]))", "queries per build")],
         8, y, unit="short"),
      ts("Model inference p95 by model version", [
            ("histogram_quantile(0.95, sum by (le, model_version, profile) (rate(gridcast_inference_duration_seconds_bucket[10m])))", "v{{model_version}} {{profile}}")],
         16, y, unit="s")]
y += 8
p += [ts("HTTP p95 latency by service", [("histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_duration_milliseconds_bucket[5m])))", "{{service_name}}")],
         0, y, unit="ms", w=12),
      ts("HTTP 5xx rate by service", [('sum by (service_name) (rate(http_server_duration_milliseconds_count{http_status_code=~"5.."}[5m]))', "{{service_name}}")],
         12, y, unit="reqps", w=12)]
y += 8
p.append(row("Data: ingestion, freshness and quality", y)); y += 1
p += [ts("Data freshness by dataset", [("max by (dataset) (gridcast_data_freshness_seconds)", "{{dataset}}")], 0, y, unit="s",
         thresholds=[(600, "orange"), (1200, "red")]),
      ts("Ingestion batches", [("sum by (dataset, status) (increase(gridcast_ingest_batches_total[5m]))", "{{dataset}} {{status}}")], 8, y, stack=True),
      ts("Quality checks not passing", [('sum by (check, status) (increase(gridcast_quality_checks_total{status!="pass"}[15m]))', "{{check}} {{status}}")], 16, y)]
y += 8
p.append(row("PostgreSQL", y)); y += 1
p += [ts("Tuples fetched / s", [('sum(rate(pg_stat_database_tup_fetched{datname="gridcast"}[5m]))', "fetched"),
                                ('sum(rate(pg_stat_database_tup_returned{datname="gridcast"}[5m]))', "returned (scanned)")], 0, y, unit="short"),
      ts("Statements / s", [('sum(rate(pg_stat_statements_calls_total{datname="gridcast"}[5m]))', "calls")], 8, y, unit="short"),
      ts("Connections by state", [('sum by (state) (pg_stat_activity_count{datname="gridcast"})', "{{state}}")], 16, y, stack=True)]
y += 8
p.append(row("Kubernetes", y)); y += 1
p += [ts("CPU vs limit (gridcast)", [('max by (k8s_deployment_name) (k8s_container_cpu_limit_utilization_ratio{k8s_namespace_name="gridcast"})', "{{k8s_deployment_name}}")],
         0, y, unit="percentunit", thresholds=[(0.9, "red")]),
      ts("CPU throttling (share of periods)", [('sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{namespace="gridcast"}[5m])) / sum by (pod) (rate(container_cpu_cfs_periods_total{namespace="gridcast"}[5m]))', "{{pod}}")],
         8, y, unit="percentunit"),
      ts("Memory vs limit (gridcast)", [('max by (k8s_deployment_name) (k8s_container_memory_limit_utilization_ratio{k8s_namespace_name="gridcast"})', "{{k8s_deployment_name}}")],
         16, y, unit="percentunit", thresholds=[(0.9, "red")])]
y += 8
p += [ts("Container restarts", [('max by (k8s_deployment_name, k8s_container_name) (k8s_container_restarts{k8s_namespace_name=~"gridcast|vendors"})', "{{k8s_container_name}}")], 0, y, w=12),
      ts("Ready replicas vs desired", [('sum by (k8s_deployment_name) (k8s_deployment_available{k8s_namespace_name="gridcast"})', "{{k8s_deployment_name}} available")], 12, y, w=12)]
y += 8
p.append(row("Logs", y)); y += 1
p.append(logs("Warnings and errors (estate + vendors)", '{k8s_namespace_name=~"gridcast|vendors"} | detected_level=~"warn|error|WARN|ERROR"', 0, y))
y += 10
p.append(logs("Kubernetes events (rollouts, scheduling, OOMKilled, back-off)", '{service_name="kubernetes-events"} | json | line_format "{{.object_reason}} {{.object_regarding_kind}}/{{.object_regarding_name}}: {{.object_note}}"', 0, y, h=8))

overview = dashboard("gridcast-overview", "GridCast — Estate overview", p,
                     "SLOs, pipeline, services, data, database, Kubernetes and logs for the GridCast estate.",
                     annotations=(MODEL_ANN, DEPLOY_ANN))

_id[0] = 0; y = 0; q = []
q.append(row("Forecast vs actual (from PostgreSQL)", y)); y += 1
q.append({"id": nid(), "type": "timeseries", "title": "System load: published forecast (p10/p50/p90) vs actual", "datasource": PG,
          "gridPos": {"x": 0, "y": y, "w": 24, "h": 10},
          "fieldConfig": {"defaults": {"unit": "MW", "custom": {"lineWidth": 2, "fillOpacity": 0, "showPoints": "never"}}, "overrides": []},
          "targets": [
            {"refId": "A", "datasource": PG, "format": "time_series", "rawQuery": True, "editorMode": "code",
             "rawSql": "SELECT date_trunc('hour', ts) AS time, sum(load_mw)/count(DISTINCT date_trunc('minute', ts)) * 1 AS actual_mw FROM (SELECT ts, sum(load_mw) AS load_mw FROM raw.demand_readings WHERE $__timeFilter(ts) GROUP BY ts) s GROUP BY 1 ORDER BY 1"},
            {"refId": "B", "datasource": PG, "format": "time_series", "rawQuery": True, "editorMode": "code",
             "rawSql": "SELECT f.target_ts AS time, sum(f.load_mw_p50) AS forecast_p50, sum(f.load_mw_p10) AS forecast_p10, sum(f.load_mw_p90) AS forecast_p90 FROM planning.dispatch_plans p JOIN ml.forecasts f USING (forecast_run_id) WHERE p.status = 'active' GROUP BY 1 ORDER BY 1"}]})
y += 10
q += [table_sql("Model registry", "SELECT m.version, m.profile, m.algorithm, round((m.metrics->>'mape_p50')::numeric,4) AS holdout_mape, round((m.metrics->>'coverage_p10_p90')::numeric,3) AS coverage, string_agg(a.alias, ',') AS aliases, m.created_by, m.created_at FROM ml.models m LEFT JOIN ml.model_aliases a USING (model_name, version) GROUP BY m.model_name, m.version ORDER BY m.version DESC", 0, y),
      table_sql("Model registry events", "SELECT at, event, alias, version, previous_version, actor, reason FROM ml.model_events ORDER BY at DESC LIMIT 20", 12, y)]
y += 8
q += [table_sql("Recent validation decisions", "SELECT validated_at, decision, failed_checks, warnings FROM quality.forecast_validations ORDER BY validated_at DESC LIMIT 25", 0, y),
      table_sql("Recent feature builds", "SELECT created_at, builder_version, lag_resolution, status, rows, db_queries, round(duration_ms::numeric,1) AS duration_ms, error FROM features.feature_runs ORDER BY created_at DESC LIMIT 25", 12, y)]
y += 8
q += [table_sql("Ingestion batches (latest per dataset/status)", "SELECT DISTINCT ON (dataset, status) dataset, source, status, rows, started_at, error FROM raw.ingestion_batches ORDER BY dataset, status, started_at DESC", 0, y),
      table_sql("Forecast runs", "SELECT created_at, model_version, server_version, rows, round(inference_ms::numeric,1) AS inference_ms FROM ml.forecast_runs ORDER BY created_at DESC LIMIT 25", 12, y)]
data = dashboard("gridcast-data", "GridCast — Data, models and decisions", q,
                 "SQL views over the GridCast database: forecasts vs actuals, registry, validation gate, feature builds.",
                 annotations=(MODEL_ANN,))
(OUT / "gridcast-overview.json").write_text(json.dumps(overview, indent=1))
(OUT / "gridcast-data.json").write_text(json.dumps(data, indent=1))
print("ok", len(p), len(q))
