import numpy as np

from gridcast.features.engineering import MODEL_FEATURES
from gridcast.ml.estimators import QuantileGBM, ScenarioForest


def _data(n=600, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, len(MODEL_FEATURES)))
    X[:, MODEL_FEATURES.index("zone_idx")] = rng.integers(0, 4, n)
    X[:, MODEL_FEATURES.index("horizon_h")] = rng.integers(1, 25, n)
    y = 1 + 0.1 * X[:, 0] + rng.normal(0, 0.02, n)
    return X, y


def test_quantile_gbm_quantiles_are_ordered():
    X, y = _data()
    q = QuantileGBM(max_iter=30).fit(X, y).predict_quantiles(X[:50])
    assert q.shape == (50, 3)
    assert np.all(q[:, 0] <= q[:, 1]) and np.all(q[:, 1] <= q[:, 2])


def test_scenario_forest_produces_calibrated_spread():
    X, y = _data()
    model = ScenarioForest(n_estimators=10, n_scenarios=20).fit(X, y)
    q = model.predict_quantiles(X[:30])
    assert model.residual_std > 0
    assert np.all(q[:, 2] - q[:, 0] > 0)
