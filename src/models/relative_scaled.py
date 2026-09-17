"""relative_scaled: weather scaling relative to the reference week's weather.

seasonal_naive and shrunk_scaled take their baseline level from one or more
historical prices (lag168 and the 4-week median). Those historical prices were
themselves formed by the weather of that time.

Applying a weather scale relative to a climatological mean (1.0) without
de-weathering the baseline causes severe compounding errors:
- If last week was a storm (e.g. 5 EUR/MWh) and this week has normal wind,
  the old models predict 5 EUR/MWh (scaling by 1.0) because they forget that
  the 5 EUR price was caused by high wind.
- If this week is also windy, they discount the price a second time.

relative_scaled resolves this by scaling by the delta:
    d_wind = wind_target - wind_lag168
    d_temp = HDD(T_target) - HDD(T_lag168)
    d_solar = solar_target - solar_lag168

In addition, it applies:
1. Heating Degree Days (HDD) with a 15 °C threshold to capture the non-linear
   heating load in Nordic winters.
2. Swedish holiday adjustments: dampening weekday peak expectations when the target
   day is an official Swedish public holiday.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from ..timeutil import TZ, horizon_hours
from .band import band_for
from .base import ForecastPoint, order_quantiles, target_window
from .seasonal_naive import baseline_stats
from .shrunk_scaled import shrunk_level
from .weather_scaled import DEFAULT_WEIGHTS, SCALE_MAX, SCALE_MIN, ZONE_WEIGHTS

HEATING_THRESHOLD_C = 15.0
HOLIDAY_PEAK_FACTOR = 0.82
HOLIDAY_OFFPEAK_FACTOR = 0.92


def _hdd(temp: float | None) -> float:
    """Heating degree days: heating load primarily increases below 15 °C."""
    if temp is None or pd.isna(temp):
        return 0.0
    return max(0.0, HEATING_THRESHOLD_C - float(temp))


def compute_relative_scale(
    zone: str,
    target_row: Any,
    ref_row: Any,
) -> float:
    """Multiplier based on weather change between target hour and reference hour."""
    weights = ZONE_WEIGHTS.get(zone, DEFAULT_WEIGHTS)

    # If reference weather is available, compute difference against reference week;
    # otherwise fallback to deviation from normal (1.0 for indices, 0.0 for anomalies).
    if ref_row is not None:
        target_wind_local = getattr(target_row, "wind_index_local", 1.0)
        ref_wind_local = getattr(ref_row, "wind_index_local", 1.0)
        d_wind_local = (
            float(target_wind_local if not pd.isna(target_wind_local) else 1.0)
            - float(ref_wind_local if not pd.isna(ref_wind_local) else 1.0)
        )

        target_wind_north = getattr(target_row, "wind_index_north", 1.0)
        ref_wind_north = getattr(ref_row, "wind_index_north", 1.0)
        d_wind_north = (
            float(target_wind_north if not pd.isna(target_wind_north) else 1.0)
            - float(ref_wind_north if not pd.isna(ref_wind_north) else 1.0)
        )

        target_wind_south = getattr(target_row, "wind_index_south", 1.0)
        ref_wind_south = getattr(ref_row, "wind_index_south", 1.0)
        d_wind_south = (
            float(target_wind_south if not pd.isna(target_wind_south) else 1.0)
            - float(ref_wind_south if not pd.isna(ref_wind_south) else 1.0)
        )

        target_temp = getattr(target_row, "temp_local", None)
        ref_temp = getattr(ref_row, "temp_local", None)
        if target_temp is not None and ref_temp is not None and not pd.isna(target_temp) and not pd.isna(ref_temp):
            d_temp_hdd = (_hdd(target_temp) - _hdd(ref_temp)) / 10.0
        else:
            t_anom_target = getattr(target_row, "temp_anomaly_local", 0.0)
            t_anom_ref = getattr(ref_row, "temp_anomaly_local", 0.0)
            d_temp_hdd = (
                float(t_anom_target if not pd.isna(t_anom_target) else 0.0)
                - float(t_anom_ref if not pd.isna(t_anom_ref) else 0.0)
            ) / 10.0

        target_solar = getattr(target_row, "solar_index_local", 0.0)
        ref_solar = getattr(ref_row, "solar_index_local", 0.0)
        d_solar = (
            float(target_solar if not pd.isna(target_solar) else 0.0)
            - float(ref_solar if not pd.isna(ref_solar) else 0.0)
        )
    else:
        target_wind_local = getattr(target_row, "wind_index_local", 1.0)
        d_wind_local = float(target_wind_local if not pd.isna(target_wind_local) else 1.0) - 1.0
        target_wind_north = getattr(target_row, "wind_index_north", 1.0)
        d_wind_north = float(target_wind_north if not pd.isna(target_wind_north) else 1.0) - 1.0
        target_wind_south = getattr(target_row, "wind_index_south", 1.0)
        d_wind_south = float(target_wind_south if not pd.isna(target_wind_south) else 1.0) - 1.0
        target_anom = getattr(target_row, "temp_anomaly_local", 0.0)
        d_temp_hdd = float(target_anom if not pd.isna(target_anom) else 0.0) / 10.0
        target_solar = getattr(target_row, "solar_index_local", 0.0)
        d_solar = float(target_solar if not pd.isna(target_solar) else 0.0)

    scale = 1.0
    scale -= weights.wind_local * d_wind_local
    scale -= weights.wind_north * d_wind_north
    scale -= weights.wind_south * d_wind_south
    scale += weights.temp * d_temp_hdd
    scale -= weights.solar * d_solar

    return min(max(scale, SCALE_MIN), SCALE_MAX)


class RelativeScaled:
    id = "relative_scaled"
    name_sv = "Relativ väderskalning"
    description_sv = (
        "Grundnivån från förra veckan (dämpad med fyraveckorsmedianen) justerad med "
        "skillnaden i väder mellan måltimmen och samma timme förra veckan, i stället "
        "för mot ett teoretiskt normalklimat. Rättar till felet där förra veckans "
        "extrema väder ärvs in blint, och lägger till temperaturtröskel och helgdagskorrigering."
    )
    quantiles = True
    derived = False

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        start, end = target_window(issued_at)
        zone_means, overall_mean = baseline_stats(features)

        # Build index for looking up past weather 168h ago
        history_map: dict[tuple[str, pd.Timestamp], Any] = {}
        for row in features.itertuples(index=False):
            history_map[(row.zone, row.ts)] = row

        targets = features[
            (features["ts"] >= pd.Timestamp(start)) & (features["ts"] <= pd.Timestamp(end))
        ].copy()

        points: list[ForecastPoint] = []
        for row in targets.itertuples(index=False):
            level = shrunk_level(row, zone_means, overall_mean)

            # Holiday dampening if target is a holiday on a normal weekday
            is_holiday = getattr(row, "is_holiday_se", False)
            is_weekend = getattr(row, "is_weekend", False)
            if is_holiday and not is_weekend:
                factor = HOLIDAY_PEAK_FACTOR if (7 <= row.hour <= 20) else HOLIDAY_OFFPEAK_FACTOR
                level *= factor

            ref_ts = row.ts - pd.Timedelta(hours=168)
            ref_row = history_map.get((row.zone, ref_ts))

            scale = compute_relative_scale(row.zone, row, ref_row)
            p50 = level * scale

            low, high = band_for(p50, horizon_hours(issued_at, row.ts.to_pydatetime()))
            p10, p50, p90 = order_quantiles(low, p50, high)
            points.append(
                ForecastPoint(
                    ts=row.ts.to_pydatetime().astimezone(TZ),
                    zone=row.zone,
                    p10=p10,
                    p50=p50,
                    p90=p90,
                )
            )
        return points
