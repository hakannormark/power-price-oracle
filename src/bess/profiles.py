"""Household load and solar PV production profiles for SE4.

Calibrated to the Bengt Ekenstierna case study house:
- 10 kWp rooftop solar PV: annual yield ~7 000 kWh.
- Annual electricity consumption: ~8 000 kWh.
- Current grid export without battery: ~5 000 kWh/year.
- Evening & night load: 8-11 kWh in summer, 10-12 kWh in winter.
"""

from __future__ import annotations

import math
from datetime import datetime
import numpy as np
import pandas as pd

from ..timeutil import TZ

# The case house has 10 kWp on the roof but yields about 7 000 kWh a year in
# Malmö, where 10 kWp facing south at 35 degrees gives about 10 500. Expressed
# as the capacity that gives the same yield, so that the same house in Luleå
# gets Luleå's sun rather than Malmö's 7 000 kWh.
CASE_HOUSE_PV_KWP_EFFECTIVE = 6.7

# The load is two parts. Appliances follow the clock and are the same anywhere.
# Heating follows the measured outdoor temperature in the zone, hour by hour:
# degrees below HEATING_BASE_C. HEATING_SHARE is the heated part of the annual
# consumption for a house in the reference climate (Malmö, about 2 600 degree
# days below 17 C); the same house further north heats more, so a larger part
# of the same annual figure lands in winter.
HEATING_BASE_C = 17.0
HEATING_SHARE = 0.40
REFERENCE_DEGREE_DAYS = 2600.0
REFERENCE_MEAN_DEGREES = REFERENCE_DEGREE_DAYS * 24.0 / 8760.0


def compose_load(base: np.ndarray, degrees: np.ndarray, annual_load_kwh: float, heating_share: float = HEATING_SHARE) -> np.ndarray:
    """Hourly load from the clock-driven part and the degrees below the heating base."""
    n = len(base)
    raw = (1.0 - heating_share) * base / base.mean() + heating_share * degrees / REFERENCE_MEAN_DEGREES
    return raw * (annual_load_kwh * n / 8760.0) / raw.sum()


# Typical diurnal load curve weights (0 to 23 hours)
# Morning peak at 07-09, evening peak at 17-21, night dip at 01-05
HOURLY_BASE_LOAD_WEIGHTS = [
    0.65, 0.55, 0.50, 0.50, 0.55, 0.70,  # 00-05
    0.95, 1.25, 1.20, 1.00, 0.90, 0.85,  # 06-11
    0.85, 0.85, 0.85, 0.95, 1.15, 1.45,  # 12-17
    1.60, 1.55, 1.40, 1.20, 0.95, 0.75,  # 18-23
]


