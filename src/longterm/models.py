"""Monthly models. Each predicts the change from the last 30 days to the target month.

Predicting the change rather than the level keeps every model anchored to the
one thing known for certain — where the price is now — and lets the reference
model be the plainest possible statement: no change.

    lt_persistence   no change: next month is the last 30 days
    lt_damped        part of the usual seasonal step, part of the way to the
                     year's mean; both parts chosen weekly on known outcomes
    lt_fundamental   plus a ridge regression on hydro, nuclear and gas
    lt_market        the futures price (live only: no free settlement history)

The full seasonal step was tried first and lost to plain persistence by half
again (MAE 29.5 against 19.8): with three years behind it, a monthly median
is mostly noise. A quarter of the step, chosen walk-forward, is what survives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FEATURES = ("seasonal_diff", "hydro_anomaly", "nuclear_delta_gw", "gas_gap")

PERSISTENCE = "lt_persistence"
DAMPED = "lt_damped"
FUNDAMENTAL = "lt_fundamental"
MARKET = "lt_market"
BACKTESTED = (PERSISTENCE, DAMPED, FUNDAMENTAL)

DESCRIPTIONS_SV = {
    PERSISTENCE: (
        "Oförändrat",
        "Nästa månad blir som de senaste 30 dagarna. Referensen alla andra mäts mot.",
    ),
    DAMPED: (
        "Dämpad säsong",
        "De senaste 30 dagarna, flyttade en bit mot den skillnad som brukar finnas "
        "mellan årstiden nu och målmånaden, och en bit mot det senaste årets snitt. "
        "Hur stora bitarna är väljs varje vecka på utfall som redan är kända.",
    ),
    FUNDAMENTAL: (
        "Fundamental",
        "Säsongsjusteringen plus en regression på vattenmagasinens nivå mot det "
        "normala, planerade kärnkraftsstopp och gaskraftens kostnad.",
    ),
    MARKET: (
        "Terminsmarknaden",
        "Euronext Nord Pools terminer: systempriset plus områdesdifferensen (EPAD) för "
        "elområdet. Månadskontrakt där det finns, annars kvartalskontraktet.",
    ),
}

# Weak on purpose. Three years of monthly history is a few dozen independent
# observations per zone; a regression allowed to fit them freely will.
RIDGE_LAMBDA = 20.0
MIN_TRAIN_SAMPLES = 40


@dataclass
class Ridge:
    mean: np.ndarray
    scale: np.ndarray
    beta: np.ndarray
    intercept: float
    n: int

    def predict(self, x: np.ndarray) -> float:
        z = (x - self.mean) / self.scale
        return float(self.intercept + z @ self.beta)


def fit_ridge(x: np.ndarray, y: np.ndarray, lam: float = RIDGE_LAMBDA) -> Ridge:
    """Ridge on standardised features; the intercept is not penalised."""
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale
    intercept = float(y.mean())
    beta = np.linalg.solve(z.T @ z + lam * np.eye(z.shape[1]), z.T @ (y - intercept))
    return Ridge(mean=mean, scale=scale, beta=beta, intercept=intercept, n=len(y))


def number(value) -> float | None:
    """A finite float, or None. Rows pass through pandas, which turns None into NaN."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else value


def feature_vector(sample: dict) -> np.ndarray:
    """Missing features count as neutral (0): no anomaly, no change, no gap."""
    return np.array([number(sample.get(name)) or 0.0 for name in FEATURES])


def baseline_predictions(sample: dict) -> dict[str, float | None]:
    return {PERSISTENCE: number(sample.get("recent"))}


# How far toward the seasonal step and toward the year's mean. A coarse grid
# on purpose: two weights from a short record, not a fitted curve.
DAMP_GRID_SEASONAL = (0.0, 0.25, 0.5, 0.75, 1.0)
DAMP_GRID_YEAR = (0.0, 0.1, 0.2, 0.3, 0.4)


def damped_prediction(sample: dict, weights: tuple[float, float]) -> float | None:
    recent = number(sample.get("recent"))
    if recent is None:
        return None
    seasonal, toward_year = weights
    year = number(sample.get("year_mean"))
    step = number(sample.get("seasonal_diff")) or 0.0
    return recent + seasonal * step + toward_year * ((year - recent) if year is not None else 0.0)


def fit_damping(rows: list[dict]) -> tuple[float, float]:
    """The grid point with the lowest MAE on rows whose outcome is known.

    Pooled over zones and horizons: two weights, and the more rows behind each
    the better. Too little history keeps plain persistence, (0, 0).
    """
    usable = [r for r in rows if number(r.get("actual")) is not None and number(r.get("recent")) is not None]
    if len(usable) < MIN_TRAIN_SAMPLES:
        return (0.0, 0.0)
    best, best_mae = (0.0, 0.0), math.inf
    for seasonal in DAMP_GRID_SEASONAL:
        for toward_year in DAMP_GRID_YEAR:
            weights = (seasonal, toward_year)
            mae = sum(abs(damped_prediction(r, weights) - r["actual"]) for r in usable) / len(usable)
            if mae < best_mae - 1e-9:
                best, best_mae = weights, mae
    return best


def fundamental_prediction(model: Ridge | None, sample: dict) -> float | None:
    recent = number(sample.get("recent"))
    if model is None or recent is None:
        return None
    return recent + model.predict(feature_vector(sample))
