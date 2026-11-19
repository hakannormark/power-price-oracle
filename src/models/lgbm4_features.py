"""The feature matrix of lightgbm_v4: v3's inputs, made blind to the price level.

v4 exists because of one measured flaw in its predecessors. lightgbm_v2 and v3
predict the residual price - shrunk level, which was meant to keep the trees
free of level bias in a new price regime. But both also receive the raw level
(shrunk, the weekly lags, raw gas and carbon prices) as split variables. In the
history they learned from, a level above 150 EUR/MWh was nearly always a short
spike, so the trees learned to pull high levels down. Asked directly, v2's q50
at 96 h in SE4 adds -18 at a level of 100, -36 at 200 and -98 at 280. In
October-November 2026 the high level persisted, and v2's bias in SE4 reached
-55 EUR/MWh from day 3.

v4 therefore sees no price level at all:

* The target is (price - shrunk) / scale, where scale is the zone's mean price
  over the last seven known days (at least SCALE_FLOOR). A move of 20 % is the
  same sample in SE1 at 30 EUR/MWh and in SE4 at 250.
* Every price feature is a difference to the shrunk level or to the week,
  divided by the same scale. The raw level, the raw lags and raw gas and carbon
  prices are not features.
* Gas and carbon enter as their change over one and four weeks, and as the gap
  between a gas plant's marginal cost and the week's price, also over the scale.

Weather is v3's, raw and unchanged: it carries no price level.

Built on lgbm3_features.build_matrix, so the rule that only prices published by
the issue time are read is inherited, not reimplemented.
"""

from __future__ import annotations

import numpy as np

from . import lgbm3_features as F

SCALE_FLOOR = 20.0  # EUR/MWh: below this a week's mean is too small to divide by
# A CCGT burns about two MWh of gas per MWh of power and emits 0.37 t CO2.
GAS_HEAT_RATE = 2.0
GAS_EMISSIONS = 0.37
SHORT_LEAD = 1  # the day after the last known day; it has its own model

V3 = {name: i for i, name in enumerate(F.FEATURE_NAMES)}

FEATURE_NAMES = [
    "zone", "hour", "dow", "month", "is_weekend", "is_holiday", "lead",
    # prices known at issue, relative to the shrunk level and the week
    "r_last", "r_lag168", "r_lag336", "r_shrunk_week", "r_hour_day",
    "r_day_week", "r_day_range", "r_spread_se4_se3", "r_spread_se3_se2",
    # weather, exactly as v3
    "temp", "hdd", "wind", "solar", "wind_north", "wind_south", "wind_de", "solar_de", "wind_dk",
    "temp_ref", "wind_ref", "wind_south_ref", "wind_de_ref", "solar_ref",
    # fuels as change and as a gap, never as a level
    "ttf_chg7", "ttf_chg28", "eua_chg28", "r_gas_gap",
]
CATEGORICAL = ["zone"]
WEATHER = [
    "temp", "hdd", "wind", "solar", "wind_north", "wind_south", "wind_de", "solar_de", "wind_dk",
    "temp_ref", "wind_ref", "wind_south_ref", "wind_de_ref", "solar_ref",
]


def scale_of(week_mean: np.ndarray, shrunk: np.ndarray) -> np.ndarray:
    """The divisor for target and features: last week's mean level, floored."""
    level = np.where(np.isnan(week_mean), shrunk, week_mean)
    level = np.where(np.isnan(level), SCALE_FLOOR, level)
    return np.maximum(np.abs(level), SCALE_FLOOR)


def _ratio(now: np.ndarray, before: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        return np.where((before > 0) & ~np.isnan(now), now / before - 1.0, np.nan)


def build_matrix(
    zone: str,
    target: np.ndarray,
    lead: np.ndarray,
    prices: dict[str, np.ndarray],
    weather: dict[str, dict[str, np.ndarray]],
    fuels: dict[str, np.ndarray],
    hour0: int,
    holidays,
    weather_age: np.ndarray | None = None,
    stats: dict[str, dict[str, np.ndarray]] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X, shrunk level, scale) for the samples of one zone. Arguments as in v3."""
    x3, shrunk = F.build_matrix(
        zone, target, lead, prices, weather, fuels, hour0, holidays, weather_age, stats
    )
    col = lambda name: x3[:, V3[name]]  # noqa: E731
    s = scale_of(col("week_mean"), shrunk)
    rel = lambda value: (value - shrunk) / s  # noqa: E731

    ref = np.asarray(target, dtype=np.int64) - 24 * np.clip(np.asarray(lead, dtype=np.int64), 1, F.MAX_LEAD)
    close = ref - 24  # the last close known on the last known day, as in v3
    ttf, eua = fuels["ttf"], fuels["eua"]
    ttf_now = F._shift(ttf, close)
    eua_now = F._shift(eua, close)
    gas_cost = GAS_HEAT_RATE * ttf_now + GAS_EMISSIONS * eua_now

    columns = {name: col(name) for name in ("zone", "hour", "dow", "month", "is_weekend", "is_holiday", "lead")}
    columns.update({
        "r_last": rel(col("p_last")),
        "r_lag168": rel(col("p_lag168")),
        "r_lag336": rel(col("p_lag336")),
        "r_shrunk_week": (shrunk - col("week_mean")) / s,
        "r_hour_day": (col("p_last") - col("day_mean")) / s,
        "r_day_week": (col("day_mean") - col("week_mean")) / s,
        "r_day_range": (col("day_max") - col("day_min")) / s,
        "r_spread_se4_se3": col("spread_se4_se3") / s,
        "r_spread_se3_se2": col("spread_se3_se2") / s,
        "ttf_chg7": _ratio(ttf_now, F._shift(ttf, close - 168)),
        "ttf_chg28": _ratio(ttf_now, F._shift(ttf, close - 4 * 168)),
        "eua_chg28": _ratio(eua_now, F._shift(eua, close - 4 * 168)),
        "r_gas_gap": (gas_cost - col("week_mean")) / s,
    })
    columns.update({name: col(name) for name in WEATHER})
    return np.column_stack([columns[name] for name in FEATURE_NAMES]), shrunk, s
