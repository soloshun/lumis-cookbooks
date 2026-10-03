"""Model training: real historical weather (Open-Meteo ERA5 archive) + simulated demand.

1. Fetch ~2 years of hourly weather per station (cached in object storage).
2. Simulate hourly zone demand from that weather with the same model the grid-telemetry vendor
   uses, so the learned weather->load relationship matches the live estate.
3. Build training samples with the *serving* feature definitions (vectorized), including
   lead-time-dependent noise on weather to mimic vendor forecast error.
4. Time-based holdout evaluation, artifact upload, registry entry, optional alias promotion.
"""

import io
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from gridcast.catalog import catalog
from gridcast.demand.model import demand_mw
from gridcast.features.engineering import MODEL_FEATURES, zone_index
from gridcast.ml import registry
from gridcast.ml.artifacts import ArtifactStore
from gridcast.ml.estimators import _WEATHER_SCALE, PROFILES
from gridcast.weather import openmeteo
from gridcast.weather.model import VARIABLES, WeatherPoint, synthetic_weather

log = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    profile: str = "standard"
    history_days: int = 730
    holdout_days: int = 60
    horizons_per_target: int = 4
    max_train_rows: int | None = None
    max_eval_rows: int | None = 4000
    weather_source: str = "openmeteo"  # or "synthetic" (offline)
    seed: int = 7


def _weather_frame(store: ArtifactStore | None, station, start: date, end: date, source: str
                   ) -> tuple[pd.DataFrame, str]:
    index = pd.date_range(datetime.combine(start, datetime.min.time(), UTC),
                          datetime.combine(end, datetime.min.time(), UTC) + timedelta(hours=23),
                          freq="h")
    if source == "openmeteo":
        key = f"datasets/openmeteo/{station.id}/{start}_{end}.csv"
        try:
            if store is not None and store.exists(key):
                frame = pd.read_csv(io.BytesIO(store.get_bytes(key)), index_col=0, parse_dates=True)
                return frame, "open-meteo-archive"
            series = openmeteo.fetch_archive(station.latitude, station.longitude, start, end)
            frame = pd.DataFrame(
                [{"ts": ts, **p.as_dict()} for ts, p in series.items()]
            ).set_index("ts").reindex(index).interpolate(limit=6).dropna()
            if store is not None:
                store.put_bytes(key, frame.to_csv().encode())
            return frame, "open-meteo-archive"
        except Exception as exc:
            log.warning("archive weather unavailable, using synthetic",
                        extra={"station": station.id, "error": str(exc)[:200]})
    rows = [{"ts": ts, **synthetic_weather(station.latitude, station.longitude,
                                            ts.to_pydatetime()).as_dict()} for ts in index]
    return pd.DataFrame(rows).set_index("ts"), "synthetic"


def _hourly_demand(zone, weather: pd.DataFrame) -> pd.Series:
    """Hourly mean demand, approximated from quarter-hour samples with interpolated weather."""
    values = weather[list(VARIABLES)].to_numpy()
    out = np.empty(len(weather))
    for i, ts in enumerate(weather.index):
        here = WeatherPoint(*values[i])
        nxt = WeatherPoint(*values[min(i + 1, len(values) - 1)])
        samples = []
        for q in range(4):
            w = q / 4
            samples.append(demand_mw(zone, ts.to_pydatetime() + timedelta(minutes=15 * q),
                                     WeatherPoint.lerp(here, nxt, w)))
        out[i] = float(np.mean(samples))
    return pd.Series(out, index=weather.index)


def build_samples(load: pd.Series, weather: pd.DataFrame, zone_id: str, *, horizons: int,
                  rng: np.random.Generator) -> pd.DataFrame:
    """Vectorized equivalent of `gridcast.features.engineering.build_row` over a history."""
    cat = catalog()
    n = len(load)
    lv = load.to_numpy()
    r24 = load.rolling(24).mean().to_numpy()
    r3 = load.rolling(3).mean().to_numpy()
    positions = np.arange(24 + 168, n)
    ks = rng.integers(1, 25, size=(len(positions), horizons))
    pos = np.repeat(positions, horizons)
    k = ks.reshape(-1)
    lag24_pos = np.where(k < 24, pos - 24, pos - 48)
    frame = pd.DataFrame({
        "zone_id": zone_id,
        "target_ts": load.index[pos],
        "horizon_h": k,
        "load_lag_24h": lv[lag24_pos],
        "load_lag_168h": lv[pos - 168],
        # rolling(w)[i] averages hours i-w+1..i. With as_of A = T - k at position pos-k, the
        # windows ending at hour A-1h sit at position pos-k-1.
        "load_mean_24h": r24[pos - k - 1],
        "load_recent_3h": r3[pos - k - 1],
        "target_load": lv[pos],
    })
    lead = np.sqrt(k / 24)
    wv = weather.reindex(load.index)
    for name in VARIABLES:
        noise = rng.standard_normal(len(pos)) * _WEATHER_SCALE[name] * lead
        values = wv[name].to_numpy()[pos] + noise
        frame[name] = np.clip(values, -50 if name == "temperature_c" else 0, None)
    ts = pd.DatetimeIndex(frame["target_ts"])
    frame["hour"] = ts.hour
    frame["dow"] = ts.dayofweek
    frame["is_holiday"] = [cat.is_holiday(t) for t in ts]
    return frame.dropna()


