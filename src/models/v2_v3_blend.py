"""v2_v3_blend: lightgbm_v2 for the near horizons, lightgbm_v3 for the far ones.

- h <= 36:      100 % lightgbm_v2
- 36 < h < 72:  linear ramp
- h >= 72:      100 % lightgbm_v3

Live, v3 has been worse than v2 up to 48 h and better from 72 h. Scored
retrospectively over 29 shared delivery days (to 2026-11-05) this blend averaged
38.03 EUR/MWh against v2's 39.74, difference -1.71 with a 90 % interval of
-2.87 to -0.46. The ramp was chosen after looking at those numbers, so that
figure is not evidence: the candidate is logged from 2026-11-05 and must meet
the promotion rule on forecasts issued since.

The band is the two models' quantiles blended with the same weights. If either
model is missing the blend publishes nothing, so its record holds only blends.
"""

from __future__ import annotations

from datetime import datetime

from ..timeutil import horizon_hours
from .base import ForecastPoint, order_quantiles

SHORT_MODEL = "lightgbm_v2"
LONG_MODEL = "lightgbm_v3"
RAMP_START_H = 36
RAMP_END_H = 72


def short_weight(h: float) -> float:
    if h <= RAMP_START_H:
        return 1.0
    if h >= RAMP_END_H:
        return 0.0
    return 1.0 - (h - RAMP_START_H) / float(RAMP_END_H - RAMP_START_H)


class V2V3Blend:
    id = "v2_v3_blend"
    name_sv = "LightGBM v2/v3-blandning"
    description_sv = (
        "LightGBM v2 de första 36 timmarna, därefter en gradvis övergång till "
        "LightGBM v3, som ensam gäller från 72 timmar. Kandidat sedan 5 november 2026: "
        "v3 har varit bättre än v2 längre fram men sämre de första två dygnen."
    )
    quantiles = True
    derived = True

    def combine(
        self, predictions: dict[str, list[ForecastPoint]], issued_at: datetime
    ) -> list[ForecastPoint]:
        short_map = {(p.zone, p.ts): p for p in predictions.get(SHORT_MODEL) or []}
        long_map = {(p.zone, p.ts): p for p in predictions.get(LONG_MODEL) or []}

        combined: list[ForecastPoint] = []
        for key in sorted(short_map.keys() & long_map.keys()):
            zone, ts = key
            a, b = short_map[key], long_map[key]
            w = short_weight(max(0, horizon_hours(issued_at, ts)))
            p10, p50, p90 = order_quantiles(
                w * a.p10 + (1 - w) * b.p10,
                w * a.p50 + (1 - w) * b.p50,
                w * a.p90 + (1 - w) * b.p90,
            )
            combined.append(ForecastPoint(ts=ts, zone=zone, p10=p10, p50=p50, p90=p90))
        return combined

    def predict(self, features, issued_at: datetime) -> list[ForecastPoint]:  # pragma: no cover
        raise NotImplementedError("v2_v3_blend is derived: call combine().")
