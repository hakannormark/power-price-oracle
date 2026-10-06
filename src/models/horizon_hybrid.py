"""horizon_hybrid: horizon-dependent blend of a short-term and a level model.

- h <= 36:      100 % LightGBM (lightgbm_v2, else lightgbm_v1)
- 36 < h < 72:  linear ramp
- h >= 72:      60 % LightGBM, 40 % market_scaled (else shrunk_scaled)

Until 2026-10-02 the long end was 100 % market level. The weight was then set
to 60/40 by hand. On the hours every horizon forecast, the blend has so far
been worse than lightgbm_v2 alone beyond day 2; see registry.py for why it is
still the default and what would change that. registry.MODEL_DEFINED_SINCE
keeps the two definitions from being scored as one.
"""

from __future__ import annotations

from datetime import datetime

from ..timeutil import horizon_hours
from .base import ForecastPoint, order_quantiles

SHORT_MODEL = "lightgbm_v2"
SHORT_FALLBACK = "lightgbm_v1"
LONG_MODEL = "market_scaled"
LONG_FALLBACK = "shrunk_scaled"

RAMP_START_H = 36
RAMP_END_H = 72
LONG_SHORT_WEIGHT = 0.60  # Retains 60 % LightGBM weather/hourly profile, 40 % market futures level


class HorizonHybrid:
    id = "horizon_hybrid"
    name_sv = "Horisonthybrid"
    description_sv = (
        "LightGBM ensam de första 36 timmarna, därefter en gradvis övergång till en "
        "blandning av 60 % LightGBM och 40 % marknadsjusterad basnivå från 72 timmar. "
        "Sajtens standardmodell sedan 2 oktober 2026. Dygn 1–2 är väl belagda; för dygn "
        "3–7 är underlaget ännu tunt och blandningens vikter är satta för hand."
    )
    quantiles = True
    derived = True

    def combine(
        self, predictions: dict[str, list[ForecastPoint]], issued_at: datetime
    ) -> list[ForecastPoint]:
        short_points = predictions.get(SHORT_MODEL) or predictions.get(SHORT_FALLBACK) or []
        long_points = predictions.get(LONG_MODEL) or predictions.get(LONG_FALLBACK) or []

        if not short_points and not long_points:
            return []
        if not short_points:
            return list(long_points)
        if not long_points:
            return list(short_points)

        short_map = {(p.zone, p.ts): p for p in short_points}
        long_map = {(p.zone, p.ts): p for p in long_points}

        all_keys = sorted(set(short_map.keys()) & set(long_map.keys()), key=lambda k: (k[0], k[1]))
        if not all_keys:
            all_keys = sorted(set(short_map.keys()) | set(long_map.keys()), key=lambda k: (k[0], k[1]))

        combined: list[ForecastPoint] = []
        for zone, ts in all_keys:
            p_short = short_map.get((zone, ts))
            p_long = long_map.get((zone, ts))

            if p_short is None:
                combined.append(p_long)
                continue
            if p_long is None:
                combined.append(p_short)
                continue

            h = max(0, horizon_hours(issued_at, ts))
            if h <= RAMP_START_H:
                w_short = 1.0
            elif h >= RAMP_END_H:
                w_short = LONG_SHORT_WEIGHT
            else:
                frac = (h - RAMP_START_H) / float(RAMP_END_H - RAMP_START_H)
                w_short = 1.0 - frac * (1.0 - LONG_SHORT_WEIGHT)

            w_long = 1.0 - w_short

            p50 = w_short * p_short.p50 + w_long * p_long.p50
            p10 = w_short * p_short.p10 + w_long * p_long.p10
            p90 = w_short * p_short.p90 + w_long * p_long.p90

            p10, p50, p90 = order_quantiles(p10, p50, p90)
            combined.append(ForecastPoint(ts=ts, zone=zone, p10=p10, p50=p50, p90=p90))

        return combined

    def predict(self, features, issued_at: datetime) -> list[ForecastPoint]:  # pragma: no cover
        raise NotImplementedError("horizon_hybrid is derived: call combine().")