def design(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cat = catalog()
    base = frame["zone_id"].map(lambda z: cat.zone(z).base_load_mw).to_numpy()
    X = pd.DataFrame({
        **{v: frame[v] for v in VARIABLES},
        "hour": frame["hour"], "dow": frame["dow"], "is_holiday": frame["is_holiday"].astype(float),
        "horizon_h": frame["horizon_h"],
        "load_lag_24h_n": frame["load_lag_24h"] / base,
        "load_lag_168h_n": frame["load_lag_168h"] / base,
        "load_mean_24h_n": frame["load_mean_24h"] / base,
        "load_recent_3h_n": frame["load_recent_3h"] / base,
        "zone_idx": frame["zone_id"].map(zone_index()),
    })[list(MODEL_FEATURES)].to_numpy(dtype=np.float64)
    y = (frame["target_load"] / base).to_numpy()
    return X, y, base


def evaluate(model, X: np.ndarray, y: np.ndarray, base: np.ndarray) -> dict[str, float]:
    t0 = time.perf_counter()
    q = model.predict_quantiles(X)
    elapsed = time.perf_counter() - t0
    actual, p10, p50, p90 = y * base, q[:, 0] * base, q[:, 1] * base, q[:, 2] * base
    pinball = np.mean([
        np.mean(np.maximum(tau * (actual - p), (tau - 1) * (actual - p)))
        for tau, p in ((0.1, p10), (0.5, p50), (0.9, p90))
    ])
    return {
        "mape_p50": float(np.mean(np.abs(actual - p50) / actual)),
        "mae_mw": float(np.mean(np.abs(actual - p50))),
        "pinball_mw": float(pinball),
        "coverage_p10_p90": float(np.mean((actual >= p10) & (actual <= p90))),
        "inference_ms_per_1k_rows": float(elapsed / len(X) * 1000 * 1000),
        "holdout_rows": int(len(X)),
    }


def train(engine: Engine, config: TrainingConfig, *, store: ArtifactStore, actor: str,
          promote_alias: str | None = None) -> dict:
    rng = np.random.default_rng(config.seed)
    end = date.today() - timedelta(days=6)  # ERA5 archive lags ~5 days
    start = end - timedelta(days=config.history_days)
    cat = catalog()
    frames, sources = [], set()
    for zone in cat.zones:
        station = cat.station(zone.station_id)
        weather, source = _weather_frame(store, station, start, end, config.weather_source)
        sources.add(source)
        load = _hourly_demand(zone, weather)
        frames.append(build_samples(load, weather, zone.id, horizons=config.horizons_per_target,
                                    rng=rng))
        log.info("zone samples built", extra={"zone": zone.id, "rows": len(frames[-1]),
                                               "weather_source": source})
    data = pd.concat(frames, ignore_index=True)
    cutoff = pd.Timestamp(end, tz=UTC) - pd.Timedelta(days=config.holdout_days)
    train_df = data[data["target_ts"] < cutoff]
    test_df = data[data["target_ts"] >= cutoff]
    if config.max_train_rows and len(train_df) > config.max_train_rows:
        train_df = train_df.sample(config.max_train_rows, random_state=config.seed)
    Xtr, ytr, _ = design(train_df)
    if config.max_eval_rows and len(test_df) > config.max_eval_rows:
        test_df = test_df.sample(config.max_eval_rows, random_state=config.seed)
    Xte, yte, bte = design(test_df)

    model = PROFILES[config.profile]()
    t0 = time.perf_counter()
    model.fit(Xtr, ytr)
    fit_seconds = time.perf_counter() - t0
    metrics = {**evaluate(model, Xte, yte, bte), "fit_seconds": round(fit_seconds, 1),
               "train_rows": int(len(Xtr))}
    log.info("model trained", extra={"profile": config.profile, **metrics})

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    uri, size = store.put_object(f"models/{registry.MODEL_NAME}/{config.profile}-{stamp}.joblib",
                                 model)
    with engine.begin() as conn:
        version = registry.register(
            conn, algorithm=model.algorithm, profile=config.profile, params=model.params(),
            metrics=metrics, feature_names=list(MODEL_FEATURES), artifact_uri=uri,
            artifact_bytes=size,
            train_start=datetime.combine(start, datetime.min.time(), UTC),
            train_end=datetime.combine(end, datetime.min.time(), UTC),
            data_source="+".join(sorted(sources)), created_by=actor,
        )
        if promote_alias:
            registry.set_alias(conn, promote_alias, version, actor=actor,
                               reason=f"initial {config.profile} model")
    return {"version": version, "artifact_uri": uri, "artifact_bytes": size, "metrics": metrics}
