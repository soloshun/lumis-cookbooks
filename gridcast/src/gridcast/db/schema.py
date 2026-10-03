"""GridCast relational model.

Schemas follow the data's journey through the estate:

    ref       reference data (grid zones, weather stations, vendors)
    raw       vendor data exactly as ingested (weather observations/forecasts, demand)
    features  model inputs built per forecast run
    ml        model registry, forecast runs and forecasts
    quality   data-quality and forecast-validation results
    planning  dispatch plans published to downstream operators

Every table and column carries a comment so the ER diagram in pgAdmin is self-documenting.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)
SCHEMAS = ("ref", "raw", "features", "ml", "quality", "planning")


def _ts(name: str, comment: str, nullable: bool = False, **kw) -> Column:
    return Column(name, DateTime(timezone=True), nullable=nullable, comment=comment, **kw)


def _created() -> Column:
    return Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="Row creation time",
    )


WEATHER_COLUMNS = (
    ("temperature_c", "Air temperature at 2 m (degC)"),
    ("relative_humidity_pct", "Relative humidity at 2 m (%)"),
    ("cloud_cover_pct", "Total cloud cover (%)"),
    ("wind_speed_ms", "Wind speed at 10 m (m/s)"),
    ("shortwave_radiation_wm2", "Global horizontal irradiance (W/m2)"),
    ("precipitation_mm", "Precipitation over the interval (mm)"),
)


def _weather_columns() -> list[Column]:
    return [Column(name, Double, nullable=False, comment=c) for name, c in WEATHER_COLUMNS]


# --------------------------------------------------------------------------------------- ref
weather_stations = Table(
    "weather_stations", metadata,
    Column("station_id", Text, primary_key=True, comment="Station identifier"),
    Column("name", Text, nullable=False, comment="Human-readable name"),
    Column("latitude", Double, nullable=False, comment="WGS84 latitude"),
    Column("longitude", Double, nullable=False, comment="WGS84 longitude"),
    Column("elevation_m", Double, nullable=False, comment="Elevation above sea level (m)"),
    schema="ref", comment="Weather stations whose conditions drive zone demand",
)

zones = Table(
    "zones", metadata,
    Column("zone_id", Text, primary_key=True, comment="Load zone identifier"),
    Column("name", Text, nullable=False, comment="Human-readable name"),
    Column("station_id", Text, ForeignKey("ref.weather_stations.station_id"), nullable=False,
           comment="Representative weather station"),
    Column("base_load_mw", Double, nullable=False, comment="Nominal base load (MW)"),
    Column("cooling_mw_per_degc", Double, nullable=False, comment="Cooling load sensitivity"),
    Column("rooftop_solar_mw", Double, nullable=False, comment="Behind-the-meter solar capacity"),
    schema="ref", comment="Load zones of the synthetic national grid",
)

weather_providers = Table(
    "weather_providers", metadata,
    Column("provider_id", Text, primary_key=True, comment="Vendor identifier"),
    Column("name", Text, nullable=False, comment="Vendor name"),
    Column("priority", SmallInteger, nullable=False, comment="1 = primary, higher = fallback"),
    schema="ref", comment="External weather data vendors",
)

# --------------------------------------------------------------------------------------- raw
weather_observations = Table(
    "weather_observations", metadata,
    Column("station_id", Text, ForeignKey("ref.weather_stations.station_id"), primary_key=True,
           comment="Observing station"),
    _ts("observed_at", "Observation timestamp reported by the vendor", primary_key=True),
    Column("provider_id", Text, ForeignKey("ref.weather_providers.provider_id"), nullable=False,
           comment="Vendor that supplied the observation"),
    *_weather_columns(),
    _ts("ingested_at", "When GridCast stored the row", server_default=func.now()),
    schema="raw", comment="5-minute weather observations as received from vendors",
)

weather_forecasts = Table(
    "weather_forecasts", metadata,
    Column("station_id", Text, ForeignKey("ref.weather_stations.station_id"), primary_key=True,
           comment="Forecast location"),
    _ts("issued_at", "Vendor forecast issue time", primary_key=True),
    _ts("valid_at", "Hour the forecast is valid for", primary_key=True),
    Column("provider_id", Text, ForeignKey("ref.weather_providers.provider_id"), nullable=False,
           comment="Vendor that issued the forecast"),
    *_weather_columns(),
    _ts("ingested_at", "When GridCast stored the row", server_default=func.now()),
    Index(None, "station_id", "valid_at", "issued_at"),
    schema="raw", comment="Hourly vendor weather forecasts (one row per issue/valid pair)",
)

demand_readings = Table(
    "demand_readings", metadata,
    Column("zone_id", Text, ForeignKey("ref.zones.zone_id"), primary_key=True,
           comment="Load zone"),
    _ts("ts", "Start of the one-minute interval", primary_key=True),
    Column("load_mw", Double, nullable=False, comment="Average zone load over the minute (MW)"),
    Column("quality", Text, nullable=False, server_default="good",
           comment="SCADA quality flag (good/estimated/suspect)"),
    _ts("ingested_at", "When GridCast stored the row", server_default=func.now()),
    schema="raw", comment="One-minute zone demand from the grid telemetry historian",
)

ingestion_batches = Table(
    "ingestion_batches", metadata,
    Column("batch_id", BigInteger, primary_key=True, autoincrement=True, comment="Batch id"),
    Column("dataset", Text, nullable=False, comment="weather_observations / weather_forecasts / demand"),
    Column("source", Text, nullable=False, comment="Vendor or system the batch came from"),
    _ts("started_at", "Batch start"),
    _ts("finished_at", "Batch end", nullable=True),
    Column("rows", Integer, nullable=False, server_default="0", comment="Rows written"),
    Column("status", Text, nullable=False, comment="ok / error"),
    Column("error", Text, comment="Error summary when status = error"),
    Index(None, "dataset", "started_at"),
    schema="raw", comment="Audit trail of every ingestion batch",
)

# ---------------------------------------------------------------------------------- features
feature_runs = Table(
    "feature_runs", metadata,
    Column("feature_run_id", UUID(as_uuid=True), primary_key=True, comment="Feature run id"),
    _ts("as_of", "Information cut-off: no data after this instant is used"),
    Column("horizon_hours", SmallInteger, nullable=False, comment="Hours ahead covered"),
    Column("status", Text, nullable=False, comment="running / completed / failed"),
    Column("builder_version", Text, nullable=False, comment="feature-service version"),
    Column("lag_resolution", Text, nullable=False, comment="Resolution used for lag features"),
    Column("rows", Integer, comment="Feature rows produced"),
    Column("db_queries", Integer, comment="Database queries issued while building"),
    Column("duration_ms", Double, comment="Build wall-clock time (ms)"),
    Column("error", Text, comment="Failure summary"),
    _created(),
    Index(None, "created_at"),
    schema="features", comment="One feature build per forecast cycle",
)

forecast_features = Table(
    "forecast_features", metadata,
    Column("feature_run_id", UUID(as_uuid=True),
           ForeignKey("features.feature_runs.feature_run_id", ondelete="CASCADE"),
           primary_key=True, comment="Owning feature run"),
    Column("zone_id", Text, ForeignKey("ref.zones.zone_id"), primary_key=True, comment="Zone"),
    _ts("target_ts", "Hour being forecast", primary_key=True),
    Column("horizon_h", SmallInteger, nullable=False, comment="Hours between as_of and target"),
    *[Column(name, Double, nullable=False, comment=f"Forecast {c.lower()}")
      for name, c in WEATHER_COLUMNS],
    Column("hour", SmallInteger, nullable=False, comment="Hour of day (UTC)"),
    Column("dow", SmallInteger, nullable=False, comment="Day of week (0 = Monday)"),
    Column("is_holiday", Boolean, nullable=False, comment="Public holiday flag"),
    Column("load_lag_24h", Double, nullable=False, comment="Mean load same hour yesterday (MW)"),
    Column("load_lag_168h", Double, nullable=False, comment="Mean load same hour last week (MW)"),
    Column("load_mean_24h", Double, nullable=False, comment="Mean load over 24 h before as_of"),
    Column("load_recent_3h", Double, nullable=False, comment="Mean load over 3 h before as_of"),
    schema="features", comment="Model inputs for each zone and target hour",
)

# ---------------------------------------------------------------------------------------- ml
models = Table(
    "models", metadata,
    Column("model_name", Text, primary_key=True, comment="Registered model name"),
    Column("version", Integer, primary_key=True, comment="Monotonic version"),
    Column("algorithm", Text, nullable=False, comment="Estimator family"),
    Column("profile", Text, nullable=False, comment="Training profile (standard / hifi)"),
    Column("params", JSONB, nullable=False, comment="Hyper-parameters"),
    Column("metrics", JSONB, nullable=False, comment="Holdout evaluation metrics"),
    Column("feature_names", JSONB, nullable=False, comment="Ordered model inputs"),
    Column("artifact_uri", Text, nullable=False, comment="s3:// URI of the serialized model"),
    Column("artifact_bytes", BigInteger, comment="Artifact size"),
    _ts("train_start", "First training timestamp"),
    _ts("train_end", "Last training timestamp"),
    Column("data_source", Text, nullable=False, comment="Weather source used for training"),
    Column("created_by", Text, nullable=False, comment="Who/what trained the model"),
    _created(),
    schema="ml", comment="Model registry: every trained model version",
)

model_aliases = Table(
    "model_aliases", metadata,
    Column("model_name", Text, primary_key=True, comment="Registered model"),
    Column("alias", Text, primary_key=True, comment="production / staging / champion ..."),
    Column("version", Integer, nullable=False, comment="Version the alias points to"),
    _ts("updated_at", "Last alias change", server_default=func.now()),
    Column("updated_by", Text, nullable=False, comment="Actor that moved the alias"),
    ForeignKeyConstraint(["model_name", "version"], ["ml.models.model_name", "ml.models.version"]),
    schema="ml", comment="Mutable pointers (e.g. production) into the registry",
)

model_events = Table(
    "model_events", metadata,
    Column("event_id", BigInteger, primary_key=True, autoincrement=True, comment="Event id"),
    Column("model_name", Text, nullable=False, comment="Registered model"),
    Column("version", Integer, nullable=False, comment="Model version"),
    Column("event", Text, nullable=False, comment="registered / alias_set"),
    Column("alias", Text, comment="Alias affected, if any"),
    Column("previous_version", Integer, comment="Version the alias pointed to before"),
    Column("actor", Text, nullable=False, comment="Who made the change"),
    Column("reason", Text, comment="Change description"),
    _ts("at", "Event time", server_default=func.now()),
    ForeignKeyConstraint(["model_name", "version"], ["ml.models.model_name", "ml.models.version"]),
    Index(None, "at"),
    schema="ml", comment="Append-only history of registry changes",
)

forecast_runs = Table(
    "forecast_runs", metadata,
    Column("forecast_run_id", UUID(as_uuid=True), primary_key=True, comment="Forecast run id"),
    Column("feature_run_id", UUID(as_uuid=True), ForeignKey("features.feature_runs.feature_run_id"),
           nullable=False, comment="Inputs used"),
    Column("model_name", Text, nullable=False, comment="Model used"),
    Column("model_version", Integer, nullable=False, comment="Model version used"),
    Column("status", Text, nullable=False, comment="completed / failed"),
    Column("rows", Integer, comment="Forecast rows"),
    Column("inference_ms", Double, comment="Model inference time (ms)"),
    Column("server_version", Text, nullable=False, comment="forecast-service version"),
    _created(),
    ForeignKeyConstraint(["model_name", "model_version"], ["ml.models.model_name", "ml.models.version"]),
    Index(None, "created_at"),
    schema="ml", comment="One model inference per forecast cycle",
)

forecasts = Table(
    "forecasts", metadata,
    Column("forecast_run_id", UUID(as_uuid=True),
           ForeignKey("ml.forecast_runs.forecast_run_id", ondelete="CASCADE"), primary_key=True,
           comment="Owning forecast run"),
    Column("zone_id", Text, ForeignKey("ref.zones.zone_id"), primary_key=True, comment="Zone"),
    _ts("target_ts", "Forecast hour", primary_key=True),
    Column("horizon_h", SmallInteger, nullable=False, comment="Hours ahead"),
    Column("load_mw_p50", Double, nullable=False, comment="Median forecast load (MW)"),
    Column("load_mw_p10", Double, nullable=False, comment="10th percentile (MW)"),
    Column("load_mw_p90", Double, nullable=False, comment="90th percentile (MW)"),
    CheckConstraint("load_mw_p10 <= load_mw_p90", name="quantiles_ordered"),
    Index(None, "zone_id", "target_ts"),
    schema="ml", comment="Hourly probabilistic load forecasts",
)

# ----------------------------------------------------------------------------------- quality
check_results = Table(
    "check_results", metadata,
    Column("check_id", BigInteger, primary_key=True, autoincrement=True, comment="Result id"),
    Column("pipeline_run_id", Text, comment="Orchestrator (Prefect) flow run id"),
    Column("subject", Text, nullable=False, comment="Dataset or artifact checked"),
    Column("check_name", Text, nullable=False, comment="Check identifier"),
    Column("status", Text, nullable=False, comment="pass / warn / fail"),
    Column("observed", Double, comment="Observed value"),
    Column("threshold", Double, comment="Threshold applied"),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb"),
           comment="Structured context"),
    _ts("checked_at", "Evaluation time", server_default=func.now()),
    Index(None, "check_name", "checked_at"),
    schema="quality", comment="Every data-quality and forecast-validation check",
)

forecast_validations = Table(
    "forecast_validations", metadata,
    Column("validation_id", UUID(as_uuid=True), primary_key=True, comment="Validation id"),
    Column("forecast_run_id", UUID(as_uuid=True), ForeignKey("ml.forecast_runs.forecast_run_id"),
           nullable=False, comment="Forecast validated"),
    Column("pipeline_run_id", Text, comment="Orchestrator flow run id"),
    Column("decision", Text, nullable=False, comment="publish / hold"),
    Column("failed_checks", JSONB, nullable=False, comment="Names of failing checks"),
    Column("warnings", JSONB, nullable=False, comment="Names of warning checks"),
    _ts("validated_at", "Decision time", server_default=func.now()),
    schema="quality", comment="Publish/hold gate decisions for each forecast",
)

# ---------------------------------------------------------------------------------- planning
dispatch_plans = Table(
    "dispatch_plans", metadata,
    Column("plan_id", UUID(as_uuid=True), primary_key=True, comment="Plan id"),
    Column("forecast_run_id", UUID(as_uuid=True), ForeignKey("ml.forecast_runs.forecast_run_id"),
           nullable=False, comment="Forecast the plan is based on"),
    Column("status", Text, nullable=False, comment="active / superseded"),
    _ts("valid_from", "First planned hour"),
    _ts("valid_to", "Last planned hour"),
    Column("total_energy_mwh", Double, nullable=False, comment="Planned energy over the horizon"),
    Column("peak_load_mw", Double, nullable=False, comment="System peak in the plan"),
    Column("published_by", Text, nullable=False, comment="Publishing pipeline run"),
    _ts("published_at", "Publication time", server_default=func.now()),
    Index(None, "published_at"),
    schema="planning", comment="Day-ahead dispatch plans consumed by grid operators",
)

plan_intervals = Table(
    "plan_intervals", metadata,
    Column("plan_id", UUID(as_uuid=True), ForeignKey("planning.dispatch_plans.plan_id",
           ondelete="CASCADE"), primary_key=True, comment="Owning plan"),
    Column("zone_id", Text, ForeignKey("ref.zones.zone_id"), primary_key=True, comment="Zone"),
    _ts("interval_start", "Hour", primary_key=True),
    Column("forecast_load_mw", Double, nullable=False, comment="Expected load (p50)"),
    Column("reserve_mw", Double, nullable=False, comment="Operating reserve (p90 - p50 + margin)"),
    Column("scheduled_generation_mw", Double, nullable=False, comment="Generation to schedule"),
    schema="planning", comment="Per-zone hourly schedule within a plan",
)
