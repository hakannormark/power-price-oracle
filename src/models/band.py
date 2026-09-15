"""The uncertainty band, calibrated against outcomes instead of asserted.

The original width was a guess: 0.30 + 0.25 * |scale - 1| of the level, widened
upward by 1.4. Measured over 82 576 out-of-sample hours it contained **39.7 %**
of outcomes while claiming 80 %, and it was equally wrong on day 1 and day 7
because it did not depend on the horizon at all.

A band that narrow is worse than no band. It says the forecast is confident
exactly where it is least reliable, and the failure is silent: nothing in the
output distinguishes a well-covered hour from a badly covered one.

These quantiles are the empirical 10th and 90th percentile of the residual

    (outcome - p50) / max(|p50|, MIN_BAND_BASE)

per forecast day, estimated on earlier quarters only and scored on later ones.
The result covers 79.7 %.

Two properties of the fitted numbers are worth stating plainly.

The band is strongly asymmetric. The downside is bounded near -0.85 of the level
while the upside runs to two or three times it, because electricity spikes
upward and cannot fall far below zero. A symmetric band cannot be calibrated
here at any width.

The band is wide: about 120 EUR/MWh on average against the old 32. That is not a
regression, it is the honest width of a seven-day electricity price forecast.
Reporting 32 was the error.

Refresh with `python -m src.research.backtest`, which prints these quantiles.
"""

from __future__ import annotations

# Fitted per forecast day, then made monotone: uncertainty cannot genuinely
# shrink as the horizon grows, and the day 6-7 dip in the raw estimates is
# sampling noise across ten quarters. Erring wide is the safer direction —
# under-covering is the failure that misleads.
RESIDUAL_QUANTILES: dict[int, tuple[float, float]] = {
    1: (-0.856, 1.762),
    2: (-0.877, 1.762),
    3: (-0.877, 2.106),
    4: (-0.877, 2.432),
    5: (-0.877, 2.726),
    6: (-0.877, 2.726),
    7: (-0.877, 2.726),
}

# Beyond the fitted horizon, hold the widest estimate rather than extrapolate.
FALLBACK = RESIDUAL_QUANTILES[7]

# Checked against live outcomes on 2026-09-15, over 16 724 scored hours of the
# default model. The table above covers 78.8 % against a target of 80 %:
#
#     day          d1    d2    d3    d4    d5    d6    d7
#     coverage   70.1  75.3  77.5  78.5  83.3  82.9  79.8
#
# Day 1 under-covers and days 5-6 over-cover, and the cause of the latter is the
# lower bound: the empirical 10th percentile of the live residual is about -0.76
# there, shallower than the -0.877 shipped, so fewer than one hour in ten falls
# out of the bottom.
#
# DO NOT refit this table from back-test residuals. The back-test feeds models
# ERA5 reanalysis, so its residuals are far tighter than anything a real
# forecast produces: it fits q90 between +1.98 and +2.07, while live residuals
# run +2.45 on day 1 to +3.14 on day 7. Two replacements fitted that way were
# measured on live outcomes and both were worse than what ships — 74.6 % for the
# back-test quantiles and 76.1 % for a flat curve, against 78.8 % here.
#
# Left unchanged deliberately. Eleven days of live scoring, with only 680 points
# on day 1, is a fortnight and not a calibration; recheck once the live record
# spans a season.

# The level a band is measured against. Below this the level carries no scale
# information — a 2 EUR/MWh hour needs an absolute band, not a proportional one.
MIN_BAND_BASE = 10.0


def forecast_day(horizon_h: int) -> int:
    """Day 1 is the first 24 hours after issue."""
    return max(1, min(7, (max(horizon_h, 0) // 24) + 1))


def band_for(p50: float, horizon_h: int) -> tuple[float, float]:
    """Calibrated p10 and p90 around a median forecast."""
    low, high = RESIDUAL_QUANTILES.get(forecast_day(horizon_h), FALLBACK)
    base = max(abs(p50), MIN_BAND_BASE)
    return p50 + low * base, p50 + high * base
