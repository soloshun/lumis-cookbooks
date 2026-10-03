from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from gridcast.features.engineering import (
    HOUR,
    MODEL_FEATURES,
    MissingHistory,
    build_row,
    lag_hour,
    to_matrix,
)
from gridcast.ml.training import build_samples, design
from gridcast.weather.model import VARIABLES

A = datetime(2026, 9, 20, 12, tzinfo=UTC)
WEATHER = dict.fromkeys(VARIABLES, 1.0)


def hourly_series(start: datetime, hours: int) -> dict[datetime, float]:
    return {start + HOUR * i: 1000.0 + i for i in range(hours)}


def test_lag_hour_falls_back_to_two_days_for_k24():
    assert lag_hour(A, A + HOUR * 1) == A + HOUR - timedelta(hours=24)
    assert lag_hour(A, A + HOUR * 24) == A + HOUR * 24 - timedelta(hours=48)


def test_build_row_uses_only_data_before_as_of():
    series = hourly_series(A - timedelta(hours=200), 200)  # ends at A - 1h
    row = build_row("zone-accra", A, 3, series, WEATHER)
    assert row.target_ts == A + HOUR * 3
    assert row.load_lag_24h == series[A + HOUR * 3 - timedelta(hours=24)]
    assert row.load_lag_168h == series[A + HOUR * 3 - timedelta(hours=168)]
    assert row.load_recent_3h == pytest.approx(np.mean([series[A - HOUR * i] for i in (1, 2, 3)]))
    assert row.load_mean_24h == pytest.approx(np.mean([series[A - HOUR * i] for i in range(1, 25)]))


def test_build_row_reports_missing_history():
    with pytest.raises(MissingHistory):
        build_row("zone-accra", A, 3, {}, WEATHER)


def test_design_matrix_follows_model_feature_order():
    series = hourly_series(A - timedelta(hours=200), 200)
    rows = [build_row("zone-kumasi", A, k, series, WEATHER).record() for k in (1, 24)]
    X = to_matrix(rows)
    assert X.shape == (2, len(MODEL_FEATURES))
    assert X[0, MODEL_FEATURES.index("horizon_h")] == 1
    assert X[1, MODEL_FEATURES.index("zone_idx")] == 1  # kumasi is the second zone


def test_vectorized_training_features_match_serving_definition():
    """Train/serve skew guard: training samples equal build_row on the same history."""
    index = pd.date_range(A - timedelta(days=12), periods=24 * 12, freq="h", tz=UTC)
    rng = np.random.default_rng(0)
    load = pd.Series(1000 + rng.normal(0, 50, len(index)).cumsum(), index=index)
    weather = pd.DataFrame({v: rng.normal(25, 3, len(index)) for v in VARIABLES}, index=index)
    samples = build_samples(load, weather, "zone-accra", horizons=3,
                            rng=np.random.default_rng(1))
    hourly = load.to_dict()
    for _, s in samples.sample(40, random_state=2).iterrows():
        as_of = s["target_ts"].to_pydatetime() - HOUR * int(s["horizon_h"])
        history = {h: v for h, v in hourly.items() if h < as_of}
        expected = build_row("zone-accra", as_of, int(s["horizon_h"]), history, WEATHER)
        assert s["load_lag_24h"] == pytest.approx(expected.load_lag_24h)
        assert s["load_lag_168h"] == pytest.approx(expected.load_lag_168h)
        assert s["load_mean_24h"] == pytest.approx(expected.load_mean_24h)
        assert s["load_recent_3h"] == pytest.approx(expected.load_recent_3h)
    X, y, base = design(samples)
    assert X.shape[1] == len(MODEL_FEATURES)
    assert np.all(base == 1150)