def generate_household_profiles(
    timestamps: list[datetime],
    weather_df: pd.DataFrame | None = None,
    annual_load_kwh: float = 8_000.0,
    annual_pv_kwh: float = 7_000.0,
    pv_capacity_kwp: float = 10.0,
    zone: str | None = None,
    pv_kwp_effective: float | None = None,
    orientation: str = "south",
    heating_share: float = HEATING_SHARE,
) -> pd.DataFrame:
    """Generate aligned hourly load and PV profiles for the given timestamps.

    With `zone`, solar production is the measured radiation in the panel's plane
    at that zone's reference point (bess/solar.py) times `pv_kwp_effective`, and
    the heating part of the load follows the zone's measured temperature: the
    real weather, hour by hour. Without it, or if nothing is stored, the old
    seasonal curves for Malmö are used.
    """
    n = len(timestamps)
    if n == 0:
        return pd.DataFrame(columns=["ts", "hour", "dow", "load_kwh", "pv_kwh", "surplus_kwh", "deficit_kwh"])

    dts = [ts.astimezone(TZ) if ts.tzinfo else ts.replace(tzinfo=TZ) for ts in timestamps]
    hours = np.array([dt.hour for dt in dts])
    dows = np.array([dt.weekday() for dt in dts])
    days_of_year = np.array([dt.timetuple().tm_yday for dt in dts])

    # 1. Base consumer load (appliances, cooking, electronics)
    # Weekday vs weekend slight adjustment
    weekend_factor = np.where(dows >= 5, 1.08, 1.0)
    base_diurnal = np.array([HOURLY_BASE_LOAD_WEIGHTS[h] for h in hours]) * weekend_factor

    # 2. Temperature/heating load: higher in winter (cold months), low in summer
    # Seasonal heating envelope centered around Jan/Feb (day 20-40)
    # Cosine seasonal curve: peak in winter (+1), trough in summer (-1)
    seasonal_temp_factor = np.cos(2 * np.pi * (days_of_year - 20) / 365.25)
    # Scaled so heating adds more in winter
    heating_component = np.maximum(0.0, 0.8 + 0.9 * seasonal_temp_factor)

    raw_load = base_diurnal * (0.55 + 0.45 * heating_component)
    # Normalize to annual_load_kwh (pro-rated to window length)
    hours_per_year = 8760.0
    scaling_load = (annual_load_kwh * (n / hours_per_year)) / raw_load.sum()
    load_kwh = raw_load * scaling_load

    # 3. Solar PV generation (10 kWp in SE4)
    # Solar elevation / solar noon profile
    # Summer solar noon reaches high peaks, winter very low
    solar_declination = 23.45 * np.sin(2 * np.pi * (284 + days_of_year) / 365.0)
    lat_se4 = 55.6  # Malmö / Skåne latitude
    
    # Hour angle (12 = solar noon)
    hour_angles = 15.0 * (hours - 12.0)
    sin_elev = (
        np.sin(np.radians(lat_se4)) * np.sin(np.radians(solar_declination))
        + np.cos(np.radians(lat_se4)) * np.cos(np.radians(solar_declination)) * np.cos(np.radians(hour_angles))
    )
    solar_elev = np.maximum(0.0, sin_elev)

    # Air mass & clear sky approximation
    raw_pv = np.where(solar_elev > 0.05, (solar_elev ** 1.15) * pv_capacity_kwp * 0.85, 0.0)

    # Weather modulation if solar irradiance is available
    if weather_df is not None and not weather_df.empty and "solar_index" in weather_df.columns:
        w_map = dict(zip(weather_df["ts"], weather_df["solar_index"]))
        irradiance_mod = np.array([w_map.get(dt, 1.0) for dt in dts])
        raw_pv = raw_pv * np.clip(irradiance_mod, 0.1, 1.8)

    # Normalize PV to annual_pv_kwh (pro-rated)
    pv_sum = raw_pv.sum()
    if pv_sum > 0:
        scaling_pv = (annual_pv_kwh * (n / hours_per_year)) / pv_sum
        pv_kwh = raw_pv * scaling_pv
    else:
        pv_kwh = np.zeros(n)

    degrees = np.zeros(n)
    if zone is not None:
        from . import solar

        measured = solar.production_per_kwp(zone, dts, orientation)
        if measured is not None:
            kwp = CASE_HOUSE_PV_KWP_EFFECTIVE if pv_kwp_effective is None else pv_kwp_effective
            pv_kwh = measured * kwp
        temp = solar.temperature(zone, dts)
        if temp is not None:
            degrees = np.maximum(0.0, HEATING_BASE_C - temp)
            load_kwh = compose_load(base_diurnal, degrees, annual_load_kwh, heating_share)

    surplus = np.maximum(0.0, pv_kwh - load_kwh)
    deficit = np.maximum(0.0, load_kwh - pv_kwh)

    df = pd.DataFrame({
        "ts": dts,
        "hour": hours,
        "dow": dows,
        "base": base_diurnal,
        "degrees": degrees,
        "load_kwh": load_kwh,
        "pv_kwh": pv_kwh,
        "surplus_kwh": surplus,
        "deficit_kwh": deficit,
    })
    return df
