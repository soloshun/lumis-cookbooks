#!/usr/bin/env bash
# First-boot database setup: databases, least-privilege roles and extensions.
# Table-level grants are applied by the GridCast migrations (they own the schema).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE gridcast_owner    LOGIN PASSWORD '${GRIDCAST_OWNER_PASSWORD}';
CREATE ROLE gridcast_ingest   LOGIN PASSWORD '${GRIDCAST_INGEST_PASSWORD}';
CREATE ROLE gridcast_app      LOGIN PASSWORD '${GRIDCAST_APP_PASSWORD}';
CREATE ROLE gridcast_planning LOGIN PASSWORD '${GRIDCAST_PLANNING_PASSWORD}';
CREATE ROLE gridcast_pipeline LOGIN PASSWORD '${GRIDCAST_PIPELINE_PASSWORD}';
CREATE ROLE gridcast_readonly LOGIN PASSWORD '${GRIDCAST_READONLY_PASSWORD}';
CREATE ROLE prefect           LOGIN PASSWORD '${PREFECT_DB_PASSWORD}';

COMMENT ON ROLE gridcast_owner    IS 'Owns GridCast schemas; used only by migrations';
COMMENT ON ROLE gridcast_ingest   IS 'ingestion service: writes raw vendor data';
COMMENT ON ROLE gridcast_app      IS 'feature-service and forecast-service (shared credential)';
COMMENT ON ROLE gridcast_planning IS 'planning-api';
COMMENT ON ROLE gridcast_pipeline IS 'forecast pipeline: quality checks and validation';
COMMENT ON ROLE gridcast_readonly IS 'Observers: Grafana, pgAdmin, postgres-exporter, Lumis';

GRANT pg_monitor TO gridcast_readonly;

CREATE DATABASE gridcast OWNER gridcast_owner;
CREATE DATABASE prefect  OWNER prefect;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname gridcast <<SQL
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO gridcast_owner;
SQL
