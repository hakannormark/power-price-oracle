"""Historical backtesting of home battery storage in SE4.

Replays actual recorded spot prices and solar irradiance over past years
to verify the financial yield of each battery offer in real historical market conditions.
Demonstrates the impact of:
- 15-minute settlement introduction (widened spreads).
- Abolition of the 60-öre tax credit (higher solar self-consumption value).
"""

from __future__ import annotations

import logging
from datetime import datetime
import pandas as pd

from ..config import ACTUALS_DIR
from ..store import load_actuals
from .dispatch import simulate_battery_dispatch
from .offers import OFFERS, BatteryOffer
from .profiles import generate_household_profiles

log = logging.getLogger(__name__)


def run_historical_backtest(
    actuals: list[dict] | None = None,
    fx_rate: float = 11.35,  # EUR/SEK fallback
    zone: str = "SE4",
    years: list[int] | None = None,
) -> dict[str, Any]:
    """Run backtest per year and per offer on real historical prices."""
    if actuals is None:
        actuals = load_actuals()

    years = years or [2022, 2023, 2024, 2025, 2026]
    # Filter zone actuals
    zone_rows = [r for r in actuals if r.get("zone") == zone]
    if not zone_rows:
        return {"ok": False, "error": f"No actuals for zone {zone}"}

    df_act = pd.DataFrame(zone_rows)
    from ..timeutil import TZ
    df_act["ts"] = pd.to_datetime(df_act["ts"], utc=True).dt.tz_convert(TZ)
    df_act["year"] = df_act["ts"].dt.year
    df_act["price_sek_kwh"] = (df_act["price_eur_mwh"] * fx_rate) / 1000.0

    yearly_results = {}
    for y in years:
        sub = df_act[df_act["year"] == y].sort_values("ts")
        if len(sub) < 1000:  # Need substantial hours
            continue

        timestamps = [t.to_pydatetime() for t in sub["ts"]]
        profile_df = generate_household_profiles(timestamps)
        spot_series = sub["price_sek_kwh"].to_numpy()

        offer_scores = {}
        for offer in OFFERS:
            res_mixed = simulate_battery_dispatch(profile_df, spot_series, offer, strategy="mixed")
            res_energy = simulate_battery_dispatch(profile_df, spot_series, offer, strategy="energy_only")
            offer_scores[offer.id] = {
                "mixed": {
                    "solar_savings_sek": res_mixed.solar_savings_sek,
                    "arbitrage_profit_sek": res_mixed.arbitrage_profit_sek,
                    "ancillary_revenue_sek": res_mixed.ancillary_revenue_sek,
                    "total_value_sek": res_mixed.total_annual_value_sek,
                    "cycles": res_mixed.equivalent_cycles,
                },
                "energy_only": {
                    "solar_savings_sek": res_energy.solar_savings_sek,
                    "arbitrage_profit_sek": res_energy.arbitrage_profit_sek,
                    "total_value_sek": res_energy.total_annual_value_sek,
                    "cycles": res_energy.equivalent_cycles,
                },
            }

        yearly_results[str(y)] = {
            "hours": len(sub),
            "mean_spot_sek_kwh": round(float(sub["price_sek_kwh"].mean()), 3),
            "min_spot_sek_kwh": round(float(sub["price_sek_kwh"].min()), 3),
            "max_spot_sek_kwh": round(float(sub["price_sek_kwh"].max()), 3),
            "offers": offer_scores,
        }

    return {
        "ok": True,
        "zone": zone,
        "years": yearly_results,
    }
