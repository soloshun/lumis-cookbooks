"""Probabilistic load models. Both predict normalized load (load / zone base load) quantiles.

`QuantileGBM` (profile `standard`): three histogram gradient-boosting models (p10/p50/p90).
Fast, compact, the production default.

`ScenarioForest` (profile `hifi`): a random forest evaluated over an ensemble of perturbed
weather scenarios, propagating weather-forecast uncertainty into the load distribution. More
expressive uncertainty, but inference cost grows with `n_scenarios x n_trees`.
"""

from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor

from gridcast.features.engineering import CATEGORICAL, MODEL_FEATURES

QUANTILES = (0.1, 0.5, 0.9)
_WEATHER_SCALE = {  # forecast-error std at 24 h lead, per weather feature
    "temperature_c": 1.6, "relative_humidity_pct": 7.0, "cloud_cover_pct": 18.0,
    "wind_speed_ms": 1.2, "shortwave_radiation_wm2": 120.0, "precipitation_mm": 0.4,
}


def _categorical_mask() -> list[bool]:
    return [name in CATEGORICAL for name in MODEL_FEATURES]


@dataclass
class QuantileGBM:
    max_iter: int = 400
    learning_rate: float = 0.06
    max_leaf_nodes: int = 48
    random_state: int = 7
    models: dict[float, HistGradientBoostingRegressor] = field(default_factory=dict)

    algorithm = "hist_gradient_boosting_quantile"

    def params(self) -> dict:
        return {"max_iter": self.max_iter, "learning_rate": self.learning_rate,
                "max_leaf_nodes": self.max_leaf_nodes, "quantiles": list(QUANTILES)}

    def fit(self, X: np.ndarray, y: np.ndarray) -> "QuantileGBM":
        for q in QUANTILES:
            model = HistGradientBoostingRegressor(
                loss="quantile", quantile=q, max_iter=self.max_iter,
                learning_rate=self.learning_rate, max_leaf_nodes=self.max_leaf_nodes,
                categorical_features=_categorical_mask(), random_state=self.random_state,
            )
            self.models[q] = model.fit(X, y)
        return self

    def predict_quantiles(self, X: np.ndarray) -> np.ndarray:
        preds = np.column_stack([self.models[q].predict(X) for q in QUANTILES])
        return np.sort(preds, axis=1)


@dataclass
class ScenarioForest:
    n_estimators: int = 200
    max_depth: int = 16
    min_samples_leaf: int = 5
    n_scenarios: int = 1200
    random_state: int = 7
    forest: RandomForestRegressor | None = None
    residual_std: float = 0.0

    algorithm = "random_forest_weather_scenarios"

    def params(self) -> dict:
        return {"n_estimators": self.n_estimators, "max_depth": self.max_depth,
                "min_samples_leaf": self.min_samples_leaf, "n_scenarios": self.n_scenarios}

    def fit(self, X: np.ndarray, y: np.ndarray) -> "ScenarioForest":
        self.forest = RandomForestRegressor(
            n_estimators=self.n_estimators, max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf, max_features=0.6, n_jobs=1,
            oob_score=True, random_state=self.random_state,
        ).fit(X, y)
        # Out-of-bag residuals estimate the model's own error, added to every scenario draw.
        self.residual_std = float(np.std(y - self.forest.oob_prediction_))
        return self

    def predict_quantiles(self, X: np.ndarray) -> np.ndarray:
        assert self.forest is not None
        rng = np.random.default_rng(self.random_state)
        horizon = X[:, MODEL_FEATURES.index("horizon_h")]
        lead = np.sqrt(np.clip(horizon, 0, None) / 24)
        draws = []
        for _ in range(self.n_scenarios):
            scenario = X.copy()
            for name, scale in _WEATHER_SCALE.items():
                j = MODEL_FEATURES.index(name)
                noise = rng.standard_normal(len(X)) * scale * lead
                lower = -50.0 if name == "temperature_c" else 0.0
                scenario[:, j] = np.clip(scenario[:, j] + noise, lower, None)
            residual = rng.standard_normal(len(X)) * self.residual_std
            draws.append(self.forest.predict(scenario) + residual)
        stacked = np.stack(draws, axis=1)
        return np.column_stack([np.quantile(stacked, q, axis=1) for q in QUANTILES])


PROFILES = {"standard": QuantileGBM, "hifi": ScenarioForest}
